"""Database integration tests for immutable catalog-projection staging."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlmodel import col, select

from syntara.plugin_platform.models.catalog import CatalogPluginVersion, PluginSource, PluginSourceReconciliation
from syntara.plugin_platform.services.catalog_index_verifier import VerifiedCatalogIndexArtifact
from syntara.plugin_platform.services.catalog_reconciliation_service import CatalogReconciliationService

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

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


def _artifact(*, artifact_repository: str = "syntara/plugins/github") -> VerifiedCatalogIndexArtifact:
    issued_at = _OBSERVED_AT - timedelta(hours=1)
    return VerifiedCatalogIndexArtifact(
        index_repository="syntara/catalog-index",
        index_digest=_digest("c"),
        catalog_index={
            "metadata": {
                "sourceId": "local-quay",
                "generation": 1,
                "issuedAt": issued_at.isoformat().replace("+00:00", "Z"),
                "expiresAt": (issued_at + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
            },
            "spec": {
                "entries": [
                    {
                        "namespace": "syntara",
                        "name": "github",
                        "version": "1.0.0",
                        "artifactRepository": artifact_repository,
                        "artifactDigest": _digest("a"),
                        "documentationDigest": _digest("b"),
                        "runtimeOwnerships": ["platform-runner"],
                    }
                ]
            },
        },
        contract_bundle_digest=_digest("d"),
    )


@pytest.mark.asyncio
async def test_staging_accepted_index_atomically_creates_active_immutable_projection(
    test_db_session: AsyncSession,
) -> None:
    source = _source()
    test_db_session.add(source)
    await test_db_session.flush()

    result = await CatalogReconciliationService(test_db_session).stage_trusted_index(
        source.id,
        _artifact(),
        trust_summary={"signature": "verified"},
        initiated_by=None,
        observed_at=_OBSERVED_AT,
    )

    persisted_source = (await test_db_session.exec(select(PluginSource).where(col(PluginSource.id) == source.id))).one()
    reconciliation = (
        await test_db_session.exec(
            select(PluginSourceReconciliation).where(col(PluginSourceReconciliation.id) == result.reconciliation_id)
        )
    ).one()
    versions = list(
        (
            await test_db_session.exec(
                select(CatalogPluginVersion).where(
                    col(CatalogPluginVersion.reconciliation_id) == result.reconciliation_id
                )
            )
        ).all()
    )

    assert result.state == "accepted"
    assert result.entry_count == 1
    assert persisted_source.active_reconciliation_id == result.reconciliation_id
    assert reconciliation.failure_code is None
    assert reconciliation.verification_summary == {
        "artifactVerifier": {"contractBundleDigest": _digest("d")},
        "trust": {"signature": "verified"},
    }
    assert [(version.namespace, version.name, version.artifact_digest) for version in versions] == [
        ("syntara", "github", _digest("a"))
    ]


@pytest.mark.asyncio
async def test_rejected_index_preserves_observation_without_publishing_projection(
    test_db_session: AsyncSession,
) -> None:
    source = _source()
    test_db_session.add(source)
    await test_db_session.flush()

    result = await CatalogReconciliationService(test_db_session).stage_trusted_index(
        source.id,
        _artifact(artifact_repository="outside/plugins/github"),
        trust_summary={"signature": "verified"},
        initiated_by=None,
        observed_at=_OBSERVED_AT,
    )

    persisted_source = (await test_db_session.exec(select(PluginSource).where(col(PluginSource.id) == source.id))).one()
    versions = list((await test_db_session.exec(select(CatalogPluginVersion))).all())

    assert result.state == "rejected"
    assert result.failure_code == "CATALOG_ARTIFACT_OUT_OF_SCOPE"
    assert result.entry_count == 1
    assert persisted_source.active_reconciliation_id is None
    assert versions == []


@pytest.mark.asyncio
async def test_exact_retry_reuses_active_projection_without_another_reconciliation_record(
    test_db_session: AsyncSession,
) -> None:
    source = _source()
    test_db_session.add(source)
    await test_db_session.flush()
    service = CatalogReconciliationService(test_db_session)

    first = await service.stage_trusted_index(
        source.id,
        _artifact(),
        trust_summary={"signature": "verified"},
        initiated_by=None,
        observed_at=_OBSERVED_AT,
    )
    retry = await service.stage_trusted_index(
        source.id,
        _artifact(),
        trust_summary={"signature": "verified"},
        initiated_by=None,
        observed_at=_OBSERVED_AT,
    )
    reconciliations = list((await test_db_session.exec(select(PluginSourceReconciliation))).all())

    assert retry.reconciliation_id == first.reconciliation_id
    assert retry.idempotent is True
    assert len(reconciliations) == 1
