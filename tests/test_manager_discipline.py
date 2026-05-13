"""Tests for manager profile prompt + dispatch_specialist reforms."""

import pytest


# ── _has_actionable_findings heuristic ───────────────────────────────


def test_has_actionable_findings_recognizes_findings_section():
    from reverser.tools.dispatch import _has_actionable_findings
    report = """### Findings
The login form at /admin/login is captcha-protected after 5 attempts.
Recommend Playwright with OCR for captcha bypass.
"""
    assert _has_actionable_findings(report) is True


def test_has_actionable_findings_recognizes_suggested_follow_up():
    from reverser.tools.dispatch import _has_actionable_findings
    report = """### TL;DR
Specialist crashed.

### Suggested follow-up
Try CVE-2024-46987 path traversal against /cms-admin/files.
"""
    assert _has_actionable_findings(report) is True


def test_has_actionable_findings_recognizes_hypothesis_outcome():
    from reverser.tools.dispatch import _has_actionable_findings
    report = """### Hypothesis outcome
REFUTED — SSH does not accept the harvested credentials.
"""
    assert _has_actionable_findings(report) is True


def test_has_actionable_findings_rejects_empty_section_body():
    from reverser.tools.dispatch import _has_actionable_findings
    report = """### Findings

### Suggested follow-up

### TL;DR
"""
    assert _has_actionable_findings(report) is False


def test_has_actionable_findings_rejects_short_section_body():
    """Section header but <20 chars under it = not actionable."""
    from reverser.tools.dispatch import _has_actionable_findings
    report = """### Findings
nothing.
"""
    assert _has_actionable_findings(report) is False


def test_has_actionable_findings_rejects_pure_traceback():
    """A stack trace without any of the three section headers doesn't qualify."""
    from reverser.tools.dispatch import _has_actionable_findings
    report = """Traceback (most recent call last):
  File "/foo/bar.py", line 123, in xyz
    raise ConnectionError("refused")
ConnectionError: refused
"""
    assert _has_actionable_findings(report) is False
