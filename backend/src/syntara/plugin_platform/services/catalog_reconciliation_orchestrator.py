"""Reconcile one configured catalog source through explicit trusted boundaries.

The orchestrator composes existing narrow components without becoming a
credential store, generic registry browser, or trust-policy implementation.
Callers must provide backend-owned adapters for registry authorization and
signature/trust policy. There is intentionally no permissive default adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol

from sqlmodel import col, select

from syntara.plugin_platform.exceptions import (
    CatalogSourceDisabledError,
    CatalogSourceNotFoundError,
)
from syntara.plugin_platform.models.catalog import PluginSource
from syntara.plugin_platform.services.catalog_index_verifier import (
    CatalogIndexArtifactInput,
    CatalogIndexArtifactVerifier,
)
from syntara.plugin_platform.services.catalog_reconciliation_service import (
    CatalogReconciliationResult,
    CatalogReconciliationService,
    catalog_source_fingerprint,
)
from syntara.plugin_platform.services.oci_distribution_client import (
    CatalogIndexLocator,
    OciDistributionClient,
)
from syntara.plugin_platform.services.registry_access import (
    CatalogRegistryAuthorizationRequest,
    CatalogRegistryAuthorizationResolver,
    CatalogRegistryTLSContextResolver,
    SystemTrustCatalogRegistryTLSContextResolver,
    safe_trust_summary,
    validate_registry_authorization,
)
from syntara.plugin_platform.source_profile import STANDARD_CATALOG_SOURCE_MODE, CatalogSourceMode

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from sqlmodel.ext.asyncio.session import AsyncSession


@dataclass(frozen=True)
class CatalogIndexTrustRequest:
    """Non-secret immutable identity submitted to a source trust-policy adapter."""

    source_id: str
    registry_origin: str
    index_repository: str
    channel: str
    index_digest: str
    trust_policy_ref: str
    mirror_policy: Literal["direct", "digest-preserving"]
    source_mode: CatalogSourceMode = STANDARD_CATALOG_SOURCE_MODE


@dataclass(frozen=True)
class CatalogIndexTrustDecision:
    """An admitted trust decision with a small, non-secret inspection summary."""

    summary: Mapping[str, str | int | bool | None]


class CatalogIndexTrustEvaluator(Protocol):
    """Verify index signature/provenance against the source's configured policy."""

    async def evaluate_catalog_index(self, request: CatalogIndexTrustRequest) -> CatalogIndexTrustDecision:
        """Return only after the immutable index digest has been admitted."""


class CatalogReconciliationOrchestrator:
    """Fetch, trust, verify, and atomically stage one configured catalog index."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        authorization_resolver: CatalogRegistryAuthorizationResolver,
        trust_evaluator: CatalogIndexTrustEvaluator,
        catalog_client: OciDistributionClient,
        artifact_verifier: CatalogIndexArtifactVerifier | None = None,
        tls_context_resolver: CatalogRegistryTLSContextResolver | None = None,
    ) -> None:
        """Bind explicit backend-owned adapters for one reconciliation operation."""
        self._session = session
        self._authorization_resolver = authorization_resolver
        self._trust_evaluator = trust_evaluator
        self._catalog_client = catalog_client
        self._artifact_verifier = artifact_verifier or CatalogIndexArtifactVerifier()
        self._tls_context_resolver = tls_context_resolver or SystemTrustCatalogRegistryTLSContextResolver()

    async def reconcile_source(
        self,
        source_id: UUID,
        *,
        initiated_by: UUID | None,
        initiation_kind: Literal["manual", "scheduled", "test"] = "manual",
        observed_at: datetime | None = None,
    ) -> CatalogReconciliationResult:
        """Stage a current source index only after explicit access and trust admission.

        The source configuration is snapshotted before network access and
        rechecked by the staging service under a database lock. This prevents a
        response acquired under a superseded registry or trust-policy setting
        from being activated after an administrative update.
        """
        source = await self._source_for_read(source_id)
        if not source.enabled:
            raise CatalogSourceDisabledError
        source_fingerprint = catalog_source_fingerprint(source)
        locator = CatalogIndexLocator(
            registry_origin=source.registry_origin,
            index_repository=source.index_repository,
            channel=source.channel,
        )
        access_request = CatalogRegistryAuthorizationRequest(
            source_id=source.source_id,
            registry_origin=source.registry_origin,
            repository=source.index_repository,
            credential_ref=source.credential_ref,
            custom_ca_ref=source.custom_ca_ref,
            source_mode=source.source_mode,
        )
        authorization = await self._authorization_resolver.resolve_authorization(access_request)
        tls_context = await self._tls_context_resolver.resolve_tls_context(access_request)
        validate_registry_authorization(authorization)
        resolved = await self._catalog_client.resolve_catalog_index(
            locator,
            authorization_header=authorization,
            tls_context=tls_context,
        )
        manifest_plan = self._artifact_verifier.inspect_manifest(
            CatalogIndexArtifactInput(
                index_repository=source.index_repository,
                index_digest=resolved.digest,
                expected_source_id=source.source_id,
                manifest_bytes=resolved.manifest_bytes,
                blobs={},
            )
        )
        trust_decision = await self._trust_evaluator.evaluate_catalog_index(
            CatalogIndexTrustRequest(
                source_id=source.source_id,
                registry_origin=source.registry_origin,
                index_repository=source.index_repository,
                channel=source.channel,
                index_digest=resolved.digest,
                trust_policy_ref=source.trust_policy_ref,
                mirror_policy=source.mirror_policy,
                source_mode=source.source_mode,
            )
        )
        trust_summary = safe_trust_summary(trust_decision.summary)
        blobs = {
            manifest_plan.config_descriptor.digest: await self._catalog_client.fetch_blob(
                locator,
                digest=manifest_plan.config_descriptor.digest,
                maximum_bytes=manifest_plan.config_descriptor.size,
                authorization_header=authorization,
                tls_context=tls_context,
            ),
            manifest_plan.content_descriptor.digest: await self._catalog_client.fetch_blob(
                locator,
                digest=manifest_plan.content_descriptor.digest,
                maximum_bytes=manifest_plan.content_descriptor.size,
                authorization_header=authorization,
                tls_context=tls_context,
            ),
        }
        verified = self._artifact_verifier.verify(
            CatalogIndexArtifactInput(
                index_repository=source.index_repository,
                index_digest=resolved.digest,
                expected_source_id=source.source_id,
                manifest_bytes=resolved.manifest_bytes,
                blobs=blobs,
            )
        )
        return await CatalogReconciliationService(self._session).stage_trusted_index(
            source_id,
            verified,
            trust_summary=trust_summary,
            initiated_by=initiated_by,
            initiation_kind=initiation_kind,
            expected_source_fingerprint=source_fingerprint,
            observed_at=observed_at or datetime.now(UTC),
        )

    async def _source_for_read(self, source_id: UUID) -> PluginSource:
        result = await self._session.exec(select(PluginSource).where(col(PluginSource.id) == source_id))
        source = result.one_or_none()
        if source is None:
            raise CatalogSourceNotFoundError
        return source
