"""Read-only views of the active immutable installed-plugin registry.

This service never resolves an OCI reference, contacts a registry, resolves a
secret, or chooses a workload image.  It presents only the server-persisted
active generation that the execution resolver uses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlmodel import col, select

from syntara.core.services import BaseService
from syntara.core.services.extensions import ConvertResourceMixin
from syntara.plugin_platform.exceptions import InstalledPluginNotFoundError, PluginRegistryConfigurationError
from syntara.plugin_platform.models.registry import (
    InstalledPlugin,
    InstalledPluginAction,
    InstalledPluginCredentialType,
    InstalledPluginIntegrationType,
    PluginGenerationMember,
    PluginRegistryGeneration,
    PluginRegistryState,
)
from syntara.plugin_platform.schemas import (
    InstalledPluginActionRead,
    InstalledPluginCredentialTypeRead,
    InstalledPluginDetailRead,
    InstalledPluginIntegrationTypeRead,
    InstalledPluginListResponse,
    InstalledPluginRead,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

    from sqlmodel.ext.asyncio.session import AsyncSession

    from syntara.core.models import User


class InstalledPluginConvertMixin(ConvertResourceMixin):
    """Convert installed rows using data already selected from one generation."""

    def __init__(self, *, enabled_by_plugin_id: dict[UUID, bool], action_counts: dict[UUID, int]) -> None:
        """Keep server-derived status and counts for one active snapshot."""
        self._enabled_by_plugin_id = enabled_by_plugin_id
        self._action_counts = action_counts

    def convert_resource(self, resource: object) -> InstalledPluginRead:
        """Return the safe authoring summary for one active registry member."""
        if not isinstance(resource, InstalledPlugin):
            msg = "Installed-plugin conversion requires an installed plugin"
            raise TypeError(msg)
        return InstalledPluginRead(
            id=resource.id,
            namespace=resource.namespace,
            name=resource.name,
            version=resource.version,
            display_name=resource.display_name,
            description=resource.description,
            artifact_repository=resource.artifact_repository,
            artifact_digest=resource.artifact_digest,
            enabled=self._enabled_by_plugin_id[resource.id],
            action_count=self._action_counts.get(resource.id, 0),
            installed_at=resource.created_at,
        )


class InstalledPluginBrowseService(BaseService):
    """List and inspect only the active server-derived plugin registry snapshot."""

    def __init__(self, session: AsyncSession, user: User) -> None:
        """Bind this database-only reader to the authenticated request."""
        super().__init__(session, user)

    async def list_plugins(
        self,
        *,
        enabled: bool | None,
        limit: int,
        cursor: str | None,
        sort: str | None,
        query_params_items: Iterable[tuple[str, str]],
        include_total: bool,
    ) -> InstalledPluginListResponse:
        """Return one paginated active registry snapshot without execution capability."""
        generation, enabled_by_plugin_id = await self._active_snapshot()
        plugin_ids = [
            plugin_id
            for plugin_id, is_enabled in enabled_by_plugin_id.items()
            if enabled is None or is_enabled is enabled
        ]
        action_counts = await self._action_counts(plugin_ids)
        self.convert_resource_mixin = InstalledPluginConvertMixin(
            enabled_by_plugin_id=enabled_by_plugin_id,
            action_counts=action_counts,
        )
        response = await self.list_resources(
            model=InstalledPlugin,
            response_type=InstalledPluginListResponse,
            limit=limit,
            cursor=cursor,
            sort=sort or "display_name",
            query_params_items=((key, value) for key, value in query_params_items if key != "enabled"),
            include_total=include_total,
            id_restriction=plugin_ids,
        )
        if generation is not None:
            response.registry_generation = generation.generation
            response.registry_change_reason = generation.change_reason
            response.registry_published_at = generation.created_at
        return response

    async def get_plugin(self, namespace: str, name: str, version: str) -> InstalledPluginDetailRead:
        """Return one active plugin revision and its safe action contracts."""
        generation, enabled_by_plugin_id = await self._active_snapshot()
        if generation is None:
            raise InstalledPluginNotFoundError
        statement = select(InstalledPlugin).where(
            col(InstalledPlugin.namespace) == namespace,
            col(InstalledPlugin.name) == name,
            col(InstalledPlugin.version) == version,
        )
        plugin = (await self.session.exec(statement)).one_or_none()
        if plugin is None or plugin.id not in enabled_by_plugin_id:
            raise InstalledPluginNotFoundError
        action_statement = (
            select(InstalledPluginAction)
            .where(col(InstalledPluginAction.plugin_installation_id) == plugin.id)
            .order_by(col(InstalledPluginAction.target_name).asc())
        )
        actions = list((await self.session.exec(action_statement)).all())
        integration_types = list(
            (
                await self.session.exec(
                    select(InstalledPluginIntegrationType)
                    .where(col(InstalledPluginIntegrationType.plugin_installation_id) == plugin.id)
                    .order_by(col(InstalledPluginIntegrationType.type_id).asc())
                )
            ).all()
        )
        credential_types = list(
            (
                await self.session.exec(
                    select(InstalledPluginCredentialType)
                    .where(col(InstalledPluginCredentialType.plugin_installation_id) == plugin.id)
                    .order_by(col(InstalledPluginCredentialType.type_id).asc())
                )
            ).all()
        )
        return InstalledPluginDetailRead(
            id=plugin.id,
            namespace=plugin.namespace,
            name=plugin.name,
            version=plugin.version,
            display_name=plugin.display_name,
            description=plugin.description,
            artifact_repository=plugin.artifact_repository,
            artifact_digest=plugin.artifact_digest,
            enabled=enabled_by_plugin_id[plugin.id],
            action_count=len(actions),
            installed_at=plugin.created_at,
            registry_generation=generation.generation,
            actions=[self._action_read(action) for action in actions],
            integration_types=[self._integration_type_read(integration) for integration in integration_types],
            credential_types=[self._credential_type_read(credential) for credential in credential_types],
        )

    async def _active_snapshot(self) -> tuple[PluginRegistryGeneration | None, dict[UUID, bool]]:
        """Read the singleton pointer and its complete membership snapshot."""
        state = (await self.session.exec(select(PluginRegistryState))).one_or_none()
        if state is None:
            msg = "Installed plugin registry has not been initialized"
            raise PluginRegistryConfigurationError(msg)
        if state.active_generation_id is None:
            return None, {}
        generation = (
            await self.session.exec(
                select(PluginRegistryGeneration).where(PluginRegistryGeneration.id == state.active_generation_id)
            )
        ).one_or_none()
        if generation is None:
            msg = "Installed plugin registry is missing its active generation"
            raise PluginRegistryConfigurationError(msg)
        members = list(
            (
                await self.session.exec(
                    select(PluginGenerationMember).where(PluginGenerationMember.generation_id == generation.id)
                )
            ).all()
        )
        return generation, {member.plugin_installation_id: member.enabled for member in members}

    async def _action_counts(self, plugin_ids: list[UUID]) -> dict[UUID, int]:
        """Count actions in one bulk query, avoiding a query per list item."""
        if not plugin_ids:
            return {}
        statement = (
            select(
                col(InstalledPluginAction.plugin_installation_id),
                func.count(col(InstalledPluginAction.id)),
            )
            .where(col(InstalledPluginAction.plugin_installation_id).in_(plugin_ids))
            .group_by(col(InstalledPluginAction.plugin_installation_id))
        )
        return dict((await self.session.exec(statement)).all())

    @staticmethod
    def _action_read(action: InstalledPluginAction) -> InstalledPluginActionRead:
        """Build an authoring contract without canonical descriptor internals."""
        return InstalledPluginActionRead(
            target_name=action.target_name,
            qualified_name=action.qualified_name,
            display_name=action.display_name,
            description=action.description,
            revision_digest=action.revision_digest,
            step_revision_id=action.step_revision_id,
            runtime_kind=action.runtime_kind,
            runtime_abi=action.runtime_abi,
            image_digest=action.image_digest,
            integration_type_revision_id=action.integration_type_revision_id,
            input_schema=action.input_schema,
            input_presentation=action.input_presentation,
            output_schema=action.output_schema,
            error_schema=action.error_schema,
        )

    @staticmethod
    def _integration_type_read(integration: InstalledPluginIntegrationType) -> InstalledPluginIntegrationTypeRead:
        """Expose a safe immutable connection contract for authoring."""
        return InstalledPluginIntegrationTypeRead(
            id=integration.id,
            type_id=integration.type_id,
            display_name=integration.display_name,
            description=integration.description,
            endpoint_kinds=integration.endpoint_kinds,
            credential_type_ids=integration.credential_type_ids,
            configuration_schema=integration.configuration_schema,
        )

    @staticmethod
    def _credential_type_read(credential: InstalledPluginCredentialType) -> InstalledPluginCredentialTypeRead:
        """Expose a safe recipe contract, never a saved credential value."""
        return InstalledPluginCredentialTypeRead(
            id=credential.id,
            integration_type_revision_id=credential.integration_type_revision_id,
            type_id=credential.type_id,
            display_name=credential.display_name,
            description=credential.description,
            credential_schema=credential.credential_schema,
            secret_field_names=credential.secret_field_names,
            issuance=credential.issuance,
        )
