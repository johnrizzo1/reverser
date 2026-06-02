"""CLI entry point for the reverser agent."""

import argparse
import asyncio
import os
import sys

from .tools._common import is_url, maybe_extract_archive


def main():
    parser = argparse.ArgumentParser(
        prog="reverser",
        description="AI-powered reverse engineering and web penetration testing agent",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v: tool calls/results, -vv: +thinking)",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="List all resumable sessions across targets and exit.",
    )
    parser.add_argument(
        "--check-targets",
        action="store_true",
        help="Scan targets/ for non-canonical (bogus) target directories "
             "and print a cleanup recommendation, then exit.",
    )
    # subcommand is required UNLESS --list-sessions is given (handled below)
    subparsers = parser.add_subparsers(dest="command", required=False)

    # Shared arguments for analysis commands
    def add_common_args(sub):
        sub.add_argument("binary", help="Path to the binary to analyze")
        sub.add_argument("--budget", type=float, default=2.0, help="Max USD budget (default: 2.0)")
        sub.add_argument("--log", metavar="PATH", help="Session log path (default: logs/<binary>_<timestamp>.jsonl)")
        sub.add_argument("--log-dir", metavar="DIR", help="Directory for session logs (default: ./logs)")

    # Shared backend arguments
    def add_backend_args(sub):
        sub.add_argument("--backend", "-b", default="claude",
                         help="LLM backend: claude, ollama, lmstudio, or any "
                              "OpenAI-compatible server (default: claude)")
        sub.add_argument("--model", "-m", default=None,
                         help="Model name/tag for non-claude backends (e.g. qwen3.5:35b-a3b-coding-nvfp4)")
        sub.add_argument("--api-base", default=None,
                         help="API base URL override (default: http://localhost:11434/v1 for ollama)")
        sub.add_argument("--model-family", default=None, choices=["deepseek", "generic"],
                         help="Override model-family detection. By default the family "
                              "is inferred from the model name. Use 'deepseek' for "
                              "DeepSeek-derived models that don't have 'deepseek' in "
                              "the name.")

    triage_parser = subparsers.add_parser("triage", help="Quick binary triage")
    add_common_args(triage_parser)
    add_backend_args(triage_parser)

    analyze_parser = subparsers.add_parser("analyze", help="Full reverse engineering analysis")
    add_common_args(analyze_parser)
    add_backend_args(analyze_parser)

    solve_parser = subparsers.add_parser("solve", help="Solve a crackme / CTF challenge")
    add_common_args(solve_parser)
    add_backend_args(solve_parser)

    # Interactive TUI
    interactive_parser = subparsers.add_parser(
        "interactive",
        aliases=["i"],
        help="Launch interactive TUI for guided analysis",
    )
    interactive_parser.add_argument("target", nargs="?", default="",
                                    help="Path to binary or target URL for web pentest")
    interactive_parser.add_argument("--profile", "-p", default="general",
                                    help="Agent profile (general, linux, windows, android, chrome, managed, "
                                         "api, pentest, ad, ctf, webpentest, webapi, webrecon, manager)")
    interactive_parser.add_argument("--budget", type=float, default=5.0, help="Max USD budget (default: 5.0)")
    interactive_parser.add_argument("--max-turns", type=int, default=50, help="Max agent turns per interaction (default: 50)")
    interactive_parser.add_argument("--max-parallel", type=int, default=1, metavar="N",
                                    help="Maximum parallel specialist dispatches (manager profile only). "
                                         "Default 1 (sequential). Increase only for safe-to-parallelize work like "
                                         "external recon across distinct subnets.")
    interactive_parser.add_argument("--list-profiles", action="store_true", help="List available profiles and exit")
    interactive_parser.add_argument(
        "--resume",
        nargs="?",
        const="__latest__",
        default=None,
        metavar="SESSION_ID",
        help="Resume a session. With no SESSION_ID, resumes the latest "
             "(target-scoped if a target arg is given, else global).",
    )
    interactive_parser.add_argument(
        "--force",
        action="store_true",
        help="With --resume: take over a session whose process is still running.",
    )
    interactive_parser.add_argument(
        "--address",
        default=None,
        help="Override the target's primary address for this session",
    )
    add_backend_args(interactive_parser)
    interactive_parser.add_argument("--validation-backend", default=None,
        help="Backend for adversarial hypothesis validation (e.g. claude, ollama). "
             "If unset, no adversarial validation runs.")
    interactive_parser.add_argument("--validation-model", default=None,
        help="Model for the validation backend (ideally different from the main model).")
    interactive_parser.add_argument("--validation-api-base", default=None,
        help="API base for the validation backend.")
    interactive_parser.add_argument("--token-cost-per-1k", type=float, default=0.0,
        help="Pseudo-USD cost per 1k tokens for local (OpenAI-compatible) backends, "
             "so token usage counts toward the budget. Default 0 (tokens tracked, no cost).")

    # Target management subcommand group
    target_parser = subparsers.add_parser("target", help="Manage targets")
    target_sub = target_parser.add_subparsers(dest="target_cmd", required=True)

    p_create = target_sub.add_parser("create", help="Create a new target")
    p_create.add_argument("name")
    p_create.add_argument("--kind", choices=["network", "binary"], required=True)
    p_create.add_argument("--address", required=True)
    p_create.add_argument("--label", default=None)

    target_sub.add_parser("list", help="List all targets")

    p_show = target_sub.add_parser("show", help="Show a target's details")
    p_show.add_argument("name")

    p_rename = target_sub.add_parser("rename", help="Rename a target")
    p_rename.add_argument("old_name")
    p_rename.add_argument("new_name")

    p_add = target_sub.add_parser("add-address", help="Add an address to a target")
    p_add.add_argument("name")
    p_add.add_argument("value")
    p_add.add_argument("--label", default=None)
    p_add.add_argument("--primary", action="store_true",
                       help="Promote the new address to primary")

    p_setp = target_sub.add_parser("set-primary", help="Promote an address to primary")
    p_setp.add_argument("name")
    p_setp.add_argument("address",
                        help="Address id (uuid hex) or address value")

    p_ret = target_sub.add_parser("retire-address", help="Mark an address retired")
    p_ret.add_argument("name")
    p_ret.add_argument("address")

    # Writeup command
    writeup_parser = subparsers.add_parser("writeup", help="Generate a markdown writeup from a session log")
    writeup_parser.add_argument("log_file", help="Path to the .jsonl session log")
    writeup_parser.add_argument("-o", "--output", metavar="PATH", help="Output markdown file (default: stdout)")

    # GUI command — Electron desktop UI
    gui_parser = subparsers.add_parser(
        "gui", aliases=["g"],
        help="Launch the Electron desktop UI (dev mode)",
    )
    gui_parser.add_argument(
        "--skip-install", action="store_true",
        help="Skip the npm install check (faster startup, fails if deps missing)",
    )

    args = parser.parse_args()

    from reverser import paths as _paths
    _paths.log_resolved_roots()

    # Top-level --list-sessions short-circuit (no subcommand required)
    if args.list_sessions:
        _run_list_sessions()
        return

    # Top-level --check-targets short-circuit
    if args.check_targets:
        _run_check_targets()
        return

    if args.command is None:
        parser.print_help(sys.stderr)
        sys.exit(2)

    if args.command == "target":
        _run_target(args)
    elif args.command == "writeup":
        _run_writeup(args)
    elif args.command in ("interactive", "i"):
        _run_interactive(args)
    elif args.command in ("gui", "g"):
        _run_gui(args)
    else:
        _run_agent(args)


