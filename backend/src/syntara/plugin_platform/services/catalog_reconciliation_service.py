"""Stage trusted catalog indexes as immutable local discovery projections.

The caller must complete registry transport, signature verification, and trust
policy evaluation before calling this service.  This module deliberately does
not resolve opaque references, contact a registry, install a plugin, or expose
workflow routes.  It validates the source-local replay/scope rules and commits
one complete catalog projection only after every entry is acceptable.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest
from typing import TYPE_CHECKING, Any, Literal, cast

from sqlmodel import col, select

from syntara.core.exceptions import SafeValueError
from syntara.core.jsonb_limits import validate_jsonb_size
from syntara.plugin_platform.exceptions import (
    CatalogSourceConfigurationError,
    CatalogSourceDisabledError,
    CatalogSourceNotFoundError,
)
from syntara.plugin_platform.models.catalog import (
    CatalogPluginVersion,
    PluginSource,
    PluginSourceReconciliation,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlmodel.ext.asyncio.session import AsyncSession

    from syntara.plugin_platform.services.catalog_index_verifier import VerifiedCatalogIndexArtifact

_JSON_FIELD_MAX_BYTES = 1_048_576


@dataclass(frozen=True)
class CatalogReconciliationDecision:
    """Pure outcome of applying a verified index to one source's checkpoint."""

    state: Literal["accepted", "rejected", "stale"]
    failure_code: str | None
    catalog_generation: int
    catalog_index_digest: str
    previous_index_digest: str | None
    issued_at: datetime
    expires_at: datetime
    entry_count: int
    entries: tuple[dict[str, Any], ...]
    idempotent: bool = False


@dataclass(frozen=True)
class CatalogReconciliationResult:
    """Persisted result safe for a future administration API to expose."""

    reconciliation_id: UUID
    state: Literal["accepted", "rejected", "stale"]
    failure_code: str | None
    catalog_generation: int
    catalog_index_digest: str
    entry_count: int
    idempotent: bool


@dataclass(frozen=True)
class CatalogIndexObservation:
    """Normalized immutable coordinates and entries from one verified index."""

    generation: int
    index_digest: str
    previous_digest: str | None
    issued_at: datetime
    expires_at: datetime
    entry_count: int
    entries: tuple[dict[str, Any], ...]


def plan_catalog_reconciliation(
    source: PluginSource,
    artifact: VerifiedCatalogIndexArtifact,
    active_reconciliation: PluginSourceReconciliation | None,
    *,
    observed_at: datetime,
) -> CatalogReconciliationDecision:
    """Apply source-local replay, predecessor, expiry, and scope policy.

    This is intentionally side-effect free so the decision rules remain unit
    testable without a registry or database.  A rejected/stale decision still
    retains the observed immutable index coordinates for safe inspection; only
    an accepted decision may replace the source's active projection.
    """
    now = _as_utc(observed_at, "observed time")
    metadata, entries = _catalog_parts(artifact)
    observation = _catalog_observation(metadata, entries, artifact.index_digest)
    rejection_code = _static_rejection_code(source, artifact, metadata, observation)
    if rejection_code is not None:
        return _rejection("rejected", rejection_code, observation)
    if observation.expires_at <= now:
        return _rejection("stale", "CATALOG_INDEX_EXPIRED", observation)
    if active_reconciliation is not None and active_reconciliation.plugin_source_id != source.id:
        msg = "Catalog source points to a reconciliation owned by another source"
        raise CatalogSourceConfigurationError(msg)
    return _checkpoint_decision(observation, active_reconciliation)


