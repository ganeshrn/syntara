"""Deterministic plugin-artifact fixtures shared by catalog integration tests."""

from __future__ import annotations

import gzip
import json
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from typing import TYPE_CHECKING, Any, Literal, cast

import httpx

from syntara.plugin_platform.models.catalog import (
    CatalogPluginVersion,
    PluginSource,
    PluginSourceReconciliation,
)
from syntara.plugin_platform.services.oci_distribution_client import OciDistributionClient

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession


OBSERVED_AT = datetime(2026, 10, 7, 12, tzinfo=UTC)
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
OCI_ARTIFACT_TYPE = "application/vnd.syntara.plugin.v1"
OCI_CONFIG_MEDIA_TYPE = "application/vnd.syntara.plugin.config.v1+json"
OCI_PLUGIN_MANIFEST_MEDIA_TYPE = "application/vnd.syntara.plugin.manifest.v1+yaml"
OCI_CONTENT_BUNDLE_MEDIA_TYPE = "application/vnd.syntara.plugin.content.v1.tar+gzip"


def canonical_json(value: object) -> bytes:
    """Return the SDK-compatible canonical JSON bytes used in fixture artifacts."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value: bytes) -> str:
    """Return the lower-case SHA-256 digest of fixture bytes."""
    return f"sha256:{sha256(value).hexdigest()}"


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


def _blob(content: bytes, media_type: str) -> tuple[dict[str, object], bytes]:
    return {"mediaType": media_type, "digest": digest(content), "size": len(content)}, content


@dataclass(frozen=True)
class PluginArtifactWire:
    """An in-memory OCI plugin artifact with digest-addressed blobs."""

    manifest: bytes
    manifest_digest: str
    blobs: dict[str, bytes]
    documentation_digest: str


def plugin_artifact_wire(
    *, runtime_ownership: Literal["platform-runner", "custom-workload"] = "custom-workload"
) -> PluginArtifactWire:
    """Build one valid plugin artifact with the requested installed runtime ownership."""
    documentation = (
        b"# GitHub\n\n<script>alert('unsafe')</script> [Docs](https://docs.example.test) [Relative](/unsafe)\n"
    )
    schema = b'{"properties":{"title":{"type":"string"}},"type":"object"}'
    documentation_digest = digest(documentation)
    schema_digest = digest(schema)
    assets: dict[str, dict[str, object]] = {
        "docs/README.md": {
            "canonicalDigest": documentation_digest,
            "digest": documentation_digest,
            "mediaType": "text/markdown",
            "references": ["plugin.yaml#/spec/documentation"],
            "size": len(documentation),
        },
        "schemas/input.json": {
            "canonicalDigest": schema_digest,
            "digest": schema_digest,
            "mediaType": "application/schema+json",
            "references": ["steps/create-issue/manifest.yaml#/spec/input"],
            "size": len(schema),
        },
    }
    runtime: dict[str, Any]
    workloads: dict[str, dict[str, str]]
    if runtime_ownership == "platform-runner":
        runtime = {
            "driver": "http.v1",
            "kind": "platform-runner",
            "operation": {
                "method": "POST",
                "path": "/repos/{input.owner}/{input.repository}/issues",
                "requestMap": {"title": {"from": "/title"}},
                "responseMap": {"id": {"from": "/id"}},
            },
        }
        workloads = {}
    else:
        runtime = {
            "abi": "syntara.container/v1alpha1",
            "kind": "custom-workload",
            "workload": "plugin-workload",
        }
        workloads = {
            "plugin-workload": {
                "abi": "syntara.container/v1alpha1",
                "image": "quay.io/syntara/github@sha256:" + "b" * 64,
            }
        }
    descriptor: dict[str, Any] = {
        "apiVersion": "syntara.io/v1alpha1",
        "kind": "PluginDescriptor",
        "metadata": {
            "description": "Creates GitHub issues.",
            "displayName": "GitHub",
            "name": "github",
            "namespace": "syntara",
            "version": "1.0.0",
        },
        "spec": {
            "assets": assets,
            "contractBundleDigest": "sha256:" + "a" * 64,
            "documentation": {"digest": documentation_digest, "path": "docs/README.md"},
            "targets": [
                {
                    "identity": "syntara.github.create-issue",
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
                    "version": "1.0.0",
                }
            ],
            "workloads": workloads,
        },
    }
    descriptor_content = canonical_json(descriptor)
    descriptor_descriptor, _ = _blob(descriptor_content, OCI_PLUGIN_MANIFEST_MEDIA_TYPE)
    bundle_descriptor, bundle_content = _blob(
        _bundle({"docs/README.md": documentation, "schemas/input.json": schema}),
        OCI_CONTENT_BUNDLE_MEDIA_TYPE,
    )
    config_content = canonical_json(
        {
            "apiVersion": "syntara.plugin.config/v1alpha1",
            "assetIndex": assets,
            "kind": "PluginArtifactConfig",
            "pluginDescriptorDigest": digest(descriptor_content),
        }
    )
    config_descriptor, _ = _blob(config_content, OCI_CONFIG_MEDIA_TYPE)
    manifest = canonical_json(
        {
            "artifactType": OCI_ARTIFACT_TYPE,
            "config": config_descriptor,
            "layers": [bundle_descriptor, descriptor_descriptor],
            "mediaType": OCI_MANIFEST_MEDIA_TYPE,
            "schemaVersion": 2,
        }
    )
    return PluginArtifactWire(
        manifest=manifest,
        manifest_digest=digest(manifest),
        blobs={
            cast("str", config_descriptor["digest"]): config_content,
            cast("str", descriptor_descriptor["digest"]): descriptor_content,
            cast("str", bundle_descriptor["digest"]): bundle_content,
        },
        documentation_digest=documentation_digest,
    )


async def seed_active_catalog(
    session: AsyncSession,
    wire: PluginArtifactWire,
    *,
    runtime_ownership: Literal["platform-runner", "custom-workload"] = "custom-workload",
) -> tuple[PluginSource, CatalogPluginVersion]:
    """Persist an accepted source and its one matching immutable catalog version."""
    source = PluginSource.model_validate(
        {
            "source_id": "local-quay",
            "display_name": "Local Quay",
            "registry_origin": "https://quay.example.test",
            "index_repository": "syntara/catalog-index",
            "channel": "stable",
            "repository_scopes": ["syntara/plugins"],
            "credential_ref": "credentials/local-quay-pull",
            "trust_policy_ref": "trust/local-quay",
        }
    )
    session.add(source)
    await session.flush()
    reconciliation = PluginSourceReconciliation(
        plugin_source_id=source.id,
        state="accepted",
        catalog_generation=1,
        catalog_index_digest=digest(b"index"),
        previous_index_digest=None,
        issued_at=OBSERVED_AT - timedelta(hours=1),
        expires_at=OBSERVED_AT + timedelta(days=7),
        entry_count=1,
        verification_summary={"trust": {"signature": "verified"}},
        initiated_by=None,
        initiation_kind="test",
    )
    session.add(reconciliation)
    await session.flush()
    source.active_reconciliation_id = reconciliation.id
    version = CatalogPluginVersion(
        reconciliation_id=reconciliation.id,
        namespace="syntara",
        name="github",
        version="1.0.0",
        artifact_repository="syntara/plugins/github",
        artifact_digest=wire.manifest_digest,
        documentation_digest=wire.documentation_digest,
        runtime_ownership=runtime_ownership,
        canonical_entry={},
    )
    session.add(version)
    await session.commit()
    return source, version


def registry_client(wire: PluginArtifactWire, paths: list[str]) -> OciDistributionClient:
    """Return a mock OCI client that serves exactly one digest-pinned artifact."""

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["authorization"] == "Bearer registry-token"
        if request.url.path.endswith(f"/manifests/{wire.manifest_digest}"):
            return httpx.Response(
                200,
                content=wire.manifest,
                headers={
                    "content-type": OCI_MANIFEST_MEDIA_TYPE,
                    "docker-content-digest": wire.manifest_digest,
                },
            )
        blob_digest = request.url.path.rsplit("/", maxsplit=1)[-1]
        content = wire.blobs[blob_digest]
        return httpx.Response(200, content=content, headers={"content-length": str(len(content))})

    return OciDistributionClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
