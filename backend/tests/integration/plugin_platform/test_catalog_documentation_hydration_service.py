"""Database integration coverage for trust-gated catalog documentation hydration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from sqlmodel import select

from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError, PluginArtifactTrustError
from syntara.plugin_platform.models.catalog import CatalogPluginAsset
from syntara.plugin_platform.services.catalog_documentation_hydration_service import (
    CatalogDocumentationHydrationService,
)
from syntara.plugin_platform.services.plugin_artifact_trust import (
    PluginArtifactTrustDecision,
    PluginArtifactTrustRequest,
)
from tests.integration.plugin_platform.plugin_artifact_helpers import (
    plugin_artifact_wire,
    registry_client,
    seed_active_catalog,
)

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

    from syntara.plugin_platform.services.registry_access import CatalogRegistryAuthorizationRequest


@dataclass
class _AuthorizationResolver:
    requests: list[CatalogRegistryAuthorizationRequest] = field(default_factory=list)

    async def resolve_authorization(self, request: CatalogRegistryAuthorizationRequest) -> str:
        self.requests.append(request)
        return "Bearer registry-token"


@dataclass
class _TrustEvaluator:
    paths: list[str]
    requests: list[PluginArtifactTrustRequest] = field(default_factory=list)

    async def evaluate_plugin_artifact(self, request: PluginArtifactTrustRequest) -> PluginArtifactTrustDecision:
        self.requests.append(request)
        assert self.paths == [f"/v2/{request.artifact_repository}/manifests/{request.artifact_digest}"]
        return PluginArtifactTrustDecision(summary={"issuer": "https://issuer.example.test", "signature": "verified"})


class _RejectingTrustEvaluator:
    async def evaluate_plugin_artifact(self, _request: PluginArtifactTrustRequest) -> PluginArtifactTrustDecision:
        code = "SIGNATURE_DENIED"
        raise PluginArtifactTrustError(code)


@pytest.mark.asyncio
async def test_hydration_admits_the_artifact_before_fetching_blobs_and_persists_safe_html(
    test_db_session: AsyncSession,
) -> None:
    wire = plugin_artifact_wire()
    _source, version = await seed_active_catalog(test_db_session, wire)
    paths: list[str] = []
    authorization = _AuthorizationResolver()
    trust = _TrustEvaluator(paths)
    client = registry_client(wire, paths)
    try:
        result = await CatalogDocumentationHydrationService(
            test_db_session,
            authorization_resolver=authorization,
            trust_evaluator=trust,
            registry_client=client,
        ).hydrate(version.id)
    finally:
        await client.close()

    asset = (await test_db_session.exec(select(CatalogPluginAsset))).one()
    assert result.idempotent is False
    assert authorization.requests[0].repository == "syntara/plugins/github"
    assert trust.requests[0].artifact_digest == wire.manifest_digest
    assert paths == [
        f"/v2/syntara/plugins/github/manifests/{wire.manifest_digest}",
        f"/v2/syntara/plugins/github/blobs/{next(iter(wire.blobs))}",
        f"/v2/syntara/plugins/github/blobs/{list(wire.blobs)[1]}",
        f"/v2/syntara/plugins/github/blobs/{list(wire.blobs)[2]}",
    ]
    assert asset.digest == wire.documentation_digest
    assert "<script>" not in (asset.rendered_html or "")
    assert '<a href="/unsafe"' not in (asset.rendered_html or "")
    assert asset.verification_summary["trust"] == {
        "issuer": "https://issuer.example.test",
        "signature": "verified",
    }


@pytest.mark.asyncio
async def test_hydration_denies_before_blob_fetch_or_persistence(test_db_session: AsyncSession) -> None:
    wire = plugin_artifact_wire()
    _source, version = await seed_active_catalog(test_db_session, wire)
    paths: list[str] = []
    client = registry_client(wire, paths)
    try:
        with pytest.raises(PluginArtifactTrustError, match="SIGNATURE_DENIED"):
            await CatalogDocumentationHydrationService(
                test_db_session,
                authorization_resolver=_AuthorizationResolver(),
                trust_evaluator=_RejectingTrustEvaluator(),
                registry_client=client,
            ).hydrate(version.id)
    finally:
        await client.close()

    assert paths == [f"/v2/syntara/plugins/github/manifests/{wire.manifest_digest}"]
    assert list((await test_db_session.exec(select(CatalogPluginAsset))).all()) == []


@pytest.mark.asyncio
async def test_hydration_does_not_persist_when_source_policy_changes_during_fetch(
    test_db_session: AsyncSession,
) -> None:
    wire = plugin_artifact_wire()
    source, version = await seed_active_catalog(test_db_session, wire)
    client = registry_client(wire, [])

    @dataclass
    class _ChangingTrustEvaluator:
        async def evaluate_plugin_artifact(self, _request: PluginArtifactTrustRequest) -> PluginArtifactTrustDecision:
            source.trust_policy_ref = "trust/reconfigured"
            await test_db_session.flush()
            return PluginArtifactTrustDecision(summary={"signature": "verified"})

    try:
        with pytest.raises(CatalogSourceConfigurationError, match="changed during documentation hydration"):
            await CatalogDocumentationHydrationService(
                test_db_session,
                authorization_resolver=_AuthorizationResolver(),
                trust_evaluator=_ChangingTrustEvaluator(),
                registry_client=client,
            ).hydrate(version.id)
    finally:
        await client.close()

    assert list((await test_db_session.exec(select(CatalogPluginAsset))).all()) == []
