"""Administrator API schemas for non-secret catalog-source configuration."""

from datetime import datetime
from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import ConfigDict, field_validator
from sqlmodel import Field, SQLModel

from syntara.core.models.base import BaseListParams
from syntara.core.models.pagination import ResourcesResponse
from syntara.plugin_platform.models.catalog import (
    validate_catalog_identifier,
    validate_catalog_opaque_reference,
    validate_catalog_registry_origin,
    validate_catalog_repository_path,
    validate_catalog_repository_scopes,
)
from syntara.plugin_platform.source_profile import STANDARD_CATALOG_SOURCE_MODE, CatalogSourceMode


class _PluginSourceFields(SQLModel):
    """Shared mutable catalog-source configuration with the database model's validation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")  # type: ignore[assignment]

    display_name: str = Field(min_length=1, max_length=128)
    source_mode: CatalogSourceMode = Field(
        default=STANDARD_CATALOG_SOURCE_MODE,
        description="Standard production source or the explicit unsafe local-PoC source.",
    )
    registry_origin: str = Field(min_length=9, max_length=512)
    index_repository: str = Field(min_length=1, max_length=255)
    channel: str = Field(min_length=1, max_length=63)
    repository_scopes: list[str] = Field(min_length=1, max_length=128)
    credential_ref: str = Field(
        min_length=1,
        max_length=256,
        description="Opaque reference to backend-managed registry access; never credential material.",
    )
    trust_policy_ref: str = Field(
        min_length=1,
        max_length=256,
        description="Opaque reference to the catalog-index trust policy.",
    )
    custom_ca_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="Optional opaque reference to backend-managed custom CA material.",
    )
    mirror_policy: Literal["direct", "digest-preserving"] = "direct"
    reconciliation_interval_seconds: int = Field(default=300, ge=60, le=86_400)

    @field_validator("registry_origin")
    @classmethod
    def _validate_registry_origin(cls, value: str) -> str:
        return validate_catalog_registry_origin(value)

    @field_validator("channel")
    @classmethod
    def _validate_channel(cls, value: str) -> str:
        return validate_catalog_identifier(value)

    @field_validator("index_repository")
    @classmethod
    def _validate_index_repository(cls, value: str) -> str:
        return validate_catalog_repository_path(value)

    @field_validator("repository_scopes")
    @classmethod
    def _validate_repository_scopes(cls, value: list[str]) -> list[str]:
        return validate_catalog_repository_scopes(value)

    @field_validator("credential_ref", "trust_policy_ref", "custom_ca_ref")
    @classmethod
    def _validate_opaque_reference(cls, value: str | None) -> str | None:
        return validate_catalog_opaque_reference(value)


class PluginSourceCreate(_PluginSourceFields):
    """Request body for registering one local catalog source."""

    source_id: str = Field(min_length=1, max_length=63)
    enabled: bool = True

    @field_validator("source_id")
    @classmethod
    def _validate_source_id(cls, value: str) -> str:
        return validate_catalog_identifier(value)


class PluginSourceUpdate(SQLModel):
    """Request body for updating a catalog source without changing its stable identity."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")  # type: ignore[assignment]

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    source_mode: CatalogSourceMode | None = None
    registry_origin: str | None = Field(default=None, min_length=9, max_length=512)
    index_repository: str | None = Field(default=None, min_length=1, max_length=255)
    channel: str | None = Field(default=None, min_length=1, max_length=63)
    repository_scopes: list[str] | None = Field(default=None, min_length=1, max_length=128)
    credential_ref: str | None = Field(default=None, min_length=1, max_length=256)
    trust_policy_ref: str | None = Field(default=None, min_length=1, max_length=256)
    custom_ca_ref: str | None = Field(default=None, min_length=1, max_length=256)
    mirror_policy: Literal["direct", "digest-preserving"] | None = None
    reconciliation_interval_seconds: int | None = Field(default=None, ge=60, le=86_400)

    @field_validator("registry_origin")
    @classmethod
    def _validate_registry_origin(cls, value: str | None) -> str | None:
        return validate_catalog_registry_origin(value) if value is not None else None

    @field_validator("channel")
    @classmethod
    def _validate_channel(cls, value: str | None) -> str | None:
        return validate_catalog_identifier(value) if value is not None else None

    @field_validator("index_repository")
    @classmethod
    def _validate_index_repository(cls, value: str | None) -> str | None:
        return validate_catalog_repository_path(value) if value is not None else None

    @field_validator("repository_scopes")
    @classmethod
    def _validate_repository_scopes(cls, value: list[str] | None) -> list[str] | None:
        return validate_catalog_repository_scopes(value) if value is not None else None

    @field_validator("credential_ref", "trust_policy_ref", "custom_ca_ref")
    @classmethod
    def _validate_required_opaque_reference(cls, value: str | None) -> str | None:
        return validate_catalog_opaque_reference(value) if value is not None else None


