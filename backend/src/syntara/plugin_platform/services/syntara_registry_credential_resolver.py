"""Resolve an OCI pull credential from Syntara's encrypted credential store.

This is the first concrete registry-access adapter.  It accepts only an
explicit Syntara credential reference and the managed ``OCI Registry``
credential type.  It creates a request-local HTTP Authorization value; the
credential, decrypted fields, and resulting header are never persisted or
returned by a catalog API.

Custom CA material is deliberately not handled here. The paired TLS resolver
owns certificate trust and passes a verifying context to the OCI transport.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

from syntara.core.lib.url_validation import validate_host_url
from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError
from syntara.plugin_platform.services.registry_access import (
    CatalogRegistryAuthorizationRequest,
    validate_registry_authorization,
)
from syntara.plugin_platform.services.syntara_credential_loader import SyntaraCredentialLoader

OCI_REGISTRY_CREDENTIAL_TYPE_NAME = "OCI Registry"

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

    from syntara.core.services.secret_service import SecretService


class SyntaraCredentialRegistryAuthorizationResolver:
    """Resolve one source-owned OCI registry credential from encrypted storage.

    The source configuration may stay extensible through its opaque
    ``credential_ref`` field, but this concrete adapter intentionally supports
    just ``credentials/<UUID>``.  Other reference schemes need their own
    explicit, auditable adapter rather than a permissive fallback.
    """

    def __init__(self, session: AsyncSession, secret_service: SecretService) -> None:
        """Bind the trusted database and secret services for one operation scope."""
        self._session = session
        self._secret_service = secret_service

    async def resolve_authorization(self, request: CatalogRegistryAuthorizationRequest) -> str:
        """Return a Basic or Bearer pull header for the configured registry only."""
        inputs = await SyntaraCredentialLoader(self._session, self._secret_service).load_inputs(
            request.credential_ref,
            expected_type_name=OCI_REGISTRY_CREDENTIAL_TYPE_NAME,
        )
        _validate_registry_origin(inputs, request.registry_origin)
        authorization = _build_authorization(inputs)
        validate_registry_authorization(authorization)
        return authorization


def _validate_registry_origin(inputs: dict[str, Any], expected_origin: str) -> None:
    """Bind the credential to one normalized registry origin before using it."""
    configured_origin = _required_string(inputs, "registry_origin")
    try:
        normalized_configured_origin = validate_host_url(configured_origin)
        normalized_expected_origin = validate_host_url(expected_origin)
    except ValueError as exc:
        msg = "Catalog registry credential has an invalid registry origin"
        raise CatalogSourceConfigurationError(msg) from exc
    if normalized_configured_origin != normalized_expected_origin:
        msg = "Catalog registry credential is not authorized for this registry origin"
        raise CatalogSourceConfigurationError(msg)


def _build_authorization(inputs: dict[str, Any]) -> str:
    """Map the validated OCI credential inputs to a single safe HTTP header."""
    bearer_token = _optional_string(inputs, "bearer_token")
    username = _optional_string(inputs, "username")
    password = _optional_string(inputs, "password")
    if bearer_token is not None:
        if username is not None or password is not None:
            msg = "Catalog registry credential must use either bearer or basic authentication"
            raise CatalogSourceConfigurationError(msg)
        return f"Bearer {bearer_token}"
    if username is None or password is None:
        msg = "Catalog registry credential must contain a bearer token or username and password"
        raise CatalogSourceConfigurationError(msg)
    if ":" in username:
        msg = "Catalog registry credential username must not contain a colon"
        raise CatalogSourceConfigurationError(msg)
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return f"Basic {encoded}"


def _required_string(inputs: dict[str, Any], field_name: str) -> str:
    """Return one non-empty string input without coercing unexpected types."""
    value = _optional_string(inputs, field_name)
    if value is None:
        msg = f"Catalog registry credential is missing '{field_name}'"
        raise CatalogSourceConfigurationError(msg)
    return value


def _optional_string(inputs: dict[str, Any], field_name: str) -> str | None:
    """Treat absent or empty fields as missing and reject control characters."""
    value = inputs.get(field_name)
    if value in (None, ""):
        return None
    if not isinstance(value, str) or "\r" in value or "\n" in value:
        msg = f"Catalog registry credential has an invalid '{field_name}' value"
        raise CatalogSourceConfigurationError(msg)
    return value
