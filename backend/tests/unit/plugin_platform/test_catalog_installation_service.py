"""Unit coverage for the strict catalog artifact-to-install mapping."""

from __future__ import annotations

from copy import deepcopy
from typing import Literal
from uuid import uuid4

import pytest

from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError
from syntara.plugin_platform.models.catalog import CatalogPluginVersion
from syntara.plugin_platform.services.artifact_verifier import VerifiedPluginArtifact
from syntara.plugin_platform.services.catalog_installation_service import build_catalog_install_candidate
from syntara.plugin_platform.services.runner_profile import PlatformRunnerProfile


def _version(
    *, runtime_ownership: Literal["platform-runner", "custom-workload"] = "custom-workload"
) -> CatalogPluginVersion:
    return CatalogPluginVersion(
        reconciliation_id=uuid4(),
        namespace="syntara",
        name="github",
        version="1.0.0",
        artifact_repository="syntara/plugins/github",
        artifact_digest="sha256:" + "a" * 64,
        documentation_digest="sha256:" + "b" * 64,
        runtime_ownership=runtime_ownership,
        canonical_entry={},
    )


def _descriptor() -> dict[str, object]:
    return {
        "apiVersion": "syntara.io/v1alpha1",
        "kind": "PluginDescriptor",
        "metadata": {
            "namespace": "syntara",
            "name": "github",
            "version": "1.0.0",
            "displayName": "GitHub",
            "description": "Creates GitHub issues.",
        },
        "spec": {
            "assets": {},
            "contractBundleDigest": "sha256:" + "c" * 64,
            "targets": [
                {
                    "kind": "Action",
                    "identity": "syntara.github.create-issue",
                    "version": "1.0.0",
                    "metadata": {
                        "name": "create-issue",
                        "displayName": "Create issue",
                        "description": "Creates one issue.",
                    },
                    "sourcePath": "steps/create-issue/manifest.yaml",
                    "runtime": {
                        "kind": "custom-workload",
                        "workload": "plugin-workload",
                        "abi": "syntara.container/v1alpha1",
                    },
                    "input": {"type": "object"},
                    "output": {"type": "object"},
                    "error": {"type": "object"},
                }
            ],
            "workloads": {
                "plugin-workload": {
                    "image": "quay.io/syntara/github@sha256:" + "d" * 64,
                    "abi": "syntara.container/v1alpha1",
                }
            },
        },
    }


def _artifact(descriptor: dict[str, object]) -> VerifiedPluginArtifact:
    return VerifiedPluginArtifact(
        artifact_repository="syntara/plugins/github",
        artifact_digest="sha256:" + "a" * 64,
        canonical_descriptor=descriptor,
        asset_index={},
        documentation=None,
    )


def _http_operation() -> dict[str, object]:
    return {
        "method": "POST",
        "path": "/repos/{input.owner}/{input.repository}/issues",
        "requestMap": {"title": {"from": "/title"}},
        "responseMap": {"id": {"from": "/id"}},
    }


def test_candidate_derives_one_immutable_action_revision_from_the_canonical_target() -> None:
    candidate = build_catalog_install_candidate(
        _artifact(_descriptor()),
        _version(),
        {"signature": "verified"},
    )

    assert candidate.namespace == "syntara"
    assert candidate.actions[0].image_digest == "quay.io/syntara/github@sha256:" + "d" * 64
    assert candidate.actions[0].revision_digest.startswith("sha256:")


def test_candidate_installs_connection_contracts_with_their_referencing_action() -> None:
    descriptor = deepcopy(_descriptor())
    specification = descriptor["spec"]
    assert isinstance(specification, dict)
    targets = specification["targets"]
    assert isinstance(targets, list)
    action = targets[0]
    assert isinstance(action, dict)
    action["integrationType"] = "syntara.github.connection"
    targets.extend(
        [
            {
                "kind": "IntegrationType",
                "identity": "syntara.github.github-connection",
                "version": "1.0.0",
                "metadata": {
                    "name": "github-connection",
                    "displayName": "GitHub connection",
                    "description": "Connects GitHub Cloud or Enterprise Server.",
                },
                "sourcePath": "integrations/github.yaml",
                "id": "syntara.github.connection",
                "endpointKinds": ["github-cloud", "github-enterprise"],
                "credentialTypes": ["syntara.github.service-token"],
                "configuration": {"type": "object"},
            },
            {
                "kind": "CredentialRecipe",
                "identity": "syntara.github.github-token",
                "version": "1.0.0",
                "metadata": {
                    "name": "github-token",
                    "displayName": "GitHub service token",
                    "description": "A static GitHub token.",
                },
                "sourcePath": "credentials/github-token.yaml",
                "id": "syntara.github.service-token",
                "integrationType": "syntara.github.connection",
                "credentialSchema": {
                    "type": "object",
                    "properties": {"token": {"type": "string"}},
                },
                "secretFields": ["token"],
                "issuance": {"kind": "static-bearer.v1"},
            },
        ]
    )

    candidate = build_catalog_install_candidate(
        _artifact(descriptor),
        _version(),
        {"signature": "verified"},
    )

    assert candidate.actions[0].integration_type_id == "syntara.github.connection"
    assert candidate.integration_types[0].credential_type_ids == ("syntara.github.service-token",)
    assert candidate.credential_types[0].secret_field_names == ("token",)


