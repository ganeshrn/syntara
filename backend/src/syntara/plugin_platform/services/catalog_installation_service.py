"""Install one catalog-selected plugin through the immutable registry boundary.

This service owns the catalog-to-install handoff, but deliberately does not
expose an HTTP endpoint.  A caller can select only an active accepted catalog
version; the service then pulls that exact digest, checks source and workload
admission before persisted installation, and rechecks the source snapshot
while holding its lock.  The installed-plugin registry remains the single
writer of active action routes.

Platform-runner actions resolve only through an injected, server-owned
runner-profile resolver. Their artifacts name a versioned driver, never an
image. When no resolver can supply an admitted immutable route, installation
fails closed without changing the digest, source, or registry transaction.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Literal, NoReturn, Protocol

from pydantic import ValidationError
from sqlmodel import col, select

from syntara.plugin_platform.exceptions import (
    CatalogPluginNotFoundError,
    CatalogSourceConfigurationError,
    CatalogSourceDisabledError,
    CatalogSourceNotFoundError,
)
from syntara.plugin_platform.models.catalog import (
    CatalogPluginVersion,
    PluginSource,
    PluginSourceReconciliation,
)
from syntara.plugin_platform.services.artifact_verifier import (
    PluginArtifactInput,
    PluginArtifactVerifier,
    VerifiedPluginArtifact,
)
from syntara.plugin_platform.services.catalog_reconciliation_service import catalog_source_fingerprint
from syntara.plugin_platform.services.oci_distribution_client import (
    OciDistributionClient,
    PluginArtifactLocator,
)
from syntara.plugin_platform.services.plugin_artifact_trust import (
    PluginArtifactTrustEvaluator,
    PluginArtifactTrustRequest,
)
from syntara.plugin_platform.services.registry_access import (
    CatalogRegistryAuthorizationRequest,
    CatalogRegistryAuthorizationResolver,
    CatalogRegistryTLSContextResolver,
    SystemTrustCatalogRegistryTLSContextResolver,
    safe_trust_summary,
    validate_registry_authorization,
)
from syntara.plugin_platform.services.registry_service import (
    InstalledActionCandidate,
    InstalledCredentialTypeCandidate,
    InstalledIntegrationTypeCandidate,
    InstallResult,
    PluginRegistryService,
    VerifiedPluginCandidate,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlmodel.ext.asyncio.session import AsyncSession

    from syntara.plugin_platform.services.runner_profile import (
        PlatformRunnerProfile,
        PlatformRunnerProfileResolver,
    )


_CONTAINER_ABI: Literal["syntara.container/v1alpha1"] = "syntara.container/v1alpha1"
_ACTION_TARGET_KEYS = {
    "error",
    "identity",
    "input",
    "kind",
    "metadata",
    "output",
    "runtime",
    "sourcePath",
    "version",
}
_ACTION_TARGET_KEYS_WITH_INTEGRATION = _ACTION_TARGET_KEYS | {"integrationType"}
_ACTION_PRESENTATION_KEY = {"inputPresentation"}
_ACTION_SCHEMA_DIGESTS_KEY = {"schemaDigests"}
_INTEGRATION_TARGET_KEYS = {
    "configuration",
    "credentialTypes",
    "endpointKinds",
    "id",
    "identity",
    "kind",
    "metadata",
    "sourcePath",
    "version",
}
_CREDENTIAL_TARGET_KEYS = {
    "credentialSchema",
    "id",
    "identity",
    "integrationType",
    "issuance",
    "kind",
    "metadata",
    "secretFields",
    "sourcePath",
    "version",
}
_TARGET_METADATA_KEYS = {"description", "displayName", "name"}
_CUSTOM_RUNTIME_KEYS = {"abi", "kind", "workload"}
_CUSTOM_RUNNER_RUNTIME_KEYS = _CUSTOM_RUNTIME_KEYS | {"entryPoint"}
_PLATFORM_RUNTIME_KEYS = {"driver", "kind", "operation"}
_WORKLOAD_BINDING_KEYS = {"abi", "image"}
_HTTP_OPERATION_KEYS = {"method", "path", "requestMap", "responseMap"}
_HTTP_METHODS = frozenset({"DELETE", "GET", "PATCH", "POST", "PUT"})
_HTTP_PATH_STATIC_CHARACTERS = re.compile(r"^[A-Za-z0-9._~!$&'()*+,;=:@{}\-/]+$")
_HTTP_PATH_INPUT = re.compile(r"\{input\.[A-Za-z][A-Za-z0-9_-]{0,127}\}")
_HTTP_MAPPING_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
_JSON_POINTER = re.compile(r"^(?:/(?:[^~/]|~[01])*)+$")
_MAX_HTTP_PATH_LENGTH = 1024
_MAX_HTTP_MAPPING_ENTRIES = 128
_MAX_JSON_POINTER_LENGTH = 1024


@dataclass(frozen=True)
class WorkloadImageAdmissionRequest:
    """An immutable publisher-workload image submitted to environment policy."""

    source_id: str
    registry_origin: str
    artifact_repository: str
    artifact_digest: str
    namespace: str
    name: str
    version: str
    image_digest: str
    runtime_abi: str
    trust_policy_ref: str
    mirror_policy: Literal["direct", "digest-preserving"]


@dataclass(frozen=True)
class WorkloadImageAdmissionDecision:
    """Safe evidence that one exact publisher workload is admitted."""

    summary: Mapping[str, str | int | bool | None]


class WorkloadImageAdmissionEvaluator(Protocol):
    """Admit a publisher workload image before it becomes selectable."""

    async def evaluate_workload_image(self, request: WorkloadImageAdmissionRequest) -> WorkloadImageAdmissionDecision:
        """Return only after the exact image digest satisfies local policy."""


@dataclass(frozen=True)
class CatalogPluginInstallResult:
    """A catalog-selected immutable plugin installation result."""

    catalog_plugin_version_id: UUID
    plugin_installation_id: UUID
    registry_generation: int
    idempotent: bool


class CatalogPluginInstallationService:
    """Resolve, admit, and install a plugin selected from an active catalog."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        authorization_resolver: CatalogRegistryAuthorizationResolver,
        artifact_trust_evaluator: PluginArtifactTrustEvaluator,
        workload_image_evaluator: WorkloadImageAdmissionEvaluator,
        registry_client: OciDistributionClient,
        artifact_verifier: PluginArtifactVerifier | None = None,
        tls_context_resolver: CatalogRegistryTLSContextResolver | None = None,
        runner_profile_resolver: PlatformRunnerProfileResolver | None = None,
    ) -> None:
        """Bind explicit backend-owned access, trust, image, and persistence ports."""
        self._session = session
        self._authorization_resolver = authorization_resolver
        self._artifact_trust_evaluator = artifact_trust_evaluator
        self._workload_image_evaluator = workload_image_evaluator
        self._registry_client = registry_client
        self._artifact_verifier = artifact_verifier or PluginArtifactVerifier()
        self._tls_context_resolver = tls_context_resolver or SystemTrustCatalogRegistryTLSContextResolver()
        self._runner_profile_resolver = runner_profile_resolver

    async def install(
        self,
        catalog_plugin_version_id: UUID,
        *,
        actor_id: UUID | None,
    ) -> CatalogPluginInstallResult:
        """Install one selected catalog revision through the common registry transaction."""
        version, source = await self._active_version_and_source(catalog_plugin_version_id)
        source_fingerprint = catalog_source_fingerprint(source)
        _require_repository_scope(version.artifact_repository, source.repository_scopes)
        locator = PluginArtifactLocator(
            registry_origin=source.registry_origin,
            artifact_repository=version.artifact_repository,
        )
        access_request = CatalogRegistryAuthorizationRequest(
            source_id=source.source_id,
            registry_origin=source.registry_origin,
            repository=version.artifact_repository,
            credential_ref=source.credential_ref,
            custom_ca_ref=source.custom_ca_ref,
            source_mode=source.source_mode,
        )
        authorization = await self._authorization_resolver.resolve_authorization(access_request)
        tls_context = await self._tls_context_resolver.resolve_tls_context(access_request)
        validate_registry_authorization(authorization)

        try:
            resolved = await self._registry_client.fetch_plugin_artifact_manifest(
                locator,
                digest=version.artifact_digest,
                authorization_header=authorization,
                tls_context=tls_context,
            )
            manifest_plan = self._artifact_verifier.inspect_manifest(
                PluginArtifactInput(
                    artifact_repository=version.artifact_repository,
                    artifact_digest=version.artifact_digest,
                    manifest_bytes=resolved.manifest_bytes,
                    blobs={},
                )
            )
            artifact_trust = await self._artifact_trust_evaluator.evaluate_plugin_artifact(
                PluginArtifactTrustRequest(
                    source_id=source.source_id,
                    registry_origin=source.registry_origin,
                    artifact_repository=version.artifact_repository,
                    artifact_digest=version.artifact_digest,
                    namespace=version.namespace,
                    name=version.name,
                    version=version.version,
                    trust_policy_ref=source.trust_policy_ref,
                    mirror_policy=source.mirror_policy,
                    source_mode=source.source_mode,
                )
            )
            artifact_trust_summary = safe_trust_summary(artifact_trust.summary)
            blobs = {
                manifest_plan.config_descriptor.digest: await self._registry_client.fetch_blob(
                    locator,
                    digest=manifest_plan.config_descriptor.digest,
                    maximum_bytes=manifest_plan.config_descriptor.size,
                    authorization_header=authorization,
                    tls_context=tls_context,
                ),
                manifest_plan.plugin_manifest_descriptor.digest: await self._registry_client.fetch_blob(
                    locator,
                    digest=manifest_plan.plugin_manifest_descriptor.digest,
                    maximum_bytes=manifest_plan.plugin_manifest_descriptor.size,
                    authorization_header=authorization,
                    tls_context=tls_context,
                ),
                manifest_plan.content_bundle_descriptor.digest: await self._registry_client.fetch_blob(
                    locator,
                    digest=manifest_plan.content_bundle_descriptor.digest,
                    maximum_bytes=manifest_plan.content_bundle_descriptor.size,
                    authorization_header=authorization,
                    tls_context=tls_context,
                ),
            }
            artifact = self._artifact_verifier.verify(
                PluginArtifactInput(
                    artifact_repository=version.artifact_repository,
                    artifact_digest=version.artifact_digest,
                    manifest_bytes=resolved.manifest_bytes,
                    blobs=blobs,
                )
            )
            _require_artifact_identity(artifact.canonical_descriptor, version)
            runner_profiles = await self._resolve_runner_profiles(artifact.canonical_descriptor)
            candidate = build_catalog_install_candidate(
                artifact,
                version,
                artifact_trust_summary,
                runner_profiles=runner_profiles,
            )
            workload_summary = await self._admit_workload_images(candidate, version, source)
            candidate = candidate.model_copy(
                update={
                    "verification_summary": {
                        "artifact": {
                            "digest": artifact.artifact_digest,
                            "trust": artifact_trust_summary,
                        },
                        "workloadImages": workload_summary,
                    }
                }
            )
            await self._require_source_unchanged(
                version,
                source_database_id=source.id,
                source_fingerprint=source_fingerprint,
            )
            installed = await PluginRegistryService(self._session).install_verified(
                candidate,
                actor_id=actor_id,
                change_reason="catalog-install",
            )
        except Exception:
            await self._session.rollback()
            raise
        return _result_from_install(version.id, installed)

    async def _resolve_runner_profiles(
        self,
        descriptor: Mapping[str, Any],
    ) -> dict[str, PlatformRunnerProfile]:
        """Resolve only drivers named by verified platform-runner action targets."""
        drivers = _platform_runner_drivers(descriptor)
        if not drivers:
            return {}
        if self._runner_profile_resolver is None:
            msg = "No approved platform runner profile resolver is configured"
            raise CatalogSourceConfigurationError(msg)
        return {
            driver: await self._runner_profile_resolver.resolve_platform_runner(driver) for driver in sorted(drivers)
        }

    async def _admit_workload_images(
        self,
        candidate: VerifiedPluginCandidate,
        version: CatalogPluginVersion,
        source: PluginSource,
    ) -> list[dict[str, Any]]:
        """Evaluate each unique exact publisher image before registry persistence."""
        summaries: list[dict[str, Any]] = []
        for image_digest in sorted(
            {action.image_digest for action in candidate.actions if action.runtime_kind == "custom-workload"}
        ):
            decision = await self._workload_image_evaluator.evaluate_workload_image(
                WorkloadImageAdmissionRequest(
                    source_id=source.source_id,
                    registry_origin=source.registry_origin,
                    artifact_repository=version.artifact_repository,
                    artifact_digest=version.artifact_digest,
                    namespace=version.namespace,
                    name=version.name,
                    version=version.version,
                    image_digest=image_digest,
                    runtime_abi=_CONTAINER_ABI,
                    trust_policy_ref=source.trust_policy_ref,
                    mirror_policy=source.mirror_policy,
                )
            )
            summaries.append({"imageDigest": image_digest, "admission": safe_trust_summary(decision.summary)})
        return summaries

    async def _active_version_and_source(
        self, catalog_plugin_version_id: UUID
    ) -> tuple[CatalogPluginVersion, PluginSource]:
        statement = (
            select(CatalogPluginVersion, PluginSource)
            .join(
                PluginSourceReconciliation,
                col(CatalogPluginVersion.reconciliation_id) == col(PluginSourceReconciliation.id),
            )
            .join(
                PluginSource,
                col(PluginSource.active_reconciliation_id) == col(PluginSourceReconciliation.id),
            )
            .where(
                col(CatalogPluginVersion.id) == catalog_plugin_version_id,
                col(PluginSource.enabled).is_(True),
                col(PluginSourceReconciliation.state) == "accepted",
            )
        )
        row = (await self._session.exec(statement)).one_or_none()
        if row is None:
            raise CatalogPluginNotFoundError
        version, source = row
        return version, source

    async def _require_source_unchanged(
        self,
        version: CatalogPluginVersion,
        *,
        source_database_id: UUID,
        source_fingerprint: str,
    ) -> None:
        source_result = await self._session.exec(
            select(PluginSource)
            .where(col(PluginSource.id) == source_database_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        source = source_result.one_or_none()
        if source is None:
            raise CatalogSourceNotFoundError
        if not source.enabled:
            raise CatalogSourceDisabledError
        if (
            source.active_reconciliation_id != version.reconciliation_id
            or source_fingerprint != catalog_source_fingerprint(source)
        ):
            msg = "Catalog source changed during plugin installation"
            raise CatalogSourceConfigurationError(msg)


def build_catalog_install_candidate(
    artifact: VerifiedPluginArtifact,
    version: CatalogPluginVersion,
    artifact_trust_summary: Mapping[str, str | int | bool | None],
    *,
    runner_profiles: Mapping[str, PlatformRunnerProfile] | None = None,
) -> VerifiedPluginCandidate:
    """Convert one verified descriptor into an install candidate.

    This is intentionally strict: an unimplemented trigger or an action whose
    runner driver has no admitted server-owned profile rejects the whole plugin
    rather than silently dropping a capability or inventing a runnable image
    route.
    """
    descriptor = artifact.canonical_descriptor
    metadata = _mapping(descriptor.get("metadata"), "plugin metadata")
    _require_keys(
        metadata,
        {"description", "displayName", "name", "namespace", "version"},
        "plugin metadata",
    )
    _require_artifact_identity(descriptor, version)
    specification = _mapping(descriptor.get("spec"), "plugin specification")
    workloads = _mapping(specification.get("workloads"), "plugin workloads")
    profiles = runner_profiles or {}
    action_targets, integration_targets, credential_targets = _partition_plugin_targets(specification.get("targets"))
    action_bindings = [_action_from_target(target, version, workloads, profiles) for target in action_targets]
    actions = [binding.action for binding in action_bindings]
    integration_types = [_integration_type_from_target(target, version) for target in integration_targets]
    credential_types = [_credential_type_from_target(target, version) for target in credential_targets]
    _validate_runtime_ownership(version, action_bindings, workloads)
    try:
        return VerifiedPluginCandidate(
            namespace=_string(metadata.get("namespace"), "plugin namespace"),
            name=_string(metadata.get("name"), "plugin name"),
            version=_string(metadata.get("version"), "plugin version"),
            display_name=_string(metadata.get("displayName"), "plugin display name"),
            description=_string(metadata.get("description"), "plugin description"),
            artifact_repository=artifact.artifact_repository,
            artifact_digest=artifact.artifact_digest,
            canonical_descriptor=dict(descriptor),
            verification_summary={"artifact": {"trust": dict(artifact_trust_summary)}},
            actions=tuple(actions),
            integration_types=tuple(integration_types),
            credential_types=tuple(credential_types),
        )
    except ValidationError as error:
        msg = "Catalog plugin is not a supported installed-plugin definition"
        raise CatalogSourceConfigurationError(msg) from error


def _partition_plugin_targets(
    raw_targets: object,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Separate the installed target kinds while rejecting unsupported plugin capabilities."""
    if not isinstance(raw_targets, list) or not raw_targets:
        _unsupported_descriptor("plugin targets")
    action_targets: list[Mapping[str, Any]] = []
    integration_targets: list[Mapping[str, Any]] = []
    credential_targets: list[Mapping[str, Any]] = []
    for raw_target in raw_targets:
        target = _mapping(raw_target, "plugin target")
        kind = target.get("kind")
        if kind == "Action":
            action_targets.append(target)
        elif kind == "IntegrationType":
            integration_targets.append(target)
        elif kind == "CredentialRecipe":
            credential_targets.append(target)
        else:
            _unsupported_descriptor("non-action target")
    if not action_targets:
        _unsupported_descriptor("plugin without an action")
    return action_targets, integration_targets, credential_targets


def _validate_runtime_ownership(
    version: CatalogPluginVersion,
    action_bindings: list[_ActionBinding],
    workloads: Mapping[str, Any],
) -> None:
    """Require the catalog capability set to exactly match the action runtimes."""
    runtime_kinds = {binding.action.runtime_kind for binding in action_bindings}
    if runtime_kinds != set(version.runtime_ownerships):
        _unsupported_descriptor("catalog runtime ownerships")
    if "custom-workload" in runtime_kinds:
        workload_ids = {
            binding.workload_id for binding in action_bindings if binding.action.runtime_kind == "custom-workload"
        }
        if len(workload_ids) != 1 or None in workload_ids:
            _unsupported_descriptor("multiple custom workloads")
        if set(workloads) != workload_ids:
            _unsupported_descriptor("additional workload or provider binding")
        return
    if workloads:
        _unsupported_descriptor("platform runner workload binding")
    if runtime_kinds == {"platform-runner"}:
        return
    _unsupported_descriptor("catalog runtime ownerships")


@dataclass(frozen=True)
class _ActionBinding:
    """One installed action and the root workload it selected."""

    action: InstalledActionCandidate
    workload_id: str | None


def _action_from_target(
    raw_target: object,
    version: CatalogPluginVersion,
    workloads: Mapping[str, Any],
    runner_profiles: Mapping[str, PlatformRunnerProfile],
) -> _ActionBinding:
    """Validate one canonical action target and its inherited workload binding."""
    target = _mapping(raw_target, "action target")
    expected_keys = set(
        _ACTION_TARGET_KEYS_WITH_INTEGRATION if "integrationType" in target else _ACTION_TARGET_KEYS
    )
    if "inputPresentation" in target:
        expected_keys |= _ACTION_PRESENTATION_KEY
    if "schemaDigests" in target:
        expected_keys |= _ACTION_SCHEMA_DIGESTS_KEY
    _require_keys(target, expected_keys, "action target")
    if target.get("kind") != "Action":
        _unsupported_descriptor("non-action target")
    target_metadata = _mapping(target.get("metadata"), "action metadata")
    _require_keys(target_metadata, _TARGET_METADATA_KEYS, "action metadata")
    target_name = _string(target_metadata.get("name"), "action target name")
    if target.get("identity") != f"{version.namespace}.{version.name}.{target_name}":
        _unsupported_descriptor("action identity")
    if target.get("version") != version.version:
        _unsupported_descriptor("action version")
    runtime = _mapping(target.get("runtime"), "action runtime")
    runtime_kind = runtime.get("kind")
    if runtime_kind == "platform-runner":
        return _platform_runner_action(target, runtime, runner_profiles)
    workload_id, image_digest = _custom_workload_binding(runtime, workloads)
    try:
        action = InstalledActionCandidate(
            target_name=target_name,
            display_name=_string(target_metadata.get("displayName"), "action display name"),
            description=_string(target_metadata.get("description"), "action description"),
            revision_digest=_canonical_digest(target),
            runtime_kind="custom-workload",
            runtime_abi=_CONTAINER_ABI,
            image_digest=image_digest,
            integration_type_id=_optional_string(target.get("integrationType"), "action integration type"),
            runner_profile_revision_id=None,
            input_schema=_schema(target.get("input"), "action input schema"),
            input_presentation=_input_presentation(target.get("inputPresentation")),
            output_schema=_schema(target.get("output"), "action output schema"),
            error_schema=_schema(target.get("error"), "action error schema"),
            canonical_definition=dict(target),
        )
    except ValidationError as error:
        msg = "Catalog action is not a supported installed-action definition"
        raise CatalogSourceConfigurationError(msg) from error
    return _ActionBinding(action=action, workload_id=workload_id)


def _platform_runner_action(
    target: Mapping[str, Any],
    runtime: Mapping[str, Any],
    runner_profiles: Mapping[str, PlatformRunnerProfile],
) -> _ActionBinding:
    """Bind a platform action to the profile selected solely by its driver."""
    driver, operation = _validated_platform_runner_runtime(runtime)
    profile = runner_profiles.get(driver)
    if profile is None or profile.driver != driver:
        _unsupported_descriptor("platform runner driver")
    resolved_runtime: dict[str, Any] = {
        "abi": profile.runtime_abi,
        "driver": driver,
        "image": profile.image_digest,
        "kind": "platform-runner",
        "profileRevisionId": str(profile.revision_id),
    }
    if operation is not None:
        resolved_runtime["operation"] = dict(operation)
    definition = dict(target)
    definition["runtime"] = resolved_runtime
    try:
        action = InstalledActionCandidate(
            target_name=_string(_mapping(target.get("metadata"), "action metadata").get("name"), "action target name"),
            display_name=_string(
                _mapping(target.get("metadata"), "action metadata").get("displayName"), "action display name"
            ),
            description=_string(
                _mapping(target.get("metadata"), "action metadata").get("description"), "action description"
            ),
            revision_digest=_canonical_digest(definition),
            runtime_kind="platform-runner",
            runtime_abi=profile.runtime_abi,
            image_digest=profile.image_digest,
            integration_type_id=_optional_string(target.get("integrationType"), "action integration type"),
            runner_profile_revision_id=profile.revision_id,
            input_schema=_schema(target.get("input"), "action input schema"),
            input_presentation=_input_presentation(target.get("inputPresentation")),
            output_schema=_schema(target.get("output"), "action output schema"),
            error_schema=_schema(target.get("error"), "action error schema"),
            canonical_definition=definition,
        )
    except ValidationError as error:
        msg = "Catalog action is not a supported installed-action definition"
        raise CatalogSourceConfigurationError(msg) from error
    return _ActionBinding(action=action, workload_id=None)


def _integration_type_from_target(
    target: Mapping[str, Any], version: CatalogPluginVersion
) -> InstalledIntegrationTypeCandidate:
    """Validate one canonical non-secret integration target for installation."""
    _require_keys(target, _INTEGRATION_TARGET_KEYS, "integration type target")
    _require_target_identity(target, version, "integration type")
    metadata = _mapping(target.get("metadata"), "integration type metadata")
    _require_keys(metadata, _TARGET_METADATA_KEYS, "integration type metadata")
    try:
        return InstalledIntegrationTypeCandidate(
            type_id=_owned_type_id(target.get("id"), version, "integration type"),
            display_name=_string(metadata.get("displayName"), "integration type display name"),
            description=_string(metadata.get("description"), "integration type description"),
            revision_digest=_canonical_digest(target),
            endpoint_kinds=tuple(_string_list(target.get("endpointKinds"), "integration endpoint kinds")),
            credential_type_ids=tuple(_string_list(target.get("credentialTypes"), "integration credential type IDs")),
            configuration_schema=_schema(target.get("configuration"), "integration configuration schema"),
            canonical_definition=dict(target),
        )
    except ValidationError as error:
        msg = "Catalog plugin contains an unsupported integration type definition"
        raise CatalogSourceConfigurationError(msg) from error


def _credential_type_from_target(
    target: Mapping[str, Any], version: CatalogPluginVersion
) -> InstalledCredentialTypeCandidate:
    """Validate one canonical credential recipe without ever accepting secret values."""
    _require_keys(target, _CREDENTIAL_TARGET_KEYS, "credential type target")
    _require_target_identity(target, version, "credential type")
    metadata = _mapping(target.get("metadata"), "credential type metadata")
    _require_keys(metadata, _TARGET_METADATA_KEYS, "credential type metadata")
    try:
        return InstalledCredentialTypeCandidate(
            type_id=_owned_type_id(target.get("id"), version, "credential type"),
            integration_type_id=_owned_type_id(target.get("integrationType"), version, "credential integration type"),
            display_name=_string(metadata.get("displayName"), "credential type display name"),
            description=_string(metadata.get("description"), "credential type description"),
            revision_digest=_canonical_digest(target),
            credential_schema=_schema(target.get("credentialSchema"), "credential schema"),
            secret_field_names=tuple(_string_list(target.get("secretFields"), "credential secret fields")),
            issuance=dict(_mapping(target.get("issuance"), "credential issuance")),
            canonical_definition=dict(target),
        )
    except ValidationError as error:
        msg = "Catalog plugin contains an unsupported credential type definition"
        raise CatalogSourceConfigurationError(msg) from error


def _custom_workload_binding(runtime: Mapping[str, Any], workloads: Mapping[str, Any]) -> tuple[str, str]:
    """Return the one server-admitted workload inherited by a custom action."""
    runtime_kind = runtime.get("kind")
    if runtime_kind not in {"custom-runner", "custom-workload"} or runtime.get("abi") != _CONTAINER_ABI:
        _unsupported_descriptor("custom workload runtime")
    if runtime_kind == "custom-runner":
        _require_keys(runtime, _CUSTOM_RUNNER_RUNTIME_KEYS, "custom runner runtime")
        _string(runtime.get("entryPoint"), "custom workload entry point")
    else:
        _require_keys(runtime, _CUSTOM_RUNTIME_KEYS, "custom workload runtime")
    workload_id = _string(runtime.get("workload"), "custom workload identifier")
    workload = _mapping(workloads.get(workload_id), "custom workload binding")
    _require_keys(workload, _WORKLOAD_BINDING_KEYS, "custom workload binding")
    image_digest = workload.get("image")
    if workload.get("abi") != _CONTAINER_ABI or not isinstance(image_digest, str):
        _unsupported_descriptor("custom workload binding")
    return workload_id, image_digest


def _result_from_install(version_id: UUID, installed: InstallResult) -> CatalogPluginInstallResult:
    return CatalogPluginInstallResult(
        catalog_plugin_version_id=version_id,
        plugin_installation_id=installed.plugin_installation_id,
        registry_generation=installed.generation,
        idempotent=installed.idempotent,
    )


def _require_repository_scope(repository: str, scopes: list[str]) -> None:
    if not any(repository == scope or repository.startswith(f"{scope}/") for scope in scopes):
        msg = "Catalog artifact repository is outside the configured source scope"
        raise CatalogSourceConfigurationError(msg)


def _require_artifact_identity(descriptor: Mapping[str, Any], version: CatalogPluginVersion) -> None:
    metadata = descriptor.get("metadata")
    if not isinstance(metadata, Mapping) or (
        metadata.get("namespace"),
        metadata.get("name"),
        metadata.get("version"),
    ) != (version.namespace, version.name, version.version):
        msg = "Catalog artifact identity does not match its accepted catalog entry"
        raise CatalogSourceConfigurationError(msg)


def _platform_runner_drivers(descriptor: Mapping[str, Any]) -> set[str]:
    """Return the versioned drivers declared by platform-runner action targets."""
    specification = _mapping(descriptor.get("spec"), "plugin specification")
    targets = specification.get("targets")
    if not isinstance(targets, list):
        _unsupported_descriptor("plugin targets")
    drivers: set[str] = set()
    for raw_target in targets:
        target = _mapping(raw_target, "action target")
        runtime = target.get("runtime")
        if not isinstance(runtime, Mapping) or runtime.get("kind") != "platform-runner":
            continue
        driver, _operation = _validated_platform_runner_runtime(runtime)
        drivers.add(driver)
    return drivers


def _validated_platform_runner_runtime(runtime: Mapping[str, Any]) -> tuple[str, Mapping[str, Any] | None]:
    """Validate declarative runner data before an admitted image may receive it."""
    if not {"driver", "kind"}.issubset(runtime) or set(runtime) - _PLATFORM_RUNTIME_KEYS:
        _unsupported_descriptor("platform runner runtime")
    driver = _string(runtime.get("driver"), "platform runner driver")
    operation = runtime.get("operation")
    if driver != "http.v1":
        if operation is not None or set(runtime) != {"driver", "kind"}:
            _unsupported_descriptor("platform runner operation")
        return driver, None
    http_operation = _mapping(operation, "http.v1 operation")
    if set(http_operation) - _HTTP_OPERATION_KEYS or "method" not in http_operation or "path" not in http_operation:
        _unsupported_descriptor("http.v1 operation")
    method = _string(http_operation.get("method"), "http.v1 operation method")
    path = _string(http_operation.get("path"), "http.v1 operation path")
    if method not in _HTTP_METHODS or not _valid_http_path(path):
        _unsupported_descriptor("http.v1 operation")
    for mapping_name in ("requestMap", "responseMap"):
        if mapping_name in http_operation:
            _validate_http_mapping(http_operation[mapping_name], f"http.v1 {mapping_name}")
    return driver, http_operation


def _valid_http_path(path: str) -> bool:
    """Accept only a relative path template without an alternate origin or query."""
    if not path.startswith("/") or path.startswith("//") or len(path) > _MAX_HTTP_PATH_LENGTH:
        return False
    static_path = _HTTP_PATH_INPUT.sub("", path)
    if "{" in static_path or "}" in static_path or not _HTTP_PATH_STATIC_CHARACTERS.fullmatch(path):
        return False
    return all(segment not in {".", ".."} for segment in path.split("/"))


def _validate_http_mapping(value: object, context: str) -> None:
    """Validate the deliberately small v1 field-to-pointer/literal mapping DSL."""
    mapping = _mapping(value, context)
    if not 1 <= len(mapping) <= _MAX_HTTP_MAPPING_ENTRIES:
        _unsupported_descriptor(context)
    for field, source in mapping.items():
        if not _HTTP_MAPPING_FIELD.fullmatch(field):
            _unsupported_descriptor(context)
        item = _mapping(source, context)
        if "from" in item:
            if set(item) - {"from", "optional"} or not isinstance(item.get("optional", False), bool):
                _unsupported_descriptor(context)
            pointer = _string(item.get("from"), context)
            if len(pointer) > _MAX_JSON_POINTER_LENGTH or not _JSON_POINTER.fullmatch(pointer):
                _unsupported_descriptor(context)
            continue
        if set(item) != {"literal"} or not isinstance(item["literal"], str | int | float | bool | type(None)):
            _unsupported_descriptor(context)


def _mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _unsupported_descriptor(context)
    return value


def _schema(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _unsupported_descriptor(context)
    return value


def _input_presentation(value: object) -> dict[str, Any] | None:
    """Accept only the versioned, declarative RJSF presentation contract."""
    if value is None:
        return None
    presentation = _mapping(value, "action input presentation")
    _require_keys(presentation, {"renderer", "uiSchema"}, "action input presentation")
    if presentation.get("renderer") != "rjsf.v1":
        _unsupported_descriptor("action input presentation renderer")
    ui_schema = _schema(presentation.get("uiSchema"), "action input presentation uiSchema")
    return {"renderer": "rjsf.v1", "uiSchema": dict(ui_schema)}


def _string_list(value: object, context: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _unsupported_descriptor(context)
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str):
        _unsupported_descriptor(context)
    return value


def _optional_string(value: object, context: str) -> str | None:
    if value is None:
        return None
    return _string(value, context)


def _require_target_identity(target: Mapping[str, Any], version: CatalogPluginVersion, context: str) -> None:
    metadata = _mapping(target.get("metadata"), f"{context} metadata")
    target_name = _string(metadata.get("name"), f"{context} target name")
    if (
        target.get("identity") != f"{version.namespace}.{version.name}.{target_name}"
        or target.get("version") != version.version
    ):
        _unsupported_descriptor(context)


def _owned_type_id(value: object, version: CatalogPluginVersion, context: str) -> str:
    type_id = _string(value, context)
    if not type_id.startswith(f"{version.namespace}.{version.name}."):
        _unsupported_descriptor(context)
    return type_id


def _require_keys(value: Mapping[str, Any], expected: set[str], context: str) -> None:
    if set(value) != expected:
        _unsupported_descriptor(context)


def _canonical_digest(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"sha256:{sha256(raw).hexdigest()}"


def _unsupported_descriptor(context: str) -> NoReturn:
    msg = f"Catalog plugin contains an unsupported {context}"
    raise CatalogSourceConfigurationError(msg)
