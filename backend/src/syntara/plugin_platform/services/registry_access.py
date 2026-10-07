"""Shared non-secret registry-access contracts for configured plugin sources.

The control plane owns credential resolution. Callers provide a validated
source snapshot and one already-authorized repository; resolvers return only a
request-local HTTP authorization value and never expose credential material to
the browser, persistent catalog rows, or plugin artifacts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError
from syntara.plugin_platform.source_profile import STANDARD_CATALOG_SOURCE_MODE, CatalogSourceMode

if TYPE_CHECKING:
    import ssl

_TRUST_SUMMARY_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_SENSITIVE_TRUST_SUMMARY_KEY = re.compile(r"token|secret|password|credential|authorization|private", re.IGNORECASE)
_MAX_TRUST_SUMMARY_FIELDS = 32
_MAX_TRUST_SUMMARY_STRING_LENGTH = 512


@dataclass(frozen=True)
class CatalogRegistryAuthorizationRequest:
    """Opaque source references needed for one repository-scoped pull request."""

    source_id: str
    registry_origin: str
    repository: str
    credential_ref: str
    custom_ca_ref: str | None
    source_mode: CatalogSourceMode = STANDARD_CATALOG_SOURCE_MODE


class CatalogRegistryAuthorizationResolver(Protocol):
    """Resolve source-owned registry access without exposing secret material."""

    async def resolve_authorization(self, request: CatalogRegistryAuthorizationRequest) -> str | None:
        """Return a request-local Authorization value, or None for anonymous access."""


class CatalogRegistryTLSContextResolver(Protocol):
    """Resolve system or source-specific certificate trust for one registry request."""

    async def resolve_tls_context(self, request: CatalogRegistryAuthorizationRequest) -> ssl.SSLContext | None:
        """Return a verifying custom-CA context, or None to use the system trust store."""


class SystemTrustCatalogRegistryTLSContextResolver:
    """Use system trust only and reject sources that require a custom CA."""

    async def resolve_tls_context(self, request: CatalogRegistryAuthorizationRequest) -> ssl.SSLContext | None:
        """Keep TLS verification enabled when no custom source CA is configured."""
        if request.custom_ca_ref is not None:
            msg = "Catalog sources with a custom CA require a configured OCI TLS transport adapter"
            raise CatalogSourceConfigurationError(msg)
        return None


def validate_registry_authorization(value: str | None) -> None:
    """Reject header injection without retaining or echoing credentials."""
    if value is not None and (not isinstance(value, str) or not value or "\r" in value or "\n" in value):
        msg = "Catalog authorization resolver returned an unsafe request value"
        raise CatalogSourceConfigurationError(msg)


def safe_trust_summary(summary: Mapping[str, str | int | bool | None]) -> dict[str, str | int | bool | None]:
    """Admit bounded inspection evidence while refusing secret-like data."""
    if not isinstance(summary, Mapping):
        msg = "Catalog trust decision has an invalid summary"
        raise CatalogSourceConfigurationError(msg)
    if len(summary) > _MAX_TRUST_SUMMARY_FIELDS:
        msg = "Catalog trust decision has too many summary fields"
        raise CatalogSourceConfigurationError(msg)
    safe_summary: dict[str, str | int | bool | None] = {}
    for key, value in summary.items():
        if (
            not isinstance(key, str)
            or _TRUST_SUMMARY_KEY_PATTERN.fullmatch(key) is None
            or _SENSITIVE_TRUST_SUMMARY_KEY.search(key)
        ):
            msg = "Catalog trust decision has an unsafe summary field"
            raise CatalogSourceConfigurationError(msg)
        if not isinstance(value, str | int | bool | type(None)):
            msg = "Catalog trust decision has an invalid summary value"
            raise CatalogSourceConfigurationError(msg)
        if isinstance(value, str) and len(value) > _MAX_TRUST_SUMMARY_STRING_LENGTH:
            msg = "Catalog trust decision has an oversized summary value"
            raise CatalogSourceConfigurationError(msg)
        safe_summary[key] = value
    return safe_summary
