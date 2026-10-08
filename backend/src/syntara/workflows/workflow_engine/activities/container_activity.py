"""Generic Temporal activity for an already-resolved external plugin action."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import suppress
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from syntara_plugin.contracts import load_bundle, validate_document
from temporalio import activity
from temporalio.exceptions import ApplicationError, CancelledError

from syntara.auth.services.token_service import TokenService
from syntara.core.config.base import Settings, get_settings
from syntara.core.database.session import AsyncSessionLocal
from syntara.plugin_platform.services.published_action_composer import (
    PublishedActionReference,
    PublishedActionWorkloadComposer,
    ServerResolvedActionContext,
)
from syntara.plugin_platform.services.registry_service import PluginRegistryService
from syntara.plugin_platform.services.workflow_action_binding import WorkflowActionBindingResolver
from syntara.workflows.clients.execution_plane_client import ExecutionPlaneClient, ExecutionPlaneClientError
from syntara.workflows.workflow_engine.models.workflow_definition import ActivityName

_ACTIVE_STATES = frozenset({"pending", "claimed", "dispatched", "cancel_requested"})
_WORKFLOW_ID_NAMESPACE = "syntara-container-work-item"
_PUBLISHED_INVOCATION_KIND = "PublishedExternalActionInvocation"
_LOCAL_POC_INTEGRATION_HANDLE = "local-poc-credentialless"
_LOCAL_POC_CAPABILITY_GRANT_ID = "local-poc-credentialless"
_LOCAL_POC_EXECUTION_POLICY_ID = "local-poc-credentialless"
_LOCAL_POC_POLICY_REVISION = "v1"

# Tests replace this with a transaction-bound database factory. The activity
# itself only reads immutable publication data and never persists credentials.
_session_factory = AsyncSessionLocal


def _stable_work_item_identity() -> tuple[str, UUID]:
    """Derive retry-stable EP identifiers from Temporal's durable activity identity."""
    info = activity.info()
    stable_source = (
        f"{_WORKFLOW_ID_NAMESPACE}:{info.workflow_id or 'unknown-workflow'}:"
        f"{info.workflow_run_id or 'unknown-run'}:{info.activity_id}"
    )
    correlation_id = uuid5(NAMESPACE_URL, stable_source)
    return f"container-{correlation_id}", correlation_id


def _is_published_action_invocation(value: Mapping[str, Any]) -> bool:
    """Recognize the narrow workflow-to-activity reference envelope."""
    return value.get("kind") == _PUBLISHED_INVOCATION_KIND


async def _compose_local_poc_workload_request(
    invocation: Mapping[str, Any],
    *,
    project_id: str,
    invocation_id: str,
    workflow_run_id: str,
) -> dict[str, Any]:
    """Resolve one credentialless local-PoC action from its published binding.

    The workflow supplies only its own immutable version/node reference and
    structured input. This deliberately admits no connection, endpoint,
    credential, policy, image, or capability selection from workflow data.
    """
    settings = get_settings()
    if not (settings.plugin_platform_local_poc_enabled and settings.plugin_platform_local_poc_execution_enabled):
        msg = "External plugin action runtime resolution is not configured"
        raise ApplicationError(msg, type="ExternalActionRuntimeUnavailable", non_retryable=True)

    workflow_version_id = invocation.get("workflowVersionId")
    node_id = invocation.get("nodeId")
    inputs = invocation.get("inputs")
    timeout_seconds = invocation.get("timeoutSeconds")
    if (
        not isinstance(workflow_version_id, str)
        or not isinstance(node_id, str)
        or not isinstance(inputs, Mapping)
        or isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds < 1
    ):
        msg = "External action invocation is invalid"
        raise ApplicationError(msg, type="ContainerRequestInvalid", non_retryable=True)
    try:
        workflow_version = UUID(workflow_version_id)
        UUID(project_id)
    except ValueError as error:
        msg = "External action invocation has an invalid workflow or project identity"
        raise ApplicationError(msg, type="ContainerRequestInvalid", non_retryable=True) from error

    async with _session_factory() as session:
        binding_resolver = WorkflowActionBindingResolver(session, PluginRegistryService(session))
        action = await binding_resolver.resolve_published_action(workflow_version, node_id)
        if action.integration_type_revision_id is not None:
            msg = "Local-PoC execution accepts only credentialless, integration-free actions"
            raise ApplicationError(msg, type="LocalPocCredentiallessOnly", non_retryable=True)
        execution_context = ServerResolvedActionContext(
            invocation_id=invocation_id,
            workflow_run_id=workflow_run_id,
            integration_handle=_LOCAL_POC_INTEGRATION_HANDLE,
            capability_grant_id=_LOCAL_POC_CAPABILITY_GRANT_ID,
            execution_policy_id=_LOCAL_POC_EXECUTION_POLICY_ID,
            policy_revision=_LOCAL_POC_POLICY_REVISION,
            timeout_seconds=timeout_seconds,
        )
        return await PublishedActionWorkloadComposer(binding_resolver).compose(
            PublishedActionReference(workflow_version_id=workflow_version, node_id=node_id),
            execution_context,
            inputs,
        )


