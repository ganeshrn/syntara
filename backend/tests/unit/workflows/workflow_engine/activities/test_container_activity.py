"""Tests for generic container action dispatch before plugin nodes are exposed."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from temporalio.exceptions import ApplicationError, CancelledError

from syntara.workflows.workflow_engine.activities.container_activity import (
    _completed_output,
    _raise_terminal_failure,
    _stable_work_item_identity,
    _validate_workload_request,
    execute_container_activity,
)


def _workload_request() -> dict[str, Any]:
    """Return a complete credentialless shared runtime request."""
    digest = "sha256:" + "a" * 64
    return {
        "apiVersion": "syntara.io/v1alpha1",
        "kind": "ContainerWorkloadRequest",
        "workItem": {
            "apiVersion": "syntara.io/v1alpha1",
            "kind": "ContainerStepWorkItem",
            "invocationId": "invocation-1",
            "workflowRunId": "workflow-run-1",
            "stepRevisionId": "acme.github.create-issue.v1",
            "pluginDigest": digest,
            "runtimeAbi": "syntara.container/v1alpha1",
            "imageDigest": "quay.io/acme/github@" + "sha256:" + "b" * 64,
            "integrationHandle": "connection-1",
            "inputs": {"title": "Example"},
            "runtimeContext": {
                "driver": "http.v1",
                "operation": {"method": "POST", "path": "/repos/{input.owner}/{input.repository}/issues"},
                "outputSchemaDigest": digest,
            },
            "credentialHandles": [],
            "capabilityGrantId": "grant-1",
            "executionPolicyId": "policy-1",
            "timeoutSeconds": 30,
            "requestHash": digest,
        },
        "policySnapshot": {
            "apiVersion": "syntara.io/v1alpha1",
            "kind": "ExecutionPolicySnapshot",
            "policyId": "policy-1",
            "revision": "revision-1",
            "requestHash": digest,
            "effectiveCapabilities": [],
        },
        "credentialLeases": [],
    }


def test_validate_workload_request_accepts_shared_credentialless_contract() -> None:
    """The activity uses the same runtime schema as EP before making an HTTP call."""
    _validate_workload_request(_workload_request())


@pytest.mark.parametrize("field", ["credentialLeases", "credentialHandles"])
def test_validate_workload_request_rejects_credential_delivery_until_broker_exists(field: str) -> None:
    """No generic action can silently accept a partial or plaintext secret path."""
    request = _workload_request()
    if field == "credentialLeases":
        request[field] = [{"unexpected": "lease"}]
    else:
        request["workItem"][field] = ["credential-handle-1"]

    with pytest.raises(ApplicationError, match="Credential-bearing"):
        _validate_workload_request(request)


def test_stable_work_item_identity_is_unchanged_for_temporal_activity_retry() -> None:
    """Temporal retries recover the same EP request rather than starting another action."""
    activity_info = SimpleNamespace(workflow_id="workflow-1", workflow_run_id="run-1", activity_id="action-1")
    with patch(
        "syntara.workflows.workflow_engine.activities.container_activity.activity.info",
        return_value=activity_info,
    ):
        first = _stable_work_item_identity()
        second = _stable_work_item_identity()

    assert first == second
    assert first[0].startswith("container-")


def test_terminal_result_mapping_fails_closed_for_object_references() -> None:
    """Object-reference retrieval needs its own authorized transfer path."""
    with pytest.raises(ApplicationError, match="not an inline object"):
        _completed_output({"result": {"object_reference": "external-object-1"}})


def test_terminal_failure_uses_safe_failure_classification() -> None:
    """The activity retains retryability but never exposes an implementation message."""
    with pytest.raises(ApplicationError) as raised:
        _raise_terminal_failure(
            {
                "status": "failed",
                "result": {
                    "error": "publisher supplied message that must not become workflow text",
                    "error_type": "ContainerWorkloadFailure",
                    "retryable": False,
                },
            }
        )

    assert "publisher" not in str(raised.value)


async def test_activity_submits_then_returns_inline_terminal_output() -> None:
    """The generic Temporal activity submits once and polls the durable EP work item."""
    project_id = uuid4()
    client = MagicMock()
    client.submit = AsyncMock(return_value={"id": str(uuid4()), "status": "pending", "result": None})
    client.get_by_request_id = AsyncMock(
        return_value={
            "id": str(uuid4()),
            "status": "completed",
            "result": {"output_schema_digest": "sha256:" + "c" * 64, "output": {"issue": 7}},
        }
    )
    client.close = AsyncMock()
    settings = SimpleNamespace(execution_plane_poll_interval_seconds=0.001)
    activity_info = SimpleNamespace(workflow_id="workflow-1", workflow_run_id="run-1", activity_id="action-1")

    with (
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity.activity.info",
            return_value=activity_info,
        ),
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity.activity.heartbeat",
        ),
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity.get_settings",
            return_value=settings,
        ),
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity._build_client",
            return_value=client,
        ),
    ):
        result = await execute_container_activity(_workload_request(), None, str(project_id))

    assert result == {"output": {"issue": 7}}
    assert client.submit.await_count == 1
    submitted_request = client.submit.await_args.kwargs["workload_request"]
    assert submitted_request["workItem"]["runtimeContext"]["driver"] == "http.v1"
    assert client.get_by_request_id.await_count == 1
    client.close.assert_awaited_once()


async def test_activity_relays_temporal_cancellation_using_stable_ep_identity() -> None:
    """A cancellation creates or updates the durable EP cancellation request before re-raising."""
    project_id = uuid4()
    client = MagicMock()
    client.submit = AsyncMock(return_value={"id": str(uuid4()), "status": "pending", "result": None})
    client.get_by_request_id = AsyncMock()
    client.cancel_by_request_id = AsyncMock(return_value={"id": str(uuid4()), "status": "cancel_requested"})
    client.close = AsyncMock()
    settings = SimpleNamespace(execution_plane_poll_interval_seconds=0.001)
    activity_info = SimpleNamespace(workflow_id="workflow-1", workflow_run_id="run-1", activity_id="action-1")

    with (
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity.activity.info",
            return_value=activity_info,
        ),
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity.activity.heartbeat",
        ),
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity.get_settings",
            return_value=settings,
        ),
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity._build_client",
            return_value=client,
        ),
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity.asyncio.sleep",
            side_effect=CancelledError("cancelled by Temporal"),
        ),
    ):
        with pytest.raises(CancelledError):
            await execute_container_activity(_workload_request(), None, str(project_id))

    client.cancel_by_request_id.assert_awaited_once()
    client.close.assert_awaited_once()
