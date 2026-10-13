"""Read only, source-qualified access to accepted catalog projections.

The service reads only rows whose reconciliation is still the active snapshot
of an enabled source. It does not contact a registry, resolve credentials,
fetch documentation, install a plugin, or expose canonical publisher markup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel import col, select

from syntara.core.services import BaseService
from syntara.core.services.extensions import ConvertResourceMixin
from syntara.plugin_platform.exceptions import CatalogPluginIdentityAmbiguousError, CatalogPluginNotFoundError
from syntara.plugin_platform.models.catalog import (
    CatalogPluginAsset,
    CatalogPluginVersion,
    PluginSource,
    PluginSourceReconciliation,
)
from syntara.plugin_platform.schemas import (
    CatalogPluginDetailRead,
    CatalogPluginDocumentationRead,
    CatalogPluginListResponse,
    CatalogPluginRead,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

    from sqlmodel.ext.asyncio.session import AsyncSession

    from syntara.core.models import User


class CatalogPluginConvertMixin(ConvertResourceMixin):
    """Convert a projected version using its already-resolved stable source identity."""

    def __init__(self, source_ids: dict[UUID, str]) -> None:
        """Keep only non-secret source labels indexed by reconciliation ID."""
        self._source_ids = source_ids

    def convert_resource(self, resource: object) -> CatalogPluginRead:
        """Return the safe explicit discovery projection for one catalog version."""
        if not isinstance(resource, CatalogPluginVersion):
            msg = "Catalog browse conversion requires a catalog plugin version"
            raise TypeError(msg)
        return CatalogPluginRead(
            id=resource.id,
            source_id=self._source_ids[resource.reconciliation_id],
            reconciliation_id=resource.reconciliation_id,
            namespace=resource.namespace,
            name=resource.name,
            version=resource.version,
            artifact_repository=resource.artifact_repository,
            artifact_digest=resource.artifact_digest,
            documentation_digest=resource.documentation_digest,
            runtime_ownerships=resource.runtime_ownerships,
            deprecated=resource.deprecated,
            withdrawn=resource.withdrawn,
            created_at=resource.created_at,
            updated_at=resource.updated_at,
        )


class CatalogBrowseService(BaseService):
    """Expose only accepted immutable catalog rows visible from enabled sources."""

    def __init__(self, session: AsyncSession, user: User) -> None:
        """Bind one authenticated reader to the database session."""
        super().__init__(session, user)

    async def list_plugins(
        self,
        *,
        source_id: str | None,
        limit: int,
        cursor: str | None,
        sort: str | None,
        query_params_items: Iterable[tuple[str, str]],
        include_total: bool,
    ) -> CatalogPluginListResponse:
        """Return a paginated list from active snapshots only.

        The stable source identity is handled before common filtering because
        it belongs to the source/reconciliation join rather than the immutable
        catalog-version row. All other filters, sorting, and cursor handling
        use the standard service implementation.
        """
        source_ids = await self._active_reconciliation_sources(source_id=source_id)
        active_version_ids = await self._active_version_ids(source_ids)
        self.convert_resource_mixin = CatalogPluginConvertMixin(source_ids)
        filtered_query_params = ((key, value) for key, value in query_params_items if key != "source_id")
        return await self.list_resources(
            model=CatalogPluginVersion,
            response_type=CatalogPluginListResponse,
            limit=limit,
            cursor=cursor,
            sort=sort or "name",
            query_params_items=filtered_query_params,
            include_total=include_total,
            id_restriction=active_version_ids,
        )

    async def get_plugin(self, namespace: str, name: str) -> CatalogPluginDetailRead:
        """Return all currently visible versions for one namespaced plugin."""
        rows = await self._plugin_rows(namespace=namespace, name=name)
        if not rows:
            raise CatalogPluginNotFoundError
        return CatalogPluginDetailRead(
            namespace=namespace,
            name=name,
            versions=[self._read_from_row(version, source_id) for version, source_id in rows],
        )

    async def get_plugin_version(
        self,
        namespace: str,
        name: str,
        version: str,
        *,
        source_id: str | None,
    ) -> CatalogPluginRead:
        """Return one deterministic active version, optionally filtered by source."""
        rows = await self._plugin_rows(
            namespace=namespace,
            name=name,
            version=version,
            source_id=source_id,
        )
        if not rows:
            raise CatalogPluginNotFoundError
        if len(rows) > 1:
            raise CatalogPluginIdentityAmbiguousError
        catalog_version, resolved_source_id = rows[0]
        return self._read_from_row(catalog_version, resolved_source_id)

    async def get_plugin_documentation(
        self,
        namespace: str,
        name: str,
        version: str,
        *,
        source_id: str | None,
    ) -> CatalogPluginDocumentationRead:
        """Return only persisted sanitized documentation for one visible version.

        The read path intentionally has neither registry nor credential access. A
        missing local documentation asset is indistinguishable from a missing
        visible plugin so callers cannot use it to infer background hydration
        state for a source they cannot inspect.
        """
        rows = await self._plugin_rows(
            namespace=namespace,
            name=name,
            version=version,
            source_id=source_id,
        )
        if not rows:
            raise CatalogPluginNotFoundError
        if len(rows) > 1:
            raise CatalogPluginIdentityAmbiguousError
        catalog_version, resolved_source_id = rows[0]
        statement = select(CatalogPluginAsset).where(
            col(CatalogPluginAsset.catalog_plugin_version_id) == catalog_version.id,
            col(CatalogPluginAsset.asset_kind) == "documentation",
            col(CatalogPluginAsset.media_type) == "text/markdown",
            col(CatalogPluginAsset.digest) == catalog_version.documentation_digest,
            col(CatalogPluginAsset.rendered_html).is_not(None),
        )
        documentation = (await self.session.exec(statement)).one_or_none()
        if documentation is None or documentation.rendered_html is None:
            raise CatalogPluginNotFoundError
        return CatalogPluginDocumentationRead(
            source_id=resolved_source_id,
            namespace=catalog_version.namespace,
            name=catalog_version.name,
            version=catalog_version.version,
            documentation_digest=documentation.digest,
            rendered_html=documentation.rendered_html,
            rendered_at=documentation.created_at,
        )

    async def _active_reconciliation_sources(self, *, source_id: str | None) -> dict[UUID, str]:
        """Map only enabled sources' active reconciliation IDs to safe source IDs."""
        statement = select(PluginSourceReconciliation.id, PluginSource.source_id).join(
            PluginSource,
            col(PluginSource.active_reconciliation_id) == col(PluginSourceReconciliation.id),
        )
        statement = statement.where(
            col(PluginSource.enabled).is_(True),
            col(PluginSourceReconciliation.state) == "accepted",
        )
        if source_id is not None:
            statement = statement.where(col(PluginSource.source_id) == source_id)
        rows = (await self.session.exec(statement)).all()
        return dict(rows)

    async def _active_version_ids(self, source_ids: dict[UUID, str]) -> list[UUID]:
        """Return version row IDs attached to the enabled active snapshots."""
        if not source_ids:
            return []
        statement = select(CatalogPluginVersion.id).where(col(CatalogPluginVersion.reconciliation_id).in_(source_ids))
        return list((await self.session.exec(statement)).all())

    async def _plugin_rows(
        self,
        *,
        namespace: str,
        name: str,
        version: str | None = None,
        source_id: str | None = None,
    ) -> list[tuple[CatalogPluginVersion, str]]:
        """Select active visible rows with their stable non-secret source identity."""
        statement = (
            select(CatalogPluginVersion, PluginSource.source_id)
            .join(
                PluginSourceReconciliation,
                col(CatalogPluginVersion.reconciliation_id) == col(PluginSourceReconciliation.id),
            )
            .join(
                PluginSource,
                col(PluginSource.active_reconciliation_id) == col(PluginSourceReconciliation.id),
            )
        )
        statement = statement.where(
            col(PluginSource.enabled).is_(True),
            col(PluginSourceReconciliation.state) == "accepted",
            col(CatalogPluginVersion.namespace) == namespace,
            col(CatalogPluginVersion.name) == name,
        )
        if version is not None:
            statement = statement.where(col(CatalogPluginVersion.version) == version)
        if source_id is not None:
            statement = statement.where(col(PluginSource.source_id) == source_id)
        statement = statement.order_by(
            col(CatalogPluginVersion.version).desc(),
            col(CatalogPluginVersion.id).asc(),
        )
        return list((await self.session.exec(statement)).all())

    @staticmethod
    def _read_from_row(resource: CatalogPluginVersion, source_id: str) -> CatalogPluginRead:
        """Build a response without leaking the stored canonical publisher entry."""
        return CatalogPluginRead(
            id=resource.id,
            source_id=source_id,
            reconciliation_id=resource.reconciliation_id,
            namespace=resource.namespace,
            name=resource.name,
            version=resource.version,
            artifact_repository=resource.artifact_repository,
            artifact_digest=resource.artifact_digest,
            documentation_digest=resource.documentation_digest,
            runtime_ownerships=resource.runtime_ownerships,
            deprecated=resource.deprecated,
            withdrawn=resource.withdrawn,
            created_at=resource.created_at,
            updated_at=resource.updated_at,
        )
