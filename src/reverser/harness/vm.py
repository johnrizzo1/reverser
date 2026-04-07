"""Incus container lifecycle management for isolated binary analysis."""

import hashlib
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)


class IncusError(Exception):
    """Raised when an Incus CLI command fails."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"incus {' '.join(cmd)} failed (rc={returncode}): {stderr[:500]}")


def _run_incus(*args: str, timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["incus", *args]
    log.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise IncusError(list(args), result.returncode, result.stderr)
    return result


def _container_name(s3_key: str) -> str:
    h = hashlib.sha256(s3_key.encode()).hexdigest()[:8]
    ts = int(time.time())
    # Incus names must be alphanumeric + hyphens, no leading digit
    return f"reverser-{h}-{ts}"


class IncusVMManager:
    def __init__(self, image: str, profile: str, anthropic_api_key: str,
                 backend: str = "claude", model: str | None = None,
                 api_base: str | None = None):
        self.image = image
        self.profile = profile
        self.anthropic_api_key = anthropic_api_key
        self.backend = backend
        self.model = model
        self.api_base = api_base

    def ensure_image_exists(self) -> bool:
        result = _run_incus("image", "list", "--format=json")
        images = json.loads(result.stdout)
        for img in images:
            aliases = [a["name"] for a in img.get("aliases", [])]
            if self.image in aliases:
                log.info("Base image '%s' found", self.image)
                return True
        raise IncusError(
            ["image", "list"], 1,
            f"Base image '{self.image}' not found. Run harness-build-image first.",
        )

    def create_container(self, s3_key: str, binary_path: str) -> str:
        name = _container_name(s3_key)
        binary_path = str(Path(binary_path).resolve())

        log.info("Creating container %s from image %s", name, self.image)
        _run_incus("launch", self.image, name, "--profile", self.profile)

        # Wait for container OS to be ready (not just container status)
        for i in range(60):
            result = _run_incus("exec", name, "--", "true", check=False, timeout=5)
            if result.returncode == 0:
                break
            time.sleep(2)
        else:
            self.destroy_container(name)
            raise IncusError(["launch"], 1, f"Container {name} did not become ready within 120s")

        # Set up the reverser user's environment (user is baked into image)
        _run_incus("exec", name, "--", "bash", "-c",
                   "cp /root/.bash_profile /home/reverser/.bash_profile && "
                   "chown 1000:1000 /home/reverser/.bash_profile")

        # Create working directories
        _run_incus("exec", name, "--", "bash", "-c",
                   "mkdir -p /tmp/results && chown 1000:1000 /tmp/results")

        # Push the binary into the container
        log.info("Pushing binary to container %s", name)
        _run_incus("file", "push", binary_path, f"{name}/tmp/target")
        _run_incus("exec", name, "--", "chmod", "+r", "/tmp/target")

        # Inject the API key for the reverser user
        _run_incus(
            "exec", name, "--", "bash", "-c",
            f"echo 'export ANTHROPIC_API_KEY={self.anthropic_api_key}' >> /home/reverser/.bash_profile && "
            "chown 1000:1000 /home/reverser/.bash_profile",
        )

        log.info("Container %s ready", name)
        return name

    def _get_host_ip(self, container_name: str) -> str | None:
        """Get the host's IP as seen from inside the container (default gateway)."""
        try:
            result = _run_incus(
                "exec", container_name, "--", "bash", "-c",
                "ip route show default | awk '{print $3}'",
                check=False, timeout=10,
            )
            ip = result.stdout.strip()
            if ip and result.returncode == 0:
                return ip
        except (subprocess.TimeoutExpired, Exception):
            pass
        log.warning("Could not determine host IP for container %s", container_name)
        return None

    def run_analysis(
        self, name: str, mode: str, budget: float, timeout: int,
        verbose: int = 0,
    ) -> tuple[int, str]:
        verbose_flags = " -" + "v" * verbose if verbose > 0 else ""
        log.info("Running 'reverser %s' in container %s (timeout=%ds)", mode, name, timeout)

        backend_flags = ""
        if self.backend != "claude":
            backend_flags += f" --backend {self.backend}"
            if self.model:
                backend_flags += f" --model {self.model}"
            api_base = self.api_base
            if api_base is None and self.backend == "ollama":
                # Resolve the host IP so the container can reach ollama.
                # localhost inside the container is the container itself.
                host_ip = self._get_host_ip(name)
                if host_ip:
                    api_base = f"http://{host_ip}:11434/v1"
                    log.info("Resolved host IP for ollama: %s", host_ip)
            if api_base:
                backend_flags += f" --api-base {api_base}"

        cmd = [
            "exec", name, "--user", "1000", "--group", "1000",
            "--env", "HOME=/home/reverser", "--",
            "bash", "-lc",
            f"reverser{verbose_flags} {mode} /tmp/target --budget {budget} "
            f"--log /tmp/results/session.jsonl --log-dir /tmp/results"
            f"{backend_flags}",
        ]
        try:
            result = _run_incus(*cmd, timeout=timeout, check=False)
            output = result.stdout + result.stderr
            log.info("Analysis exited with code %d", result.returncode)
            return result.returncode, output
        except subprocess.TimeoutExpired:
            log.warning("Analysis timed out after %ds in container %s", timeout, name)
            return -1, f"Analysis timed out after {timeout}s"

    def collect_results(self, name: str, dest_dir: str) -> Path:
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)

        log.info("Collecting results from container %s to %s", name, dest)

        # Pull the results directory
        # incus file pull -r creates a "results" subdir inside dest;
        # remove it first to avoid "file exists" errors on re-runs.
        results_subdir = dest / "results"
        if results_subdir.exists():
            shutil.rmtree(results_subdir)
        try:
            _run_incus("file", "pull", "-r", f"{name}/tmp/results/", str(dest) + "/")
        except IncusError as e:
            log.warning("Failed to pull results: %s", e)

        # Also grab any markdown/text reports the agent may have created
        try:
            result = _run_incus(
                "exec", name, "--", "bash", "-c",
                "find /tmp -maxdepth 2 -name '*.md' -o -name '*.txt' 2>/dev/null",
                check=False,
            )
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line and line != "/tmp/results":
                    try:
                        _run_incus("file", "pull", f"{name}{line}", str(dest) + "/")
                    except IncusError:
                        pass
        except (IncusError, subprocess.TimeoutExpired):
            pass

        return dest

    def destroy_container(self, name: str):
        log.info("Destroying container %s", name)
        try:
            _run_incus("stop", name, "--force", check=False, timeout=30)
        except subprocess.TimeoutExpired:
            pass
        try:
            _run_incus("delete", name, "--force", timeout=30)
        except (IncusError, subprocess.TimeoutExpired) as e:
            log.error("Failed to destroy container %s: %s", name, e)

    def cleanup_orphans(self, state_db, timeout_seconds: int):
        """Destroy containers that have exceeded the timeout."""
        # Check state DB for orphaned records
        orphaned = state_db.get_orphaned_vms(timeout_seconds)
        for record in orphaned:
            vm_name = record.get("vm_name")
            if vm_name:
                log.warning("Cleaning up orphaned container: %s", vm_name)
                self.destroy_container(vm_name)
                state_db.mark_failed(
                    record["s3_key"], record["etag"],
                    "Timed out (orphan cleanup)",
                )

        # Also scan for any reverser-* containers not in DB
        try:
            result = _run_incus("list", "--format=json")
            containers = json.loads(result.stdout)
            for c in containers:
                name = c.get("name", "")
                if name.startswith("reverser-"):
                    running = state_db.get_running()
                    tracked_vms = {r["vm_name"] for r in running}
                    if name not in tracked_vms:
                        log.warning("Destroying untracked container: %s", name)
                        self.destroy_container(name)
        except (IncusError, subprocess.TimeoutExpired):
            pass

    def list_containers(self) -> list[dict]:
        result = _run_incus("list", "--format=json", check=False)
        if result.returncode != 0:
            return []
        containers = json.loads(result.stdout)
        return [c for c in containers if c.get("name", "").startswith("reverser-")]
