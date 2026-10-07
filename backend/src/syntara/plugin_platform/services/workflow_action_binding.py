"""Publish-time pinning of enabled installed actions for workflow revisions.

The service is intentionally not a workflow-definition parser or runtime
adapter. A later generic external-action node will construct the bounded
references accepted here. When that reference selects a project connection,
this service resolves its workflow project server-side and checks the exact
installed integration-type revision before it persists the selection.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import select

from syntara.core.jsonb_limits import validate_jsonb_size
from syntara.plugin_platform.exceptions import (
    InstalledActionNotFoundError,
    PluginConnectionError,
    WorkflowActionBindingError,
    WorkloadRequestCompositionError,
)
from syntara.plugin_platform.models.workflow_action_binding import WorkflowActionBinding
from syntara.plugin_platform.services.workload_request import validate_installed_action_inputs
from syntara.workflows.models import Workflow, WorkflowVersion

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

    from syntara.plugin_platform.services.plugin_connection_service import ResolvedPluginConnection
    from syntara.plugin_platform.services.registry_service import ResolvedInstalledAction


_NODE_ID_PATTERN = r"^[a-zA-Z_][a-zA-Z0-9_]*$"
_STEP_REVISION_PATTERN = r"^[a-z0-9][a-z0-9.-]{2,190}:[a-f0-9]{64}$"
_MAX_INPUT_BYTES = 1_048_576
_BINDING_SET_CONFLICT = "binding-set-conflict"
_DUPLICATE_NODE_ID = "duplicate-node-id"
_ACTION_NOT_ENABLED = "action-not-enabled"
_MISSING_REGISTRY_GENERATION = "missing-registry-generation"
_BINDING_CONFLICT = "binding-conflict"
_DYNAMIC_INPUTS_NOT_SUPPORTED = "dynamic-inputs-not-supported"
_BINDING_NOT_FOUND = "binding-not-found"
_PINNED_ROUTE_MISMATCH = "pinned-route-mismatch"
_CONNECTION_NOT_SUPPORTED_BY_ACTION = "connection-not-supported-by-action"
_CONNECTION_RESOLUTION_UNAVAILABLE = "connection-resolution-unavailable"
_CONNECTION_NOT_AVAILABLE = "connection-not-available"
_WORKFLOW_PROJECT_NOT_FOUND = "workflow-project-not-found"


class EnabledInstalledActionResolver(Protocol):
    """Resolve only actions selectable in the active registry generation."""

    async def resolve_enabled_action(self, step_revision_id: str) -> ResolvedInstalledAction:
        """Return the server-owned route for one enabled installed action."""


class PinnedInstalledActionResolver(Protocol):
    """Read an immutable action revision already selected at publication."""

    async def resolve_pinned_action(self, step_revision_id: str) -> ResolvedInstalledAction:
        """Return the server-owned route without active-generation gating."""


class EnabledPluginConnectionResolver(Protocol):
    """Resolve only an enabled connection in the workflow's project."""

    async def resolve_enabled(
        self,
        connection_id: UUID,
        *,
        project_id: UUID,
        integration_type_revision_id: UUID,
    ) -> ResolvedPluginConnection:
        """Return a trusted connection identity for an exact installed type."""


class PublishedWorkflowActionResolver(Protocol):
    """Resolve one route from a durable workflow-step binding only."""

    async def resolve_published_action(
        self,
        workflow_version_id: UUID,
        node_id: str,
    ) -> ResolvedInstalledAction:
        """Return the immutable route selected for the published workflow step."""


class ExternalActionReference(BaseModel):
    """Bounded workflow data used to pin one external action at publication.

    This intentionally has no image, runtime, credential, grant, policy, or
    timeout field. A workflow may name one existing project connection, but
    the binding service resolves that identity server-side before using it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1, max_length=128, pattern=_NODE_ID_PATTERN)
    step_revision_id: str = Field(min_length=69, max_length=256, pattern=_STEP_REVISION_PATTERN)
    plugin_connection_id: UUID | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)

    @field_validator("inputs")
    @classmethod
    def _validate_json_inputs(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Accept one bounded JSON object and isolate later mutation from callers."""
        try:
            json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as error:
            msg = "external action inputs must be JSON serializable"
            raise ValueError(msg) from error
        validate_jsonb_size(value, field_name="external action inputs", max_bytes=_MAX_INPUT_BYTES)
        return deepcopy(value)


@dataclass(frozen=True)
class ResolvedActionBinding:
    """Trusted action route resolved while binding a workflow revision."""

    reference: ExternalActionReference
    action: ResolvedInstalledAction
    plugin_connection_id: UUID | None


