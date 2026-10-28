"""Unit coverage for the bounded OCI Distribution catalog client."""

from __future__ import annotations

import ssl
from hashlib import sha256
from typing import Any

import httpx
import pytest

from syntara.plugin_platform.exceptions import CatalogRegistryClientError
from syntara.plugin_platform.services.oci_distribution_client import (
    CatalogIndexLocator,
    OciDistributionClient,
    PluginArtifactLocator,
)

_ORIGIN = "https://quay.example.test"
_REPOSITORY = "syntara/catalog-index"
_CHANNEL = "stable"


def _digest(content: bytes) -> str:
    return f"sha256:{sha256(content).hexdigest()}"


def _locator() -> CatalogIndexLocator:
    return CatalogIndexLocator(
        registry_origin=_ORIGIN,
        index_repository=_REPOSITORY,
        channel=_CHANNEL,
    )


def _client(handler: httpx.AsyncBaseTransport) -> OciDistributionClient:
    return OciDistributionClient(http_client=httpx.AsyncClient(transport=handler))


def _plugin_locator() -> PluginArtifactLocator:
    return PluginArtifactLocator(
        registry_origin=_ORIGIN,
        artifact_repository="syntara/plugins/github",
    )


async def test_resolve_channel_refetches_the_pinned_manifest_with_backend_authentication() -> None:
    manifest = b'{"schemaVersion":2}'
    digest = _digest(manifest)
    observed: list[dict[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.append(
            {
                "path": request.url.path,
                "accept": request.headers["accept"],
                "authorization": request.headers["authorization"],
            }
        )
        return httpx.Response(
            200,
            content=manifest,
            headers={
                "content-type": "application/vnd.oci.image.manifest.v1+json",
                "docker-content-digest": digest,
            },
        )

    client = _client(httpx.MockTransport(handler))
    try:
        resolved = await client.resolve_catalog_index(_locator(), authorization_header="Bearer registry-token")
    finally:
        await client.close()

    assert resolved.digest == digest
    assert resolved.manifest_bytes == manifest
    assert observed == [
        {
            "path": "/v2/syntara/catalog-index/manifests/stable",
            "accept": "application/vnd.oci.image.manifest.v1+json, application/vnd.oci.image.index.v1+json",
            "authorization": "Bearer registry-token",
        },
        {
            "path": f"/v2/syntara/catalog-index/manifests/{digest}",
            "accept": "application/vnd.oci.image.manifest.v1+json, application/vnd.oci.image.index.v1+json",
            "authorization": "Bearer registry-token",
        },
    ]


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (
            httpx.Response(302, headers={"location": "https://other.example.test"}),
            "redirect",
            False,
        ),
        (httpx.Response(401, text="sensitive registry output"), "http-status", False),
        (httpx.Response(503, text="sensitive registry output"), "http-status", True),
        (
            httpx.Response(
                200,
                content=b"{}",
                headers={
                    "content-type": "application/vnd.oci.image.manifest.v1+json",
                    "docker-content-digest": "sha256:" + "0" * 64,
                },
            ),
            "manifest-digest",
            False,
        ),
        (
            httpx.Response(
                200,
                headers={
                    "content-type": "application/vnd.oci.image.manifest.v1+json",
                    "content-length": "1048577",
                },
            ),
            "manifest-size",
            False,
        ),
    ],
)
async def test_client_rejects_unsafe_or_untrusted_registry_responses(
    response: httpx.Response, code: str, *, retryable: bool
) -> None:
    client = _client(httpx.MockTransport(lambda _request: response))
    try:
        with pytest.raises(CatalogRegistryClientError, match=code) as raised:
            await client.resolve_catalog_index(_locator())
    finally:
        await client.close()

    assert raised.value.retryable is retryable
    assert "sensitive" not in str(raised.value)


async def test_client_returns_only_a_bounded_blob_that_matches_its_digest() -> None:
    content = b"catalog-content"
    digest = _digest(content)
    observed: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["path"] = request.url.path
        observed["accept"] = request.headers["accept"]
        return httpx.Response(200, content=content, headers={"content-length": str(len(content))})

    client = _client(httpx.MockTransport(handler))
    try:
        blob = await client.fetch_blob(_locator(), digest=digest, maximum_bytes=len(content))
    finally:
        await client.close()

    assert blob == content
    assert observed == {
        "path": f"/v2/syntara/catalog-index/blobs/{digest}",
        "accept": "application/octet-stream",
    }


