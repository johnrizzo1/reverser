#!/usr/bin/env node
// Read [project].version from pyproject.toml and write it to
// desktop/package.json's "version" field. Runs before electron-builder.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pyprojectPath = path.join(root, "pyproject.toml");
const packageJsonPath = path.join(root, "desktop", "package.json");

const pyproject = fs.readFileSync(pyprojectPath, "utf-8");
// Minimal TOML parsing for the [project] version field — avoid pulling in a TOML dep.
const m = pyproject.match(/^\[project\][^\[]*?\bversion\s*=\s*"([^"]+)"/m);
if (!m) {
  console.error("[sync-version] FAIL: could not find [project] version in pyproject.toml");
  process.exit(1);
}
const version = m[1];

const pkg = JSON.parse(fs.readFileSync(packageJsonPath, "utf-8"));
if (pkg.version === version) {
  console.log(`[sync-version] desktop/package.json already at ${version}`);
  process.exit(0);
}
pkg.version = version;
fs.writeFileSync(packageJsonPath, JSON.stringify(pkg, null, 2) + "\n");
console.log(`[sync-version] desktop/package.json -> ${version}`);
