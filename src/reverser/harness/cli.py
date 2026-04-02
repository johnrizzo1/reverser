"""CLI entry point for reverser harness."""

import json
import logging
import signal
import subprocess
import sys
import time
from pathlib import Path

import click

from .config import Config, load_config
from .state import StateDB


def _setup_logging(verbose: int):
    level = logging.DEBUG if verbose >= 2 else logging.INFO if verbose >= 1 else logging.WARNING
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=level,
        stream=sys.stderr,
    )


@click.group()
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v info, -vv debug)")
@click.option("--config", "config_path", default=None, help="Path to harness.toml")
@click.pass_context
def main(ctx, verbose, config_path):
    """Reverser Harness - S3 monitor for isolated binary analysis."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config_path)
    ctx.obj["verbose"] = verbose


@main.command()
@click.pass_context
def init(ctx):
    """Initialize Incus profile, firewall rules, and state database."""
    config = ctx.obj["config"]
    log = logging.getLogger("init")

    # 1. Validate Incus is reachable
    click.echo("Checking Incus...")
    try:
        result = subprocess.run(
            ["incus", "version"], capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            click.echo(f"ERROR: Incus not reachable: {result.stderr}", err=True)
            sys.exit(1)
        click.echo(f"  Incus version: {result.stdout.strip()}")
    except FileNotFoundError:
        click.echo("ERROR: 'incus' command not found", err=True)
        sys.exit(1)

    # 2. Create/update the Incus profile
    click.echo(f"Setting up Incus profile '{config.incus_profile}'...")
    profile_yaml = Path(__file__).parent.parent.parent.parent / "incus" / "profile.yaml"
    if not profile_yaml.exists():
        import os
        reverser_home = os.environ.get("REVERSER_HOME", ".")
        profile_yaml = Path(reverser_home) / "incus" / "profile.yaml"

    if profile_yaml.exists():
        # Check if profile exists
        result = subprocess.run(
            ["incus", "profile", "list", "--format=json"],
            capture_output=True, text=True,
        )
        profiles = json.loads(result.stdout) if result.returncode == 0 else []
        profile_names = [p["name"] for p in profiles]

        if config.incus_profile not in profile_names:
            subprocess.run(
                ["incus", "profile", "create", config.incus_profile],
                capture_output=True, text=True,
            )

        with open(profile_yaml) as f:
            subprocess.run(
                ["incus", "profile", "edit", config.incus_profile],
                input=f.read(), capture_output=True, text=True,
            )
        click.echo(f"  Profile '{config.incus_profile}' configured")
    else:
        click.echo(f"  WARNING: {profile_yaml} not found, skipping profile setup")

    # 3. Set up firewall rules
    click.echo("Setting up firewall rules...")
    firewall_script = Path(__file__).parent.parent.parent.parent / "incus" / "setup-firewall.sh"
    if not firewall_script.exists():
        import os
        reverser_home = os.environ.get("REVERSER_HOME", ".")
        firewall_script = Path(reverser_home) / "incus" / "setup-firewall.sh"

    if firewall_script.exists():
        result = subprocess.run(
            ["sudo", "bash", str(firewall_script)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            click.echo("  Firewall rules applied")
        else:
            click.echo(f"  WARNING: Firewall setup failed: {result.stderr}", err=True)
    else:
        click.echo(f"  WARNING: {firewall_script} not found, skipping firewall setup")

    # 4. Initialize state database
    click.echo(f"Initializing state database at {config.db_path}...")
    state_db = StateDB(config.db_path)
    state_db.close()
    click.echo("  Database ready")

    # 5. Create staging and results directories
    Path(config.staging_dir).mkdir(parents=True, exist_ok=True)
    Path(config.results_dir).mkdir(parents=True, exist_ok=True)
    click.echo(f"  Directories: {config.staging_dir}, {config.results_dir}")

    click.echo("\nInitialization complete.")
    click.echo("Next steps:")
    click.echo("  1. Run 'harness-build-image' to create the reverser container image")
    click.echo("  2. Run 'harness-test' to verify container isolation")
    click.echo("  3. Run 'harness-run' to start monitoring S3")


@main.command()
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v info, -vv debug)")
@click.pass_context
def monitor(ctx, verbose):
    """Poll S3 for new binaries and analyze them in isolated containers."""
    config = ctx.obj["config"]
    config.validate()

    # Merge group-level and command-level verbosity
    verbose = max(verbose, ctx.obj["verbose"])
    _setup_logging(verbose)
    log = logging.getLogger("monitor")

    from .monitor import S3Monitor
    from .pipeline import AnalysisPipeline

    state_db = StateDB(config.db_path)
    s3mon = S3Monitor(config, state_db)
    pipeline = AnalysisPipeline(config, s3mon, state_db, verbose=verbose)

    # Verify base image exists
    pipeline.vm_manager.ensure_image_exists()

    click.echo(f"Monitoring s3://{config.s3_bucket}/{config.s3_prefix}")
    click.echo(f"Mode: {config.analysis_mode} | Budget: ${config.analysis_budget} | Poll: {config.poll_interval_seconds}s")
    click.echo("Press Ctrl+C to stop.\n")

    shutdown = False

    def handle_signal(signum, frame):
        nonlocal shutdown
        shutdown = True
        click.echo("\nShutting down after current task...")

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    while not shutdown:
        # Clean up any orphaned containers
        pipeline.vm_manager.cleanup_orphans(state_db, config.analysis_timeout_seconds)

        # Poll for new binaries
        if verbose:
            click.echo(f"Polling s3://{config.s3_bucket}/{config.s3_prefix}...")
        new_objects = s3mon.poll()
        if verbose and not new_objects:
            click.echo("  No new binaries found.")
        for obj in new_objects:
            if shutdown:
                break

            state_db.mark_pending(obj.key, obj.etag, obj.size)
            click.echo(f"New binary: {obj.key} ({obj.size} bytes)")

            success = pipeline.process_binary(obj.key, obj.etag, obj.size)
            status = "completed" if success else "FAILED"
            click.echo(f"Analysis {status}: {obj.key}")

        if not shutdown:
            time.sleep(config.poll_interval_seconds)

    state_db.close()
    click.echo("Stopped.")


@main.command()
@click.pass_context
def status(ctx):
    """Show processing statistics."""
    config = ctx.obj["config"]
    state_db = StateDB(config.db_path)
    stats = state_db.get_stats()
    state_db.close()

    if not stats:
        click.echo("No records yet.")
        return

    click.echo("Processing stats:")
    total = 0
    for s, count in sorted(stats.items()):
        click.echo(f"  {s:12s}: {count}")
        total += count
    click.echo(f"  {'total':12s}: {total}")


@main.command()
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v info, -vv debug)")
@click.argument("binary", type=click.Path(exists=True))
@click.pass_context
def process(ctx, verbose, binary):
    """Analyze a local binary directly (skip S3)."""
    config = ctx.obj["config"]
    if not config.anthropic_api_key:
        click.echo("ERROR: ANTHROPIC_API_KEY environment variable is required", err=True)
        sys.exit(1)

    from .monitor import S3Monitor
    from .pipeline import AnalysisPipeline

    verbose = max(verbose, ctx.obj["verbose"])
    _setup_logging(verbose)

    state_db = StateDB(config.db_path)
    s3mon = S3Monitor(config, state_db)
    pipeline = AnalysisPipeline(config, s3mon, state_db, verbose=verbose)

    pipeline.vm_manager.ensure_image_exists()

    click.echo(f"Analyzing: {binary}")
    click.echo(f"Mode: {config.analysis_mode} | Budget: ${config.analysis_budget}")

    success = pipeline.process_local(binary)
    if success:
        click.echo("Analysis completed successfully.")
    else:
        click.echo("Analysis failed.", err=True)
        sys.exit(1)


@main.command()
@click.option("--failed-only", is_flag=True, help="Only reset failed analyses")
@click.pass_context
def reset(ctx, failed_only):
    """Clear state DB so binaries are reprocessed on next run."""
    config = ctx.obj["config"]
    state_db = StateDB(config.db_path)

    if failed_only:
        count = state_db.delete_by_status("failed")
        click.echo(f"Cleared {count} failed record(s).")
    else:
        stats = state_db.get_stats()
        if not stats:
            click.echo("No records to clear.")
            state_db.close()
            return
        for s, c in sorted(stats.items()):
            click.echo(f"  {s}: {c}")
        if click.confirm("Clear all records?"):
            count = state_db.delete_all()
            click.echo(f"Cleared {count} record(s).")
        else:
            click.echo("Aborted.")

    state_db.close()


@main.command()
@click.pass_context
def cleanup(ctx):
    """Destroy orphaned reverser containers."""
    config = ctx.obj["config"]

    from .vm import IncusVMManager

    state_db = StateDB(config.db_path)
    vm_mgr = IncusVMManager(
        image=config.incus_image,
        profile=config.incus_profile,
        anthropic_api_key=config.anthropic_api_key,
    )

    containers = vm_mgr.list_containers()
    if not containers:
        click.echo("No reverser containers found.")
        state_db.close()
        return

    click.echo(f"Found {len(containers)} reverser container(s):")
    for c in containers:
        click.echo(f"  {c['name']} ({c.get('status', 'unknown')})")

    if click.confirm("Destroy all?"):
        for c in containers:
            vm_mgr.destroy_container(c["name"])
            click.echo(f"  Destroyed: {c['name']}")

    state_db.close()


@main.command("test-vm")
@click.pass_context
def test_vm(ctx):
    """Launch a test container and verify network isolation."""
    config = ctx.obj["config"]

    from .vm import IncusVMManager, _run_incus

    vm_mgr = IncusVMManager(
        image=config.incus_image,
        profile=config.incus_profile,
        anthropic_api_key=config.anthropic_api_key or "test-key",
    )

    vm_mgr.ensure_image_exists()

    click.echo("Launching test container...")
    name = None
    try:
        name = vm_mgr.create_container("test/isolation-check", "/dev/null")
        click.echo(f"  Container: {name}")

        # Test 1: Can reach Anthropic API
        click.echo("\nTest 1: Anthropic API reachability...")
        result = _run_incus(
            "exec", name, "--", "bash", "-c",
            "curl -sf -o /dev/null -w '%{http_code}' --connect-timeout 5 https://api.anthropic.com/ 2>/dev/null || echo 'FAIL'",
            check=False,
        )
        output = result.stdout.strip()
        if output and output != "FAIL":
            click.echo(f"  PASS: api.anthropic.com reachable (HTTP {output})")
        else:
            click.echo("  FAIL: api.anthropic.com unreachable")

        # Test 2: Cannot reach the internet
        click.echo("Test 2: Internet isolation...")
        result = _run_incus(
            "exec", name, "--", "bash", "-c",
            "curl -sf -o /dev/null --connect-timeout 5 https://example.com/ 2>/dev/null && echo 'REACHABLE' || echo 'BLOCKED'",
            check=False,
        )
        output = result.stdout.strip()
        if output == "BLOCKED":
            click.echo("  PASS: example.com blocked")
        else:
            click.echo("  FAIL: example.com was reachable (firewall not configured?)")

        # Test 3: Reverser CLI available
        click.echo("Test 3: Reverser CLI...")
        result = _run_incus(
            "exec", name, "--", "bash", "-lc", "reverser --help",
            check=False,
        )
        if result.returncode == 0:
            click.echo("  PASS: reverser CLI available")
        else:
            click.echo(f"  FAIL: reverser CLI not found: {result.stderr[:200]}")

        click.echo("\nTest complete.")

    finally:
        if name:
            click.echo(f"Destroying test container {name}...")
            vm_mgr.destroy_container(name)
