# Manual smoke test — Playwright web_browser_* tools

**Goal:** End-to-end verify the 14 web_browser_* tools against a real
target. Out-of-suite; ~20 minutes.

**Prereqs:**
- `devenv shell` active; venv has playwright; ~/.cache/ms-playwright/ has Chromium
- `.reverser-authorized` file present (or `REVERSER_PENTEST_AUTHORIZED=1`)
- A target webapp. Suggestion: a local DVWA instance, or
  `https://juice-shop.herokuapp.com` (publicly authorized testing target).

---

## Walkthrough

### 1. Start the browser

```
reverser i -p webpentest https://juice-shop.herokuapp.com
```

In the TUI input box:
```
web_browser_start(target="juice-shop.herokuapp.com")
```

Expected: `status: started`, viewport reported, started_at set.

Confirm: `web_browser_status()` reports `running`, current_url initially blank.

### 2. Navigate to the landing page

```
web_browser_navigate(url="https://juice-shop.herokuapp.com")
```

Expected: status=200, title contains "Juice Shop", url_final populated.

### 3. Snapshot the page

```
web_browser_snapshot()
```

Expected: accessibility tree includes navigation buttons, "Account" link,
search box. Console errors empty or minimal.

### 4. Crawl the SPA

```
web_browser_crawl(start_url="https://juice-shop.herokuapp.com",
                  max_pages=15, max_depth=2)
```

Expected:
- `pages_visited`: 10-15 URLs across /#/about, /#/contact, /#/login, etc.
- `forms_discovered`: at least the search and login forms.
- `apis_called`: includes `/api/Products`, `/rest/...`, etc.
- `out_of_scope_skipped: 0` (no scope.toml).

This is the SPA-discoverability win — none of those /#/ routes would
show up in a path-fuzzing wordlist.

### 5. Log in (form fill)

Pick a known account or register one first via the UI. Then:

```
web_browser_navigate(url="https://juice-shop.herokuapp.com/#/login")
web_browser_fill_form(
    fields={"input#email": "test@juice-sh.op", "input#password": "test123"},
    submit_selector="button#loginButton",
)
```

Expected: post_submit_url shows /#/search or similar (login redirected away from /#/login).

### 6. Network log

```
web_browser_network_log(filter_url="/api/")
```

Expected: at least the POST /rest/user/login and subsequent /api/Quantity?... calls.

### 7. XSS confirmation

Add a finding first via `kb_add_finding(...)`. Then:

```
web_browser_navigate(url="https://juice-shop.herokuapp.com/#/search?q=test")
web_browser_confirm_xss(
    payload="<iframe src=\"javascript:window.__xss_fired_sentinel__=true\">",
    finding_id=<id>,
)
```

(Juice Shop has known XSS at /#/search via the q parameter; refer to
their challenge list.)

Expected: confirmed=True, evidence="sentinel_global", screenshot_path
populated under findings/<id>/.

Confirm: `kb_show` lists the finding with an evidence_paths entry; the
screenshot file exists.

### 8. Capture standalone evidence

```
web_browser_capture_finding(finding_id=<id>, description="post-XSS landing")
```

Expected: screenshot-2.png appears (auto-increment).

### 9. Status check

```
web_browser_status()
```

Expected: screenshots_taken >= 2, target is correct, started_at unchanged.

### 10. Clean shutdown

```
web_browser_close()
```

Expected: status=closed. Subsequent `web_browser_status()` reports
not_running.

---

## Pass criteria

- All 14 tools called at least once without error
- At least one XSS confirmation produces both evidence and a screenshot
- Crawl discovers SPA routes that ffuf would miss
- Scope.toml enforcement works (try navigating to an out-of-scope host —
  should fail with `scope.toml violation`)
- Browser shuts down cleanly on `web_browser_close()`

## Fail-safe

If chromium hangs, the process is `chromium` or `chrome` in `ps aux`. Kill
manually:
```
pkill -f "chromium.*--remote-debugging-port"
```
Then re-run `web_browser_start`.
