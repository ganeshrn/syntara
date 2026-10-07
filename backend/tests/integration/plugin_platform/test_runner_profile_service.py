"""Database-backed coverage for immutable platform-runner profile revisions."""

from __future__ import annotations

import pytest
from sqlmodel import select

from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError
from syntara.plugin_platform.models.registry import RunnerProfileRevision
from syntara.plugin_platform.services.runner_profile import (
    DatabasePlatformRunnerProfileResolver,
    RunnerProfileRevisionCandidate,
    RunnerProfileRevisionService,
)


def _candidate(image_character: str) -> RunnerProfileRevisionCandidate:
    """Create a trusted test-only input after hypothetical image admission."""
    return RunnerProfileRevisionCandidate(
        driver="http.v1",
        image_digest="quay.io/syntara/http-runner@sha256:" + image_character * 64,
        verification_summary={"imagePolicy": "admitted", "scan": "passed"},
    )


@pytest.mark.asyncio
async def test_admitted_profile_revision_is_immutable_and_new_installs_select_the_active_revision(
    test_db_session,
) -> None:
    """A profile update selects a new immutable route without modifying the old one."""
    service = RunnerProfileRevisionService(test_db_session)
    first = await service.record_admitted_revision(_candidate("a"), actor_id=None)
    repeat = await service.record_admitted_revision(_candidate("a"), actor_id=None)
    second = await service.record_admitted_revision(_candidate("b"), actor_id=None)

    resolver = DatabasePlatformRunnerProfileResolver(test_db_session)
    selected = await resolver.resolve_platform_runner("http.v1")
    revisions = list((await test_db_session.exec(select(RunnerProfileRevision))).all())

    assert repeat.idempotent is True
    assert first.profile.revision_id != second.profile.revision_id
    assert selected == second.profile
    assert {revision.id for revision in revisions} == {
        first.profile.revision_id,
        second.profile.revision_id,
    }
    first_revision = next(revision for revision in revisions if revision.id == first.profile.revision_id)
    assert first_revision.image_digest.endswith("a" * 64)


@pytest.mark.asyncio
async def test_resolver_rejects_an_unknown_driver(test_db_session) -> None:
    """A missing profile remains a safe installation failure."""
    resolver = DatabasePlatformRunnerProfileResolver(test_db_session)

    with pytest.raises(CatalogSourceConfigurationError, match="No approved platform runner profile"):
        await resolver.resolve_platform_runner("http.v1")
