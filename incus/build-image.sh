#!/usr/bin/env bash
# Build the reverser base container image with all RE tools pre-installed.
# This runs WITH internet access; the resulting image is used in isolated containers.
set -euo pipefail

IMAGE_ALIAS="${1:-reverser-base}"
BUILD_CONTAINER="reverser-build-$(date +%s)"
REVERSER_SRC="${REVERSER_SRC:-${REVERSER_HOME:-$HOME/Projects/reverser}}"

if [ ! -d "$REVERSER_SRC" ]; then
    echo "ERROR: Reverser source not found at $REVERSER_SRC" >&2
    echo "Set REVERSER_SRC or REVERSER_HOME to the reverser project directory." >&2
    exit 1
fi

echo "=== Building reverser base image ==="
echo "Source:    $REVERSER_SRC"
echo "Image:     $IMAGE_ALIAS"
echo "Container: $BUILD_CONTAINER"
echo ""

# Check for existing image
if incus image list --format=json | jq -e ".[] | select(.aliases[]?.name == \"$IMAGE_ALIAS\")" >/dev/null 2>&1; then
    echo "WARNING: Image '$IMAGE_ALIAS' already exists."
    read -rp "Overwrite? [y/N] " answer
    if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
        echo "Aborted."
        exit 0
    fi
    incus image delete "$IMAGE_ALIAS"
fi

cleanup() {
    echo "Cleaning up build container..."
    incus delete "$BUILD_CONTAINER" --force 2>/dev/null || true
    # Restore firewall rules if they were suspended
    if [ -n "${FIREWALL_SUSPENDED:-}" ]; then
        echo "Restoring firewall rules..."
        sudo bash "$SCRIPT_DIR/setup-firewall.sh" 2>/dev/null || true
    fi
}
trap cleanup EXIT

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Suspend firewall rules so the build container has internet access
if sudo nft list table inet reverser_harness >/dev/null 2>&1; then
    echo "Suspending firewall rules for build..."
    sudo nft delete table inet reverser_harness
    FIREWALL_SUSPENDED=1
fi

# 1. Launch a NixOS container with default (internet-enabled) profile
echo "Launching NixOS build container..."
incus launch images:nixos/unstable "$BUILD_CONTAINER" -c security.nesting=true

echo "Waiting for container to boot..."
for i in $(seq 1 60); do
    if incus exec "$BUILD_CONTAINER" -- true 2>/dev/null; then
        break
    fi
    sleep 2
done

# Wait for networking to come up
sleep 5

# 2. Update nix channel and install RE system packages
echo "Updating nix channels..."
incus exec "$BUILD_CONTAINER" -- nix-channel --update

echo "Installing system RE tools..."
incus exec "$BUILD_CONTAINER" -- bash -c '
    nix-env -iA \
        nixos.radare2 \
        nixos.elfutils \
        nixos.cfr \
        nixos.gdb \
        nixos.strace \
        nixos.ltrace \
        nixos.valgrind \
        nixos.qemu \
        nixos.wireshark-cli \
        nixos.tcpdump \
        nixos.mitmproxy \
        nixos.ngrep \
        nixos.binwalk \
        nixos.patchelf \
        nixos.checksec \
        nixos.upx \
        nixos.ssdeep \
        nixos.yara \
        nixos.hexxy \
        nixos.aflplusplus \
        nixos.honggfuzz \
        nixos.radamsa \
        nixos.zzuf \
        nixos.john \
        nixos.sleuthkit \
        nixos.nasm \
        nixos.ropgadget \
        nixos.ropr \
        nixos.one_gadget \
        nixos.apktool \
        nixos.dex2jar \
        nixos.z3 \
        nixos.python3 \
        nixos.python3Packages.pip \
        nixos.python3Packages.setuptools \
        nixos.gcc \
        nixos.gnumake \
        nixos.file \
        nixos.nodejs
'

# 2b. Install Wine separately (may fail due to GCC ICE in some nixpkgs versions)
echo "Installing Wine (optional)..."
incus exec "$BUILD_CONTAINER" -- bash -c '
    nix-env -iA nixos.wineWowPackages.stable 2>&1
' || echo "WARNING: Wine installation failed — PE binary support will be unavailable."

