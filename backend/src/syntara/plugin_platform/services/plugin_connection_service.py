"""Internal creation and resolution for non-secret plugin connection identities.

This service does not resolve endpoints, credentials, OAuth, or remote
resources. It establishes the durable project/type boundary those later
services must use rather than accepting a caller-provided plugin identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select

from syntara.core.constants import FieldLimits
from syntara.plugin_platform.exceptions import PluginConnectionError
from syntara.plugin_platform.models.plugin_connection import PluginConnection
from syntara.plugin_platform.models.registry import InstalledPluginIntegrationType

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession


_ENDPOINT_KIND_PATTERN = r"^[a-z][a-z0-9-]{0,62}$"
_INTEGRATION_TYPE_NOT_FOUND = "integration-type-not-found"
_ENDPOINT_KIND_NOT_DECLARED = "endpoint-kind-not-declared"
_CONNECTION_NAME_CONFLICT = "connection-name-conflict"
_CONNECTION_NOT_FOUND = "connection-not-found"
_CONNECTION_DISABLED = "connection-disabled"
_CONNECTION_TYPE_MISMATCH = "connection-type-mismatch"


class PluginConnectionCreate(BaseModel):
    """Non-secret data required to create one project connection identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=FieldLimits.NAME_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=FieldLimits.DESCRIPTION_MAX_LENGTH)
    project_id: UUID
    integration_type_revision_id: UUID
    endpoint_kind: str = Field(min_length=1, max_length=63, pattern=_ENDPOINT_KIND_PATTERN)


@dataclass(frozen=True)
class ResolvedPluginConnection:
    """Trusted non-secret connection data for a later workflow adapter."""

    connection_id: UUID
    project_id: UUID
    integration_type_revision_id: UUID
    endpoint_kind: str


class PluginConnectionService:
    """Persist and resolve plugin connection identities inside caller transactions."""

    def __init__(self, session: AsyncSession) -> None:
        """Use the caller-owned database transaction and no external clients."""
        self._session = session

    async def create(
        self,
        candidate: PluginConnectionCreate,
        *,
        actor_id: UUID,
    ) -> PluginConnection:
        """Validate one declared endpoint kind and stage a non-secret record."""
        integration_type = await self._integration_type_or_raise(candidate.integration_type_revision_id)
        if candidate.endpoint_kind not in integration_type.endpoint_kinds:
            raise PluginConnectionError(_ENDPOINT_KIND_NOT_DECLARED)
        existing = await self._connection_by_name(candidate.project_id, candidate.name)
        if existing is not None:
            raise PluginConnectionError(_CONNECTION_NAME_CONFLICT)
        connection = PluginConnection(
            name=candidate.name,
            description=candidate.description,
            project_id=candidate.project_id,
            integration_type_revision_id=integration_type.id,
            endpoint_kind=candidate.endpoint_kind,
            created_by=actor_id,
        )
        self._session.add(connection)
        await self._session.flush()
        return connection

    async def resolve_enabled(
        self,
        connection_id: UUID,
        *,
        project_id: UUID,
        integration_type_revision_id: UUID,
    ) -> ResolvedPluginConnection:
        """Resolve only an enabled project connection for the pinned type revision."""
        connection = await self._session.get(PluginConnection, connection_id)
        if connection is None or connection.project_id != project_id:
            raise PluginConnectionError(_CONNECTION_NOT_FOUND)
        if not connection.enabled:
            raise PluginConnectionError(_CONNECTION_DISABLED)
        if connection.integration_type_revision_id != integration_type_revision_id:
            raise PluginConnectionError(_CONNECTION_TYPE_MISMATCH)
        return ResolvedPluginConnection(
            connection_id=connection.id,
            project_id=connection.project_id,
            integration_type_revision_id=connection.integration_type_revision_id,
            endpoint_kind=connection.endpoint_kind,
        )

    async def _integration_type_or_raise(self, revision_id: UUID) -> InstalledPluginIntegrationType:
        """Load the immutable type rather than trusting a caller-supplied identifier."""
        integration_type = await self._session.get(InstalledPluginIntegrationType, revision_id)
        if integration_type is None:
            raise PluginConnectionError(_INTEGRATION_TYPE_NOT_FOUND)
        return integration_type

    async def _connection_by_name(self, project_id: UUID, name: str) -> PluginConnection | None:
        """Find the one project-scoped name before staging a new record."""
        result = await self._session.exec(
            select(PluginConnection).where(
                PluginConnection.project_id == project_id,
                PluginConnection.name == name,
            )
        )
        return result.one_or_none()
