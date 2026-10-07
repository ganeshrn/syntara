"""Contract tests for administrator installation of an active catalog plugin."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlmodel import select

from syntara.api.main import app
from syntara.plugin_platform.models.registry import InstalledPlugin, PluginRegistryState
from syntara.plugin_platform.services.catalog_installation_service import (
    CatalogPluginInstallationService,
    WorkloadImageAdmissionDecision,
    WorkloadImageAdmissionRequest,
)
from syntara.plugin_platform.services.plugin_artifact_trust import (
    PluginArtifactTrustDecision,
    PluginArtifactTrustRequest,
)
from tests.integration.plugin_platform.plugin_artifact_helpers import (
    PluginArtifactWire,
    plugin_artifact_wire,
    registry_client,
    seed_active_catalog,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlmodel.ext.asyncio.session import AsyncSession

    from syntara.core.models import User
    from syntara.plugin_platform.services.oci_distribution_client import OciDistributionClient
    from syntara.plugin_platform.services.registry_access import CatalogRegistryAuthorizationRequest


BASE_URL = "/api/v1/catalog/plugins/syntara/github/versions/1.0.0/install"


@dataclass
class _AuthorizationResolver:
    """Test-only backend registry credential adapter."""

    async def resolve_authorization(self, _request: CatalogRegistryAuthorizationRequest) -> str:
        return "Bearer registry-token"


@dataclass
class _ArtifactTrustEvaluator:
    """Test-only immutable artifact admission adapter."""

    async def evaluate_plugin_artifact(self, _request: PluginArtifactTrustRequest) -> PluginArtifactTrustDecision:
        return PluginArtifactTrustDecision(summary={"signature": "verified"})


@dataclass
class _WorkloadImageEvaluator:
    """Test-only immutable workload admission adapter."""

    async def evaluate_workload_image(self, _request: WorkloadImageAdmissionRequest) -> WorkloadImageAdmissionDecision:
        return WorkloadImageAdmissionDecision(summary={"imagePolicy": "admitted"})


async def _installer(
    session: AsyncSession,
) -> tuple[CatalogPluginInstallationService, PluginArtifactWire, OciDistributionClient, list[str]]:
    """Seed a selected catalog version and construct explicitly trusted test adapters."""
    wire = plugin_artifact_wire()
    await seed_active_catalog(session, wire)
    session.add(PluginRegistryState())
    await session.commit()
    paths: list[str] = []
    client = registry_client(wire, paths)
    return (
        CatalogPluginInstallationService(
            session,
            authorization_resolver=_AuthorizationResolver(),
            artifact_trust_evaluator=_ArtifactTrustEvaluator(),
            workload_image_evaluator=_WorkloadImageEvaluator(),
            registry_client=client,
        ),
        wire,
        client,
        paths,
    )


class TestCatalogPluginInstall:
    """The browser command selects catalog data; trusted adapters select everything else."""

    async def test_install_uses_selected_catalog_version_and_is_idempotent(
        self,
        auth_client: AsyncClient,
        test_db_session: AsyncSession,
        test_user: User,
    ) -> None:
        """A caller cannot submit a registry route, digest, or workload image to this command."""
        service, wire, client, paths = await _installer(test_db_session)
        factory_attribute = "plugin_catalog_installation_service_factory"
        missing = object()
        previous_factory = getattr(app.state, factory_attribute, missing)
        factory_sessions: list[AsyncSession] = []

        def factory(session: AsyncSession) -> CatalogPluginInstallationService:
            factory_sessions.append(session)
            return service

        setattr(app.state, factory_attribute, factory)
        try:
            installed = await auth_client.post(BASE_URL, params={"source_id": "local-quay"})
            repeated = await auth_client.post(BASE_URL, params={"source_id": "local-quay"})
        finally:
            if previous_factory is missing:
                delattr(app.state, factory_attribute)
            else:
                setattr(app.state, factory_attribute, previous_factory)
            await client.close()

        assert installed.status_code == 200
        payload = installed.json()
        assert payload["registry_generation"] == 1
        assert payload["idempotent"] is False

        assert repeated.status_code == 200
        assert repeated.json() == {**payload, "idempotent": True}

        plugin = (await test_db_session.exec(select(InstalledPlugin))).one()
        assert payload["catalog_plugin_version_id"]
        assert payload["plugin_installation_id"] == str(plugin.id)
        assert plugin.artifact_digest == wire.manifest_digest
        assert plugin.installed_by == test_user.id
        assert factory_sessions == [test_db_session, test_db_session]
        assert paths == [
            f"/v2/syntara/plugins/github/manifests/{wire.manifest_digest}",
            f"/v2/syntara/plugins/github/blobs/{next(iter(wire.blobs))}",
            f"/v2/syntara/plugins/github/blobs/{list(wire.blobs)[1]}",
            f"/v2/syntara/plugins/github/blobs/{list(wire.blobs)[2]}",
            f"/v2/syntara/plugins/github/manifests/{wire.manifest_digest}",
            f"/v2/syntara/plugins/github/blobs/{next(iter(wire.blobs))}",
            f"/v2/syntara/plugins/github/blobs/{list(wire.blobs)[1]}",
            f"/v2/syntara/plugins/github/blobs/{list(wire.blobs)[2]}",
        ]

    async def test_install_fails_closed_without_trusted_adapter_composition(self, auth_client: AsyncClient) -> None:
        """The API never substitutes an anonymous resolver or permissive policy evaluator."""
        response = await auth_client.post(BASE_URL, params={"source_id": "local-quay"})

        assert response.status_code == 503
        assert response.json()["code"] == "PLUGIN_INSTALLATION_UNAVAILABLE"
