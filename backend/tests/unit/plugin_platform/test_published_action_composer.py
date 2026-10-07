"""Tests for the installed-action application composition seam."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from syntara.plugin_platform.exceptions import WorkflowActionBindingError, WorkloadRequestCompositionError
from syntara.plugin_platform.services.published_action_composer import (
    PublishedActionReference,
    PublishedActionWorkloadComposer,
    ServerResolvedActionContext,
)
from syntara.plugin_platform.services.registry_service import ResolvedInstalledAction


def _digest(character: str) -> str:
    """Build a deterministic test-only SHA-256 digest."""
    return f"sha256:{character * 64}"


def _action() -> ResolvedInstalledAction:
    """Return a credentialless installed action with a server-owned image route."""
    return ResolvedInstalledAction(
        step_revision_id=f"syntara.example.echo:{'a' * 64}",
        plugin_digest=_digest("b"),
        runtime_kind="custom-workload",
        runtime_abi="syntara.container/v1alpha1",
        image_digest=f"quay.io/syntara/example@{_digest('c')}",
        integration_type_revision_id=None,
        runner_profile_revision_id=None,
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["message"],
            "properties": {"message": {"type": "string"}},
        },
        output_schema={"type": "object"},
        error_schema={"type": "object"},
        canonical_definition={"kind": "Action", "metadata": {"name": "echo"}},
        registry_generation=7,
    )


@dataclass
class _Resolver:
    """Record the workflow-step binding requested by the composer."""

    action: ResolvedInstalledAction
    routes: set[tuple[UUID, str]]
    calls: list[tuple[UUID, str]]

    async def resolve_published_action(self, workflow_version_id: UUID, node_id: str) -> ResolvedInstalledAction:
        """Return the configured route only for a known published workflow step."""
        reference = (workflow_version_id, node_id)
        self.calls.append(reference)
        if reference not in self.routes:
            code = "binding-not-found"
            raise WorkflowActionBindingError(code, node_id=node_id)
        return self.action


def _context() -> ServerResolvedActionContext:
    """Return non-secret values that a future server-side policy resolver owns."""
    return ServerResolvedActionContext(
        invocation_id="invocation-1",
        workflow_run_id="workflow-run-1",
        integration_handle="connection-1",
        capability_grant_id="grant-1",
        execution_policy_id="policy-1",
        policy_revision="revision-1",
        timeout_seconds=60,
        effective_capabilities=("network.example",),
    )


@pytest.mark.asyncio
async def test_composer_resolves_the_published_workflow_step_before_it_builds_the_request() -> None:
    """A workflow route is always obtained from its durable step binding."""
    action = _action()
    reference = PublishedActionReference(workflow_version_id=uuid4(), node_id="echo_message")
    resolver = _Resolver(action=action, routes={(reference.workflow_version_id, reference.node_id)}, calls=[])
    composer = PublishedActionWorkloadComposer(resolver)

    request = await composer.compose(
        reference,
        _context(),
        {"message": "hello"},
    )

    assert resolver.calls == [(reference.workflow_version_id, reference.node_id)]
    assert request["workItem"]["stepRevisionId"] == action.step_revision_id
    assert request["workItem"]["imageDigest"] == action.image_digest
    assert request["workItem"]["inputs"] == {"message": "hello"}
    assert request["credentialLeases"] == []


@pytest.mark.asyncio
async def test_composer_stops_when_the_published_workflow_step_binding_is_missing() -> None:
    """An arbitrary installed action cannot bypass durable publication routing."""
    action = _action()
    published_reference = PublishedActionReference(workflow_version_id=uuid4(), node_id="echo_message")
    resolver = _Resolver(
        action=action,
        routes={(published_reference.workflow_version_id, published_reference.node_id)},
        calls=[],
    )
    composer = PublishedActionWorkloadComposer(resolver)
    missing_reference = PublishedActionReference(workflow_version_id=uuid4(), node_id="unbound_step")

    with pytest.raises(WorkflowActionBindingError) as raised:
        await composer.compose(
            missing_reference,
            _context(),
            {"message": "hello"},
        )

    assert raised.value.code == "binding-not-found"
    assert resolver.calls == [(missing_reference.workflow_version_id, missing_reference.node_id)]


@pytest.mark.asyncio
async def test_composer_rejects_inputs_that_do_not_match_the_installed_action_schema() -> None:
    """Resolved workflow values are still validated before any EP request exists."""
    action = _action()
    reference = PublishedActionReference(workflow_version_id=uuid4(), node_id="echo_message")
    composer = PublishedActionWorkloadComposer(
        _Resolver(action=action, routes={(reference.workflow_version_id, reference.node_id)}, calls=[])
    )

    with pytest.raises(WorkloadRequestCompositionError) as raised:
        await composer.compose(
            reference,
            _context(),
            {"unexpected": "value"},
        )

    assert raised.value.code == "input"