def test_candidate_rejects_an_action_that_references_an_undeclared_connection_type() -> None:
    descriptor = deepcopy(_descriptor())
    specification = descriptor["spec"]
    assert isinstance(specification, dict)
    targets = specification["targets"]
    assert isinstance(targets, list)
    action = targets[0]
    assert isinstance(action, dict)
    action["integrationType"] = "syntara.github.connection"

    with pytest.raises(CatalogSourceConfigurationError, match="supported installed-plugin definition"):
        build_catalog_install_candidate(_artifact(descriptor), _version(), {"signature": "verified"})


def test_candidate_rejects_trigger_targets_until_trigger_definition_persistence_exists() -> None:
    descriptor = deepcopy(_descriptor())
    specification = descriptor["spec"]
    assert isinstance(specification, dict)
    targets = specification["targets"]
    assert isinstance(targets, list)
    assert isinstance(targets[0], dict)
    targets[0]["kind"] = "Trigger"

    with pytest.raises(CatalogSourceConfigurationError, match="non-action target"):
        build_catalog_install_candidate(_artifact(descriptor), _version(), {"signature": "verified"})


def test_candidate_binds_platform_runner_actions_to_the_server_owned_profile() -> None:
    descriptor = deepcopy(_descriptor())
    specification = descriptor["spec"]
    assert isinstance(specification, dict)
    targets = specification["targets"]
    assert isinstance(targets, list)
    assert isinstance(targets[0], dict)
    targets[0]["runtime"] = {
        "kind": "platform-runner",
        "driver": "http.v1",
        "operation": _http_operation(),
    }
    specification["workloads"] = {}

    candidate = build_catalog_install_candidate(
        _artifact(descriptor),
        _version(runtime_ownership="platform-runner"),
        {"signature": "verified"},
        runner_profiles={
            "http.v1": PlatformRunnerProfile(
                revision_id=uuid4(),
                driver="http.v1",
                image_digest="quay.io/syntara/http-runner@sha256:" + "e" * 64,
            )
        },
    )

    action = candidate.actions[0]
    assert action.runtime_kind == "platform-runner"
    assert action.image_digest == "quay.io/syntara/http-runner@sha256:" + "e" * 64
    assert action.canonical_definition["runtime"] == {
        "abi": "syntara.container/v1alpha1",
        "driver": "http.v1",
        "image": "quay.io/syntara/http-runner@sha256:" + "e" * 64,
        "kind": "platform-runner",
        "operation": _http_operation(),
        "profileRevisionId": str(action.runner_profile_revision_id),
    }
    assert action.runner_profile_revision_id is not None


def test_candidate_rejects_platform_runner_without_an_admitted_profile() -> None:
    descriptor = deepcopy(_descriptor())
    specification = descriptor["spec"]
    assert isinstance(specification, dict)
    targets = specification["targets"]
    assert isinstance(targets, list)
    assert isinstance(targets[0], dict)
    targets[0]["runtime"] = {
        "kind": "platform-runner",
        "driver": "http.v1",
        "operation": _http_operation(),
    }
    specification["workloads"] = {}

    with pytest.raises(CatalogSourceConfigurationError, match="platform runner driver"):
        build_catalog_install_candidate(
            _artifact(descriptor),
            _version(runtime_ownership="platform-runner"),
            {"signature": "verified"},
        )


@pytest.mark.parametrize("path", ["//attacker.example.test/issues", "/repos/%2Fissues"])
def test_candidate_rejects_an_http_operation_that_can_select_another_origin(path: str) -> None:
    descriptor = deepcopy(_descriptor())
    specification = descriptor["spec"]
    assert isinstance(specification, dict)
    targets = specification["targets"]
    assert isinstance(targets, list)
    assert isinstance(targets[0], dict)
    targets[0]["runtime"] = {
        "kind": "platform-runner",
        "driver": "http.v1",
        "operation": {
            "method": "POST",
            "path": path,
            "requestMap": {"title": {"from": "/title"}},
        },
    }
    specification["workloads"] = {}

    with pytest.raises(CatalogSourceConfigurationError, match=r"http\.v1 operation"):
        build_catalog_install_candidate(
            _artifact(descriptor),
            _version(runtime_ownership="platform-runner"),
            {"signature": "verified"},
            runner_profiles={
                "http.v1": PlatformRunnerProfile(
                    revision_id=uuid4(),
                    driver="http.v1",
                    image_digest="quay.io/syntara/http-runner@sha256:" + "e" * 64,
                )
            },
        )


def test_candidate_rejects_catalog_runtime_ownership_that_disagrees_with_the_artifact() -> None:
    version = _version()
    version.runtime_ownership = "platform-runner"

    with pytest.raises(CatalogSourceConfigurationError, match="catalog runtime ownership"):
        build_catalog_install_candidate(_artifact(_descriptor()), version, {"signature": "verified"})


def test_candidate_rejects_unimplemented_provider_or_extra_workload_bindings() -> None:
    descriptor = deepcopy(_descriptor())
    specification = descriptor["spec"]
    assert isinstance(specification, dict)
    workloads = specification["workloads"]
    assert isinstance(workloads, dict)
    workloads["provider-image"] = {
        "image": "quay.io/syntara/provider@sha256:" + "e" * 64,
        "abi": "syntara.provider/v1alpha1",
        "provider": "syntara.github.options",
    }

    with pytest.raises(CatalogSourceConfigurationError, match="additional workload or provider binding"):
        build_catalog_install_candidate(_artifact(descriptor), _version(), {"signature": "verified"})
