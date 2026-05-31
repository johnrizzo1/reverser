import pytest

from reverser.refocus import refocus_target, RefocusResult, RefocusScopeError


def _make_target(tmp_path, monkeypatch, name="box", ip="10.0.0.1"):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser import paths
    paths._reset_caches_for_tests()
    from reverser.targets import create_target
    return create_target(name=name, kind="network", initial_address=ip)


def test_refocus_promotes_new_address_and_remaps(tmp_path, monkeypatch):
    from reverser.kb.store import KB, HostFact
    _make_target(tmp_path, monkeypatch)
    KB("box").record_host(HostFact(ip="10.0.0.1", hostname="box.htb"))
    res = refocus_target("box", "10.0.0.2")
    assert isinstance(res, RefocusResult)
    assert res.old_ip == "10.0.0.1" and res.new_ip == "10.0.0.2"
    assert res.rows_remapped["hosts"] == 1
    from reverser.targets import load_target
    assert load_target("box").primary_address.value == "10.0.0.2"


def test_refocus_same_ip_is_noop(tmp_path, monkeypatch):
    _make_target(tmp_path, monkeypatch)
    res = refocus_target("box", "10.0.0.1")
    assert res.old_ip == "10.0.0.1" and res.new_ip == "10.0.0.1"
    assert res.rows_remapped == {"hosts": 0, "services": 0, "cred_results": 0}


def test_refocus_reuses_existing_address(tmp_path, monkeypatch):
    from reverser.targets import add_address, load_target
    t = _make_target(tmp_path, monkeypatch)
    add_address(t, "10.0.0.2", "ip")
    refocus_target("box", "10.0.0.2")
    t2 = load_target("box")
    assert t2.primary_address.value == "10.0.0.2"
    assert sum(1 for a in t2.addresses if a.value == "10.0.0.2") == 1


def test_refocus_out_of_scope_aborts_then_force(tmp_path, monkeypatch):
    _make_target(tmp_path, monkeypatch)
    scope_dir = tmp_path / "box"
    scope_dir.mkdir(parents=True, exist_ok=True)
    (scope_dir / "scope.toml").write_text('[scope]\nin_scope_cidrs = ["10.0.0.0/29"]\n')
    from reverser.targets import load_target
    with pytest.raises(RefocusScopeError):
        refocus_target("box", "10.0.0.50")
    # scope check runs BEFORE any mutation — primary must be unchanged
    assert load_target("box").primary_address.value == "10.0.0.1"
    res = refocus_target("box", "10.0.0.50", force_scope=True)
    assert res.scope_warning is not None
    assert load_target("box").primary_address.value == "10.0.0.50"


def test_refocus_unknown_target_raises_refocus_error(tmp_path, monkeypatch):
    from reverser.refocus import RefocusError
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    import reverser.kb
    reverser.kb._kb_cache.clear()
    # no target created — must be a clean RefocusError, not FileNotFoundError
    with pytest.raises(RefocusError):
        refocus_target("ghost", "10.0.0.2")


def test_refocus_retired_address_value_raises_refocus_error(tmp_path, monkeypatch):
    from reverser.refocus import RefocusError
    from reverser.targets import add_address, retire_address, load_target
    t = _make_target(tmp_path, monkeypatch)
    # add a 2nd address, make it primary, then retire the original 10.0.0.1
    t = add_address(t, "10.0.0.9", "ip", make_primary=True)
    orig_id = next(a.id for a in t.addresses if a.value == "10.0.0.1")
    retire_address(t, orig_id)
    # refocusing back to the retired value must be a clean RefocusError, not a 500
    with pytest.raises(RefocusError):
        refocus_target("box", "10.0.0.1")
