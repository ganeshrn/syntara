"""Explicit source-profile rules shared by catalog configuration and adapters.

The standard profile is the production contract: HTTPS plus a deployment-owned
credential and signature policy. ``local-poc`` is intentionally much narrower.
It exists only to let a developer reconcile a local Quay index while developing
the catalog path. It cannot select an arbitrary HTTP origin, custom CA, mirror,
or credential/trust mechanism.
"""

from __future__ import annotations

from typing import Literal

CatalogSourceMode = Literal["standard", "local-poc"]

STANDARD_CATALOG_SOURCE_MODE: CatalogSourceMode = "standard"
LOCAL_POC_CATALOG_SOURCE_MODE: CatalogSourceMode = "local-poc"
LOCAL_POC_REGISTRY_ORIGINS = frozenset({"http://localhost:8443", "http://127.0.0.1:8443"})
LOCAL_POC_ANONYMOUS_CREDENTIAL_REF = "development/local-poc-anonymous"
LOCAL_POC_UNSIGNED_TRUST_POLICY_REF = "development/local-poc-unsigned"


def validate_catalog_source_profile(
    *,
    source_mode: CatalogSourceMode,
    registry_origin: str,
    credential_ref: str,
    trust_policy_ref: str,
    custom_ca_ref: str | None,
    mirror_policy: str,
) -> None:
    """Reject any configuration that blurs local-PoC and standard trust modes."""
    if source_mode == STANDARD_CATALOG_SOURCE_MODE:
        if not registry_origin.startswith("https://"):
            msg = "Standard catalog sources must use HTTPS"
            raise ValueError(msg)
        return

    if registry_origin not in LOCAL_POC_REGISTRY_ORIGINS:
        msg = "Local-PoC catalog sources must use the configured loopback Quay origin"
        raise ValueError(msg)
    if credential_ref != LOCAL_POC_ANONYMOUS_CREDENTIAL_REF:
        msg = "Local-PoC catalog sources must use the local anonymous access reference"
        raise ValueError(msg)
    if trust_policy_ref != LOCAL_POC_UNSIGNED_TRUST_POLICY_REF:
        msg = "Local-PoC catalog sources must use the local unsigned trust reference"
        raise ValueError(msg)
    if custom_ca_ref is not None:
        msg = "Local-PoC catalog sources cannot use a custom CA"
        raise ValueError(msg)
    if mirror_policy != "direct":
        msg = "Local-PoC catalog sources must use direct registry access"
        raise ValueError(msg)


def validate_local_poc_registry_access(
    *,
    source_mode: CatalogSourceMode,
    registry_origin: str,
    credential_ref: str,
    custom_ca_ref: str | None,
) -> None:
    """Defend the local adapter from being reused for a non-local source.

    Persisted sources have already passed :func:`validate_catalog_source_profile`,
    but the registry adapter is an execution boundary.  It validates the subset
    of source properties it receives so a future call site cannot turn this
    development-only adapter into a generic anonymous HTTP client.
    """
    if source_mode != LOCAL_POC_CATALOG_SOURCE_MODE:
        msg = "The local-PoC registry adapter only accepts local-PoC sources"
        raise ValueError(msg)
    if registry_origin not in LOCAL_POC_REGISTRY_ORIGINS:
        msg = "The local-PoC registry adapter only accepts the configured loopback Quay origin"
        raise ValueError(msg)
    if credential_ref != LOCAL_POC_ANONYMOUS_CREDENTIAL_REF:
        msg = "The local-PoC registry adapter only accepts anonymous local access"
        raise ValueError(msg)
    if custom_ca_ref is not None:
        msg = "The local-PoC registry adapter cannot use a custom CA"
        raise ValueError(msg)


def validate_local_poc_catalog_trust(
    *,
    source_mode: CatalogSourceMode,
    registry_origin: str,
    trust_policy_ref: str,
    mirror_policy: str,
) -> None:
    """Defend the local unsigned catalog evaluator from broad reuse."""
    if source_mode != LOCAL_POC_CATALOG_SOURCE_MODE:
        msg = "The local-PoC trust evaluator only accepts local-PoC sources"
        raise ValueError(msg)
    if registry_origin not in LOCAL_POC_REGISTRY_ORIGINS:
        msg = "The local-PoC trust evaluator only accepts the configured loopback Quay origin"
        raise ValueError(msg)
    if trust_policy_ref != LOCAL_POC_UNSIGNED_TRUST_POLICY_REF:
        msg = "The local-PoC trust evaluator only accepts the local unsigned trust reference"
        raise ValueError(msg)
    if mirror_policy != "direct":
        msg = "The local-PoC trust evaluator only accepts direct registry access"
        raise ValueError(msg)


def validate_local_poc_plugin_artifact_trust(
    *,
    source_mode: CatalogSourceMode,
    registry_origin: str,
    trust_policy_ref: str,
    mirror_policy: str,
) -> None:
    """Defend the local unsigned documentation path from broader reuse.

    The local profile may inspect an accepted plugin artifact only to verify
    and render its documentation. Installation and workload admission have
    separate composition roots and stay unavailable in this profile.
    """
    validate_local_poc_catalog_trust(
        source_mode=source_mode,
        registry_origin=registry_origin,
        trust_policy_ref=trust_policy_ref,
        mirror_policy=mirror_policy,
    )
