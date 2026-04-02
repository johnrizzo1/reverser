# reverser

An AI-powered reverse engineering agent. Give it a binary, it analyzes it autonomously using radare2, Ghidra, GDB, angr, and 25+ other RE tools. Supports Claude and local models (Ollama, vLLM, llama.cpp).

## Prerequisites

- [devenv](https://devenv.sh/getting-started/#installation)
- One of:
  - `ANTHROPIC_API_KEY` environment variable (for Claude backend)
  - [Ollama](https://ollama.com/) running locally (for local model backend)
- Wine (optional, for Windows PE binaries)

## Setup

```sh
devenv shell
```

This drops you into a shell with 30+ RE tools (radare2, rizin, ghidra, gdb, strace, binwalk, yara, angr, etc.) and installs the `reverser` CLI.

## Usage

### Autonomous analysis

```sh
# Quick triage — file type, security, strings, headers
reverser triage <binary>

# Full analysis — triage + static + dynamic + report
reverser analyze <binary>

# Solve a crackme / CTF challenge
reverser solve <binary>
```

### Interactive TUI

Launch a conversational interface where you guide the agent, use skills, and switch profiles:

```sh
# Interactive mode with default (general) profile
reverser interactive <binary>
reverser i <binary>              # short alias

# Pick a profile for your target type
reverser i <binary> --profile ctf
reverser i <binary> -p android
reverser i <binary> -p api       # API discovery / documentation
```

Inside the TUI:
- Type messages to chat with the agent
- Press **F1** to run a skill (Triage, Analyze, Solve, Decompile, API Map, etc.)
- Press **F2** to switch profiles mid-session
- Press **F3** to load a different binary
- Type `/help` for all commands

### Options (all analysis commands)

```
-v              Show tool calls and results
-vv             Also show agent thinking
--budget N      Max API spend in USD (default: 2.0 for autonomous, 5.0 for interactive)
--log PATH      Custom session log path
--log-dir DIR   Custom log directory (default: ./logs)
```

### Backend selection

By default, reverser uses Claude via the Anthropic API. You can use a local model instead:

```sh
# Ollama (auto-detects http://localhost:11434/v1)
reverser analyze <binary> -b ollama -m qwen3.5:35b-a3b-coding-nvfp4

# Interactive TUI with a local model
reverser i <binary> -b ollama -m qwen3.5:35b-a3b-coding-nvfp4 -p ctf

# Any OpenAI-compatible endpoint (vLLM, llama.cpp, etc.)
reverser solve <binary> -b local -m qwen3.5 --api-base http://gpu-server:8000/v1
```

Backend flags:

```
-b, --backend   Backend: claude (default), ollama, or any name for OpenAI-compatible
-m, --model     Model name/tag (required for non-claude backends)
--api-base      API base URL override
```

### Profiles

Profiles specialize the agent's system prompt, tool priorities, and available skills for different target types:

| Profile | Key | Description |
|---------|-----|-------------|
| General | `general` | Broad RE — works for any binary (default) |
| Linux Binary | `linux` | ELF-focused — syscalls, shared libs, debugging |
| Windows Binary | `windows` | PE-focused — DLL imports, .NET/COM, Windows API |
| Android APK | `android` | APK/XAPK — DEX, native libs, manifest, API endpoints |
| Chrome Extension | `chrome` | CRX/JS — manifest, permissions, data flow, security |
| Java / .NET | `managed` | JVM bytecode and .NET IL — decompilation, serialization |
| API Discovery | `api` | Document network APIs, endpoints, auth, request/response schemas |
| CTF / Crackme | `ctf` | Solve crackmes — find the flag/key/password |

```sh
reverser interactive --list-profiles   # show all profiles and their skills
```

### Writeups

Every run logs to `logs/<binary>_<timestamp>.jsonl`. Generate a markdown writeup from any log:

```sh
reverser writeup logs/boring_20260330_234443.jsonl
reverser writeup logs/boring_20260330_234443.jsonl -o writeup.md
```

## Windows Binaries

PE files (.exe, .dll) are supported via wine. The agent detects PE format automatically and uses wine for execution, strace, and GDB. Static analysis tools (radare2, strings, objdump) work natively on PE files.

## Harness (Isolated Container Analysis)

The harness monitors an S3 bucket for binaries, runs analysis in isolated Incus containers with network restrictions (only Anthropic API access), and uploads results back to S3.

```sh
harness-init            # Set up Incus profile, firewall, and state DB
harness-build-image     # Build the reverser container base image
harness-test            # Verify container isolation
harness-run             # Start S3 monitoring loop
harness-process <file>  # Analyze a local binary in a container
harness-status          # Show processing statistics
harness-reset           # Clear state DB (--failed-only to reset failures)
harness-cleanup         # Destroy orphaned containers
```

Configuration lives in `harness.toml`. Requires AWS credentials and `ANTHROPIC_API_KEY` in `.env` or environment.

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
  cli.py              CLI entry point (triage, analyze, solve, interactive, writeup)
  agent.py            Agent orchestration (backend-agnostic)
  prompts.py          System prompt and analysis methodology
  profiles.py         Agent profiles for different target types
  session_log.py      JSONL session logger
  writeup.py          Markdown writeup generator
  backends/
    base.py           Backend protocol (AgentEvent, Backend ABC)
    claude.py          Claude backend (via claude_agent_sdk)
    openai_compat.py   OpenAI-compatible backend (Ollama, vLLM, llama.cpp)
    tools.py           MCP-to-OpenAI tool conversion
  tools/
    _common.py         Subprocess helpers, PE detection, pagination
    triage.py          File identification and initial assessment
    static.py          Disassembly and decompilation
    dynamic.py         Runtime tracing and debugging
    python_analysis.py Symbolic execution and emulation
    exploit.py         ROP gadget search
  tui/
    app.py             Interactive TUI application (Textual)
    session.py         Multi-turn agent session manager
  harness/
    cli.py             Harness CLI (init, monitor, process, status, etc.)
    config.py          TOML + env configuration loader
    monitor.py         S3 bucket polling and download
    pipeline.py        Analysis pipeline orchestration
    state.py           SQLite state tracker
    vm.py              Incus container lifecycle management
incus/
  build-image.sh       Container image builder
  setup-firewall.sh    nftables network isolation rules
  profile.yaml         Incus resource limits profile
  re-tools.nix         Ghidra/Rizin Nix composition
```
