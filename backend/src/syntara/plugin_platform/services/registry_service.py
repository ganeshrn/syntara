"""Immutable installed-plugin persistence and active-generation resolution.

This service is intentionally behind the artifact-verification boundary.  It
does not resolve OCI references, fetch registry content, evaluate signatures,
or accept browser/workflow input.  A later direct-digest install orchestrator
must perform those checks and supply a ``VerifiedPluginCandidate`` only after
all verification succeeds.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator
from sqlmodel import col, select

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlmodel.ext.asyncio.session import AsyncSession

from syntara.core.exceptions import SafeValueError
from syntara.core.jsonb_limits import validate_jsonb_size
from syntara.plugin_platform.exceptions import (
    InstalledActionNotFoundError,
    InstalledPluginNotFoundError,
    PluginInstallConflictError,
    PluginRegistryConfigurationError,
)
from syntara.plugin_platform.models.registry import (
    InstalledPlugin,
    InstalledPluginAction,
    InstalledPluginCredentialType,
    InstalledPluginIntegrationType,
    PluginGenerationMember,
    PluginRegistryGeneration,
    PluginRegistryState,
)

_JSON_FIELD_MAX_BYTES = 1_048_576
_IDENTIFIER_PATTERN = r"^[a-z][a-z0-9-]{0,62}$"
_SEMVER_PATTERN = r"^(?:0|[1-9][0-9]*)\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$"
_SHA256_PATTERN = r"^sha256:[a-f0-9]{64}$"
_IMAGE_DIGEST_PATTERN = r"^[a-z0-9][a-z0-9./_-]*@sha256:[a-f0-9]{64}$"
_REPOSITORY_PATTERN = r"^[a-z0-9][a-z0-9._/-]{0,254}$"
_QUALIFIED_IDENTIFIER_PATTERN = r"^[a-z][a-z0-9-]{0,62}(?:\.[a-z][a-z0-9-]{0,62})+$"
_FIELD_IDENTIFIER_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,127}$"
_IDENTIFIER_RE = re.compile(_IDENTIFIER_PATTERN)
_QUALIFIED_IDENTIFIER_RE = re.compile(_QUALIFIED_IDENTIFIER_PATTERN)
_FIELD_IDENTIFIER_RE = re.compile(_FIELD_IDENTIFIER_PATTERN)
_MAX_OAUTH_SCOPES = 64
_MAX_OAUTH_SCOPE_LENGTH = 128
_MAX_JWT_AUDIENCE_LENGTH = 256

_INTEGRATION_ENDPOINT_KINDS_ERROR = "integration endpoint kinds must be unique identifiers"
_INTEGRATION_CREDENTIAL_TYPES_ERROR = "integration credential type IDs must be unique qualified identifiers"
_INTEGRATION_CONFIGURATION_SCHEMA_ERROR = "integration configuration is not a valid JSON Schema document"
_CREDENTIAL_FIELD_NAMES_ERROR = "credential secret field names must be unique field identifiers"
_CREDENTIAL_SCHEMA_ERROR = "credential schema is not a valid JSON Schema document"
_CREDENTIAL_ISSUANCE_ERROR = "credential issuance is not an allowed broker recipe"
_DUPLICATE_INTEGRATION_TYPE_ERROR = "plugin candidate contains duplicate integration type IDs"
_DUPLICATE_CREDENTIAL_TYPE_ERROR = "plugin candidate contains duplicate credential type IDs"
_INCOMPATIBLE_CREDENTIAL_TYPE_ERROR = "integration type references an undeclared or incompatible credential type"
_UNDECLARED_INTEGRATION_TYPE_ERROR = "credential type references an undeclared integration type"
_UNDECLARED_ACTION_INTEGRATION_TYPE_ERROR = "action references an undeclared integration type"


class InstalledActionCandidate(BaseModel):
    """A single immutable action after policy resolved its runtime binding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_name: str = Field(min_length=1, max_length=63, pattern=_IDENTIFIER_PATTERN)
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2_000)
    revision_digest: str = Field(min_length=71, max_length=71, pattern=_SHA256_PATTERN)
    runtime_kind: Literal["platform-runner", "custom-workload"]
    runtime_abi: Literal["syntara.container/v1alpha1"] = "syntara.container/v1alpha1"
    image_digest: str = Field(min_length=73, max_length=512, pattern=_IMAGE_DIGEST_PATTERN)
    integration_type_id: str | None = Field(
        default=None,
        min_length=5,
        max_length=191,
        pattern=_QUALIFIED_IDENTIFIER_PATTERN,
    )
    runner_profile_revision_id: UUID | None = None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    error_schema: dict[str, Any]
    canonical_definition: dict[str, Any]

    @field_validator("input_schema", "output_schema", "error_schema", "canonical_definition")
    @classmethod
    def _validate_bounded_json(cls, value: dict[str, Any], info: ValidationInfo) -> dict[str, Any]:
        return validate_jsonb_size(value, field_name=str(info.field_name), max_bytes=_JSON_FIELD_MAX_BYTES)

    @field_validator("input_schema", "output_schema", "error_schema")
    @classmethod
    def _validate_json_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure later workflow/UI validation never receives an invalid schema."""
        try:
            Draft202012Validator.check_schema(value)
        except SchemaError as error:
            msg = "action schema is not a valid JSON Schema document"
            raise SafeValueError(msg) from error
        return value

    @model_validator(mode="after")
    def _validate_runner_profile_binding(self) -> InstalledActionCandidate:
        """Require a persisted profile only for server-managed runners."""
        if self.runtime_kind == "platform-runner" and self.runner_profile_revision_id is None:
            msg = "platform-runner actions require a runner profile revision"
            raise SafeValueError(msg)
        if self.runtime_kind == "custom-workload" and self.runner_profile_revision_id is not None:
            msg = "custom-workload actions cannot bind a runner profile revision"
            raise SafeValueError(msg)
        return self


class InstalledIntegrationTypeCandidate(BaseModel):
    """One verified non-secret integration definition from a plugin artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type_id: str = Field(min_length=5, max_length=191, pattern=_QUALIFIED_IDENTIFIER_PATTERN)
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2_000)
    revision_digest: str = Field(min_length=71, max_length=71, pattern=_SHA256_PATTERN)
    endpoint_kinds: tuple[str, ...] = Field(min_length=1, max_length=16)
    credential_type_ids: tuple[str, ...] = Field(min_length=1, max_length=16)
    configuration_schema: dict[str, Any]
    canonical_definition: dict[str, Any]

    @field_validator("endpoint_kinds")
    @classmethod
    def _validate_endpoint_kinds(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(_IDENTIFIER_RE.fullmatch(item) is None for item in value):
            raise SafeValueError(_INTEGRATION_ENDPOINT_KINDS_ERROR)
        return value

    @field_validator("credential_type_ids")
    @classmethod
    def _validate_credential_type_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(_QUALIFIED_IDENTIFIER_RE.fullmatch(item) is None for item in value):
            raise SafeValueError(_INTEGRATION_CREDENTIAL_TYPES_ERROR)
        return value

    @field_validator("configuration_schema", "canonical_definition")
    @classmethod
    def _validate_bounded_json(cls, value: dict[str, Any], info: ValidationInfo) -> dict[str, Any]:
        return validate_jsonb_size(value, field_name=str(info.field_name), max_bytes=_JSON_FIELD_MAX_BYTES)

    @field_validator("configuration_schema")
    @classmethod
    def _validate_configuration_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            Draft202012Validator.check_schema(value)
        except SchemaError as error:
            raise SafeValueError(_INTEGRATION_CONFIGURATION_SCHEMA_ERROR) from error
        return value


class InstalledCredentialTypeCandidate(BaseModel):
    """One verified credential recipe definition; it never carries a secret value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type_id: str = Field(min_length=5, max_length=191, pattern=_QUALIFIED_IDENTIFIER_PATTERN)
    integration_type_id: str = Field(min_length=5, max_length=191, pattern=_QUALIFIED_IDENTIFIER_PATTERN)
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2_000)
    revision_digest: str = Field(min_length=71, max_length=71, pattern=_SHA256_PATTERN)
    credential_schema: dict[str, Any]
    secret_field_names: tuple[str, ...] = Field(min_length=1, max_length=64)
    issuance: dict[str, Any]
    canonical_definition: dict[str, Any]

    @field_validator("secret_field_names")
    @classmethod
    def _validate_secret_field_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(_FIELD_IDENTIFIER_RE.fullmatch(item) is None for item in value):
            raise SafeValueError(_CREDENTIAL_FIELD_NAMES_ERROR)
        return value

    @field_validator("credential_schema", "issuance", "canonical_definition")
    @classmethod
    def _validate_bounded_json(cls, value: dict[str, Any], info: ValidationInfo) -> dict[str, Any]:
        return validate_jsonb_size(value, field_name=str(info.field_name), max_bytes=_JSON_FIELD_MAX_BYTES)

    @field_validator("credential_schema")
    @classmethod
    def _validate_credential_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            Draft202012Validator.check_schema(value)
        except SchemaError as error:
            raise SafeValueError(_CREDENTIAL_SCHEMA_ERROR) from error
        return value

    @field_validator("issuance")
    @classmethod
    def _validate_issuance(cls, value: dict[str, Any]) -> dict[str, Any]:
        kind = value.get("kind")
        if kind == "static-bearer.v1" and set(value) == {"kind"}:
            return value
        if (
            kind == "oauth2-authorization-code.v1"
            and set(value) == {"kind", "pkce", "scopes"}
            and value.get("pkce") is True
            and isinstance(value.get("scopes"), list)
            and 1 <= len(value["scopes"]) <= _MAX_OAUTH_SCOPES
            and all(isinstance(scope, str) and 1 <= len(scope) <= _MAX_OAUTH_SCOPE_LENGTH for scope in value["scopes"])
            and len(value["scopes"]) == len(set(value["scopes"]))
        ):
            return value
        if (
            kind == "jwt-token-exchange.v1"
            and set(value) == {"audience", "kind"}
            and isinstance(value.get("audience"), str)
            and 1 <= len(value["audience"]) <= _MAX_JWT_AUDIENCE_LENGTH
        ):
            return value
        raise SafeValueError(_CREDENTIAL_ISSUANCE_ERROR)


class VerifiedPluginCandidate(BaseModel):
    """Result of verification, ready for immutable persistence only.

    The type makes the call boundary explicit; it does not itself establish
    trust.  The future verifier owns trust establishment and calls this service
    only after checking the artifact before parsing/installing it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: str = Field(min_length=1, max_length=63, pattern=_IDENTIFIER_PATTERN)
    name: str = Field(min_length=1, max_length=63, pattern=_IDENTIFIER_PATTERN)
    version: str = Field(min_length=5, max_length=128, pattern=_SEMVER_PATTERN)
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2_000)
    artifact_repository: str = Field(min_length=1, max_length=255, pattern=_REPOSITORY_PATTERN)
    artifact_digest: str = Field(min_length=71, max_length=71, pattern=_SHA256_PATTERN)
    canonical_descriptor: dict[str, Any]
    verification_summary: dict[str, Any] = Field(default_factory=dict)
    actions: tuple[InstalledActionCandidate, ...] = Field(min_length=1, max_length=100)
    integration_types: tuple[InstalledIntegrationTypeCandidate, ...] = ()
    credential_types: tuple[InstalledCredentialTypeCandidate, ...] = ()

    @field_validator("canonical_descriptor", "verification_summary")
    @classmethod
    def _validate_bounded_json(cls, value: dict[str, Any], info: ValidationInfo) -> dict[str, Any]:
        return validate_jsonb_size(value, field_name=str(info.field_name), max_bytes=_JSON_FIELD_MAX_BYTES)

    @field_validator("actions")
    @classmethod
    def _validate_unique_action_targets(
        cls, actions: tuple[InstalledActionCandidate, ...]
    ) -> tuple[InstalledActionCandidate, ...]:
        names = [action.target_name for action in actions]
        if len(names) != len(set(names)):
            msg = "plugin candidate contains duplicate action target names"
            raise SafeValueError(msg)
        revisions = [action.revision_digest for action in actions]
        if len(revisions) != len(set(revisions)):
            msg = "plugin candidate contains duplicate action revision digests"
            raise SafeValueError(msg)
        return actions

    @model_validator(mode="after")
    def _validate_connection_type_links(self) -> VerifiedPluginCandidate:
        """Reject dangling or cross-plugin connection references before persistence."""
        integrations = {integration.type_id: integration for integration in self.integration_types}
        credentials = {credential.type_id: credential for credential in self.credential_types}
        if len(integrations) != len(self.integration_types):
            raise SafeValueError(_DUPLICATE_INTEGRATION_TYPE_ERROR)
        if len(credentials) != len(self.credential_types):
            raise SafeValueError(_DUPLICATE_CREDENTIAL_TYPE_ERROR)
        for integration in self.integration_types:
            for credential_type_id in integration.credential_type_ids:
                credential = credentials.get(credential_type_id)
                if credential is None or credential.integration_type_id != integration.type_id:
                    raise SafeValueError(_INCOMPATIBLE_CREDENTIAL_TYPE_ERROR)
        for credential in self.credential_types:
            if credential.integration_type_id not in integrations:
                raise SafeValueError(_UNDECLARED_INTEGRATION_TYPE_ERROR)
        for action in self.actions:
            if action.integration_type_id is not None and action.integration_type_id not in integrations:
                raise SafeValueError(_UNDECLARED_ACTION_INTEGRATION_TYPE_ERROR)
        return self


