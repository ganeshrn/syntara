"""Unit coverage for the narrow local-PoC catalog installation adapters."""

from __future__ import annotations

import pytest

from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError
from syntara.plugin_platform.services.catalog_installation_service import WorkloadImageAdmissionRequest
from syntara.plugin_platform.services.local_poc_catalog_reconciliation_service import (
    LocalPocCatalogRegistryAuthorizationResolver,
    LocalPocWorkloadImageAdmissionEvaluator,
)
from syntara.plugin_platform.services.registry_access import CatalogRegistryAuthorizationRequest


@pytest.mark.asyncio
async def test_local_poc_rejects_publisher_workload_image_admission() -> None:
    """Anonymous local metadata access must not become workload-image admission."""
    evaluator = LocalPocWorkloadImageAdmissionEvaluator()

    with pytest.raises(CatalogSourceConfigurationError, match="does not admit publisher workload images"):
        await evaluator.evaluate_workload_image(
            WorkloadImageAdmissionRequest(
                source_id="local-quay",
                registry_origin="http://localhost:8443",
                artifact_repository="syntara-admin/plugins/example",
                artifact_digest="sha256:" + "a" * 64,
                namespace="syntara",
                name="example",
                version="0.1.0",
                image_digest="localhost:8443/syntara/example@sha256:" + "b" * 64,
                runtime_abi="syntara.container/v1alpha1",
                trust_policy_ref="development/local-poc-unsigned",
                mirror_policy="direct",
            )
        )


@pytest.mark.asyncio
async def test_local_poc_registry_resolver_requires_the_local_poc_source_mode() -> None:
    resolver = LocalPocCatalogRegistryAuthorizationResolver()
    request = CatalogRegistryAuthorizationRequest(
        source_id="local-quay",
        registry_origin="http://localhost:8443",
        repository="syntara-admin/plugins/github",
        credential_ref="development/local-poc-anonymous",
        custom_ca_ref=None,
        source_mode="local-poc",
    )

    assert await resolver.resolve_authorization(request) is None
