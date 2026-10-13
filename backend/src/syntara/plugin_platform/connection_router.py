"""Project-scoped plugin connection setup and selection endpoints.

The API accepts only installed immutable type revisions and encrypts submitted
credential values immediately. It never returns a secret, an opaque secret ID,
or a publisher-controlled execution route.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, status
from sqlmodel.ext.asyncio.session import AsyncSession

from syntara.audit.decorators import audit
from syntara.audit.models.audit_event import EventCategory
from syntara.auth import get_current_user
from syntara.authz.dependencies import PermissionChecker
from syntara.core.database.session import get_db
from syntara.core.models import User
from syntara.core.services.secret_service import create_secret_service
from syntara.core.syntara_router import SyntaraRouter
from syntara.plugin_platform.models.plugin_connection import PluginConnection
from syntara.plugin_platform.schemas import (
    PluginConnectionCreate,
    PluginConnectionListParams,
    PluginConnectionListResponse,
    PluginConnectionRead,
    PluginConnectionUpdate,
)
from syntara.plugin_platform.services.plugin_connection_service import (
    PluginConnectionProvision,
    PluginConnectionService,
)

router = SyntaraRouter(prefix="/plugin_connections", tags=["Plugin Platform"])

_connection_create = PermissionChecker("plugin_connection", "create", body_project_field="project_id")
_connection_read = PermissionChecker("plugin_connection", "read", query_project_param="project_id")
_connection_update = PermissionChecker(
    "plugin_connection", "update", resource_model=PluginConnection, resource_id_param="connection_id"
)
_connection_delete = PermissionChecker(
    "plugin_connection", "delete", resource_model=PluginConnection, resource_id_param="connection_id"
)


def get_plugin_connection_service(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PluginConnectionService:
    """Build a request-scoped connection service with encrypted secret storage."""
    return PluginConnectionService(db, create_secret_service(db))


@router.post(
    "",
    summary="Create plugin connection",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_connection_create)],
    operation_id="create_plugin_connection",
)
@audit(EventCategory.USER_ACTION, event_action="plugin_connection_create")
async def create_plugin_connection(
    data: PluginConnectionCreate,
    service: Annotated[PluginConnectionService, Depends(get_plugin_connection_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PluginConnectionRead:
    """Create a typed connection and store its credential through SecretService."""
    connection = await service.provision(
        # ``credential_inputs`` is intentionally excluded from response
        # serialization, so construct the internal request explicitly rather
        # than round-tripping through ``model_dump``.
        PluginConnectionProvision(
            name=data.name,
            description=data.description,
            project_id=data.project_id,
            integration_type_revision_id=data.integration_type_revision_id,
            credential_type_revision_id=data.credential_type_revision_id,
            endpoint_kind=data.endpoint_kind,
            configuration=data.configuration,
            credential_inputs=data.credential_inputs,
        ),
        actor_id=current_user.id,
    )
    return PluginConnectionRead.model_validate(connection)


@router.get(
    "",
    summary="List plugin connections",
    dependencies=[Depends(_connection_read)],
    operation_id="list_plugin_connections",
)
async def list_plugin_connections(
    params: Annotated[PluginConnectionListParams, Query()],
    service: Annotated[PluginConnectionService, Depends(get_plugin_connection_service)],
) -> PluginConnectionListResponse:
    """List safe connection metadata for one workflow project and action type."""
    connections = await service.list_for_project(
        project_id=params.project_id,
        integration_type_revision_id=params.integration_type_revision_id,
        enabled=params.enabled,
    )
    return PluginConnectionListResponse(
        resources=[PluginConnectionRead.model_validate(connection) for connection in connections],
        total=len(connections) if params.include_total else None,
    )


@router.patch(
    "/{connection_id}",
    summary="Enable or disable plugin connection",
    dependencies=[Depends(_connection_update)],
    operation_id="update_plugin_connection",
)
@audit(EventCategory.USER_ACTION, event_action="plugin_connection_update", capture_args={"connection_id"})
async def update_plugin_connection(
    connection_id: UUID,
    data: PluginConnectionUpdate,
    service: Annotated[PluginConnectionService, Depends(get_plugin_connection_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PluginConnectionRead:
    """Enable or disable one project connection without exposing its credential."""
    connection = await service.set_enabled(connection_id, enabled=data.enabled, actor_id=current_user.id)
    return PluginConnectionRead.model_validate(connection)


@router.delete(
    "/{connection_id}",
    summary="Delete plugin connection",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_connection_delete)],
    operation_id="delete_plugin_connection",
)
@audit(EventCategory.USER_ACTION, event_action="plugin_connection_delete", capture_args={"connection_id"})
async def delete_plugin_connection(
    connection_id: UUID,
    service: Annotated[PluginConnectionService, Depends(get_plugin_connection_service)],
) -> None:
    """Delete an unused connection and its encrypted credential material."""
    await service.delete(connection_id)