class CatalogReconciliationService:
    """Persist a complete catalog projection after all external trust checks pass."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the service to one control-plane database session."""
        self._session = session

    async def stage_trusted_index(
        self,
        source_id: UUID,
        artifact: VerifiedCatalogIndexArtifact,
        *,
        trust_summary: Mapping[str, Any],
        initiated_by: UUID | None,
        initiation_kind: Literal["manual", "scheduled", "test"] = "manual",
        expected_source_fingerprint: str | None = None,
        observed_at: datetime | None = None,
    ) -> CatalogReconciliationResult:
        """Stage one trusted artifact and atomically activate it when accepted.

        ``artifact`` must come from :class:`CatalogIndexArtifactVerifier` and
        ``trust_summary`` must describe a completed signature/trust-policy
        decision. A future orchestration layer owns producing this handoff.
        """
        source = await self._source_for_update(source_id)
        if not source.enabled:
            raise CatalogSourceDisabledError
        if expected_source_fingerprint is not None and not compare_digest(
            expected_source_fingerprint,
            catalog_source_fingerprint(source),
        ):
            msg = "Catalog source changed during reconciliation"
            raise CatalogSourceConfigurationError(msg)
        active = await self._active_reconciliation(source)
        decision = plan_catalog_reconciliation(
            source,
            artifact,
            active,
            observed_at=observed_at or datetime.now(UTC),
        )
        if decision.idempotent:
            if active is None:
                msg = "Catalog reconciliation idempotency requires an active projection"
                raise CatalogSourceConfigurationError(msg)
            return _result_from_reconciliation(active, idempotent=True)

        verification_summary = validate_jsonb_size(
            {
                "artifactVerifier": {"contractBundleDigest": artifact.contract_bundle_digest},
                "trust": dict(trust_summary),
            },
            field_name="catalog verification summary",
            max_bytes=_JSON_FIELD_MAX_BYTES,
        )
        reconciliation = PluginSourceReconciliation(
            plugin_source_id=source.id,
            state=decision.state,
            catalog_generation=decision.catalog_generation,
            catalog_index_digest=decision.catalog_index_digest,
            previous_index_digest=decision.previous_index_digest,
            issued_at=decision.issued_at,
            expires_at=decision.expires_at,
            entry_count=decision.entry_count,
            failure_code=decision.failure_code,
            verification_summary=verification_summary,
            initiated_by=initiated_by,
            initiation_kind=initiation_kind,
        )
        self._session.add(reconciliation)
        try:
            await self._session.flush()
            if decision.state == "accepted":
                self._session.add_all(
                    [
                        CatalogPluginVersion(
                            reconciliation_id=reconciliation.id,
                            namespace=entry["namespace"],
                            name=entry["name"],
                            version=entry["version"],
                            artifact_repository=entry["artifactRepository"],
                            artifact_digest=entry["artifactDigest"],
                            documentation_digest=entry["documentationDigest"],
                            runtime_ownerships=entry["runtimeOwnerships"],
                            deprecated=entry.get("deprecated", False),
                            withdrawn=entry.get("withdrawn", False),
                            canonical_entry=validate_jsonb_size(
                                entry,
                                field_name="catalog plugin entry",
                                max_bytes=_JSON_FIELD_MAX_BYTES,
                            ),
                        )
                        for entry in decision.entries
                    ]
                )
                source.active_reconciliation_id = reconciliation.id
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        return _result_from_reconciliation(reconciliation, idempotent=False)

    async def _source_for_update(self, source_id: UUID) -> PluginSource:
        result = await self._session.exec(
            select(PluginSource).where(col(PluginSource.id) == source_id).with_for_update()
        )
        source = result.one_or_none()
        if source is None:
            raise CatalogSourceNotFoundError
        return source

    async def _active_reconciliation(self, source: PluginSource) -> PluginSourceReconciliation | None:
        if source.active_reconciliation_id is None:
            return None
        result = await self._session.exec(
            select(PluginSourceReconciliation).where(
                col(PluginSourceReconciliation.id) == source.active_reconciliation_id
            )
        )
        return result.one_or_none()


