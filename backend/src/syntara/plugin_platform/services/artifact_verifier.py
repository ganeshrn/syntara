"""Bounded, offline verification of a compiled Syntara OCI plugin artifact.

Registry transport and signature/trust policy intentionally sit outside this
module.  Its job is narrower and must happen first: prove that the supplied
bytes match their OCI descriptors and form the one canonical artifact shape
that later trust, policy, and persistence stages may consume.
"""

from __future__ import annotations

import gzip
import json
import re
import tarfile
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
from typing import IO, Any, NoReturn, cast

from syntara.plugin_platform.exceptions import PluginArtifactVerificationError

OCI_IMAGE_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
OCI_ARTIFACT_TYPE = "application/vnd.syntara.plugin.v1"
OCI_CONFIG_MEDIA_TYPE = "application/vnd.syntara.plugin.config.v1+json"
OCI_PLUGIN_MANIFEST_MEDIA_TYPE = "application/vnd.syntara.plugin.manifest.v1+yaml"
OCI_CONTENT_BUNDLE_MEDIA_TYPE = "application/vnd.syntara.plugin.content.v1.tar+gzip"

_DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
_REPOSITORY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,254}$")
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_CONFIG_BYTES = 1_048_576
_MAX_COMPRESSED_BUNDLE_BYTES = 4 * 1_048_576
_MAX_EXPANDED_BUNDLE_BYTES = 16 * 1_048_576
_MAX_ASSET_BYTES = 1_048_576
_MAX_ASSETS = 100
_MAX_COMPRESSION_RATIO = 100
_OCI_SCHEMA_VERSION = 2
_BUNDLE_FILE_MODE = 0o644
_MAX_ASSET_PATH_LENGTH = 1024
_ASCII_PRINTABLE_MIN = 32
_ASCII_DELETE = 127
_SCHEMA_SOURCE_FIELDS = frozenset({"configuration", "credentialSchema", "error", "input", "output"})
_HTTP_MAPPING_SOURCE_FIELDS = frozenset({"runtime/operation/requestMap", "runtime/operation/responseMap"})


@dataclass(frozen=True)
class PluginArtifactInput:
    """All immutable response bytes supplied by a registry transport boundary."""

    artifact_repository: str
    artifact_digest: str
    manifest_bytes: bytes
    blobs: Mapping[str, bytes]


@dataclass(frozen=True)
class PluginArtifactBlobDescriptor:
    """One bounded, digest-addressed OCI blob declared by a plugin manifest."""

    media_type: str
    digest: str
    size: int


@dataclass(frozen=True)
class PluginArtifactManifestPlan:
    """A safe plugin-blob fetch plan obtained before any blob is decoded.

    The immutable manifest digest can be sent to a source trust evaluator
    before the caller retrieves the plugin descriptor, config, or content
    bundle.  The plan intentionally carries only validated OCI descriptors.
    """

    artifact_repository: str
    artifact_digest: str
    config_descriptor: PluginArtifactBlobDescriptor
    plugin_manifest_descriptor: PluginArtifactBlobDescriptor
    content_bundle_descriptor: PluginArtifactBlobDescriptor


@dataclass(frozen=True)
class VerifiedPluginDocumentation:
    """One bounded UTF-8 Markdown document proven to belong to the artifact."""

    path: str
    digest: str
    content: str


@dataclass(frozen=True)
class VerifiedPluginArtifact:
    """Integrity-verified artifact data, not yet a trusted install candidate."""

    artifact_repository: str
    artifact_digest: str
    canonical_descriptor: Mapping[str, Any]
    asset_index: Mapping[str, Mapping[str, Any]]
    documentation: VerifiedPluginDocumentation | None


