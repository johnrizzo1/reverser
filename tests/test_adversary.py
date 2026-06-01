import pytest

from reverser.adversary import Verdict, parse_verdict, run_adversary_validation


def test_parse_json_block():
    txt = 'prose\n```json\n{"verdict": "refuted", "reasoning": "no link to RCE"}\n```'
    v = parse_verdict(txt)
    assert v.verdict == "refuted" and "RCE" in v.reasoning


def test_parse_markdown_fallback():
    v = parse_verdict("VERDICT: upheld\nREASONING: evidence holds")
    assert v.verdict == "upheld" and "holds" in v.reasoning


def test_parse_unparseable_is_inconclusive():
    assert parse_verdict("rambling with no verdict").verdict == "inconclusive"


def test_parse_normalizes_unknown_verdict_to_inconclusive():
    assert parse_verdict('```json\n{"verdict": "maybe", "reasoning": "x"}\n```').verdict == "inconclusive"


class _FakeBackend:
    def __init__(self, *a, **k):
        self.kwargs = None
    async def run(self, prompt, system_prompt, *, max_turns=50, max_budget_usd=5.0, allowed_tools=None):
        self.kwargs = {"allowed_tools": allowed_tools, "max_turns": max_turns}
        from reverser.backends.base import AgentEvent
        yield AgentEvent(kind="text", content='```json\n{"verdict":"refuted","reasoning":"gap"}\n```')
        yield AgentEvent(kind="result", subtype="success", cost=0.02, turns=1)


@pytest.mark.asyncio
async def test_run_adversary_is_read_only_and_parses(monkeypatch):
    import reverser.adversary as adv
    fake = _FakeBackend()
    monkeypatch.setattr(adv, "create_backend", lambda *a, **k: fake)
    v = await run_adversary_validation(
        "DC allows unsigned SMB", "evidence: nmap signing:off",
        backend_name="claude", model=None, api_base=None,
    )
    assert v.verdict == "refuted" and v.cost == 0.02
    assert fake.kwargs["allowed_tools"] == []   # read-only: no tools
