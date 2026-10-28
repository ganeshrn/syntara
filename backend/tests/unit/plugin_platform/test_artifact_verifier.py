"""Unit coverage for bounded OCI plugin-artifact verification."""

from __future__ import annotations

import gzip
import json
import tarfile
from hashlib import sha256
from io import BytesIO
from typing import Any, TypedDict

import pytest

from syntara.plugin_platform.exceptions import PluginArtifactVerificationError
from syntara.plugin_platform.services.artifact_verifier import (
    OCI_ARTIFACT_TYPE,
    OCI_CONFIG_MEDIA_TYPE,
    OCI_CONTENT_BUNDLE_MEDIA_TYPE,
    OCI_IMAGE_MANIFEST_MEDIA_TYPE,
    OCI_PLUGIN_MANIFEST_MEDIA_TYPE,
    PluginArtifactInput,
    PluginArtifactVerifier,
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


class _BlobDescriptor(TypedDict):
    """Typed OCI descriptor used by the fixture's manifest and blob map."""

    mediaType: str
    digest: str
    size: int


def _blob(content: bytes, media_type: str) -> tuple[_BlobDescriptor, bytes]:
    return {"mediaType": media_type, "digest": _digest(content), "size": len(content)}, content


def _bundle(entries: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for path, content in sorted(entries.items()):
                entry = tarfile.TarInfo(path)
                entry.size = len(content)
                entry.mode = 0o644
                entry.uid = 0
                entry.gid = 0
                entry.mtime = 0
                entry.uname = ""
                entry.gname = ""
                archive.addfile(entry, BytesIO(content))
    return output.getvalue()


def _artifact(
    *,
    bundle_path: str = "schemas/input.json",
    descriptor_suffix: bytes = b"",
    documentation: bytes | None = None,
    http_operation: bool = False,
    connection_types: bool = False,
    mapping_media_type: str = "application/yaml",
    input_presentation: bool = False,
) -> PluginArtifactInput:
    schema = b'{"properties":{"title":{"type":"string"}},"type":"object"}'
    schema_digest = _digest(schema)
    asset = {
        "canonicalDigest": schema_digest,
        "digest": schema_digest,
        "mediaType": "application/schema+json",
        "references": ["steps/create-issue/manifest.yaml#/spec/input"],
        "size": len(schema),
    }
    assets: dict[str, dict[str, object]] = {"schemas/input.json": asset}
    runtime: dict[str, Any] = {
        "abi": "syntara.container/v1alpha1",
        "kind": "custom-workload",
        "workload": "plugin-workload",
    }
    workloads: dict[str, dict[str, str]] = {
        "plugin-workload": {
            "abi": "syntara.container/v1alpha1",
            "image": "quay.io/acme/issue-tools@sha256:" + "b" * 64,
        }
    }
    entries: dict[str, bytes] = {bundle_path: schema}
    if http_operation:
        request_mapping = {"title": {"from": "/title"}}
        request_mapping_content = _canonical_json(request_mapping)
        request_mapping_digest = _digest(request_mapping_content)
        assets["mappings/create-issue.request.yaml"] = {
            "canonicalDigest": request_mapping_digest,
            "digest": request_mapping_digest,
            "mediaType": mapping_media_type,
            "references": ["steps/create-issue/manifest.yaml#/spec/runtime/operation/requestMap"],
            "size": len(request_mapping_content),
        }
        entries["mappings/create-issue.request.yaml"] = request_mapping_content
        runtime = {
            "driver": "http.v1",
            "kind": "platform-runner",
            "operation": {
                "method": "POST",
                "path": "/repos/{input.owner}/{input.repository}/issues",
                "requestMap": request_mapping,
            },
        }
        workloads = {}
    specification: dict[str, Any] = {
        "assets": assets,
        "contractBundleDigest": "sha256:" + "a" * 64,
        "targets": [
            {
                "identity": "acme.issue-tools.create-issue",
                "input": {"properties": {"title": {"type": "string"}}, "type": "object"},
                "kind": "Action",
                "metadata": {
                    "description": "Creates one issue.",
                    "displayName": "Create issue",
                    "name": "create-issue",
                },
                "output": {"type": "object"},
                "error": {"type": "object"},
                "runtime": runtime,
                "sourcePath": "steps/create-issue/manifest.yaml",
                "version": "0.1.0",
            }
        ],
        "workloads": workloads,
    }
    if input_presentation:
        ui_schema = {"title": {"ui:autofocus": True}, "ui:order": ["title", "*"]}
        ui_schema_content = _canonical_json(ui_schema)
        assets["ui/create-issue.yaml"] = {
            "canonicalDigest": _digest(ui_schema_content),
            "digest": _digest(ui_schema_content),
            "mediaType": "application/yaml",
            "references": ["steps/create-issue/manifest.yaml#/spec/inputPresentation/uiSchema"],
            "size": len(ui_schema_content),
        }
        entries["ui/create-issue.yaml"] = ui_schema_content
        specification["targets"][0]["inputPresentation"] = {
            "renderer": "rjsf.v1",
            "uiSchema": ui_schema,
        }
    if connection_types:
        integration_schema = b'{"additionalProperties":false,"type":"object"}'
        credential_schema = b'{"additionalProperties":false,"properties":{"token":{"type":"string"}},"type":"object"}'
        integration_schema_value = json.loads(integration_schema)
        credential_schema_value = json.loads(credential_schema)
        assets["schemas/github-connection.yaml"] = {
            "canonicalDigest": _digest(_canonical_json(integration_schema_value)),
            "digest": _digest(integration_schema),
            "mediaType": "application/schema+yaml",
            "references": ["integrations/github.yaml#/spec/configuration"],
            "size": len(integration_schema),
        }
        assets["schemas/github-token.yaml"] = {
            "canonicalDigest": _digest(_canonical_json(credential_schema_value)),
            "digest": _digest(credential_schema),
            "mediaType": "application/schema+yaml",
            "references": ["credentials/github-token.yaml#/spec/credentialSchema"],
            "size": len(credential_schema),
        }
        entries["schemas/github-connection.yaml"] = integration_schema
        entries["schemas/github-token.yaml"] = credential_schema
        specification["targets"].extend(
            [
                {
                    "credentialTypes": ["acme.issue-tools.github-token"],
                    "configuration": integration_schema_value,
                    "endpointKinds": ["github-cloud", "github-enterprise"],
                    "id": "acme.issue-tools.github-connection",
                    "identity": "acme.issue-tools.github-connection",
                    "kind": "IntegrationType",
                    "metadata": {
                        "description": "Connects GitHub.",
                        "displayName": "GitHub connection",
                        "name": "github-connection",
                    },
                    "sourcePath": "integrations/github.yaml",
                    "version": "0.1.0",
                },
                {
                    "credentialSchema": credential_schema_value,
                    "id": "acme.issue-tools.github-token",
                    "identity": "acme.issue-tools.github-token",
                    "integrationType": "acme.issue-tools.github-connection",
                    "issuance": {"kind": "static-bearer.v1"},
                    "kind": "CredentialRecipe",
                    "metadata": {
                        "description": "A GitHub token.",
                        "displayName": "GitHub token",
                        "name": "github-token",
                    },
                    "secretFields": ["token"],
                    "sourcePath": "credentials/github-token.yaml",
                    "version": "0.1.0",
                },
            ]
        )
    if documentation is not None:
        documentation_digest = _digest(documentation)
        assets["docs/README.md"] = {
            "canonicalDigest": documentation_digest,
            "digest": documentation_digest,
            "mediaType": "text/markdown",
            "references": ["plugin.yaml#/spec/documentation"],
            "size": len(documentation),
        }
        specification["documentation"] = {"digest": documentation_digest, "path": "docs/README.md"}
        entries["docs/README.md"] = documentation
    descriptor: dict[str, Any] = {
        "apiVersion": "syntara.io/v1alpha1",
        "kind": "PluginDescriptor",
        "metadata": {
            "description": "Creates GitHub issues.",
            "displayName": "Issue tools",
            "name": "issue-tools",
            "namespace": "acme",
            "version": "0.1.0",
        },
        "spec": specification,
    }
    descriptor_content = _canonical_json(descriptor) + descriptor_suffix
    metadata_descriptor, metadata_content = _blob(descriptor_content, OCI_PLUGIN_MANIFEST_MEDIA_TYPE)
    bundle_descriptor, bundle_content = _blob(_bundle(entries), OCI_CONTENT_BUNDLE_MEDIA_TYPE)
    config_content = _canonical_json(
        {
            "apiVersion": "syntara.plugin.config/v1alpha1",
            "assetIndex": assets,
            "kind": "PluginArtifactConfig",
            "pluginDescriptorDigest": _digest(_canonical_json(descriptor)),
        }
    )
    config_descriptor, config_content = _blob(config_content, OCI_CONFIG_MEDIA_TYPE)
    manifest_content = _canonical_json(
        {
            "artifactType": OCI_ARTIFACT_TYPE,
            "config": config_descriptor,
            "layers": [bundle_descriptor, metadata_descriptor],
            "mediaType": OCI_IMAGE_MANIFEST_MEDIA_TYPE,
            "schemaVersion": 2,
        }
    )
    return PluginArtifactInput(
        artifact_repository="quay.io/acme/plugins/issue-tools",
        artifact_digest=_digest(manifest_content),
        manifest_bytes=manifest_content,
        blobs={
            config_descriptor["digest"]: config_content,
            metadata_descriptor["digest"]: metadata_content,
            bundle_descriptor["digest"]: bundle_content,
        },
    )


def test_verifier_accepts_canonical_sdk_artifact_shape() -> None:
    verified = PluginArtifactVerifier().verify(_artifact())

    assert verified.artifact_repository == "quay.io/acme/plugins/issue-tools"
    assert verified.canonical_descriptor["metadata"]["name"] == "issue-tools"
    assert verified.asset_index["schemas/input.json"]["references"] == ["steps/create-issue/manifest.yaml#/spec/input"]
    assert verified.documentation is None


def test_verifier_accepts_a_typed_http_operation_mapping_asset() -> None:
    verified = PluginArtifactVerifier().verify(_artifact(http_operation=True))

    assert verified.asset_index["mappings/create-issue.request.yaml"]["mediaType"] == "application/yaml"


def test_verifier_accepts_an_input_presentation_ui_schema_asset() -> None:
    verified = PluginArtifactVerifier().verify(_artifact(input_presentation=True))

    target = verified.canonical_descriptor["spec"]["targets"][0]
    assert target["inputPresentation"]["renderer"] == "rjsf.v1"
    assert verified.asset_index["ui/create-issue.yaml"]["mediaType"] == "application/yaml"


def test_verifier_accepts_schema_assets_for_connection_and_credential_types() -> None:
    verified = PluginArtifactVerifier().verify(_artifact(connection_types=True))

    assert verified.asset_index["schemas/github-connection.yaml"]["references"] == [
        "integrations/github.yaml#/spec/configuration"
    ]
    assert verified.asset_index["schemas/github-token.yaml"]["references"] == [
        "credentials/github-token.yaml#/spec/credentialSchema"
    ]


def test_verifier_rejects_a_mapping_asset_with_schema_media_type() -> None:
    with pytest.raises(PluginArtifactVerificationError, match="asset-reference"):
        PluginArtifactVerifier().verify(_artifact(http_operation=True, mapping_media_type="application/schema+yaml"))


def test_manifest_inspection_returns_bounded_blob_plan_without_loading_blob_content() -> None:
    artifact = _artifact()
    plan = PluginArtifactVerifier().inspect_manifest(
        PluginArtifactInput(
            artifact_repository=artifact.artifact_repository,
            artifact_digest=artifact.artifact_digest,
            manifest_bytes=artifact.manifest_bytes,
            blobs={},
        )
    )

    assert plan.artifact_digest == artifact.artifact_digest
    assert plan.config_descriptor.media_type == OCI_CONFIG_MEDIA_TYPE
    assert plan.plugin_manifest_descriptor.media_type == OCI_PLUGIN_MANIFEST_MEDIA_TYPE
    assert plan.content_bundle_descriptor.media_type == OCI_CONTENT_BUNDLE_MEDIA_TYPE


def test_manifest_inspection_rejects_a_non_normalized_artifact_repository() -> None:
    artifact = _artifact()
    malformed = PluginArtifactInput(
        artifact_repository="quay.io/acme/../plugins/issue-tools",
        artifact_digest=artifact.artifact_digest,
        manifest_bytes=artifact.manifest_bytes,
        blobs={},
    )

    with pytest.raises(PluginArtifactVerificationError, match="repository"):
        PluginArtifactVerifier().inspect_manifest(malformed)


def test_verifier_extracts_only_documentation_bound_to_the_signed_descriptor() -> None:
    documentation = b"# Issue tools\n\nCreate and label GitHub issues.\n"

    verified = PluginArtifactVerifier().verify(_artifact(documentation=documentation))

    assert verified.documentation is not None
    assert verified.documentation.path == "docs/README.md"
    assert verified.documentation.digest == _digest(documentation)
    assert verified.documentation.content == documentation.decode("utf-8")


def test_verifier_rejects_markdown_masquerading_as_an_action_schema() -> None:
    artifact = _artifact(documentation=b'{"type":"object"}')
    manifest = json.loads(artifact.manifest_bytes)
    config_descriptor = manifest["config"]
    config = json.loads(artifact.blobs[config_descriptor["digest"]])
    metadata_descriptor = next(
        layer for layer in manifest["layers"] if layer["mediaType"] == OCI_PLUGIN_MANIFEST_MEDIA_TYPE
    )
    old_metadata_digest = metadata_descriptor["digest"]
    descriptor = json.loads(artifact.blobs[metadata_descriptor["digest"]])
    descriptor["spec"]["assets"]["docs/README.md"]["references"] = ["steps/create-issue/manifest.yaml#/spec/input"]
    descriptor_bytes = _canonical_json(descriptor)
    new_metadata_descriptor, _ = _blob(descriptor_bytes, OCI_PLUGIN_MANIFEST_MEDIA_TYPE)
    metadata_descriptor.clear()
    metadata_descriptor.update(new_metadata_descriptor)
    config["assetIndex"]["docs/README.md"]["references"] = ["steps/create-issue/manifest.yaml#/spec/input"]
    config["pluginDescriptorDigest"] = _digest(descriptor_bytes)
    config_bytes = _canonical_json(config)
    new_config_descriptor, _ = _blob(config_bytes, OCI_CONFIG_MEDIA_TYPE)
    manifest["config"] = new_config_descriptor
    manifest_bytes = _canonical_json(manifest)
    blobs = dict(artifact.blobs)
    blobs.pop(config_descriptor["digest"])
    blobs.pop(old_metadata_digest)
    blobs[new_config_descriptor["digest"]] = config_bytes
    blobs[new_metadata_descriptor["digest"]] = descriptor_bytes
    malformed = PluginArtifactInput(
        artifact_repository=artifact.artifact_repository,
        artifact_digest=_digest(manifest_bytes),
        manifest_bytes=manifest_bytes,
        blobs=blobs,
    )

    with pytest.raises(PluginArtifactVerificationError, match="asset-reference"):
        PluginArtifactVerifier().verify(malformed)


@pytest.mark.parametrize(
    ("artifact", "code"),
    [
        (_artifact(descriptor_suffix=b"\n"), "plugin-manifest-canonical"),
        (_artifact(bundle_path="schemas/other.json"), "content-bundle-entry"),
    ],
)
def test_verifier_rejects_noncanonical_or_mismatched_content(artifact: PluginArtifactInput, code: str) -> None:
    with pytest.raises(PluginArtifactVerificationError, match=code):
        PluginArtifactVerifier().verify(artifact)


def test_verifier_rejects_a_blob_that_does_not_match_its_descriptor() -> None:
    artifact = _artifact()
    blobs = dict(artifact.blobs)
    config_digest = next(digest for digest, content in blobs.items() if b"PluginArtifactConfig" in content)
    blobs[config_digest] = b"{}"
    corrupted = PluginArtifactInput(
        artifact_repository=artifact.artifact_repository,
        artifact_digest=artifact.artifact_digest,
        manifest_bytes=artifact.manifest_bytes,
        blobs=blobs,
    )

    with pytest.raises(PluginArtifactVerificationError, match="config-blob"):
        PluginArtifactVerifier().verify(corrupted)
