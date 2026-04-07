"""CLI entry point for the reverser agent."""

import argparse
import asyncio
import os
import sys

from .tools._common import is_url

# Profiles that operate on web targets rather than binary files
_WEB_PROFILES = {"webpentest", "webapi", "webrecon"}


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
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Shared arguments for analysis commands
    def add_common_args(sub):
        sub.add_argument("binary", help="Path to the binary to analyze")
        sub.add_argument("--budget", type=float, default=2.0, help="Max USD budget (default: 2.0)")
        sub.add_argument("--log", metavar="PATH", help="Session log path (default: logs/<binary>_<timestamp>.jsonl)")
        sub.add_argument("--log-dir", metavar="DIR", help="Directory for session logs (default: ./logs)")

    # Shared backend arguments
    def add_backend_args(sub):
        sub.add_argument("--backend", "-b", default="claude",
                         help="LLM backend: claude, ollama, or any OpenAI-compatible server (default: claude)")
        sub.add_argument("--model", "-m", default=None,
                         help="Model name/tag for non-claude backends (e.g. qwen3.5:35b-a3b-coding-nvfp4)")
        sub.add_argument("--api-base", default=None,
                         help="API base URL override (default: http://localhost:11434/v1 for ollama)")

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
                                         "api, ctf, webpentest, webapi, webrecon)")
    interactive_parser.add_argument("--budget", type=float, default=5.0, help="Max USD budget (default: 5.0)")
    interactive_parser.add_argument("--max-turns", type=int, default=50, help="Max agent turns per interaction (default: 50)")
    interactive_parser.add_argument("--list-profiles", action="store_true", help="List available profiles and exit")
    add_backend_args(interactive_parser)

    # Writeup command
    writeup_parser = subparsers.add_parser("writeup", help="Generate a markdown writeup from a session log")
    writeup_parser.add_argument("log_file", help="Path to the .jsonl session log")
    writeup_parser.add_argument("-o", "--output", metavar="PATH", help="Output markdown file (default: stdout)")

    args = parser.parse_args()

    if args.command == "writeup":
        _run_writeup(args)
    elif args.command in ("interactive", "i"):
        _run_interactive(args)
    else:
        _run_agent(args)


def _run_agent(args):
    binary = os.path.abspath(args.binary)
    if not os.path.isfile(binary):
        print(f"Error: file not found: {binary}", file=sys.stderr)
        sys.exit(1)

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
    ))


def _run_interactive(args):
    if getattr(args, "list_profiles", False):
        from .profiles import list_profiles
        for p in list_profiles():
            print(f"  {p.key:10s}  {p.name}")
            print(f"             {p.description}")
            print(f"             Skills: {', '.join(s.name for s in p.skills)}")
            print()
        return

    target = getattr(args, "target", "") or ""
    profile_key = args.profile
    is_web_profile = profile_key in _WEB_PROFILES

    if target:
        if is_url(target):
            # URL target — valid for web profiles (and we'll allow it for any profile)
            pass
        else:
            # File path — resolve and validate
            target = os.path.abspath(target)
            if not os.path.isfile(target):
                print(f"Error: file not found: {target}", file=sys.stderr)
                sys.exit(1)

    from .tui.app import run_tui
    run_tui(
        binary_path=target,
        profile=profile_key,
        budget=args.budget,
        max_turns=args.max_turns,
        backend=args.backend,
        model=args.model,
        api_base=args.api_base,
    )


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