class PluginSourceRead(SQLModel):
    """Non-secret catalog-source configuration returned to authorized administrators."""

    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)  # type: ignore[assignment]

    id: UUID
    source_id: str
    display_name: str
    source_mode: CatalogSourceMode
    registry_origin: str
    index_repository: str
    channel: str
    repository_scopes: list[str]
    credential_ref: str
    trust_policy_ref: str
    custom_ca_ref: str | None
    mirror_policy: Literal["direct", "digest-preserving"]
    reconciliation_interval_seconds: int
    enabled: bool
    active_reconciliation_id: UUID | None
    created_at: datetime
    updated_at: datetime
    labels: dict[str, str] = Field(default_factory=dict)


class PluginSourceListParams(BaseListParams):
    """Query parameters for paginated administrator catalog-source listings."""

    source_id: str | None = None
    display_name: str | None = None
    channel: str | None = None
    enabled: bool | None = None


class PluginSourceListResponse(ResourcesResponse[PluginSourceRead]):
    """Paginated catalog-source response."""


class PluginSourceReconciliationRead(SQLModel):
    """Safe result of one manually requested catalog-source reconciliation."""

    reconciliation_id: UUID
    state: Literal["accepted", "rejected", "stale"]
    failure_code: str | None
    catalog_generation: int
    catalog_index_digest: str
    entry_count: int
    idempotent: bool


class CatalogPluginRead(SQLModel):
    """Safe immutable plugin-version projection visible in the local catalog."""

    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)  # type: ignore[assignment]

    id: UUID
    source_id: str = Field(description="Stable catalog source identity for this visible projection.")
    reconciliation_id: UUID
    namespace: str
    name: str
    version: str
    artifact_repository: str
    artifact_digest: str
    documentation_digest: str
    runtime_ownerships: list[Literal["platform-runner", "custom-workload"]] = Field(
        min_length=1,
        max_length=2,
    )
    deprecated: bool
    withdrawn: bool
    created_at: datetime
    updated_at: datetime


class CatalogPluginDocumentationRead(SQLModel):
    """Sanitized documentation for one source-qualified visible catalog version."""

    source_id: str = Field(description="Stable catalog source identity for this visible projection.")
    namespace: str
    name: str
    version: str
    documentation_digest: str = Field(description="Digest of the verified publisher Markdown bytes.")
    rendered_html: str = Field(description="Sanitized rendered documentation; never raw publisher Markdown.")
    rendered_at: datetime


class CatalogPluginDocumentationHydrationRead(SQLModel):
    """Safe outcome of one administrator-requested documentation hydration."""

    catalog_plugin_version_id: UUID
    documentation_digest: str
    idempotent: bool = Field(
        description="True when the exact verified documentation was already stored for this catalog revision."
    )


class CatalogPluginInstallRead(SQLModel):
    """Safe result of an administrator's catalog-selected plugin installation."""

    catalog_plugin_version_id: UUID
    plugin_installation_id: UUID
    registry_generation: int = Field(ge=1)
    idempotent: bool = Field(
        description="True when the exact verified catalog revision was already active in the installed registry."
    )


class CatalogPluginDetailRead(SQLModel):
    """All currently visible versions of one namespaced catalog plugin."""

    namespace: str
    name: str
    versions: list[CatalogPluginRead]


class CatalogPluginListParams(BaseListParams):
    """Query parameters for read-only visible catalog-version listings."""

    source_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=63,
        description="Optional stable catalog source identity.",
    )
    namespace: str | None = Field(default=None, min_length=1, max_length=63)
    name: str | None = Field(default=None, min_length=1, max_length=63)
    version: str | None = Field(default=None, min_length=5, max_length=128)
    deprecated: bool | None = None
    withdrawn: bool | None = None

    @field_validator("source_id", "namespace", "name")
    @classmethod
    def _validate_identifier(cls, value: str | None) -> str | None:
        return validate_catalog_identifier(value) if value is not None else None


