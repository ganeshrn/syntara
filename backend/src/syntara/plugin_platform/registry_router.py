"""Authenticated read-only API for the active installed-plugin registry."""

from typing import Annotated

from fastapi import Depends, Path, Query, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from syntara.auth import get_current_user
from syntara.authz.dependencies import PermissionChecker
from syntara.core.database.session import get_db
from syntara.core.models import User
from syntara.core.syntara_router import SyntaraRouter
from syntara.plugin_platform.schemas import (
    InstalledPluginDetailRead,
    InstalledPluginListParams,
    InstalledPluginListResponse,
)
from syntara.plugin_platform.services.installed_plugin_browse_service import InstalledPluginBrowseService

router = SyntaraRouter(prefix="/plugin_registry", tags=["Plugin Platform"])

_registry_read = PermissionChecker("plugin_registry", "read")
_IDENTIFIER_PATTERN = r"^[a-z][a-z0-9-]{0,62}$"
_VERSION_PATTERN = r"^(?:0|[1-9][0-9]*)\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$"


def get_installed_plugin_browse_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> InstalledPluginBrowseService:
    """Construct a request-scoped service with database read capability only."""
    return InstalledPluginBrowseService(db, current_user)


@router.get(
    "",
    summary="List installed plugins",
    dependencies=[Depends(_registry_read)],
    operation_id="list_installed_plugins",
    response_description="Paginated plugins in the active installed registry generation",
)
async def list_installed_plugins(
    request: Request,
    service: Annotated[InstalledPluginBrowseService, Depends(get_installed_plugin_browse_service)],
    params: Annotated[InstalledPluginListParams, Query()],
) -> InstalledPluginListResponse:
    """List the safe current registry snapshot without reaching an OCI registry."""
    return await service.list_plugins(
        enabled=params.enabled,
        limit=params.limit,
        cursor=params.cursor,
        sort=params.sort,
        query_params_items=request.query_params.items(),
        include_total=params.include_total,
    )


@router.get(
    "/{namespace}/{name}/versions/{version}",
    summary="Get installed plugin",
    dependencies=[Depends(_registry_read)],
    operation_id="get_installed_plugin",
    response_description="One installed plugin and its active action contracts",
)
async def get_installed_plugin(
    namespace: Annotated[str, Path(min_length=1, max_length=63, pattern=_IDENTIFIER_PATTERN)],
    name: Annotated[str, Path(min_length=1, max_length=63, pattern=_IDENTIFIER_PATTERN)],
    version: Annotated[str, Path(min_length=5, max_length=128, pattern=_VERSION_PATTERN)],
    service: Annotated[InstalledPluginBrowseService, Depends(get_installed_plugin_browse_service)],
) -> InstalledPluginDetailRead:
    """Read a current plugin revision's safe workflow action contracts."""
    return await service.get_plugin(namespace, name, version)
