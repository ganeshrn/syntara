"""Server-owned, immutable bindings for platform-runner action drivers.

Plugin artifacts identify a versioned driver such as ``http.v1``. They never
choose a container image. A profile identity selects one immutable,
administrator-admitted revision; installation pins that exact revision into
the action record. Updating a driver therefore changes only future installs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from sqlmodel import col, select

from syntara.core.jsonb_limits import validate_jsonb_size
from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError
from syntara.plugin_platform.models.registry import RunnerProfile, RunnerProfileRevision

CONTAINER_RUNTIME_ABI: Literal["syntara.container/v1alpha1"] = "syntara.container/v1alpha1"
_DRIVER_PATTERN = r"^[a-z][a-z0-9-]*\.v[1-9][0-9]*$"
# Include ``:`` so a local or on-premises registry can use a non-default port
# (for example ``localhost:8443/namespace/runner@sha256:...``). The value is
# still an immutable image digest and is admitted server-side, never supplied
# by a workflow or plugin artifact.
_IMAGE_DIGEST_PATTERN = r"^[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}$"
_JSON_FIELD_MAX_BYTES = 65_536

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlmodel.ext.asyncio.session import AsyncSession


@dataclass(frozen=True)
class PlatformRunnerProfile:
    """One administrator-admitted, immutable runtime route for a driver."""

    revision_id: UUID
    driver: str
    image_digest: str
    runtime_abi: Literal["syntara.container/v1alpha1"] = CONTAINER_RUNTIME_ABI


class PlatformRunnerProfileResolver(Protocol):
    """Resolve a plugin-declared driver without accepting caller routing data."""

    async def resolve_platform_runner(self, driver: str) -> PlatformRunnerProfile:
        """Return the approved profile for one exact versioned driver."""


class RunnerProfileRevisionCandidate(BaseModel):
    """Trusted profile data supplied only after image admission has succeeded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    driver: str = Field(min_length=4, max_length=128, pattern=_DRIVER_PATTERN)
    image_digest: str = Field(min_length=73, max_length=512, pattern=_IMAGE_DIGEST_PATTERN)
    runtime_abi: Literal["syntara.container/v1alpha1"] = CONTAINER_RUNTIME_ABI
    verification_summary: dict[str, Any] = Field(default_factory=dict)

    @field_validator("verification_summary")
    @classmethod
    def _validate_bounded_evidence(cls, value: dict[str, Any], info: ValidationInfo) -> dict[str, Any]:
        """Keep only bounded, non-secret evidence beside the immutable revision."""
        return validate_jsonb_size(value, field_name=str(info.field_name), max_bytes=_JSON_FIELD_MAX_BYTES)


@dataclass(frozen=True)
class RunnerProfileRevisionResult:
    """The selected immutable runner route after an admission operation."""

    profile: PlatformRunnerProfile
    idempotent: bool


class RunnerProfileRevisionService:
    """Persist and activate immutable runner profiles after external admission.

    This service intentionally has no registry client, signer, or browser
    input. The caller must establish image provenance, scanning, and policy
    before supplying a ``RunnerProfileRevisionCandidate``.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind persistence to one request or administrator operation."""
        self._session = session

    async def record_admitted_revision(
        self,
        candidate: RunnerProfileRevisionCandidate,
        *,
        actor_id: UUID | None,
    ) -> RunnerProfileRevisionResult:
        """Create or select a digest-derived profile revision and activate it."""
        profile = await self._profile_for_driver(candidate.driver)
        if profile is None:
            profile = RunnerProfile(driver=candidate.driver)
            self._session.add(profile)
            await self._session.flush()

        revision_digest = runner_profile_revision_digest(candidate)
        revision = await self._revision_for_digest(revision_digest)
        if revision is not None:
            if revision.profile_id != profile.id:
                msg = "Runner profile revision digest is already assigned to a different driver"
                raise CatalogSourceConfigurationError(msg)
            if revision.runtime_abi != candidate.runtime_abi or revision.image_digest != candidate.image_digest:
                msg = "Runner profile revision digest does not match its persisted immutable binding"
                raise CatalogSourceConfigurationError(msg)
        else:
            revision = RunnerProfileRevision(
                profile_id=profile.id,
                revision_digest=revision_digest,
                runtime_abi=candidate.runtime_abi,
                image_digest=candidate.image_digest,
                verification_summary=candidate.verification_summary,
                admitted_by=actor_id,
            )
            self._session.add(revision)
            await self._session.flush()

        already_active = profile.active_revision_id == revision.id
        if not already_active:
            profile.active_revision_id = revision.id
            self._session.add(profile)
        await self._session.commit()
        return RunnerProfileRevisionResult(
            profile=PlatformRunnerProfile(
                revision_id=revision.id,
                driver=profile.driver,
                image_digest=revision.image_digest,
                runtime_abi=cast("Literal['syntara.container/v1alpha1']", revision.runtime_abi),
            ),
            idempotent=already_active,
        )

    async def _profile_for_driver(self, driver: str) -> RunnerProfile | None:
        query = select(RunnerProfile).where(col(RunnerProfile.driver) == driver).with_for_update()
        return (await self._session.exec(query)).one_or_none()

    async def _revision_for_digest(self, revision_digest: str) -> RunnerProfileRevision | None:
        query = select(RunnerProfileRevision).where(col(RunnerProfileRevision.revision_digest) == revision_digest)
        return (await self._session.exec(query)).one_or_none()


class DatabasePlatformRunnerProfileResolver:
    """Resolve a driver only through the active persisted runner-profile revision."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind lookup to the same transaction as plugin installation."""
        self._session = session

    async def resolve_platform_runner(self, driver: str) -> PlatformRunnerProfile:
        """Return the active immutable route or fail before installation persists."""
        query = (
            select(RunnerProfile, RunnerProfileRevision)
            .join(
                RunnerProfileRevision,
                col(RunnerProfile.active_revision_id) == col(RunnerProfileRevision.id),
            )
            .where(col(RunnerProfile.driver) == driver)
        )
        row = (await self._session.exec(query)).one_or_none()
        if row is None:
            msg = f"No approved platform runner profile is configured for driver {driver!r}"
            raise CatalogSourceConfigurationError(msg)
        profile, revision = row
        return PlatformRunnerProfile(
            revision_id=revision.id,
            driver=profile.driver,
            image_digest=revision.image_digest,
            runtime_abi=cast("Literal['syntara.container/v1alpha1']", revision.runtime_abi),
        )


@dataclass(frozen=True)
class StaticPlatformRunnerProfileResolver:
    """Small explicit resolver for tests and later deployment composition."""

    profiles: Mapping[str, PlatformRunnerProfile]

    async def resolve_platform_runner(self, driver: str) -> PlatformRunnerProfile:
        """Return only a profile whose server-owned identity matches the driver."""
        profile = self.profiles.get(driver)
        if profile is None or profile.driver != driver:
            msg = f"No approved platform runner profile is configured for driver {driver!r}"
            raise CatalogSourceConfigurationError(msg)
        return profile


def runner_profile_revision_digest(candidate: RunnerProfileRevisionCandidate) -> str:
    """Derive a stable identity from exactly the execution-relevant binding."""
    canonical = json.dumps(
        {
            "driver": candidate.driver,
            "image": candidate.image_digest,
            "runtimeAbi": candidate.runtime_abi,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{sha256(canonical).hexdigest()}"
