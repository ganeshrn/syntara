"""Internal creation and resolution for non-secret plugin connection identities.

This service does not resolve endpoints, credentials, OAuth, or remote
resources. It establishes the durable project/type boundary those later
services must use rather than accepting a caller-provided plugin identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from syntara.core.constants import FieldLimits
from syntara.plugin_platform.exceptions import PluginConnectionError
from syntara.plugin_platform.models.plugin_connection import PluginConnection
from syntara.plugin_platform.models.registry import InstalledPluginCredentialType, InstalledPluginIntegrationType
from syntara.plugin_platform.models.workflow_action_binding import WorkflowActionBinding

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

    from syntara.core.services.secret_service import SecretService


_ENDPOINT_KIND_PATTERN = r"^[a-z][a-z0-9-]{0,62}$"
_INTEGRATION_TYPE_NOT_FOUND = "integration-type-not-found"
_ENDPOINT_KIND_NOT_DECLARED = "endpoint-kind-not-declared"
_CONNECTION_NAME_CONFLICT = "connection-name-conflict"
_CONNECTION_NOT_FOUND = "connection-not-found"
_CONNECTION_DISABLED = "connection-disabled"
_CONNECTION_TYPE_MISMATCH = "connection-type-mismatch"
_CREDENTIAL_TYPE_NOT_FOUND = "credential-type-not-found"
_CREDENTIAL_TYPE_MISMATCH = "credential-type-mismatch"
_CONNECTION_CONFIGURATION_INVALID = "connection-configuration-invalid"
_CONNECTION_CREDENTIAL_INVALID = "connection-credential-invalid"
_CONNECTION_IN_USE = "connection-in-use"


class PluginConnectionCreate(BaseModel):
    """Non-secret data required to create one project connection identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=FieldLimits.NAME_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=FieldLimits.DESCRIPTION_MAX_LENGTH)
    project_id: UUID
    integration_type_revision_id: UUID
    endpoint_kind: str = Field(min_length=1, max_length=63, pattern=_ENDPOINT_KIND_PATTERN)


class PluginConnectionProvision(PluginConnectionCreate):
    """One complete connection setup request, including secret material for encryption.

    ``credential_inputs`` is request-only. It is passed directly to
    ``SecretService`` after validation and never written to the connection row
    or returned in a response.
    """

    configuration: dict[str, Any] = Field(default_factory=dict)
    credential_type_revision_id: UUID
    credential_inputs: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedPluginConnection:
    """Trusted non-secret connection data for a later workflow adapter."""

    connection_id: UUID
    project_id: UUID
    integration_type_revision_id: UUID
    endpoint_kind: str