class CatalogPluginListResponse(ResourcesResponse[CatalogPluginRead]):
    """Paginated catalog version projection for plugin discovery."""


class InstalledPluginActionRead(SQLModel):
    """One immutable action exposed by an installed plugin revision.

    The schemas are safe, validated contracts for a workflow authoring client;
    canonical publisher definitions and secret-bearing integration state remain
    internal to the control plane.
    """

    target_name: str
    qualified_name: str
    display_name: str
    description: str
    revision_digest: str
    step_revision_id: str
    runtime_kind: Literal["platform-runner", "custom-workload"]
    runtime_abi: str
    image_digest: str
    integration_type_revision_id: UUID | None = None
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    error_schema: dict[str, object]


class InstalledPluginRead(SQLModel):
    """Safe summary of a plugin revision in the active registry snapshot."""

    id: UUID
    namespace: str
    name: str
    version: str
    display_name: str
    description: str
    artifact_repository: str
    artifact_digest: str
    enabled: bool
    action_count: int = Field(ge=0)
    installed_at: datetime


class InstalledPluginDetailRead(InstalledPluginRead):
    """Safe installed-plugin detail including its workflow action contracts."""

    registry_generation: int = Field(ge=1)
    actions: list[InstalledPluginActionRead]
    integration_types: list["InstalledPluginIntegrationTypeRead"] = Field(default_factory=list)
    credential_types: list["InstalledPluginCredentialTypeRead"] = Field(default_factory=list)


class InstalledPluginIntegrationTypeRead(SQLModel):
    """Safe installed integration contract needed to configure a connection."""

    id: UUID
    type_id: str
    display_name: str
    description: str
    endpoint_kinds: list[str]
    credential_type_ids: list[str]
    configuration_schema: dict[str, object]


class InstalledPluginCredentialTypeRead(SQLModel):
    """Safe installed credential recipe; secret values are never part of this contract."""

    id: UUID
    integration_type_revision_id: UUID
    type_id: str
    display_name: str
    description: str
    credential_schema: dict[str, object]
    secret_field_names: list[str]
    issuance: dict[str, object]


class PluginConnectionCreate(SQLModel):
    """Create a project-scoped connection using installed immutable type revisions."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2_000)
    project_id: UUID
    integration_type_revision_id: UUID
    credential_type_revision_id: UUID
    endpoint_kind: str = Field(min_length=1, max_length=63)
    configuration: dict[str, Any] = Field(default_factory=dict)
    credential_inputs: dict[str, Any] = Field(default_factory=dict, exclude=True)


class PluginConnectionUpdate(SQLModel):
    """Mutable lifecycle state for an existing project connection."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")  # type: ignore[assignment]

    enabled: bool


class PluginConnectionRead(SQLModel):
    """Safe project connection metadata. Credential values and secret IDs are excluded."""

    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)  # type: ignore[assignment]

    id: UUID
    name: str
    description: str | None
    project_id: UUID
    integration_type_revision_id: UUID
    credential_type_revision_id: UUID | None
    endpoint_kind: str
    configuration: dict[str, object]
    enabled: bool
    created_at: datetime
    updated_at: datetime


class PluginConnectionListParams(BaseListParams):
    """Filters for project-scoped connection listing and workflow selection."""

    project_id: UUID
    integration_type_revision_id: UUID | None = None
    enabled: bool | None = None


class PluginConnectionListResponse(ResourcesResponse[PluginConnectionRead]):
    """Non-secret plugin connection list."""


class InstalledPluginListParams(BaseListParams):
    """Query parameters for the active installed-plugin registry snapshot."""

    namespace: str | None = Field(default=None, min_length=1, max_length=63)
    name: str | None = Field(default=None, min_length=1, max_length=63)
    version: str | None = Field(default=None, min_length=5, max_length=128)
    enabled: bool | None = None


class InstalledPluginListResponse(ResourcesResponse[InstalledPluginRead]):
    """Paginated safe view of the current immutable registry snapshot."""

    registry_generation: int | None = Field(
        default=None,
        ge=1,
        description="Active generation; null when no plugin has been installed yet.",
    )
    registry_change_reason: str | None = Field(
        default=None,
        description="Safe reason that produced the active registry snapshot.",
    )
    registry_published_at: datetime | None = Field(
        default=None,
        description="Time at which the active registry snapshot was published.",
    )