class WorkflowActionBindingService:
    """Persist immutable action route snapshots inside the caller's transaction."""

    def __init__(
        self,
        session: AsyncSession,
        action_resolver: EnabledInstalledActionResolver,
        connection_resolver: EnabledPluginConnectionResolver | None = None,
    ) -> None:
        """Use only Syntara-owned action and optional connection resolvers."""
        self._session = session
        self._action_resolver = action_resolver
        self._connection_resolver = connection_resolver

    async def bind_for_publication(
        self,
        workflow_version_id: UUID,
        references: Sequence[ExternalActionReference],
    ) -> tuple[WorkflowActionBinding, ...]:
        """Validate and stage immutable action bindings without committing.

        Resolving every reference before querying or adding records ensures a
        failed publication cannot leave a partial route set in the surrounding
        workflow transaction. Calling this method again for the same immutable
        version is idempotent only when every stored snapshot still matches.
        """
        resolved = await self._resolve_references(workflow_version_id, references)
        existing = await self._existing_bindings(workflow_version_id)
        expected_node_ids = {binding.reference.node_id for binding in resolved}
        if set(existing) - expected_node_ids:
            raise WorkflowActionBindingError(_BINDING_SET_CONFLICT)

        staged: list[WorkflowActionBinding] = []
        for binding in resolved:
            current = existing.get(binding.reference.node_id)
            if current is not None:
                _require_matching_binding(current, binding)
                staged.append(current)
                continue
            staged.append(_binding_record(workflow_version_id, binding))

        new_bindings = [binding for binding in staged if binding.node_id not in existing]
        if new_bindings:
            self._session.add_all(new_bindings)
        return tuple(staged)

    async def _resolve_references(
        self,
        workflow_version_id: UUID,
        references: Sequence[ExternalActionReference],
    ) -> list[ResolvedActionBinding]:
        """Resolve all enabled action routes and validate only static input data."""
        seen_node_ids: set[str] = set()
        resolved: list[ResolvedActionBinding] = []
        project_id: UUID | None = None
        for reference in references:
            if reference.node_id in seen_node_ids:
                raise WorkflowActionBindingError(_DUPLICATE_NODE_ID, node_id=reference.node_id)
            seen_node_ids.add(reference.node_id)
            try:
                action = await self._action_resolver.resolve_enabled_action(reference.step_revision_id)
            except InstalledActionNotFoundError as error:
                raise WorkflowActionBindingError(_ACTION_NOT_ENABLED, node_id=reference.node_id) from error
            _reject_templates(reference.inputs, node_id=reference.node_id)
            try:
                validate_installed_action_inputs(action, reference.inputs)
            except WorkloadRequestCompositionError as error:
                code = f"{error.code}-inputs"
                raise WorkflowActionBindingError(code, node_id=reference.node_id) from error
            if action.registry_generation is None:
                raise WorkflowActionBindingError(_MISSING_REGISTRY_GENERATION, node_id=reference.node_id)
            if (
                reference.plugin_connection_id is not None
                and self._connection_resolver is not None
                and project_id is None
            ):
                project_id = await self._workflow_project_id(workflow_version_id)
            plugin_connection_id = await self._resolve_connection_id(reference, action, project_id)
            resolved.append(
                ResolvedActionBinding(
                    reference=reference,
                    action=action,
                    plugin_connection_id=plugin_connection_id,
                )
            )
        return resolved

    async def _existing_bindings(self, workflow_version_id: UUID) -> dict[str, WorkflowActionBinding]:
        """Read the complete existing binding set for idempotence and conflict checks."""
        result = await self._session.exec(
            select(WorkflowActionBinding).where(WorkflowActionBinding.workflow_version_id == workflow_version_id)
        )
        return {binding.node_id: binding for binding in result.all()}

    async def _resolve_connection_id(
        self,
        reference: ExternalActionReference,
        action: ResolvedInstalledAction,
        project_id: UUID | None,
    ) -> UUID | None:
        """Resolve a selected connection against the workflow's trusted project scope."""
        if reference.plugin_connection_id is None:
            return None
        if action.integration_type_revision_id is None:
            raise WorkflowActionBindingError(_CONNECTION_NOT_SUPPORTED_BY_ACTION, node_id=reference.node_id)
        if self._connection_resolver is None or project_id is None:
            raise WorkflowActionBindingError(_CONNECTION_RESOLUTION_UNAVAILABLE, node_id=reference.node_id)
        try:
            connection = await self._connection_resolver.resolve_enabled(
                reference.plugin_connection_id,
                project_id=project_id,
                integration_type_revision_id=action.integration_type_revision_id,
            )
        except PluginConnectionError as error:
            raise WorkflowActionBindingError(_CONNECTION_NOT_AVAILABLE, node_id=reference.node_id) from error
        return connection.connection_id

    async def _workflow_project_id(self, workflow_version_id: UUID) -> UUID:
        """Read the workflow project rather than trusting a publication caller."""
        result = await self._session.exec(
            select(Workflow.project_id)
            .join(WorkflowVersion, WorkflowVersion.workflow_id == Workflow.id)  # type: ignore[arg-type]
            .where(WorkflowVersion.id == workflow_version_id)
        )
        project_id = result.one_or_none()
        if project_id is None:
            raise WorkflowActionBindingError(_WORKFLOW_PROJECT_NOT_FOUND)
        return project_id