@dataclass(frozen=True)
class InstallResult:
    """The persisted install and generation selected by this call."""

    plugin_installation_id: UUID
    generation: int
    idempotent: bool


@dataclass(frozen=True)
class LifecycleResult:
    """The generation selected by an enable or disable lifecycle operation."""

    plugin_installation_id: UUID
    generation: int
    enabled: bool
    idempotent: bool


@dataclass(frozen=True)
class ResolvedInstalledAction:
    """Trusted execution route resolved from the active registry generation."""

    step_revision_id: str
    plugin_digest: str
    runtime_kind: str
    runtime_abi: str
    image_digest: str
    integration_type_revision_id: UUID | None
    runner_profile_revision_id: UUID | None
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    error_schema: Mapping[str, Any]
    canonical_definition: Mapping[str, Any]
    registry_generation: int | None


class PluginRegistryService:
    """Persist verified plugin revisions and resolve only active action routes."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the service to one async database session."""
        self._session = session

    async def install_verified(
        self,
        candidate: VerifiedPluginCandidate,
        *,
        actor_id: UUID | None,
        change_reason: str = "direct-digest-install",
    ) -> InstallResult:
        """Install a verified artifact into a new complete active generation.

        A duplicate exact artifact is idempotent.  A different digest for an
        existing namespace/name/version is rejected, keeping version identity
        immutable.  This method is deliberately not an OCI verification API.
        """
        state = await self._state_for_update()
        existing = await self._existing_identity(candidate)
        if existing is not None:
            if existing.artifact_digest != candidate.artifact_digest:
                msg = (
                    "A different artifact is already installed for "
                    f"{candidate.namespace}.{candidate.name} {candidate.version}"
                )
                raise PluginInstallConflictError(msg)
            active_generation = await self._active_generation(state)
            if active_generation is None:
                msg = "Installed plugin registry is missing its active generation"
                raise PluginRegistryConfigurationError(msg)
            return InstallResult(existing.id, active_generation.generation, idempotent=True)

        generation = PluginRegistryGeneration(
            generation=state.next_generation,
            change_reason=change_reason,
            actor_id=actor_id,
        )
        plugin = InstalledPlugin(
            namespace=candidate.namespace,
            name=candidate.name,
            version=candidate.version,
            display_name=candidate.display_name,
            description=candidate.description,
            artifact_repository=candidate.artifact_repository,
            artifact_digest=candidate.artifact_digest,
            canonical_descriptor=candidate.canonical_descriptor,
            verification_summary=candidate.verification_summary,
            installed_by=actor_id,
        )
        self._session.add_all([generation, plugin])
        await self._session.flush()

        installed_integrations = {
            integration.type_id: InstalledPluginIntegrationType(
                plugin_installation_id=plugin.id,
                type_id=integration.type_id,
                display_name=integration.display_name,
                description=integration.description,
                revision_digest=integration.revision_digest,
                endpoint_kinds=list(integration.endpoint_kinds),
                credential_type_ids=list(integration.credential_type_ids),
                configuration_schema=integration.configuration_schema,
                canonical_definition=integration.canonical_definition,
            )
            for integration in candidate.integration_types
        }
        self._session.add_all(installed_integrations.values())
        await self._session.flush()

        self._session.add_all(
            [
                InstalledPluginCredentialType(
                    plugin_installation_id=plugin.id,
                    integration_type_revision_id=installed_integrations[credential.integration_type_id].id,
                    type_id=credential.type_id,
                    display_name=credential.display_name,
                    description=credential.description,
                    revision_digest=credential.revision_digest,
                    credential_schema=credential.credential_schema,
                    secret_field_names=list(credential.secret_field_names),
                    issuance=credential.issuance,
                    canonical_definition=credential.canonical_definition,
                )
                for credential in candidate.credential_types
            ]
        )

        for action in candidate.actions:
            self._session.add(
                InstalledPluginAction(
                    plugin_installation_id=plugin.id,
                    target_name=action.target_name,
                    qualified_name=_qualified_name(candidate, action),
                    display_name=action.display_name,
                    description=action.description,
                    revision_digest=action.revision_digest,
                    step_revision_id=_step_revision_id(candidate, action),
                    runtime_kind=action.runtime_kind,
                    runtime_abi=action.runtime_abi,
                    image_digest=action.image_digest,
                    integration_type_revision_id=(
                        installed_integrations[action.integration_type_id].id
                        if action.integration_type_id is not None
                        else None
                    ),
                    runner_profile_revision_id=action.runner_profile_revision_id,
                    input_schema=action.input_schema,
                    output_schema=action.output_schema,
                    error_schema=action.error_schema,
                    canonical_definition=action.canonical_definition,
                )
            )

        previous_members = await self._members_for_active_generation(state)
        self._session.add_all(
            [
                PluginGenerationMember(
                    generation_id=generation.id,
                    plugin_installation_id=member.plugin_installation_id,
                    enabled=member.enabled,
                )
                for member in previous_members
            ]
        )
        self._session.add(
            PluginGenerationMember(
                generation_id=generation.id,
                plugin_installation_id=plugin.id,
                enabled=True,
            )
        )
        state.active_generation_id = generation.id
        state.next_generation += 1
        await self._session.commit()
        return InstallResult(plugin.id, generation.generation, idempotent=False)

    async def resolve_enabled_action(self, step_revision_id: str) -> ResolvedInstalledAction:
        """Resolve an action only if it is enabled in the active generation."""
        state = await self._state_for_read()
        active_generation = await self._active_generation(state)
        if active_generation is None:
            raise InstalledActionNotFoundError
        query = (
            select(InstalledPluginAction, InstalledPlugin)
            .join(
                PluginGenerationMember,
                col(PluginGenerationMember.plugin_installation_id) == col(InstalledPluginAction.plugin_installation_id),
            )
            .join(InstalledPlugin, col(InstalledPlugin.id) == col(InstalledPluginAction.plugin_installation_id))
            .where(
                col(PluginGenerationMember.generation_id) == active_generation.id,
                col(PluginGenerationMember.enabled).is_(True),
                col(InstalledPluginAction.step_revision_id) == step_revision_id,
            )
        )
        row = (await self._session.exec(query)).one_or_none()
        if row is None:
            raise InstalledActionNotFoundError
        action, plugin = row
        return _resolved_action(action, plugin, registry_generation=active_generation.generation)

    async def resolve_pinned_action(self, step_revision_id: str) -> ResolvedInstalledAction:
        """Read a revision already pinned by a published workflow.

        Publication requires an enabled action, but an immutable published
        workflow must not silently change or stop using its exact route merely
        because a later registry generation disables it. Lifecycle APIs will
        later reject destructive removal while durable bindings exist.
        """
        query = (
            select(InstalledPluginAction, InstalledPlugin)
            .join(InstalledPlugin, col(InstalledPlugin.id) == col(InstalledPluginAction.plugin_installation_id))
            .where(col(InstalledPluginAction.step_revision_id) == step_revision_id)
        )
        row = (await self._session.exec(query)).one_or_none()
        if row is None:
            raise InstalledActionNotFoundError
        action, plugin = row
        return _resolved_action(action, plugin, registry_generation=None)

    async def set_enabled(
        self,
        plugin_installation_id: UUID,
        *,
        enabled: bool,
        actor_id: UUID | None,
        change_reason: str | None = None,
    ) -> LifecycleResult:
        """Publish a new immutable generation with one plugin enabled or disabled.

        The operation deliberately creates no destructive change to plugin or
        action definition rows.  Later workflow-reference checks must guard
        public disable/uninstall APIs; this internal foundation has no public
        caller and is used only to establish copy-forward lifecycle semantics.
        """
        state = await self._state_for_update()
        active_generation = await self._active_generation(state)
        if active_generation is None:
            raise InstalledPluginNotFoundError
        members = await self._members_for_active_generation(state)
        current = next((member for member in members if member.plugin_installation_id == plugin_installation_id), None)
        if current is None:
            raise InstalledPluginNotFoundError
        if current.enabled is enabled:
            return LifecycleResult(
                plugin_installation_id=plugin_installation_id,
                generation=active_generation.generation,
                enabled=enabled,
                idempotent=True,
            )

        generation = PluginRegistryGeneration(
            generation=state.next_generation,
            change_reason=change_reason or ("plugin-enable" if enabled else "plugin-disable"),
            actor_id=actor_id,
        )
        self._session.add(generation)
        await self._session.flush()
        self._session.add_all(
            [
                PluginGenerationMember(
                    generation_id=generation.id,
                    plugin_installation_id=member.plugin_installation_id,
                    enabled=enabled if member.plugin_installation_id == plugin_installation_id else member.enabled,
                )
                for member in members
            ]
        )
        state.active_generation_id = generation.id
        state.next_generation += 1
        await self._session.commit()
        return LifecycleResult(
            plugin_installation_id=plugin_installation_id,
            generation=generation.generation,
            enabled=enabled,
            idempotent=False,
        )

    async def _state_for_update(self) -> PluginRegistryState:
        result = await self._session.exec(select(PluginRegistryState).with_for_update())
        state = result.one_or_none()
        if state is None:
            msg = "Installed plugin registry has not been initialized"
            raise PluginRegistryConfigurationError(msg)
        return state

    async def _state_for_read(self) -> PluginRegistryState:
        result = await self._session.exec(select(PluginRegistryState))
        state = result.one_or_none()
        if state is None:
            msg = "Installed plugin registry has not been initialized"
            raise PluginRegistryConfigurationError(msg)
        return state

    async def _active_generation(self, state: PluginRegistryState) -> PluginRegistryGeneration | None:
        if state.active_generation_id is None:
            return None
        result = await self._session.exec(
            select(PluginRegistryGeneration).where(PluginRegistryGeneration.id == state.active_generation_id)
        )
        return result.one_or_none()

    async def _members_for_active_generation(self, state: PluginRegistryState) -> list[PluginGenerationMember]:
        if state.active_generation_id is None:
            return []
        result = await self._session.exec(
            select(PluginGenerationMember).where(PluginGenerationMember.generation_id == state.active_generation_id)
        )
        return list(result.all())

    async def _existing_identity(self, candidate: VerifiedPluginCandidate) -> InstalledPlugin | None:
        query = select(InstalledPlugin).where(
            col(InstalledPlugin.namespace) == candidate.namespace,
            col(InstalledPlugin.name) == candidate.name,
            col(InstalledPlugin.version) == candidate.version,
        )
        result = await self._session.exec(query)
        return result.one_or_none()


