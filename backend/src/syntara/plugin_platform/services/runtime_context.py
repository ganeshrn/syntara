"""Build non-secret runner context from one immutable installed action.

This module is deliberately a composition boundary, not an HTTP client or an
integration resolver. It accepts a trusted action resolved from the active
registry, verifies its persisted canonical definition against the selected
route, and returns only the bounded data that may cross the container ABI.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from syntara_plugin.contracts import CanonicalJsonError, canonical_json_digest, load_bundle, validate_document

from syntara.plugin_platform.exceptions import RuntimeContextConfigurationError
from syntara.plugin_platform.services.runner_profile import CONTAINER_RUNTIME_ABI

if TYPE_CHECKING:
    from syntara.plugin_platform.services.registry_service import ResolvedInstalledAction

_HTTP_DRIVER = "http.v1"
_HTTP_RUNTIME_KEYS = frozenset({"abi", "driver", "image", "kind", "operation", "profileRevisionId"})
_RUNTIME_KIND_ERROR = "runtime-kind"
_ROUTE_ERROR = "route"
_UNSUPPORTED_DRIVER_ERROR = "unsupported-driver"
_RUNTIME_DEFINITION_ERROR = "runtime-definition"
_OPERATION_ERROR = "operation"
_OUTPUT_SCHEMA_ERROR = "output-schema"
_HTTP_CONTEXT_ERROR = "http-context"


def build_runtime_context(action: ResolvedInstalledAction) -> dict[str, Any]:
    """Return the safe runner context for one already-resolved installed action.

    Custom workloads own their dispatch behavior and receive no platform-runner
    context. Platform runners are fail-closed until this function knows their
    versioned driver contract.
    """
    if action.runtime_kind == "custom-workload":
        return {}
    if action.runtime_kind != "platform-runner":
        raise RuntimeContextConfigurationError(_RUNTIME_KIND_ERROR)
    if action.runtime_abi != CONTAINER_RUNTIME_ABI or action.runner_profile_revision_id is None:
        raise RuntimeContextConfigurationError(_ROUTE_ERROR)

    runtime = _runtime_definition(action)
    if runtime.get("driver") != _HTTP_DRIVER:
        raise RuntimeContextConfigurationError(_UNSUPPORTED_DRIVER_ERROR)
    if set(runtime) != _HTTP_RUNTIME_KEYS:
        raise RuntimeContextConfigurationError(_RUNTIME_DEFINITION_ERROR)
    if (
        runtime.get("kind") != "platform-runner"
        or runtime.get("abi") != action.runtime_abi
        or runtime.get("image") != action.image_digest
        or runtime.get("profileRevisionId") != str(action.runner_profile_revision_id)
    ):
        raise RuntimeContextConfigurationError(_ROUTE_ERROR)

    operation = runtime.get("operation")
    if not isinstance(operation, Mapping):
        raise RuntimeContextConfigurationError(_OPERATION_ERROR)
    if action.canonical_definition.get("output") != action.output_schema:
        raise RuntimeContextConfigurationError(_OUTPUT_SCHEMA_ERROR)

    context: dict[str, Any] = {
        "driver": _HTTP_DRIVER,
        "operation": deepcopy(dict(operation)),
        "outputSchemaDigest": _canonical_json_digest(action.output_schema),
    }
    if validate_document(load_bundle(), "http_runner_context", context):
        raise RuntimeContextConfigurationError(_HTTP_CONTEXT_ERROR)
    return context


def _runtime_definition(action: ResolvedInstalledAction) -> Mapping[str, Any]:
    runtime = action.canonical_definition.get("runtime")
    if not isinstance(runtime, Mapping) or any(not isinstance(key, str) for key in runtime):
        raise RuntimeContextConfigurationError(_RUNTIME_DEFINITION_ERROR)
    return runtime


def _canonical_json_digest(value: Mapping[str, Any]) -> str:
    """Digest one persisted JSON document using the contract's stable form."""
    try:
        return canonical_json_digest(value)
    except CanonicalJsonError as error:
        raise RuntimeContextConfigurationError(_OUTPUT_SCHEMA_ERROR) from error