def _run_list_sessions():
    from .sessions import list_all
    snapshots = list_all()
    if not snapshots:
        print("No sessions found.")
        return

    print("Sessions across all targets:")
    print(f"  {'TARGET':<16} {'ID':<24} {'STATE':<11} {'PROFILE':<10} "
          f"{'STARTED':<20} {'LAST ACTIVE':<20} {'TURNS':>6} {'COST':>8}")
    for s in snapshots:
        cost_str = f"${s.stats.total_cost:.2f}"
        print(
            f"  {s.target:<16} {s.session_id:<24} {s.state:<11} "
            f"{s.config.profile:<10} {s.started_at:<20} "
            f"{s.last_active_at:<20} {s.stats.turns:>6} {cost_str:>8}"
        )
    print()
    print("Resume the latest session for a target with: reverser i <target> --resume")
    print("Resume a specific session with:              reverser i --resume <ID>")


def _run_check_targets():
    """Scan targets/ for non-canonical directories. Advisory-only — no auto-cleanup."""
    from .sessions import _is_canonical_target_name, _targets_root
    root = _targets_root()
    if not root.is_dir():
        print(f"No targets/ directory at {root}.")
        return
    bogus = []
    canonical_count = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        if _is_canonical_target_name(entry.name):
            canonical_count += 1
        else:
            bogus.append(entry)
    if not bogus:
        print(f"✓ All {canonical_count} target directories have canonical names.")
        return
    print(f"⚠ {len(bogus)} non-canonical (bogus) target directories detected:")
    print()
    for b in bogus:
        print(f"  {b}")
    print()
    print("These were created by past CLI parsing bugs (URL schemes, free-text "
          "targets, CIDR slashes, etc.). The new input validation (shipped "
          "2026-05-12) prevents new bogus dirs. To clean up:")
    print()
    for b in bogus:
        print(f"  rm -rf {b!s}")
    print()
    print("If any of these contain real KB data you want to preserve, move the "
          "relevant files to the canonical target dir before deleting.")


