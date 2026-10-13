"""Unit coverage for local catalog persistence model invariants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from syntara.plugin_platform.models.catalog import (
    CatalogPluginAsset,
    CatalogPluginVersion,
    PluginSource,
    PluginSourceReconciliation,
)

_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_SOURCE_ID = UUID("019c0493-284d-7e72-aee5-8e3663e15a30")
_RECONCILIATION_ID = UUID("019c0493-284d-7e72-aee5-8e3663e15a31")
_VERSION_ID = UUID("019c0493-284d-7e72-aee5-8e3663e15a32")


def _source_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source_id": "local-quay",
        "display_name": "Local Quay",
        "registry_origin": "https://Quay.Local:8443",
        "index_repository": "syntara/catalog-index",
        "channel": "stable",
        "repository_scopes": ["syntara/plugins"],
        "credential_ref": "credentials/local-quay-pull",
        "trust_policy_ref": "trust/local-quay",
    }
    payload.update(overrides)
    return payload


def _accepted_reconciliation_payload(**overrides: object) -> dict[str, object]:
    issued_at = datetime(2026, 10, 6, 12, tzinfo=UTC)
    payload: dict[str, object] = {
        "plugin_source_id": _SOURCE_ID,
        "state": "accepted",
        "catalog_generation": 42,
        "catalog_index_digest": _DIGEST_A,
        "previous_index_digest": _DIGEST_B,
        "issued_at": issued_at,
        "expires_at": issued_at + timedelta(days=7),
        "entry_count": 2,
    }
    payload.update(overrides)
    return payload


def _catalog_version_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "reconciliation_id": _RECONCILIATION_ID,
        "namespace": "syntara",
        "name": "github",
        "version": "1.0.0",
        "artifact_repository": "syntara/plugins/github",
        "artifact_digest": _DIGEST_A,
        "documentation_digest": _DIGEST_B,
        "runtime_ownerships": ["platform-runner"],
        "canonical_entry": {"name": "github"},
    }
    payload.update(overrides)
    return payload


def test_source_accepts_only_normalized_configuration_and_opaque_references() -> None:
    source = PluginSource.model_validate(_source_payload())

    assert source.registry_origin == "https://quay.local:8443"
    assert source.source_mode == "standard"
    assert source.repository_scopes == ["syntara/plugins"]
    assert source.credential_ref == "credentials/local-quay-pull"
    assert {"token", "password", "private_key", "certificate"}.isdisjoint(PluginSource.model_fields)
    table = PluginSource.__table__  # type: ignore[attr-defined]
    assert any(
        foreign_key.name == "fk_plugin_sources_active_reconciliation" for foreign_key in table.foreign_key_constraints
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_id": "Local-Quay"},
        {"registry_origin": "http://quay.local"},
        {"registry_origin": "https://quay.local/catalog"},
        {"index_repository": "syntara/../catalog-index"},
        {"repository_scopes": ["syntara/plugins", "syntara/plugins"]},
        {"credential_ref": "credentials/with whitespace"},
    ],
)
def test_source_rejects_unsafe_or_ambiguous_configuration(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PluginSource.model_validate(_source_payload(**overrides))


def test_source_accepts_only_the_fixed_local_poc_profile() -> None:
    source = PluginSource.model_validate(
        _source_payload(
            source_mode="local-poc",
            registry_origin="http://localhost:8443",
            credential_ref="development/local-poc-anonymous",
            trust_policy_ref="development/local-poc-unsigned",
            mirror_policy="direct",
        )
    )

    assert source.source_mode == "local-poc"
    assert source.registry_origin == "http://localhost:8443"


@pytest.mark.parametrize(
    "overrides",
    [
        {"registry_origin": "http://registry.example.test"},
        {"credential_ref": "credentials/local-quay-pull"},
        {"trust_policy_ref": "trust/local-quay"},
        {"custom_ca_ref": "trust/local-quay-ca"},
        {"mirror_policy": "digest-preserving"},
    ],
)
def test_local_poc_source_rejects_a_generalized_transport_or_trust_bypass(overrides: dict[str, object]) -> None:
    local_profile: dict[str, object] = {
        "source_mode": "local-poc",
        "registry_origin": "http://localhost:8443",
        "credential_ref": "development/local-poc-anonymous",
        "trust_policy_ref": "development/local-poc-unsigned",
        "mirror_policy": "direct",
    }
    local_profile.update(overrides)

    with pytest.raises(ValidationError):
        PluginSource.model_validate(_source_payload(**local_profile))


def test_accepted_reconciliation_records_a_complete_immutable_high_water_mark() -> None:
    reconciliation = PluginSourceReconciliation.model_validate(_accepted_reconciliation_payload())

    assert reconciliation.catalog_generation == 42
    assert reconciliation.catalog_index_digest == _DIGEST_A
    assert reconciliation.failure_code is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"catalog_generation": None},
        {"catalog_index_digest": None},
        {"issued_at": None},
        {"expires_at": None},
        {"failure_code": "UNTRUSTED_SIGNER"},
        {"expires_at": datetime(2026, 10, 6, 11, tzinfo=UTC)},
    ],
)
def test_accepted_reconciliation_rejects_incomplete_or_invalid_high_water_mark(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PluginSourceReconciliation.model_validate(_accepted_reconciliation_payload(**overrides))


@pytest.mark.parametrize(
    "payload",
    [
        _accepted_reconciliation_payload(state="rejected", failure_code="UNTRUSTED_SIGNER"),
        _accepted_reconciliation_payload(state="stale", failure_code="INDEX_EXPIRED"),
    ],
)
def test_nonaccepted_reconciliations_preserve_observed_index_evidence_and_a_safe_reason(
    payload: dict[str, object],
) -> None:
    reconciliation = PluginSourceReconciliation.model_validate(payload)

    assert reconciliation.catalog_generation == 42
    assert reconciliation.catalog_index_digest == _DIGEST_A


@pytest.mark.parametrize(
    "payload",
    [
        {
            **_accepted_reconciliation_payload(state="rejected"),
            "failure_code": None,
        },
        {
            **_accepted_reconciliation_payload(state="rejected", failure_code="UNTRUSTED_SIGNER"),
            "catalog_generation": None,
        },
        {
            **_accepted_reconciliation_payload(state="rejected", failure_code="UNTRUSTED_SIGNER"),
            "catalog_index_digest": None,
        },
        {
            **_accepted_reconciliation_payload(state="rejected", failure_code="UNTRUSTED_SIGNER"),
            "issued_at": None,
        },
        {
            **_accepted_reconciliation_payload(state="rejected", failure_code="UNTRUSTED_SIGNER"),
            "expires_at": None,
        },
        {
            **_accepted_reconciliation_payload(state="accepted"),
            "failure_code": "UNTRUSTED_SIGNER",
        },
    ],
)
def test_reconciliation_outcomes_require_complete_observed_evidence_and_consistent_reason(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PluginSourceReconciliation.model_validate(payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"namespace": "Syntara"},
        {"version": "v1"},
        {"artifact_repository": "syntara/../github"},
        {"artifact_digest": "sha256:" + "A" * 64},
    ],
)
def test_catalog_version_rejects_noncanonical_identity_or_artifact_reference(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CatalogPluginVersion.model_validate(_catalog_version_payload(**overrides))


def test_catalog_asset_is_bounded_to_one_safe_digest_reference() -> None:
    asset = CatalogPluginAsset.model_validate(
        {
            "catalog_plugin_version_id": _VERSION_ID,
            "asset_kind": "documentation",
            "media_type": "text/markdown",
            "digest": _DIGEST_A,
            "size_bytes": 512,
            "rendered_html": "<h1>Documentation</h1>\n",
            "verification_summary": {"trust": {"signature": "verified"}},
        }
    )

    assert asset.digest == _DIGEST_A
    assert asset.rendered_html == "<h1>Documentation</h1>\n"
    with pytest.raises(ValidationError):
        CatalogPluginAsset.model_validate(
            {
                "catalog_plugin_version_id": _VERSION_ID,
                "asset_kind": "documentation",
                "media_type": "text/markdown",
                "digest": _DIGEST_A.upper(),
                "size_bytes": 512,
                "rendered_html": "<h1>Documentation</h1>\n",
            }
        )

    with pytest.raises(ValidationError):
        CatalogPluginAsset.model_validate(
            {
                "catalog_plugin_version_id": _VERSION_ID,
                "asset_kind": "documentation",
                "media_type": "text/plain",
                "digest": _DIGEST_A,
                "size_bytes": 512,
                "rendered_html": "<h1>Documentation</h1>\n",
            }
        )

    with pytest.raises(ValidationError):
        CatalogPluginAsset.model_validate(
            {
                "catalog_plugin_version_id": _VERSION_ID,
                "asset_kind": "documentation",
                "media_type": "text/markdown",
                "digest": _DIGEST_A,
                "size_bytes": 512,
            }
        )
