"""Unit coverage for non-secret reconciliation-orchestration boundaries."""

from __future__ import annotations

import pytest

from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError
from syntara.plugin_platform.services.registry_access import safe_trust_summary, validate_registry_authorization


def test_authorization_resolver_output_rejects_header_injection() -> None:
    with pytest.raises(CatalogSourceConfigurationError, match="unsafe request value"):
        validate_registry_authorization("Bearer safe\r\nInjected: value")


def test_authorization_resolver_output_rejects_non_string_value() -> None:
    with pytest.raises(CatalogSourceConfigurationError, match="unsafe request value"):
        validate_registry_authorization(object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "summary",
    [
        {"accessToken": "never persist this"},
        {"signature": {"nested": "unsupported"}},
        {"signature": "x" * 513},
        object(),
    ],
)
def test_trust_summary_refuses_secret_or_unbounded_values(summary: object) -> None:
    with pytest.raises(CatalogSourceConfigurationError):
        safe_trust_summary(summary)


def test_trust_summary_accepts_small_safe_inspection_evidence() -> None:
    summary = safe_trust_summary(
        {
            "issuer": "https://issuer.example.test",
            "signature": "verified",
            "transparencyLog": True,
            "policyVersion": 2,
        }
    )

    assert summary == {
        "issuer": "https://issuer.example.test",
        "signature": "verified",
        "transparencyLog": True,
        "policyVersion": 2,
    }
