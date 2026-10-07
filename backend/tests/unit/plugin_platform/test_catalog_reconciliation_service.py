"""Unit coverage for trusted catalog-index reconciliation decisions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from syntara.core.exceptions import SafeValueError
from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError
from syntara.plugin_platform.models.catalog import PluginSource, PluginSourceReconciliation
from syntara.plugin_platform.services.catalog_index_verifier import VerifiedCatalogIndexArtifact
from syntara.plugin_platform.services.catalog_reconciliation_service import (
    catalog_source_fingerprint,
    plan_catalog_reconciliation,
)

_OBSERVED_AT = datetime(2026, 10, 7, 12, tzinfo=UTC)


def _digest(character: str) -> str:
    return f"sha256:{character * 64}"


def _source(**overrides: object) -> PluginSource:
    payload: dict[str, object] = {
        "source_id": "local-quay",
        "display_name": "Local Quay",
        "registry_origin": "https://quay.local:8443",
        "index_repository": "syntara/catalog-index",
        "channel": "stable",
        "repository_scopes": ["syntara/plugins"],
        "credential_ref": "credentials/local-quay-pull",
        "trust_policy_ref": "trust/local-quay",
    }
    payload.update(overrides)
    return PluginSource.model_validate(payload)


def _entry(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "namespace": "syntara",
        "name": "github",
        "version": "1.0.0",
        "artifactRepository": "syntara/plugins/github",
        "artifactDigest": _digest("a"),
        "documentationDigest": _digest("b"),
        "runtimeOwnership": "platform-runner",
    }
    value.update(overrides)
    return value


def _artifact(
    *,
    generation: int = 1,
    digest_character: str = "c",
    previous_digest: str | None = None,
    source_id: str = "local-quay",
    index_repository: str = "syntara/catalog-index",
    expires_at: datetime | None = None,
    entries: list[dict[str, object]] | None = None,
) -> VerifiedCatalogIndexArtifact:
    issued_at = _OBSERVED_AT - timedelta(hours=1)
    expiry = expires_at or (_OBSERVED_AT + timedelta(days=7))
    metadata: dict[str, object] = {
        "sourceId": source_id,
        "generation": generation,
        "issuedAt": issued_at.isoformat().replace("+00:00", "Z"),
        "expiresAt": expiry.isoformat().replace("+00:00", "Z"),
    }
    if previous_digest is not None:
        metadata["previousIndexDigest"] = previous_digest
    return VerifiedCatalogIndexArtifact(
        index_repository=index_repository,
        index_digest=_digest(digest_character),
        catalog_index={
            "metadata": metadata,
            "spec": {"entries": entries or [_entry()]},
        },
        contract_bundle_digest=_digest("d"),
    )


def _active(source: PluginSource, *, generation: int = 1, digest_character: str = "c") -> PluginSourceReconciliation:
    issued_at = _OBSERVED_AT - timedelta(days=1)
    return PluginSourceReconciliation(
        plugin_source_id=source.id,
        state="accepted",
        catalog_generation=generation,
        catalog_index_digest=_digest(digest_character),
        issued_at=issued_at,
        expires_at=issued_at + timedelta(days=7),
        entry_count=1,
    )


def test_initial_index_in_source_scope_is_accepted() -> None:
    decision = plan_catalog_reconciliation(_source(), _artifact(), None, observed_at=_OBSERVED_AT)

    assert decision.state == "accepted"
    assert decision.failure_code is None
    assert decision.entry_count == 1
    assert decision.entries[0]["artifactRepository"] == "syntara/plugins/github"


def test_expired_index_is_recorded_as_stale_without_replacing_active_projection() -> None:
    source = _source()
    decision = plan_catalog_reconciliation(
        source,
        _artifact(expires_at=_OBSERVED_AT),
        _active(source),
        observed_at=_OBSERVED_AT,
    )

    assert decision.state == "stale"
    assert decision.failure_code == "CATALOG_INDEX_EXPIRED"
    assert decision.entries == ()


@pytest.mark.parametrize(
    ("artifact", "failure_code"),
    [
        (_artifact(source_id="other-source"), "CATALOG_SOURCE_MISMATCH"),
        (_artifact(index_repository="other/catalog-index"), "CATALOG_INDEX_REPOSITORY_MISMATCH"),
        (_artifact(entries=[_entry(artifactRepository="other/plugins/github")]), "CATALOG_ARTIFACT_OUT_OF_SCOPE"),
        (_artifact(previous_digest=_digest("a")), "CATALOG_PREDECESSOR_UNANCHORED"),
    ],
)
def test_initial_source_rejects_unbound_or_out_of_scope_catalogs(
    artifact: VerifiedCatalogIndexArtifact,
    failure_code: str,
) -> None:
    decision = plan_catalog_reconciliation(_source(), artifact, None, observed_at=_OBSERVED_AT)

    assert decision.state == "rejected"
    assert decision.failure_code == failure_code


@pytest.mark.parametrize(
    ("artifact", "active_generation", "failure_code"),
    [
        (_artifact(generation=1, digest_character="d"), 1, "CATALOG_GENERATION_CONFLICT"),
        (_artifact(generation=1, digest_character="d"), 2, "CATALOG_GENERATION_REPLAYED"),
        (
            _artifact(generation=2, digest_character="d", previous_digest=_digest("a")),
            1,
            "CATALOG_PREDECESSOR_MISMATCH",
        ),
    ],
)
def test_active_source_rejects_conflicting_replayed_or_unlinked_indexes(
    artifact: VerifiedCatalogIndexArtifact,
    active_generation: int,
    failure_code: str,
) -> None:
    source = _source()
    decision = plan_catalog_reconciliation(
        source,
        artifact,
        _active(source, generation=active_generation),
        observed_at=_OBSERVED_AT,
    )

    assert decision.state == "rejected"
    assert decision.failure_code == failure_code


def test_active_source_reuses_an_exact_unexpired_index_without_a_second_projection() -> None:
    source = _source()
    decision = plan_catalog_reconciliation(source, _artifact(), _active(source), observed_at=_OBSERVED_AT)

    assert decision.state == "accepted"
    assert decision.idempotent is True
    assert decision.entries == ()


def test_active_source_accepts_a_new_generation_linked_to_its_current_digest() -> None:
    source = _source()
    decision = plan_catalog_reconciliation(
        source,
        _artifact(generation=2, digest_character="d", previous_digest=_digest("c")),
        _active(source),
        observed_at=_OBSERVED_AT,
    )

    assert decision.state == "accepted"
    assert decision.idempotent is False
    assert decision.catalog_generation == 2
    assert len(decision.entries) == 1


def test_source_rejects_an_active_checkpoint_owned_by_another_source() -> None:
    source = _source()

    with pytest.raises(CatalogSourceConfigurationError, match="owned by another source"):
        plan_catalog_reconciliation(
            source,
            _artifact(),
            _active(_source(source_id="other-source")),
            observed_at=_OBSERVED_AT,
        )


def test_source_fingerprint_is_stable_for_semantically_equivalent_scope_order() -> None:
    first = _source(repository_scopes=["syntara/plugins", "acme/plugins"])
    reordered = _source(repository_scopes=["acme/plugins", "syntara/plugins"])

    assert catalog_source_fingerprint(first) == catalog_source_fingerprint(reordered)


def test_naive_observation_time_is_rejected_before_reconciliation() -> None:
    with pytest.raises(SafeValueError, match="Observed time must be timezone aware"):
        plan_catalog_reconciliation(
            _source(),
            _artifact(),
            None,
            observed_at=datetime.fromisoformat("2026-10-07T12:00:00"),
        )
