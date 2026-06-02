from types import SimpleNamespace

from reverser.backends.tokens import estimate_tokens, tokens_from_usage


def test_estimate_tokens_char_over_4():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1       # 4 chars -> 1
    assert estimate_tokens("abcde") == 2      # ceil(5/4) = 2
    assert estimate_tokens("a" * 400) == 100


def test_estimate_tokens_none_safe():
    assert estimate_tokens(None) == 0


def test_tokens_from_usage_object():
    u = SimpleNamespace(prompt_tokens=30, completion_tokens=12)
    assert tokens_from_usage(u) == 42


def test_tokens_from_usage_dict():
    assert tokens_from_usage({"prompt_tokens": 5, "completion_tokens": 7}) == 12


def test_tokens_from_usage_missing_or_none():
    assert tokens_from_usage(None) == 0
    assert tokens_from_usage(SimpleNamespace(prompt_tokens=None, completion_tokens=None)) == 0
    assert tokens_from_usage({"prompt_tokens": 9}) == 9  # missing completion -> 0


def test_agent_event_has_tokens_field():
    from reverser.backends.base import AgentEvent
    e = AgentEvent(kind="result", cost=0.5, tokens=1234)
    assert e.tokens == 1234
    assert AgentEvent(kind="text").tokens is None
