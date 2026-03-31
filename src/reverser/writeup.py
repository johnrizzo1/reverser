"""Generate a markdown writeup from a session log."""

import json
import os
from datetime import datetime

from .session_log import load_session_log


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.split("\n"))


def _format_tool_input(input_dict: dict) -> str:
    """Format tool input for display, keeping it readable."""
    parts = []
    for k, v in input_dict.items():
        if isinstance(v, str) and len(v) > 120:
            v = v[:120] + "..."
        parts.append(f"  {k}: {v}")
    return "\n".join(parts)


def generate_writeup(log_path: str) -> str:
    """Generate a markdown writeup from a JSONL session log."""
    entries = load_session_log(log_path)
    if not entries:
        return "# Empty Session Log\n\nNo entries found.\n"

    lines = []

    # Header from session_start
    start = next((e for e in entries if e["type"] == "session_start"), None)
    end = next((e for e in entries if e["type"] == "session_end"), None)

    binary_name = os.path.basename(start["binary"]) if start else "unknown"
    mode = start["mode"] if start else "unknown"

    lines.append(f"# Reverse Engineering Writeup: `{binary_name}`\n")

    if start:
        ts = start.get("ts", "")
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                lines.append(f"**Date:** {dt.strftime('%Y-%m-%d %H:%M UTC')}")
            except ValueError:
                pass
        lines.append(f"**Mode:** {mode}")
        lines.append(f"**Binary:** `{start['binary']}`")
    if end:
        if end.get("cost"):
            lines.append(f"**Cost:** ${end['cost']:.4f}")
        if end.get("turns"):
            lines.append(f"**Turns:** {end['turns']}")
        lines.append(f"**Result:** {end.get('subtype', 'unknown')}")
    lines.append("")

    lines.append("---\n")

    current_turn = 0
    turn_has_heading = False

    for entry in entries:
        etype = entry["type"]

        if etype == "turn":
            current_turn = entry["turn"]
            turn_has_heading = False

        elif etype == "thinking":
            if not turn_has_heading:
                lines.append(f"### Turn {current_turn}\n")
                turn_has_heading = True
            lines.append("<details>")
            lines.append("<summary>Agent reasoning</summary>\n")
            lines.append(entry["text"])
            lines.append("\n</details>\n")

        elif etype == "tool_call":
            if not turn_has_heading:
                lines.append(f"### Turn {current_turn}\n")
                turn_has_heading = True
            name = entry["name"]
            # Strip MCP prefix for readability
            display_name = name.replace("mcp__re__", "")
            lines.append(f"**Tool:** `{display_name}`")
            lines.append("```")
            lines.append(_format_tool_input(entry["input"]))
            lines.append("```\n")

        elif etype == "tool_result":
            content = entry.get("content", "")
            is_error = entry.get("is_error", False)
            label = "Error" if is_error else "Result"

            # Truncate very long results in the writeup
            result_lines = content.split("\n")
            if len(result_lines) > 60:
                content = "\n".join(result_lines[:60]) + f"\n\n... ({len(result_lines) - 60} more lines)"

            lines.append(f"<details>")
            lines.append(f"<summary>{label} ({len(result_lines)} lines)</summary>\n")
            lines.append(f"```")
            lines.append(content)
            lines.append(f"```\n")
            lines.append("</details>\n")

        elif etype == "text":
            text = entry["text"].strip()
            if text:
                lines.append(text)
                lines.append("")

    # Final summary
    if end and end.get("result"):
        lines.append("---\n")
        lines.append("## Final Result\n")
        lines.append(end["result"])
        lines.append("")

    return "\n".join(lines)
