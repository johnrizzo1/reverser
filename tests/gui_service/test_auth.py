"""Auth middleware unit tests.

The token check is applied at the FastAPI dependency layer; here we test
the predicate that the dependency wraps so we don't need a full app to
exercise its edge cases.
"""
from reverser.gui_service.auth import is_authorized


def test_is_authorized_accepts_correct_bearer(service_config):
    header = f"Bearer {service_config.token}"
    assert is_authorized(header, service_config.token) is True


def test_is_authorized_rejects_wrong_bearer(service_config):
    assert is_authorized("Bearer wrong-token", service_config.token) is False


def test_is_authorized_rejects_missing_header(service_config):
    assert is_authorized(None, service_config.token) is False


def test_is_authorized_rejects_empty_header(service_config):
    assert is_authorized("", service_config.token) is False


def test_is_authorized_rejects_wrong_scheme(service_config):
    assert is_authorized(f"Basic {service_config.token}", service_config.token) is False


def test_is_authorized_accepts_ws_query_token(service_config):
    """WS upgrade uses ?token=… in the query string."""
    from reverser.gui_service.auth import is_authorized_query
    assert is_authorized_query(service_config.token, service_config.token) is True
    assert is_authorized_query("wrong", service_config.token) is False
    assert is_authorized_query(None, service_config.token) is False
