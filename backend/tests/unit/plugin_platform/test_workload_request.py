"""Tests for pure composition of generic credentialless container requests."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast
from uuid import UUID

import pytest
from syntara_plugin.contracts import load_bundle, validate_document

from syntara.plugin_platform.exceptions import WorkloadRequestCompositionError
from syntara.plugin_platform.services.registry_service import ResolvedInstalledAction
from syntara.plugin_platform.services.workload_request import (
    TrustedActionInvocation,
    build_container_workload_request,
)


def _digest(character: str) -> str:
    """Build a deterministic test-only SHA-256 digest."""
    return f"sha256:{character * 64}"


def _github_create_issue_action() -> ResolvedInstalledAction:
    """Model the compiled GitHub create-issue action after installation."""
    profile_revision_id = UUID("12345678-1234-5678-1234-567812345678")
    integration_type_revision_id = UUID("87654321-4321-8765-4321-876543218765")
    image_digest = f"quay.io/syntara/http-runner@{_digest('a')}"
    operation: dict[str, Any] = {
        "method": "POST",
        "path": "/repos/{input.owner}/{input.repository}/issues",
        "requestMap": {
            "body": {"from": "/body", "optional": True},
            "title": {"from": "/title"},
        },
        "responseMap": {
            "id": {"from": "/id"},
            "number": {"from": "/number"},
            "url": {"from": "/html_url"},
        },
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "number", "url"],
        "properties": {
            "id": {"type": "integer"},
            "number": {"type": "integer"},
            "url": {"type": "string", "format": "uri"},
        },
    }
    return ResolvedInstalledAction(
        step_revision_id=f"syntara.github.create-issue:{'b' * 64}",
        plugin_digest=_digest("c"),
        runtime_kind="platform-runner",
        runtime_abi="syntara.container/v1alpha1",
        image_digest=image_digest,
        integration_type_revision_id=integration_type_revision_id,
        runner_profile_revision_id=profile_revision_id,
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
        output_schema=output_schema,
        error_schema={"type": "object"},
        canonical_definition={
            "kind": "Action",
            "metadata": {"name": "create-issue"},
            "runtime": {
                "abi": "syntara.container/v1alpha1",
                "driver": "http.v1",
                "image": image_digest,
                "kind": "platform-runner",
                "operation": operation,
                "profileRevisionId": str(profile_revision_id),
            },
            "output": output_schema,
        },
        registry_generation=7,
    )


def _invocation(action: ResolvedInstalledAction | None = None, *, timeout_seconds: int = 60) -> TrustedActionInvocation:
    """Return trusted server-side references for a single workflow attempt."""
    return TrustedActionInvocation(
        action=action or _github_create_issue_action(),
        invocation_id="invocation-1",
        workflow_run_id="workflow-run-1",
        integration_handle="connection-1",
        capability_grant_id="grant-1",
        execution_policy_id="policy-1",
        policy_revision="revision-1",
        timeout_seconds=timeout_seconds,
        effective_capabilities=("network.github-api", "network.github-read"),
    )


def _inputs() -> dict[str, str]:
    """Return valid structured data from the workflow expression evaluator."""
    return {
        "owner": "syntara-orchestration",
        "repository": "syntara",
        "title": "Created by Syntara",
    }


def test_build_container_workload_request_joins_the_installed_github_action_to_trusted_state() -> None:
    """The request is complete, schema-valid, and has no secret delivery path."""
    action = _github_create_issue_action()

    request = build_container_workload_request(_invocation(action), _inputs())

    assert validate_document(load_bundle(), "runtime", request) == []
    assert request["credentialLeases"] == []
    assert request["workItem"]["credentialHandles"] == []
    assert request["workItem"]["stepRevisionId"] == action.step_revision_id
    assert request["workItem"]["imageDigest"] == action.image_digest
    assert request["workItem"]["inputs"] == _inputs()
    assert request["workItem"]["runtimeContext"] == {
        "driver": "http.v1",
        "operation": action.canonical_definition["runtime"]["operation"],
        "outputSchemaDigest": "sha256:c10eea594533d7a55109bcf23218114ea27fb48649820e62bd9f1438d2a5d8e7",
    }
    assert request["workItem"]["requestHash"] == request["policySnapshot"]["requestHash"]
    assert request["policySnapshot"]["effectiveCapabilities"] == [
        "network.github-api",
        "network.github-read",
    ]


def test_workload_request_hash_is_deterministic_for_equivalent_input_mapping_order() -> None:
    """Equivalent structured input cannot change the durable request identity."""
    first = build_container_workload_request(_invocation(), _inputs())
    reordered_inputs = {
        "title": "Created by Syntara",
        "repository": "syntara",
        "owner": "syntara-orchestration",
    }
    second = build_container_workload_request(_invocation(), reordered_inputs)

    assert first["workItem"]["requestHash"] == second["workItem"]["requestHash"]


def test_custom_workload_omits_platform_runner_context() -> None:
    """A publisher workload image has no Syntara-managed runner declaration."""
    action = replace(
        _github_create_issue_action(),
        runtime_kind="custom-workload",
        runner_profile_revision_id=None,
    )

    request = build_container_workload_request(_invocation(action), _inputs())

    assert "runtimeContext" not in request["workItem"]


@pytest.mark.parametrize(
    ("invocation", "inputs", "code"),
    [
        (_invocation(), {"owner": "syntara-orchestration"}, "input"),
        (_invocation(timeout_seconds=0), _inputs(), "identity"),
        (
            replace(_invocation(), effective_capabilities=("network.github-api", "network.github-api")),
            _inputs(),
            "capabilities",
        ),
        (
            replace(_invocation(), effective_capabilities=cast("tuple[str, ...]", "network.github-api")),
            _inputs(),
            "capabilities",
        ),
    ],
)
def test_workload_request_composition_fails_closed_for_invalid_trusted_or_workflow_state(
    invocation: TrustedActionInvocation,
    inputs: dict[str, str],
    code: str,
) -> None:
    """No incomplete request reaches the generic Temporal activity."""
    with pytest.raises(WorkloadRequestCompositionError) as raised:
        build_container_workload_request(invocation, inputs)

    assert raised.value.code == code