def _qualified_name(candidate: VerifiedPluginCandidate, action: InstalledActionCandidate) -> str:
    """Create a readable qualified action name from immutable plugin identity."""
    return f"{candidate.namespace}.{candidate.name}.{action.target_name}"


def _step_revision_id(candidate: VerifiedPluginCandidate, action: InstalledActionCandidate) -> str:
    """Build a bounded ABI-safe step revision ID without truncation or ambiguity."""
    return f"{_qualified_name(candidate, action)}:{action.revision_digest.removeprefix('sha256:')}"


def _resolved_action(
    action: InstalledPluginAction,
    plugin: InstalledPlugin,
    *,
    registry_generation: int | None,
) -> ResolvedInstalledAction:
    """Map immutable persistence records to one trusted runtime route."""
    return ResolvedInstalledAction(
        step_revision_id=action.step_revision_id,
        plugin_digest=plugin.artifact_digest,
        runtime_kind=action.runtime_kind,
        runtime_abi=action.runtime_abi,
        image_digest=action.image_digest,
        integration_type_revision_id=action.integration_type_revision_id,
        runner_profile_revision_id=action.runner_profile_revision_id,
        input_schema=action.input_schema,
        output_schema=action.output_schema,
        error_schema=action.error_schema,
        canonical_definition=deepcopy(action.canonical_definition),
        registry_generation=registry_generation,
    )
