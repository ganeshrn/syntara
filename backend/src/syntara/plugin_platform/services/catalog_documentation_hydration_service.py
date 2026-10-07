"""Trust-gated hydration of one accepted catalog plugin's documentation.

Catalog browsing reads only local projections. This internal service performs
the separate background/admin work needed to make documentation available: it
retrieves a catalog-selected plugin artifact by exact digest, admits that
digest through the source trust policy before reading its blobs, verifies the
artifact, renders Markdown safely, and persists only the rendered result.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlmodel import col, select

from syntara.core.jsonb_limits import validate_jsonb_size
from syntara.plugin_platform.exceptions import (
    CatalogPluginNotFoundError,
    CatalogSourceConfigurationError,
    CatalogSourceDisabledError,
    CatalogSourceNotFoundError,
)
from syntara.plugin_platform.models.catalog import (
    CatalogPluginAsset,
    CatalogPluginVersion,
    PluginSource,
    PluginSourceReconciliation,
)
from syntara.plugin_platform.services.artifact_verifier import (
    PluginArtifactInput,
    PluginArtifactVerifier,
)
from syntara.plugin_platform.services.catalog_reconciliation_service import catalog_source_fingerprint
from syntara.plugin_platform.services.documentation_renderer import render_plugin_documentation
from syntara.plugin_platform.services.oci_distribution_client import (
    OciDistributionClient,
    PluginArtifactLocator,
)
from syntara.plugin_platform.services.plugin_artifact_trust import (
    PluginArtifactTrustEvaluator,
    PluginArtifactTrustRequest,
)
from syntara.plugin_platform.services.registry_access import (
    CatalogRegistryAuthorizationRequest,
    CatalogRegistryAuthorizationResolver,
    CatalogRegistryTLSContextResolver,
    SystemTrustCatalogRegistryTLSContextResolver,
    safe_trust_summary,
    validate_registry_authorization,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlmodel.ext.asyncio.session import AsyncSession

_JSON_FIELD_MAX_BYTES = 1_048_576


@dataclass(frozen=True)
class CatalogDocumentationHydrationResult:
    """The durable non-secret documentation state produced by one hydration."""

    catalog_plugin_version_id: UUID
    documentation_digest: str
    idempotent: bool


class CatalogDocumentationHydrationService:
    """Hydrate safe rendered documentation for an active accepted catalog version."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        authorization_resolver: CatalogRegistryAuthorizationResolver,
        trust_evaluator: PluginArtifactTrustEvaluator,
        registry_client: OciDistributionClient,
        artifact_verifier: PluginArtifactVerifier | None = None,
        tls_context_resolver: CatalogRegistryTLSContextResolver | None = None,
    ) -> None:
        """Bind explicit backend-owned source access and trust adapters."""
        self._session = session
        self._authorization_resolver = authorization_resolver
        self._trust_evaluator = trust_evaluator
        self._registry_client = registry_client
        self._artifact_verifier = artifact_verifier or PluginArtifactVerifier()
        self._tls_context_resolver = tls_context_resolver or SystemTrustCatalogRegistryTLSContextResolver()

    async def hydrate(self, catalog_plugin_version_id: UUID) -> CatalogDocumentationHydrationResult:
        """Persist sanitized documentation only after digest-bound trust and verification.

        The source/version snapshot is revalidated under lock after network I/O,
        so changing a registry, credential, policy, repository scope, or active
        catalog snapshot cannot publish content fetched under prior authority.
        """
        version, source = await self._active_version_and_source(catalog_plugin_version_id)
        source_fingerprint = catalog_source_fingerprint(source)
        _require_repository_scope(version.artifact_repository, source.repository_scopes)
        locator = PluginArtifactLocator(
            registry_origin=source.registry_origin,
            artifact_repository=version.artifact_repository,
        )
        access_request = CatalogRegistryAuthorizationRequest(
            source_id=source.source_id,
            registry_origin=source.registry_origin,
            repository=version.artifact_repository,
            credential_ref=source.credential_ref,
            custom_ca_ref=source.custom_ca_ref,
            source_mode=source.source_mode,
        )
        authorization = await self._authorization_resolver.resolve_authorization(access_request)
        tls_context = await self._tls_context_resolver.resolve_tls_context(access_request)
        validate_registry_authorization(authorization)
        resolved = await self._registry_client.fetch_plugin_artifact_manifest(
            locator,
            digest=version.artifact_digest,
            authorization_header=authorization,
            tls_context=tls_context,
        )
        manifest_plan = self._artifact_verifier.inspect_manifest(
            PluginArtifactInput(
                artifact_repository=version.artifact_repository,
                artifact_digest=version.artifact_digest,
                manifest_bytes=resolved.manifest_bytes,
                blobs={},
            )
        )
        trust_decision = await self._trust_evaluator.evaluate_plugin_artifact(
            PluginArtifactTrustRequest(
                source_id=source.source_id,
                registry_origin=source.registry_origin,
                artifact_repository=version.artifact_repository,
                artifact_digest=version.artifact_digest,
                namespace=version.namespace,
                name=version.name,
                version=version.version,
                trust_policy_ref=source.trust_policy_ref,
                mirror_policy=source.mirror_policy,
                source_mode=source.source_mode,
            )
        )
        trust_summary = safe_trust_summary(trust_decision.summary)
        blobs = {
            manifest_plan.config_descriptor.digest: await self._registry_client.fetch_blob(
                locator,
                digest=manifest_plan.config_descriptor.digest,
                maximum_bytes=manifest_plan.config_descriptor.size,
                authorization_header=authorization,
                tls_context=tls_context,
            ),
            manifest_plan.plugin_manifest_descriptor.digest: await self._registry_client.fetch_blob(
                locator,
                digest=manifest_plan.plugin_manifest_descriptor.digest,
                maximum_bytes=manifest_plan.plugin_manifest_descriptor.size,
                authorization_header=authorization,
                tls_context=tls_context,
            ),
            manifest_plan.content_bundle_descriptor.digest: await self._registry_client.fetch_blob(
                locator,
                digest=manifest_plan.content_bundle_descriptor.digest,
                maximum_bytes=manifest_plan.content_bundle_descriptor.size,
                authorization_header=authorization,
                tls_context=tls_context,
            ),
        }
        artifact = self._artifact_verifier.verify(
            PluginArtifactInput(
                artifact_repository=version.artifact_repository,
                artifact_digest=version.artifact_digest,
                manifest_bytes=resolved.manifest_bytes,
                blobs=blobs,
            )
        )
        documentation = artifact.documentation
        if documentation is None or documentation.digest != version.documentation_digest:
            msg = "Catalog artifact documentation does not match its accepted catalog entry"
            raise CatalogSourceConfigurationError(msg)
        _require_artifact_identity(artifact.canonical_descriptor, version)
        rendered_html = render_plugin_documentation(documentation.content)
        verification_summary = validate_jsonb_size(
            {
                "artifactDigest": version.artifact_digest,
                "artifactVerifier": {
                    "documentationDigest": documentation.digest,
                    "documentationPath": documentation.path,
                },
                "trust": trust_summary,
            },
            field_name="catalog documentation verification summary",
            max_bytes=_JSON_FIELD_MAX_BYTES,
        )
        try:
            return await self._persist_documentation(
                version,
                source_database_id=source.id,
                source_fingerprint=source_fingerprint,
                documentation_digest=documentation.digest,
                documentation_size=len(documentation.content.encode("utf-8")),
                rendered_html=rendered_html,
                verification_summary=verification_summary,
            )
        except Exception:
            await self._session.rollback()
            raise

    async def _active_version_and_source(
        self, catalog_plugin_version_id: UUID
    ) -> tuple[CatalogPluginVersion, PluginSource]:
        statement = (
            select(CatalogPluginVersion, PluginSource)
            .join(
                PluginSourceReconciliation,
                col(CatalogPluginVersion.reconciliation_id) == col(PluginSourceReconciliation.id),
            )
            .join(
                PluginSource,
                col(PluginSource.active_reconciliation_id) == col(PluginSourceReconciliation.id),
            )
            .where(
                col(CatalogPluginVersion.id) == catalog_plugin_version_id,
                col(PluginSource.enabled).is_(True),
                col(PluginSourceReconciliation.state) == "accepted",
            )
        )
        row = (await self._session.exec(statement)).one_or_none()
        if row is None:
            raise CatalogPluginNotFoundError
        version, source = row
        return version, source

    async def _persist_documentation(
        self,
        version: CatalogPluginVersion,
        *,
        source_database_id: UUID,
        source_fingerprint: str,
        documentation_digest: str,
        documentation_size: int,
        rendered_html: str,
        verification_summary: dict[str, Any],
    ) -> CatalogDocumentationHydrationResult:
        source_result = await self._session.exec(
            select(PluginSource)
            .where(col(PluginSource.id) == source_database_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        source = source_result.one_or_none()
        if source is None:
            raise CatalogSourceNotFoundError
        if not source.enabled:
            raise CatalogSourceDisabledError
        if (
            source.active_reconciliation_id != version.reconciliation_id
            or source_fingerprint != catalog_source_fingerprint(source)
        ):
            msg = "Catalog source changed during documentation hydration"
            raise CatalogSourceConfigurationError(msg)

        existing_result = await self._session.exec(
            select(CatalogPluginAsset)
            .where(
                col(CatalogPluginAsset.catalog_plugin_version_id) == version.id,
                col(CatalogPluginAsset.asset_kind) == "documentation",
            )
            .with_for_update()
        )
        existing = existing_result.one_or_none()
        if existing is not None:
            if (
                existing.digest == documentation_digest
                and existing.size_bytes == documentation_size
                and existing.media_type == "text/markdown"
                and existing.rendered_html == rendered_html
            ):
                await self._session.commit()
                return CatalogDocumentationHydrationResult(
                    catalog_plugin_version_id=version.id,
                    documentation_digest=documentation_digest,
                    idempotent=True,
                )
            msg = "Catalog documentation conflicts with its immutable catalog version"
            raise CatalogSourceConfigurationError(msg)

        self._session.add(
            CatalogPluginAsset.model_validate(
                {
                    "catalog_plugin_version_id": version.id,
                    "asset_kind": "documentation",
                    "media_type": "text/markdown",
                    "digest": documentation_digest,
                    "size_bytes": documentation_size,
                    "rendered_html": rendered_html,
                    "verification_summary": verification_summary,
                }
            )
        )
        await self._session.commit()
        return CatalogDocumentationHydrationResult(
            catalog_plugin_version_id=version.id,
            documentation_digest=documentation_digest,
            idempotent=False,
        )


def _require_repository_scope(repository: str, scopes: list[str]) -> None:
    if not any(repository == scope or repository.startswith(f"{scope}/") for scope in scopes):
        msg = "Catalog artifact repository is outside the configured source scope"
        raise CatalogSourceConfigurationError(msg)


def _require_artifact_identity(descriptor: Mapping[str, Any], version: CatalogPluginVersion) -> None:
    metadata = descriptor.get("metadata")
    if not isinstance(metadata, Mapping) or (
        metadata.get("namespace"),
        metadata.get("name"),
        metadata.get("version"),
    ) != (version.namespace, version.name, version.version):
        msg = "Catalog artifact identity does not match its accepted catalog entry"
        raise CatalogSourceConfigurationError(msg)