class PluginArtifactVerifier:
    """Verify bounded OCI artifact content before signature trust or persistence."""

    def verify(self, artifact: PluginArtifactInput) -> VerifiedPluginArtifact:
        """Verify all descriptors, canonical metadata, and inert bundle entries."""
        plan = self.inspect_manifest(artifact)

        config = _json_object(
            _blob(plan.config_descriptor, artifact.blobs, _MAX_CONFIG_BYTES, "config-blob"),
            _MAX_CONFIG_BYTES,
            "config-json",
        )
        _require(
            set(config) == {"apiVersion", "assetIndex", "kind", "pluginDescriptorDigest"},
            "config-shape",
        )
        _require(config.get("apiVersion") == "syntara.plugin.config/v1alpha1", "config-version")
        _require(config.get("kind") == "PluginArtifactConfig", "config-kind")
        descriptor_bytes = _blob(
            plan.plugin_manifest_descriptor,
            artifact.blobs,
            _MAX_MANIFEST_BYTES,
            "plugin-manifest-blob",
        )
        descriptor = _canonical_descriptor(descriptor_bytes)
        _require(config.get("pluginDescriptorDigest") == _digest(_canonical_json(descriptor)), "descriptor-digest")
        asset_index = _asset_index(config.get("assetIndex"))
        _require(_canonical_json(_descriptor_assets(descriptor)) == _canonical_json(asset_index), "asset-index")
        _verify_asset_references(descriptor, asset_index)
        bundle = _blob(
            plan.content_bundle_descriptor,
            artifact.blobs,
            _MAX_COMPRESSED_BUNDLE_BYTES,
            "content-bundle-blob",
        )
        asset_contents = _verify_content_bundle(bundle, asset_index)
        return VerifiedPluginArtifact(
            artifact_repository=artifact.artifact_repository,
            artifact_digest=artifact.artifact_digest,
            canonical_descriptor=descriptor,
            asset_index=asset_index,
            documentation=_verified_documentation(descriptor, asset_index, asset_contents),
        )

    def inspect_manifest(self, artifact: PluginArtifactInput) -> PluginArtifactManifestPlan:
        """Validate the OCI envelope and return exact bounded blob descriptors.

        No plugin blob is loaded or decoded here. A caller that obtains the
        manifest from a registry can evaluate source trust using this immutable
        digest before fetching the three returned blobs.
        """
        _require(
            _REPOSITORY_PATTERN.fullmatch(artifact.artifact_repository) is not None
            and all(part not in {".", ".."} for part in artifact.artifact_repository.split("/")),
            "repository",
        )
        _require(_is_digest(artifact.artifact_digest), "artifact-digest")
        _require(len(artifact.manifest_bytes) <= _MAX_MANIFEST_BYTES, "manifest-size")
        _require(_digest(artifact.manifest_bytes) == artifact.artifact_digest, "manifest-digest")

        manifest = _json_object(artifact.manifest_bytes, _MAX_MANIFEST_BYTES, "manifest-json")
        _require(manifest.get("schemaVersion") == _OCI_SCHEMA_VERSION, "manifest-schema-version")
        _require(manifest.get("mediaType") == OCI_IMAGE_MANIFEST_MEDIA_TYPE, "manifest-media-type")
        _require(manifest.get("artifactType") == OCI_ARTIFACT_TYPE, "artifact-type")
        config_descriptor = _descriptor(manifest.get("config"), "config-descriptor")
        _require(config_descriptor.media_type == OCI_CONFIG_MEDIA_TYPE, "config-media-type")
        layers = _list(manifest.get("layers"), "layers-shape")

        layer_descriptors = [_descriptor(layer, "layer-descriptor") for layer in layers]
        by_media_type: dict[str, PluginArtifactBlobDescriptor] = {}
        for layer in layer_descriptors:
            media_type = layer.media_type
            _require(
                media_type in {OCI_PLUGIN_MANIFEST_MEDIA_TYPE, OCI_CONTENT_BUNDLE_MEDIA_TYPE},
                "layer-media-type",
            )
            _require(media_type not in by_media_type, "layer-duplicate")
            by_media_type[media_type] = layer
        _require(
            set(by_media_type) == {OCI_PLUGIN_MANIFEST_MEDIA_TYPE, OCI_CONTENT_BUNDLE_MEDIA_TYPE},
            "layer-set",
        )
        plugin_manifest_descriptor = by_media_type[OCI_PLUGIN_MANIFEST_MEDIA_TYPE]
        content_bundle_descriptor = by_media_type[OCI_CONTENT_BUNDLE_MEDIA_TYPE]
        _require(0 < config_descriptor.size <= _MAX_CONFIG_BYTES, "config-descriptor")
        _require(0 < plugin_manifest_descriptor.size <= _MAX_MANIFEST_BYTES, "layer-descriptor")
        _require(0 < content_bundle_descriptor.size <= _MAX_COMPRESSED_BUNDLE_BYTES, "layer-descriptor")
        return PluginArtifactManifestPlan(
            artifact_repository=artifact.artifact_repository,
            artifact_digest=artifact.artifact_digest,
            config_descriptor=config_descriptor,
            plugin_manifest_descriptor=plugin_manifest_descriptor,
            content_bundle_descriptor=content_bundle_descriptor,
        )