async def test_client_constructs_a_per_request_client_with_the_custom_tls_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source CA context must reach OCI transport without weakening verification."""
    content = b"catalog-content"
    digest = _digest(content)
    provided_context = ssl.create_default_context()
    captured_verify_values: list[ssl.SSLContext] = []
    original_client = httpx.AsyncClient

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, headers={"content-length": str(len(content))})

    def custom_ca_client(**kwargs: object) -> httpx.AsyncClient:
        verify = kwargs["verify"]
        assert isinstance(verify, ssl.SSLContext)
        timeout = kwargs["timeout"]
        follow_redirects = kwargs["follow_redirects"]
        assert isinstance(timeout, httpx.Timeout)
        assert isinstance(follow_redirects, bool)
        captured_verify_values.append(verify)
        return original_client(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
            follow_redirects=follow_redirects,
        )

    monkeypatch.setattr("syntara.plugin_platform.services.oci_distribution_client.httpx.AsyncClient", custom_ca_client)
    client = OciDistributionClient(http_client=original_client(transport=httpx.MockTransport(handler)))
    try:
        blob = await client.fetch_blob(
            _locator(),
            digest=digest,
            maximum_bytes=len(content),
            tls_context=provided_context,
        )
    finally:
        await client.close()

    assert blob == content
    assert captured_verify_values == [provided_context]


async def test_client_fetches_a_plugin_manifest_by_exact_digest_only() -> None:
    manifest = b'{"schemaVersion":2}'
    digest = _digest(manifest)
    observed: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["path"] = request.url.path
        observed["authorization"] = request.headers["authorization"]
        return httpx.Response(
            200,
            content=manifest,
            headers={
                "content-type": "application/vnd.oci.image.manifest.v1+json",
                "docker-content-digest": digest,
            },
        )

    client = _client(httpx.MockTransport(handler))
    try:
        resolved = await client.fetch_plugin_artifact_manifest(
            _plugin_locator(),
            digest=digest,
            authorization_header="Bearer registry-token",
        )
    finally:
        await client.close()

    assert resolved.digest == digest
    assert resolved.manifest_bytes == manifest
    assert observed == {
        "path": f"/v2/syntara/plugins/github/manifests/{digest}",
        "authorization": "Bearer registry-token",
    }


@pytest.mark.parametrize("reference", ["latest", "stable", "v1.2.3"])
async def test_client_refuses_plugin_manifest_tags(reference: str) -> None:
    client = _client(httpx.MockTransport(lambda _request: httpx.Response(500)))
    try:
        with pytest.raises(ValueError, match="artifact digest"):
            await client.fetch_plugin_artifact_manifest(_plugin_locator(), digest=reference)
    finally:
        await client.close()


@pytest.mark.parametrize(
    ("content", "headers", "code"),
    [
        (b"too-large", {"content-length": "9"}, "blob-size"),
        (b"content", {"content-length": "not-a-number"}, "blob-content-length"),
    ],
)
async def test_client_rejects_oversized_or_malformed_blob_responses(
    content: bytes, headers: dict[str, str], code: str
) -> None:
    client = _client(httpx.MockTransport(lambda _request: httpx.Response(200, content=content, headers=headers)))
    try:
        with pytest.raises(CatalogRegistryClientError, match=code):
            await client.fetch_blob(_locator(), digest=_digest(content), maximum_bytes=1)
    finally:
        await client.close()


@pytest.mark.parametrize(
    "locator",
    [
        {"registry_origin": "https://quay.example.test/unsafe", "index_repository": _REPOSITORY, "channel": _CHANNEL},
        {"registry_origin": _ORIGIN, "index_repository": "../catalog", "channel": _CHANNEL},
        {"registry_origin": _ORIGIN, "index_repository": "syntara/../catalog", "channel": _CHANNEL},
        {"registry_origin": _ORIGIN, "index_repository": _REPOSITORY, "channel": "stable/unsafe"},
    ],
)
def test_locator_rejects_path_injection(locator: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        CatalogIndexLocator(**locator)


def test_locator_allows_only_the_fixed_local_poc_http_origin() -> None:
    locator = CatalogIndexLocator(
        registry_origin="http://host.containers.internal:8443",
        index_repository=_REPOSITORY,
        channel=_CHANNEL,
    )

    assert locator.registry_origin == "http://host.containers.internal:8443"

    with pytest.raises(ValueError, match="scheme must be https"):
        CatalogIndexLocator(
            registry_origin="http://registry.example.test:8443",
            index_repository=_REPOSITORY,
            channel=_CHANNEL,
        )


@pytest.mark.parametrize(
    "locator",
    [
        {"registry_origin": "https://quay.example.test/unsafe", "artifact_repository": "syntara/plugins/github"},
        {"registry_origin": _ORIGIN, "artifact_repository": "../plugins/github"},
        {"registry_origin": _ORIGIN, "artifact_repository": "syntara/../plugins/github"},
    ],
)
def test_plugin_artifact_locator_rejects_path_injection(locator: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        PluginArtifactLocator(**locator)
