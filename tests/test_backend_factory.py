"""Tests for the create_backend factory's name → api_base resolution."""

from unittest.mock import patch

import pytest

from reverser.backends import create_backend


def _resolved_api_base(name, model="any-model", api_base=None):
    """Capture the api_base passed to OpenAICompatBackend without actually connecting."""
    with patch("reverser.backends.openai_compat.OpenAICompatBackend") as M:
        create_backend(name, tools=[], model=model, api_base=api_base)
        assert M.call_count == 1
        kwargs = M.call_args.kwargs
        return kwargs["api_base"]


def test_ollama_default_api_base():
    assert _resolved_api_base("ollama") == "http://localhost:11434/v1"


def test_lmstudio_default_api_base():
    assert _resolved_api_base("lmstudio") == "http://localhost:1234/v1"


def test_lmstudio_blank_api_base_uses_local_default():
    assert _resolved_api_base("lmstudio", api_base="") == "http://localhost:1234/v1"
    assert _resolved_api_base("lmstudio", api_base="   ") == "http://localhost:1234/v1"


def test_unknown_name_falls_back_to_generic_default():
    """Any name we don't special-case routes to the generic OpenAI default."""
    assert _resolved_api_base("some-other-server") == "http://localhost:8000/v1"


def test_explicit_api_base_overrides_default():
    """Passing --api-base wins over the name-based default for every backend."""
    custom = "http://192.168.1.50:9999/v1"
    assert _resolved_api_base("ollama", api_base=custom) == custom
    assert _resolved_api_base("lmstudio", api_base=custom) == custom
    assert _resolved_api_base("lmstudio", api_base=f"  {custom}  ") == custom


def test_claude_does_not_require_model_or_api_base():
    """Claude backend doesn't go through OpenAICompatBackend at all."""
    with patch("reverser.backends.claude.ClaudeBackend") as M:
        create_backend("claude", tools=[])
        M.assert_called_once_with([])


def test_non_claude_backends_require_model():
    """Forgetting --model for an OpenAI-compatible backend should raise."""
    with pytest.raises(ValueError, match="model"):
        create_backend("ollama", tools=[], model=None)
    with pytest.raises(ValueError, match="model"):
        create_backend("lmstudio", tools=[], model=None)


def test_model_family_passes_through_to_openai_compat():
    """create_backend forwards model_family to OpenAICompatBackend."""
    with patch("reverser.backends.openai_compat.OpenAICompatBackend") as M:
        create_backend(
            "lmstudio",
            tools=[],
            model="deepseek-coder-v2-lite-instruct",
            model_family="deepseek",
        )
        assert M.call_args.kwargs["model_family"] == "deepseek"


def test_model_family_defaults_to_none():
    """When omitted, model_family is None (auto-detect happens inside the backend)."""
    with patch("reverser.backends.openai_compat.OpenAICompatBackend") as M:
        create_backend("ollama", tools=[], model="qwen3-coder")
        assert M.call_args.kwargs.get("model_family") is None


def test_claude_factory_ignores_model_family():
    """Claude path doesn't accept model_family — passing it shouldn't crash."""
    with patch("reverser.backends.claude.ClaudeBackend") as M:
        create_backend("claude", tools=[], model_family="deepseek")
        M.assert_called_once_with([])


def test_create_backend_forwards_token_cost():
    from reverser.backends import create_backend
    be = create_backend("ollama", [], model="m", token_cost_per_1k=1.5)
    assert be._token_cost_per_1k == 1.5


def test_create_backend_token_cost_defaults_zero():
    from reverser.backends import create_backend
    be = create_backend("ollama", [], model="m")
    assert be._token_cost_per_1k == 0.0
