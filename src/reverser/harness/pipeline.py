"""Orchestrates the full analysis pipeline: S3 download -> container -> analyze -> collect."""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .monitor import S3Monitor
from .state import StateDB
from .vm import IncusVMManager

log = logging.getLogger(__name__)


def _s3_key_to_dirname(s3_key: str) -> str:
    """Convert an S3 key to a safe directory name."""
    name = Path(s3_key).stem
    # Replace non-alphanumeric chars with underscores
    return re.sub(r"[^a-zA-Z0-9._-]", "_", name)


class AnalysisPipeline:
    def __init__(self, config: Config, monitor: S3Monitor, state_db: StateDB, verbose: int = 0):
        self.config = config
        self.monitor = monitor
        self.state_db = state_db
        self.verbose = verbose
        self.vm_manager = IncusVMManager(
            image=config.incus_image,
            profile=config.incus_profile,
            anthropic_api_key=config.anthropic_api_key,
        )

    def _status(self, msg: str):
        """Print a progress message when verbose mode is enabled."""
        if self.verbose:
            import click
            click.echo(f"  -> {msg}")

    @staticmethod
    def _explain_exit_code(exit_code: int) -> str:
        """Return a human-readable explanation for a process exit code."""
        explanations = {
            -1: "Analysis timed out (exceeded the configured timeout limit).",
            1: "Reverser exited with a general error.",
            2: "Reverser exited with a usage/argument error.",
            137: "Process was killed (SIGKILL). Likely out-of-memory or container resource limit exceeded.",
            139: "Process crashed with a segmentation fault (SIGSEGV).",
            143: "Process was terminated (SIGTERM). Likely hit the analysis timeout.",
        }
        if exit_code in explanations:
            return explanations[exit_code]
        if exit_code > 128:
            signal_num = exit_code - 128
            return f"Process was killed by signal {signal_num}."
        return f"Reverser exited with unexpected code {exit_code}."

    def _write_summary(self, result_dir: Path, s3_key: str, exit_code: int,
                       output: str, success: bool, error: str | None = None):
        """Write a STATUS.md report into the results directory."""
        result_dir.mkdir(parents=True, exist_ok=True)
        status = "SUCCESS" if success else "FAILED"
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        lines = [
            f"# Analysis Report: {Path(s3_key).name}",
            "",
            f"**Status:** {status}",
            f"**Date:** {timestamp}",
            f"**Mode:** {self.config.analysis_mode}",
            f"**Budget:** ${self.config.analysis_budget}",
            f"**Exit Code:** {exit_code}",
            "",
        ]

        if not success:
            explanation = error or self._explain_exit_code(exit_code)
            lines += [
                "## Failure Reason",
                "",
                explanation,
                "",
            ]

        # Include tail of reverser output for context on failures
        if not success and output:
            # Keep last 100 lines to avoid huge reports
            tail = "\n".join(output.splitlines()[-100:])
            lines += [
                "## Reverser Output (last 100 lines)",
                "",
                "```",
                tail,
                "```",
                "",
            ]

        summary_path = result_dir / "STATUS.md"
        summary_path.write_text("\n".join(lines))
        log.info("Wrote summary report to %s", summary_path)

    def process_binary(self, s3_key: str, etag: str, size: int) -> bool:
        """Full lifecycle for one binary. Returns True on success."""
        vm_name = None
        local_path = None

        try:
            # 1. Download from S3
            log.info("Processing: %s (%d bytes)", s3_key, size)
            self._status(f"Downloading from S3: {s3_key}")
            local_path = self.monitor.download(s3_key, self.config.staging_dir)

            # 2. Create container
            self._status("Creating isolated container...")
            vm_name = self.vm_manager.create_container(s3_key, str(local_path))
            self._status(f"Container ready: {vm_name}")
            self.state_db.mark_running(s3_key, etag, vm_name)

            # 3. Run analysis
            self._status(f"Running reverser {self.config.analysis_mode} (budget=${self.config.analysis_budget}, timeout={self.config.analysis_timeout_seconds}s)")
            exit_code, output = self.vm_manager.run_analysis(
                vm_name,
                mode=self.config.analysis_mode,
                budget=self.config.analysis_budget,
                timeout=self.config.analysis_timeout_seconds,
                verbose=self.verbose,
            )
            self._status(f"Reverser exited with code {exit_code}")

            # 4. Collect results
            result_dir = Path(self.config.results_dir) / _s3_key_to_dirname(s3_key)
            self._status(f"Collecting results to {result_dir}")
            self.vm_manager.collect_results(vm_name, str(result_dir))

            # 5. Write summary report
            success = exit_code == 0
            self._write_summary(result_dir, s3_key, exit_code, output, success)

            # 6. Upload results to S3 if configured
            if self.config.results_s3_prefix:
                upload_prefix = f"{self.config.results_s3_prefix.rstrip('/')}/{_s3_key_to_dirname(s3_key)}"
                self._status(f"Uploading results to s3://{self.config.s3_bucket}/{upload_prefix}")
                self.monitor.upload_results(result_dir, upload_prefix)

            # 7. Update state
            if success:
                self.state_db.mark_completed(s3_key, etag, str(result_dir))
                log.info("Analysis completed successfully: %s -> %s", s3_key, result_dir)
                return True
            else:
                self.state_db.mark_failed(
                    s3_key, etag,
                    f"exit code {exit_code}: {output[:2000]}",
                )
                log.warning("Analysis failed (exit %d): %s", exit_code, s3_key)
                return False

        except Exception as e:
            log.exception("Pipeline error for %s: %s", s3_key, e)
            error_msg = str(e)[:500]
            self.state_db.mark_failed(s3_key, etag, error_msg)
            # Write summary for pipeline-level errors too
            result_dir = Path(self.config.results_dir) / _s3_key_to_dirname(s3_key)
            self._write_summary(result_dir, s3_key, -2, "", False,
                                error=f"Pipeline error: {error_msg}")
            if self.config.results_s3_prefix:
                upload_prefix = f"{self.config.results_s3_prefix.rstrip('/')}/{_s3_key_to_dirname(s3_key)}"
                try:
                    self.monitor.upload_results(result_dir, upload_prefix)
                except Exception:
                    log.warning("Failed to upload error report for %s", s3_key)
            return False

        finally:
            # Always clean up container
            if vm_name:
                self._status(f"Destroying container {vm_name}")
                try:
                    self.vm_manager.destroy_container(vm_name)
                except Exception:
                    log.exception("Failed to destroy container %s", vm_name)

            # Clean up staging file
            if local_path and local_path.exists():
                local_path.unlink(missing_ok=True)

    def process_local(self, binary_path: str) -> bool:
        """Analyze a local binary directly, skipping S3."""
        path = Path(binary_path).resolve()
        if not path.is_file():
            log.error("File not found: %s", path)
            return False

        s3_key = f"local/{path.name}"
        etag = "local"
        size = path.stat().st_size

        self.state_db.mark_pending(s3_key, etag, size)

        vm_name = None
        try:
            self._status("Creating isolated container...")
            vm_name = self.vm_manager.create_container(s3_key, str(path))
            self._status(f"Container ready: {vm_name}")
            self.state_db.mark_running(s3_key, etag, vm_name)

            self._status(f"Running reverser {self.config.analysis_mode} (budget=${self.config.analysis_budget}, timeout={self.config.analysis_timeout_seconds}s)")
            exit_code, output = self.vm_manager.run_analysis(
                vm_name,
                mode=self.config.analysis_mode,
                budget=self.config.analysis_budget,
                timeout=self.config.analysis_timeout_seconds,
                verbose=self.verbose,
            )
            self._status(f"Reverser exited with code {exit_code}")

            result_dir = Path(self.config.results_dir) / _s3_key_to_dirname(s3_key)
            self._status(f"Collecting results to {result_dir}")
            self.vm_manager.collect_results(vm_name, str(result_dir))

            success = exit_code == 0
            self._write_summary(result_dir, s3_key, exit_code, output, success)

            if success:
                self.state_db.mark_completed(s3_key, etag, str(result_dir))
                log.info("Analysis completed: %s -> %s", path.name, result_dir)
                return True
            else:
                self.state_db.mark_failed(s3_key, etag, f"exit code {exit_code}: {output[:2000]}")
                log.warning("Analysis failed (exit %d): %s", exit_code, path.name)
                return False

        except Exception as e:
            log.exception("Pipeline error for %s: %s", path.name, e)
            error_msg = str(e)[:500]
            self.state_db.mark_failed(s3_key, etag, error_msg)
            result_dir = Path(self.config.results_dir) / _s3_key_to_dirname(s3_key)
            self._write_summary(result_dir, s3_key, -2, "", False,
                                error=f"Pipeline error: {error_msg}")
            return False

        finally:
            if vm_name:
                self._status(f"Destroying container {vm_name}")
                try:
                    self.vm_manager.destroy_container(vm_name)
                except Exception:
                    log.exception("Failed to destroy container %s", vm_name)