class PluginConnectionService:
    """Persist and resolve plugin connection identities inside caller transactions."""

    def __init__(self, session: AsyncSession, secret_service: SecretService | None = None) -> None:
        """Use the caller-owned database transaction and no external clients."""
        self._session = session
        self._secret_service = secret_service

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

    async def provision(
        self,
        candidate: PluginConnectionProvision,
        *,
        actor_id: UUID,
    ) -> PluginConnection:
        """Create one complete typed connection and encrypt its credential values.

        This validates only immutable, already-installed plugin contract rows.
        Browser input never supplies a plugin identity, a schema, an image, or
        an endpoint policy. The secret is stored by Syntara's credential
        service and only its opaque ``secret_id`` is persisted.
        """
        if self._secret_service is None:
            raise PluginConnectionError(_CONNECTION_CREDENTIAL_INVALID)
        integration_type = await self._integration_type_or_raise(candidate.integration_type_revision_id)
        credential_type = await self._credential_type_or_raise(candidate.credential_type_revision_id)
        if candidate.endpoint_kind not in integration_type.endpoint_kinds:
            raise PluginConnectionError(_ENDPOINT_KIND_NOT_DECLARED)
        if credential_type.integration_type_revision_id != integration_type.id:
            raise PluginConnectionError(_CREDENTIAL_TYPE_MISMATCH)
        if credential_type.type_id not in integration_type.credential_type_ids:
            raise PluginConnectionError(_CREDENTIAL_TYPE_MISMATCH)
        if await self._connection_by_name(candidate.project_id, candidate.name) is not None:
            raise PluginConnectionError(_CONNECTION_NAME_CONFLICT)

        _validate_document(
            integration_type.configuration_schema,
            candidate.configuration,
            _CONNECTION_CONFIGURATION_INVALID,
        )
        configured_endpoint_kind = candidate.configuration.get("endpointKind")
        if configured_endpoint_kind != candidate.endpoint_kind:
            raise PluginConnectionError(_CONNECTION_CONFIGURATION_INVALID)
        _validate_document(
            credential_type.credential_schema,
            candidate.credential_inputs,
            _CONNECTION_CREDENTIAL_INVALID,
        )

        secret_id = await self._secret_service.create_secret(candidate.credential_inputs)
        connection = PluginConnection(
            name=candidate.name,
            description=candidate.description,
            project_id=candidate.project_id,
            integration_type_revision_id=integration_type.id,
            credential_type_revision_id=credential_type.id,
            endpoint_kind=candidate.endpoint_kind,
            configuration=candidate.configuration,
            secret_id=secret_id,
            created_by=actor_id,
        )
        self._session.add(connection)
        await self._session.flush()
        return connection

    async def list_for_project(
        self,
        *,
        project_id: UUID,
        integration_type_revision_id: UUID | None = None,
        enabled: bool | None = True,
    ) -> list[PluginConnection]:
        """List non-secret connection metadata for one selected workflow project."""
        statement = select(PluginConnection).where(PluginConnection.project_id == project_id)
        if integration_type_revision_id is not None:
            statement = statement.where(PluginConnection.integration_type_revision_id == integration_type_revision_id)
        if enabled is not None:
            statement = statement.where(PluginConnection.enabled == enabled)
        result = await self._session.exec(statement.order_by(PluginConnection.name))
        return list(result.all())

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

    async def set_enabled(self, connection_id: UUID, *, enabled: bool, actor_id: UUID) -> PluginConnection:
        """Change whether future workflow publication may select this connection."""
        connection = await self._connection_or_raise(connection_id)
        if connection.enabled == enabled:
            return connection
        connection.enabled = enabled
        connection.update_by_user(actor_id)
        self._session.add(connection)
        await self._session.commit()
        await self._session.refresh(connection)
        return connection

    async def delete(self, connection_id: UUID) -> None:
        """Delete an unused connection and its encrypted credential material atomically."""
        connection = await self._connection_or_raise(connection_id)
        binding = await self._session.exec(
            select(WorkflowActionBinding.id).where(WorkflowActionBinding.plugin_connection_id == connection.id).limit(1)
        )
        if binding.first() is not None:
            raise PluginConnectionError(_CONNECTION_IN_USE)

        secret_id = connection.secret_id
        connection.secret_id = None
        self._session.add(connection)
        await self._session.flush()
        if secret_id is not None:
            if self._secret_service is None:
                raise PluginConnectionError(_CONNECTION_CREDENTIAL_INVALID)
            await self._secret_service.delete_secret(secret_id)
        await self._session.delete(connection)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            # A workflow revision may pin the connection after the preflight
            # query above. The FK remains the final authority and a rollback
            # restores both the connection and its encrypted secret record.
            await self._session.rollback()
            raise PluginConnectionError(_CONNECTION_IN_USE) from exc

    async def _connection_or_raise(self, connection_id: UUID) -> PluginConnection:
        connection = await self._session.get(PluginConnection, connection_id)
        if connection is None:
            raise PluginConnectionError(_CONNECTION_NOT_FOUND)
        return connection

    async def _integration_type_or_raise(self, revision_id: UUID) -> InstalledPluginIntegrationType:
        """Load the immutable type rather than trusting a caller-supplied identifier."""
        integration_type = await self._session.get(InstalledPluginIntegrationType, revision_id)
        if integration_type is None:
            raise PluginConnectionError(_INTEGRATION_TYPE_NOT_FOUND)
        return integration_type

    async def _credential_type_or_raise(self, revision_id: UUID) -> InstalledPluginCredentialType:
        """Load the immutable recipe selected by a connection setup request."""
        credential_type = await self._session.get(InstalledPluginCredentialType, revision_id)
        if credential_type is None:
            raise PluginConnectionError(_CREDENTIAL_TYPE_NOT_FOUND)
        return credential_type

    async def _connection_by_name(self, project_id: UUID, name: str) -> PluginConnection | None:
        """Find the one project-scoped name before staging a new record."""
        result = await self._session.exec(
            select(PluginConnection).where(
                PluginConnection.project_id == project_id,
                PluginConnection.name == name,
            )
        )
        return result.one_or_none()


def _validate_document(schema: dict[str, Any], value: dict[str, Any], code: str) -> None:
    """Validate request values against an immutable installed JSON Schema."""
    try:
        validator = Draft202012Validator(schema)
    except SchemaError as error:
        raise PluginConnectionError(code) from error
    if any(validator.iter_errors(value)):
        raise PluginConnectionError(code)
