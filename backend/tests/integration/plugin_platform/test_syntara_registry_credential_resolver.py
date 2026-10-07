"""Integration coverage for the Syntara-backed OCI registry credential adapter."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlmodel import select

from syntara.authz.models import Project
from syntara.core.services.secret_service import create_secret_service
from syntara.credentials.lib.preseed import preseed_credential_types
from syntara.credentials.models.credential import CredentialCreate
from syntara.credentials.models.credential_type import CredentialType
from syntara.credentials.services.credential_service import CredentialService
from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError
from syntara.plugin_platform.services.registry_access import CatalogRegistryAuthorizationRequest
from syntara.plugin_platform.services.syntara_registry_credential_resolver import (
    OCI_REGISTRY_CREDENTIAL_TYPE_NAME,
    SyntaraCredentialRegistryAuthorizationResolver,
)
from syntara.plugin_platform.services.syntara_registry_tls_resolver import (
    OCI_REGISTRY_CA_CREDENTIAL_TYPE_NAME,
    SyntaraCredentialRegistryTLSContextResolver,
)
from tests.fixtures.tls import generate_ca


async def _create_registry_credential(
    session,
    user,
    *,
    inputs: dict[str, str],
    credential_type_name: str = OCI_REGISTRY_CREDENTIAL_TYPE_NAME,
) -> str:
    """Create one managed OCI Registry credential and return its opaque source reference."""
    await preseed_credential_types(session)
    result = await session.exec(select(CredentialType).where(CredentialType.name == credential_type_name))
    credential_type = result.one()
    project = Project(name=f"registry-credential-{uuid4().hex[:8]}", description="Registry credential test project")
    session.add(project)
    await session.flush()
    credential = await CredentialService(session, user, create_secret_service(session)).create_credential(
        CredentialCreate(
            name=f"registry-pull-{uuid4().hex[:8]}",
            credential_type_id=credential_type.id,
            inputs=inputs,
            project_id=project.id,
        )
    )
    return f"credentials/{credential.id}"


def _request(
    credential_ref: str, *, registry_origin: str = "https://quay.example.test"
) -> CatalogRegistryAuthorizationRequest:
    """Build a bounded source request like the catalog flows provide."""
    return CatalogRegistryAuthorizationRequest(
        source_id="local-quay",
        registry_origin=registry_origin,
        repository="syntara/catalog-index",
        credential_ref=credential_ref,
        custom_ca_ref=None,
    )


@pytest.mark.asyncio
async def test_resolver_returns_basic_pull_header_for_bound_oci_registry_credential(test_db_session, test_user) -> None:
    credential_ref = await _create_registry_credential(
        test_db_session,
        test_user,
        inputs={
            "registry_origin": "https://quay.example.test",
            "username": "acme+plugin-reader",
            "password": "robot-password",
        },
    )

    authorization = await SyntaraCredentialRegistryAuthorizationResolver(
        test_db_session, create_secret_service(test_db_session)
    ).resolve_authorization(_request(credential_ref))

    assert authorization == "Basic YWNtZStwbHVnaW4tcmVhZGVyOnJvYm90LXBhc3N3b3Jk"


@pytest.mark.asyncio
async def test_resolver_returns_bearer_pull_header_for_bound_oci_registry_credential(
    test_db_session, test_user
) -> None:
    credential_ref = await _create_registry_credential(
        test_db_session,
        test_user,
        inputs={
            "registry_origin": "https://quay.example.test",
            "bearer_token": "scoped-pull-token",
        },
    )

    authorization = await SyntaraCredentialRegistryAuthorizationResolver(
        test_db_session, create_secret_service(test_db_session)
    ).resolve_authorization(_request(credential_ref))

    assert authorization == "Bearer scoped-pull-token"


@pytest.mark.asyncio
async def test_resolver_refuses_to_send_registry_credential_to_another_origin(test_db_session, test_user) -> None:
    credential_ref = await _create_registry_credential(
        test_db_session,
        test_user,
        inputs={
            "registry_origin": "https://quay.example.test",
            "bearer_token": "scoped-pull-token",
        },
    )

    with pytest.raises(CatalogSourceConfigurationError, match="not authorized for this registry origin"):
        await SyntaraCredentialRegistryAuthorizationResolver(
            test_db_session, create_secret_service(test_db_session)
        ).resolve_authorization(_request(credential_ref, registry_origin="https://other-registry.example.test"))


@pytest.mark.asyncio
async def test_tls_resolver_loads_a_source_bound_custom_ca_bundle(test_db_session, test_user, tmp_path) -> None:
    generate_ca(tmp_path)
    custom_ca_ref = await _create_registry_credential(
        test_db_session,
        test_user,
        inputs={
            "registry_origin": "https://quay.example.test",
            "ca_bundle": (tmp_path / "ca.pem").read_text(),
        },
        credential_type_name=OCI_REGISTRY_CA_CREDENTIAL_TYPE_NAME,
    )
    request = _request("credentials/00000000-0000-4000-8000-000000000000")
    request = CatalogRegistryAuthorizationRequest(
        source_id=request.source_id,
        registry_origin=request.registry_origin,
        repository=request.repository,
        credential_ref=request.credential_ref,
        custom_ca_ref=custom_ca_ref,
    )

    context = await SyntaraCredentialRegistryTLSContextResolver(
        test_db_session, create_secret_service(test_db_session)
    ).resolve_tls_context(request)

    assert context is not None
    assert context.check_hostname is True
    assert context.verify_mode.name == "CERT_REQUIRED"


@pytest.mark.asyncio
async def test_tls_resolver_refuses_to_use_a_custom_ca_for_another_registry_origin(
    test_db_session, test_user, tmp_path
) -> None:
    generate_ca(tmp_path)
    custom_ca_ref = await _create_registry_credential(
        test_db_session,
        test_user,
        inputs={
            "registry_origin": "https://quay.example.test",
            "ca_bundle": (tmp_path / "ca.pem").read_text(),
        },
        credential_type_name=OCI_REGISTRY_CA_CREDENTIAL_TYPE_NAME,
    )
    request = CatalogRegistryAuthorizationRequest(
        source_id="local-quay",
        registry_origin="https://another-registry.example.test",
        repository="syntara/catalog-index",
        credential_ref="credentials/00000000-0000-4000-8000-000000000000",
        custom_ca_ref=custom_ca_ref,
    )

    with pytest.raises(CatalogSourceConfigurationError, match="not authorized for this registry origin"):
        await SyntaraCredentialRegistryTLSContextResolver(
            test_db_session, create_secret_service(test_db_session)
        ).resolve_tls_context(request)
