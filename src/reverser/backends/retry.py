"""Shared transient-error retry/backoff for LLM backends.

classify_error is duck-typed (status_code / class name) so it works for the
openai exception hierarchy without brittle isinstance ordering, and stays unit-
testable with lightweight fakes.
"""

from __future__ import annotations

import asyncio
import random
from typing import Awaitable, Callable, Optional, TypeVar

T = TypeVar("T")

_TRANSIENT_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "RateLimitError",
    "InternalServerError",
    "APIConnectionTimeoutError",
}


def classify_error(exc: BaseException) -> str:
    """Return 'transient' (worth retrying) or 'terminal' (do not retry).
    Unknown errors are 'terminal' on purpose."""
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    if code is not None:
        try:
            code = int(code)
        except (TypeError, ValueError):
            return "terminal"
        if code >= 500 or code in (408, 409, 429):
            return "transient"
        return "terminal"
    if type(exc).__name__ in _TRANSIENT_NAMES:
        return "transient"
    return "terminal"


async def call_with_retries(
    make_call: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 3,
    base_delay: float = 2.0,
    cap: float = 30.0,
    on_retry: Optional[Callable[[int, float, BaseException], None]] = None,
    sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    rng: Callable[[], float] = random.random,
) -> T:
    """Await make_call(); retry transient failures with capped exponential
    backoff. Terminal errors re-raise immediately; exhaustion re-raises the last
    error. on_retry(attempt, delay, exc) fires before each backoff sleep.
    When sleep is None, asyncio.sleep is resolved at call time (so tests can
    monkeypatch reverser.backends.retry.asyncio.sleep)."""
    _sleep = sleep if sleep is not None else asyncio.sleep
    attempt = 0
    while True:
        try:
            return await make_call()
        except Exception as exc:  # noqa: BLE001 — classify decides
            if classify_error(exc) == "terminal" or attempt >= max_retries:
                raise
            delay = min(cap, base_delay * (2 ** attempt))
            delay += rng() * min(1.0, delay * 0.1)
            if on_retry is not None:
                on_retry(attempt + 1, delay, exc)
            await _sleep(delay)
            attempt += 1
