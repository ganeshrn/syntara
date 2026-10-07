"""Bounded, offline verification of one Syntara OCI catalog-index artifact.

Registry transport, signature evidence, source policy, replay checkpoints, and
database projection deliberately remain outside this boundary. This verifier
only proves that immutable, already-fetched bytes are the one canonical OCI
shape consumed by the later reconciliation service.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any, NoReturn, cast

from syntara_plugin.contracts import CatalogCheckpoint, load_bundle, validate_catalog_index

from syntara.plugin_platform.exceptions import CatalogIndexArtifactVerificationError

OCI_IMAGE_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
OCI_CATALOG_INDEX_ARTIFACT_TYPE = "application/vnd.syntara.catalog-index.v1"
OCI_CATALOG_INDEX_CONFIG_MEDIA_TYPE = "application/vnd.syntara.catalog-index.config.v1+json"
OCI_CATALOG_INDEX_CONTENT_MEDIA_TYPE = "application/vnd.syntara.catalog-index.content.v1+json"

_DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_REPOSITORY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,254}$")
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_CONFIG_BYTES = 1_048_576
_MAX_INDEX_BYTES = 4 * 1_048_576
_MAX_CATALOG_ENTRIES = 10_000
_OCI_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class CatalogIndexArtifactInput:
    """Immutable bytes acquired by a future registry transport adapter."""

    index_repository: str
    index_digest: str
    expected_source_id: str
    manifest_bytes: bytes
    blobs: Mapping[str, bytes]


@dataclass(frozen=True)
class VerifiedCatalogIndexArtifact:
    """Canonical catalog content that is ready for later trust and reconciliation."""

    index_repository: str
    index_digest: str
    catalog_index: Mapping[str, Any]
    contract_bundle_digest: str


@dataclass(frozen=True)
class OciBlobDescriptor:
    """One bounded digest-addressed OCI blob named by an index manifest."""

    media_type: str
    digest: str
    size: int


@dataclass(frozen=True)
class CatalogIndexManifestPlan:
    """Safe blob-fetch plan obtained before catalog content is decoded.

    This is deliberately limited to the OCI envelope. A trust adapter can
    decide whether the immutable manifest digest is admissible before the
    caller retrieves or decodes the config and catalog-index blobs.
    """

    index_repository: str
    index_digest: str
    expected_source_id: str
    config_descriptor: OciBlobDescriptor
    content_descriptor: OciBlobDescriptor


class CatalogIndexArtifactVerifier:
    """Verify OCI/catalog shape and contracts before a mutable reconciliation step."""

    def verify(self, artifact: CatalogIndexArtifactInput) -> VerifiedCatalogIndexArtifact:
        """Verify the supplied OCI bytes without registry or database access."""
        plan = self.inspect_manifest(artifact)

        config_bytes = _blob(plan.config_descriptor, artifact.blobs, "config-blob")
        config = _canonical_object(config_bytes, _MAX_CONFIG_BYTES, "config-json", "config-canonical")
        _require(
            set(config)
            == {
                "apiVersion",
                "catalogIndexDigest",
                "contractBundleDigest",
                "entryCount",
                "generation",
                "kind",
                "sourceId",
            },
            "config-shape",
        )
        _require(config.get("apiVersion") == "syntara.catalog-index.config/v1alpha1", "config-version")
        _require(config.get("kind") == "CatalogIndexArtifactConfig", "config-kind")
        _require(
            isinstance(config.get("catalogIndexDigest"), str) and _is_digest(config["catalogIndexDigest"]),
            "config-index-digest",
        )
        _require(
            isinstance(config.get("contractBundleDigest"), str) and _is_digest(config["contractBundleDigest"]),
            "config-contract-bundle",
        )
        _require(
            isinstance(config.get("sourceId"), str) and _IDENTIFIER_PATTERN.fullmatch(config["sourceId"]) is not None,
            "config-source-id",
        )
        _require(
            isinstance(config.get("generation"), int)
            and not isinstance(config["generation"], bool)
            and config["generation"] >= 1,
            "config-generation",
        )
        _require(
            isinstance(config.get("entryCount"), int)
            and not isinstance(config["entryCount"], bool)
            and 1 <= config["entryCount"] <= _MAX_CATALOG_ENTRIES,
            "config-entry-count",
        )

        index_bytes = _blob(plan.content_descriptor, artifact.blobs, "catalog-index-blob")
        catalog_index = _canonical_object(
            index_bytes,
            _MAX_INDEX_BYTES,
            "catalog-index-json",
            "catalog-index-canonical",
        )
        _require(config.get("catalogIndexDigest") == _digest(index_bytes), "config-index-digest")

        bundle = load_bundle()
        _require(config.get("contractBundleDigest") == bundle.digest, "config-contract-bundle")
        metadata = _mapping(catalog_index.get("metadata"), "catalog-index-metadata")
        specification = _mapping(catalog_index.get("spec"), "catalog-index-spec")
        _require(config.get("sourceId") == artifact.expected_source_id, "config-source-id")
        _require(metadata.get("sourceId") == artifact.expected_source_id, "catalog-index-source")
        _require(config.get("generation") == metadata.get("generation"), "config-generation")
        entries = _list(specification.get("entries"), "catalog-index-entries")
        _require(config.get("entryCount") == len(entries), "config-entry-count")

        issued_at = _parse_issued_at(metadata.get("issuedAt"))
        diagnostics = validate_catalog_index(
            bundle,
            catalog_index,
            source_id=artifact.expected_source_id,
            now=issued_at,
            checkpoint=CatalogCheckpoint(),
        )
        _require(not diagnostics, "catalog-index-contract")
        return VerifiedCatalogIndexArtifact(
            index_repository=artifact.index_repository,
            index_digest=artifact.index_digest,
            catalog_index=catalog_index,
            contract_bundle_digest=bundle.digest,
        )

    def inspect_manifest(self, artifact: CatalogIndexArtifactInput) -> CatalogIndexManifestPlan:
        """Validate the OCI envelope and return its bounded config/content descriptors.

        This does not fetch or decode either descriptor's blob. Callers that
        need a signature/trust decision use the returned immutable manifest
        digest before requesting those bytes.
        """
        _require(
            _REPOSITORY_PATTERN.fullmatch(artifact.index_repository) is not None,
            "index-repository",
        )
        _require(_is_digest(artifact.index_digest), "index-digest")
        _require(
            _IDENTIFIER_PATTERN.fullmatch(artifact.expected_source_id) is not None,
            "expected-source-id",
        )
        _require(len(artifact.manifest_bytes) <= _MAX_MANIFEST_BYTES, "manifest-size")
        _require(_digest(artifact.manifest_bytes) == artifact.index_digest, "manifest-digest")

        manifest = _json_object(artifact.manifest_bytes, _MAX_MANIFEST_BYTES, "manifest-json")
        _require(manifest.get("schemaVersion") == _OCI_SCHEMA_VERSION, "manifest-schema-version")
        _require(manifest.get("mediaType") == OCI_IMAGE_MANIFEST_MEDIA_TYPE, "manifest-media-type")
        _require(manifest.get("artifactType") == OCI_CATALOG_INDEX_ARTIFACT_TYPE, "artifact-type")
        config_descriptor = _descriptor(manifest.get("config"), "config-descriptor")
        _require(config_descriptor.media_type == OCI_CATALOG_INDEX_CONFIG_MEDIA_TYPE, "config-media-type")
        layers = _list(manifest.get("layers"), "layers-shape")
        _require(len(layers) == 1, "layer-set")
        index_descriptor = _descriptor(layers[0], "layer-descriptor")
        _require(
            index_descriptor.media_type == OCI_CATALOG_INDEX_CONTENT_MEDIA_TYPE,
            "layer-media-type",
        )
        _require(0 < config_descriptor.size <= _MAX_CONFIG_BYTES, "config-descriptor")
        _require(0 < index_descriptor.size <= _MAX_INDEX_BYTES, "layer-descriptor")
        return CatalogIndexManifestPlan(
            index_repository=artifact.index_repository,
            index_digest=artifact.index_digest,
            expected_source_id=artifact.expected_source_id,
            config_descriptor=config_descriptor,
            content_descriptor=index_descriptor,
        )


def _descriptor(value: object, code: str) -> OciBlobDescriptor:
    descriptor = _mapping(value, code)
    media_type = descriptor.get("mediaType")
    digest = descriptor.get("digest")
    size = descriptor.get("size")
    _require(isinstance(media_type, str) and bool(media_type), code)
    _require(isinstance(digest, str) and _is_digest(digest), code)
    _require(isinstance(size, int) and not isinstance(size, bool) and size >= 0, code)
    return OciBlobDescriptor(
        media_type=cast("str", media_type),
        digest=cast("str", digest),
        size=cast("int", size),
    )


def _blob(descriptor: OciBlobDescriptor, blobs: Mapping[str, bytes], code: str) -> bytes:
    digest = descriptor.digest
    content = blobs.get(digest)
    if not isinstance(content, bytes):
        _fail(code)
    _require(len(content) == descriptor.size, code)
    _require(_digest(content) == digest, code)
    return content


def _canonical_object(raw: bytes, maximum_bytes: int, json_code: str, canonical_code: str) -> Mapping[str, Any]:
    value = _json_object(raw, maximum_bytes, json_code)
    _require(raw == _canonical_json(value), canonical_code)
    return value


def _json_object(raw: bytes, maximum_bytes: int, code: str) -> Mapping[str, Any]:
    _require(len(raw) <= maximum_bytes, code)
    try:
        value = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)
    return _mapping(value, code)


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return cast("Mapping[str, Any]", value)


def _list(value: object, code: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(code)
    return value


def _parse_issued_at(value: object) -> datetime:
    if not isinstance(value, str):
        _fail("catalog-index-issued-at")
    try:
        return datetime.fromisoformat(value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        _fail("catalog-index-issued-at")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("json-duplicate-key")
        result[key] = value
    return result


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


def _is_digest(value: str) -> bool:
    return _DIGEST_PATTERN.fullmatch(value) is not None


def _require(condition: object, code: str) -> None:
    if not condition:
        _fail(code)


def _fail(code: str) -> NoReturn:
    raise CatalogIndexArtifactVerificationError(code)
