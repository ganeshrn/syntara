"""Unit coverage for the server-owned platform-runner profile boundary."""

from __future__ import annotations

from uuid import uuid4

import pytest

from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError
from syntara.plugin_platform.services.runner_profile import (
    PlatformRunnerProfile,
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
