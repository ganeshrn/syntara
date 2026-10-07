"""Configuration tests for the Syntara-to-Execution-Plane bridge."""

import pytest
from pydantic import ValidationError

from syntara.core.config.base import Settings


def test_execution_plane_settings_load_operator_owned_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """The endpoint and bounded timing controls are ordinary Syntara deployment settings."""
    monkeypatch.setenv("APP_EXECUTION_PLANE_URL", "https://execution-plane.example.test/")
    monkeypatch.setenv("APP_EXECUTION_PLANE_SERVICE_TOKEN_LIFETIME_SECONDS", "120")
    monkeypatch.setenv("APP_EXECUTION_PLANE_POLL_INTERVAL_SECONDS", "1.5")

    settings = Settings()

    assert settings.execution_plane_url == "https://execution-plane.example.test"
    assert settings.execution_plane_service_token_lifetime_seconds == 120
    assert settings.execution_plane_poll_interval_seconds == 1.5


def test_execution_plane_settings_reject_non_http_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    """A local path or non-HTTP URI cannot become an internal dispatch route."""
    monkeypatch.setenv("APP_EXECUTION_PLANE_URL", "file:///tmp/execution-plane")

    with pytest.raises(ValidationError, match="absolute HTTP"):
        Settings()
