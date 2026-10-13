"""Unit coverage for the server-owned platform-runner profile boundary."""

from __future__ import annotations

from uuid import uuid4

import pytest

from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError
from syntara.plugin_platform.services.runner_profile import (
    PlatformRunnerProfile,
    RunnerProfileRevisionCandidate,
    StaticPlatformRunnerProfileResolver,
)


@pytest.mark.asyncio
async def test_static_resolver_returns_only_the_profile_for_the_exact_driver() -> None:
    profile = PlatformRunnerProfile(
        revision_id=uuid4(),
        driver="http.v1",
        image_digest="quay.io/syntara/http-runner@sha256:" + "a" * 64,
    )
    resolver = StaticPlatformRunnerProfileResolver({"http.v1": profile})

    assert await resolver.resolve_platform_runner("http.v1") is profile


@pytest.mark.asyncio
async def test_static_resolver_rejects_an_unconfigured_driver() -> None:
    resolver = StaticPlatformRunnerProfileResolver({})

    with pytest.raises(CatalogSourceConfigurationError, match="No approved platform runner profile"):
        await resolver.resolve_platform_runner("http.v1")


def test_runner_profile_candidate_accepts_a_digest_from_a_registry_with_a_port() -> None:
    candidate = RunnerProfileRevisionCandidate(
        driver="http.v1",
        image_digest="localhost:8443/syntara-admin/runners/http-v1@sha256:" + "a" * 64,
    )

    assert candidate.image_digest.startswith("localhost:8443/")
