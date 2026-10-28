"""Integration coverage for catalog-selected immutable plugin installation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

import pytest
from sqlmodel import select

from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError, PluginArtifactTrustError
from syntara.plugin_platform.models.registry import (
    InstalledPlugin,
    InstalledPluginAction,
    PluginRegistryState,
    RunnerProfile,
    RunnerProfileRevision,
)
from syntara.plugin_platform.services.catalog_installation_service import (
    CatalogPluginInstallationService,
    WorkloadImageAdmissionDecision,
    WorkloadImageAdmissionEvaluator,
    WorkloadImageAdmissionRequest,
)
from syntara.plugin_platform.services.plugin_artifact_trust import (
    PluginArtifactTrustDecision,
    PluginArtifactTrustEvaluator,
    PluginArtifactTrustRequest,
)
from syntara.plugin_platform.services.runner_profile import (
    PlatformRunnerProfile,
    PlatformRunnerProfileResolver,
)
from tests.integration.plugin_platform.plugin_artifact_helpers import (
    PluginArtifactWire,
    plugin_artifact_wire,
    registry_client,
    seed_active_catalog,
)

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

    from syntara.plugin_platform.models.catalog import CatalogPluginVersion
    from syntara.plugin_platform.services.oci_distribution_client import OciDistributionClient
    from syntara.plugin_platform.services.registry_access import CatalogRegistryAuthorizationRequest


@dataclass
class _AuthorizationResolver:
    requests: list[CatalogRegistryAuthorizationRequest] = field(default_factory=list)

    async def resolve_authorization(self, request: CatalogRegistryAuthorizationRequest) -> str:
        self.requests.append(request)
        return "Bearer registry-token"


@dataclass
class _ArtifactTrustEvaluator:
    paths: list[str]
    requests: list[PluginArtifactTrustRequest] = field(default_factory=list)

    async def evaluate_plugin_artifact(self, request: PluginArtifactTrustRequest) -> PluginArtifactTrustDecision:
        self.requests.append(request)
        assert self.paths == [f"/v2/{request.artifact_repository}/manifests/{request.artifact_digest}"]
        return PluginArtifactTrustDecision(summary={"issuer": "https://issuer.example.test", "signature": "verified"})


@dataclass
class _WorkloadImageEvaluator:
    requests: list[WorkloadImageAdmissionRequest] = field(default_factory=list)

    async def evaluate_workload_image(self, request: WorkloadImageAdmissionRequest) -> WorkloadImageAdmissionDecision:
        self.requests.append(request)
        return WorkloadImageAdmissionDecision(summary={"imagePolicy": "admitted", "scan": "passed"})


class _RejectingArtifactTrustEvaluator:
    async def evaluate_plugin_artifact(self, _request: PluginArtifactTrustRequest) -> PluginArtifactTrustDecision:
        code = "SIGNATURE_DENIED"
        raise PluginArtifactTrustError(code)


@dataclass
class _RunnerProfileResolver:
    requests: list[str] = field(default_factory=list)
    revision_id: UUID = field(default_factory=uuid4)

    async def resolve_platform_runner(self, driver: str) -> PlatformRunnerProfile:
        self.requests.append(driver)
        assert driver == "http.v1"
        return PlatformRunnerProfile(
            revision_id=self.revision_id,
            driver=driver,
            image_digest="quay.io/syntara/http-runner@sha256:" + "c" * 64,
        )


async def _install(
    session: AsyncSession,
    wire: PluginArtifactWire,
    *,
    artifact_trust_evaluator: PluginArtifactTrustEvaluator | None = None,
    workload_image_evaluator: WorkloadImageAdmissionEvaluator | None = None,
    runner_profile_resolver: PlatformRunnerProfileResolver | None = None,
    runtime_ownership: Literal["platform-runner", "custom-workload"] = "custom-workload",
) -> tuple[
    CatalogPluginInstallationService,
    CatalogPluginVersion,
    OciDistributionClient,
    list[str],
]:
    """Create an explicitly injected service and its catalog fixture."""
    _source, version = await seed_active_catalog(
        session,
        wire,
        runtime_ownership=runtime_ownership,
    )
    await _seed_registry_state(session)
    paths: list[str] = []
    artifact_trust = artifact_trust_evaluator or _ArtifactTrustEvaluator(paths)
    workload_admission = workload_image_evaluator or _WorkloadImageEvaluator()
    client = registry_client(wire, paths)
    service = CatalogPluginInstallationService(
        session,
        authorization_resolver=_AuthorizationResolver(),
        artifact_trust_evaluator=artifact_trust,
        workload_image_evaluator=workload_admission,
        registry_client=client,
        runner_profile_resolver=runner_profile_resolver,
    )
    return service, version, client, paths


async def _seed_registry_state(session: AsyncSession) -> None:
    """Mirror the migration's singleton row in the empty integration template."""
    session.add(PluginRegistryState())
    await session.commit()


async def _seed_runner_profile(session: AsyncSession, resolver: _RunnerProfileResolver) -> None:
    """Persist the profile revision referenced by the test-only resolver."""
    profile = RunnerProfile(driver="http.v1")
    session.add(profile)
    await session.flush()
    revision = RunnerProfileRevision(
        id=resolver.revision_id,
        profile_id=profile.id,
        revision_digest="sha256:" + "d" * 64,
        runtime_abi="syntara.container/v1alpha1",
        image_digest="quay.io/syntara/http-runner@sha256:" + "c" * 64,
    )
    session.add(revision)
    await session.flush()
    profile.active_revision_id = revision.id
    session.add(profile)
    await session.commit()


