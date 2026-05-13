"""Tests for target-name sanitization in sessions.target_key and CLI validation."""

import pytest


# ── target_key behavior ──────────────────────────────────────────────


def test_target_key_strips_http_scheme():
    from reverser.sessions import target_key
    assert target_key("http://10.129.60.148") == "10.129.60.148"


def test_target_key_strips_https_scheme_with_path():
    from reverser.sessions import target_key
    # URL with path → netloc only
    assert target_key("https://10.129.60.148/admin") == "10.129.60.148"


def test_target_key_takes_cidr_network_portion():
    from reverser.sessions import target_key
    assert target_key("10.129.244.0/24") == "10.129.244.0"


def test_target_key_scrubs_special_chars():
    from reverser.sessions import target_key
    # Sentence-like input → underscores
    result = target_key("As is common in real life pentests")
    assert "_" in result
    assert " " not in result


def test_target_key_clamps_length_at_64():
    from reverser.sessions import target_key
    long_input = "a" * 200
    result = target_key(long_input)
    assert len(result) <= 64


def test_target_key_lowercases_everything():
    from reverser.sessions import target_key
    assert target_key("EXAMPLE.COM") == "example.com"


def test_target_key_raises_on_empty_input():
    from reverser.sessions import target_key
    with pytest.raises(ValueError, match="non-empty"):
        target_key("")
    with pytest.raises(ValueError, match="non-empty"):
        target_key("   ")


def test_target_key_preserves_plain_ip():
    from reverser.sessions import target_key
    assert target_key("10.10.10.5") == "10.10.10.5"


def test_target_key_preserves_hostname():
    from reverser.sessions import target_key
    assert target_key("dc01.corp.local") == "dc01.corp.local"


def test_target_key_handles_ipv6_port_form():
    """IPv6 with port (host[:port]) should keep its colons."""
    from reverser.sessions import target_key
    # 192.168.1.1:8080 — colon allowed by canonical regex
    assert target_key("192.168.1.1:8080") == "192.168.1.1:8080"


def test_target_key_strips_abs_path_basename():
    """Existing behavior: absolute paths reduced to basename."""
    from reverser.sessions import target_key
    assert target_key("/tmp/binary") == "binary"


# ── _is_canonical_target_name behavior ────────────────────────────────


def test_is_canonical_target_name_accepts_ip():
    from reverser.sessions import _is_canonical_target_name
    assert _is_canonical_target_name("10.10.10.5") is True


def test_is_canonical_target_name_accepts_hostname():
    from reverser.sessions import _is_canonical_target_name
    assert _is_canonical_target_name("dc01.corp.local") is True


def test_is_canonical_target_name_rejects_url_with_colon_slash():
    """'http:' as a directory name is bogus from CLI parsing."""
    from reverser.sessions import _is_canonical_target_name
    assert _is_canonical_target_name("http:") is False


def test_is_canonical_target_name_rejects_sentence():
    from reverser.sessions import _is_canonical_target_name
    assert _is_canonical_target_name("As is common in real life pentests") is False
