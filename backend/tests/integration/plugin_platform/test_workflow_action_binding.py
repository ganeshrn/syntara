"""Database integration coverage for durable workflow action-route bindings."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlmodel import select

from syntara.plugin_platform.models.registry import PluginRegistryState
from syntara.plugin_platform.models.workflow_action_binding import WorkflowActionBinding
from syntara.plugin_platform.services.plugin_connection_service import (
    PluginConnectionCreate,
    PluginConnectionService,
)
from syntara.plugin_platform.services.published_action_composer import (
    PublishedActionReference,
    PublishedActionWorkloadComposer,
    ServerResolvedActionContext,
)
from syntara.plugin_platform.services.registry_service import (
    InstalledActionCandidate,
    InstalledCredentialTypeCandidate,
    InstalledIntegrationTypeCandidate,
    PluginRegistryService,
    VerifiedPluginCandidate,
)
from syntara.plugin_platform.services.workflow_action_binding import (
    ExternalActionReference,
    WorkflowActionBindingResolver,
    WorkflowActionBindingService,
)
from syntara.workflows.models import Workflow, WorkflowVersion

if TYPE_CHECKING:
    from uuid import UUID

    from sqlmodel.ext.asyncio.session import AsyncSession

    from syntara.core.models import User


def _digest(character: str) -> str:
    """Build a deterministic test-only SHA-256 digest."""
    return f"sha256:{character * 64}"


def _candidate() -> VerifiedPluginCandidate:
    """Return a minimal verified custom-workload action fixture."""
    return VerifiedPluginCandidate(
        namespace="syntara",
        name="echo",
        version="1.0.0",
        display_name="Echo",
        description="Echo plugin.",
        artifact_repository="syntara/plugins/echo",
        artifact_digest=_digest("a"),
        canonical_descriptor={"kind": "PluginDescriptor", "spec": {"targets": []}},
        actions=(
            InstalledActionCandidate(
                target_name="echo",
                display_name="Echo",
                description="Returns its message.",
                revision_digest=_digest("b"),
                runtime_kind="custom-workload",
                image_digest=f"quay.io/syntara/echo@{_digest('c')}",
                integration_type_id="syntara.echo.connection",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["message"],
                    "properties": {"message": {"type": "string"}},
                },
                output_schema={"type": "object"},
                error_schema={"type": "object"},
                canonical_definition={"kind": "Action", "metadata": {"name": "echo"}},
            ),
        ),
        integration_types=(
            InstalledIntegrationTypeCandidate(
                type_id="syntara.echo.connection",
                display_name="Echo connection",
                description="Non-secret connection metadata for the echo fixture.",
                revision_digest=_digest("d"),
                endpoint_kinds=("https",),
                credential_type_ids=("syntara.echo.service-token",),
                configuration_schema={"type": "object", "additionalProperties": False},
                canonical_definition={"kind": "IntegrationType", "id": "syntara.echo.connection"},
            ),
        ),
        credential_types=(
            InstalledCredentialTypeCandidate(
                type_id="syntara.echo.service-token",
                integration_type_id="syntara.echo.connection",
                display_name="Echo service token",
                description="Secret-bearing credential recipe fixture.",
                revision_digest=_digest("e"),
                credential_schema={"type": "object", "required": ["token"]},
                secret_field_names=("token",),
                issuance={"kind": "static-bearer.v1"},
                canonical_definition={"kind": "CredentialRecipe", "id": "syntara.echo.service-token"},
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


async def _workflow_version(
    session: AsyncSession,
    user: User,
    project_id: UUID,
) -> WorkflowVersion:
    """Persist a minimal workflow revision so the binding's foreign key is real."""
    workflow = Workflow(
        id=uuid4(),
        name="action-binding-workflow",
        created_by=user.id,
        project_id=project_id,
    )
    session.add(workflow)
    await session.flush()
    version = WorkflowVersion(
        id=uuid4(),
        workflow_id=workflow.id,
        version=1,
        schema_version="2.0.0",
        workflow_definition={
            "schema_version": "2.0.0",
            "name": workflow.name,
            "triggers": [],
            "nodes": [],
            "edges": [],
        },
        created_by=user.id,
    )
    session.add(version)
    await session.commit()
    return version


@pytest.mark.asyncio
async def test_published_binding_persists_server_resolved_route_after_plugin_is_disabled(
    test_db_session: AsyncSession,
    test_user: User,
    test_project_id: UUID,
) -> None:
    """New publication needs an enabled action; an existing route remains immutable."""
    registry = PluginRegistryService(test_db_session)
    installed = await registry.install_verified(_candidate(), actor_id=test_user.id)
    action_id = "syntara.echo.echo:" + "b" * 64
    version = await _workflow_version(test_db_session, test_user, test_project_id)
    action = await registry.resolve_enabled_action(action_id)
    assert action.integration_type_revision_id is not None
    connection = await PluginConnectionService(test_db_session).create(
        PluginConnectionCreate(
            name="Echo test connection",
            project_id=test_project_id,
            integration_type_revision_id=action.integration_type_revision_id,
            endpoint_kind="https",
        ),
        actor_id=test_user.id,
    )

    binding_service = WorkflowActionBindingService(
        test_db_session,
        registry,
        PluginConnectionService(test_db_session),
    )
    bindings = await binding_service.bind_for_publication(
        version.id,
        [
            ExternalActionReference(
                node_id="echo_message",
                step_revision_id=action_id,
                plugin_connection_id=connection.id,
                inputs={"message": "hello"},
            )
        ],
    )
    await test_db_session.commit()

    stored = (
        await test_db_session.exec(
            select(WorkflowActionBinding).where(WorkflowActionBinding.workflow_version_id == version.id)
        )
    ).one()
    await registry.set_enabled(installed.plugin_installation_id, enabled=False, actor_id=test_user.id)
    composer = PublishedActionWorkloadComposer(WorkflowActionBindingResolver(test_db_session, registry))
    request = await composer.compose(
        PublishedActionReference(workflow_version_id=version.id, node_id="echo_message"),
        ServerResolvedActionContext(
            invocation_id="binding-integration-invocation",
            workflow_run_id="binding-integration-run",
            integration_handle="connection-1",
            capability_grant_id="grant-1",
            execution_policy_id="policy-1",
            policy_revision="revision-1",
            timeout_seconds=60,
        ),
        {"message": "hello"},
    )

    assert bindings[0].id == stored.id
    assert stored.step_revision_id == action_id
    assert stored.registry_generation == 1
    assert stored.integration_type_revision_id is not None
    assert stored.plugin_connection_id == connection.id
    assert bindings[0].integration_type_revision_id == stored.integration_type_revision_id
    assert bindings[0].plugin_connection_id == connection.id
    assert request["workItem"]["stepRevisionId"] == action_id
    assert request["workItem"]["imageDigest"] == stored.image_digest
    assert request["workItem"]["inputs"] == {"message": "hello"}
