"""Static analysis tools: disassembly, decompilation, symbol listing via radare2 and binutils."""

from claude_agent_sdk import tool

from ._common import run_cmd, paginate, format_tool_result, format_error, cmd_result_to_tool_result


@tool(
    "r2_command",
    "Run a radare2 command on a binary. The binary is opened, analyzed (aaa), then your command is executed. "
    "Append 'j' to most commands for JSON output (e.g. 'aflj' for function list, 'izj' for strings, 'pdfj @main' for disassembly). "
    "Common commands: afl (functions), pdf @func (disasm), pdc @func (pseudo-C), iz (strings), ii (imports), "
    "ie (entry points), axt @addr (xrefs to), axf @addr (xrefs from), /R pattern (ROP search).",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the binary file"},
            "command": {"type": "string", "description": "Radare2 command to execute after analysis"},
        },
        "required": ["path", "command"],
    },
)
async def r2_command(args: dict) -> dict:
    try:
        import r2pipe
    except ImportError:
        return format_error("r2pipe not installed. Run inside devenv shell.")

    try:
        r2 = r2pipe.open(args["path"], flags=["-2"])  # -2 suppresses stderr
        r2.cmd("aaa")
        output = r2.cmd(args["command"])
        r2.quit()
    except Exception as e:
        return format_error(f"radare2 error: {e}")

    if len(output) > 8000:
        output = output[:8000] + "\n\n[OUTPUT TRUNCATED — refine your query or use pagination commands in r2]"
    return format_tool_result(output)


@tool(
    "r2_decompile",
    "Decompile a function to pseudo-C using radare2. Provide the function name (e.g. 'main', 'sym.check_key') "
    "or address (e.g. '0x401000').",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the binary file"},
            "function": {"type": "string", "description": "Function name or address to decompile"},
        },
        "required": ["path", "function"],
    },
)
async def r2_decompile(args: dict) -> dict:
    try:
        import r2pipe
    except ImportError:
        return format_error("r2pipe not installed. Run inside devenv shell.")

    try:
        r2 = r2pipe.open(args["path"], flags=["-2"])
        r2.cmd("aaa")
        output = r2.cmd(f"pdc @{args['function']}")
        if not output.strip():
            output = r2.cmd(f"pdf @{args['function']}")
        r2.quit()
    except Exception as e:
        return format_error(f"radare2 decompile error: {e}")

    if len(output) > 8000:
        output = output[:8000] + "\n\n[OUTPUT TRUNCATED]"
    return format_tool_result(output)


@tool(
    "objdump_disasm",
    "Disassemble a binary with objdump. Optionally filter to a specific symbol. Results are paginated.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the binary file"},
            "symbol": {"type": "string", "description": "Symbol/function to disassemble (uses --disassemble=symbol)"},
            "offset": {"type": "integer", "description": "Line offset for pagination", "default": 0},
            "limit": {"type": "integer", "description": "Max lines to return", "default": 80},
        },
        "required": ["path"],
    },
)
async def objdump_disasm(args: dict) -> dict:
    cmd = ["objdump", "-d", "-M", "intel"]
    symbol = args.get("symbol")
    if symbol:
        cmd += [f"--disassemble={symbol}"]
    cmd.append(args["path"])

    result = run_cmd(cmd, max_output=100000)
    if result["returncode"] != 0:
        return cmd_result_to_tool_result(result)

    page = paginate(result["stdout"], args.get("offset", 0), args.get("limit", 80))
    return format_tool_result(f"{page['showing']}\n\n{page['content']}")


@tool(
    "nm_symbols",
    "List symbols from a binary using nm. Shows demangled names. Use grep_pattern to filter.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the binary file"},
            "grep_pattern": {"type": "string", "description": "Optional pattern to filter symbols"},
        },
        "required": ["path"],
    },
)
async def nm_symbols(args: dict) -> dict:
    result = run_cmd(["nm", "-C", args["path"]])
    if result["returncode"] != 0:
        # Try dynamic symbols for stripped binaries
        result = run_cmd(["nm", "-C", "-D", args["path"]])
    if result["returncode"] != 0:
        return cmd_result_to_tool_result(result)

    text = result["stdout"]
    grep = args.get("grep_pattern")
    if grep:
        text = "\n".join(line for line in text.split("\n") if grep.lower() in line.lower())

    if len(text) > 8000:
        text = text[:8000] + "\n\n[OUTPUT TRUNCATED — use grep_pattern to filter]"
    return format_tool_result(text)


TOOLS = [r2_command, r2_decompile, objdump_disasm, nm_symbols]
