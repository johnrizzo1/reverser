# Nix expression for RE tools that need composition (withPlugins/withExtensions).
# Used by build-image.sh to install into the container.
{ pkgs ? import <nixos> {} }:

let
  # Rizin with the Ghidra decompiler plugin
  rizin-with-ghidra = pkgs.rizin.withPlugins (ps: [
    ps.rz-ghidra
  ]);

  # Ghidra with useful RE extensions
  ghidra-with-extensions = pkgs.ghidra.withExtensions (exts: [
    exts.findcrypt
    exts.ghidra-golanganalyzerextension
    # exts.gnudisassembler  # build fails (binutils compile in sandbox); system binutils covers this
    exts.machinelearning
    exts.wasm
  ]);

in pkgs.buildEnv {
  name = "re-tools-composed";
  paths = [
    ghidra-with-extensions
    rizin-with-ghidra
  ];
}
