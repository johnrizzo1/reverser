"""Shared fixtures for gui_service tests."""
import pytest
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
def service_config() -> ServiceConfig:
    """A ServiceConfig with a known token for tests."""
    return ServiceConfig(
        host="127.0.0.1",
        port=0,
        token="test-token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        project_root=".",
    )