def _run_agent(args):
    binary = os.path.abspath(args.binary)
    if not os.path.isfile(binary):
        print(f"Error: file not found: {binary}", file=sys.stderr)
        sys.exit(1)

    # If it's a zip archive, extract and use the extraction directory
    extract_dir, members = maybe_extract_archive(binary)
    if extract_dir:
        print(f"Extracted zip to: {extract_dir}", file=sys.stderr)
        for m in members:
            print(f"  {os.path.relpath(m, extract_dir)}", file=sys.stderr)
        if len(members) == 1:
            binary = members[0]
        else:
            binary = extract_dir

    from .session_log import session_log_path

    log_path = args.log
    if log_path is None:
        log_path = session_log_path(binary, log_dir=args.log_dir)

    from .agent import run_agent
    asyncio.run(run_agent(
        binary,
        mode=args.command,
        budget=args.budget,
        verbosity=args.verbose,
        log_path=log_path,
        backend_name=args.backend,
        model=args.model,
        api_base=args.api_base,
        model_family=args.model_family,
    ))


def _validate_target_arg(target: str) -> tuple[bool, str | None]:
    """Quick validation gate. Returns (is_valid, error_message).

    Designed to reject the kinds of inputs we've seen go wrong: pasted
    multi-line text, target identifiers > 120 chars, things that look like
    sentences rather than network identifiers. Defense-in-depth — target_key
    in sessions.py would still scrub these, but a CLI-level error is better UX.

    See docs/superpowers/specs/2026-05-12-manager-reliability-design.md §9.3.
    """
    if not target:
        return True, None  # empty is fine — TUI prompts for it

    target = target.strip()

    if len(target) > 120:
        return False, (
            f"Target argument is {len(target)} chars (max 120). "
            "Did you accidentally paste a description or scenario text? "
            "Pass just the IP, hostname, or URL."
        )

    # Multi-line input
    if "\n" in target or "\r" in target:
        return False, (
            "Target argument contains newlines. "
            "Pass a single-line IP, hostname, or URL."
        )

    # Whitespace inside (after strip) — looks like a sentence
    if " " in target or "\t" in target:
        return False, (
            f"Target argument contains whitespace: {target!r}. "
            "Pass a single token (IP, hostname, or URL — no spaces)."
        )

    return True, None


