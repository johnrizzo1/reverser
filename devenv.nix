{ pkgs, lib, config, inputs, ... }:

{
  env.REVERSER_HOME = "${config.devenv.root}";

  # ── Core RE packages ──────────────────────────────────────────────
  packages = with pkgs; [
    # Version control
    git

    # Disassemblers / Decompilers
    radare2
    (rizin.withPlugins (ps: [ ps.rz-ghidra ]))
    (ghidra.withExtensions (exts: [
      exts.findcrypt
      exts.ghidra-golanganalyzerextension
      # exts.gnudisassembler  # build fails (binutils compile in sandbox); system binutils covers this
      exts.machinelearning
      exts.wasm
    ]))
    # retdec                  # build failure in capstone dep
    binutils
    elfutils
    # jadx                    # dep build issue (quark-engine/plotly)
    cfr

    # Dynamic analysis / Debuggers
    gdb
    strace
    ltrace
    valgrind
    qemu

    # Network analysis
    wireshark-cli          # tshark, editcap, mergecap
    tcpdump
    mitmproxy
    ngrep

    # Binary analysis / File identification
    binwalk
    patchelf
    checksec
    upx
    # detect-it-easy           # pillow-heif build issue
    ssdeep
    yara

    # Hex viewing
    hexxy

    # Fuzzing
    aflplusplus
    honggfuzz
    radamsa
    zzuf

    # Crypto / Password cracking
    # hashcat                 # needs opencv/opencl - enable if GPU available
    john

    # Forensics
    # volatility3             # build issue - enable when network allows
    sleuthkit

    # Assembler
    nasm

    # Exploit development
    ropgadget
    ropr
    one_gadget
    # pwntools                # also available as CLI; Python version below

    # Android RE
    apktool
    # jadx                    # listed above in disassemblers
    dex2jar

    # SMT solver
    z3

    # Malware analysis
    # yargen
  ];

  # ── Python with RE libraries ──────────────────────────────────────
  languages.python = {
    enable = true;
    package = pkgs.python3;
    venv = {
      enable = true;
      requirements = ''
        claude-agent-sdk
        angr
        capstone
        unicorn
        pwntools
        r2pipe
        rzpipe
        pyelftools
        yara-python
        lief
        pyshark
        ropper
        keystone-engine
        pefile
        malduck
        flare-floss
        pyhidra
      '';
    };
  };

  # ── Convenience scripts ───────────────────────────────────────────
  scripts.re-info.exec = ''
    echo "🔍 Reverser Agent Development Environment"
    echo ""
    echo "Core tools:"
    echo "  radare2  $(r2 -V 2>/dev/null | head -1)"
    echo "  rizin    $(rizin -V 2>/dev/null | head -1)"
    echo "  ghidra   $(ghidra-analyzeHeadless --help 2>&1 | head -1 || echo 'installed')"
    echo "  gdb      $(gdb --version 2>/dev/null | head -1)"
    echo "  tshark   $(tshark --version 2>/dev/null | head -1)"
    echo ""
    echo "Python RE libraries: angr, capstone, unicorn, pwntools, r2pipe,"
    echo "  rzpipe, pyelftools, yara-python, lief, pyshark, ropper,"
    echo "  keystone-engine, pefile, malduck, flare-floss, pyhidra"
    echo ""
    echo "Run 'devenv info' for full environment details."
  '';

  scripts.re-triage.exec = ''
    # Quick binary triage: usage: re-triage <binary>
    if [ -z "$1" ]; then
      echo "Usage: re-triage <binary>"
      exit 1
    fi
    echo "=== File type ==="
    file "$1"
    echo ""
    echo "=== Checksec ==="
    checksec --file="$1" 2>/dev/null || echo "(checksec not available)"
    echo ""
    echo "=== Interesting strings (top 20) ==="
    strings -n 8 "$1" | head -20
    echo ""
    echo "=== ELF info ==="
    readelf -h "$1" 2>/dev/null || echo "(not an ELF)"
    echo ""
    echo "=== Binwalk signatures ==="
    binwalk -q "$1" 2>/dev/null | head -20
  '';

  enterShell = ''
    echo "Reverser agent environment loaded."
    echo "Run 're-info' for tool summary or 're-triage <binary>' for quick analysis."
  '';

  enterTest = ''
    echo "Testing reverser environment..."
    r2 -V > /dev/null 2>&1 && echo "✓ radare2" || echo "✗ radare2"
    rizin -V > /dev/null 2>&1 && echo "✓ rizin" || echo "✗ rizin"
    rizin -L 2>/dev/null | grep -q ghidra && echo "✓ rz-ghidra" || echo "✗ rz-ghidra"
    ghidra-analyzeHeadless > /dev/null 2>&1; [ $? -ne 127 ] && echo "✓ ghidra" || echo "✗ ghidra"
    gdb --version > /dev/null 2>&1 && echo "✓ gdb" || echo "✗ gdb"
    tshark --version > /dev/null 2>&1 && echo "✓ tshark" || echo "✗ tshark"
    python3 -c "import angr" > /dev/null 2>&1 && echo "✓ angr" || echo "✗ angr"
    python3 -c "import capstone" > /dev/null 2>&1 && echo "✓ capstone" || echo "✗ capstone"
    python3 -c "import unicorn" > /dev/null 2>&1 && echo "✓ unicorn" || echo "✗ unicorn"
    python3 -c "import pwn" > /dev/null 2>&1 && echo "✓ pwntools" || echo "✗ pwntools"
    python3 -c "import pyhidra" > /dev/null 2>&1 && echo "✓ pyhidra" || echo "✗ pyhidra"
    echo "Done."
  '';
}
