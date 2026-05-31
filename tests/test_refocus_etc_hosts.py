from reverser.refocus import rewrite_hosts_entry


def test_rewrite_updates_existing_line(tmp_path):
    p = tmp_path / "hosts"
    p.write_text("127.0.0.1 localhost\n10.0.0.1 box.htb admin.box.htb\n")
    changed = rewrite_hosts_entry(str(p), "box.htb", "10.0.0.1", "10.0.0.2")
    assert changed is True
    assert "10.0.0.2 box.htb admin.box.htb" in p.read_text()
    assert "10.0.0.1 box.htb" not in p.read_text()


def test_rewrite_adds_line_when_missing(tmp_path):
    p = tmp_path / "hosts"
    p.write_text("127.0.0.1 localhost\n")
    changed = rewrite_hosts_entry(str(p), "box.htb", None, "10.0.0.2")
    assert changed is True
    assert "10.0.0.2 box.htb" in p.read_text()


def test_rewrite_noop_when_already_correct(tmp_path):
    p = tmp_path / "hosts"
    p.write_text("10.0.0.2 box.htb\n")
    changed = rewrite_hosts_entry(str(p), "box.htb", "10.0.0.1", "10.0.0.2")
    assert changed is False


def test_rewrite_preserves_comments_and_blanks(tmp_path):
    p = tmp_path / "hosts"
    p.write_text("# comment\n\n127.0.0.1 localhost\n10.0.0.1 box.htb\n")
    rewrite_hosts_entry(str(p), "box.htb", "10.0.0.1", "10.0.0.2")
    text = p.read_text()
    assert "# comment" in text and "127.0.0.1 localhost" in text
