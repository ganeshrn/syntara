"""Contract tests for administrator catalog-source configuration APIs."""

from unittest.mock import AsyncMock
from uuid import uuid4

from httpx import AsyncClient

from syntara.core.config.base import get_settings
from syntara.plugin_platform.exceptions import CatalogIndexArtifactVerificationError
from syntara.plugin_platform.services.catalog_reconciliation_service import CatalogReconciliationResult

BASE_URL = "/api/v1/plugin_sources"


def _payload(source_id: str | None = None) -> dict[str, object]:
    return {
        "source_id": source_id or f"local-quay-{uuid4().hex[:8]}",
        "display_name": "Local Quay",
        "registry_origin": "https://Quay.Local:8443",
        "index_repository": "syntara/catalog-index",
        "channel": "stable",
        "repository_scopes": ["syntara/plugins"],
        "credential_ref": "credentials/local-quay-pull",
        "trust_policy_ref": "trust/local-quay",
        "custom_ca_ref": "trust/local-quay-ca",
        "reconciliation_interval_seconds": 300,
    }


def _local_poc_payload(source_id: str | None = None) -> dict[str, object]:
    return {
        "source_id": source_id or f"local-poc-{uuid4().hex[:8]}",
        "display_name": "Local Quay development catalog",
        "source_mode": "local-poc",
        "registry_origin": "http://localhost:8443",
        "index_repository": "syntara/catalog-index",
        "channel": "stable",
        "repository_scopes": ["syntara/plugins"],
        "credential_ref": "development/local-poc-anonymous",
        "trust_policy_ref": "development/local-poc-unsigned",
        "reconciliation_interval_seconds": 300,
    }


