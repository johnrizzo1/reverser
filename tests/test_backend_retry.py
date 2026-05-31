import pytest

from reverser.backends.retry import classify_error, call_with_retries


class _FakeRateLimit(Exception):
    status_code = 429

class _FakeAuth(Exception):
    status_code = 401

class _FakeServer(Exception):
    status_code = 503

class APIConnectionError(Exception):  # name match, no status_code
    pass

class APITimeoutError(Exception):
    pass

class _Weird(Exception):
    pass


@pytest.mark.parametrize("exc,expected", [
    (_FakeRateLimit(), "transient"),
    (_FakeServer(), "transient"),
    (APIConnectionError(), "transient"),
    (APITimeoutError(), "transient"),
    (_FakeAuth(), "terminal"),
    (_Weird(), "terminal"),
])
def test_classify_error(exc, expected):
    assert classify_error(exc) == expected


def test_classify_408_409_transient():
    class E408(Exception):
        status_code = 408
    class E409(Exception):
        status_code = 409
    assert classify_error(E408()) == "transient"
    assert classify_error(E409()) == "transient"


async def _record(store, d):
    store.append(d)


@pytest.mark.asyncio
async def test_returns_on_first_success():
    sleeps = []
    async def call():
        return "ok"
    out = await call_with_retries(call, sleep=lambda d: _record(sleeps, d))
    assert out == "ok" and sleeps == []


@pytest.mark.asyncio
async def test_retries_transient_then_succeeds():
    sleeps, retries = [], []
    calls = {"n": 0}
    async def call():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _FakeRateLimit()
        return "ok"
    out = await call_with_retries(
        call, sleep=lambda d: _record(sleeps, d),
        on_retry=lambda a, d, e: retries.append((a, d)), rng=lambda: 0.0,
    )
    assert out == "ok"
    assert len(sleeps) == 1 and len(retries) == 1 and retries[0][0] == 1
    assert sleeps[0] == 2.0


@pytest.mark.asyncio
async def test_terminal_reraises_immediately():
    sleeps = []
    async def call():
        raise _FakeAuth()
    with pytest.raises(_FakeAuth):
        await call_with_retries(call, sleep=lambda d: _record(sleeps, d))
    assert sleeps == []


@pytest.mark.asyncio
async def test_exhausts_then_reraises():
    sleeps = []
    async def call():
        raise _FakeRateLimit()
    with pytest.raises(_FakeRateLimit):
        await call_with_retries(call, max_retries=3, sleep=lambda d: _record(sleeps, d), rng=lambda: 0.0)
    assert sleeps == [2.0, 4.0, 8.0]


@pytest.mark.asyncio
async def test_backoff_caps():
    sleeps = []
    async def call():
        raise _FakeRateLimit()
    with pytest.raises(_FakeRateLimit):
        await call_with_retries(call, max_retries=6, base_delay=2.0, cap=30.0,
                                sleep=lambda d: _record(sleeps, d), rng=lambda: 0.0)
    assert sleeps == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
