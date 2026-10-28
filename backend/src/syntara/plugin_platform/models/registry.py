"""Immutable installed-plugin records and registry-generation membership.

The registry is intentionally environment-scoped.  A plugin artifact and each
of its action definitions are immutable records; a registry generation selects
which installed artifacts are currently enabled.  Publishing a new generation
copies the prior enabled set and changes one singleton pointer only after the
new snapshot is complete.
"""

from __future__ import annotations

from typing import Any, ClassVar
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import Boolean, Field, Integer, SQLModel

from syntara.core.constants import FieldLimits
from syntara.core.models.base import BaseResource
from syntara.core.models.base.base_resource import AuditLevel


class PluginRegistryState(SQLModel, table=True):
    """Singleton pointer to the active immutable installed-plugin generation."""

    __tablename__ = "plugin_registry_state"
    __table_args__ = (
        UniqueConstraint("is_singleton", name="uq_plugin_registry_state_singleton"),
        CheckConstraint("is_singleton = true", name="ck_plugin_registry_state_singleton"),
        CheckConstraint("next_generation >= 1", name="ck_plugin_registry_state_next_generation"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    is_singleton: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    active_generation_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("plugin_registry_generations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    next_generation: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, server_default=text("1")),
    )


class PluginRegistryGeneration(BaseResource, table=True):
    """An immutable registry snapshot created by a successful lifecycle change."""

    __tablename__ = "plugin_registry_generations"
    __auditable__: ClassVar[AuditLevel] = AuditLevel.NONE
    __table_args__ = (
        UniqueConstraint("generation", name="uq_plugin_registry_generations_generation"),
        CheckConstraint("generation >= 1", name="ck_plugin_registry_generations_positive"),
    )

    generation: int = Field(index=True)
    change_reason: str = Field(
        min_length=1,
        max_length=128,
        sa_type=String(128),  # type: ignore[call-overload]
        description="Safe lifecycle reason, for example direct-digest-install.",
    )
    actor_id: UUID | None = Field(
        default=None,
        foreign_key="principals.id",
        ondelete="SET NULL",
        description="Administrator or service principal that created the generation.",
    )


class RunnerProfile(BaseResource, table=True):
    """Stable server-owned identity that selects one admitted runner revision."""

    __tablename__ = "runner_profiles"
    __table_args__ = (UniqueConstraint("driver", name="uq_runner_profiles_driver"),)

    driver: str = Field(
        min_length=4,
        max_length=128,
        sa_type=String(128),  # type: ignore[call-overload]
        description="Versioned runner driver identity, for example http.v1.",
    )
    active_revision_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("runner_profile_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        description="The administrator-selected immutable runner revision.",
    )


class RunnerProfileRevision(BaseResource, table=True):
    """An immutable, admitted image and ABI binding for one runner profile."""

    __tablename__ = "runner_profile_revisions"
    __auditable__: ClassVar[AuditLevel] = AuditLevel.NONE
    __table_args__ = (
        UniqueConstraint("profile_id", "revision_digest", name="uq_runner_profile_revisions_profile_revision"),
        UniqueConstraint("revision_digest", name="uq_runner_profile_revisions_digest"),
    )

    profile_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("runner_profiles.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    revision_digest: str = Field(
        min_length=71,
        max_length=71,
        sa_type=String(71),  # type: ignore[call-overload]
        description="Digest derived from the admitted driver, image, and ABI binding.",
    )
    runtime_abi: str = Field(
        min_length=1,
        max_length=64,
        sa_type=String(64),  # type: ignore[call-overload]
    )
    image_digest: str = Field(
        min_length=73,
        max_length=512,
        sa_type=String(512),  # type: ignore[call-overload]
        description="Administrator-admitted immutable runner image; never author supplied.",
    )
    verification_summary: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
        description="Bounded, non-secret image-admission evidence.",
    )
    admitted_by: UUID | None = Field(
        default=None,
        foreign_key="principals.id",
        ondelete="SET NULL",
        index=True,
    )


class InstalledPlugin(BaseResource, table=True):
    """One immutable, verified plugin artifact revision.

    This row is created only after an artifact verifier has established the
    source, digest, signature, contract, and policy outcome.  It stores no
    registry credentials, signing keys, OAuth material, or other secrets.
    """

    __tablename__ = "installed_plugins"
    __table_args__ = (
        UniqueConstraint("namespace", "name", "version", name="uq_installed_plugins_identity_version"),
        UniqueConstraint("artifact_digest", name="uq_installed_plugins_artifact_digest"),
        Index("ix_installed_plugins_namespace_name", "namespace", "name"),
    )
    __filterable_fields__: ClassVar[list[str]] = [
        *BaseResource.__filterable_fields__,
        "namespace",
        "name",
        "version",
        "artifact_digest",
    ]
    __sortable_fields__: ClassVar[list[str]] = [
        *BaseResource.__sortable_fields__,
        "namespace",
        "name",
        "version",
        "display_name",
    ]

    namespace: str = Field(
        min_length=1,
        max_length=63,
        sa_type=String(63),  # type: ignore[call-overload]
        index=True,
    )
    name: str = Field(
        min_length=1,
        max_length=63,
        sa_type=String(63),  # type: ignore[call-overload]
        index=True,
    )
    version: str = Field(
        min_length=5,
        max_length=128,
        sa_type=String(128),  # type: ignore[call-overload]
    )
    display_name: str = Field(
        min_length=1,
        max_length=128,
        sa_type=String(128),  # type: ignore[call-overload]
    )
    description: str = Field(
        min_length=1,
        max_length=FieldLimits.DESCRIPTION_MAX_LENGTH,
        sa_type=String(FieldLimits.DESCRIPTION_MAX_LENGTH),  # type: ignore[call-overload]
    )
    artifact_repository: str = Field(
        min_length=1,
        max_length=255,
        sa_type=String(255),  # type: ignore[call-overload]
        description="Repository identity verified during installation, without a tag.",
    )
    artifact_digest: str = Field(
        min_length=71,
        max_length=71,
        sa_type=String(71),  # type: ignore[call-overload]
    )
    canonical_descriptor: dict[str, Any] = Field(
        sa_type=JSONB,
        description="Bounded canonical descriptor retained after artifact verification.",
    )
    verification_summary: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
        description="Non-secret verification evidence summary for later inspection.",
    )
    installed_by: UUID | None = Field(
        default=None,
        foreign_key="principals.id",
        ondelete="SET NULL",
        index=True,
    )


