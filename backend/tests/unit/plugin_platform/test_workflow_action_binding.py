"""Unit coverage for durable publication-time action-route pinning."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from syntara.plugin_platform.exceptions import InstalledActionNotFoundError, WorkflowActionBindingError
from syntara.plugin_platform.models.workflow_action_binding import WorkflowActionBinding
from syntara.plugin_platform.services.registry_service import ResolvedInstalledAction
from syntara.plugin_platform.services.workflow_action_binding import (
    ExternalActionReference,
    WorkflowActionBindingResolver,
    WorkflowActionBindingService,
)

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession


def _digest(character: str) -> str:
    """Build a deterministic test-only SHA-256 digest."""
    return f"sha256:{character * 64}"


def _action() -> ResolvedInstalledAction:
    """Return the installed GitHub action compiled from its YAML input contract."""
    return ResolvedInstalledAction(
        step_revision_id=f"syntara.github.create-issue:{'a' * 64}",
        plugin_digest=_digest("b"),
        runtime_kind="platform-runner",
        runtime_abi="syntara.container/v1alpha1",
        image_digest=f"quay.io/syntara/http-runner@{_digest('c')}",
        integration_type_revision_id=UUID("12345678-1234-5678-1234-567812345679"),
        runner_profile_revision_id=UUID("12345678-1234-5678-1234-567812345678"),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["owner", "repository", "title"],
            "properties": {
                "owner": {"type": "string"},
                "repository": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
        },
        output_schema={"type": "object"},
        error_schema={"type": "object"},
        canonical_definition={"kind": "Action", "metadata": {"name": "create-issue"}},
        registry_generation=7,
    )


@dataclass
class _Resolver:
    """Resolve one enabled action and record the requested immutable revision."""

    action: ResolvedInstalledAction
    calls: list[str] = field(default_factory=list)

    async def resolve_enabled_action(self, step_revision_id: str) -> ResolvedInstalledAction:
        """Return only the configured active action."""
        self.calls.append(step_revision_id)
        if step_revision_id != self.action.step_revision_id:
            raise InstalledActionNotFoundError
        return self.action

    async def resolve_pinned_action(self, step_revision_id: str) -> ResolvedInstalledAction:
        """Return the installed immutable action even after a later disable."""
        self.calls.append(step_revision_id)
        if step_revision_id != self.action.step_revision_id:
            raise InstalledActionNotFoundError
        return self.action


@dataclass
class _Result:
    """Minimal result adapter used by the no-I/O unit test session."""

    bindings: list[WorkflowActionBinding]

    def all(self) -> list[WorkflowActionBinding]:
        """Return the configured query rows."""
        return self.bindings

    def one_or_none(self) -> WorkflowActionBinding | None:
        """Return the only configured binding, like the SQLModel result adapter."""
        if not self.bindings:
            return None
        return self.bindings[0]


@dataclass
class _Session:
    """Record session interactions without requiring a database in unit tests."""

    existing: list[WorkflowActionBinding] = field(default_factory=list)
    added: list[WorkflowActionBinding] = field(default_factory=list)

    async def exec(self, _statement: object) -> _Result:
        """Return the preconfigured existing binding set."""
        return _Result(self.existing)

    def add_all(self, bindings: list[WorkflowActionBinding]) -> None:
        """Record staged bindings; the caller owns commit/rollback."""
        self.added.extend(bindings)


def _reference(action: ResolvedInstalledAction | None = None) -> ExternalActionReference:
    """Return a static GitHub issue request valid for the installed schema."""
    resolved = action or _action()
    return ExternalActionReference(
        node_id="create_issue",
        step_revision_id=resolved.step_revision_id,
        inputs={
            "owner": "syntara-orchestration",
            "repository": "syntara",
            "title": "Created by Syntara",
        },
    )


@pytest.mark.asyncio
async def test_binding_service_pins_only_server_resolved_route_metadata() -> None:
    """Workflow input cannot supply an image, policy, connection, or credential route."""
    action = _action()
    session = _Session()
    service = WorkflowActionBindingService(cast("AsyncSession", session), _Resolver(action))

    bindings = await service.bind_for_publication(uuid4(), [_reference(action)])

    assert len(bindings) == 1
    binding = bindings[0]
    assert binding.step_revision_id == action.step_revision_id
    assert binding.plugin_digest == action.plugin_digest
    assert binding.image_digest == action.image_digest
    assert binding.runtime_abi == action.runtime_abi
    assert binding.integration_type_revision_id == action.integration_type_revision_id
    assert binding.registry_generation == 7
    assert session.added == [binding]


@pytest.mark.asyncio
async def test_binding_service_rejects_invalid_action_input_before_staging_any_route() -> None:
    """The installed YAML-derived schema controls static workflow configuration."""
    action = _action()
    session = _Session()
    service = WorkflowActionBindingService(cast("AsyncSession", session), _Resolver(action))
    reference = _reference(action).model_copy(update={"inputs": {"owner": "syntara-orchestration"}})

    with pytest.raises(WorkflowActionBindingError) as raised:
        await service.bind_for_publication(uuid4(), [reference])

    assert raised.value.code == "input-inputs"
    assert session.added == []


@pytest.mark.asyncio
async def test_binding_service_rejects_dynamic_input_until_expression_typing_is_available() -> None:
    """The first demo accepts static structured values only, rather than guessing types."""
    action = _action()
    service = WorkflowActionBindingService(cast("AsyncSession", _Session()), _Resolver(action))
    reference = _reference(action).model_copy(
        update={"inputs": {"owner": "syntara-orchestration", "repository": "syntara", "title": "${trigger.title}"}}
    )

    with pytest.raises(WorkflowActionBindingError) as raised:
        await service.bind_for_publication(uuid4(), [reference])

    assert raised.value.code == "dynamic-inputs-not-supported"


@pytest.mark.asyncio
async def test_binding_service_rejects_actions_not_enabled_for_publication() -> None:
    """Only a current active-generation member can be newly bound."""
    action = _action()
    service = WorkflowActionBindingService(cast("AsyncSession", _Session()), _Resolver(action))
    reference = _reference(action).model_copy(update={"step_revision_id": f"syntara.github.other:{'d' * 64}"})

    with pytest.raises(WorkflowActionBindingError) as raised:
        await service.bind_for_publication(uuid4(), [reference])

    assert raised.value.code == "action-not-enabled"


@pytest.mark.asyncio
async def test_binding_service_fails_closed_when_a_connection_cannot_be_resolved() -> None:
    """A workflow cannot persist a connection ID unless Syntara can verify it."""
    action = _action()
    service = WorkflowActionBindingService(cast("AsyncSession", _Session()), _Resolver(action))
    reference = _reference(action).model_copy(update={"plugin_connection_id": uuid4()})

    with pytest.raises(WorkflowActionBindingError) as raised:
        await service.bind_for_publication(uuid4(), [reference])

    assert raised.value.code == "connection-resolution-unavailable"


@pytest.mark.asyncio
async def test_binding_service_is_idempotent_for_the_same_immutable_route() -> None:
    """Republishing the same revision neither mutates nor duplicates its route record."""
    action = _action()
    workflow_version_id = uuid4()
    existing = WorkflowActionBinding(
        workflow_version_id=workflow_version_id,
        node_id="create_issue",
        step_revision_id=action.step_revision_id,
        plugin_digest=action.plugin_digest,
        runtime_kind=action.runtime_kind,
        runtime_abi=action.runtime_abi,
        image_digest=action.image_digest,
        integration_type_revision_id=action.integration_type_revision_id,
        runner_profile_revision_id=action.runner_profile_revision_id,
        registry_generation=7,
    )
    session = _Session(existing=[existing])
    service = WorkflowActionBindingService(cast("AsyncSession", session), _Resolver(action))

    bindings = await service.bind_for_publication(workflow_version_id, [_reference(action)])

    assert bindings == (existing,)
    assert session.added == []


@pytest.mark.asyncio
async def test_binding_resolver_uses_only_the_published_workflow_step_route() -> None:
    """The runtime composer cannot select an arbitrary installed action revision."""
    action = _action()
    workflow_version_id = uuid4()
    binding = WorkflowActionBinding(
        workflow_version_id=workflow_version_id,
        node_id="create_issue",
        step_revision_id=action.step_revision_id,
        plugin_digest=action.plugin_digest,
        runtime_kind=action.runtime_kind,
        runtime_abi=action.runtime_abi,
        image_digest=action.image_digest,
        integration_type_revision_id=action.integration_type_revision_id,
        runner_profile_revision_id=action.runner_profile_revision_id,
        registry_generation=7,
    )
    resolver = _Resolver(action)
    service = WorkflowActionBindingResolver(cast("AsyncSession", _Session(existing=[binding])), resolver)

    resolved = await service.resolve_published_action(workflow_version_id, "create_issue")

    assert resolved == action
    assert resolver.calls == [action.step_revision_id]


@pytest.mark.asyncio
async def test_binding_resolver_fails_closed_when_an_immutable_route_no_longer_matches() -> None:
    """A corrupted or invalid mutable route must never reach generic dispatch."""
    action = _action()
    workflow_version_id = uuid4()
    binding = WorkflowActionBinding(
        workflow_version_id=workflow_version_id,
        node_id="create_issue",
        step_revision_id=action.step_revision_id,
        plugin_digest=action.plugin_digest,
        runtime_kind=action.runtime_kind,
        runtime_abi=action.runtime_abi,
        image_digest=action.image_digest,
        integration_type_revision_id=action.integration_type_revision_id,
        runner_profile_revision_id=action.runner_profile_revision_id,
        registry_generation=7,
    )
    altered_action = replace(action, integration_type_revision_id=uuid4())
    service = WorkflowActionBindingResolver(
        cast("AsyncSession", _Session(existing=[binding])),
        _Resolver(altered_action),
    )

    with pytest.raises(WorkflowActionBindingError) as raised:
        await service.resolve_published_action(workflow_version_id, "create_issue")

    assert raised.value.code == "pinned-route-mismatch"


def test_external_action_reference_forbids_execution_route_fields() -> None:
    """The generic envelope permits an identity, never a browser-supplied image route."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ExternalActionReference.model_validate(
            {
                "node_id": "create_issue",
                "step_revision_id": f"syntara.github.create-issue:{'a' * 64}",
                "inputs": {},
                "image_digest": f"quay.io/attacker/image@{_digest('e')}",
            }
        )