def _descriptor(value: object, code: str) -> PluginArtifactBlobDescriptor:
    descriptor = _mapping(value, code)
    media_type = descriptor.get("mediaType")
    digest = descriptor.get("digest")
    size = descriptor.get("size")
    _require(isinstance(media_type, str) and media_type, code)
    _require(isinstance(digest, str) and _is_digest(digest), code)
    _require(isinstance(size, int) and not isinstance(size, bool) and size >= 0, code)
    return PluginArtifactBlobDescriptor(
        media_type=cast("str", media_type),
        digest=cast("str", digest),
        size=cast("int", size),
    )


def _blob(
    descriptor: PluginArtifactBlobDescriptor,
    blobs: Mapping[str, bytes],
    maximum_bytes: int,
    code: str,
) -> bytes:
    digest = descriptor.digest
    content = blobs.get(digest)
    if not isinstance(content, bytes):
        _fail(code)
    _require(len(content) == descriptor.size, code)
    _require(len(content) <= maximum_bytes, code)
    _require(_digest(content) == digest, code)
    return content


def _canonical_descriptor(raw: bytes) -> Mapping[str, Any]:
    descriptor = _json_object(raw, _MAX_MANIFEST_BYTES, "plugin-manifest-json")
    _require(raw == _canonical_json(descriptor), "plugin-manifest-canonical")
    _require(descriptor.get("apiVersion") == "syntara.io/v1alpha1", "plugin-version")
    _require(descriptor.get("kind") == "PluginDescriptor", "plugin-kind")
    metadata = _mapping(descriptor.get("metadata"), "plugin-metadata")
    for field in ("namespace", "name", "version", "displayName", "description"):
        _require(isinstance(metadata.get(field), str) and bool(metadata[field]), "plugin-metadata")
    spec = _mapping(descriptor.get("spec"), "plugin-spec")
    _require(isinstance(spec.get("targets"), list), "plugin-targets")
    return descriptor


def _descriptor_assets(descriptor: Mapping[str, Any]) -> Mapping[str, Any]:
    spec = _mapping(descriptor.get("spec"), "plugin-spec")
    return _mapping(spec.get("assets"), "plugin-assets")


def _asset_index(value: object) -> Mapping[str, Mapping[str, Any]]:
    raw_index = _mapping(value, "asset-index")
    _require(len(raw_index) <= _MAX_ASSETS, "asset-index")
    index: dict[str, Mapping[str, Any]] = {}
    for path, raw_metadata in raw_index.items():
        _require(isinstance(path, str), "asset-path")
        _validate_asset_path(path)
        metadata = _mapping(raw_metadata, "asset-index")
        _require(
            set(metadata) == {"canonicalDigest", "digest", "mediaType", "references", "size"},
            "asset-index",
        )
        _require(
            isinstance(metadata.get("canonicalDigest"), str) and _is_digest(metadata["canonicalDigest"]), "asset-index"
        )
        _require(isinstance(metadata.get("digest"), str) and _is_digest(metadata["digest"]), "asset-index")
        _require(
            metadata.get("mediaType")
            in {
                "application/json",
                "application/schema+json",
                "application/schema+yaml",
                "application/yaml",
                "text/markdown",
            },
            "asset-index",
        )
        _require(
            isinstance(metadata.get("size"), int)
            and not isinstance(metadata["size"], bool)
            and 0 <= metadata["size"] <= _MAX_ASSET_BYTES,
            "asset-index",
        )
        references = _string_list(metadata.get("references"), "asset-index")
        _require(bool(references), "asset-index")
        _require(references == sorted(set(references)), "asset-index")
        index[path] = dict(metadata)
    return index


