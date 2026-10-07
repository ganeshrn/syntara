"""Small trusted loader for managed Syntara credentials used by plugin sources."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy.orm import selectinload
from sqlmodel import select

from syntara.core.lib.encryption import EncryptionError
from syntara.credentials.models.credential import Credential
from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

    from syntara.core.services.secret_service import SecretService

_CREDENTIAL_REFERENCE_PATTERN = re.compile(r"^credentials/([0-9a-fA-F-]{36})$")


class SyntaraCredentialLoader:
    """Load one enabled managed credential and its decrypted inputs internally."""

    def __init__(self, session: AsyncSession, secret_service: SecretService) -> None:
        """Bind the database and encrypted secret store for one trusted operation."""
        self._session = session
        self._secret_service = secret_service

    async def load_inputs(self, credential_ref: str, *, expected_type_name: str) -> dict[str, Any]:
        """Return decrypted fields only for the expected managed credential type."""
        credential_id = parse_syntara_credential_reference(credential_ref)
        result = await self._session.exec(
            select(Credential).where(Credential.id == credential_id).options(selectinload(Credential.credential_type))  # type: ignore[arg-type]
        )
        credential = result.one_or_none()
        if credential is None:
            msg = "Configured catalog credential is unavailable"
            raise CatalogSourceConfigurationError(msg)
        if not credential.enabled:
            msg = "Configured catalog credential is disabled"
            raise CatalogSourceConfigurationError(msg)
        credential_type = credential.credential_type
        if credential_type is None or credential_type.name != expected_type_name or not credential_type.managed:
            msg = "Configured catalog credential does not have the required managed type"
            raise CatalogSourceConfigurationError(msg)
        if credential.secret_id is None:
            msg = "Configured catalog credential has no stored inputs"
            raise CatalogSourceConfigurationError(msg)
        try:
            return await self._secret_service.retrieve_secret(credential.secret_id)
        except (EncryptionError, KeyError) as exc:
            msg = "Configured catalog credential could not be decrypted"
            raise CatalogSourceConfigurationError(msg) from exc


def parse_syntara_credential_reference(value: str) -> UUID:
    """Accept only the canonical opaque Syntara credential reference form."""
    match = _CREDENTIAL_REFERENCE_PATTERN.fullmatch(value)
    if match is None:
        msg = "Catalog credentials must use the credentials/<UUID> reference form"
        raise CatalogSourceConfigurationError(msg)
    try:
        return UUID(match.group(1))
    except ValueError as exc:
        msg = "Catalog credential reference is invalid"
        raise CatalogSourceConfigurationError(msg) from exc
