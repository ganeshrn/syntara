"""Explicitly unsafe, loopback-only catalog reconciliation for local development.

This module is intentionally separate from the production composition path.
It is enabled only through ``APP_PLUGIN_PLATFORM_LOCAL_POC_ENABLED=true`` and
accepts only the exact local Quay profile validated in ``source_profile``.
It reconciles a catalog index for discovery, may hydrate verified plugin
documentation, and can install a platform-runner plugin only through an
already admitted server-owned runner profile. It cannot admit publisher
workload images or execute workloads.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError
from syntara.plugin_platform.services.catalog_documentation_hydration_service import (
    CatalogDocumentationHydrationResult,
    CatalogDocumentationHydrationService,
)
from syntara.plugin_platform.services.catalog_installation_service import (
    CatalogPluginInstallationService,
    WorkloadImageAdmissionDecision,
    WorkloadImageAdmissionRequest,
)
from syntara.plugin_platform.services.catalog_reconciliation_orchestrator import (
    CatalogIndexTrustDecision,
    CatalogIndexTrustRequest,
    CatalogReconciliationOrchestrator,
)
from syntara.plugin_platform.services.oci_distribution_client import OciDistributionClient
from syntara.plugin_platform.services.plugin_artifact_trust import (
    PluginArtifactTrustDecision,
    PluginArtifactTrustRequest,
)
from syntara.plugin_platform.services.runner_profile import DatabasePlatformRunnerProfileResolver
from syntara.plugin_platform.source_profile import (
    LOCAL_POC_REGISTRY_ORIGINS,
    validate_local_poc_catalog_trust,
    validate_local_poc_plugin_artifact_trust,
    validate_local_poc_registry_access,
)

_LOCAL_IMAGE_REFERENCE = re.compile(r"^([a-z0-9.-]+:\d{2,5})/[a-z0-9][a-z0-9._/-]*@(sha256:[a-f0-9]{64})$")

if TYPE_CHECKING:
    from uuid import UUID

    from sqlmodel.ext.asyncio.session import AsyncSession

    from syntara.plugin_platform.services.catalog_reconciliation_service import CatalogReconciliationResult
    from syntara.plugin_platform.services.registry_access import CatalogRegistryAuthorizationRequest


class LocalPocCatalogRegistryAuthorizationResolver:
    """Allow anonymous pull only for the fixed local development source profile."""

    async def resolve_authorization(self, request: CatalogRegistryAuthorizationRequest) -> str | None:
        """Return anonymous access after defending the adapter's narrow boundary."""
        try:
            validate_local_poc_registry_access(
                source_mode=request.source_mode,
                registry_origin=request.registry_origin,
                credential_ref=request.credential_ref,
                custom_ca_ref=request.custom_ca_ref,
            )
        except ValueError as exc:
            raise CatalogSourceConfigurationError(str(exc)) from exc
        return None


class LocalPocCatalogIndexTrustEvaluator:
    """Record the intentional unsigned exception for a local catalog index only."""

    async def evaluate_catalog_index(self, request: CatalogIndexTrustRequest) -> CatalogIndexTrustDecision:
        """Admit the local index only after the fixed profile boundary is rechecked."""
        try:
            validate_local_poc_catalog_trust(
                source_mode=request.source_mode,
                registry_origin=request.registry_origin,
                trust_policy_ref=request.trust_policy_ref,
                mirror_policy=request.mirror_policy,
            )
        except ValueError as exc:
            raise CatalogSourceConfigurationError(str(exc)) from exc
        return CatalogIndexTrustDecision(
            summary={
                "profile": "local-poc",
                "registryTransport": "loopback-http",
                "signatureVerification": "skipped",
                "unsafeDevelopmentMode": True,
            }
        )


class LocalPocPluginArtifactTrustEvaluator:
    """Admit a local metadata artifact under the bounded local-PoC exception.

    This makes the local unsigned exception observable in persisted evidence.
    The generic hydrator still verifies all artifact bytes, identity, and
    documentation digest before it renders anything.
    """

    async def evaluate_plugin_artifact(self, request: PluginArtifactTrustRequest) -> PluginArtifactTrustDecision:
        """Admit the fixed local profile after independently checking its boundary."""
        try:
            validate_local_poc_plugin_artifact_trust(
                source_mode=request.source_mode,
                registry_origin=request.registry_origin,
                trust_policy_ref=request.trust_policy_ref,
                mirror_policy=request.mirror_policy,
            )
        except ValueError as exc:
            raise CatalogSourceConfigurationError(str(exc)) from exc
        return PluginArtifactTrustDecision(
            summary={
                "profile": "local-poc",
                "operation": "catalog-metadata",
                "registryTransport": "loopback-http",
                "signatureVerification": "skipped",
                "unsafeDevelopmentMode": True,
            }
        )