def catalog_source_fingerprint(source: PluginSource) -> str:
    """Return a stable digest for fields that authorize one catalog fetch.

    The orchestrator snapshots this value before network I/O. The staging
    service recomputes it while holding the source lock, preventing a response
    fetched under an old registry, credential, custom-CA, or trust-policy
    configuration from becoming active after an administrator changes source
    configuration.
    """
    configuration = {
        "channel": source.channel,
        "credentialRef": source.credential_ref,
        "customCaRef": source.custom_ca_ref,
        "indexRepository": source.index_repository,
        "mirrorPolicy": source.mirror_policy,
        "registryOrigin": source.registry_origin,
        "repositoryScopes": sorted(source.repository_scopes),
        "sourceId": source.source_id,
        "sourceMode": source.source_mode,
        "trustPolicyRef": source.trust_policy_ref,
    }
    canonical = json.dumps(configuration, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return f"sha256:{sha256(canonical).hexdigest()}"


def _catalog_parts(artifact: VerifiedCatalogIndexArtifact) -> tuple[Mapping[str, Any], list[object]]:
    metadata = artifact.catalog_index.get("metadata")
    specification = artifact.catalog_index.get("spec")
    if not isinstance(metadata, Mapping) or not isinstance(specification, Mapping):
        msg = "Trusted catalog index has an invalid metadata or spec shape"
        raise CatalogSourceConfigurationError(msg)
    entries = specification.get("entries")
    if not isinstance(entries, list):
        msg = "Trusted catalog index has an invalid entry list"
        raise CatalogSourceConfigurationError(msg)
    return cast("Mapping[str, Any]", metadata), cast("list[object]", entries)


def _catalog_observation(
    metadata: Mapping[str, Any],
    entries: list[object],
    index_digest: str,
) -> CatalogIndexObservation:
    """Normalize one externally verified index into safe local decision input."""
    return CatalogIndexObservation(
        generation=_required_positive_int(metadata.get("generation"), "catalog generation"),
        index_digest=index_digest,
        previous_digest=_optional_string(metadata.get("previousIndexDigest"), "previous index digest"),
        issued_at=_timestamp(metadata.get("issuedAt"), "catalog issue time"),
        expires_at=_timestamp(metadata.get("expiresAt"), "catalog expiry time"),
        entry_count=len(entries),
        entries=tuple(_catalog_entry(entry) for entry in entries),
    )


def _static_rejection_code(
    source: PluginSource,
    artifact: VerifiedCatalogIndexArtifact,
    metadata: Mapping[str, Any],
    observation: CatalogIndexObservation,
) -> str | None:
    """Return source-binding, identity, or scope failures before checkpoint comparison."""
    if metadata.get("sourceId") != source.source_id:
        return "CATALOG_SOURCE_MISMATCH"
    if artifact.index_repository != source.index_repository:
        return "CATALOG_INDEX_REPOSITORY_MISMATCH"
    identities = {(entry["namespace"], entry["name"], entry["version"]) for entry in observation.entries}
    if len(identities) != len(observation.entries):
        return "CATALOG_IDENTITY_CONFLICT"
    if any(not _is_in_scope(entry["artifactRepository"], source.repository_scopes) for entry in observation.entries):
        return "CATALOG_ARTIFACT_OUT_OF_SCOPE"
    return None


def _checkpoint_decision(
    observation: CatalogIndexObservation,
    active_reconciliation: PluginSourceReconciliation | None,
) -> CatalogReconciliationDecision:
    """Compare one valid observation with the immutable accepted high-water mark."""
    if active_reconciliation is None:
        return _initial_checkpoint_decision(observation)

    _require_active_reconciliation(active_reconciliation)
    active_generation = cast("int", active_reconciliation.catalog_generation)
    active_digest = cast("str", active_reconciliation.catalog_index_digest)
    if observation.generation <= active_generation:
        return _nonadvancing_checkpoint_decision(observation, active_generation, active_digest)
    return _advancing_checkpoint_decision(observation, active_digest)


def _initial_checkpoint_decision(observation: CatalogIndexObservation) -> CatalogReconciliationDecision:
    """Accept only a root index when the source has no accepted checkpoint."""
    if observation.previous_digest is not None:
        return _rejection("rejected", "CATALOG_PREDECESSOR_UNANCHORED", observation)
    return _accepted(observation)


def _nonadvancing_checkpoint_decision(
    observation: CatalogIndexObservation,
    active_generation: int,
    active_digest: str,
) -> CatalogReconciliationDecision:
    """Reject replays/conflicts while making an exact unexpired retry idempotent."""
    if observation.generation < active_generation:
        return _rejection("rejected", "CATALOG_GENERATION_REPLAYED", observation)
    if observation.index_digest == active_digest:
        return _accepted(observation, idempotent=True)
    return _rejection("rejected", "CATALOG_GENERATION_CONFLICT", observation)


def _advancing_checkpoint_decision(
    observation: CatalogIndexObservation,
    active_digest: str,
) -> CatalogReconciliationDecision:
    """Accept only an advancing index that links to the accepted predecessor."""
    if observation.previous_digest != active_digest:
        return _rejection("rejected", "CATALOG_PREDECESSOR_MISMATCH", observation)
    return _accepted(observation)


def _catalog_entry(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        msg = "Trusted catalog index has an invalid entry"
        raise CatalogSourceConfigurationError(msg)
    entry = dict(value)
    required = {
        "namespace",
        "name",
        "version",
        "artifactRepository",
        "artifactDigest",
        "documentationDigest",
        "runtimeOwnerships",
    }
    string_fields = required - {"runtimeOwnerships"}
    ownerships = entry.get("runtimeOwnerships")
    if (
        not required.issubset(entry)
        or any(not isinstance(entry[field], str) for field in string_fields)
        or not isinstance(ownerships, list)
        or not ownerships
        or len(ownerships) != len(set(ownerships))
        or any(ownership not in {"platform-runner", "custom-workload"} for ownership in ownerships)
    ):
        msg = "Trusted catalog index entry has invalid required fields"
        raise CatalogSourceConfigurationError(msg)
    if not isinstance(entry.get("deprecated", False), bool) or not isinstance(entry.get("withdrawn", False), bool):
        msg = "Trusted catalog index entry has invalid lifecycle flags"
        raise CatalogSourceConfigurationError(msg)
    return entry


def _require_active_reconciliation(reconciliation: PluginSourceReconciliation) -> None:
    if (
        reconciliation.state != "accepted"
        or reconciliation.catalog_generation is None
        or reconciliation.catalog_index_digest is None
    ):
        msg = "Catalog source points to an invalid active reconciliation"
        raise CatalogSourceConfigurationError(msg)


def _required_positive_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        msg = f"Trusted catalog index has an invalid {field_name}"
        raise CatalogSourceConfigurationError(msg)
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"Trusted catalog index has an invalid {field_name}"
        raise CatalogSourceConfigurationError(msg)
    return value


def _timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        msg = f"Trusted catalog index has an invalid {field_name}"
        raise CatalogSourceConfigurationError(msg)
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value)
    except ValueError as error:
        msg = f"Trusted catalog index has an invalid {field_name}"
        raise CatalogSourceConfigurationError(msg) from error
    return _as_utc(parsed, field_name)


def _as_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = f"{field_name.capitalize()} must be timezone aware"
        raise SafeValueError(msg)
    return value.astimezone(UTC)


def _is_in_scope(repository: str, scopes: list[str]) -> bool:
    return any(repository == scope or repository.startswith(f"{scope}/") for scope in scopes)


def _rejection(
    state: Literal["rejected", "stale"],
    failure_code: str,
    observation: CatalogIndexObservation,
) -> CatalogReconciliationDecision:
    """Keep observed immutable coordinates for a rejected or stale index."""
    return CatalogReconciliationDecision(
        state=state,
        failure_code=failure_code,
        catalog_generation=observation.generation,
        catalog_index_digest=observation.index_digest,
        previous_index_digest=observation.previous_digest,
        issued_at=observation.issued_at,
        expires_at=observation.expires_at,
        entry_count=observation.entry_count,
        entries=(),
    )


def _accepted(observation: CatalogIndexObservation, *, idempotent: bool = False) -> CatalogReconciliationDecision:
    """Build an accepted decision, optionally reusing the current immutable snapshot."""
    return CatalogReconciliationDecision(
        state="accepted",
        failure_code=None,
        catalog_generation=observation.generation,
        catalog_index_digest=observation.index_digest,
        previous_index_digest=observation.previous_digest,
        issued_at=observation.issued_at,
        expires_at=observation.expires_at,
        entry_count=observation.entry_count,
        entries=() if idempotent else observation.entries,
        idempotent=idempotent,
    )


def _result_from_reconciliation(
    reconciliation: PluginSourceReconciliation,
    *,
    idempotent: bool,
) -> CatalogReconciliationResult:
    if reconciliation.catalog_generation is None or reconciliation.catalog_index_digest is None:
        msg = "Catalog reconciliation result is missing immutable index coordinates"
        raise CatalogSourceConfigurationError(msg)
    return CatalogReconciliationResult(
        reconciliation_id=reconciliation.id,
        state=reconciliation.state,
        failure_code=reconciliation.failure_code,
        catalog_generation=reconciliation.catalog_generation,
        catalog_index_digest=reconciliation.catalog_index_digest,
        entry_count=reconciliation.entry_count,
        idempotent=idempotent,
    )
