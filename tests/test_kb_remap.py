from reverser.kb.store import KB, HostFact, ServiceFact


def _fresh_kb(tmp_path, monkeypatch, target="recon"):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    import reverser.kb
    reverser.kb._kb_cache.clear()
    return KB(target)


def test_remap_simple_rename(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    kb.record_host(HostFact(ip="10.0.0.1", hostname="box.htb"))
    kb.record_service(ServiceFact(host_ip="10.0.0.1", port=80, proto="tcp", service="http"))
    counts = kb.remap_address("10.0.0.1", "10.0.0.2")
    assert counts["hosts"] == 1 and counts["services"] == 1
    hosts = kb.get_hosts()
    assert [h.ip for h in hosts] == ["10.0.0.2"]
    assert hosts[0].hostname == "box.htb"
    assert [s.host_ip for s in kb.get_services()] == ["10.0.0.2"]


def test_remap_merges_on_host_conflict(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    kb.record_host(HostFact(ip="10.0.0.1", hostname="box.htb"))
    kb.record_host(HostFact(ip="10.0.0.2", os="Linux"))
    kb.remap_address("10.0.0.1", "10.0.0.2")
    hosts = kb.get_hosts()
    assert [h.ip for h in hosts] == ["10.0.0.2"]
    assert hosts[0].os == "Linux" and hosts[0].hostname == "box.htb"


def test_remap_skips_duplicate_service(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    kb.record_service(ServiceFact(host_ip="10.0.0.1", port=80, proto="tcp", service="http"))
    kb.record_service(ServiceFact(host_ip="10.0.0.2", port=80, proto="tcp", service="http"))
    kb.remap_address("10.0.0.1", "10.0.0.2")
    svcs = kb.get_services()
    assert len(svcs) == 1 and svcs[0].host_ip == "10.0.0.2"


def test_remap_records_note(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    kb.record_host(HostFact(ip="10.0.0.1"))
    kb.remap_address("10.0.0.1", "10.0.0.2")
    notes = kb.get_notes()
    assert any("10.0.0.1" in n and "10.0.0.2" in n for n in notes)  # get_notes returns strings


def test_remap_same_ip_noop(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    kb.record_host(HostFact(ip="10.0.0.1"))
    counts = kb.remap_address("10.0.0.1", "10.0.0.1")
    assert counts == {"hosts": 0, "services": 0, "cred_results": 0}