class LocalPocWorkloadImageAdmissionEvaluator:
    """Admit only immutable images from the fixed local development registry."""

    async def evaluate_workload_image(self, request: WorkloadImageAdmissionRequest) -> WorkloadImageAdmissionDecision:
        """Allow the demo workload without turning local anonymous access into production admission."""
        if request.registry_origin not in LOCAL_POC_REGISTRY_ORIGINS:
            msg = "Local-PoC workload admission requires the configured local registry"
            raise CatalogSourceConfigurationError(msg)
        image_match = _LOCAL_IMAGE_REFERENCE.fullmatch(request.image_digest)
        if image_match is None:
            msg = "Local-PoC workload images must use an immutable sha256 digest"
            raise CatalogSourceConfigurationError(msg)
        image_origin = f"http://{image_match.group(1)}"
        if image_origin not in LOCAL_POC_REGISTRY_ORIGINS:
            msg = "Local-PoC workload image must come from the configured local registry"
            raise CatalogSourceConfigurationError(msg)
        if request.runtime_abi != "syntara.container/v1alpha1":
            msg = "Local-PoC workload ABI is not supported"
            raise CatalogSourceConfigurationError(msg)
        return WorkloadImageAdmissionDecision(
            summary={
                "profile": "local-poc",
                "operation": "workload-install",
                "registryTransport": "loopback-http",
                "imageDigestPinned": True,
                "signatureVerification": "skipped",
                "unsafeDevelopmentMode": True,
            }
        )


class LocalPocCatalogReconciliationService:
    """Compose only the local catalog-discovery adapters for one request session."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind reconciliation work to one control-plane transaction session."""
        self._session = session

    async def reconcile_source(
        self,
        source_id: UUID,
        *,
        initiated_by: UUID | None,
    ) -> CatalogReconciliationResult:
        """Fetch and stage a catalog index under the explicit local profile.

        The normal catalog verifier still validates OCI manifest/blob digests
        and index shape before persistence. Only signature policy is skipped,
        and only after the local profile checks above have admitted the source.
        """
        async with OciDistributionClient() as client:
            orchestrator = CatalogReconciliationOrchestrator(
                self._session,
                authorization_resolver=LocalPocCatalogRegistryAuthorizationResolver(),
                trust_evaluator=LocalPocCatalogIndexTrustEvaluator(),
                catalog_client=client,
            )
            return await orchestrator.reconcile_source(
                source_id,
                initiated_by=initiated_by,
                initiation_kind="manual",
            )


class LocalPocCatalogDocumentationHydrationService:
    """Compose only the local, documentation-only artifact hydration path."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind one control-plane transaction session to the local profile."""
        self._session = session

    async def hydrate(self, catalog_plugin_version_id: UUID) -> CatalogDocumentationHydrationResult:
        """Fetch, verify, sanitize, and persist documentation for one catalog version."""
        async with OciDistributionClient() as client:
            return await CatalogDocumentationHydrationService(
                self._session,
                authorization_resolver=LocalPocCatalogRegistryAuthorizationResolver(),
                trust_evaluator=LocalPocPluginArtifactTrustEvaluator(),
                registry_client=client,
            ).hydrate(catalog_plugin_version_id)


def build_local_poc_catalog_installation_service(
    session: AsyncSession,
    *,
    registry_client: OciDistributionClient,
) -> CatalogPluginInstallationService:
    """Compose the narrow local installer for platform-runner catalog entries.

    The caller owns the OCI client's lifetime.  Installation still reads only
    the catalog-selected immutable artifact digest, verifies all artifact
    bytes, and resolves a runner image from the database-backed profile
    registry.  The plugin artifact, browser, and workflow cannot select an
    image route.
    """
    return CatalogPluginInstallationService(
        session,
        authorization_resolver=LocalPocCatalogRegistryAuthorizationResolver(),
        artifact_trust_evaluator=LocalPocPluginArtifactTrustEvaluator(),
        workload_image_evaluator=LocalPocWorkloadImageAdmissionEvaluator(),
        registry_client=registry_client,
        runner_profile_resolver=DatabasePlatformRunnerProfileResolver(session),
    )
