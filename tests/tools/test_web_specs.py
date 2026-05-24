"""Regression checks on LLM-facing tool specs in reverser.tools.web.

Tool descriptions and defaults are how the LLM learns to call a tool. If
the example tags don't match anything in nuclei's template metadata, the
LLM dutifully copies the example and gets zero hits — which is exactly
what happened with the original `cves,misconfigurations,exposures`
example (the real tags are the singular `cve`, `misconfig`, `exposure`).
"""
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