class InstalledPluginAction(BaseResource, table=True):
    """Immutable executable action selected from an installed plugin artifact."""

    __tablename__ = "installed_plugin_actions"
    __table_args__ = (
        UniqueConstraint("plugin_installation_id", "target_name", name="uq_installed_plugin_actions_target"),
        UniqueConstraint("step_revision_id", name="uq_installed_plugin_actions_step_revision"),
        Index("ix_installed_plugin_actions_qualified_name", "qualified_name"),
    )

    plugin_installation_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("installed_plugins.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    target_name: str = Field(
        min_length=1,
        max_length=63,
        sa_type=String(63),  # type: ignore[call-overload]
    )
    qualified_name: str = Field(
        min_length=5,
        max_length=191,
        sa_type=String(191),  # type: ignore[call-overload]
    )
    display_name: str = Field(
        min_length=1,
        max_length=128,
        sa_type=String(128),  # type: ignore[call-overload]
    )
    description: str = Field(
        min_length=1,
        max_length=FieldLimits.DESCRIPTION_MAX_LENGTH,
        sa_type=String(FieldLimits.DESCRIPTION_MAX_LENGTH),  # type: ignore[call-overload]
    )
    revision_digest: str = Field(
        min_length=71,
        max_length=71,
        sa_type=String(71),  # type: ignore[call-overload]
    )
    step_revision_id: str = Field(
        min_length=1,
        max_length=256,
        sa_type=String(256),  # type: ignore[call-overload]
        description="Server-derived immutable identity accepted by the container ABI.",
    )
    runtime_kind: str = Field(
        sa_type=String(32),  # type: ignore[call-overload]
    )
    runtime_abi: str = Field(
        default="syntara.container/v1alpha1",
        sa_type=String(64),  # type: ignore[call-overload]
    )
    image_digest: str = Field(
        min_length=73,
        max_length=512,
        sa_type=String(512),  # type: ignore[call-overload]
        description="Server-admitted immutable workload image; never supplied by a workflow.",
    )
    integration_type_revision_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey(
                "installed_plugin_integration_types.id",
                name="fk_installed_plugin_actions_integration_type_revision",
                ondelete="RESTRICT",
            ),
            nullable=True,
            index=True,
        ),
        description="Optional immutable integration type selected by this external action.",
    )
    runner_profile_revision_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("runner_profile_revisions.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        description="Exact server-owned profile revision for a platform-runner action.",
    )
    input_schema: dict[str, Any] = Field(sa_type=JSONB)
    input_presentation: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    output_schema: dict[str, Any] = Field(sa_type=JSONB)
    error_schema: dict[str, Any] = Field(sa_type=JSONB)
    canonical_definition: dict[str, Any] = Field(sa_type=JSONB)


