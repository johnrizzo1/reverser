"""Token-count helpers for backends that don't report cost natively.

estimate_tokens: rough char/4 heuristic (the standard ballpark for English text).
tokens_from_usage: read prompt+completion tokens from an OpenAI-style usage block
(object or dict), tolerating missing/None fields.
"""

from __future__ import annotations

import math


def estimate_tokens(text: str | None) -> int:
    """Rough token estimate: ceil(len/4). None/empty -> 0."""
    if not text:
        return 0
    return math.ceil(len(text) / 4)


def _num(v) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def tokens_from_usage(usage) -> int:
    """Total tokens (prompt + completion) from an OpenAI usage block (object or
    dict). Missing/None/unparseable fields count as 0; returns 0 if usage is None."""
    if usage is None:
        return 0
    if isinstance(usage, dict):
        pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
    else:
        pt, ct = getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)
    return _num(pt) + _num(ct)
