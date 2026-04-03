"""Utility tools: file I/O, shell access, and general helpers."""

import os

from claude_agent_sdk import tool

from ._common import run_cmd, format_tool_result, format_error, cmd_result_to_tool_result


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
