"""Unit coverage for bounded OCI catalog-index verification."""

from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from typing import Any, cast

import pytest
from syntara_plugin.contracts import load_bundle

from syntara.plugin_platform.exceptions import CatalogIndexArtifactVerificationError
from syntara.plugin_platform.services.catalog_index_verifier import (
    OCI_CATALOG_INDEX_ARTIFACT_TYPE,
    OCI_CATALOG_INDEX_CONFIG_MEDIA_TYPE,
    OCI_CATALOG_INDEX_CONTENT_MEDIA_TYPE,
    OCI_IMAGE_MANIFEST_MEDIA_TYPE,
    CatalogIndexArtifactInput,
    CatalogIndexArtifactVerifier,
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


def _blob(content: bytes, media_type: str) -> tuple[dict[str, object], bytes]:
    return {"mediaType": media_type, "digest": _digest(content), "size": len(content)}, content


def _index() -> dict[str, Any]:
    return {
        "apiVersion": "syntara.io/v1alpha1",
        "kind": "CatalogIndex",
        "metadata": {
            "sourceId": "local-quay",
            "generation": 42,
            "issuedAt": "2026-10-06T12:00:00Z",
            "expiresAt": "2026-10-13T12:00:00Z",
            "previousIndexDigest": "sha256:" + "a" * 64,
        },
        "spec": {
            "contractVersion": "syntara.io/v1alpha1",
            "entries": [
                {
                    "namespace": "syntara",
                    "name": "github",
                    "version": "1.0.0",
                    "artifactRepository": "syntara/plugins/github",
                    "artifactDigest": "sha256:" + "1" * 64,
                    "documentationDigest": "sha256:" + "2" * 64,
                    "runtimeOwnership": "platform-runner",
                },
                {
                    "namespace": "syntara",
                    "name": "aap",
                    "version": "1.0.0",
                    "artifactRepository": "syntara/plugins/aap",
                    "artifactDigest": "sha256:" + "3" * 64,
                    "documentationDigest": "sha256:" + "4" * 64,
                    "runtimeOwnership": "custom-workload",
                },
            ],
        },
    }


def _index_with_duplicate_identity() -> dict[str, Any]:
    document = deepcopy(_index())
    document["spec"]["entries"] = [document["spec"]["entries"][0]] * 2
    return document


def _artifact(
    *,
    document: dict[str, Any] | None = None,
    index_bytes: bytes | None = None,
    config_overrides: dict[str, object] | None = None,
    expected_source_id: str = "local-quay",
) -> CatalogIndexArtifactInput:
    catalog_index = _index() if document is None else document
    content = _canonical_json(catalog_index) if index_bytes is None else index_bytes
    index_descriptor, index_blob = _blob(content, OCI_CATALOG_INDEX_CONTENT_MEDIA_TYPE)
    config: dict[str, object] = {
        "apiVersion": "syntara.catalog-index.config/v1alpha1",
        "kind": "CatalogIndexArtifactConfig",
        "catalogIndexDigest": index_descriptor["digest"],
        "contractBundleDigest": load_bundle().digest,
        "entryCount": len(catalog_index["spec"]["entries"]),
        "generation": catalog_index["metadata"]["generation"],
        "sourceId": catalog_index["metadata"]["sourceId"],
    }
    if config_overrides:
        config.update(config_overrides)
    config_descriptor, config_blob = _blob(_canonical_json(config), OCI_CATALOG_INDEX_CONFIG_MEDIA_TYPE)
    manifest = _canonical_json(
        {
            "schemaVersion": 2,
            "mediaType": OCI_IMAGE_MANIFEST_MEDIA_TYPE,
            "artifactType": OCI_CATALOG_INDEX_ARTIFACT_TYPE,
            "config": config_descriptor,
            "layers": [index_descriptor],
        }
    )
    return CatalogIndexArtifactInput(
        index_repository="syntara/catalog-index",
        index_digest=_digest(manifest),
        expected_source_id=expected_source_id,
        manifest_bytes=manifest,
        blobs={
            cast("str", config_descriptor["digest"]): config_blob,
            cast("str", index_descriptor["digest"]): index_blob,
        },
    )


def test_verifier_accepts_the_deterministic_catalog_index_artifact_shape() -> None:
    artifact = _artifact()

    verified = CatalogIndexArtifactVerifier().verify(artifact)

    assert verified.index_repository == artifact.index_repository
    assert verified.index_digest == artifact.index_digest
    assert verified.contract_bundle_digest == load_bundle().digest
    assert verified.catalog_index["metadata"]["generation"] == 42
    assert [entry["name"] for entry in verified.catalog_index["spec"]["entries"]] == ["github", "aap"]


def test_manifest_inspection_returns_only_bounded_blob_fetch_descriptors() -> None:
    artifact = _artifact()

    plan = CatalogIndexArtifactVerifier().inspect_manifest(artifact)

    assert plan.index_digest == artifact.index_digest
    assert plan.config_descriptor.media_type == OCI_CATALOG_INDEX_CONFIG_MEDIA_TYPE
    assert plan.content_descriptor.media_type == OCI_CATALOG_INDEX_CONTENT_MEDIA_TYPE
    assert plan.config_descriptor.digest in artifact.blobs
    assert plan.content_descriptor.digest in artifact.blobs


@pytest.mark.parametrize(
    ("artifact", "code"),
    [
        (_artifact(index_bytes=_canonical_json(_index()) + b"\n"), "catalog-index-canonical"),
        (_artifact(config_overrides={"entryCount": 1}), "config-entry-count"),
        (_artifact(config_overrides={"entryCount": True}), "config-entry-count"),
        (_artifact(config_overrides={"generation": True}), "config-generation"),
        (_artifact(document=_index_with_duplicate_identity()), "catalog-index-contract"),
        (_artifact(expected_source_id="other-source"), "config-source-id"),
    ],
)
def test_verifier_rejects_noncanonical_or_invalid_catalog_artifacts(
    artifact: CatalogIndexArtifactInput, code: str
) -> None:
    with pytest.raises(CatalogIndexArtifactVerificationError, match=code):
        CatalogIndexArtifactVerifier().verify(artifact)