class TestPluginSources:
    """Catalog-source CRUD never accepts or returns secret material."""

    async def test_create_returns_non_secret_normalized_source_configuration(self, auth_client: AsyncClient) -> None:
        """Creation accepts opaque references and returns normalized routing metadata."""
        payload = _payload()

        response = await auth_client.post(BASE_URL, json=payload)

        assert response.status_code == 201
        data = response.json()
        assert data["source_id"] == payload["source_id"]
        assert data["registry_origin"] == "https://quay.local:8443"
        assert data["credential_ref"] == "credentials/local-quay-pull"
        assert data["trust_policy_ref"] == "trust/local-quay"
        assert data["enabled"] is True
        assert data["active_reconciliation_id"] is None
        assert {"token", "access_token", "private_key", "certificate"}.isdisjoint(data)

    async def test_source_lifecycle_supports_list_update_disable_enable_and_delete(
        self, auth_client: AsyncClient
    ) -> None:
        """Administrators can manage discovery configuration without reconciliation side effects."""
        created = await auth_client.post(BASE_URL, json=_payload())
        assert created.status_code == 201
        source_id = created.json()["id"]

        listed = await auth_client.get(BASE_URL, params={"enabled": "true", "include_total": "true"})
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert [resource["id"] for resource in listed.json()["resources"]] == [source_id]

        updated = await auth_client.patch(
            f"{BASE_URL}/{source_id}",
            json={"display_name": "Local Quay (production)", "reconciliation_interval_seconds": 600},
        )
        assert updated.status_code == 200
        assert updated.json()["display_name"] == "Local Quay (production)"
        assert updated.json()["reconciliation_interval_seconds"] == 600

        disabled = await auth_client.post(f"{BASE_URL}/{source_id}/disable")
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False

        enabled = await auth_client.post(f"{BASE_URL}/{source_id}/enable")
        assert enabled.status_code == 200
        assert enabled.json()["enabled"] is True

        deleted = await auth_client.delete(f"{BASE_URL}/{source_id}")
        assert deleted.status_code == 204
        assert (await auth_client.get(f"{BASE_URL}/{source_id}")).status_code == 404

    async def test_create_rejects_duplicate_identity(self, auth_client: AsyncClient) -> None:
        """Stable source identity remains unique across administrator requests."""
        payload = _payload("local-quay")
        assert (await auth_client.post(BASE_URL, json=payload)).status_code == 201

        duplicate = await auth_client.post(BASE_URL, json=payload)
        assert duplicate.status_code == 409

    async def test_create_rejects_unsafe_credential_reference(self, auth_client: AsyncClient) -> None:
        """Credential values cannot be supplied where an opaque reference is required."""
        inline_secret = _payload()
        inline_secret["credential_ref"] = "Bearer secret-value"
        assert (await auth_client.post(BASE_URL, json=inline_secret)).status_code == 422

    async def test_update_rejects_identity_replacement_and_unsafe_origin(self, auth_client: AsyncClient) -> None:
        """A source identity is immutable and registry routing remains HTTPS-only."""
        created = await auth_client.post(BASE_URL, json=_payload())
        assert created.status_code == 201
        source_id = created.json()["id"]

        identity_replacement = await auth_client.patch(f"{BASE_URL}/{source_id}", json={"source_id": "another-source"})
        assert identity_replacement.status_code == 422

        unsafe_origin = await auth_client.patch(
            f"{BASE_URL}/{source_id}", json={"registry_origin": "http://registry.example.test"}
        )
        assert unsafe_origin.status_code == 422

    async def test_local_poc_source_requires_a_deployment_opt_in(
        self,
        auth_client: AsyncClient,
        monkeypatch,
    ) -> None:
        """A matching payload alone cannot enable local unsigned discovery."""
        monkeypatch.delenv("APP_PLUGIN_PLATFORM_LOCAL_POC_ENABLED", raising=False)
        get_settings.cache_clear()
        try:
            response = await auth_client.post(BASE_URL, json=_local_poc_payload())
        finally:
            get_settings.cache_clear()

        assert response.status_code == 400
        assert response.json()["code"] == "CATALOG_SOURCE_CONFIGURATION_ERROR"

    async def test_local_poc_source_can_be_created_only_when_the_deployment_switch_is_explicit(
        self,
        auth_client: AsyncClient,
        monkeypatch,
    ) -> None:
        """The returned source makes the unsafe profile visible to administrators."""
        monkeypatch.setenv("APP_PLUGIN_PLATFORM_LOCAL_POC_ENABLED", "true")
        get_settings.cache_clear()
        try:
            response = await auth_client.post(BASE_URL, json=_local_poc_payload())
        finally:
            get_settings.cache_clear()

        assert response.status_code == 201
        assert response.json()["source_mode"] == "local-poc"
        assert response.json()["registry_origin"] == "http://localhost:8443"

    async def test_reconciliation_endpoint_fails_closed_without_local_poc_composition(
        self,
        auth_client: AsyncClient,
    ) -> None:
        """The normal deployment has no permissive reconciliation fallback."""
        created = await auth_client.post(BASE_URL, json=_payload())
        assert created.status_code == 201

        response = await auth_client.post(f"{BASE_URL}/{created.json()['id']}/reconcile")

        assert response.status_code == 503
        assert response.json()["code"] == "CATALOG_SOURCE_RECONCILIATION_UNAVAILABLE"

    async def test_local_poc_reconciliation_endpoint_uses_only_the_explicit_local_service(
        self,
        auth_client: AsyncClient,
        monkeypatch,
    ) -> None:
        """The opt-in route exposes only a safe reconciliation outcome, not registry internals."""
        expected = CatalogReconciliationResult(
            reconciliation_id=uuid4(),
            state="accepted",
            failure_code=None,
            catalog_generation=1,
            catalog_index_digest="sha256:" + "a" * 64,
            entry_count=0,
            idempotent=False,
        )
        reconcile = AsyncMock(return_value=expected)
        monkeypatch.setenv("APP_PLUGIN_PLATFORM_LOCAL_POC_ENABLED", "true")
        monkeypatch.setattr(
            "syntara.plugin_platform.router.LocalPocCatalogReconciliationService.reconcile_source",
            reconcile,
        )
        get_settings.cache_clear()
        try:
            created = await auth_client.post(BASE_URL, json=_local_poc_payload())
            assert created.status_code == 201

            response = await auth_client.post(f"{BASE_URL}/{created.json()['id']}/reconcile")
        finally:
            get_settings.cache_clear()

        assert response.status_code == 200
        assert response.json() == {
            "reconciliation_id": str(expected.reconciliation_id),
            "state": "accepted",
            "failure_code": None,
            "catalog_generation": 1,
            "catalog_index_digest": "sha256:" + "a" * 64,
            "entry_count": 0,
            "idempotent": False,
        }
        reconcile.assert_awaited_once()

    async def test_local_poc_reconciliation_returns_a_safe_client_error_for_an_invalid_index(
        self,
        auth_client: AsyncClient,
        monkeypatch,
    ) -> None:
        """An invalid publisher artifact is never reported as an opaque server failure."""
        reconcile = AsyncMock(side_effect=CatalogIndexArtifactVerificationError("config-source-id"))
        monkeypatch.setenv("APP_PLUGIN_PLATFORM_LOCAL_POC_ENABLED", "true")
        monkeypatch.setattr(
            "syntara.plugin_platform.router.LocalPocCatalogReconciliationService.reconcile_source",
            reconcile,
        )
        get_settings.cache_clear()
        try:
            created = await auth_client.post(BASE_URL, json=_local_poc_payload())
            assert created.status_code == 201

            response = await auth_client.post(f"{BASE_URL}/{created.json()['id']}/reconcile")
        finally:
            get_settings.cache_clear()

        assert response.status_code == 422
        assert response.json()["code"] == "CATALOG_INDEX_ARTIFACT_INVALID"
        assert "config-source-id" not in response.json()["detail"]
        reconcile.assert_awaited_once()