# 3. Install Ghidra (with extensions) and Rizin (with rz-ghidra plugin)
echo "Installing Ghidra + extensions and Rizin + rz-ghidra..."
incus file push "$SCRIPT_DIR/re-tools.nix" "$BUILD_CONTAINER/tmp/re-tools.nix"
incus exec "$BUILD_CONTAINER" -- bash -c '
    export PATH="/root/.nix-profile/bin:$PATH"
    export NIX_PATH="nixos=/root/.nix-defexpr/channels/nixos:$NIX_PATH"
    nix-env -if /tmp/re-tools.nix
'

# 4. Install Claude Code CLI (required by claude_agent_sdk)
echo "Installing Claude Code CLI..."
incus exec "$BUILD_CONTAINER" -- bash -c '
    export PATH="/root/.nix-profile/bin:$PATH"
    export npm_config_prefix="/root/.npm-global"
    npm install -g @anthropic-ai/claude-code
'

# 4. Push reverser source into the container (excluding .devenv, logs, etc.)
echo "Pushing reverser source..."
STAGING_DIR=$(mktemp -d)
rsync -a --exclude='.devenv' --exclude='logs' --exclude='.direnv' --exclude='__pycache__' \
    "$REVERSER_SRC/" "$STAGING_DIR/reverser/"
incus file push -r "$STAGING_DIR/reverser/" "$BUILD_CONTAINER/opt/"
rm -rf "$STAGING_DIR"

# 6. Create virtualenv and install Python RE packages
echo "Installing Python RE packages..."
incus exec "$BUILD_CONTAINER" -- bash -c '
    export PATH="/root/.nix-profile/bin:$PATH"

    python3 -m venv /opt/venv
    export PATH="/opt/venv/bin:$PATH"

    pip install \
        claude-agent-sdk \
        angr \
        capstone \
        unicorn \
        pwntools \
        r2pipe \
        rzpipe \
        pyelftools \
        yara-python \
        lief \
        pyshark \
        ropper \
        keystone-engine \
        pefile \
        malduck \
        flare-floss \
        pyhidra
'

# 5. Fix bundled claude binary for NixOS (patch ELF interpreter)
echo "Patching bundled claude binary..."
incus exec "$BUILD_CONTAINER" -- bash -c '
    export PATH="/root/.nix-profile/bin:$PATH"
    BUNDLED="/opt/venv/lib/python3.13/site-packages/claude_agent_sdk/_bundled/claude"
    if [ -f "$BUNDLED" ]; then
        INTERP=$(find /nix/store -maxdepth 3 -name "ld-linux-x86-64.so.2" 2>/dev/null | head -1)
        if [ -n "$INTERP" ]; then
            patchelf --set-interpreter "$INTERP" "$BUNDLED"
            echo "  Patched interpreter to: $INTERP"
        fi
    fi
'

# 6. Install the reverser agent
echo "Installing reverser agent..."
incus exec "$BUILD_CONTAINER" -- bash -c '
    export PATH="/opt/venv/bin:/root/.nix-profile/bin:$PATH"
    cd /opt/reverser && pip install -e .
'

# 7. Make venv and tools accessible to all users
# Fix venv python symlink: it points through /root/.nix-profile which non-root can't traverse
incus exec "$BUILD_CONTAINER" -- bash -c '
    REAL_PYTHON=$(readlink -f /opt/venv/bin/python3)
    ln -sf "$REAL_PYTHON" /opt/venv/bin/python3
    chmod -R a+rX /opt/venv /opt/reverser
    # Make /root traversable so non-root user can follow .nix-profile symlinks
    chmod a+rx /root
    chmod -R a+rX /root/.npm-global 2>/dev/null || true
'

