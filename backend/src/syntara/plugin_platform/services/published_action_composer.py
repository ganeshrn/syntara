"""Compose one container request from a published workflow-step binding.

The workflow-facing representation contains only the workflow revision and
workflow-step identity. This adapter resolves that binding against a durable
published route before it delegates to the lower-level request builder.
It intentionally has no browser, workflow-definition, database-session, or
Temporal dependency: publication and activity adapters own those boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from syntara.plugin_platform.services.workload_request import (
    TrustedActionInvocation,
    build_container_workload_request,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from syntara.plugin_platform.services.workflow_action_binding import PublishedWorkflowActionResolver


@dataclass(frozen=True)
class PublishedActionReference:
    """Identity of one external action route frozen for a workflow step.

    It deliberately excludes an action revision, image, runtime, policy,
    capability, integration, and credential data. A publication adapter must
    create its server-owned binding before a runtime activity can use it.
    """

    workflow_version_id: UUID
    node_id: str


@dataclass(frozen=True)
class ServerResolvedActionContext:
    """Non-secret execution state resolved by trusted control-plane services.

    Future connection and policy services create this value after checking the
    published workflow binding, project scope, and current authorization.  It
    must never be populated from a browser request or workflow expression.
    """

    invocation_id: str
    workflow_run_id: str
    integration_handle: str
    capability_grant_id: str
    execution_policy_id: str
    policy_revision: str
    timeout_seconds: int
    effective_capabilities: tuple[str, ...] = ()


class PublishedActionWorkloadComposer:
    """Join a published action reference to server-owned execution state.

    This is the application-composition seam between future workflow
    publication/runtime adapters and the generic ``ContainerWorkloadRequest``
    builder.  It cannot be given an image, action definition, or credentials.
    """

    def __init__(self, binding_resolver: PublishedWorkflowActionResolver) -> None:
        """Use the publication-binding resolver supplied by the activity adapter."""
        self._binding_resolver = binding_resolver

    async def compose(
        self,
        action_reference: PublishedActionReference,
        execution_context: ServerResolvedActionContext,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Resolve the published workflow-step route then compose one request."""
        action = await self._binding_resolver.resolve_published_action(
            action_reference.workflow_version_id,
            action_reference.node_id,
        )
        invocation = TrustedActionInvocation(
            action=action,
            invocation_id=execution_context.invocation_id,
            workflow_run_id=execution_context.workflow_run_id,
            integration_handle=execution_context.integration_handle,
            capability_grant_id=execution_context.capability_grant_id,
            execution_policy_id=execution_context.execution_policy_id,
            policy_revision=execution_context.policy_revision,
            timeout_seconds=execution_context.timeout_seconds,
            effective_capabilities=execution_context.effective_capabilities,
        )
        return build_container_workload_request(invocation, inputs)