def _run_interactive(args):
    if getattr(args, "list_profiles", False):
        from .profiles import list_profiles
        for p in list_profiles():
            print(f"  {p.key:10s}  {p.name}")
            print(f"             {p.description}")
            print(f"             Skills: {', '.join(s.name for s in p.skills)}")
            print()
        return

    # Validate target argument BEFORE doing any work (per spec §9.3)
    target_arg = getattr(args, "target", "") or ""
    ok, err = _validate_target_arg(target_arg)
    if not ok:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(2)

    target = getattr(args, "target", "") or ""
    profile_key = args.profile
    resume_snap = None

    # Handle --resume routing
    if getattr(args, "resume", None) is not None:
        resume_snap = _resolve_resume(args, target)
        if resume_snap is None:
            sys.exit(1)
        # Snapshot wins for target/profile/budget/max_turns unless explicitly overridden
        target = resume_snap.target
        profile_explicit = any(a in ("-p", "--profile") for a in sys.argv[1:])
        budget_explicit = any(a == "--budget" for a in sys.argv[1:])
        max_turns_explicit = any(a == "--max-turns" for a in sys.argv[1:])
        if profile_explicit and profile_key != resume_snap.config.profile:
            print(
                f"Error: resume must use the same profile (snapshot uses "
                f"{resume_snap.config.profile!r}; got -p {profile_key!r}). "
                f"Drop -p to use the snapshot's profile, or start a new session.",
                file=sys.stderr,
            )
            sys.exit(1)
        profile_key = resume_snap.config.profile
        budget = args.budget if budget_explicit else resume_snap.config.budget
        max_turns = args.max_turns if max_turns_explicit else resume_snap.config.max_turns
    else:
        budget = args.budget
        max_turns = args.max_turns

    # resolved_target_obj is set when we go through resolve_target so that
    # run_tui can use AgentSession.from_target (populates target_name +
    # active_address_id on the session snapshot).
    resolved_target_obj = None

    if target and resume_snap is None:
        # Only validate/resolve target on fresh sessions (resume targets came from snapshot)
        if is_url(target):
            # Web targets: resolve through Target model so the snapshot carries identity
            from .session_start import resolve_target as _resolve_target
            override_addr = getattr(args, "address", None)
            try:
                resolved_target_obj = _resolve_target(target, override_address=override_addr)
                target = resolved_target_obj.primary_address.value
            except Exception as exc:
                print(f"Error resolving target: {exc}", file=sys.stderr)
                sys.exit(1)
        else:
            # Could be a named target, a file path, or a raw network address.
            # Try resolve_target first (handles named targets + raw IPs/hostnames).
            # Fall back to legacy file-path logic if it looks like a file.
            from .session_start import resolve_target as _resolve_target
            override_addr = getattr(args, "address", None)
            # If it looks like an existing file path, use legacy extraction then resolve.
            abs_path = os.path.abspath(target)
            if os.path.isfile(abs_path):
                # Zip extraction before Target resolution
                extract_dir, members = maybe_extract_archive(abs_path)
                if extract_dir:
                    print(f"Extracted zip to: {extract_dir}", file=sys.stderr)
                    for m in members:
                        print(f"  {os.path.relpath(m, extract_dir)}", file=sys.stderr)
                    abs_path = members[0] if len(members) == 1 else extract_dir
                target = abs_path
                try:
                    resolved_target_obj = _resolve_target(target, override_address=override_addr)
                    target = resolved_target_obj.primary_address.value
                except Exception as exc:
                    print(f"Error resolving target: {exc}", file=sys.stderr)
                    sys.exit(1)
            else:
                # Named target or raw network address — resolve_target handles all cases
                try:
                    resolved_target_obj = _resolve_target(target, override_address=override_addr)
                    target = resolved_target_obj.primary_address.value
                except Exception as exc:
                    print(f"Error resolving target: {exc}", file=sys.stderr)
                    sys.exit(1)

    from .tui.app import run_tui
    run_tui(
        binary_path=target,
        profile=profile_key,
        budget=budget,
        max_turns=max_turns,
        backend=args.backend,
        model=args.model,
        api_base=args.api_base,
        validation_backend=getattr(args, "validation_backend", None),
        validation_model=getattr(args, "validation_model", None),
        validation_api_base=getattr(args, "validation_api_base", None),
        token_cost_per_1k=getattr(args, "token_cost_per_1k", 0.0),
        resume_from=resume_snap,
        target_obj=resolved_target_obj,
    )


def _resolve_resume(args, target_arg):
    """Resolve --resume value into a SessionSnapshot or print error and return None."""
    from .sessions import (
        load, latest_for_target, latest_global, list_all,
        is_session_alive,
        SessionNotFoundError, SessionStateError,
    )

    resume_value = args.resume

    if resume_value == "__latest__":
        # No specific ID — pick the latest
        if target_arg:
            snap = latest_for_target(target_arg, exclude_completed=True)
            if snap is None:
                print(
                    f"Error: no resumable sessions for {target_arg}. "
                    f"Start a new session with: reverser i -p <profile> {target_arg}",
                    file=sys.stderr,
                )
                return None
        else:
            snap = latest_global(exclude_completed=True)
            if snap is None:
                print(
                    "Error: no sessions to resume. "
                    "Start with: reverser i -p <profile> <target>",
                    file=sys.stderr,
                )
                return None
    else:
        # Specific session ID — find it across targets if no target_arg
        target = target_arg
        if not target:
            for s in list_all():
                if s.session_id == resume_value:
                    target = s.target
                    break
            if not target:
                print(
                    f"Error: no snapshot with session_id={resume_value!r} found. "
                    f"Run reverser --list-sessions to see available sessions.",
                    file=sys.stderr,
                )
                return None
        try:
            snap = load(target, resume_value)
        except SessionNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            return None

    # Reject completed sessions
    if snap.state == "completed":
        print(
            f"Error: session {snap.session_id} is completed and cannot be resumed. "
            f"Run reverser --list-sessions to see other options.",
            file=sys.stderr,
        )
        return None

    # Liveness check
    if is_session_alive(snap) and not args.force:
        print(
            f"Error: session {snap.session_id} is currently running in PID {snap.pid}. "
            f"Use --force to take over (warning: the original process's writes will conflict).",
            file=sys.stderr,
        )
        return None

    return snap


