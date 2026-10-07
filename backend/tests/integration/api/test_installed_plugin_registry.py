"""Contract tests for read-only active installed-plugin registry APIs."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest_asyncio
from sqlmodel import select

from syntara.plugin_platform.models.registry import PluginRegistryState
from syntara.plugin_platform.services.registry_service import (
    InstalledActionCandidate,
    PluginRegistryService,
    VerifiedPluginCandidate,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlmodel.ext.asyncio.session import AsyncSession

    from syntara.core.models import User

BASE_URL = "/api/v1/plugin_registry"


def _digest(character: str) -> str:
    return f"sha256:{character * 64}"


def _candidate(
    *,
    name: str = "github",
    version: str = "1.0.0",
    artifact_character: str = "a",
    action_name: str = "create-issue",
    action_character: str = "b",
) -> VerifiedPluginCandidate:
    """Build a test-only already-verified registry handoff."""
    return VerifiedPluginCandidate(
        namespace="syntara",
        name=name,
        version=version,
        display_name=name.replace("-", " ").title(),
        description=f"{name} workflow actions.",
        artifact_repository=f"quay.local/syntara/plugins/{name}",
        artifact_digest=_digest(artifact_character),
        canonical_descriptor={"kind": "PluginDescriptor", "internal": {"must_not": "leak"}},
        verification_summary={"trust": {"decision": "admitted"}},
        actions=(
            InstalledActionCandidate(
                target_name=action_name,
                display_name=action_name.replace("-", " ").title(),
                description=f"{action_name} action.",
                revision_digest=_digest(action_character),
                runtime_kind="custom-workload",
                image_digest=f"quay.local/syntara/workloads/{name}@{_digest('c')}",
                input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
                output_schema={"type": "object"},
                error_schema={"type": "object"},
                canonical_definition={"internal": {"must_not": "leak"}},
            ),
        ),
    )


@pytest_asyncio.fixture(autouse=True)
async def initialized_plugin_registry(test_db_session: AsyncSession) -> None:
    """Restore the singleton migration seed after the isolated test DB is cleared."""
    state = (await test_db_session.exec(select(PluginRegistryState))).one_or_none()
    if state is None:
        test_db_session.add(PluginRegistryState())
        await test_db_session.commit()


class TestInstalledPluginRegistry:
    """The UI can see one safe immutable active-generation snapshot."""

    async def test_list_and_detail_expose_only_safe_active_registry_data(
        self,
        auth_client: AsyncClient,
        test_db_session: AsyncSession,
        test_user: User,
    ) -> None:
        """A selected workflow action has the contracts it needs, not persistence internals."""
        service = PluginRegistryService(test_db_session)
        await service.install_verified(_candidate(), actor_id=test_user.id)

        listed = await auth_client.get(BASE_URL, params={"include_total": "true"})

        assert listed.status_code == 200
        payload = listed.json()
        assert payload["registry_generation"] == 1
        assert payload["registry_change_reason"] == "direct-digest-install"
        assert payload["total"] == 1
        plugin = payload["resources"][0]
        assert plugin["name"] == "github"
        assert plugin["enabled"] is True
        assert plugin["action_count"] == 1
        assert {"canonical_descriptor", "verification_summary", "credential_ref", "trust_policy_ref"}.isdisjoint(plugin)

        detail = await auth_client.get(f"{BASE_URL}/syntara/github/versions/1.0.0")

        assert detail.status_code == 200
        detail_payload = detail.json()
        assert detail_payload["registry_generation"] == 1
        assert detail_payload["actions"][0]["qualified_name"] == "syntara.github.create-issue"
        assert detail_payload["actions"][0]["input_schema"] == {
            "type": "object",
            "properties": {"title": {"type": "string"}},
        }
        assert {"canonical_definition", "credential_ref", "trust_policy_ref"}.isdisjoint(detail_payload["actions"][0])

    async def test_current_snapshot_keeps_disabled_plugins_visible_but_not_selectable(
        self,
        auth_client: AsyncClient,
        test_db_session: AsyncSession,
        test_user: User,
    ) -> None:
        """The library can explain why a revision is unavailable to execution."""
        service = PluginRegistryService(test_db_session)
        github = await service.install_verified(_candidate(), actor_id=test_user.id)
        await service.install_verified(
            _candidate(
                name="aap",
                artifact_character="d",
                action_name="launch-job",
                action_character="e",
            ),
            actor_id=test_user.id,
        )
        await service.set_enabled(github.plugin_installation_id, enabled=False, actor_id=test_user.id)

        disabled = await auth_client.get(BASE_URL, params={"enabled": "false", "include_total": "true"})

        assert disabled.status_code == 200
        payload = disabled.json()
        assert payload["registry_generation"] == 3
        assert payload["total"] == 1
        assert [(plugin["name"], plugin["enabled"]) for plugin in payload["resources"]] == [("github", False)]

        detail = await auth_client.get(f"{BASE_URL}/syntara/github/versions/1.0.0")
        assert detail.status_code == 200
        assert detail.json()["enabled"] is False

    async def test_empty_registry_returns_a_safe_empty_snapshot(self, auth_client: AsyncClient) -> None:
        """A new environment can render an empty installed-plugin library."""
        response = await auth_client.get(BASE_URL, params={"include_total": "true"})

        assert response.status_code == 200
        assert response.json() == {
            "next": None,
            "prev": None,
            "total": 0,
            "resources": [],
            "registry_generation": None,
            "registry_change_reason": None,
            "registry_published_at": None,
        }

    async def test_missing_plugin_returns_safe_not_found(self, auth_client: AsyncClient) -> None:
        """A detail lookup never discloses registry internals for an absent identity."""
        response = await auth_client.get(f"{BASE_URL}/syntara/not-present/versions/1.0.0")

        assert response.status_code == 404
        assert response.json()["code"] == "INSTALLED_PLUGIN_NOT_FOUND"