# 8. Create non-root 'reverser' user via NixOS config (claude_agent_sdk refuses root)
echo "Creating reverser user..."
incus exec "$BUILD_CONTAINER" -- bash -c '
    sed -i "/^}$/i\\
  users.users.reverser = { isNormalUser = true; uid = 1000; home = \"/home/reverser\"; };" \
        /etc/nixos/configuration.nix
    export NIX_PATH="nixpkgs=/root/.nix-defexpr/channels/nixos:nixos-config=/etc/nixos/configuration.nix:$NIX_PATH"
    nixos-rebuild switch 2>&1 | tail -5
'

# 8. Set up PATH in profile so the venv and nix-profile are always available
incus exec "$BUILD_CONTAINER" -- bash -c '
    # Build LD_LIBRARY_PATH from nix store lib dirs (z3, gcc, etc.)
    NIX_LIB_DIRS=$(find /nix/store -maxdepth 3 \( -name "libz3.so" -o -name "libstdc++.so.6" \) 2>/dev/null \
        | xargs -I{} dirname {} | sort -u | paste -sd:)
    cat >> /root/.bash_profile <<BASHRC
export PATH="/opt/venv/bin:/root/.nix-profile/bin:/root/.npm-global/bin:\$PATH"
export npm_config_prefix="/root/.npm-global"
export LD_LIBRARY_PATH="${NIX_LIB_DIRS}\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
BASHRC
'

# 7. Verify installation
echo ""
echo "=== Verification ==="
incus exec "$BUILD_CONTAINER" -- bash -c '
    source /root/.bash_profile
    check_cmd() { command -v "$1" >/dev/null 2>&1 && echo "OK: $1" || echo "FAIL: $1"; }
    check_py()  { python3 -c "import $1" 2>/dev/null && echo "OK: $1 (python)" || echo "FAIL: $1 (python)"; }

    # Core tools
    check_cmd reverser
    check_cmd claude
    check_cmd r2
    check_cmd rizin
    check_cmd ghidra-analyzeHeadless
    check_cmd gdb
    check_cmd objdump
    check_cmd strings

    # Ghidra decompiler via rizin
    rizin -qc 'Lc' -- 2>/dev/null | grep -q ghidra && echo "OK: rz-ghidra plugin" || echo "FAIL: rz-ghidra plugin"

    # Dynamic analysis
    check_cmd strace
    check_cmd ltrace
    check_cmd valgrind
    check_cmd qemu-x86_64

    # Network
    check_cmd tshark
    check_cmd tcpdump
    check_cmd mitmproxy

    # Binary analysis
    check_cmd binwalk
    check_cmd checksec
    check_cmd upx
    check_cmd yara
    check_cmd ssdeep

    # Fuzzing
    check_cmd afl-fuzz
    check_cmd honggfuzz
    check_cmd radamsa

    # Exploit dev / Forensics
    check_cmd ROPgadget
    check_cmd ropr
    check_cmd one_gadget
    check_cmd nasm
    check_cmd john
    check_cmd fls

    # Windows (Wine)
    check_cmd wine

    # Android
    check_cmd apktool
    check_cmd d2j-dex2jar

    # Python RE libraries
    check_py angr
    check_py capstone
    check_py unicorn
    check_py pwn
    check_py r2pipe
    check_py rzpipe
    check_py elftools
    check_py yara
    check_py lief
    check_py pyshark
    check_py ropper
    check_py keystone
    check_py pefile
    check_py malduck
    check_py floss
    check_py pyhidra
'

# Clean up caches and unnecessary files to shrink the image
echo ""
echo "=== Cleaning up caches ==="
incus exec "$BUILD_CONTAINER" -- bash -c '
    export PATH="/opt/venv/bin:/root/.nix-profile/bin:$PATH"

    # pip cache
    pip cache purge 2>/dev/null || true
    rm -rf /root/.cache/pip /root/.cache/uv 2>/dev/null || true

    # npm cache
    npm cache clean --force 2>/dev/null || true
    rm -rf /root/.npm/_cacache 2>/dev/null || true

    # Nix garbage collection — remove unreferenced store paths
    nix-collect-garbage -d 2>/dev/null || true

    # Remove old NixOS system generations (keeps current only)
    nix-env --delete-generations old 2>/dev/null || true

    # Nix channel cache / tarballs
    rm -rf /root/.cache/nix 2>/dev/null || true
    rm -rf /nix/var/nix/temproots/* 2>/dev/null || true

    # Python bytecache — regenerates on import
    find /opt/venv -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

    # Misc caches
    rm -rf /tmp/* /var/tmp/* 2>/dev/null || true
    rm -rf /root/.cache/pip /root/.cache/fontconfig 2>/dev/null || true

    # Strip .pyc from site-packages (saves ~5-10% of venv)
    find /opt/venv -name "*.pyc" -delete 2>/dev/null || true

    echo "Cleanup done."
'

# Stop and publish as image
echo "Stopping container and creating image..."
incus stop "$BUILD_CONTAINER"
incus publish "$BUILD_CONTAINER" --alias "$IMAGE_ALIAS" \
    description="NixOS with reverser RE tools ($(date +%Y-%m-%d))"

# cleanup trap handles deletion

echo ""
echo "=== Image '$IMAGE_ALIAS' created successfully ==="
echo "Verify with: incus image list"
echo "Test with:   harness-test"
