"""Trust boundary for independently published plugin RuntimeBinding artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from syntara.plugin_platform.source_profile import STANDARD_CATALOG_SOURCE_MODE, CatalogSourceMode

ArtifactKind = Literal["runtime-binding", "runtime-binding-index"]


@dataclass(frozen=True)
class RuntimeBindingTrustRequest:
    """Non-secret identity for one verified binding or binding-index digest."""

    source_id: str
    registry_origin: str
    artifact_repository: str
    artifact_digest: str
    namespace: str
    name: str
    version: str
    artifact_kind: ArtifactKind
    trust_policy_ref: str
    mirror_policy: Literal["direct", "digest-preserving"]
    source_mode: CatalogSourceMode = STANDARD_CATALOG_SOURCE_MODE


@dataclass(frozen=True)
class RuntimeBindingTrustDecision:
    """Safe evidence that a binding signature satisfies source policy."""

    summary: dict[str, str | int | bool | None]


class RuntimeBindingTrustEvaluator(Protocol):
    """Verify binding and binding-index signatures against catalog source policy."""

    async def evaluate_runtime_binding(self, request: RuntimeBindingTrustRequest) -> RuntimeBindingTrustDecision:
        """Return only when the exact artifact digest is trusted."""
