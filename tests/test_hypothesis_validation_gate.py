import pytest
from types import SimpleNamespace

import reverser.tools.kb as kbmod
from reverser.tools.kb import kb_add_hypothesis, kb_update_hypothesis
from reverser.adversary import Verdict


def _handler(tool):
    return getattr(tool, "handler", None) or getattr(tool, "fn", None) or tool


def _session(*, validation_backend=None):
    from reverser.sessions import current_session
    sess = SimpleNamespace(config=SimpleNamespace(
        validation_backend=validation_backend, validation_model="adv",
        validation_api_base=None))
    token = current_session.set(sess)
    return sess, token


async def _confirmable_hyp(target):
    add = await _handler(kb_add_hypothesis)({
        "target": target, "statement": "DC allows unsigned SMB",
        "rationale": "nmap", "confidence": 60})
    hid = int(add["content"][0]["text"].split("#")[1].split(" ")[0])
    await _handler(kb_update_hypothesis)({"target": target, "id": hid, "status": "testing"})
    return hid


@pytest.fixture(autouse=True)
def _authz(monkeypatch):
    monkeypatch.setattr(kbmod, "_check_auth", lambda: None)


@pytest.mark.asyncio
async def test_refuted_blocks_confirm(tmp_targets_dir, monkeypatch):
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.sessions import current_session
    _sess, _tok = _session(validation_backend="claude")
    try:
        async def fake_adv(*a, **k):
            return Verdict(verdict="refuted", reasoning="no SMB signing evidence", model="adv")
        monkeypatch.setattr(kbmod, "run_adversary_validation", fake_adv)
        hid = await _confirmable_hyp("t1")
        res = await _handler(kb_update_hypothesis)({
            "target": "t1", "id": hid, "status": "confirmed",
            "evidence_refs": [{"kind": "note", "id": 1}]})
        assert res.get("is_error") is True
        assert "refus" in res["content"][0]["text"].lower()
        from reverser.kb import for_target
        assert for_target("t1").get_hypothesis(hid).status == "testing"
    finally:
        current_session.reset(_tok)


@pytest.mark.asyncio
async def test_upheld_allows_confirm(tmp_targets_dir, monkeypatch):
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.sessions import current_session
    _sess, _tok = _session(validation_backend="claude")
    try:
        async def fake_adv(*a, **k):
            return Verdict(verdict="upheld", reasoning="evidence holds", model="adv")
        monkeypatch.setattr(kbmod, "run_adversary_validation", fake_adv)
        hid = await _confirmable_hyp("t2")
        res = await _handler(kb_update_hypothesis)({
            "target": "t2", "id": hid, "status": "confirmed",
            "evidence_refs": [{"kind": "note", "id": 1}]})
        assert res.get("is_error") is not True
        from reverser.kb import for_target
        assert for_target("t2").get_hypothesis(hid).status == "confirmed"
    finally:
        current_session.reset(_tok)


@pytest.mark.asyncio
async def test_no_validator_skips_adversary(tmp_targets_dir, monkeypatch):
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.sessions import current_session
    _sess, _tok = _session(validation_backend=None)
    try:
        called = {"n": 0}
        async def fake_adv(*a, **k):
            called["n"] += 1
            return Verdict(verdict="refuted", reasoning="x")
        monkeypatch.setattr(kbmod, "run_adversary_validation", fake_adv)
        hid = await _confirmable_hyp("t3")
        res = await _handler(kb_update_hypothesis)({
            "target": "t3", "id": hid, "status": "confirmed",
            "evidence_refs": [{"kind": "note", "id": 1}]})
        assert res.get("is_error") is not True and called["n"] == 0
        from reverser.kb import for_target
        assert for_target("t3").get_hypothesis(hid).status == "confirmed"
    finally:
        current_session.reset(_tok)


@pytest.mark.asyncio
async def test_adversary_error_fails_open(tmp_targets_dir, monkeypatch):
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.sessions import current_session
    _sess, _tok = _session(validation_backend="claude")
    try:
        async def boom(*a, **k):
            raise RuntimeError("validator down")
        monkeypatch.setattr(kbmod, "run_adversary_validation", boom)
        hid = await _confirmable_hyp("t4")
        res = await _handler(kb_update_hypothesis)({
            "target": "t4", "id": hid, "status": "confirmed",
            "evidence_refs": [{"kind": "note", "id": 1}]})
        assert res.get("is_error") is not True
        from reverser.kb import for_target
        assert for_target("t4").get_hypothesis(hid).status == "confirmed"
    finally:
        current_session.reset(_tok)


@pytest.mark.asyncio
async def test_non_confirmed_transition_skips_adversary(tmp_targets_dir, monkeypatch):
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.sessions import current_session
    _sess, _tok = _session(validation_backend="claude")
    try:
        called = {"n": 0}
        async def fake_adv(*a, **k):
            called["n"] += 1
            return Verdict(verdict="refuted", reasoning="x")
        monkeypatch.setattr(kbmod, "run_adversary_validation", fake_adv)
        hid = await _confirmable_hyp("t5")
        res = await _handler(kb_update_hypothesis)({
            "target": "t5", "id": hid, "status": "abandoned", "rationale": "drop"})
        assert res.get("is_error") is not True and called["n"] == 0
    finally:
        current_session.reset(_tok)