class WorkflowActionBindingResolver:
    """Read and verify the immutable action route bound to a workflow step.

    The generic Temporal activity must receive this resolver rather than a
    direct action-revision resolver. That means a trusted caller cannot select
    any installed action merely by naming its revision: it must use the exact
    route that workflow publication recorded for the selected workflow step.
    """

    def __init__(self, session: AsyncSession, action_resolver: PinnedInstalledActionResolver) -> None:
        """Use the caller transaction and immutable action lookup supplied by Syntara."""
        self._session = session
        self._action_resolver = action_resolver

    async def resolve_published_action(
        self,
        workflow_version_id: UUID,
        node_id: str,
    ) -> ResolvedInstalledAction:
        """Return one route only after it matches its durable publication snapshot."""
        result = await self._session.exec(
            select(WorkflowActionBinding).where(
                WorkflowActionBinding.workflow_version_id == workflow_version_id,
                WorkflowActionBinding.node_id == node_id,
            )
        )
        binding = result.one_or_none()
        if binding is None:
            raise WorkflowActionBindingError(_BINDING_NOT_FOUND, node_id=node_id)
        try:
            action = await self._action_resolver.resolve_pinned_action(binding.step_revision_id)
        except InstalledActionNotFoundError as error:
            raise WorkflowActionBindingError(_BINDING_NOT_FOUND, node_id=node_id) from error
        _require_pinned_route_match(binding, action)
        return action


def _binding_record(workflow_version_id: UUID, binding: ResolvedActionBinding) -> WorkflowActionBinding:
    """Create one uncommitted immutable route snapshot from trusted installed state."""
    action = binding.action
    if action.registry_generation is None:
        raise WorkflowActionBindingError(_MISSING_REGISTRY_GENERATION, node_id=binding.reference.node_id)
    return WorkflowActionBinding(
        workflow_version_id=workflow_version_id,
        node_id=binding.reference.node_id,
        step_revision_id=action.step_revision_id,
        plugin_digest=action.plugin_digest,
        runtime_kind=action.runtime_kind,
        runtime_abi=action.runtime_abi,
        image_digest=action.image_digest,
        integration_type_revision_id=action.integration_type_revision_id,
        plugin_connection_id=binding.plugin_connection_id,
        runner_profile_revision_id=action.runner_profile_revision_id,
        registry_generation=action.registry_generation,
    )


def _require_matching_binding(
    current: WorkflowActionBinding,
    resolved: ResolvedActionBinding,
) -> None:
    """Reject a changed route for a version that already has durable bindings."""
    action = resolved.action
    expected = (
        action.step_revision_id,
        action.plugin_digest,
        action.runtime_kind,
        action.runtime_abi,
        action.image_digest,
        action.integration_type_revision_id,
        resolved.plugin_connection_id,
        action.runner_profile_revision_id,
        action.registry_generation,
    )
    actual = (
        current.step_revision_id,
        current.plugin_digest,
        current.runtime_kind,
        current.runtime_abi,
        current.image_digest,
        current.integration_type_revision_id,
        current.plugin_connection_id,
        current.runner_profile_revision_id,
        current.registry_generation,
    )
    if actual != expected:
        raise WorkflowActionBindingError(_BINDING_CONFLICT, node_id=resolved.reference.node_id)


def _require_pinned_route_match(binding: WorkflowActionBinding, action: ResolvedInstalledAction) -> None:
    """Fail closed if immutable installed rows no longer match publication evidence."""
    expected = (
        binding.step_revision_id,
        binding.plugin_digest,
        binding.runtime_kind,
        binding.runtime_abi,
        binding.image_digest,
        binding.integration_type_revision_id,
        binding.runner_profile_revision_id,
    )
    actual = (
        action.step_revision_id,
        action.plugin_digest,
        action.runtime_kind,
        action.runtime_abi,
        action.image_digest,
        action.integration_type_revision_id,
        action.runner_profile_revision_id,
    )
    if actual != expected:
        raise WorkflowActionBindingError(_PINNED_ROUTE_MISMATCH, node_id=binding.node_id)


def _reject_templates(value: Mapping[str, Any], *, node_id: str) -> None:
    """Keep the first static-input demo explicit until expression typing lands."""
    if _contains_template(value):
        raise WorkflowActionBindingError(_DYNAMIC_INPUTS_NOT_SUPPORTED, node_id=node_id)


def _contains_template(value: Any) -> bool:  # noqa: ANN401
    """Recognize Syntara template syntax without evaluating arbitrary content."""
    if isinstance(value, Mapping):
        return any(_contains_template(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_template(item) for item in value)
    return isinstance(value, str) and "${" in value and "}" in value
