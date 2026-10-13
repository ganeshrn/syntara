"""Contract tests for browsing accepted local plugin catalog projections."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlmodel import col, select

from syntara.core.config.base import get_settings
from syntara.plugin_platform.models.catalog import (
    CatalogPluginAsset,
    CatalogPluginVersion,
    PluginSource,
    PluginSourceReconciliation,
)
from syntara.plugin_platform.services.catalog_documentation_hydration_service import (
    CatalogDocumentationHydrationResult,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlmodel.ext.asyncio.session import AsyncSession

BASE_URL = "/api/v1/catalog/plugins"
_OBSERVED_AT = datetime(2026, 10, 7, 12, tzinfo=UTC)


def _digest(character: str) -> str:
    return f"sha256:{character * 64}"


def _source(source_id: str, *, enabled: bool = True) -> PluginSource:
    return PluginSource.model_validate(
        {
            "source_id": source_id,
            "display_name": source_id.replace("-", " ").title(),
            "registry_origin": "https://quay.local:8443",
            "index_repository": f"{source_id}/catalog-index",
            "channel": "stable",
            "repository_scopes": [f"{source_id}/plugins"],
            "credential_ref": f"credentials/{source_id}",
            "trust_policy_ref": f"trust/{source_id}",
            "enabled": enabled,
        }
    )


async def _seed_active_catalog(
    session: AsyncSession,
    *,
    source_id: str = "local-quay",
    enabled: bool = True,
    entries: list[dict[str, Any]] | None = None,
) -> None:
    source = _source(source_id, enabled=enabled)
    session.add(source)
    await session.flush()

    reconciliation = PluginSourceReconciliation(
        plugin_source_id=source.id,
        state="accepted",
        catalog_generation=1,
        catalog_index_digest=_digest("c"),
        previous_index_digest=None,
        issued_at=_OBSERVED_AT - timedelta(hours=1),
        expires_at=_OBSERVED_AT + timedelta(days=7),
        entry_count=len(entries or []),
        verification_summary={"trust": {"decision": "admitted"}},
        initiated_by=None,
        initiation_kind="manual",
    )
    session.add(reconciliation)
    await session.flush()

    source.active_reconciliation_id = reconciliation.id
    plugin_entries = entries or [
        {
            "namespace": "syntara",
            "name": "github",
            "version": "1.0.0",
            "artifact_repository": f"{source_id}/plugins/github",
            "artifact_digest": _digest("a"),
            "documentation_digest": _digest("b"),
            "runtime_ownerships": ["platform-runner"],
            "deprecated": False,
            "withdrawn": False,
        }
    ]
    session.add_all(
        [
            CatalogPluginVersion(
                reconciliation_id=reconciliation.id,
                canonical_entry={"publisherMarkup": "<script>unsafe</script>"},
                **entry,
            )
            for entry in plugin_entries
        ]
    )
    await session.commit()


async def _seed_documentation(session: AsyncSession, *, source_id: str = "local-quay") -> None:
    """Persist the safe local output of a previously successful hydration job."""
    version = (
        await session.exec(
            select(CatalogPluginVersion)
            .join(
                PluginSourceReconciliation,
                col(CatalogPluginVersion.reconciliation_id) == col(PluginSourceReconciliation.id),
            )
            .join(PluginSource, col(PluginSource.active_reconciliation_id) == col(PluginSourceReconciliation.id))
            .where(col(PluginSource.source_id) == source_id)
        )
    ).one()
    session.add(
        CatalogPluginAsset.model_validate(
            {
                "catalog_plugin_version_id": version.id,
                "asset_kind": "documentation",
                "media_type": "text/markdown",
                "digest": version.documentation_digest,
                "size_bytes": 128,
                "rendered_html": "<h1>GitHub</h1><p>Safe local documentation.</p>",
                "verification_summary": {"trust": {"signature": "verified"}},
            }
        )
    )
    await session.commit()


class TestCatalogBrowse:
    """Catalog reads only return the enabled source's active immutable snapshot."""

    async def test_list_filter_detail_and_version_return_safe_active_projection(
        self,
        auth_client: AsyncClient,
        test_db_session: AsyncSession,
    ) -> None:
        """The UI can browse safe digest-pinned entries without publisher markup or secrets."""
        await _seed_active_catalog(
            test_db_session,
            entries=[
                {
                    "namespace": "syntara",
                    "name": "aap",
                    "version": "1.0.0",
                    "artifact_repository": "local-quay/plugins/aap",
                    "artifact_digest": _digest("d"),
                    "documentation_digest": _digest("e"),
                    "runtime_ownerships": ["platform-runner"],
                    "deprecated": False,
                    "withdrawn": False,
                },
                {
                    "namespace": "syntara",
                    "name": "github",
                    "version": "1.0.0",
                    "artifact_repository": "local-quay/plugins/github",
                    "artifact_digest": _digest("a"),
                    "documentation_digest": _digest("b"),
                    "runtime_ownerships": ["custom-workload"],
                    "deprecated": False,
                    "withdrawn": False,
                },
            ],
        )

        listed = await auth_client.get(BASE_URL, params={"limit": 1, "sort": "name", "include_total": "true"})
        assert listed.status_code == 200
        listed_data = listed.json()
        assert listed_data["total"] == 2
        assert [entry["name"] for entry in listed_data["resources"]] == ["aap"]
        assert listed_data["next"] is not None

        filtered = await auth_client.get(BASE_URL, params={"name[contains]": "git"})
        assert filtered.status_code == 200
        filtered_entry = filtered.json()["resources"][0]
        assert filtered_entry["name"] == "github"
        assert filtered_entry["source_id"] == "local-quay"
        assert filtered_entry["artifact_digest"] == _digest("a")
        assert "canonical_entry" not in filtered_entry
        assert "publisherMarkup" not in filtered_entry
        assert {"token", "credential_ref", "trust_policy_ref"}.isdisjoint(filtered_entry)

        detail = await auth_client.get(f"{BASE_URL}/syntara/github")
        assert detail.status_code == 200
        assert detail.json()["namespace"] == "syntara"
        assert [entry["version"] for entry in detail.json()["versions"]] == ["1.0.0"]

        version = await auth_client.get(f"{BASE_URL}/syntara/github/versions/1.0.0")
        assert version.status_code == 200
        assert version.json()["documentation_digest"] == _digest("b")

    async def test_disabled_source_is_not_visible_in_catalog(
        self,
        auth_client: AsyncClient,
        test_db_session: AsyncSession,
    ) -> None:
        """Disabling a source removes its discovery snapshot without touching its evidence."""
        await _seed_active_catalog(test_db_session, enabled=False)

        response = await auth_client.get(BASE_URL, params={"include_total": "true"})
        assert response.status_code == 200
        assert response.json() == {"resources": [], "next": None, "prev": None, "total": 0}

    async def test_version_requires_source_selection_when_sources_conflict(
        self,
        auth_client: AsyncClient,
        test_db_session: AsyncSession,
    ) -> None:
        """Duplicate active identity stays inspectable without silently choosing a publisher."""
        await _seed_active_catalog(test_db_session, source_id="local-quay")
        await _seed_active_catalog(test_db_session, source_id="team-quay")

        ambiguous = await auth_client.get(f"{BASE_URL}/syntara/github/versions/1.0.0")
        assert ambiguous.status_code == 409
        assert ambiguous.json()["code"] == "CATALOG_PLUGIN_IDENTITY_AMBIGUOUS"

        selected = await auth_client.get(
            f"{BASE_URL}/syntara/github/versions/1.0.0",
            params={"source_id": "team-quay"},
        )
        assert selected.status_code == 200
        assert selected.json()["source_id"] == "team-quay"

    async def test_missing_catalog_plugin_returns_safe_not_found(self, auth_client: AsyncClient) -> None:
        """Catalog lookup does not disclose source state for a missing identity."""
        response = await auth_client.get(f"{BASE_URL}/syntara/not-present")
        assert response.status_code == 404
        assert response.json()["code"] == "CATALOG_PLUGIN_NOT_FOUND"

    async def test_documentation_returns_only_admitted_rendered_html(
        self,
        auth_client: AsyncClient,
        test_db_session: AsyncSession,
    ) -> None:
        """The browser gets no publisher Markdown, evidence, registry response, or secret reference."""
        await _seed_active_catalog(test_db_session)
        await _seed_documentation(test_db_session)

        response = await auth_client.get(f"{BASE_URL}/syntara/github/versions/1.0.0/documentation")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source_id"] == "local-quay"
        assert payload["namespace"] == "syntara"
        assert payload["name"] == "github"
        assert payload["version"] == "1.0.0"
        assert payload["documentation_digest"] == _digest("b")
        assert payload["rendered_html"] == "<h1>GitHub</h1><p>Safe local documentation.</p>"
        assert payload["rendered_at"]
        assert {"verification_summary", "credential_ref", "publisher_markdown"}.isdisjoint(payload)

    async def test_documentation_requires_source_selection_and_never_hydrates_on_read(
        self,
        auth_client: AsyncClient,
        test_db_session: AsyncSession,
    ) -> None:
        """Conflicting sources stay explicit, while absent persisted documentation remains a safe 404."""
        await _seed_active_catalog(test_db_session, source_id="local-quay")
        await _seed_active_catalog(test_db_session, source_id="team-quay")
        await _seed_documentation(test_db_session, source_id="team-quay")

        ambiguous = await auth_client.get(f"{BASE_URL}/syntara/github/versions/1.0.0/documentation")
        assert ambiguous.status_code == 409
        assert ambiguous.json()["code"] == "CATALOG_PLUGIN_IDENTITY_AMBIGUOUS"

        absent = await auth_client.get(
            f"{BASE_URL}/syntara/github/versions/1.0.0/documentation",
            params={"source_id": "local-quay"},
        )
        assert absent.status_code == 404
        assert absent.json()["code"] == "CATALOG_PLUGIN_NOT_FOUND"

        selected = await auth_client.get(
            f"{BASE_URL}/syntara/github/versions/1.0.0/documentation",
            params={"source_id": "team-quay"},
        )
        assert selected.status_code == 200
        assert selected.json()["source_id"] == "team-quay"

    async def test_documentation_hydration_fails_closed_without_local_poc_composition(
        self,
        auth_client: AsyncClient,
        test_db_session: AsyncSession,
    ) -> None:
        """A normal deployment does not substitute an unsigned documentation reader."""
        await _seed_active_catalog(test_db_session)

        response = await auth_client.post(
            f"{BASE_URL}/syntara/github/versions/1.0.0/documentation/hydrate",
            params={"source_id": "local-quay"},
        )

        assert response.status_code == 503
        assert response.json()["code"] == "CATALOG_DOCUMENTATION_HYDRATION_UNAVAILABLE"

    async def test_local_poc_hydrates_only_the_source_selected_catalog_version(
        self,
        auth_client: AsyncClient,
        test_db_session: AsyncSession,
        monkeypatch,
    ) -> None:
        """The local development route accepts no registry URL, token, or digest from the browser."""
        await _seed_active_catalog(test_db_session)
        selected_version = (await test_db_session.exec(select(CatalogPluginVersion))).one()
        hydrated = CatalogDocumentationHydrationResult(
            catalog_plugin_version_id=uuid4(),
            documentation_digest=_digest("b"),
            idempotent=False,
        )
        hydrate = AsyncMock(return_value=hydrated)
        monkeypatch.setenv("APP_PLUGIN_PLATFORM_LOCAL_POC_ENABLED", "true")
        monkeypatch.setattr(
            "syntara.plugin_platform.catalog_router.LocalPocCatalogDocumentationHydrationService.hydrate",
            hydrate,
        )
        get_settings.cache_clear()
        try:
            response = await auth_client.post(
                f"{BASE_URL}/syntara/github/versions/1.0.0/documentation/hydrate",
                params={"source_id": "local-quay"},
            )
        finally:
            get_settings.cache_clear()

        assert response.status_code == 200
        assert response.json() == {
            "catalog_plugin_version_id": str(hydrated.catalog_plugin_version_id),
            "documentation_digest": _digest("b"),
            "idempotent": False,
        }
        hydrate.assert_awaited_once()
        assert hydrate.await_args.args == (selected_version.id,)
