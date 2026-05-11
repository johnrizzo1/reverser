"""Utility tools: file I/O, shell access, and general helpers."""

import os
import re
import shlex

from claude_agent_sdk import tool

from ._common import run_cmd, format_tool_result, format_error, cmd_result_to_tool_result


# ── MCP tool redirect ──────────────────────────────────────────────
# Local models frequently call MCP tool names as shell commands
# (e.g. `bash -c "pe_info /path/to/file"`). We intercept these and
# dispatch to the real tool handler so the call succeeds.

# Map of tool names that models commonly try to run via bash.
# Values are (module_path, handler_name) tuples — resolved lazily.
_TOOL_REDIRECTS: dict[str, tuple[str, str]] = {
    "pe_info":         ("reverser.tools.triage",  "pe_info"),
    "file_info":       ("reverser.tools.triage",  "file_info"),
    "strings_search":  ("reverser.tools.triage",  "strings_search"),
    "checksec_binary": ("reverser.tools.triage",  "checksec_binary"),
    "readelf_info":    ("reverser.tools.triage",  "readelf_info"),
    "binwalk_scan":    ("reverser.tools.triage",  "binwalk_scan"),
    "r2_command":      ("reverser.tools.static",  "r2_command"),
    "r2_decompile":    ("reverser.tools.static",  "r2_decompile"),
    "objdump_disasm":  ("reverser.tools.static",  "objdump_disasm"),
    "nm_symbols":      ("reverser.tools.static",  "nm_symbols"),
    "run_binary":      ("reverser.tools.dynamic", "run_binary"),
    "strace_run":      ("reverser.tools.dynamic", "strace_run"),
    "gdb_batch":       ("reverser.tools.dynamic", "gdb_batch"),
    "nmap_scan":       ("reverser.tools.network", "nmap_scan"),
    "dns_recon":       ("reverser.tools.network", "dns_recon"),
    "nbtscan":         ("reverser.tools.network", "nbtscan"),
    "ldap_search":     ("reverser.tools.network", "ldap_search"),
}


async def _try_redirect_tool(command: str) -> dict | None:
    """If *command* looks like ``tool_name arg1 arg2 ...``, dispatch to the
    real MCP handler and return its result.  Returns None if no redirect."""
    parts = command.strip().split(None, 1)
    if not parts:
        return None
    tool_name = parts[0]
    if tool_name not in _TOOL_REDIRECTS:
        return None

    # Best-effort argument parsing: the first non-flag token is the path
    rest = parts[1] if len(parts) > 1 else ""
    args: dict = {}

    # Strip any flag-like prefixes the model may have added
    tokens = shlex.split(rest) if rest else []
    path_tokens = [t for t in tokens if not t.startswith("-")]
    flag_tokens = [t for t in tokens if t.startswith("-")]

    if path_tokens:
        args["path"] = path_tokens[0]
    # Some tools use "command" (r2_command) or "function" (r2_decompile)
    if tool_name == "r2_command" and len(path_tokens) > 1:
        args["command"] = " ".join(path_tokens[1:])
    elif tool_name == "r2_decompile" and len(path_tokens) > 1:
        args["function"] = path_tokens[1]

    mod_path, func_name = _TOOL_REDIRECTS[tool_name]
    import importlib
    mod = importlib.import_module(mod_path)
    handler = getattr(mod, func_name)
    # The handler is the decorated tool; call its .handler attribute
    real_handler = getattr(handler, "handler", handler)
    return await real_handler(args)