def _verify_asset_references(descriptor: Mapping[str, Any], asset_index: Mapping[str, Mapping[str, Any]]) -> None:
    spec = _mapping(descriptor.get("spec"), "plugin-spec")
    targets = _list(spec.get("targets"), "plugin-targets")
    by_source_path: dict[str, Mapping[str, Any]] = {}
    for raw_target in targets:
        target = _mapping(raw_target, "plugin-targets")
        source_path = target.get("sourcePath")
        if not isinstance(source_path, str) or source_path in by_source_path:
            _fail("plugin-targets")
        by_source_path[source_path] = target

    documentation = spec.get("documentation")
    documentation_path: str | None = None
    documentation_digest: str | None = None
    if documentation is not None:
        documentation_mapping = _mapping(documentation, "plugin-documentation")
        _require(set(documentation_mapping) == {"digest", "path"}, "plugin-documentation")
        path = documentation_mapping.get("path")
        digest = documentation_mapping.get("digest")
        _require(isinstance(path, str) and path.endswith(".md"), "plugin-documentation")
        _require(isinstance(digest, str) and _is_digest(digest), "plugin-documentation")
        documentation_path = path
        documentation_digest = digest

    for path, asset in asset_index.items():
        for reference in _string_list(asset.get("references"), "asset-index"):
            if reference == "plugin.yaml#/spec/documentation":
                _require(
                    documentation_path == path
                    and documentation_digest == asset["digest"]
                    and asset["canonicalDigest"] == asset["digest"]
                    and asset["mediaType"] == "text/markdown",
                    "asset-reference",
                )
                continue
            source_path, separator, field = reference.partition("#/spec/")
            _require(separator == "#/spec/", "asset-reference")
            _require(asset["mediaType"] == _reference_media_type(path, field), "asset-reference")
            referenced_target = by_source_path.get(source_path)
            if referenced_target is None:
                _fail("asset-reference")
            value = _target_document_value(referenced_target, field)
            _require(isinstance(value, Mapping), "asset-reference")
            _require(_digest(_canonical_json(value)) == asset["canonicalDigest"], "asset-reference")
    if documentation_path is not None:
        document_asset = asset_index.get(documentation_path)
        if document_asset is None:
            _fail("plugin-documentation")
        _require(
            "plugin.yaml#/spec/documentation" in _string_list(document_asset.get("references"), "asset-index"),
            "plugin-documentation",
        )


def _verify_content_bundle(bundle: bytes, asset_index: Mapping[str, Mapping[str, Any]]) -> Mapping[str, bytes]:
    _require(len(bundle) <= _MAX_COMPRESSED_BUNDLE_BYTES, "content-bundle-size")
    seen: set[str] = set()
    contents: dict[str, bytes] = {}
    expanded_bytes = 0
    try:
        with (
            gzip.GzipFile(fileobj=BytesIO(bundle), mode="rb") as compressed,
            tarfile.open(fileobj=compressed, mode="r|") as archive,
        ):
            for member in archive:
                _require(member.isfile(), "content-bundle-entry")
                _validate_asset_path(member.name)
                _require(member.name not in seen and member.name in asset_index, "content-bundle-entry")
                _require(
                    member.mode == _BUNDLE_FILE_MODE
                    and member.uid == 0
                    and member.gid == 0
                    and member.mtime == 0
                    and not member.uname
                    and not member.gname
                    and not member.linkname
                    and not member.sparse
                    and not member.pax_headers,
                    "content-bundle-metadata",
                )
                expected = asset_index[member.name]
                _require(member.size == expected["size"], "content-bundle-entry")
                stream = archive.extractfile(member)
                if stream is None:
                    _fail("content-bundle-entry")
                content, digest, size = _read_asset(stream, _MAX_ASSET_BYTES)
                expanded_bytes += size
                _require(expanded_bytes <= _MAX_EXPANDED_BUNDLE_BYTES, "content-bundle-expanded-size")
                _require(
                    len(bundle) == 0 or expanded_bytes <= len(bundle) * _MAX_COMPRESSION_RATIO,
                    "content-bundle-compression-ratio",
                )
                _require(digest == expected["digest"] and size == expected["size"], "content-bundle-entry")
                seen.add(member.name)
                contents[member.name] = content
    except (EOFError, OSError, tarfile.TarError):
        _fail("content-bundle-format")
    _require(seen == set(asset_index), "content-bundle-index")
    return contents