class InstalledPluginIntegrationType(BaseResource, table=True):
    """One immutable, non-secret integration contract from an installed plugin."""

    __tablename__ = "installed_plugin_integration_types"
    __table_args__ = (
        UniqueConstraint(
            "plugin_installation_id",
            "type_id",
            name="uq_installed_plugin_integration_types_plugin_type",
        ),
        Index("ix_installed_plugin_integration_types_type_id", "type_id"),
    )

    plugin_installation_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("installed_plugins.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    type_id: str = Field(
        min_length=5,
        max_length=191,
        sa_type=String(191),  # type: ignore[call-overload]
        description="Stable qualified integration identity supplied by the plugin contract.",
    )
    display_name: str = Field(
        min_length=1,
        max_length=128,
        sa_type=String(128),  # type: ignore[call-overload]
    )
    description: str = Field(
        min_length=1,
        max_length=FieldLimits.DESCRIPTION_MAX_LENGTH,
        sa_type=String(FieldLimits.DESCRIPTION_MAX_LENGTH),  # type: ignore[call-overload]
    )
    revision_digest: str = Field(
        min_length=71,
        max_length=71,
        sa_type=String(71),  # type: ignore[call-overload]
    )
    endpoint_kinds: list[str] = Field(default_factory=list, sa_type=JSONB)
    credential_type_ids: list[str] = Field(default_factory=list, sa_type=JSONB)
    configuration_schema: dict[str, Any] = Field(sa_type=JSONB)
    canonical_definition: dict[str, Any] = Field(sa_type=JSONB)


class InstalledPluginCredentialType(BaseResource, table=True):
    """One immutable credential recipe definition, never a credential value."""

    __tablename__ = "installed_plugin_credential_types"
    __table_args__ = (
        UniqueConstraint(
            "plugin_installation_id",
            "type_id",
            name="uq_installed_plugin_credential_types_plugin_type",
        ),
        Index("ix_installed_plugin_credential_types_type_id", "type_id"),
    )

    plugin_installation_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("installed_plugins.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    integration_type_revision_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey(
                "installed_plugin_integration_types.id",
                name="fk_installed_plugin_credential_types_integration_type_revision",
                ondelete="RESTRICT",
            ),
            nullable=False,
            index=True,
        ),
    )
    type_id: str = Field(
        min_length=5,
        max_length=191,
        sa_type=String(191),  # type: ignore[call-overload]
        description="Stable qualified credential identity supplied by the plugin contract.",
    )
    display_name: str = Field(
        min_length=1,
        max_length=128,
        sa_type=String(128),  # type: ignore[call-overload]
    )
    description: str = Field(
        min_length=1,
        max_length=FieldLimits.DESCRIPTION_MAX_LENGTH,
        sa_type=String(FieldLimits.DESCRIPTION_MAX_LENGTH),  # type: ignore[call-overload]
    )
    revision_digest: str = Field(
        min_length=71,
        max_length=71,
        sa_type=String(71),  # type: ignore[call-overload]
    )
    credential_schema: dict[str, Any] = Field(sa_type=JSONB)
    secret_field_names: list[str] = Field(default_factory=list, sa_type=JSONB)
    issuance: dict[str, Any] = Field(sa_type=JSONB)
    canonical_definition: dict[str, Any] = Field(sa_type=JSONB)


class PluginGenerationMember(SQLModel, table=True):
    """Membership of one immutable plugin artifact in one registry snapshot."""

    __tablename__ = "plugin_generation_members"
    __table_args__ = (
        UniqueConstraint("generation_id", "plugin_installation_id", name="uq_plugin_generation_member"),
        Index("ix_plugin_generation_members_plugin", "plugin_installation_id"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    generation_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("plugin_registry_generations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    plugin_installation_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("installed_plugins.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
    )
    enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