@pytest.mark.asyncio
async def test_catalog_install_admits_artifact_and_workload_before_persisting_active_action(
    test_db_session: AsyncSession,
) -> None:
    wire = plugin_artifact_wire()
    workload_admission = _WorkloadImageEvaluator()
    service, version, client, paths = await _install(
        test_db_session,
        wire,
        workload_image_evaluator=workload_admission,
    )
    try:
        result = await service.install(version.id, actor_id=None)
    finally:
        await client.close()

    plugin = (await test_db_session.exec(select(InstalledPlugin))).one()
    action = (await test_db_session.exec(select(InstalledPluginAction))).one()
    assert result.catalog_plugin_version_id == version.id
    assert result.idempotent is False
    assert result.registry_generation == 1
    assert plugin.artifact_digest == wire.manifest_digest
    assert action.image_digest == "quay.io/syntara/github@sha256:" + "b" * 64
    assert action.input_presentation == {
        "renderer": "rjsf.v1",
        "uiSchema": {"title": {"ui:autofocus": True}},
    }
    assert action.step_revision_id.startswith("syntara.github.create-issue:")
    assert len(workload_admission.requests) == 1
    assert workload_admission.requests[0].image_digest == action.image_digest
    assert paths == [
        f"/v2/syntara/plugins/github/manifests/{wire.manifest_digest}",
        f"/v2/syntara/plugins/github/blobs/{next(iter(wire.blobs))}",
        f"/v2/syntara/plugins/github/blobs/{list(wire.blobs)[1]}",
        f"/v2/syntara/plugins/github/blobs/{list(wire.blobs)[2]}",
    ]
    assert plugin.verification_summary["workloadImages"] == [
        {
            "imageDigest": action.image_digest,
            "admission": {"imagePolicy": "admitted", "scan": "passed"},
        }
    ]


@pytest.mark.asyncio
async def test_catalog_install_resolves_platform_runner_from_the_server_owned_profile(
    test_db_session: AsyncSession,
) -> None:
    wire = plugin_artifact_wire(runtime_ownership="platform-runner")
    runner_profiles = _RunnerProfileResolver()
    await _seed_runner_profile(test_db_session, runner_profiles)
    workload_admission = _WorkloadImageEvaluator()
    service, version, client, _paths = await _install(
        test_db_session,
        wire,
        runner_profile_resolver=runner_profiles,
        runtime_ownership="platform-runner",
        workload_image_evaluator=workload_admission,
    )
    try:
        await service.install(version.id, actor_id=None)
    finally:
        await client.close()

    action = (await test_db_session.exec(select(InstalledPluginAction))).one()
    assert action.runtime_kind == "platform-runner"
    assert action.image_digest == "quay.io/syntara/http-runner@sha256:" + "c" * 64
    assert action.canonical_definition["runtime"] == {
        "abi": "syntara.container/v1alpha1",
        "driver": "http.v1",
        "image": "quay.io/syntara/http-runner@sha256:" + "c" * 64,
        "kind": "platform-runner",
        "operation": {
            "method": "POST",
            "path": "/repos/{input.owner}/{input.repository}/issues",
            "requestMap": {"title": {"from": "/title"}},
            "responseMap": {"id": {"from": "/id"}},
        },
        "profileRevisionId": str(runner_profiles.revision_id),
    }
    assert action.runner_profile_revision_id == runner_profiles.revision_id
    assert runner_profiles.requests == ["http.v1"]
    assert workload_admission.requests == []


@pytest.mark.asyncio
async def test_catalog_install_denies_before_blob_fetch_or_persistence(
    test_db_session: AsyncSession,
) -> None:
    wire = plugin_artifact_wire()
    service, version, client, paths = await _install(
        test_db_session,
        wire,
        artifact_trust_evaluator=_RejectingArtifactTrustEvaluator(),
    )
    try:
        with pytest.raises(PluginArtifactTrustError, match="SIGNATURE_DENIED"):
            await service.install(version.id, actor_id=None)
    finally:
        await client.close()

    assert paths == [f"/v2/syntara/plugins/github/manifests/{wire.manifest_digest}"]
    assert list((await test_db_session.exec(select(InstalledPlugin))).all()) == []


@pytest.mark.asyncio
async def test_catalog_install_refuses_to_persist_when_source_policy_changes_during_fetch(
    test_db_session: AsyncSession,
) -> None:
    wire = plugin_artifact_wire()
    source, version = await seed_active_catalog(test_db_session, wire)
    await _seed_registry_state(test_db_session)
    paths: list[str] = []

    @dataclass
    class _ChangingAdmissionEvaluator:
        async def evaluate_workload_image(
            self, _request: WorkloadImageAdmissionRequest
        ) -> WorkloadImageAdmissionDecision:
            source.trust_policy_ref = "trust/reconfigured"
            await test_db_session.flush()
            return WorkloadImageAdmissionDecision(summary={"imagePolicy": "admitted"})

    client = registry_client(wire, paths)
    try:
        with pytest.raises(CatalogSourceConfigurationError, match="changed during plugin installation"):
            await CatalogPluginInstallationService(
                test_db_session,
                authorization_resolver=_AuthorizationResolver(),
                artifact_trust_evaluator=_ArtifactTrustEvaluator(paths),
                workload_image_evaluator=_ChangingAdmissionEvaluator(),
                registry_client=client,
            ).install(version.id, actor_id=None)
    finally:
        await client.close()

    assert list((await test_db_session.exec(select(InstalledPlugin))).all()) == []
