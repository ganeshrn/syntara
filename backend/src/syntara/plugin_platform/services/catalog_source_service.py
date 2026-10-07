"""Administrator lifecycle operations for non-secret catalog-source configuration."""

from collections.abc import Iterable
from typing import Any
from uuid import UUID

import structlog
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from syntara.core.models import User
from syntara.core.services import BaseService
from syntara.core.services.extensions import ConvertResourceMixin
from syntara.plugin_platform.exceptions import (
    CatalogSourceConfigurationError,
    CatalogSourceIdentityConflictError,
    CatalogSourceNotFoundError,
)
from syntara.plugin_platform.models.catalog import PluginSource
from syntara.plugin_platform.schemas import (
    PluginSourceCreate,
    PluginSourceListResponse,
    PluginSourceRead,
    PluginSourceUpdate,
)

logger = structlog.stdlib.get_logger(__name__)

_SOURCE_CONFIGURATION_FIELDS = frozenset(
    {
        "source_mode",
        "registry_origin",
        "index_repository",
        "channel",
        "repository_scopes",
        "credential_ref",
        "trust_policy_ref",
        "custom_ca_ref",
        "mirror_policy",
    }
)
_SOURCE_MUTABLE_FIELDS = frozenset({"display_name", "reconciliation_interval_seconds", *_SOURCE_CONFIGURATION_FIELDS})
_SOURCE_CREATE_FIELDS = frozenset({"source_id", "enabled", *_SOURCE_MUTABLE_FIELDS})


class PluginSourceConvertMixin(ConvertResourceMixin):
    """Convert persisted source configuration to the admin read shape."""

    def convert_resource(self, resource: PluginSource) -> PluginSourceRead:  # type: ignore[override]
        """Return only opaque references, never resolved secret material."""
        return PluginSourceRead.model_validate(resource)


class CatalogSourceService(BaseService):
    """Manage catalog sources without resolving registry credentials or running workloads."""

    def __init__(self, session: AsyncSession, user: User, *, local_poc_enabled: bool = False) -> None:
        """Bind the administrator's request session to this service."""
        super().__init__(session, user, convert_resource_mixin=PluginSourceConvertMixin())
        self._local_poc_enabled = local_poc_enabled

    async def create_source(self, request: PluginSourceCreate) -> PluginSource:
        """Persist a syntactically valid source with opaque security references."""
        try:
            source = PluginSource.model_validate(request.model_dump())
        except ValidationError as exc:
            raise CatalogSourceConfigurationError from exc
        self._require_enabled_source_profile(source)

        self.session.add(source)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise CatalogSourceIdentityConflictError(request.source_id) from exc
        await self.session.refresh(source)
        logger.info("catalog_source_created", plugin_source_id=str(source.id), source_id=source.source_id)
        return source

    async def get_source(self, source_id: UUID) -> PluginSource:
        """Return one source by its Syntara resource ID."""
        result = await self.session.exec(select(PluginSource).where(col(PluginSource.id) == source_id))
        source = result.one_or_none()
        if source is None:
            raise CatalogSourceNotFoundError
        return source

    async def list_sources(
        self,
        *,
        limit: int,
        cursor: str | None,
        sort: str | None,
        query_params_items: Iterable[tuple[str, str]],
        include_total: bool,
    ) -> PluginSourceListResponse:
        """List source configuration using the common cursor/filter contract."""
        return await self.list_resources(
            model=PluginSource,
            response_type=PluginSourceListResponse,
            limit=limit,
            cursor=cursor,
            sort=sort,
            query_params_items=query_params_items,
            include_total=include_total,
        )

    async def update_source(self, source_id: UUID, request: PluginSourceUpdate) -> PluginSource:
        """Update source settings and hide stale catalog content when routing changes."""
        source = await self.get_source(source_id)
        changes = request.model_dump(exclude_unset=True)
        if not changes:
            return source
        candidate = self._validated_candidate(source, changes)
        self._require_enabled_source_profile(candidate)
        if _SOURCE_CONFIGURATION_FIELDS.intersection(changes):
            # Existing catalog rows remain immutable historical evidence, but the
            # old active projection must not look current under new routing/trust.
            source.active_reconciliation_id = None
        for field_name in _SOURCE_MUTABLE_FIELDS.intersection(changes):
            setattr(source, field_name, getattr(candidate, field_name))
        self.session.add(source)
        await self.session.commit()
        await self.session.refresh(source)
        logger.info("catalog_source_updated", plugin_source_id=str(source.id), source_id=source.source_id)
        return source

    async def set_enabled(self, source_id: UUID, *, enabled: bool) -> PluginSource:
        """Enable or disable future discovery without deleting historical evidence."""
        source = await self.get_source(source_id)
        source.enabled = enabled
        self.session.add(source)
        await self.session.commit()
        await self.session.refresh(source)
        logger.info(
            "catalog_source_enablement_updated",
            plugin_source_id=str(source.id),
            source_id=source.source_id,
            enabled=enabled,
        )
        return source

    async def delete_source(self, source_id: UUID) -> None:
        """Remove a source and its discovery-only projections, never installed plugins."""
        source = await self.get_source(source_id)
        source_identifier = source.source_id
        await self.session.delete(source)
        await self.session.commit()
        logger.info("catalog_source_deleted", plugin_source_id=str(source_id), source_id=source_identifier)

    @staticmethod
    def _validated_candidate(source: PluginSource, changes: dict[str, Any]) -> PluginSource:
        """Validate partial updates against the complete persisted source contract."""
        values = {field_name: getattr(source, field_name) for field_name in _SOURCE_CREATE_FIELDS}
        values.update(changes)
        try:
            return PluginSource.model_validate(values)
        except ValidationError as exc:
            raise CatalogSourceConfigurationError from exc

    def _require_enabled_source_profile(self, source: PluginSource) -> None:
        """Require an explicit deployment switch before persisting an unsafe source."""
        if source.source_mode == "local-poc" and not self._local_poc_enabled:
            msg = "Local-PoC catalog sources are disabled for this deployment"
            raise CatalogSourceConfigurationError(msg)
