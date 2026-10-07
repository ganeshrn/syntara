"""Control-plane compatibility checks for the Syntara plugin contract corpus."""

from datetime import datetime

from syntara_plugin.contracts import (
    CatalogCheckpoint,
    load_bundle,
    load_fixture_bundle,
    validate_catalog_index,
    validate_document,
)


def _fixture_time(value: str) -> datetime:
    """Parse the RFC 3339 fixture timestamp into an aware datetime."""
    if value.endswith("Z"):
        value = value.removesuffix("Z") + "+00:00"
    return datetime.fromisoformat(value)


def test_syntara_accepts_the_pinned_plugin_contract_fixture_corpus() -> None:
    """Verify the control plane can consume each SDK contract fixture without network access."""
    contracts = load_bundle()
    fixtures = load_fixture_bundle()

    assert fixtures.contract_bundle_digest == contracts.digest

    for case in fixtures.document_cases:
        errors = validate_document(
            contracts,
            case["kind"],
            fixtures.document(case["path"]),
        )
        assert errors == []

    for case in fixtures.catalog_cases:
        checkpoint = case["checkpoint"]
        diagnostics = validate_catalog_index(
            contracts,
            fixtures.document(case["path"]),
            source_id=case["sourceId"],
            now=_fixture_time(case["now"]),
            checkpoint=CatalogCheckpoint(
                generation=checkpoint["generation"],
                index_digest=checkpoint["indexDigest"],
            ),
        )
        assert [diagnostic.code for diagnostic in diagnostics] == case["expectedCodes"]
