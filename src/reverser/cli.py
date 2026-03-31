"""CLI entry point for the reverser agent."""

import argparse
import asyncio
import os
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="reverser",
        description="Claude-powered reverse engineering agent",
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

    triage_parser = subparsers.add_parser("triage", help="Quick binary triage")
    add_common_args(triage_parser)

    analyze_parser = subparsers.add_parser("analyze", help="Full reverse engineering analysis")
    add_common_args(analyze_parser)

    solve_parser = subparsers.add_parser("solve", help="Solve a crackme / CTF challenge")
    add_common_args(solve_parser)

    # Writeup command
    writeup_parser = subparsers.add_parser("writeup", help="Generate a markdown writeup from a session log")
    writeup_parser.add_argument("log_file", help="Path to the .jsonl session log")
    writeup_parser.add_argument("-o", "--output", metavar="PATH", help="Output markdown file (default: stdout)")

    args = parser.parse_args()

    if args.command == "writeup":
        _run_writeup(args)
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
    ))


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
