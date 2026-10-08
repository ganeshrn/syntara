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
    _compose_local_poc_workload_request,
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


async def test_local_poc_composition_requires_explicit_catalog_and_execution_switches() -> None:
    """Local development cannot accidentally enable plugin execution."""
    request = {
        "kind": "PublishedExternalActionInvocation",
        "workflowVersionId": str(uuid4()),
        "nodeId": "hello",
        "inputs": {},
        "timeoutSeconds": 30,
    }
    settings = SimpleNamespace(
        plugin_platform_local_poc_enabled=True,
        plugin_platform_local_poc_execution_enabled=False,
    )

    with (
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity.get_settings",
            return_value=settings,
        ),
        pytest.raises(ApplicationError) as raised,
    ):
        await _compose_local_poc_workload_request(
            request,
            project_id=str(uuid4()),
            invocation_id="container-1",
            workflow_run_id="run-1",
        )

    assert raised.value.type == "ExternalActionRuntimeUnavailable"


async def test_local_poc_composition_rejects_any_integration_backed_action() -> None:
    """The local bridge never resolves an endpoint, connection, or credential."""
    request = {
        "kind": "PublishedExternalActionInvocation",
        "workflowVersionId": str(uuid4()),
        "nodeId": "hello",
        "inputs": {},
        "timeoutSeconds": 30,
    }
    settings = SimpleNamespace(
        plugin_platform_local_poc_enabled=True,
        plugin_platform_local_poc_execution_enabled=True,
    )
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    session_cm.__aexit__ = AsyncMock(return_value=False)
    resolver = MagicMock()
    resolver.resolve_published_action = AsyncMock(return_value=SimpleNamespace(integration_type_revision_id=uuid4()))

    with (
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity.get_settings",
            return_value=settings,
        ),
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity._session_factory",
            return_value=session_cm,
        ),
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity.WorkflowActionBindingResolver",
            return_value=resolver,
        ),
        pytest.raises(ApplicationError) as raised,
    ):
        await _compose_local_poc_workload_request(
            request,
            project_id=str(uuid4()),
            invocation_id="container-1",
            workflow_run_id="run-1",
        )

    assert raised.value.type == "LocalPocCredentiallessOnly"


async def test_local_poc_composition_uses_only_server_owned_static_context() -> None:
    """A permitted local action receives opaque, credentialless control-plane values."""
    request = {
        "kind": "PublishedExternalActionInvocation",
        "workflowVersionId": str(uuid4()),
        "nodeId": "hello",
        "inputs": {"message": "Hello"},
        "timeoutSeconds": 30,
    }
    settings = SimpleNamespace(
        plugin_platform_local_poc_enabled=True,
        plugin_platform_local_poc_execution_enabled=True,
    )
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    session_cm.__aexit__ = AsyncMock(return_value=False)
    resolver = MagicMock()
    resolver.resolve_published_action = AsyncMock(return_value=SimpleNamespace(integration_type_revision_id=None))
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=_workload_request())

    with (
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity.get_settings",
            return_value=settings,
        ),
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity._session_factory",
            return_value=session_cm,
        ),
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity.WorkflowActionBindingResolver",
            return_value=resolver,
        ),
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity.PublishedActionWorkloadComposer",
            return_value=composer,
        ),
    ):
        result = await _compose_local_poc_workload_request(
            request,
            project_id=str(uuid4()),
            invocation_id="container-1",
            workflow_run_id="run-1",
        )

    assert result == _workload_request()
    execution_context = composer.compose.await_args.args[1]
    assert execution_context.integration_handle == "local-poc-credentialless"
    assert execution_context.capability_grant_id == "local-poc-credentialless"
    assert execution_context.execution_policy_id == "local-poc-credentialless"
    assert execution_context.effective_capabilities == ()


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


async def test_activity_resolves_a_published_reference_before_ep_submission() -> None:
    """The workflow can only send a published reference, never a constructed image route."""
    project_id = uuid4()
    request = {
        "kind": "PublishedExternalActionInvocation",
        "workflowVersionId": str(uuid4()),
        "nodeId": "hello",
        "inputs": {"message": "Hello"},
        "timeoutSeconds": 30,
    }
    client = MagicMock()
    client.submit = AsyncMock(
        return_value={"id": str(uuid4()), "status": "completed", "result": {"output": {"ok": True}}}
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
            "syntara.workflows.workflow_engine.activities.container_activity.get_settings",
            return_value=settings,
        ),
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity._compose_local_poc_workload_request",
            new_callable=AsyncMock,
            return_value=_workload_request(),
        ) as compose,
        patch(
            "syntara.workflows.workflow_engine.activities.container_activity._build_client",
            return_value=client,
        ),
    ):
        result = await execute_container_activity(request, None, str(project_id))

    assert result == {"output": {"ok": True}}
    compose_call = compose.await_args
    assert compose_call is not None
    assert compose_call.args[0] == request
    assert "imageDigest" not in request
    client.submit.assert_awaited_once()


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
