"""Pinned-key Cosign trust admission for catalog and plugin OCI artifacts.

This module deliberately implements a small, deployment-owned policy surface:
one configured policy pins one public key to one registry origin and bounded
repository/namespace scope.  A catalog source selects that policy by its
opaque reference.  The policy cannot add executable hooks, trust a mutable
tag, or fall back to an unverified result.

The first subprocess adapter is intentionally suitable for public,
system-trusted registries.  It runs Cosign with a fresh empty Docker
configuration so a developer's host credentials are never inherited.  A
private-registry credential bridge and custom-CA bridge must be explicit later
adapters; silently passing a source credential on a process command line
would expose it through process inspection.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlsplit

from syntara.core.lib.url_validation import validate_host_url
from syntara.plugin_platform.exceptions import (
    CatalogIndexTrustError,
    CatalogSourceConfigurationError,
    PluginArtifactTrustError,
)
from syntara.plugin_platform.services.catalog_reconciliation_orchestrator import (
    CatalogIndexTrustDecision,
    CatalogIndexTrustRequest,
)
from syntara.plugin_platform.services.plugin_artifact_trust import (
    PluginArtifactTrustDecision,
    PluginArtifactTrustRequest,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_SHA256_DIGEST_PREFIX = "sha256:"
_SHA256_DIGEST_LENGTH = len(_SHA256_DIGEST_PREFIX) + 64
_MAX_PUBLIC_KEY_BYTES = 64 * 1024
_MAX_POLICY_REFERENCE_LENGTH = 256
_MAX_REPOSITORY_PREFIX_LENGTH = 255
_MAX_NAMESPACE_LENGTH = 63
_DEFAULT_TIMEOUT_SECONDS = 30
_MAX_TIMEOUT_SECONDS = 300

_COSIGN_UNAVAILABLE = "COSIGN_UNAVAILABLE"
_COSIGN_START_FAILED = "COSIGN_START_FAILED"
_COSIGN_TIMEOUT = "COSIGN_TIMEOUT"
_COSIGN_VERIFICATION_FAILED = "COSIGN_VERIFICATION_FAILED"
_COSIGN_POLICY_REPOSITORY_DENIED = "COSIGN_POLICY_REPOSITORY_DENIED"
_COSIGN_POLICY_NAMESPACE_DENIED = "COSIGN_POLICY_NAMESPACE_DENIED"


class CosignVerificationError(Exception):
    """A safe stable reason returned by the local Cosign runner."""

    def __init__(self, code: str) -> None:
        """Retain a code without retaining process output or credential data."""
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CosignVerificationRequest:
    """One exact digest and public-key policy submitted to Cosign."""

    image_reference: str
    public_key_pem: str
    require_transparency_log: bool


class CosignVerificationRunner(Protocol):
    """Verify one OCI digest without exposing verifier implementation details."""

    async def verify(self, request: CosignVerificationRequest) -> None:
        """Raise ``CosignVerificationError`` unless the signature is admitted."""


class SubprocessCosignVerificationRunner:
    """Run a bounded non-interactive Cosign verification in an isolated environment."""

    def __init__(self, *, executable: str = "cosign", timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS) -> None:
        """Configure one local Cosign binary and a bounded verification timeout."""
        if not executable or "\x00" in executable:
            msg = "Cosign executable configuration is invalid"
            raise ValueError(msg)
        if not 1 <= timeout_seconds <= _MAX_TIMEOUT_SECONDS:
            msg = f"Cosign timeout must be between 1 and {_MAX_TIMEOUT_SECONDS} seconds"
            raise ValueError(msg)
        self._executable = executable
        self._timeout_seconds = timeout_seconds

    async def verify(self, request: CosignVerificationRequest) -> None:
        """Verify one immutable OCI digest using an ephemeral public-key file."""
        executable = shutil.which(self._executable)
        if executable is None:
            raise CosignVerificationError(_COSIGN_UNAVAILABLE)

        with tempfile.TemporaryDirectory(prefix="syntara-cosign-") as temporary_directory:
            temporary_path = Path(temporary_directory)
            public_key_path = temporary_path / "cosign.pub"
            _write_private_file(public_key_path, request.public_key_pem.encode("utf-8"))
            command = [
                executable,
                "verify",
                "--key",
                str(public_key_path),
                "--output",
                "json",
            ]
            if not request.require_transparency_log:
                command.append("--insecure-ignore-tlog")
            command.append(request.image_reference)

            # An empty Docker configuration prevents accidental use of the
            # workstation's pull credentials or credential helpers.  The
            # initial adapter supports only public, system-trusted registries.
            environment = {
                "DOCKER_CONFIG": temporary_directory,
                "PATH": os.environ.get("PATH", ""),
            }
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=environment,
                )
            except OSError as exc:
                raise CosignVerificationError(_COSIGN_START_FAILED) from exc
            try:
                await asyncio.wait_for(process.wait(), timeout=self._timeout_seconds)
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise CosignVerificationError(_COSIGN_TIMEOUT) from exc
            if process.returncode != 0:
                raise CosignVerificationError(_COSIGN_VERIFICATION_FAILED)


@dataclass(frozen=True)
class PinnedCosignKeyTrustPolicy:
    """Administrator-owned, pinned-key policy for one catalog source class."""

    reference: str
    registry_origin: str
    repository_prefixes: tuple[str, ...]
    plugin_namespaces: frozenset[str]
    public_key_pem: str
    require_transparency_log: bool = True

    def __post_init__(self) -> None:
        """Reject malformed policy data before it can form a Cosign invocation."""
        if (
            not self.reference
            or len(self.reference) > _MAX_POLICY_REFERENCE_LENGTH
            or any(character.isspace() for character in self.reference)
        ):
            msg = "Cosign trust policy reference is invalid"
            raise ValueError(msg)
        try:
            normalized_origin = validate_host_url(self.registry_origin)
        except ValueError as exc:
            msg = "Cosign trust policy registry origin is invalid"
            raise ValueError(msg) from exc
        if not normalized_origin.startswith("https://"):
            msg = "Cosign trust policy registry origin must use HTTPS"
            raise ValueError(msg)
        object.__setattr__(self, "registry_origin", normalized_origin)
        repository_prefixes = tuple(self.repository_prefixes)
        if not repository_prefixes or len(repository_prefixes) != len(set(repository_prefixes)):
            msg = "Cosign trust policy must contain unique repository prefixes"
            raise ValueError(msg)
        for prefix in repository_prefixes:
            _validate_repository_prefix(prefix)
        object.__setattr__(self, "repository_prefixes", repository_prefixes)
        plugin_namespaces = frozenset(self.plugin_namespaces)
        if not plugin_namespaces:
            msg = "Cosign trust policy must contain at least one plugin namespace"
            raise ValueError(msg)
        for namespace in plugin_namespaces:
            _validate_namespace(namespace)
        object.__setattr__(self, "plugin_namespaces", plugin_namespaces)
        if not isinstance(cast("object", self.require_transparency_log), bool):
            msg = "Cosign trust policy transparency-log mode must be boolean"
            raise TypeError(msg)
        encoded_public_key = self.public_key_pem.encode("utf-8")
        if (
            not self.public_key_pem.startswith("-----BEGIN PUBLIC KEY-----")
            or "-----END PUBLIC KEY-----" not in self.public_key_pem
            or len(encoded_public_key) > _MAX_PUBLIC_KEY_BYTES
        ):
            msg = "Cosign trust policy public key is invalid"
            raise ValueError(msg)

    @property
    def public_key_fingerprint(self) -> str:
        """Return a safe policy-evidence fingerprint, not the key itself."""
        return f"sha256:{sha256(self.public_key_pem.encode('utf-8')).hexdigest()}"


class PinnedCosignTrustEvaluator:
    """Apply configured pinned-key policies to indexes and plugin artifacts."""

    def __init__(
        self,
        policies: Mapping[str, PinnedCosignKeyTrustPolicy],
        *,
        runner: CosignVerificationRunner | None = None,
    ) -> None:
        """Bind immutable policy objects and one local verifier implementation."""
        if not policies:
            msg = "At least one Cosign trust policy is required"
            raise ValueError(msg)
        if any(reference != policy.reference for reference, policy in policies.items()):
            msg = "Cosign trust policy map keys must match policy references"
            raise ValueError(msg)
        self._policies = dict(policies)
        self._runner = runner or SubprocessCosignVerificationRunner()

    async def evaluate_catalog_index(self, request: CatalogIndexTrustRequest) -> CatalogIndexTrustDecision:
        """Verify a catalog-index artifact against its source-selected policy."""
        policy = self._policy_for(
            request.trust_policy_ref,
            registry_origin=request.registry_origin,
            repository=request.index_repository,
            namespace=None,
        )
        try:
            await self._runner.verify(
                CosignVerificationRequest(
                    image_reference=_image_reference(
                        request.registry_origin,
                        request.index_repository,
                        request.index_digest,
                    ),
                    public_key_pem=policy.public_key_pem,
                    require_transparency_log=policy.require_transparency_log,
                )
            )
        except CosignVerificationError as exc:
            raise CatalogIndexTrustError(exc.code) from exc
        return CatalogIndexTrustDecision(summary=_trust_summary(policy))

    async def evaluate_plugin_artifact(self, request: PluginArtifactTrustRequest) -> PluginArtifactTrustDecision:
        """Verify a plugin artifact and its namespace binding before decoding blobs."""
        policy = self._policy_for(
            request.trust_policy_ref,
            registry_origin=request.registry_origin,
            repository=request.artifact_repository,
            namespace=request.namespace,
        )
        try:
            await self._runner.verify(
                CosignVerificationRequest(
                    image_reference=_image_reference(
                        request.registry_origin,
                        request.artifact_repository,
                        request.artifact_digest,
                    ),
                    public_key_pem=policy.public_key_pem,
                    require_transparency_log=policy.require_transparency_log,
                )
            )
        except CosignVerificationError as exc:
            raise PluginArtifactTrustError(exc.code) from exc
        return PluginArtifactTrustDecision(summary=_trust_summary(policy))

    def _policy_for(
        self,
        reference: str,
        *,
        registry_origin: str,
        repository: str,
        namespace: str | None,
    ) -> PinnedCosignKeyTrustPolicy:
        policy = self._policies.get(reference)
        if policy is None:
            msg = "Configured catalog trust policy is unavailable"
            raise CatalogSourceConfigurationError(msg)
        try:
            normalized_origin = validate_host_url(registry_origin)
        except ValueError as exc:
            msg = "Catalog registry origin is invalid for Cosign verification"
            raise CatalogSourceConfigurationError(msg) from exc
        if normalized_origin != policy.registry_origin:
            msg = "Configured catalog trust policy is not authorized for this registry origin"
            raise CatalogSourceConfigurationError(msg)
        if not any(_repository_is_within_prefix(repository, prefix) for prefix in policy.repository_prefixes):
            msg = _COSIGN_POLICY_REPOSITORY_DENIED
            if namespace is None:
                raise CatalogIndexTrustError(msg)
            raise PluginArtifactTrustError(msg)
        if namespace is not None and namespace not in policy.plugin_namespaces:
            raise PluginArtifactTrustError(_COSIGN_POLICY_NAMESPACE_DENIED)
        return policy


def _write_private_file(path: Path, content: bytes) -> None:
    """Create a short-lived owner-only input file for the external verifier."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _validate_repository_prefix(value: str) -> None:
    if (
        not value
        or len(value) > _MAX_REPOSITORY_PREFIX_LENGTH
        or value.startswith("/")
        or value.endswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789._-/" for character in value)
    ):
        msg = "Cosign trust policy repository prefix is invalid"
        raise ValueError(msg)


