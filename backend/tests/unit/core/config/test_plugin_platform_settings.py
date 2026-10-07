"""Settings coverage for opt-in local plugin-platform development support."""

from __future__ import annotations

from syntara.core.config.base import Settings


def test_local_poc_catalog_reconciliation_is_disabled_by_default_and_requires_an_explicit_switch(
    monkeypatch,
) -> None:
    """The unsafe local path must not become active from an absent or ambient setting."""
    monkeypatch.delenv("APP_PLUGIN_PLATFORM_LOCAL_POC_ENABLED", raising=False)
    assert Settings().plugin_platform_local_poc_enabled is False

    monkeypatch.setenv("APP_PLUGIN_PLATFORM_LOCAL_POC_ENABLED", "true")
    assert Settings().plugin_platform_local_poc_enabled is True