def _resolve_address_id(target, ident):
    """Return address.id for the address matching by id or value; raise SystemExit if not found."""
    for a in target.addresses:
        if a.id == ident or a.value == ident:
            return a.id
    raise SystemExit(f"No address matching {ident!r} on target {target.name!r}")


def _run_target(args):
    if args.target_cmd == "create":
        from reverser import targets
        t = targets.create_target(
            name=args.name, kind=args.kind,
            initial_address=args.address, label=args.label,
        )
        print(f"Created target {t.name!r} with address {t.primary_address.value}")
        return

    if args.target_cmd == "list":
        from reverser import targets
        for t in targets.list_targets():
            print(f"{t.name}\t{t.kind}\t{t.primary_address.value}\t({len(t.addresses)} addrs)")
        return

    if args.target_cmd == "show":
        from reverser import targets
        t = targets.load_target(args.name)
        import json as _json
        print(_json.dumps(t.to_dict(), indent=2, sort_keys=True))
        return

    if args.target_cmd == "rename":
        from reverser import targets
        t = targets.rename_target(args.old_name, args.new_name)
        print(f"Renamed to {t.name!r}")
        return

    if args.target_cmd == "add-address":
        from reverser import targets
        t = targets.load_target(args.name)
        kind = targets._infer_address_kind(args.value, t.kind)
        t = targets.add_address(t, args.value, kind=kind,
                                label=args.label, make_primary=args.primary)
        print(f"Added {args.value}; primary is now {t.primary_address.value}")
        return

    if args.target_cmd == "set-primary":
        from reverser import targets
        t = targets.load_target(args.name)
        addr_id = _resolve_address_id(t, args.address)
        t = targets.set_primary(t, addr_id)
        print(f"Primary set to {t.primary_address.value}")
        return

    if args.target_cmd == "retire-address":
        from reverser import targets
        t = targets.load_target(args.name)
        addr_id = _resolve_address_id(t, args.address)
        t = targets.retire_address(t, addr_id)
        print("Address retired")
        return


def _run_writeup(args):
    log_file = args.log_file
    if not os.path.isfile(log_file):
        print(f"Error: log file not found: {log_file}", file=sys.stderr)
        sys.exit(1)

    from .writeup import generate_writeup

    md = generate_writeup(log_file)

    if args.output:
        with open(args.output, "w") as f:
            f.write(md)
        print(f"Writeup saved to: {args.output}", file=sys.stderr)
    else:
        print(md)


def _run_gui(args):
    """Launch the Electron desktop UI in dev mode.

    Resolves desktop/ relative to this package (../.. from cli.py), installs
    npm deps if node_modules/ is missing, then execs `npm run dev`. The
    Python service is supervised by Electron's main process, not by us.
    """
    import shutil
    import subprocess
    from pathlib import Path

    desktop = Path(__file__).resolve().parent.parent.parent / "desktop"
    if not desktop.is_dir():
        print(f"Error: desktop/ not found at {desktop}", file=sys.stderr)
        print("The Electron UI lives at <repo-root>/desktop. Make sure you're "
              "running from a checkout that includes it.", file=sys.stderr)
        sys.exit(1)

    npm = shutil.which("npm")
    if npm is None:
        print("Error: `npm` not found on PATH.", file=sys.stderr)
        print("Install Node.js 18+ (e.g. via your devenv shell, brew, or nvm) "
              "and try again.", file=sys.stderr)
        sys.exit(1)

    node_modules = desktop / "node_modules"
    if not node_modules.is_dir() and not args.skip_install:
        print(f"First run: installing npm deps in {desktop} (this can take "
              "a minute — Electron downloads ~80 MB)...", file=sys.stderr)
        rc = subprocess.run([npm, "install"], cwd=desktop).returncode
        if rc != 0:
            print(f"npm install failed (exit {rc}).", file=sys.stderr)
            sys.exit(rc)

    # Hand off to npm. Use subprocess.run so Ctrl-C in our terminal cleanly
    # terminates the child (npm forwards SIGINT to vite + electron).
    try:
        rc = subprocess.run([npm, "run", "dev"], cwd=desktop).returncode
    except KeyboardInterrupt:
        rc = 130
    sys.exit(rc)
