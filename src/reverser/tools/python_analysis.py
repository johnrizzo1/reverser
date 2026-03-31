"""Advanced analysis tools using Python RE libraries: angr, capstone, unicorn."""

from claude_agent_sdk import tool

from ._common import format_tool_result, format_error


@tool(
    "angr_find_paths",
    "Use angr symbolic execution to find input that reaches a target address while avoiding others. "
    "First use static analysis to identify the target (success) and avoid (failure) addresses. "
    "This tool is powerful for solving crackmes and CTF challenges.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the binary"},
            "find_addr": {"type": "string", "description": "Target address to reach (hex, e.g. '0x401234')"},
            "avoid_addrs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Addresses to avoid (hex strings)",
                "default": [],
            },
            "stdin_length": {
                "type": "integer",
                "description": "Expected stdin input length in bytes (helps constrain symbolic execution)",
            },
            "timeout": {"type": "integer", "description": "Max seconds for exploration", "default": 120},
        },
        "required": ["path", "find_addr"],
    },
)
async def angr_find_paths(args: dict) -> dict:
    try:
        import angr
        import claripy
    except ImportError:
        return format_error("angr not installed. Run inside devenv shell.")

    try:
        proj = angr.Project(args["path"], auto_load_libs=False)

        stdin_len = args.get("stdin_length")
        if stdin_len:
            sym_input = claripy.BVS("stdin", stdin_len * 8)
            state = proj.factory.entry_state(stdin=angr.SimFileStream(content=sym_input))
            # Constrain to printable ASCII
            for i in range(stdin_len):
                byte = sym_input.get_byte(i)
                state.solver.add(byte >= 0x20)
                state.solver.add(byte <= 0x7e)
        else:
            state = proj.factory.entry_state()

        simgr = proj.factory.simgr(state)

        find = int(args["find_addr"], 16)
        avoid = [int(a, 16) for a in args.get("avoid_addrs", [])]

        simgr.explore(find=find, avoid=avoid, timeout=args.get("timeout", 120))

        if simgr.found:
            found_state = simgr.found[0]
            stdin_result = found_state.posix.dumps(0)
            stdout_result = found_state.posix.dumps(1)

            output = f"Solution found!\n\nStdin (bytes): {stdin_result!r}\n"
            try:
                output += f"Stdin (text): {stdin_result.decode('ascii', errors='replace')}\n"
            except Exception:
                pass
            if stdout_result:
                output += f"\nStdout: {stdout_result.decode('ascii', errors='replace')}\n"
            output += f"\nExploration stats: {len(simgr.found)} found, {len(simgr.avoid)} avoided, {len(simgr.active)} active"
            return format_tool_result(output)

        return format_tool_result(
            f"No path found to {args['find_addr']}.\n"
            f"Stats: {len(simgr.avoid)} avoided, {len(simgr.active)} still active, {len(simgr.deadended)} deadended.\n"
            "Try adjusting find/avoid addresses or increasing timeout."
        )
    except Exception as e:
        return format_error(f"angr error: {e}")


@tool(
    "disassemble_bytes",
    "Disassemble raw bytes using Capstone. Provide hex-encoded bytes and architecture.",
    {
        "type": "object",
        "properties": {
            "hex_bytes": {"type": "string", "description": "Hex-encoded bytes to disassemble (e.g. '4889e5 4883ec10')"},
            "arch": {
                "type": "string",
                "description": "Architecture: x86, x64, arm, arm64, mips",
                "default": "x64",
            },
            "base_addr": {"type": "string", "description": "Base address for disassembly (hex)", "default": "0x0"},
        },
        "required": ["hex_bytes"],
    },
)
async def disassemble_bytes(args: dict) -> dict:
    try:
        import capstone
    except ImportError:
        return format_error("capstone not installed. Run inside devenv shell.")

    arch_map = {
        "x86": (capstone.CS_ARCH_X86, capstone.CS_MODE_32),
        "x64": (capstone.CS_ARCH_X86, capstone.CS_MODE_64),
        "arm": (capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM),
        "arm64": (capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM),
        "mips": (capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS32),
    }

    arch_name = args.get("arch", "x64").lower()
    if arch_name not in arch_map:
        return format_error(f"Unknown arch '{arch_name}'. Supported: {', '.join(arch_map)}")

    cs_arch, cs_mode = arch_map[arch_name]
    md = capstone.Cs(cs_arch, cs_mode)

    hex_str = args["hex_bytes"].replace(" ", "").replace("\n", "")
    try:
        code = bytes.fromhex(hex_str)
    except ValueError as e:
        return format_error(f"Invalid hex: {e}")

    base = int(args.get("base_addr", "0x0"), 16)

    lines = []
    for addr, size, mnemonic, op_str in md.disasm_lite(code, base):
        lines.append(f"  0x{addr:08x}:  {mnemonic:8s} {op_str}")

    if not lines:
        return format_tool_result("No valid instructions found in the provided bytes.")
    return format_tool_result("\n".join(lines))


TOOLS = [angr_find_paths, disassemble_bytes]
