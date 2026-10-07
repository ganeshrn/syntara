"""Administrator API for catalog-source configuration and lifecycle."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, Request, status
from sqlmodel.ext.asyncio.session import AsyncSession

from syntara.audit.decorators import audit
from syntara.audit.models.audit_event import EventCategory
from syntara.auth import get_current_user
from syntara.authz.dependencies import PermissionChecker
from syntara.core.config.base import get_settings
from syntara.core.database.session import get_db
from syntara.core.models import User
from syntara.core.models.error import ErrorData
from syntara.core.syntara_router import SyntaraRouter
from syntara.plugin_platform.exceptions import CatalogSourceReconciliationUnavailableError
from syntara.plugin_platform.schemas import (
    PluginSourceCreate,
    PluginSourceListParams,
    PluginSourceListResponse,
    PluginSourceRead,
    PluginSourceReconciliationRead,
    PluginSourceUpdate,
)
from syntara.plugin_platform.services.catalog_source_service import CatalogSourceService
from syntara.plugin_platform.services.local_poc_catalog_reconciliation_service import (
    LocalPocCatalogReconciliationService,
)

router = SyntaraRouter(prefix="/plugin_sources", tags=["Plugin Platform"])

_source_create = PermissionChecker("plugin_source", "create")
_source_list = PermissionChecker("plugin_source", "read")
_source_read = PermissionChecker(
    "plugin_source",
    "read",
    resource_id_param="plugin_source_id",
)
_source_update = PermissionChecker(
    "plugin_source",
    "update",
    resource_id_param="plugin_source_id",
)
_source_delete = PermissionChecker(
    "plugin_source",
    "delete",
    resource_id_param="plugin_source_id",
)
_source_enable = PermissionChecker(
    "plugin_source",
    "enable",
    resource_id_param="plugin_source_id",
)
_source_disable = PermissionChecker(
    "plugin_source",
    "disable",
    resource_id_param="plugin_source_id",
)
_source_reconcile = PermissionChecker(
    "plugin_source",
    "reconcile",
    resource_id_param="plugin_source_id",
)


def get_catalog_source_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CatalogSourceService:
    """Construct the request-scoped catalog-source administration service."""
    return CatalogSourceService(
        db,
        current_user,
        local_poc_enabled=get_settings().plugin_platform_local_poc_enabled,
    )


def get_catalog_source_reconciliation_service(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LocalPocCatalogReconciliationService:
    """Return the development reconciler only when its deployment switch is enabled.

    This is intentionally not a fallback for the normal signed source path.
    A production reconciler must be composed separately with its own trusted
    credential and signature-policy adapters.
    """
    if not get_settings().plugin_platform_local_poc_enabled:
        raise CatalogSourceReconciliationUnavailableError
    return LocalPocCatalogReconciliationService(db)


@router.post(
    "",
    summary="Create plugin catalog source",
    response_model=PluginSourceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_source_create)],
    operation_id="create_plugin_source",
    response_description="Plugin catalog source created",
)
@audit(EventCategory.USER_ACTION, event_action="plugin_source_create")
async def create_plugin_source(
    source_request: PluginSourceCreate,
    service: Annotated[CatalogSourceService, Depends(get_catalog_source_service)],
) -> PluginSourceRead:
    """Register non-secret local catalog-source configuration."""
    return PluginSourceRead.model_validate(await service.create_source(source_request))


@router.get(
    "",
    summary="List plugin catalog sources",
    dependencies=[Depends(_source_list)],
    operation_id="list_plugin_sources",
    response_description="Paginated plugin catalog sources",
)
async def list_plugin_sources(
    request: Request,
    service: Annotated[CatalogSourceService, Depends(get_catalog_source_service)],
    params: Annotated[PluginSourceListParams, Query()],
) -> PluginSourceListResponse:
    """List administrator-visible catalog sources with cursor pagination."""
    return await service.list_sources(
        limit=params.limit,
        cursor=params.cursor,
        sort=params.sort,
        query_params_items=request.query_params.items(),
        include_total=params.include_total,
    )


@router.get(
    "/{plugin_source_id}",
    summary="Get plugin catalog source",
    dependencies=[Depends(_source_read)],
    operation_id="get_plugin_source",
    response_description="Plugin catalog source details",
)
async def get_plugin_source(
    plugin_source_id: UUID,
    service: Annotated[CatalogSourceService, Depends(get_catalog_source_service)],
) -> PluginSourceRead:
    """Get non-secret source configuration by resource ID."""
    return PluginSourceRead.model_validate(await service.get_source(plugin_source_id))


@router.post(
    "/{plugin_source_id}/reconcile",
    summary="Reconcile plugin catalog source",
    response_model=PluginSourceReconciliationRead,
    dependencies=[Depends(_source_reconcile)],
    operation_id="reconcile_plugin_source",
    response_description="Safe result of catalog-source reconciliation",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorData,
            "description": (
                "Catalog source reconciliation is unavailable until a trusted source reconciler is configured."
            ),
        }
    },
)
@audit(EventCategory.USER_ACTION, event_action="plugin_source_reconcile", capture_args={"plugin_source_id"})
async def reconcile_plugin_source(
    plugin_source_id: UUID,
    reconciliation_service: Annotated[
        LocalPocCatalogReconciliationService,
        Depends(get_catalog_source_reconciliation_service),
    ],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PluginSourceReconciliationRead:
    """Discover an OCI catalog index through the explicitly enabled local-PoC path.

    The local mode reconciles discovery metadata only. It cannot be used to
    fetch plugin payloads, install plugins, or start a workload.
    """
    result = await reconciliation_service.reconcile_source(
        plugin_source_id,
        initiated_by=current_user.id,
    )
    return PluginSourceReconciliationRead(
        reconciliation_id=result.reconciliation_id,
        state=result.state,
        failure_code=result.failure_code,
        catalog_generation=result.catalog_generation,
        catalog_index_digest=result.catalog_index_digest,
        entry_count=result.entry_count,
        idempotent=result.idempotent,
    )


@router.patch(
    "/{plugin_source_id}",
    summary="Update plugin catalog source",
    dependencies=[Depends(_source_update)],
    operation_id="update_plugin_source",
    response_description="Updated plugin catalog source",
)
@audit(EventCategory.USER_ACTION, event_action="plugin_source_update", capture_args={"plugin_source_id"})
async def update_plugin_source(
    plugin_source_id: UUID,
    source_request: PluginSourceUpdate,
    service: Annotated[CatalogSourceService, Depends(get_catalog_source_service)],
) -> PluginSourceRead:
    """Update source settings without changing its signed-index identity."""
    return PluginSourceRead.model_validate(await service.update_source(plugin_source_id, source_request))


@router.post(
    "/{plugin_source_id}/disable",
    summary="Disable plugin catalog source",
    dependencies=[Depends(_source_disable)],
    operation_id="disable_plugin_source",
    response_description="Disabled plugin catalog source",
)
@audit(EventCategory.USER_ACTION, event_action="plugin_source_disable", capture_args={"plugin_source_id"})
async def disable_plugin_source(
    plugin_source_id: UUID,
    service: Annotated[CatalogSourceService, Depends(get_catalog_source_service)],
) -> PluginSourceRead:
    """Stop future discovery while retaining the source's historical evidence."""
    return PluginSourceRead.model_validate(await service.set_enabled(plugin_source_id, enabled=False))


@router.post(
    "/{plugin_source_id}/enable",
    summary="Enable plugin catalog source",
    dependencies=[Depends(_source_enable)],
    operation_id="enable_plugin_source",
    response_description="Enabled plugin catalog source",
)
@audit(EventCategory.USER_ACTION, event_action="plugin_source_enable", capture_args={"plugin_source_id"})
async def enable_plugin_source(
    plugin_source_id: UUID,
    service: Annotated[CatalogSourceService, Depends(get_catalog_source_service)],
) -> PluginSourceRead:
    """Allow future reconciliation for an existing source."""
    return PluginSourceRead.model_validate(await service.set_enabled(plugin_source_id, enabled=True))


@router.delete(
    "/{plugin_source_id}",
    summary="Delete plugin catalog source",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_source_delete)],
    operation_id="delete_plugin_source",
    response_description="Plugin catalog source deleted",
)
@audit(EventCategory.USER_ACTION, event_action="plugin_source_delete", capture_args={"plugin_source_id"})
async def delete_plugin_source(
    plugin_source_id: UUID,
    service: Annotated[CatalogSourceService, Depends(get_catalog_source_service)],
) -> None:
    """Remove discovery configuration and projections without uninstalling content."""
    await service.delete_source(plugin_source_id)
