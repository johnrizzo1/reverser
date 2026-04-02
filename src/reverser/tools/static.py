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


@tool(
    "jadx_decompile",
    "Decompile a Java/Android binary (JAR, APK, DEX, AAR, class files) to Java source using jadx. "
    "Returns decompiled source for a specific class or all classes. Use class_filter to narrow results.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the JAR, APK, DEX, or class file"},
            "class_filter": {
                "type": "string",
                "description": "Grep pattern to filter output to specific class/method names",
            },
            "show_resources": {
                "type": "boolean",
                "description": "Also show decoded resources (AndroidManifest.xml, etc.)",
                "default": False,
            },
            "offset": {"type": "integer", "description": "Line offset for pagination", "default": 0},
            "limit": {"type": "integer", "description": "Max lines to return", "default": 120},
        },
        "required": ["path"],
    },
)
async def jadx_decompile(args: dict) -> dict:
    import tempfile

    path = args["path"]
    show_resources = args.get("show_resources", False)

    with tempfile.TemporaryDirectory(prefix="jadx_") as tmpdir:
        cmd = ["jadx", "--no-imports", "--no-debug-info", "-d", tmpdir, path]
        if show_resources:
            cmd.insert(1, "--export-gradle")

        result = run_cmd(cmd, timeout=60, max_output=200000)
        if result["returncode"] != 0 and not result["stdout"]:
            return cmd_result_to_tool_result(result)

        # Collect all .java files from output directory
        import os
        output_lines = []
        for root, _, files in os.walk(tmpdir):
            for fname in sorted(files):
                if not fname.endswith(".java") and not (show_resources and fname.endswith(".xml")):
                    continue
                fpath = os.path.join(root, fname)
                relpath = os.path.relpath(fpath, tmpdir)
                try:
                    content = open(fpath).read()
                except Exception:
                    continue
                output_lines.append(f"// ===== {relpath} =====")
                output_lines.append(content)

        text = "\n".join(output_lines)

    class_filter = args.get("class_filter")
    if class_filter:
        # Show file headers + matching lines with context
        filtered = []
        lines = text.split("\n")
        for line in lines:
            if line.startswith("// =====") or class_filter.lower() in line.lower():
                filtered.append(line)
        text = "\n".join(filtered)

    page = paginate(text, args.get("offset", 0), args.get("limit", 120))
    return format_tool_result(f"{page['showing']}\n\n{page['content']}")


@tool(
    "procyon_decompile",
    "Decompile a Java class file or JAR to Java source using Procyon. Produces higher-fidelity output "
    "than jadx for standard Java (non-Android) bytecode. Specify a class name to decompile a single class "
    "from a JAR, or pass a .class file directly.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the JAR or .class file"},
            "class_name": {
                "type": "string",
                "description": "Fully qualified class name to decompile from a JAR (e.g. 'com.example.Main')",
            },
            "offset": {"type": "integer", "description": "Line offset for pagination", "default": 0},
            "limit": {"type": "integer", "description": "Max lines to return", "default": 120},
        },
        "required": ["path"],
    },
)
async def procyon_decompile(args: dict) -> dict:
    path = args["path"]
    class_name = args.get("class_name")

    cmd = ["procyon", "-jar", path] if path.endswith(".jar") else ["procyon", path]
    if class_name:
        cmd = ["procyon", "-jar", path, class_name]

    result = run_cmd(cmd, timeout=60, max_output=100000)
    if result["returncode"] != 0 and not result["stdout"]:
        return cmd_result_to_tool_result(result)

    page = paginate(result["stdout"], args.get("offset", 0), args.get("limit", 120))
    return format_tool_result(f"{page['showing']}\n\n{page['content']}")


TOOLS = [r2_command, r2_decompile, objdump_disasm, nm_symbols, jadx_decompile, procyon_decompile]
