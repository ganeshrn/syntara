"""Resolve source-bound OCI custom CA bundles from Syntara credentials."""

from __future__ import annotations

import ssl
from typing import TYPE_CHECKING, Any

from syntara.core.lib.url_validation import validate_host_url
from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError
from syntara.plugin_platform.services.syntara_credential_loader import SyntaraCredentialLoader

OCI_REGISTRY_CA_CREDENTIAL_TYPE_NAME = "OCI Registry CA Bundle"

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

    from syntara.core.services.secret_service import SecretService
    from syntara.plugin_platform.services.registry_access import CatalogRegistryAuthorizationRequest


class SyntaraCredentialRegistryTLSContextResolver:
    """Build one certificate-verifying context for a configured registry source."""

    def __init__(self, session: AsyncSession, secret_service: SecretService) -> None:
        """Bind the database and encrypted secret store for one trusted operation."""
        self._session = session
        self._secret_service = secret_service

    async def resolve_tls_context(self, request: CatalogRegistryAuthorizationRequest) -> ssl.SSLContext | None:
        """Return system trust when no CA is configured, otherwise load its PEM bundle."""
        if request.custom_ca_ref is None:
            return None
        inputs = await SyntaraCredentialLoader(self._session, self._secret_service).load_inputs(
            request.custom_ca_ref,
            expected_type_name=OCI_REGISTRY_CA_CREDENTIAL_TYPE_NAME,
        )
        _validate_registry_origin(inputs, request.registry_origin)
        ca_bundle = _required_pem_bundle(inputs)
        try:
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            context.load_verify_locations(cadata=ca_bundle)
        except ssl.SSLError as exc:
            msg = "Configured catalog custom CA bundle is invalid"
            raise CatalogSourceConfigurationError(msg) from exc
        return context


def _validate_registry_origin(inputs: dict[str, Any], expected_origin: str) -> None:
    """Prevent a CA bundle configured for one source from being used by another."""
    configured_origin = _required_string(inputs, "registry_origin")
    try:
        if validate_host_url(configured_origin) != validate_host_url(expected_origin):
            msg = "Configured catalog custom CA is not authorized for this registry origin"
            raise CatalogSourceConfigurationError(msg)
    except ValueError as exc:
        msg = "Configured catalog custom CA has an invalid registry origin"
        raise CatalogSourceConfigurationError(msg) from exc


def _required_pem_bundle(inputs: dict[str, Any]) -> str:
    """Return one bounded-looking PEM string before OpenSSL parses it."""
    bundle = _required_string(inputs, "ca_bundle")
    if "-----BEGIN CERTIFICATE-----" not in bundle:
        msg = "Configured catalog custom CA bundle is not PEM encoded"
        raise CatalogSourceConfigurationError(msg)
    return bundle


def _required_string(inputs: dict[str, Any], field_name: str) -> str:
    value = inputs.get(field_name)
    if not isinstance(value, str) or not value.strip():
        msg = f"Configured catalog custom CA is missing '{field_name}'"
        raise CatalogSourceConfigurationError(msg)
    return value.strip()
