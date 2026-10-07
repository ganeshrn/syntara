"""Database-backed coverage for non-secret plugin connection identities."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlmodel import select

from syntara.plugin_platform.exceptions import PluginConnectionError
from syntara.plugin_platform.models.registry import PluginRegistryState
from syntara.plugin_platform.services.plugin_connection_service import (
    PluginConnectionCreate,
    PluginConnectionService,
)
from syntara.plugin_platform.services.registry_service import (
    InstalledActionCandidate,
    InstalledCredentialTypeCandidate,
    InstalledIntegrationTypeCandidate,
    PluginRegistryService,
    VerifiedPluginCandidate,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlmodel.ext.asyncio.session import AsyncSession

    from syntara.core.models import User


def _digest(character: str) -> str:
    """Build a deterministic test-only SHA-256 digest."""
    return f"sha256:{character * 64}"


def _candidate() -> VerifiedPluginCandidate:
    """Return one action with a matching immutable connection contract."""
    return VerifiedPluginCandidate(
        namespace="syntara",
        name="github",
        version="1.0.0",
        display_name="GitHub",
        description="GitHub plugin fixture.",
        artifact_repository="syntara/plugins/github",
        artifact_digest=_digest("a"),
        canonical_descriptor={"kind": "PluginDescriptor", "spec": {"targets": []}},
        actions=(
            InstalledActionCandidate(
                target_name="create-issue",
                display_name="Create issue",
                description="Creates a GitHub issue.",
                revision_digest=_digest("b"),
                runtime_kind="custom-workload",
                image_digest=f"quay.io/syntara/github@{_digest('c')}",
                integration_type_id="syntara.github.connection",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                error_schema={"type": "object"},
                canonical_definition={"kind": "Action", "metadata": {"name": "create-issue"}},
            ),
        ),
        integration_types=(
            InstalledIntegrationTypeCandidate(
                type_id="syntara.github.connection",
                display_name="GitHub connection",
                description="GitHub Cloud or Enterprise Server.",
                revision_digest=_digest("d"),
                endpoint_kinds=("github-cloud", "github-enterprise"),
                credential_type_ids=("syntara.github.service-token",),
                configuration_schema={"type": "object"},
                canonical_definition={"kind": "IntegrationType", "id": "syntara.github.connection"},
            ),
        ),
        credential_types=(
            InstalledCredentialTypeCandidate(
                type_id="syntara.github.service-token",
                integration_type_id="syntara.github.connection",
                display_name="GitHub service token",
                description="A static bearer token.",
                revision_digest=_digest("e"),
                credential_schema={"type": "object", "required": ["token"]},
                secret_field_names=("token",),
                issuance={"kind": "static-bearer.v1"},
                canonical_definition={"kind": "CredentialRecipe", "id": "syntara.github.service-token"},
            ),
        ),
    )


@pytest_asyncio.fixture(autouse=True)
async def _initialized_plugin_registry(test_db_session: AsyncSession) -> None:
    """Restore the singleton state expected by the immutable registry service."""
    state = (await test_db_session.exec(select(PluginRegistryState))).one_or_none()
    if state is None:
        test_db_session.add(PluginRegistryState())
        await test_db_session.commit()


async def _installed_type_revision(
    session: AsyncSession,
    user: User,
) -> UUID:
    """Install one verified fixture and resolve the action-selected type revision."""
    registry = PluginRegistryService(session)
    await registry.install_verified(_candidate(), actor_id=user.id)
    action = await registry.resolve_enabled_action("syntara.github.create-issue:" + "b" * 64)
    assert action.integration_type_revision_id is not None
    return action.integration_type_revision_id


@pytest.mark.asyncio
async def test_connection_pins_one_declared_endpoint_kind(
    test_db_session: AsyncSession,
    test_project_id: UUID,
    test_user: User,
) -> None:
    """A connection selects only an installed immutable type and declared endpoint kind."""
    integration_type_revision_id = await _installed_type_revision(test_db_session, test_user)
    service = PluginConnectionService(test_db_session)

    connection = await service.create(
        PluginConnectionCreate(
            name="GitHub Cloud",
            description="Non-secret GitHub Cloud connection identity.",
            project_id=test_project_id,
            integration_type_revision_id=integration_type_revision_id,
            endpoint_kind="github-cloud",
        ),
        actor_id=test_user.id,
    )
    resolved = await service.resolve_enabled(
        connection.id,
        project_id=test_project_id,
        integration_type_revision_id=integration_type_revision_id,
    )

    assert resolved.connection_id == connection.id
    assert resolved.endpoint_kind == "github-cloud"
    assert resolved.integration_type_revision_id == integration_type_revision_id


@pytest.mark.asyncio
async def test_connection_rejects_an_endpoint_kind_not_declared_by_its_installed_type(
    test_db_session: AsyncSession,
    test_project_id: UUID,
    test_user: User,
) -> None:
    """A caller cannot invent an endpoint family for a plugin connection."""
    integration_type_revision_id = await _installed_type_revision(test_db_session, test_user)
    service = PluginConnectionService(test_db_session)

    with pytest.raises(PluginConnectionError) as raised:
        await service.create(
            PluginConnectionCreate(
                name="Unexpected endpoint",
                project_id=test_project_id,
                integration_type_revision_id=integration_type_revision_id,
                endpoint_kind="arbitrary-external-service",
            ),
            actor_id=test_user.id,
        )

    assert raised.value.code == "endpoint-kind-not-declared"


@pytest.mark.asyncio
async def test_connection_resolution_requires_its_exact_enabled_type_revision(
    test_db_session: AsyncSession,
    test_project_id: UUID,
    test_user: User,
) -> None:
    """Resolution never substitutes a disabled connection or another type revision."""
    integration_type_revision_id = await _installed_type_revision(test_db_session, test_user)
    service = PluginConnectionService(test_db_session)
    connection = await service.create(
        PluginConnectionCreate(
            name="GitHub Enterprise",
            project_id=test_project_id,
            integration_type_revision_id=integration_type_revision_id,
            endpoint_kind="github-enterprise",
        ),
        actor_id=test_user.id,
    )

    with pytest.raises(PluginConnectionError) as mismatched:
        await service.resolve_enabled(
            connection.id,
            project_id=test_project_id,
            integration_type_revision_id=uuid4(),
        )
    assert mismatched.value.code == "connection-type-mismatch"

    connection.enabled = False
    test_db_session.add(connection)
    await test_db_session.flush()
    with pytest.raises(PluginConnectionError) as disabled:
        await service.resolve_enabled(
            connection.id,
            project_id=test_project_id,
            integration_type_revision_id=integration_type_revision_id,
        )
    assert disabled.value.code == "connection-disabled"


def test_connection_create_rejects_configuration_or_credential_input() -> None:
    """The first connection record cannot accept a secret-bearing payload."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PluginConnectionCreate.model_validate(
            {
                "name": "GitHub Cloud",
                "project_id": "00000000-0000-4000-8000-000000000000",
                "integration_type_revision_id": "00000000-0000-4000-8000-000000000001",
                "endpoint_kind": "github-cloud",
                "configuration": {"endpointOrigin": "https://github.com"},
                "credential_id": "00000000-0000-4000-8000-000000000002",
            }
        )