def _validate_workload_request(workload_request: Mapping[str, Any]) -> None:
    """Accept only the shared credentialless generic work-item contract."""
    request = dict(workload_request)
    work_item = request.get("workItem")
    if request.get("credentialLeases") or (isinstance(work_item, Mapping) and work_item.get("credentialHandles")):
        msg = "Credential-bearing external actions are not enabled"
        raise ApplicationError(msg, type="CredentialDeliveryUnavailable", non_retryable=True)
    errors = validate_document(load_bundle(), "runtime", request)
    if errors or request.get("kind") != "ContainerWorkloadRequest":
        msg = "External action request does not satisfy the Syntara runtime contract"
        raise ApplicationError(msg, type="ContainerRequestInvalid", non_retryable=True)
    if not isinstance(work_item, Mapping):
        msg = "External action request does not contain a valid work item"
        raise ApplicationError(msg, type="ContainerRequestInvalid", non_retryable=True)


def _build_client(settings: Settings) -> ExecutionPlaneClient:
    """Create the trusted client only when the operator configured an EP origin."""
    if settings.execution_plane_url is None:
        msg = "External action execution is not configured"
        raise ApplicationError(msg, type="ExecutionPlaneUnavailable", non_retryable=True)
    if settings.s2s_tls_enabled and not settings.execution_plane_url.startswith("https://"):
        msg = "External action execution requires an HTTPS Execution Plane URL when S2S TLS is enabled"
        raise ApplicationError(msg, type="ExecutionPlaneUnavailable", non_retryable=True)
    token_service = TokenService()
    return ExecutionPlaneClient(
        base_url=settings.execution_plane_url,
        token_for_project=token_service.create_execution_plane_token,
        timeout_seconds=settings.execution_plane_request_timeout_seconds,
    )


def _activity_error(error: ExecutionPlaneClientError) -> ApplicationError:
    """Translate an API failure without exposing upstream endpoint data or bodies."""
    message = "Execution Plane could not accept or read the external action"
    return ApplicationError(message, type="ExecutionPlaneRequestError", non_retryable=not error.retryable)


def _completed_output(work_item: Mapping[str, Any]) -> dict[str, Any]:
    """Return a currently-supported inline object result and fail closed otherwise."""
    result = work_item.get("result")
    if not isinstance(result, Mapping):
        msg = "Execution Plane completed without a valid action result"
        raise ApplicationError(msg, type="ExecutionPlaneProtocolError", non_retryable=True)
    output = result.get("output")
    if not isinstance(output, dict):
        msg = "External action result is not an inline object"
        raise ApplicationError(msg, type="ExecutionPlaneResultUnavailable", non_retryable=True)
    return {"output": output}


def _raise_terminal_failure(work_item: Mapping[str, Any]) -> None:
    """Raise a bounded Temporal error from a terminal EP state."""
    status = work_item.get("status")
    result = work_item.get("result")
    if status == "failed" and isinstance(result, Mapping):
        error_type = result.get("error_type")
        retryable = result.get("retryable")
        message = "The plugin action did not complete."
        raise ApplicationError(
            message,
            type=error_type if isinstance(error_type, str) else "ExecutionPlaneWorkloadFailure",
            non_retryable=retryable is not True,
        )
    if status == "cancelled":
        msg = "External action was cancelled"
        raise CancelledError(msg)
    if status == "reconciliation_required":
        msg = "External action outcome requires reconciliation"
        raise ApplicationError(msg, type="ExecutionPlaneOutcomeUnknown", non_retryable=True)
    msg = "Execution Plane returned an unsupported terminal state"
    raise ApplicationError(msg, type="ExecutionPlaneProtocolError", non_retryable=True)


@activity.defn(name=ActivityName.CONTAINER)
async def execute_container_activity(
    workload_request: dict[str, Any],
    _output_config: dict[str, str] | None,
    project_id: str,
) -> dict[str, Any]:
    """Submit one resolved plugin action then wait on its durable EP work item.

    Installation and policy code, not the workflow author, constructs the
    immutable request. The initial implementation permits no credentials and
    supports only inline object results; the broker and object-result path land
    as separate, explicitly gated increments.
    """
    request_id, correlation_id = _stable_work_item_identity()
    if _is_published_action_invocation(workload_request):
        activity_info = activity.info()
        workload_request = await _compose_local_poc_workload_request(
            workload_request,
            project_id=project_id,
            invocation_id=request_id,
            workflow_run_id=activity_info.workflow_run_id or "unknown-run",
        )
    _validate_workload_request(workload_request)
    try:
        project = UUID(project_id)
    except ValueError as error:
        msg = "External action project scope is invalid"
        raise ApplicationError(msg, type="ContainerRequestInvalid", non_retryable=True) from error

    settings = get_settings()
    client = _build_client(settings)
    try:
        try:
            work_item = await client.submit(
                project_id=project,
                request_id=request_id,
                work_correlation_id=correlation_id,
                workload_request=workload_request,
            )
        except ExecutionPlaneClientError as error:
            raise _activity_error(error) from error

        while work_item.get("status") in _ACTIVE_STATES:
            activity.heartbeat({"execution_plane_request_id": request_id})
            await asyncio.sleep(settings.execution_plane_poll_interval_seconds)
            try:
                work_item = await client.get_by_request_id(project_id=project, request_id=request_id)
            except ExecutionPlaneClientError as error:
                raise _activity_error(error) from error

        if work_item.get("status") == "completed":
            return _completed_output(work_item)
        _raise_terminal_failure(work_item)
        msg = "Execution Plane returned an invalid terminal state"
        raise ApplicationError(msg, type="ExecutionPlaneProtocolError", non_retryable=True)
    except CancelledError:
        with suppress(ExecutionPlaneClientError):
            await client.cancel_by_request_id(
                project_id=project,
                request_id=request_id,
                work_correlation_id=correlation_id,
            )
        raise
    finally:
        await client.close()