def _validate_namespace(value: str) -> None:
    if (
        not value
        or len(value) > _MAX_NAMESPACE_LENGTH
        or value[0] < "a"
        or value[0] > "z"
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in value)
    ):
        msg = "Cosign trust policy plugin namespace is invalid"
        raise ValueError(msg)


def _repository_is_within_prefix(repository: str, prefix: str) -> bool:
    return repository == prefix or repository.startswith(f"{prefix}/")


def _image_reference(registry_origin: str, repository: str, digest: str) -> str:
    """Build a registry reference only from normalized source-owned inputs."""
    if (
        not digest.startswith(_SHA256_DIGEST_PREFIX)
        or len(digest) != _SHA256_DIGEST_LENGTH
        or any(character not in "0123456789abcdef" for character in digest[len(_SHA256_DIGEST_PREFIX) :])
    ):
        msg = "Catalog artifact digest is invalid for Cosign verification"
        raise CatalogSourceConfigurationError(msg)
    try:
        parsed = urlsplit(validate_host_url(registry_origin))
    except ValueError as exc:
        msg = "Catalog registry origin is invalid for Cosign verification"
        raise CatalogSourceConfigurationError(msg) from exc
    if parsed.scheme != "https" or not parsed.netloc:
        msg = "Catalog registry origin is invalid for Cosign verification"
        raise CatalogSourceConfigurationError(msg)
    _validate_repository_prefix(repository)
    return f"{parsed.netloc}/{repository}@{digest}"


def _trust_summary(policy: PinnedCosignKeyTrustPolicy) -> dict[str, str | bool]:
    """Return bounded non-secret evidence safe for catalog inspection records."""
    return {
        "verifier": "cosign",
        "policyRef": policy.reference,
        "signerKeySha256": policy.public_key_fingerprint,
        "transparencyLogRequired": policy.require_transparency_log,
    }
