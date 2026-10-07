"""Tests for the typed external-action workflow envelope and publish pinning."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError
from temporalio.exceptions import ApplicationError

from syntara.plugin_platform.exceptions import WorkflowActionBindingError
from syntara.workflows.exceptions import WorkflowPublishValidationError
from syntara.workflows.models import WorkflowVersion
from syntara.workflows.models.workflow_definition import WorkflowDefinition
from syntara.workflows.services.workflow_service import WorkflowService
from syntara.workflows.validators import workflow_validator
from syntara.workflows.workflow_engine.dynamic_workflow import OrchestratorWorkflow
from syntara.workflows.workflow_engine.graph import ActivityNode
from syntara.workflows.workflow_engine.models.workflow_definition import NodeType


def _step_revision_id() -> str:
    return "syntara.github.create-issue:" + "a" * 64


def _definition(*, parameters: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "schema_version": "2.0.0",
        "name": "External action",
        "triggers": [{"id": "start", "type": "manual_trigger", "parameters": {}}],
        "nodes": [
            {
                "id": "create_issue",
                "type": "external_action",
                "parameters": parameters
                or {
                    "step_revision_id": _step_revision_id(),
                    "inputs": {"title": "Plugin platform"},
                },
            }
        ],
        "edges": [{"from": "start", "to": "create_issue"}],
    }


def _service() -> WorkflowService:
    service = WorkflowService.__new__(WorkflowService)
    service.session = AsyncMock()
    service.user = MagicMock()
    return service


def test_external_action_node_accepts_only_the_safe_workflow_fields() -> None:
    """The stored node cannot include an image, command, policy, or credentials."""
    workflow = WorkflowDefinition.model_validate(_definition())
    action = workflow.nodes[0]

    assert action.type == "external_action"
    assert action.parameters.step_revision_id == _step_revision_id()

    invalid = _definition(
        parameters={
            "step_revision_id": _step_revision_id(),
            "inputs": {},
            "image": "quay.io/example/overridden@sha256:" + "b" * 64,
        }
    )
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(invalid)


def test_external_action_node_is_accepted_by_the_runtime_json_schema() -> None:
    """The persisted JSON schema and Pydantic authoring model accept the same envelope."""
    result = workflow_validator.collect_findings(_definition())

    assert result.error_count == 0


@pytest.mark.asyncio
async def test_publication_pins_external_action_through_the_server_owned_service() -> None:
    """Publication forwards only the typed action reference, never a runtime route."""
    service = _service()
    workflow_version = MagicMock(spec=WorkflowVersion)
    workflow_version.id = uuid4()

    with patch(
        "syntara.workflows.services.workflow_service.WorkflowActionBindingService.bind_for_publication",
        new_callable=AsyncMock,
    ) as bind_for_publication:
        await service._bind_external_actions_for_publication(workflow_version, _definition())

    call = bind_for_publication.await_args
    assert call is not None
    binding_version_id, references = call.args
    assert binding_version_id == workflow_version.id
    assert len(references) == 1
    assert references[0].node_id == "create_issue"
    assert references[0].step_revision_id == _step_revision_id()
    assert references[0].inputs == {"title": "Plugin platform"}


@pytest.mark.asyncio
async def test_publication_turns_binding_denial_into_a_workflow_validation_error() -> None:
    """A disabled or unavailable action blocks publication before the pointer moves."""
    service = _service()
    workflow_version = MagicMock(spec=WorkflowVersion)
    workflow_version.id = uuid4()

    with (
        patch(
            "syntara.workflows.services.workflow_service.WorkflowActionBindingService.bind_for_publication",
            new_callable=AsyncMock,
            side_effect=WorkflowActionBindingError("action-not-enabled", node_id="create_issue"),
        ),
        pytest.raises(WorkflowPublishValidationError) as raised,
    ):
        await service._bind_external_actions_for_publication(workflow_version, _definition())

    finding = raised.value.validation_result.findings[0]
    assert finding.node_id == "create_issue"
    assert finding.message.endswith("action-not-enabled")


@pytest.mark.asyncio
async def test_external_action_execution_fails_closed_until_runtime_context_exists() -> None:
    """An exposed node is never silently skipped or given a permissive route."""
    workflow = OrchestratorWorkflow.__new__(OrchestratorWorkflow)
    node = ActivityNode("create_issue", NodeType.EXTERNAL_ACTION, {})

    with pytest.raises(ApplicationError) as raised:
        await workflow._dispatch_node_to_executor(node, {}, MagicMock(), timeout_seconds=60)

    assert raised.value.type == "ExternalActionRuntimeUnavailable"
