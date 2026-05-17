# Release checklist

Run through this before tagging a release. Each item must pass on each
supported platform (macOS arm64, Linux x64).

## Pre-release

- [ ] All open PRs for the milestone are merged.
- [ ] `pytest tests/ -q` passes on `main` (excluding the pre-existing
      `test_handshake_*` env failures).
- [ ] `cd desktop && npm run lint` passes.
- [ ] CI build matrix is green on the latest commit on `main`.
- [ ] CHANGELOG / release notes drafted (if applicable).
- [ ] `pyproject.toml` version bumped.

## Per-platform manual verification

After running `package-installer` (or downloading a CI artifact):

### macOS arm64

- [ ] Installer (`.dmg`) opens; Applications icon visible; drag-to-install works.
- [ ] First launch (without removing quarantine): see the Gatekeeper dialog.
      Run `xattr -d com.apple.quarantine /Applications/reverser.app`. Re-open.
- [ ] App window appears; Dashboard view loads; ≥10 profile cards visible
      within 30 seconds.
- [ ] Settings → Health: every check shows OK (Python service, nmap,
      Playwright Chromium).
- [ ] Create a new engagement using the `general` profile, type a message,
      receive an agent response. (Full handshake + agent loop.)
- [ ] Switch to the `webpentest` profile, create an engagement, verify
      Playwright Chromium launches (the agent emits a screenshot or page
      title).
- [ ] Stop the engagement; verify the snapshot persists in
      `~/Library/Application Support/reverser/project/targets/<target>/`.

### Linux x64

- [ ] `chmod +x reverser-*.AppImage && ./reverser-*.AppImage` launches.
- [ ] App window appears; Dashboard view loads; ≥10 profile cards visible.
- [ ] Settings → Health: same OK checks as macOS.
- [ ] Same `general` profile end-to-end test.
- [ ] Same `webpentest` profile test.
- [ ] Snapshot persists in `~/.config/reverser/project/targets/<target>/`.

## Tag the release

- [ ] `git tag v<version> && git push origin v<version>`
- [ ] Release workflow finishes successfully on GitHub Actions.
- [ ] GitHub Release appears with both `.dmg` and `.AppImage` plus
      `latest-mac.yml` and `latest-linux.yml`.
- [ ] gitea mirror release appears with the same assets.