def _read_asset(stream: IO[bytes], maximum_bytes: int) -> tuple[bytes, str, int]:
    digest = sha256()
    total = 0
    content = bytearray()
    while chunk := stream.read(64 * 1024):
        total += len(chunk)
        _require(total <= maximum_bytes, "content-bundle-entry")
        digest.update(chunk)
        content.extend(chunk)
    return bytes(content), f"sha256:{digest.hexdigest()}", total


def _validate_asset_path(path: str) -> None:
    parts = PurePosixPath(path).parts
    _require(
        bool(path)
        and path == unicodedata.normalize("NFC", path)
        and len(path) <= _MAX_ASSET_PATH_LENGTH
        and not path.startswith("/")
        and "\\" not in path
        and all(ord(character) >= _ASCII_PRINTABLE_MIN and ord(character) != _ASCII_DELETE for character in path)
        and all(part not in {"", ".", ".."} and not part.startswith(".") for part in parts)
        and path.endswith((".json", ".yaml", ".yml", ".md")),
        "asset-path",
    )


def _reference_media_type(path: str, field: str) -> str:
    """Return the exact media type allowed for one typed asset field."""
    if field in _SCHEMA_SOURCE_FIELDS:
        prefix = "application/schema+"
    elif field in _HTTP_MAPPING_SOURCE_FIELDS:
        prefix = "application/"
    else:
        _fail("asset-reference")
    if path.endswith(".json"):
        return f"{prefix}json"
    if path.endswith((".yaml", ".yml")):
        return f"{prefix}yaml"
    _fail("asset-reference")
    raise AssertionError


def _target_document_value(target: Mapping[str, Any], field: str) -> object:
    """Read one explicitly supported nested document source without generic pointers."""
    if field in _SCHEMA_SOURCE_FIELDS:
        return target.get(field)
    if field in _HTTP_MAPPING_SOURCE_FIELDS:
        runtime = _mapping(target.get("runtime"), "asset-reference")
        operation = _mapping(runtime.get("operation"), "asset-reference")
        return operation.get(field.rsplit("/", maxsplit=1)[-1])
    _fail("asset-reference")
    raise AssertionError


def _verified_documentation(
    descriptor: Mapping[str, Any],
    asset_index: Mapping[str, Mapping[str, Any]],
    asset_contents: Mapping[str, bytes],
) -> VerifiedPluginDocumentation | None:
    spec = _mapping(descriptor.get("spec"), "plugin-spec")
    raw_documentation = spec.get("documentation")
    if raw_documentation is None:
        return None
    documentation = _mapping(raw_documentation, "plugin-documentation")
    path = documentation.get("path")
    digest = documentation.get("digest")
    if not isinstance(path, str) or not isinstance(digest, str):
        _fail("plugin-documentation")
    asset = asset_index.get(path)
    content = asset_contents.get(path)
    if asset is None or content is None:
        _fail("plugin-documentation")
    _require(asset.get("digest") == digest and asset.get("mediaType") == "text/markdown", "plugin-documentation")
    _require(b"\x00" not in content, "plugin-documentation")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        _fail("plugin-documentation")
    return VerifiedPluginDocumentation(path=path, digest=digest, content=text)


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


def _string_list(value: object, code: str) -> list[str]:
    values = _list(value, code)
    if not all(isinstance(item, str) for item in values):
        _fail(code)
    return cast("list[str]", values)


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
    raise PluginArtifactVerificationError(code)
