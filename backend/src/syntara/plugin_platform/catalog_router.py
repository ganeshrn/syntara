"""Authenticated discovery and administrator-install APIs for accepted plugin catalog projections."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Path, Query, Request, status
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
from syntara.plugin_platform.exceptions import (
    CatalogDocumentationHydrationUnavailableError,
    CatalogPluginInstallationUnavailableError,
)
from syntara.plugin_platform.schemas import (
    CatalogPluginDetailRead,
    CatalogPluginDocumentationHydrationRead,
    CatalogPluginDocumentationRead,
    CatalogPluginInstallRead,
    CatalogPluginListParams,
    CatalogPluginListResponse,
    CatalogPluginRead,
)
from syntara.plugin_platform.services.catalog_browse_service import CatalogBrowseService
from syntara.plugin_platform.services.catalog_installation_service import CatalogPluginInstallationService
from syntara.plugin_platform.services.local_poc_catalog_reconciliation_service import (
    LocalPocCatalogDocumentationHydrationService,
    build_local_poc_catalog_installation_service,
)
from syntara.plugin_platform.services.oci_distribution_client import OciDistributionClient

router = SyntaraRouter(prefix="/catalog/plugins", tags=["Plugin Platform"])

_catalog_read = PermissionChecker("plugin_catalog", "read")
_catalog_hydrate_documentation = PermissionChecker("plugin_catalog", "hydrate_documentation")
_registry_install = PermissionChecker("plugin_registry", "install")

_IDENTIFIER_PATTERN = r"^[a-z][a-z0-9-]{0,62}$"
_VERSION_PATTERN = r"^(?:0|[1-9][0-9]*)\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$"


def get_catalog_browse_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CatalogBrowseService:
    """Construct a request-scoped service with no registry or secret capability."""
    return CatalogBrowseService(db, current_user)


async def get_catalog_plugin_installation_service(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AsyncIterator[CatalogPluginInstallationService]:
    """Resolve the deployment-owned installer without accepting browser runtime inputs.

    A production composition root must place a callable named
    ``plugin_catalog_installation_service_factory`` on application state. Its
    only input is the request database session; it owns the registry access,
    trust, and workload-admission adapters.  The sole exception is the
    explicitly enabled loopback-only local-PoC composition.  It still verifies
    the catalog-selected artifact and only resolves a server-owned persisted
    platform-runner profile; it cannot admit publisher workload images.
    """
    if get_settings().plugin_platform_local_poc_enabled:
        async with OciDistributionClient() as client:
            yield build_local_poc_catalog_installation_service(db, registry_client=client)
        return

    factory = getattr(request.app.state, "plugin_catalog_installation_service_factory", None)
    if not callable(factory):
        raise CatalogPluginInstallationUnavailableError
    service = factory(db)
    if not isinstance(service, CatalogPluginInstallationService):
        raise CatalogPluginInstallationUnavailableError
    yield service


def get_catalog_documentation_hydration_service(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LocalPocCatalogDocumentationHydrationService:
    """Return the explicit loopback-only documentation hydrator for local development.

    Production composition must supply its own signed artifact trust evaluator;
    this local adapter deliberately cannot install a plugin or dispatch a
    workload.
    """
    if not get_settings().plugin_platform_local_poc_enabled:
        raise CatalogDocumentationHydrationUnavailableError
    return LocalPocCatalogDocumentationHydrationService(db)


@router.get(
    "",
    summary="List catalog plugins",
    dependencies=[Depends(_catalog_read)],
    operation_id="list_catalog_plugins",
    response_description="Paginated visible catalog plugin versions",
)
async def list_catalog_plugins(
    request: Request,
    service: Annotated[CatalogBrowseService, Depends(get_catalog_browse_service)],
    params: Annotated[CatalogPluginListParams, Query()],
) -> CatalogPluginListResponse:
    """List plugins from the immutable active snapshots of enabled sources."""
    return await service.list_plugins(
        source_id=params.source_id,
        limit=params.limit,
        cursor=params.cursor,
        sort=params.sort,
        query_params_items=request.query_params.items(),
        include_total=params.include_total,
    )


@router.get(
    "/{namespace}/{name}",
    summary="Get catalog plugin",
    dependencies=[Depends(_catalog_read)],
    operation_id="get_catalog_plugin",
    response_description="Visible versions of one catalog plugin",
)
async def get_catalog_plugin(
    namespace: Annotated[str, Path(min_length=1, max_length=63, pattern=_IDENTIFIER_PATTERN)],
    name: Annotated[str, Path(min_length=1, max_length=63, pattern=_IDENTIFIER_PATTERN)],
    service: Annotated[CatalogBrowseService, Depends(get_catalog_browse_service)],
) -> CatalogPluginDetailRead:
    """Inspect all active, source-qualified versions of one catalog plugin."""
    return await service.get_plugin(namespace, name)


@router.get(
    "/{namespace}/{name}/versions/{version}",
    summary="Get catalog plugin version",
    dependencies=[Depends(_catalog_read)],
    operation_id="get_catalog_plugin_version",
    response_description="One visible immutable catalog plugin version",
)
async def get_catalog_plugin_version(
    namespace: Annotated[str, Path(min_length=1, max_length=63, pattern=_IDENTIFIER_PATTERN)],
    name: Annotated[str, Path(min_length=1, max_length=63, pattern=_IDENTIFIER_PATTERN)],
    version: Annotated[str, Path(min_length=5, max_length=128, pattern=_VERSION_PATTERN)],
    service: Annotated[CatalogBrowseService, Depends(get_catalog_browse_service)],
    source_id: Annotated[
        str | None,
        Query(
            min_length=1, max_length=63, pattern=_IDENTIFIER_PATTERN, description="Optional stable catalog source ID."
        ),
    ] = None,
) -> CatalogPluginRead:
    """Inspect one version; require source selection if active sources conflict."""
    return await service.get_plugin_version(namespace, name, version, source_id=source_id)


@router.get(
    "/{namespace}/{name}/versions/{version}/documentation",
    summary="Get catalog plugin documentation",
    dependencies=[Depends(_catalog_read)],
    operation_id="get_catalog_plugin_documentation",
    response_description="Sanitized documentation for one visible immutable catalog plugin version",
)
async def get_catalog_plugin_documentation(
    namespace: Annotated[str, Path(min_length=1, max_length=63, pattern=_IDENTIFIER_PATTERN)],
    name: Annotated[str, Path(min_length=1, max_length=63, pattern=_IDENTIFIER_PATTERN)],
    version: Annotated[str, Path(min_length=5, max_length=128, pattern=_VERSION_PATTERN)],
    service: Annotated[CatalogBrowseService, Depends(get_catalog_browse_service)],
    source_id: Annotated[
        str | None,
        Query(
            min_length=1, max_length=63, pattern=_IDENTIFIER_PATTERN, description="Optional stable catalog source ID."
        ),
    ] = None,
) -> CatalogPluginDocumentationRead:
    """Read previously admitted, sanitized documentation without contacting a registry."""
    return await service.get_plugin_documentation(namespace, name, version, source_id=source_id)


@router.post(
    "/{namespace}/{name}/versions/{version}/documentation/hydrate",
    summary="Hydrate catalog plugin documentation",
    dependencies=[Depends(_catalog_hydrate_documentation)],
    operation_id="hydrate_catalog_plugin_documentation",
    response_description="Result of verifying and safely rendering catalog plugin documentation",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorData,
            "description": "Catalog documentation hydration is unavailable until trusted adapters are configured.",
        }
    },
)
@audit(
    EventCategory.USER_ACTION,
    event_action="plugin_catalog_documentation_hydrate",
    capture_args={"namespace", "name", "version", "source_id"},
)
async def hydrate_catalog_plugin_documentation(
    namespace: Annotated[str, Path(min_length=1, max_length=63, pattern=_IDENTIFIER_PATTERN)],
    name: Annotated[str, Path(min_length=1, max_length=63, pattern=_IDENTIFIER_PATTERN)],
    version: Annotated[str, Path(min_length=5, max_length=128, pattern=_VERSION_PATTERN)],
    source_id: Annotated[
        str,
        Query(
            min_length=1,
            max_length=63,
            pattern=_IDENTIFIER_PATTERN,
            description="Stable catalog source identity selected by the administrator.",
        ),
    ],
    browse_service: Annotated[CatalogBrowseService, Depends(get_catalog_browse_service)],
    hydration_service: Annotated[
        LocalPocCatalogDocumentationHydrationService,
        Depends(get_catalog_documentation_hydration_service),
    ],
) -> CatalogPluginDocumentationHydrationRead:
    """Persist safe documentation for one source-selected, digest-pinned plugin.

    The browser supplies only the visible catalog identity. The service derives
    registry location, artifact digest, and trust configuration from the
    accepted server-side catalog projection; it never accepts a URL, token, or
    artifact reference from the caller.
    """
    selected = await browse_service.get_plugin_version(
        namespace,
        name,
        version,
        source_id=source_id,
    )
    result = await hydration_service.hydrate(selected.id)
    return CatalogPluginDocumentationHydrationRead(
        catalog_plugin_version_id=result.catalog_plugin_version_id,
        documentation_digest=result.documentation_digest,
        idempotent=result.idempotent,
    )


@router.post(
    "/{namespace}/{name}/versions/{version}/install",
    summary="Install catalog plugin",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(_registry_install)],
    operation_id="install_catalog_plugin",
    response_description="Catalog-selected plugin installed into the immutable active registry",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorData,
            "description": "Plugin installation is unavailable until trusted installation adapters are configured.",
        }
    },
)
@audit(
    EventCategory.USER_ACTION,
    event_action="plugin_catalog_install",
    capture_args={"namespace", "name", "version", "source_id"},
)
async def install_catalog_plugin(
    namespace: Annotated[str, Path(min_length=1, max_length=63, pattern=_IDENTIFIER_PATTERN)],
    name: Annotated[str, Path(min_length=1, max_length=63, pattern=_IDENTIFIER_PATTERN)],
    version: Annotated[str, Path(min_length=5, max_length=128, pattern=_VERSION_PATTERN)],
    source_id: Annotated[
        str,
        Query(
            min_length=1,
            max_length=63,
            pattern=_IDENTIFIER_PATTERN,
            description="Stable catalog source identity selected by the administrator.",
        ),
    ],
    browse_service: Annotated[CatalogBrowseService, Depends(get_catalog_browse_service)],
    installation_service: Annotated[
        CatalogPluginInstallationService,
        Depends(get_catalog_plugin_installation_service),
    ],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CatalogPluginInstallRead:
    """Install exactly one active catalog version through trusted server-owned adapters.

    The browser supplies only the visible catalog identity and mandatory source
    selection. The endpoint resolves that local immutable row, then the
    installer obtains all registry/authentication/trust/image inputs from the
    configured source and server-owned adapters.

    The operation fails closed when the deployment has not configured all
    trusted installation adapters.
    """
    selected = await browse_service.get_plugin_version(
        namespace,
        name,
        version,
        source_id=source_id,
    )
    result = await installation_service.install(selected.id, actor_id=current_user.id)
    return CatalogPluginInstallRead(
        catalog_plugin_version_id=result.catalog_plugin_version_id,
        plugin_installation_id=result.plugin_installation_id,
        registry_generation=result.registry_generation,
        idempotent=result.idempotent,
    )
