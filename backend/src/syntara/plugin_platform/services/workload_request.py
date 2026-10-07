"""Compose a credentialless container request from trusted installed state.

This module is deliberately not a workflow node, HTTP client, credential
resolver, or policy engine. Its caller must already have resolved the action,
the invocation identity, and the policy references on the server side.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator, SchemaError
from syntara_plugin.contracts import (
    CanonicalJsonError,
    container_workload_request_digest,
    load_bundle,
    validate_document,
)

from syntara.plugin_platform.exceptions import WorkloadRequestCompositionError
from syntara.plugin_platform.services.runtime_context import build_runtime_context

if TYPE_CHECKING:
    from syntara.plugin_platform.services.registry_service import ResolvedInstalledAction

_API_VERSION = "syntara.io/v1alpha1"
_WORKLOAD_REQUEST_KIND = "ContainerWorkloadRequest"
_WORK_ITEM_KIND = "ContainerStepWorkItem"
_POLICY_SNAPSHOT_KIND = "ExecutionPolicySnapshot"
_INPUT_SCHEMA_ERROR = "input-schema"
_INPUT_ERROR = "input"
_IDENTITY_ERROR = "identity"
_CAPABILITIES_ERROR = "capabilities"
_REQUEST_ERROR = "request"


@dataclass(frozen=True)
class TrustedActionInvocation:
    """Server-owned values needed to compose one immutable container request.

    This type is intentionally internal: no browser or workflow expression is
    authorized to provide its integration, policy, image, action revision, or
    execution limits.
    """

    action: ResolvedInstalledAction
    invocation_id: str
    workflow_run_id: str
    integration_handle: str
    capability_grant_id: str
    execution_policy_id: str
    policy_revision: str
    timeout_seconds: int
    effective_capabilities: tuple[str, ...] = ()


def build_container_workload_request(
    invocation: TrustedActionInvocation,
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Return one schema-valid, credentialless request for the generic activity.

    The request hash covers the complete request before either mirrored hash
    field is inserted. It therefore commits to the resolved action route,
    workflow input, immutable runner context, and policy snapshot.
    """
    if not isinstance(inputs, Mapping):
        raise WorkloadRequestCompositionError(_INPUT_ERROR)
    validate_installed_action_inputs(invocation.action, inputs)
    capabilities = _normalise_capabilities(invocation.effective_capabilities)
    context = build_runtime_context(invocation.action)
    work_item: dict[str, Any] = {
        "apiVersion": _API_VERSION,
        "kind": _WORK_ITEM_KIND,
        "invocationId": invocation.invocation_id,
        "workflowRunId": invocation.workflow_run_id,
        "stepRevisionId": invocation.action.step_revision_id,
        "pluginDigest": invocation.action.plugin_digest,
        "runtimeAbi": invocation.action.runtime_abi,
        "imageDigest": invocation.action.image_digest,
        "integrationHandle": invocation.integration_handle,
        "inputs": deepcopy(dict(inputs)),
        "credentialHandles": [],
        "capabilityGrantId": invocation.capability_grant_id,
        "executionPolicyId": invocation.execution_policy_id,
        "timeoutSeconds": invocation.timeout_seconds,
    }
    if context:
        work_item["runtimeContext"] = context
    policy_snapshot: dict[str, Any] = {
        "apiVersion": _API_VERSION,
        "kind": _POLICY_SNAPSHOT_KIND,
        "policyId": invocation.execution_policy_id,
        "revision": invocation.policy_revision,
        "effectiveCapabilities": capabilities,
    }
    request_without_hashes: dict[str, Any] = {
        "apiVersion": _API_VERSION,
        "kind": _WORKLOAD_REQUEST_KIND,
        "workItem": work_item,
        "policySnapshot": policy_snapshot,
        "credentialLeases": [],
    }
    request_hash = _request_digest(request_without_hashes)
    work_item["requestHash"] = request_hash
    policy_snapshot["requestHash"] = request_hash
    request = request_without_hashes
    _validate_request(request)
    return request


def validate_installed_action_inputs(action: ResolvedInstalledAction, inputs: Mapping[str, Any]) -> None:
    """Validate resolved structured values against one installed action schema.

    The same check is used before a publication route is pinned and before a
    work item is composed. It only accepts a mapping: runtime expression
    evaluation remains the workflow engine's responsibility.
    """
    try:
        validator = Draft202012Validator(action.input_schema)
    except SchemaError as error:
        raise WorkloadRequestCompositionError(_INPUT_SCHEMA_ERROR) from error
    errors = list(validator.iter_errors(dict(inputs)))
    if errors:
        raise WorkloadRequestCompositionError(_INPUT_ERROR)


def _normalise_capabilities(capabilities: Sequence[str]) -> list[str]:
    if isinstance(capabilities, str) or not isinstance(capabilities, Sequence):
        raise WorkloadRequestCompositionError(_CAPABILITIES_ERROR)
    if any(not isinstance(capability, str) for capability in capabilities):
        raise WorkloadRequestCompositionError(_CAPABILITIES_ERROR)
    normalised = sorted(capabilities)
    if len(normalised) != len(set(normalised)):
        raise WorkloadRequestCompositionError(_CAPABILITIES_ERROR)
    return normalised


def _request_digest(value: Mapping[str, Any]) -> str:
    """Delegate request hashing to the shared cross-component contract rule."""
    try:
        return container_workload_request_digest(value)
    except CanonicalJsonError as error:
        raise WorkloadRequestCompositionError(_REQUEST_ERROR) from error


def _validate_request(request: Mapping[str, Any]) -> None:
    if validate_document(load_bundle(), "runtime", request):
        raise WorkloadRequestCompositionError(_IDENTITY_ERROR)
