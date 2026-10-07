"""Shared trust-admission contract for one digest-pinned plugin artifact."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from syntara.plugin_platform.source_profile import STANDARD_CATALOG_SOURCE_MODE, CatalogSourceMode

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class PluginArtifactTrustRequest:
    """Non-secret artifact identity submitted to a configured source policy."""

    source_id: str
    registry_origin: str
    artifact_repository: str
    artifact_digest: str
    namespace: str
    name: str
    version: str
    trust_policy_ref: str
    mirror_policy: Literal["direct", "digest-preserving"]
    source_mode: CatalogSourceMode = STANDARD_CATALOG_SOURCE_MODE


@dataclass(frozen=True)
class PluginArtifactTrustDecision:
    """An admitted plugin-artifact trust decision with safe inspection evidence."""

    summary: Mapping[str, str | int | bool | None]


class PluginArtifactTrustEvaluator(Protocol):
    """Verify one plugin artifact digest against the configured source policy."""

    async def evaluate_plugin_artifact(self, request: PluginArtifactTrustRequest) -> PluginArtifactTrustDecision:
        """Return only after the immutable plugin artifact digest has been admitted."""
