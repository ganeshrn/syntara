"""Unit coverage for the deliberately narrow local catalog reconciliation adapters."""

from __future__ import annotations

import pytest

from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError
from syntara.plugin_platform.services.catalog_reconciliation_orchestrator import CatalogIndexTrustRequest
from syntara.plugin_platform.services.local_poc_catalog_reconciliation_service import (
    LocalPocCatalogIndexTrustEvaluator,
    LocalPocCatalogRegistryAuthorizationResolver,
    LocalPocPluginArtifactTrustEvaluator,
)
from syntara.plugin_platform.services.plugin_artifact_trust import PluginArtifactTrustRequest
from syntara.plugin_platform.services.registry_access import CatalogRegistryAuthorizationRequest

_DIGEST = "sha256:" + "a" * 64


def _access_request(**overrides: object) -> CatalogRegistryAuthorizationRequest:
    values: dict[str, object] = {
        "source_id": "local-quay",
        "registry_origin": "http://localhost:8443",
        "repository": "syntara/catalog-index",
        "credential_ref": "development/local-poc-anonymous",
        "custom_ca_ref": None,
        "source_mode": "local-poc",
    }
    values.update(overrides)
    return CatalogRegistryAuthorizationRequest(**values)  # type: ignore[arg-type]


def _trust_request(**overrides: object) -> CatalogIndexTrustRequest:
    values: dict[str, object] = {
        "source_id": "local-quay",
        "registry_origin": "http://localhost:8443",
        "index_repository": "syntara/catalog-index",
        "channel": "stable",
        "index_digest": _DIGEST,
        "trust_policy_ref": "development/local-poc-unsigned",
        "mirror_policy": "direct",
        "source_mode": "local-poc",
    }
    values.update(overrides)
    return CatalogIndexTrustRequest(**values)  # type: ignore[arg-type]


def _plugin_artifact_request(**overrides: object) -> PluginArtifactTrustRequest:
    values: dict[str, object] = {
        "source_id": "local-quay",
        "registry_origin": "http://localhost:8443",
        "artifact_repository": "syntara/plugins/github",
        "artifact_digest": _DIGEST,
        "namespace": "syntara",
        "name": "github",
        "version": "0.1.0",
        "trust_policy_ref": "development/local-poc-unsigned",
        "mirror_policy": "direct",
        "source_mode": "local-poc",
    }
    values.update(overrides)
    return PluginArtifactTrustRequest(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_local_poc_registry_adapter_allows_only_anonymous_loopback_access() -> None:
    resolver = LocalPocCatalogRegistryAuthorizationResolver()

    authorization = await resolver.resolve_authorization(_access_request())

    assert authorization is None

    with pytest.raises(CatalogSourceConfigurationError):
        await resolver.resolve_authorization(_access_request(registry_origin="http://registry.example.test"))
    with pytest.raises(CatalogSourceConfigurationError):
        await resolver.resolve_authorization(_access_request(source_mode="standard"))


@pytest.mark.asyncio
async def test_local_poc_trust_adapter_records_the_unsigned_exception_only_for_its_profile() -> None:
    evaluator = LocalPocCatalogIndexTrustEvaluator()

    decision = await evaluator.evaluate_catalog_index(_trust_request())

    assert decision.summary == {
        "profile": "local-poc",
        "registryTransport": "loopback-http",
        "signatureVerification": "skipped",
        "unsafeDevelopmentMode": True,
    }
    with pytest.raises(CatalogSourceConfigurationError):
        await evaluator.evaluate_catalog_index(_trust_request(trust_policy_ref="trust/example"))
    with pytest.raises(CatalogSourceConfigurationError):
        await evaluator.evaluate_catalog_index(_trust_request(mirror_policy="digest-preserving"))


@pytest.mark.asyncio
async def test_local_poc_plugin_artifact_trust_is_documentation_only_and_profile_bound() -> None:
    evaluator = LocalPocPluginArtifactTrustEvaluator()

    decision = await evaluator.evaluate_plugin_artifact(_plugin_artifact_request())

    assert decision.summary == {
        "profile": "local-poc",
        "operation": "documentation-hydration",
        "registryTransport": "loopback-http",
        "signatureVerification": "skipped",
        "unsafeDevelopmentMode": True,
    }
    with pytest.raises(CatalogSourceConfigurationError):
        await evaluator.evaluate_plugin_artifact(_plugin_artifact_request(source_mode="standard"))
