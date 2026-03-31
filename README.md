# reverser

A Claude-powered reverse engineering agent. Give it a binary, it analyzes it autonomously using radare2, Ghidra, GDB, angr, and 12 other RE tools.

## Prerequisites

- [devenv](https://devenv.sh/getting-started/#installation)
- `ANTHROPIC_API_KEY` environment variable
- Wine (optional, for Windows PE binaries)

## Setup

```sh
devenv shell
pip install -e .
```

This drops you into a shell with 30+ RE tools (radare2, rizin, ghidra, gdb, strace, binwalk, yara, angr, etc.) and installs the `reverser` CLI.

## Usage

```sh
# Quick triage — file type, security, strings, headers
reverser triage <binary>

# Full analysis — triage + static + dynamic + report
reverser analyze <binary>

# Solve a crackme / CTF challenge
reverser solve <binary>
```

### Options

```
-v            Show tool calls and results
-vv           Also show agent thinking
--budget N    Max API spend in USD (default: 2.0)
--log PATH    Custom session log path
--log-dir DIR Custom log directory (default: ./logs)
```

### Writeups

Every run logs to `logs/<binary>_<timestamp>.jsonl`. Generate a markdown writeup from any log:

```sh
reverser writeup logs/boring_20260330_234443.jsonl
reverser writeup logs/boring_20260330_234443.jsonl -o writeup.md
```

## Windows Binaries

PE files (.exe, .dll) are supported via wine. The agent detects PE format automatically and uses wine for execution, strace, and GDB. Static analysis tools (radare2, strings, objdump) work natively on PE files.

## Tools

| Category | Tools |
|----------|-------|
| Triage | file, strings, checksec, readelf, pe_info, binwalk |
| Static | radare2 (r2pipe), objdump, nm |
| Dynamic | run_binary, strace, gdb (batch) |
| Advanced | angr (symbolic execution), capstone |
| Exploit | ROPgadget |

## Project Structure

```
src/reverser/
  cli.py            CLI entry point
  agent.py          Agent orchestration (Claude Agent SDK)
  prompts.py        System prompt and analysis methodology
  session_log.py    JSONL session logger
  writeup.py        Markdown writeup generator
  tools/
    _common.py      Subprocess helpers, PE detection, pagination
    triage.py       File identification and initial assessment
    static.py       Disassembly and decompilation
    dynamic.py      Runtime tracing and debugging
    python_analysis.py  Symbolic execution and emulation
    exploit.py      ROP gadget search
```
