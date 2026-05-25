"""Regression checks on LLM-facing tool specs in reverser.tools.web.

Tool descriptions and defaults are how the LLM learns to call a tool. If
the example tags don't match anything in nuclei's template metadata, the
LLM dutifully copies the example and gets zero hits — which is exactly
what happened with the original `cves,misconfigurations,exposures`
example (the real tags are the singular `cve`, `misconfig`, `exposure`).
"""
import pytest
from unittest.mock import MagicMock

from reverser.tools.web import nuclei_scan


def test_nuclei_templates_default_uses_real_tags():
    """The `templates` arg has a default so an LLM that omits it still
    loads a useful slice of templates. The default must use tag names
    that nuclei's templates actually carry — singular `cve`, `misconfig`,
    `exposure`. The earlier plural forms each match ~0 templates.
    """
    spec = nuclei_scan.input_schema["templates"]
    assert spec.get("default") == "cve,misconfig,exposure", (
        f"templates default must use real nuclei tags; got {spec.get('default')!r}"
    )


def test_nuclei_templates_description_uses_singular_tags():
    """The description's example must match what the LLM should actually
    pass. The original wording showed plural forms that load zero
    templates."""
    desc = nuclei_scan.input_schema["templates"]["description"]
    assert "cve" in desc, f"description should mention `cve` tag: {desc!r}"
    # The plurals are the broken forms; assert they're gone so a future
    # edit doesn't silently revert.
    for broken in ("cves", "misconfigurations", "exposures"):
        assert broken not in desc, (
            f"description must not show {broken!r} (matches ~0 templates): {desc!r}"
        )


# ── web_browser _ensure_browser target-mismatch semantics ────────────────────


def test_ensure_browser_raises_on_target_mismatch(monkeypatch):
    """_ensure_browser compares by string value, not session identity.

    When a browser is already running for target A and _ensure_browser is
    called with a different string B, it must raise RuntimeError. This
    confirms that the comparison (`_state["target"] != target`) is purely
    value-based — meaning that when callers pass sess.active_address.value,
    any rebinding of the target's primary address correctly invalidates the
    cached browser singleton without requiring object-identity tricks.
    """
    import reverser.tools.web_browser as wb

    mock_browser = MagicMock()
    mock_browser.is_connected.return_value = True

    # Simulate a running browser locked to "target-a"
    monkeypatch.setitem(wb._state, "browser", mock_browser)
    monkeypatch.setitem(wb._state, "target", "target-a")

    with pytest.raises(RuntimeError, match="target-a"):
        wb._ensure_browser("target-b")


def test_ensure_browser_same_target_does_not_raise(monkeypatch):
    """_ensure_browser is idempotent when called with the same string value."""
    import reverser.tools.web_browser as wb

    mock_page = MagicMock()
    mock_browser = MagicMock()
    mock_browser.is_connected.return_value = True

    monkeypatch.setitem(wb._state, "browser", mock_browser)
    monkeypatch.setitem(wb._state, "target", "target-a")
    monkeypatch.setitem(wb._state, "page", mock_page)

    # Same target string — must return cached page without raising
    result = wb._ensure_browser("target-a")
    assert result is mock_page
