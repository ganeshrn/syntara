"""Database integration coverage for catalog reconciliation orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING

import httpx
import pytest
from sqlmodel import col, select
from syntara_plugin.contracts import load_bundle

from syntara.plugin_platform.exceptions import CatalogIndexTrustError, CatalogSourceConfigurationError
from syntara.plugin_platform.models.catalog import CatalogPluginVersion, PluginSource, PluginSourceReconciliation
from syntara.plugin_platform.services.catalog_index_verifier import (
    OCI_CATALOG_INDEX_CONFIG_MEDIA_TYPE,
    OCI_CATALOG_INDEX_CONTENT_MEDIA_TYPE,
    OCI_IMAGE_MANIFEST_MEDIA_TYPE,
)
from syntara.plugin_platform.services.catalog_reconciliation_orchestrator import (
    CatalogIndexTrustDecision,
    CatalogIndexTrustRequest,
    CatalogReconciliationOrchestrator,
    CatalogRegistryAuthorizationRequest,
)
from syntara.plugin_platform.services.oci_distribution_client import OciDistributionClient

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

_OBSERVED_AT = datetime(2026, 10, 7, 12, tzinfo=UTC)


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


def _source() -> PluginSource:
    return PluginSource.model_validate(
        {
            "source_id": "local-quay",
            "display_name": "Local Quay",
            "registry_origin": "https://quay.example.test",
            "index_repository": "syntara/catalog-index",
            "channel": "stable",
            "repository_scopes": ["syntara/plugins"],
            "credential_ref": "credentials/local-quay-pull",
            "trust_policy_ref": "trust/local-quay",
        }
    )


@dataclass(frozen=True)
class _CatalogArtifactWire:
    manifest: bytes
    manifest_digest: str
    blobs: dict[str, bytes]


def _artifact_wire() -> _CatalogArtifactWire:
    issued_at = _OBSERVED_AT - timedelta(hours=1)
    index = {
        "apiVersion": "syntara.io/v1alpha1",
        "kind": "CatalogIndex",
        "metadata": {
            "sourceId": "local-quay",
            "generation": 1,
            "issuedAt": issued_at.isoformat().replace("+00:00", "Z"),
            "expiresAt": (issued_at + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
        },
        "spec": {
            "contractVersion": "syntara.io/v1alpha1",
            "entries": [
                {
                    "namespace": "syntara",
                    "name": "github",
                    "version": "1.0.0",
                    "artifactRepository": "syntara/plugins/github",
                    "artifactDigest": "sha256:" + "a" * 64,
                    "documentationDigest": "sha256:" + "b" * 64,
                    "runtimeOwnership": "platform-runner",
                }
            ],
        },
    }
    index_bytes = _canonical_json(index)
    config = {
        "apiVersion": "syntara.catalog-index.config/v1alpha1",
        "kind": "CatalogIndexArtifactConfig",
        "catalogIndexDigest": _digest(index_bytes),
        "contractBundleDigest": load_bundle().digest,
        "entryCount": 1,
        "generation": 1,
        "sourceId": "local-quay",
    }
    config_bytes = _canonical_json(config)
    manifest = _canonical_json(
        {
            "schemaVersion": 2,
            "mediaType": OCI_IMAGE_MANIFEST_MEDIA_TYPE,
            "artifactType": "application/vnd.syntara.catalog-index.v1",
            "config": {
                "mediaType": OCI_CATALOG_INDEX_CONFIG_MEDIA_TYPE,
                "digest": _digest(config_bytes),
                "size": len(config_bytes),
            },
            "layers": [
                {
                    "mediaType": OCI_CATALOG_INDEX_CONTENT_MEDIA_TYPE,
                    "digest": _digest(index_bytes),
                    "size": len(index_bytes),
                }
            ],
        }
    )
    return _CatalogArtifactWire(
        manifest=manifest,
        manifest_digest=_digest(manifest),
        blobs={_digest(config_bytes): config_bytes, _digest(index_bytes): index_bytes},
    )


@dataclass
class _AuthorizationResolver:
    requests: list[CatalogRegistryAuthorizationRequest] = field(default_factory=list)

    async def resolve_authorization(self, request: CatalogRegistryAuthorizationRequest) -> str:
        self.requests.append(request)
        return "Bearer registry-token"


@dataclass
class _TrustEvaluator:
    requests: list[CatalogIndexTrustRequest] = field(default_factory=list)

    async def evaluate_catalog_index(self, request: CatalogIndexTrustRequest) -> CatalogIndexTrustDecision:
        self.requests.append(request)
        return CatalogIndexTrustDecision(summary={"issuer": "https://issuer.example.test", "signature": "verified"})


class _RejectingTrustEvaluator:
    async def evaluate_catalog_index(self, _request: CatalogIndexTrustRequest) -> CatalogIndexTrustDecision:
        code = "SIGNATURE_DENIED"
        raise CatalogIndexTrustError(code)


def _catalog_client(wire: _CatalogArtifactWire, paths: list[str]) -> OciDistributionClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["authorization"] == "Bearer registry-token"
        if request.url.path.endswith("/manifests/stable") or request.url.path.endswith(
            f"/manifests/{wire.manifest_digest}"
        ):
            return httpx.Response(
                200,
                content=wire.manifest,
                headers={
                    "content-type": OCI_IMAGE_MANIFEST_MEDIA_TYPE,
                    "docker-content-digest": wire.manifest_digest,
                },
            )
        digest = request.url.path.rsplit("/", maxsplit=1)[-1]
        return httpx.Response(200, content=wire.blobs[digest], headers={"content-length": str(len(wire.blobs[digest]))})

    return OciDistributionClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
async def test_orchestrator_admits_trusted_index_before_fetching_and_stages_projection(
    test_db_session: AsyncSession,
) -> None:
    source = _source()
    test_db_session.add(source)
    await test_db_session.flush()
    wire = _artifact_wire()
    paths: list[str] = []
    authorization = _AuthorizationResolver()
    trust = _TrustEvaluator()
    client = _catalog_client(wire, paths)
    try:
        result = await CatalogReconciliationOrchestrator(
            test_db_session,
            authorization_resolver=authorization,
            trust_evaluator=trust,
            catalog_client=client,
        ).reconcile_source(source.id, initiated_by=None, observed_at=_OBSERVED_AT)
    finally:
        await client.close()

    versions = list((await test_db_session.exec(select(CatalogPluginVersion))).all())
    reconciliation = (
        await test_db_session.exec(
            select(PluginSourceReconciliation).where(col(PluginSourceReconciliation.id) == result.reconciliation_id)
        )
    ).one()
    assert result.state == "accepted"
    assert authorization.requests[0].credential_ref == "credentials/local-quay-pull"
    assert trust.requests[0].index_digest == wire.manifest_digest
    assert paths == [
        "/v2/syntara/catalog-index/manifests/stable",
        f"/v2/syntara/catalog-index/manifests/{wire.manifest_digest}",
        f"/v2/syntara/catalog-index/blobs/{next(iter(wire.blobs))}",
        f"/v2/syntara/catalog-index/blobs/{next(reversed(wire.blobs))}",
    ]
    assert reconciliation.verification_summary["trust"] == {
        "issuer": "https://issuer.example.test",
        "signature": "verified",
    }
    assert [(version.namespace, version.name) for version in versions] == [("syntara", "github")]


@pytest.mark.asyncio
async def test_orchestrator_fails_closed_before_blob_fetch_when_trust_denies_index(
    test_db_session: AsyncSession,
) -> None:
    source = _source()
    test_db_session.add(source)
    await test_db_session.flush()
    wire = _artifact_wire()
    paths: list[str] = []
    client = _catalog_client(wire, paths)
    try:
        with pytest.raises(CatalogIndexTrustError, match="SIGNATURE_DENIED"):
            await CatalogReconciliationOrchestrator(
                test_db_session,
                authorization_resolver=_AuthorizationResolver(),
                trust_evaluator=_RejectingTrustEvaluator(),
                catalog_client=client,
            ).reconcile_source(source.id, initiated_by=None, observed_at=_OBSERVED_AT)
    finally:
        await client.close()

    reconciliations = list((await test_db_session.exec(select(PluginSourceReconciliation))).all())
    assert paths == [
        "/v2/syntara/catalog-index/manifests/stable",
        f"/v2/syntara/catalog-index/manifests/{wire.manifest_digest}",
    ]
    assert reconciliations == []


@pytest.mark.asyncio
async def test_orchestrator_refuses_to_stage_after_source_trust_configuration_changes(
    test_db_session: AsyncSession,
) -> None:
    source = _source()
    test_db_session.add(source)
    await test_db_session.flush()
    wire = _artifact_wire()
    client = _catalog_client(wire, [])

    @dataclass
    class _ChangingTrustEvaluator:
        async def evaluate_catalog_index(self, _request: CatalogIndexTrustRequest) -> CatalogIndexTrustDecision:
            source.trust_policy_ref = "trust/reconfigured"
            await test_db_session.flush()
            return CatalogIndexTrustDecision(summary={"signature": "verified"})

    try:
        with pytest.raises(CatalogSourceConfigurationError, match="changed during reconciliation"):
            await CatalogReconciliationOrchestrator(
                test_db_session,
                authorization_resolver=_AuthorizationResolver(),
                trust_evaluator=_ChangingTrustEvaluator(),
                catalog_client=client,
            ).reconcile_source(source.id, initiated_by=None, observed_at=_OBSERVED_AT)
    finally:
        await client.close()

    reconciliations = list((await test_db_session.exec(select(PluginSourceReconciliation))).all())
    assert reconciliations == []
