"""A deliberately narrow OCI Distribution client for configured plugin sources.

This client never lists a registry or constructs a repository from browser
input. A later source service validates and stores the locator. Reconciliation
uses it to resolve one catalog-index channel to a digest; catalog and install
flows use it to retrieve a preselected plugin artifact by exact digest and
only the blobs named by that immutable manifest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol
from urllib.parse import quote

import httpx

from syntara.core.lib.url_validation import validate_host_url
from syntara.plugin_platform.exceptions import CatalogRegistryClientError

if TYPE_CHECKING:
    import ssl
    from types import TracebackType
    from typing import Self

_OCI_IMAGE_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
_OCI_IMAGE_INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
_MANIFEST_ACCEPT = f"{_OCI_IMAGE_MANIFEST_MEDIA_TYPE}, {_OCI_IMAGE_INDEX_MEDIA_TYPE}"
_DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
_REPOSITORY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,254}$")
_CHANNEL_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_MAX_MANIFEST_BYTES = 1_048_576
_REDIRECT_STATUS_MINIMUM = 300
_REDIRECT_STATUS_EXCLUSIVE_MAXIMUM = 400
_SERVER_ERROR_MINIMUM_STATUS = 500
_RETRYABLE_STATUS_CODES = frozenset({408, 429})


@dataclass(frozen=True)
class CatalogIndexLocator:
    """The validated, non-secret route to one configured catalog index."""

    registry_origin: str
    index_repository: str
    channel: str

    def __post_init__(self) -> None:
        """Reject path injection and normalize the origin before any request is built."""
        object.__setattr__(self, "registry_origin", validate_host_url(self.registry_origin))
        if _REPOSITORY_PATTERN.fullmatch(self.index_repository) is None or any(
            part in {".", ".."} for part in self.index_repository.split("/")
        ):
            msg = "Catalog index repository must be a normalized OCI repository path"
            raise ValueError(msg)
        if _CHANNEL_PATTERN.fullmatch(self.channel) is None:
            msg = "Catalog index channel must be a normalized OCI tag"
            raise ValueError(msg)

    @property
    def repository(self) -> str:
        """Return the only OCI repository this locator may address."""
        return self.index_repository


@dataclass(frozen=True)
class PluginArtifactLocator:
    """The validated, non-secret route to one already-selected plugin artifact.

    Unlike a catalog index locator, this type intentionally has no mutable tag
    or channel. Its caller must supply an artifact digest obtained from an
    accepted catalog entry or a separately authorized direct-digest request.
    """

    registry_origin: str
    artifact_repository: str

    def __post_init__(self) -> None:
        """Reject path injection and normalize the origin before any request is built."""
        object.__setattr__(self, "registry_origin", validate_host_url(self.registry_origin))
        if _REPOSITORY_PATTERN.fullmatch(self.artifact_repository) is None or any(
            part in {".", ".."} for part in self.artifact_repository.split("/")
        ):
            msg = "Plugin artifact repository must be a normalized OCI repository path"
            raise ValueError(msg)

    @property
    def repository(self) -> str:
        """Return the only OCI repository this locator may address."""
        return self.artifact_repository


class OciRepositoryLocator(Protocol):
    """The validated repository coordinates needed by common OCI requests."""

    @property
    def registry_origin(self) -> str:
        """Return a normalized HTTPS registry origin."""
        ...

    @property
    def repository(self) -> str:
        """Return a validated OCI repository path."""
        ...


@dataclass(frozen=True)
class ResolvedCatalogIndexManifest:
    """A manifest pinned by both its registry header and its exact response bytes."""

    digest: str
    manifest_bytes: bytes


@dataclass(frozen=True)
class ResolvedPluginArtifactManifest:
    """An exact plugin-artifact manifest bound to its OCI digest and bytes."""

    digest: str
    manifest_bytes: bytes


class OciDistributionClient:
    """Read configured OCI source artifacts without general registry discovery."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create a redirect-disabled client or adopt an injected test/client pool."""
        self._timeout = httpx.Timeout(timeout_seconds)
        self._http_client = http_client or httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
        )
        self._owns_client = http_client is None

    async def close(self) -> None:
        """Release only a client pool created by this instance."""
        if self._owns_client:
            await self._http_client.aclose()

    async def __aenter__(self) -> Self:
        """Enter the catalog client context."""
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Close an owned client when the catalog operation completes."""
        await self.close()

    async def resolve_catalog_index(
        self,
        locator: CatalogIndexLocator,
        *,
        authorization_header: str | None = None,
        tls_context: ssl.SSLContext | None = None,
    ) -> ResolvedCatalogIndexManifest:
        """Resolve a movable channel, then refetch its exact immutable manifest.

        ``authorization_header`` is backend-resolved request material. It is
        neither persisted nor returned and this client never follows a registry
        challenge to another origin.
        """
        channel_digest, _channel_manifest = await self._fetch_manifest(
            locator,
            locator.channel,
            authorization_header=authorization_header,
            tls_context=tls_context,
        )
        pinned_digest, pinned_manifest = await self._fetch_manifest(
            locator,
            channel_digest,
            authorization_header=authorization_header,
            tls_context=tls_context,
        )
        if pinned_digest != channel_digest:
            _fail("pinned-manifest-digest", retryable=False)
        return ResolvedCatalogIndexManifest(digest=pinned_digest, manifest_bytes=pinned_manifest)

    async def fetch_plugin_artifact_manifest(
        self,
        locator: PluginArtifactLocator,
        *,
        digest: str,
        authorization_header: str | None = None,
        tls_context: ssl.SSLContext | None = None,
    ) -> ResolvedPluginArtifactManifest:
        """Fetch one preselected plugin manifest by immutable digest only.

        The method deliberately accepts no tag, catalog channel, or user-built
        OCI reference. It binds the supplied digest to both the registry header
        and response bytes before a caller can inspect descriptors or retrieve
        the config and bundle blobs.
        """
        if _DIGEST_PATTERN.fullmatch(digest) is None:
            msg = "Plugin artifact digest must be a SHA-256 OCI digest"
            raise ValueError(msg)
        resolved_digest, manifest_bytes = await self._fetch_manifest(
            locator,
            digest,
            authorization_header=authorization_header,
            tls_context=tls_context,
        )
        return ResolvedPluginArtifactManifest(digest=resolved_digest, manifest_bytes=manifest_bytes)

    async def fetch_blob(
        self,
        locator: OciRepositoryLocator,
        *,
        digest: str,
        maximum_bytes: int,
        authorization_header: str | None = None,
        tls_context: ssl.SSLContext | None = None,
    ) -> bytes:
        """Return one digest-addressed blob with a strict response-size limit."""
        if _DIGEST_PATTERN.fullmatch(digest) is None:
            msg = "Catalog blob digest must be a SHA-256 OCI digest"
            raise ValueError(msg)
        if maximum_bytes <= 0:
            msg = "Catalog blob maximum_bytes must be positive"
            raise ValueError(msg)
        _headers, content_bytes = await self._fetch_bounded_bytes(
            locator,
            f"blobs/{quote(digest, safe=':')}",
            accept="application/octet-stream",
            maximum_bytes=maximum_bytes,
            size_code="blob-size",
            content_length_code="blob-content-length",
            authorization_header=authorization_header,
            tls_context=tls_context,
        )
        if _digest(content_bytes) != digest:
            _fail("blob-digest", retryable=False)
        return content_bytes

    async def _fetch_manifest(
        self,
        locator: OciRepositoryLocator,
        reference: str,
        *,
        authorization_header: str | None,
        tls_context: ssl.SSLContext | None,
    ) -> tuple[str, bytes]:
        """Fetch one manifest and bind its returned header digest to exact bytes."""
        headers, content = await self._fetch_bounded_bytes(
            locator,
            f"manifests/{quote(reference, safe=':')}",
            accept=_MANIFEST_ACCEPT,
            maximum_bytes=_MAX_MANIFEST_BYTES,
            size_code="manifest-size",
            content_length_code="manifest-content-length",
            authorization_header=authorization_header,
            tls_context=tls_context,
        )
        content_type = headers.get("content-type", "").partition(";")[0].strip().lower()
        if content_type != _OCI_IMAGE_MANIFEST_MEDIA_TYPE:
            _fail("manifest-content-type", retryable=False)
        digest = headers.get("docker-content-digest")
        if digest is None or _DIGEST_PATTERN.fullmatch(digest) is None:
            _fail("manifest-header-digest", retryable=False)
        if _digest(content) != digest:
            _fail("manifest-digest", retryable=False)
        if _DIGEST_PATTERN.fullmatch(reference) is not None and reference != digest:
            _fail("pinned-manifest-digest", retryable=False)
        return digest, content

    async def _fetch_bounded_bytes(
        self,
        locator: OciRepositoryLocator,
        suffix: str,
        *,
        accept: str,
        maximum_bytes: int,
        size_code: str,
        content_length_code: str,
        authorization_header: str | None,
        tls_context: ssl.SSLContext | None,
    ) -> tuple[httpx.Headers, bytes]:
        """Stream a successful response without retaining unbounded registry data."""
        request_client = self._http_client
        owns_request_client = False
        if tls_context is not None:
            request_client = httpx.AsyncClient(
                timeout=self._timeout,
                verify=tls_context,
                follow_redirects=False,
            )
            owns_request_client = True
        try:
            async with request_client.stream(
                "GET",
                self._url(locator, suffix),
                headers=self._headers(accept, authorization_header),
            ) as response:
                _require_success(response)
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_bytes = int(content_length)
                    except ValueError:
                        _fail(content_length_code, retryable=False)
                    if declared_bytes < 0:
                        _fail(content_length_code, retryable=False)
                    if declared_bytes > maximum_bytes:
                        _fail(size_code, retryable=False)
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > maximum_bytes:
                        _fail(size_code, retryable=False)
                    content.extend(chunk)
                return response.headers, bytes(content)
        except httpx.RequestError as error:
            code = "request"
            raise CatalogRegistryClientError(code, retryable=True) from error
        finally:
            if owns_request_client:
                await request_client.aclose()

    @staticmethod
    def _headers(accept: str, authorization_header: str | None) -> dict[str, str]:
        """Build request headers without retaining optional credential material."""
        headers = {"Accept": accept}
        if authorization_header is not None:
            headers["Authorization"] = authorization_header
        return headers

    @staticmethod
    def _url(locator: OciRepositoryLocator, suffix: str) -> str:
        """Construct an OCI API URL from prevalidated fixed segments only."""
        return f"{locator.registry_origin}/v2/{locator.repository}/{suffix}"


def _digest(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


def _require_success(response: httpx.Response) -> None:
    """Map an upstream response to a stable, non-reflective retry classification."""
    if _REDIRECT_STATUS_MINIMUM <= response.status_code < _REDIRECT_STATUS_EXCLUSIVE_MAXIMUM:
        _fail("redirect", retryable=False)
    if response.status_code != httpx.codes.OK:
        retryable = (
            response.status_code >= _SERVER_ERROR_MINIMUM_STATUS or response.status_code in _RETRYABLE_STATUS_CODES
        )
        _fail("http-status", retryable=retryable)


def _fail(code: str, *, retryable: bool) -> None:
    raise CatalogRegistryClientError(code, retryable=retryable)
