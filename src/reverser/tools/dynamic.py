"""Dynamic analysis tools: system call tracing, debugging, execution."""

from claude_agent_sdk import tool

from ._common import (
    run_cmd, format_tool_result, format_error, cmd_result_to_tool_result,
    is_pe, wine_wrap,
)


@tool(
    "run_binary",
    "Run a binary and capture its output. Automatically uses wine for Windows PE executables. "
    "Provide optional stdin_data and command-line args.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the binary to run"},
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Command-line arguments to pass to the binary",
                "default": [],
            },
            "stdin_data": {"type": "string", "description": "Data to send to binary's stdin"},
            "timeout": {"type": "integer", "description": "Max seconds to run", "default": 10},
        },
        "required": ["path"],
    },
)
async def run_binary(args: dict) -> dict:
    path = args["path"]
    binary_args = args.get("args", [])
    cmd = wine_wrap([path] + binary_args, path)

    result = run_cmd(
        cmd,
        timeout=args.get("timeout", 10),
        stdin_data=args.get("stdin_data"),
    )
    output = result["stdout"]
    if result["stderr"]:
        # Filter wine debug noise
        stderr_lines = [
            line for line in result["stderr"].split("\n")
            if not line.startswith("wine: ") and not line.startswith("00")
            and "fixme:" not in line and "err:" not in line
        ]
        filtered_stderr = "\n".join(stderr_lines).strip()
        if filtered_stderr:
            output += f"\n\n[stderr]: {filtered_stderr[:1000]}"
    return format_tool_result(output)


@tool(
    "strace_run",
    "Run a binary under strace to trace system calls. For Windows PE binaries, traces the wine process. "
    "Optionally filter to specific syscall categories with trace_filter "
    "(e.g. 'file', 'network', 'process', 'memory', or specific syscalls like 'open,read,write').",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the binary to trace"},
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Command-line arguments to pass to the binary",
                "default": [],
            },
            "stdin_data": {"type": "string", "description": "Data to send to binary's stdin"},
            "trace_filter": {"type": "string", "description": "strace -e trace= filter (e.g. 'file', 'network', 'open,read')"},
            "timeout": {"type": "integer", "description": "Max seconds to run", "default": 10},
        },
        "required": ["path"],
    },
)
async def strace_run(args: dict) -> dict:
    path = args["path"]
    binary_args = args.get("args", [])

    cmd = ["strace", "-f", "-s", "256"]
    trace_filter = args.get("trace_filter")
    if trace_filter:
        cmd += ["-e", f"trace={trace_filter}"]

    # strace wraps the whole command — wine included
    cmd += wine_wrap([path] + binary_args, path)

    result = run_cmd(
        cmd,
        timeout=args.get("timeout", 10),
        stdin_data=args.get("stdin_data"),
    )
    # strace outputs to stderr
    output = result["stderr"] or result["stdout"]
    if len(output) > 8000:
        output = output[:8000] + "\n\n[OUTPUT TRUNCATED — use trace_filter to narrow scope]"
    return format_tool_result(output)


@tool(
    "gdb_batch",
    "Run GDB in batch mode with a series of commands. For Windows PE binaries, uses 'wine' as the target "
    "and passes the binary path as an argument (GDB attaches to the wine process). "
    "Each command is a GDB command like 'break main', 'run', 'info registers', "
    "'x/20x $rsp', 'disassemble main', 'continue'. Commands are separated by newlines.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the binary to debug"},
            "commands": {"type": "string", "description": "GDB commands, one per line"},
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Arguments to pass to the binary being debugged",
                "default": [],
            },
            "timeout": {"type": "integer", "description": "Max seconds to run", "default": 15},
        },
        "required": ["path", "commands"],
    },
)
async def gdb_batch(args: dict) -> dict:
    path = args["path"]
    binary_args = args.get("args", [])

    commands = args["commands"].strip().split("\n")
    cmd = ["gdb", "-batch", "-nx"]
    for c in commands:
        c = c.strip()
        if c:
            cmd += ["-ex", c]

    if is_pe(path):
        # Debug wine running the PE
        cmd += ["--args", "wine", path] + binary_args
    else:
        cmd += ["--args", path] + binary_args

    result = run_cmd(cmd, timeout=args.get("timeout", 15))
    return cmd_result_to_tool_result(result)


TOOLS = [run_binary, strace_run, gdb_batch]
