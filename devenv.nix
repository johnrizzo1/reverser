{ pkgs, lib, config, inputs, ... }:

{
  env.REVERSER_HOME = "${config.devenv.root}";
  env.SECLISTS_PATH = "${pkgs.seclists}/share/wordlists/seclists";

  dotenv.enable = true;

  # ── Core RE packages ──────────────────────────────────────────────
  packages = with pkgs; [
    # Version control
    git

    # Harness dependencies
    awscli2
    sqlite
    jq
    unzip

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
    # jadx                    # quark-engine dep conflicts with devenv Python env; installed standalone via ~/.local/bin
    cfr
    procyon

    # Dynamic analysis / Debuggers
    qemu

    # Network analysis
    wireshark-cli          # tshark, editcap, mergecap
    tcpdump
    mitmproxy
    ngrep

    # Penetration testing / Network recon
    nmap
    nikto
    gobuster
    sslscan
    whatweb
    nbtscan
    ffuf                  # web fuzzer (paths, vhosts, params)
    subfinder             # passive subdomain enumeration
    nuclei                # template-based vuln scanner
    testssl               # TLS/SSL scanner (testssl.sh)
    # sqlmap is a Python package (not top-level in nixpkgs); installed via
    # the Python venv requirements below.
    # enum4linux-ng — NOT included here because it transitively depends on
    # `samba` (for smbclient/rpcclient/nmblookup), and samba-4.22.7 fails
    # to build with current Clang on Darwin (strict-initializer-not-constant
    # error in Samba's own test code). To install on macOS:
    #   brew install samba
    #   pipx install git+https://github.com/cddmp/enum4linux-ng.git
    # On Linux, samba builds OK — re-enable the `enum4linux-ng` package
    # here (it's wired into reverser as the `enum4linux_ng` MCP tool).
    # netexec (nxc) is installed from PyPI in the Python venv below — the
    # nixpkgs build is currently marked broken on this channel. Upstream's
    # recommended install method is `pip install netexec` anyway.
    # Exploit-db + Metasploit bridge (Top 5 #1)
    metasploit-framework   # msfconsole, msfrpcd, msfvenom
    exploitdb              # searchsploit CLI + the exploit database
    neo4j                  # graph database for BloodHound (per-target instances)
    krb5                   # kinit, klist, krb5-config
    kerbrute               # Kerberos brute-force (user/password enumeration)
    openldap               # ldapsearch, ldapwhoami — AD/LDAP enumeration
    dnsrecon               # DNS enumeration and zone-transfer checks
    dnsutils               # dig, nslookup, host
    seclists               # wordlists for gobuster, kerberos, etc.

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
    tinyxxd                # xxd hex dump (lightweight, no vim dependency)

    # Fuzzing
    radamsa

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
    dex2jar

    # SMT solver
    z3

    # Malware analysis
    # yargen
    inetutils
    sshpass

    semgrep
  ]

  # ── Linux-only packages ────────────────────────────────────────────
  ++ lib.optionals stdenv.isLinux [
    gdb
    samba
    strace
    ltrace
    elfutils
    valgrind
    aflplusplus
    honggfuzz
    zzuf
  ]

  # ── macOS-specific packages ────────────────────────────────────────
  ++ lib.optionals stdenv.isDarwin [
    lldb                       # debugger (gdb equivalent)
  ];

  # ── Python with RE libraries ──────────────────────────────────────
  languages.python = {
    enable = true;
    package = pkgs.python3;
    venv = {
      enable = true;
      requirements = ''
        claude-agent-sdk
        boto3
        click
        textual
        openai
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
        ldap3
        impacket
        pycryptodome     # provides Crypto module — required by ldap3 NTLM auth
        requests         # HTTP client used by various web tools
        wafw00f          # WAF detection CLI (also exposes a Python module)
        sqlmap           # SQL injection scanner (CLI; not top-level in nixpkgs)
        pymetasploit3    # JSON-RPC client to msfrpcd (used by metasploit_* tools)
	invoke
	pynacl
	paramiko
        neo4j
        bloodhound
        # NetExec is installed explicitly in enterShell (not here) — when
        # the venv-requirements mechanism processes a `pkg @ git+url` entry,
        # it imports the module but does not always wire up console_scripts
        # entry-points (so the `nxc` binary ends up missing from venv/bin).
        # Installing via `pip install` in enterShell is more reliable.
      '';
    };
  };

  # ── Harness scripts ────────────────────────────────────────────────
  scripts.harness-init.exec = ''
    python -m reverser.harness init "$@"
  '';

  scripts.harness-run.exec = ''
    python -m reverser.harness monitor "$@"
  '';

  scripts.harness-build-image.exec = ''
    bash "$REVERSER_HOME/incus/build-image.sh" "$@"
  '';

  scripts.harness-test.exec = ''
    python -m reverser.harness test-vm "$@"
  '';

  scripts.harness-status.exec = ''
    python -m reverser.harness status "$@"
  '';

  scripts.harness-cleanup.exec = ''
    python -m reverser.harness cleanup "$@"
  '';

  scripts.harness-reset.exec = ''
    python -m reverser.harness reset "$@"
  '';

  scripts.harness-process.exec = ''
    python -m reverser.harness process "$@"
  '';

  scripts.re-info.exec = ''
    echo "🔍 Reverser Agent Development Environment"
    echo ""
    echo "Core tools:"
    echo "  radare2  $(r2 -V 2>/dev/null | head -1)"
    echo "  rizin    $(rizin -V 2>/dev/null | head -1)"
    echo "  ghidra   $(ghidra-analyzeHeadless --help 2>&1 | head -1 || echo 'installed')"
    echo "  debugger $(gdb --version 2>/dev/null | head -1 || lldb --version 2>/dev/null | head -1)"
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
    pip install -q --no-deps -e "$REVERSER_HOME" 2>/dev/null || true

    # NetExec (nxc): install from upstream git into the venv so the `nxc`
    # binary lands in venv/bin and is reachable on PATH. Idempotent — only
    # runs when nxc is missing, which means it does no work on subsequent
    # shell entries. NetExec isn't on PyPI and the nixpkgs package is marked
    # broken on this channel, so explicit pip-from-git is the fallback.
    if ! command -v nxc >/dev/null 2>&1; then
      echo "Installing NetExec (nxc) into venv from upstream git..."
      if pip install -q --upgrade "git+https://github.com/Pennyw0rth/NetExec.git@v1.5.1"; then
        echo "  ✓ NetExec installed: $(nxc --version 2>&1 | head -1)"
      else
        echo "  ✗ NetExec install failed — install manually with:"
        echo "    pip install git+https://github.com/Pennyw0rth/NetExec.git"
      fi
    fi

    hash -r
    echo "Reverser agent environment loaded."
    echo ""
    echo "Agent commands:"
    echo "  reverser triage <binary>                Quick file/security assessment"
    echo "  reverser analyze <binary>               Full RE analysis"
    echo "  reverser solve <binary>                 Solve a crackme/CTF challenge"
    echo "  reverser interactive <binary|url>       Launch interactive TUI (alias: i)"
    echo "  reverser writeup <log.jsonl>            Generate markdown from session log"
    echo ""
    echo "  Options: -v (verbose) -vv (thinking) --budget N --profile P"
    echo "  Backend: -b ollama -m <model>  (or claude by default)"
    echo "  RE Profiles: general linux windows android chrome managed api pentest ad ctf"
    echo "  Web Profiles: webpentest webapi webrecon"
    echo ""
    echo "  Web pentest: REVERSER_PENTEST_AUTHORIZED=1 reverser i -p webpentest https://target.com"
    echo ""
    echo "Shell helpers:"
    echo "  re-info                                 Tool summary"
    echo "  re-triage <binary>                      Quick binary triage (shell)"
    echo ""
    echo "Harness commands:"
    echo "  harness-init          Initialize Incus profile, firewall, and state DB"
    echo "  harness-run           Start S3 monitor loop"
    echo "  harness-build-image   Build the reverser container base image"
    echo "  harness-test          Launch a test container and verify isolation"
    echo "  harness-status        Show processing stats"
    echo "  harness-process FILE  Analyze a local binary in an isolated container"
    echo "  harness-reset         Clear state DB (--failed-only to reset failures)"
    echo "  harness-cleanup       Destroy orphaned containers"
  '';

  enterTest = ''
    echo "Testing reverser environment..."
    r2 -V > /dev/null 2>&1 && echo "✓ radare2" || echo "✗ radare2"
    rizin -V > /dev/null 2>&1 && echo "✓ rizin" || echo "✗ rizin"
    rizin -L 2>/dev/null | grep -q ghidra && echo "✓ rz-ghidra" || echo "✗ rz-ghidra"
    ghidra-analyzeHeadless > /dev/null 2>&1; [ $? -ne 127 ] && echo "✓ ghidra" || echo "✗ ghidra"
    gdb --version > /dev/null 2>&1 && echo "✓ gdb" || lldb --version > /dev/null 2>&1 && echo "✓ lldb (gdb n/a)" || echo "✗ gdb/lldb"
    tshark --version > /dev/null 2>&1 && echo "✓ tshark" || echo "✗ tshark"
    python3 -c "import angr" > /dev/null 2>&1 && echo "✓ angr" || echo "✗ angr"
    python3 -c "import capstone" > /dev/null 2>&1 && echo "✓ capstone" || echo "✗ capstone"
    python3 -c "import unicorn" > /dev/null 2>&1 && echo "✓ unicorn" || echo "✗ unicorn"
    python3 -c "import pwn" > /dev/null 2>&1 && echo "✓ pwntools" || echo "✗ pwntools"
    python3 -c "import pyhidra" > /dev/null 2>&1 && echo "✓ pyhidra" || echo "✗ pyhidra"
    echo "Testing web pentest tools..."
    nmap --version > /dev/null 2>&1 && echo "✓ nmap" || echo "✗ nmap"
    nikto -Version > /dev/null 2>&1 && echo "✓ nikto" || echo "✗ nikto"
    nuclei -version > /dev/null 2>&1 && echo "✓ nuclei" || echo "✗ nuclei"
    subfinder -version > /dev/null 2>&1 && echo "✓ subfinder" || echo "✗ subfinder"
    ffuf -V > /dev/null 2>&1 && echo "✓ ffuf" || echo "✗ ffuf"
    sqlmap --version > /dev/null 2>&1 && echo "✓ sqlmap" || echo "✗ sqlmap"
    testssl.sh --help > /dev/null 2>&1 && echo "✓ testssl" || echo "✗ testssl"
    python3 -c "import wafw00f" > /dev/null 2>&1 && echo "✓ wafw00f" || echo "✗ wafw00f"
    echo "Testing harness dependencies..."
    python3 -c "import boto3" > /dev/null 2>&1 && echo "✓ boto3" || echo "✗ boto3"
    python3 -c "import click" > /dev/null 2>&1 && echo "✓ click" || echo "✗ click"
    incus version > /dev/null 2>&1 && echo "✓ incus" || echo "✗ incus (check socket permissions)"
    aws --version > /dev/null 2>&1 && echo "✓ awscli" || echo "✗ awscli"
    echo "Testing BloodHound stack..."
    neo4j --version > /dev/null 2>&1 && echo "✓ neo4j" || echo "✗ neo4j"
    python3 -c "import neo4j" > /dev/null 2>&1 && echo "✓ neo4j (py)" || echo "✗ neo4j (py)"
    python3 -c "import bloodhound" > /dev/null 2>&1 && echo "✓ bloodhound (py)" || echo "✗ bloodhound (py)"
    echo "Done."
  '';
}
