"""RFC 9457 handlers for catalog-source administrator API errors."""

from typing import TYPE_CHECKING

import structlog
from fastapi import Request, status
from fastapi.responses import JSONResponse

from syntara.core.error_handlers import PROBLEM_TYPES, create_problem_details_response

if TYPE_CHECKING:
    from syntara.plugin_platform.exceptions import (
        CatalogDocumentationHydrationUnavailableError,
        CatalogIndexArtifactVerificationError,
        CatalogPluginIdentityAmbiguousError,
        CatalogPluginInstallationUnavailableError,
        CatalogPluginNotFoundError,
        CatalogSourceConfigurationError,
        CatalogSourceIdentityConflictError,
        CatalogSourceNotFoundError,
        CatalogSourceReconciliationUnavailableError,
        InstalledPluginNotFoundError,
    )

logger = structlog.stdlib.get_logger(__name__)


def catalog_index_artifact_verification_handler(
    request: Request, exc: "CatalogIndexArtifactVerificationError"
) -> JSONResponse:
    """Reject an invalid catalog index without returning a generic server error."""
    logger.warning("catalog_index_artifact_verification_failed", exc_info=exc)
    return create_problem_details_response(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        problem_type=PROBLEM_TYPES["validation_error"],
        title="Catalog Index Artifact Invalid",
        detail="The catalog index artifact did not satisfy the configured source contract.",
        code="CATALOG_INDEX_ARTIFACT_INVALID",
        retryable=False,
        instance=str(request.url),
    )


def catalog_plugin_installation_unavailable_handler(
    request: Request, exc: "CatalogPluginInstallationUnavailableError"
) -> JSONResponse:
    """Fail closed until a deployment wires all trusted installation adapters."""
    logger.warning("catalog_plugin_installation_unavailable", exc_info=exc)
    return create_problem_details_response(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        problem_type=PROBLEM_TYPES["service_unavailable"],
        title="Plugin Installation Unavailable",
        detail="Plugin installation is not configured. Contact an administrator.",
        code="PLUGIN_INSTALLATION_UNAVAILABLE",
        retryable=False,
        instance=str(request.url),
    )


def catalog_source_reconciliation_unavailable_handler(
    request: Request, exc: "CatalogSourceReconciliationUnavailableError"
) -> JSONResponse:
    """Fail closed when the deployment has not explicitly enabled a reconciler."""
    logger.warning("catalog_source_reconciliation_unavailable", exc_info=exc)
    return create_problem_details_response(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        problem_type=PROBLEM_TYPES["service_unavailable"],
        title="Catalog Source Reconciliation Unavailable",
        detail="Catalog source reconciliation is not configured. Contact an administrator.",
        code="CATALOG_SOURCE_RECONCILIATION_UNAVAILABLE",
        retryable=False,
        instance=str(request.url),
    )


def catalog_documentation_hydration_unavailable_handler(
    request: Request, exc: "CatalogDocumentationHydrationUnavailableError"
) -> JSONResponse:
    """Fail closed when no documentation-only hydrator was explicitly configured."""
    logger.warning("catalog_documentation_hydration_unavailable", exc_info=exc)
    return create_problem_details_response(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        problem_type=PROBLEM_TYPES["service_unavailable"],
        title="Catalog Documentation Hydration Unavailable",
        detail="Catalog documentation hydration is not configured. Contact an administrator.",
        code="CATALOG_DOCUMENTATION_HYDRATION_UNAVAILABLE",
        retryable=False,
        instance=str(request.url),
    )


def catalog_source_not_found_handler(request: Request, exc: "CatalogSourceNotFoundError") -> JSONResponse:
    """Return one non-enumerating not-found response."""
    logger.info("catalog_source_not_found", exc_info=exc)
    return create_problem_details_response(
        status_code=status.HTTP_404_NOT_FOUND,
        problem_type=PROBLEM_TYPES["resource_not_found"],
        title="Catalog Source Not Found",
        detail=exc.message,
        code="CATALOG_SOURCE_NOT_FOUND",
        retryable=False,
        instance=str(request.url),
    )


def catalog_source_conflict_handler(request: Request, exc: "CatalogSourceIdentityConflictError") -> JSONResponse:
    """Return a safe stable-identity conflict response."""
    logger.info("catalog_source_identity_conflict", exc_info=exc)
    return create_problem_details_response(
        status_code=status.HTTP_409_CONFLICT,
        problem_type=PROBLEM_TYPES["name_conflict"],
        title="Catalog Source Identity Conflict",
        detail=exc.message,
        code="CATALOG_SOURCE_IDENTITY_CONFLICT",
        retryable=False,
        instance=str(request.url),
    )


def catalog_source_configuration_handler(request: Request, exc: "CatalogSourceConfigurationError") -> JSONResponse:
    """Return a safe source-configuration response without upstream details."""
    logger.warning("catalog_source_configuration_error", exc_info=exc)
    return create_problem_details_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        problem_type=PROBLEM_TYPES["validation_error"],
        title="Catalog Source Configuration Error",
        detail=exc.message,
        code="CATALOG_SOURCE_CONFIGURATION_ERROR",
        retryable=False,
        instance=str(request.url),
    )


def catalog_plugin_not_found_handler(request: Request, exc: "CatalogPluginNotFoundError") -> JSONResponse:
    """Return one safe not-found response for the active catalog projection."""
    logger.info("catalog_plugin_not_found", exc_info=exc)
    return create_problem_details_response(
        status_code=status.HTTP_404_NOT_FOUND,
        problem_type=PROBLEM_TYPES["resource_not_found"],
        title="Catalog Plugin Not Found",
        detail=exc.message,
        code="CATALOG_PLUGIN_NOT_FOUND",
        retryable=False,
        instance=str(request.url),
    )


def installed_plugin_not_found_handler(request: Request, exc: "InstalledPluginNotFoundError") -> JSONResponse:
    """Return one safe not-found response for the active installed registry."""
    logger.info("installed_plugin_not_found", exc_info=exc)
    return create_problem_details_response(
        status_code=status.HTTP_404_NOT_FOUND,
        problem_type=PROBLEM_TYPES["resource_not_found"],
        title="Installed Plugin Not Found",
        detail=exc.message,
        code="INSTALLED_PLUGIN_NOT_FOUND",
        retryable=False,
        instance=str(request.url),
    )


def catalog_plugin_ambiguous_handler(request: Request, exc: "CatalogPluginIdentityAmbiguousError") -> JSONResponse:
    """Require source selection without exposing any source credentials or internals."""
    logger.info("catalog_plugin_identity_ambiguous", exc_info=exc)
    return create_problem_details_response(
        status_code=status.HTTP_409_CONFLICT,
        problem_type=PROBLEM_TYPES["name_conflict"],
        title="Catalog Plugin Identity Ambiguous",
        detail=exc.message,
        code="CATALOG_PLUGIN_IDENTITY_AMBIGUOUS",
        retryable=False,
        instance=str(request.url),
    )
