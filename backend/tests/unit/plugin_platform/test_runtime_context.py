"""Tests for trusted, non-secret platform-runner context composition."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from typing import Any
from uuid import UUID

import pytest
from syntara_plugin.contracts import load_bundle, validate_document

from syntara.plugin_platform.exceptions import RuntimeContextConfigurationError
from syntara.plugin_platform.services.registry_service import ResolvedInstalledAction
from syntara.plugin_platform.services.runtime_context import build_runtime_context


def _digest(character: str) -> str:
    """Build a deterministic test-only SHA-256 digest."""
    return f"sha256:{character * 64}"


def _github_create_issue_action() -> ResolvedInstalledAction:
    """Model the compiled GitHub create-issue action after installation."""
    profile_revision_id = UUID("12345678-1234-5678-1234-567812345678")
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
    runtime = {
        "abi": "syntara.container/v1alpha1",
        "driver": "http.v1",
        "image": image_digest,
        "kind": "platform-runner",
        "operation": operation,
        "profileRevisionId": str(profile_revision_id),
    }
    return ResolvedInstalledAction(
        step_revision_id=f"syntara.github.create-issue:{'b' * 64}",
        plugin_digest=_digest("c"),
        runtime_kind="platform-runner",
        runtime_abi="syntara.container/v1alpha1",
        image_digest=image_digest,
        integration_type_revision_id=None,
        runner_profile_revision_id=profile_revision_id,
        input_schema={
            "type": "object",
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
            "runtime": runtime,
            "output": output_schema,
        },
        registry_generation=7,
    )


def _output_schema_digest(output_schema: object) -> str:
    """Independently reproduce the published canonical JSON digest rule."""
    encoded = json.dumps(
        output_schema,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def test_build_runtime_context_produces_the_github_http_runner_contract() -> None:
    """The stored GitHub action becomes a bounded, ABI-valid HTTP context."""
    action = _github_create_issue_action()

    context = build_runtime_context(action)

    assert context == {
        "driver": "http.v1",
        "operation": action.canonical_definition["runtime"]["operation"],
        "outputSchemaDigest": _output_schema_digest(action.output_schema),
    }
    assert validate_document(load_bundle(), "http_runner_context", context) == []


def test_custom_workload_requires_no_platform_runner_context() -> None:
    """Custom workloads continue to own their dispatch inside their own image."""
    action = replace(
        _github_create_issue_action(),
        runtime_kind="custom-workload",
        runner_profile_revision_id=None,
    )

    assert build_runtime_context(action) == {}


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("wrong-image", "route"),
        ("unsupported-driver", "unsupported-driver"),
        ("malformed-operation", "http-context"),
        ("changed-output", "output-schema"),
    ],
)
def test_build_runtime_context_fails_closed_when_persisted_data_no_longer_matches(mutation: str, code: str) -> None:
    """No untrusted or mismatched persisted detail can reach the container ABI."""
    action = _github_create_issue_action()
    definition = deepcopy(dict(action.canonical_definition))
    runtime = definition["runtime"]
    assert isinstance(runtime, dict)

    if mutation == "wrong-image":
        runtime["image"] = f"quay.io/syntara/http-runner@{_digest('d')}"
    elif mutation == "unsupported-driver":
        runtime["driver"] = "aap.v1"
    elif mutation == "malformed-operation":
        runtime["operation"] = {"method": "POST", "path": "/issues", "authorization": "secret"}
    elif mutation == "changed-output":
        definition["output"] = {"type": "object"}

    with pytest.raises(RuntimeContextConfigurationError) as raised:
        build_runtime_context(replace(action, canonical_definition=definition))

    assert raised.value.code == code