def _fix_r2_arg_order(command: str) -> str:
    """Reorder radare2 CLI arguments so the file comes last.

    Local models frequently produce invocations like:
        radare2 -e opt=val file.exe -c 'afl'
    which fails because radare2 treats everything after the file as
    additional files to open.  This function detects the pattern and
    moves trailing flags before the file argument:
        radare2 -e opt=val -c 'afl' file.exe

    Only touches simple, single-command invocations (no pipes/&&/||).
    """
    stripped = command.strip()
    # Only fix lines that start with radare2 or r2, and aren't piped/chained
    if not re.match(r'^(radare2|r2)\s', stripped):
        return command
    # Don't touch compound shell commands — too risky to reorder.
    # Note: semicolons inside r2 -c arguments (e.g. -c 'aaa; afl') are
    # fine — they're r2 command separators, not shell operators. We only
    # bail on pipes and logical operators which genuinely chain commands.
    if any(op in stripped for op in ['|', '&&', '||', '$(', '`']):
        return command

    try:
        parts = shlex.split(stripped)
    except ValueError:
        return command  # malformed quoting — leave it alone

    if len(parts) < 3:
        return command

    # Identify which part is the binary file (not a flag value).
    # Walk the args: flags consume the next token as their value for
    # -e, -c, -i, -q is standalone, etc.
    FLAG_WITH_VALUE = {'-e', '-c', '-i', '-b', '-B', '-s', '-m', '-p', '-F'}
    binary_idx = None
    trailing_flags = []  # (idx, flag, [value]) tuples found AFTER the binary
    i = 1  # skip argv[0] (radare2/r2)
    while i < len(parts):
        tok = parts[i]
        if tok.startswith('-'):
            if binary_idx is not None:
                # Flag after the binary — needs reordering
                if tok in FLAG_WITH_VALUE and i + 1 < len(parts):
                    trailing_flags.append((i, tok, parts[i + 1]))
                    i += 2
                else:
                    trailing_flags.append((i, tok, None))
                    i += 1
            else:
                # Flag before binary — skip over its value
                if tok in FLAG_WITH_VALUE and i + 1 < len(parts):
                    i += 2
                else:
                    i += 1
        else:
            if binary_idx is None:
                binary_idx = i
            i += 1

    if not trailing_flags or binary_idx is None:
        return command  # nothing to fix

    # Rebuild: [r2, pre-binary-flags..., trailing-flags..., binary]
    pre = parts[1:binary_idx]
    binary = parts[binary_idx]

    reordered_flags = []
    for _, flag, val in trailing_flags:
        reordered_flags.append(flag)
        if val is not None:
            reordered_flags.append(val)

    new_parts = [parts[0]] + pre + reordered_flags + [binary]
    return ' '.join(shlex.quote(p) for p in new_parts)


@tool(
    "write_file",
    "Write content to a file. Use this to save analysis reports, API documentation, "
    "extracted data, or any other output the user requests. Creates parent directories "
    "if needed. Overwrites existing files.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to write"},
            "content": {"type": "string", "description": "Content to write to the file"},
        },
        "required": ["path", "content"],
    },
)
async def write_file(args: dict) -> dict:
    path = args["path"]
    content = args["content"]

    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
    except Exception as e:
        return format_error(f"Failed to write file: {e}")

    return format_tool_result(f"Wrote {len(content)} bytes to {path}")


@tool(
    "read_file",
    "Read the contents of a file. Use this to examine extracted files, configs, "
    "or other data on disk. Returns the file content as text.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to read"},
            "offset": {"type": "integer", "description": "Line offset to start reading from", "default": 0},
            "limit": {"type": "integer", "description": "Max lines to return", "default": 200},
        },
        "required": ["path"],
    },
)
async def read_file(args: dict) -> dict:
    path = args["path"]
    offset = args.get("offset", 0)
    limit = args.get("limit", 200)

    try:
        with open(path, "r") as f:
            content = f.read()
    except Exception as e:
        return format_error(f"Failed to read file: {e}")

    lines = content.split("\n")
    total = len(lines)
    selected = lines[offset:offset + limit]
    end = min(offset + limit, total)

    result = "\n".join(selected)
    if end < total:
        result += f"\n\n[Showing lines {offset + 1}-{end} of {total}]"

    return format_tool_result(result)


@tool(
    "bash",
    "Run a shell command and return its output. Use this for general-purpose tasks "
    "like extracting archives, running scripts, piping commands, listing directories, "
    "or any operation not covered by the specialized RE tools.",
    {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to execute"},
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default: 30, max: 120)",
                "default": 30,
            },
        },
        "required": ["command"],
    },
)
async def bash(args: dict) -> dict:
    command = args["command"]

    # Redirect MCP tool names invoked as shell commands
    redirect = await _try_redirect_tool(command)
    if redirect is not None:
        return redirect

    command = _fix_r2_arg_order(command)
    timeout = min(args.get("timeout", 30), 120)

    result = run_cmd(
        ["bash", "-c", command],
        timeout=timeout,
        max_output=16000,
    )
    return cmd_result_to_tool_result(result)


@tool(
    "list_directory",
    "List files and directories at the given path. Useful for exploring extracted "
    "archives, finding APK contents, or navigating the filesystem.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path to list"},
            "recursive": {
                "type": "boolean",
                "description": "List recursively (default: false)",
                "default": False,
            },
        },
        "required": ["path"],
    },
)
async def list_directory(args: dict) -> dict:
    path = args["path"]
    recursive = args.get("recursive", False)

    if not os.path.isdir(path):
        return format_error(f"Not a directory: {path}")

    if recursive:
        result = run_cmd(["find", path, "-type", "f"], max_output=16000)
        return cmd_result_to_tool_result(result)

    try:
        entries = sorted(os.listdir(path))
        lines = []
        for entry in entries:
            full = os.path.join(path, entry)
            if os.path.isdir(full):
                lines.append(f"  {entry}/")
            else:
                size = os.path.getsize(full)
                lines.append(f"  {entry}  ({size} bytes)")
        return format_tool_result("\n".join(lines))
    except Exception as e:
        return format_error(f"Failed to list directory: {e}")


TOOLS = [write_file, read_file, bash, list_directory]
