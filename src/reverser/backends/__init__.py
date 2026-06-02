"""Backend abstraction for running the RE agent with different LLM providers."""

from .base import AgentEvent, Backend
from .tools import mcp_tools_to_openai, extract_tool_result_text

__all__ = [
    "AgentEvent",
    "Backend",
    "DEFAULT_API_BASES",
    "resolve_api_base",
    "mcp_tools_to_openai",
    "extract_tool_result_text",
    "create_backend",
]


# Default api_base per backend key. Shared between create_backend()
# (the runtime dispatcher) and the gui_service models route.
DEFAULT_API_BASES: dict[str, str] = {
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
}
_GENERIC_DEFAULT_API_BASE = "http://localhost:8000/v1"


def resolve_api_base(name: str, api_base: str | None) -> str:
    """Return explicit API base or the backend's default.

    GUI/API callers may send an empty string when the optional field is left
    blank. Treat that the same as None so LM Studio and Ollama fall back to
    their local defaults.
    """
    cleaned = api_base.strip() if api_base is not None else None
    return cleaned or DEFAULT_API_BASES.get(name, _GENERIC_DEFAULT_API_BASE)


def create_backend(
    name: str,
    tools: list,
    *,
    model: str | None = None,
    api_base: str | None = None,
    model_family: str | None = None,
    token_cost_per_1k: float = 0.0,
) -> Backend:
    """Factory to create a backend by name.

    Args:
        name: 'claude', 'ollama', 'lmstudio', or any OpenAI-compatible provider.
        tools: List of SdkMcpTool instances (from the tools package).
        model: Model name/tag. Required for non-claude backends.
        api_base: API base URL override. Defaults per backend.
        model_family: 'deepseek' | 'generic' | None. None (default) means
            auto-detect from the model name. Ignored by the Claude backend.
    """
    if name == "claude":
        from .claude import ClaudeBackend
        return ClaudeBackend(tools)

    # Everything else uses the OpenAI-compatible backend.
    # 'ollama' and 'lmstudio' are convenience shortcuts that pre-fill the
    # api_base with each tool's default port. Any other name falls through
    # to the generic OpenAI default (port 8000); use --api-base to override.
    if not model:
        raise ValueError(f"--model is required for backend '{name}'")

    api_base = resolve_api_base(name, api_base)

    from .openai_compat import OpenAICompatBackend
    return OpenAICompatBackend(
        tools=tools,
        model=model,
        api_base=api_base,
        model_family=model_family,
        token_cost_per_1k=token_cost_per_1k,
    )
