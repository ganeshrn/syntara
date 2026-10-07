"""Persistent local catalog-source configuration and immutable discovery projections."""

from __future__ import annotations

import re
from datetime import datetime  # noqa: TC003 - SQLModel resolves table annotations at class construction.
from typing import Any, ClassVar, Literal, Self
from uuid import UUID  # noqa: TC003 - SQLModel resolves table annotations at class construction.

from pydantic import ConfigDict, field_validator, model_validator
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import DateTime, Field

from syntara.core.lib.url_validation import validate_host_url
from syntara.core.models.base import BaseResource
from syntara.core.models.base.base_resource import AuditLevel
from syntara.plugin_platform.source_profile import (
    STANDARD_CATALOG_SOURCE_MODE,
    CatalogSourceMode,
    validate_catalog_source_profile,
)

_IDENTIFIER_PATTERN = r"^[a-z][a-z0-9-]{0,62}$"
_OPAQUE_REFERENCE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"
_REPOSITORY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,254}$")
_SHA256_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
_SEMVER_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_REASON_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")
_MAX_REPOSITORY_SCOPES = 128
_MIN_RECONCILIATION_INTERVAL_SECONDS = 60
_MAX_RECONCILIATION_INTERVAL_SECONDS = 86_400


def validate_catalog_registry_origin(value: str) -> str:
    """Normalize a registry origin before its profile applies transport policy."""
    return validate_host_url(value)


def validate_catalog_identifier(value: str) -> str:
    """Validate the lower-case identifier grammar shared by source IDs and channels."""
    if re.fullmatch(_IDENTIFIER_PATTERN, value) is None:
        msg = "Catalog source identifiers must use lower-case identifier grammar"
        raise ValueError(msg)
    return value


def validate_catalog_opaque_reference(value: str | None) -> str | None:
    """Permit opaque handles while rejecting whitespace and secret-like free text."""
    if value is not None and re.fullmatch(_OPAQUE_REFERENCE_PATTERN, value) is None:
        msg = "Catalog source references must use opaque reference grammar"
        raise ValueError(msg)
    return value


def validate_catalog_repository_path(value: str) -> str:
    """Reject non-normalized OCI repository paths before persistence or use."""
    if _REPOSITORY_PATTERN.fullmatch(value) is None or any(part in {".", ".."} for part in value.split("/")):
        msg = "Catalog repository paths must be normalized OCI repository paths"
        raise ValueError(msg)
    return value


def validate_catalog_repository_scopes(values: list[str]) -> list[str]:
    """Validate bounded, deduplicated repository prefixes for one catalog source."""
    if len(values) != len(set(values)):
        msg = "Catalog repository scopes must be unique"
        raise ValueError(msg)
    return [validate_catalog_repository_path(value) for value in values]


class PluginSource(BaseResource, table=True):
    """Administrator-configured route and policy references for one catalog source.

    Credential, custom-CA, and trust-policy values are opaque references only.
    Their secret material belongs to the existing trusted secret/credential
    boundaries and is never stored in this table.
    """

    __tablename__ = "plugin_sources"
    __auditable__: ClassVar[AuditLevel] = AuditLevel.META
    __auditable_fields__: ClassVar[list[str]] = [
        "source_id",
        "display_name",
        "source_mode",
        "registry_origin",
        "index_repository",
        "channel",
        "repository_scopes",
        "credential_ref",
        "trust_policy_ref",
        "custom_ca_ref",
        "mirror_policy",
        "reconciliation_interval_seconds",
        "enabled",
        "active_reconciliation_id",
    ]
    __filterable_fields__: ClassVar[list[str]] = [
        *BaseResource.__filterable_fields__,
        "source_id",
        "display_name",
        "channel",
        "enabled",
    ]
    __sortable_fields__: ClassVar[list[str]] = [
        *BaseResource.__sortable_fields__,
        "source_id",
        "display_name",
        "channel",
    ]
    __table_args__ = (
        UniqueConstraint("source_id", name="uq_plugin_sources_source_id"),
        Index("ix_plugin_sources_enabled", "enabled"),
        CheckConstraint(
            "mirror_policy IN ('direct', 'digest-preserving')",
            name="ck_plugin_sources_mirror_policy",
        ),
        CheckConstraint(
            "source_mode IN ('standard', 'local-poc')",
            name="ck_plugin_sources_source_mode",
        ),
        CheckConstraint(
            "reconciliation_interval_seconds BETWEEN 60 AND 86400",
            name="ck_plugin_sources_reconciliation_interval",
        ),
    )

    source_id: str = Field(
        min_length=1,
        max_length=63,
        sa_type=String(63),  # type: ignore[call-overload]
        description="Stable source identity also required by its catalog index.",
    )
    display_name: str = Field(
        min_length=1,
        max_length=128,
        sa_type=String(128),  # type: ignore[call-overload]
    )
    source_mode: CatalogSourceMode = Field(
        default=STANDARD_CATALOG_SOURCE_MODE,
        sa_column=Column(String(16), nullable=False, server_default=text("'standard'")),
        description="Standard production source or the explicit unsafe local-PoC source.",
    )
    registry_origin: str = Field(
        min_length=9,
        max_length=512,
        sa_type=String(512),  # type: ignore[call-overload]
    )
    index_repository: str = Field(
        min_length=1,
        max_length=255,
        sa_type=String(255),  # type: ignore[call-overload]
    )
    channel: str = Field(
        min_length=1,
        max_length=63,
        sa_type=String(63),  # type: ignore[call-overload]
    )
    repository_scopes: list[str] = Field(
        min_length=1,
        max_length=_MAX_REPOSITORY_SCOPES,
        sa_type=JSONB,
        description="Allowed repository prefixes for this source's signed catalog entries.",
    )
    credential_ref: str = Field(
        min_length=1,
        max_length=256,
        sa_type=String(256),  # type: ignore[call-overload]
    )
    trust_policy_ref: str = Field(
        min_length=1,
        max_length=256,
        sa_type=String(256),  # type: ignore[call-overload]
    )
    custom_ca_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        sa_type=String(256),  # type: ignore[call-overload]
    )
    mirror_policy: Literal["direct", "digest-preserving"] = Field(
        default="direct",
        sa_column=Column(String(32), nullable=False, server_default=text("'direct'")),
    )
    reconciliation_interval_seconds: int = Field(
        default=300,
        ge=_MIN_RECONCILIATION_INTERVAL_SECONDS,
        le=_MAX_RECONCILIATION_INTERVAL_SECONDS,
        sa_column=Column(Integer, nullable=False, server_default=text("300")),
    )
    enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    active_reconciliation_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey(
                "plugin_source_reconciliations.id",
                name="fk_plugin_sources_active_reconciliation",
                ondelete="SET NULL",
                use_alter=True,
            ),
            nullable=True,
        ),
        description="The complete accepted catalog projection currently visible to users.",
    )

    @field_validator("registry_origin")
    @classmethod
    def _validate_registry_origin(cls, value: str) -> str:
        """Require a normalized origin with no path/query injection."""
        return validate_catalog_registry_origin(value)

    @field_validator("source_id", "channel")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        """Match the shared catalog contract's lower-case identifier grammar."""
        return validate_catalog_identifier(value)

    @field_validator("credential_ref", "trust_policy_ref", "custom_ca_ref")
    @classmethod
    def _validate_opaque_reference(cls, value: str | None) -> str | None:
        """Allow opaque handles without accepting whitespace or secret material."""
        return validate_catalog_opaque_reference(value)

    @field_validator("index_repository")
    @classmethod
    def _validate_index_repository(cls, value: str) -> str:
        """Reject non-normalized OCI repository paths before persistence."""
        return validate_catalog_repository_path(value)

    @field_validator("repository_scopes")
    @classmethod
    def _validate_repository_scopes(cls, values: list[str]) -> list[str]:
        """Persist a bounded, deduplicated set of normalized repository prefixes."""
        return validate_catalog_repository_scopes(values)

    @model_validator(mode="after")
    def _validate_source_profile(self) -> Self:
        """Keep the unsafe local profile from becoming a generic trust bypass."""
        validate_catalog_source_profile(
            source_mode=self.source_mode,
            registry_origin=self.registry_origin,
            credential_ref=self.credential_ref,
            trust_policy_ref=self.trust_policy_ref,
            custom_ca_ref=self.custom_ca_ref,
            mirror_policy=self.mirror_policy,
        )
        return self


class PluginSourceReconciliation(BaseResource, table=True):
    """An immutable accepted, rejected, or stale observation of one source."""

    __tablename__ = "plugin_source_reconciliations"
    __auditable__: ClassVar[AuditLevel] = AuditLevel.NONE
    # SQLModel constructs table rows by assigning fields one at a time. The
    # outcome validator is cross-field, so assignment validation would reject
    # a valid row while its required fields are still being populated. API and
    # service boundaries use model_validate() before persistence; PostgreSQL
    # check constraints remain the durable backstop.
    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True,
        validate_by_name=True,
        validate_assignment=False,
        extra="forbid",
    )
    __table_args__ = (
        CheckConstraint(
            "state IN ('accepted', 'rejected', 'stale')",
            name="ck_plugin_source_reconciliations_state",
        ),
        CheckConstraint(
            "entry_count >= 0",
            name="ck_plugin_source_reconciliations_entry_count",
        ),
        CheckConstraint(
            "catalog_generation IS NOT NULL AND catalog_index_digest IS NOT NULL "
            "AND issued_at IS NOT NULL AND expires_at IS NOT NULL "
            "AND ((state = 'accepted' AND failure_code IS NULL) "
            "OR (state <> 'accepted' AND failure_code IS NOT NULL))",
            name="ck_plugin_source_reconciliations_outcome",
        ),
        CheckConstraint(
            "expires_at > issued_at",
            name="ck_plugin_source_reconciliations_time_window",
        ),
        CheckConstraint(
            "initiation_kind IN ('manual', 'scheduled', 'test')",
            name="ck_plugin_source_reconciliations_initiation_kind",
        ),
        Index("ix_plugin_source_reconciliations_source_created", "plugin_source_id", "created_at"),
    )

    plugin_source_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("plugin_sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    state: Literal["accepted", "rejected", "stale"] = Field(sa_type=String(16))  # type: ignore[call-overload]
    catalog_generation: int | None = Field(
        default=None,
        ge=1,
        sa_type=Integer,
        sa_column_kwargs={"nullable": False},
    )
    catalog_index_digest: str | None = Field(
        default=None,
        min_length=71,
        max_length=71,
        sa_type=String(71),  # type: ignore[call-overload]
        sa_column_kwargs={"nullable": False},
    )
    previous_index_digest: str | None = Field(
        default=None,
        min_length=71,
        max_length=71,
        sa_type=String(71),  # type: ignore[call-overload]
    )
    failure_code: str | None = Field(
        default=None,
        min_length=3,
        max_length=128,
        sa_type=String(128),  # type: ignore[call-overload]
    )
    issued_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[call-overload]
        sa_column_kwargs={"nullable": False},
    )
    expires_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[call-overload]
        sa_column_kwargs={"nullable": False},
    )
    entry_count: int = Field(default=0, ge=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    verification_summary: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    initiated_by: UUID | None = Field(
        default=None,
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("principals.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    initiation_kind: Literal["manual", "scheduled", "test"] = Field(
        default="manual",
        sa_column=Column(String(16), nullable=False, server_default=text("'manual'")),
    )

    @model_validator(mode="after")
    def _validate_outcome_shape(self) -> Self:
        """Retain observed index evidence without letting failures advance a source."""
        if self.catalog_generation is None or self.catalog_index_digest is None:
            msg = "Catalog reconciliation requires an observed generation and index digest"
            raise ValueError(msg)
        if self.issued_at is None or self.expires_at is None:
            msg = "Catalog reconciliation requires issued and expiry timestamps"
            raise ValueError(msg)
        if self.state == "accepted":
            if self.failure_code is not None:
                msg = "Accepted reconciliation cannot have a failure code"
                raise ValueError(msg)
        elif self.failure_code is None:
            msg = "Rejected or stale reconciliation requires a failure code"
            raise ValueError(msg)
        if self.issued_at is not None and self.expires_at is not None and self.expires_at <= self.issued_at:
            msg = "Catalog reconciliation expiry must be later than its issue time"
            raise ValueError(msg)
        return self

    @field_validator("catalog_index_digest", "previous_index_digest")
    @classmethod
    def _validate_digest(cls, value: str | None) -> str | None:
        """Accept only lower-case SHA-256 content digests."""
        if value is not None and _SHA256_PATTERN.fullmatch(value) is None:
            msg = "Catalog digest must be a lower-case SHA-256 digest"
            raise ValueError(msg)
        return value

    @field_validator("failure_code")
    @classmethod
    def _validate_failure_code(cls, value: str | None) -> str | None:
        """Keep machine-readable failure reasons stable and safe for UI display."""
        if value is not None and _REASON_CODE_PATTERN.fullmatch(value) is None:
            msg = "Catalog reconciliation failure code must use upper-case reason-code grammar"
            raise ValueError(msg)
        return value


class CatalogPluginVersion(BaseResource, table=True):
    """One immutable plugin version projected from an accepted reconciliation."""

    __tablename__ = "catalog_plugin_versions"
    __auditable__: ClassVar[AuditLevel] = AuditLevel.NONE
    __filterable_fields__: ClassVar[list[str]] = [
        *BaseResource.__filterable_fields__,
        "namespace",
        "name",
        "version",
        "runtime_ownership",
        "deprecated",
        "withdrawn",
    ]
    __sortable_fields__: ClassVar[list[str]] = [
        *BaseResource.__sortable_fields__,
        "namespace",
        "name",
        "version",
        "runtime_ownership",
        "deprecated",
        "withdrawn",
    ]
    __table_args__ = (
        UniqueConstraint(
            "reconciliation_id",
            "namespace",
            "name",
            "version",
            name="uq_catalog_plugin_versions_reconciliation_identity",
        ),
        Index("ix_catalog_plugin_versions_identity", "namespace", "name", "version"),
        CheckConstraint(
            "runtime_ownership IN ('platform-runner', 'custom-workload')",
            name="ck_catalog_plugin_versions_runtime_ownership",
        ),
    )

    reconciliation_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("plugin_source_reconciliations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    namespace: str = Field(
        min_length=1,
        max_length=63,
        sa_type=String(63),  # type: ignore[call-overload]
    )
    name: str = Field(
        min_length=1,
        max_length=63,
        sa_type=String(63),  # type: ignore[call-overload]
    )
    version: str = Field(
        min_length=5,
        max_length=128,
        sa_type=String(128),  # type: ignore[call-overload]
    )
    artifact_repository: str = Field(
        min_length=1,
        max_length=255,
        sa_type=String(255),  # type: ignore[call-overload]
    )
    artifact_digest: str = Field(
        min_length=71,
        max_length=71,
        sa_type=String(71),  # type: ignore[call-overload]
    )
    documentation_digest: str = Field(
        min_length=71,
        max_length=71,
        sa_type=String(71),  # type: ignore[call-overload]
    )
    runtime_ownership: Literal["platform-runner", "custom-workload"] = Field(
        sa_type=String(32),  # type: ignore[call-overload]
    )
    deprecated: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    withdrawn: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    canonical_entry: dict[str, Any] = Field(
        sa_type=JSONB,
        description="Safe canonical index entry retained for read-only inspection.",
    )

    @field_validator("artifact_repository")
    @classmethod
    def _validate_artifact_repository(cls, value: str) -> str:
        """Keep browser-visible catalog rows free of ambiguous repository paths."""
        if _REPOSITORY_PATTERN.fullmatch(value) is None or any(part in {".", ".."} for part in value.split("/")):
            msg = "Plugin artifact repository must be a normalized OCI repository path"
            raise ValueError(msg)
        return value

    @field_validator("namespace", "name")
    @classmethod
    def _validate_plugin_identifier(cls, value: str) -> str:
        """Keep catalog identity compatible with SDK identifier grammar."""
        if re.fullmatch(_IDENTIFIER_PATTERN, value) is None:
            msg = "Catalog plugin identity must use lower-case identifier grammar"
            raise ValueError(msg)
        return value

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        """Accept the SemVer form required by the catalog contract."""
        if _SEMVER_PATTERN.fullmatch(value) is None:
            msg = "Catalog plugin version must use supported SemVer grammar"
            raise ValueError(msg)
        return value

    @field_validator("artifact_digest", "documentation_digest")
    @classmethod
    def _validate_plugin_digest(cls, value: str) -> str:
        """Accept only lower-case SHA-256 content digests."""
        if _SHA256_PATTERN.fullmatch(value) is None:
            msg = "Catalog plugin digest must be a lower-case SHA-256 digest"
            raise ValueError(msg)
        return value


class CatalogPluginAsset(BaseResource, table=True):
    """A bounded, immutable catalog asset such as rendered documentation or an icon."""

    __tablename__ = "catalog_plugin_assets"
    __auditable__: ClassVar[AuditLevel] = AuditLevel.NONE
    # SQLModel constructs table rows by assigning fields one at a time. The
    # documentation invariant spans asset kind, media type, and rendered
    # content, so assignment validation would reject a valid row before all
    # fields have been set. Service boundaries use model_validate() before
    # persistence; the database constraint remains the durable backstop.
    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True,
        validate_by_name=True,
        validate_assignment=False,
        extra="forbid",
    )
    __table_args__ = (
        UniqueConstraint(
            "catalog_plugin_version_id",
            "asset_kind",
            name="uq_catalog_plugin_assets_version_kind",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_catalog_plugin_assets_size_bytes"),
    )

    catalog_plugin_version_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("catalog_plugin_versions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    asset_kind: str = Field(
        min_length=1,
        max_length=64,
        sa_type=String(64),  # type: ignore[call-overload]
    )
    media_type: str = Field(
        min_length=3,
        max_length=128,
        sa_type=String(128),  # type: ignore[call-overload]
    )
    digest: str = Field(
        min_length=71,
        max_length=71,
        sa_type=String(71),  # type: ignore[call-overload]
    )
    size_bytes: int = Field(ge=0, sa_type=Integer)
    rendered_html: str | None = Field(
        default=None,
        sa_type=Text(),  # type: ignore[call-overload]
        description="Sanitized rendered documentation only; raw publisher Markdown is never persisted here.",
    )
    verification_summary: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )

    @model_validator(mode="after")
    def _validate_documentation_content(self) -> Self:
        """Require sanitized display content only for the documentation asset kind."""
        if self.asset_kind == "documentation":
            if self.media_type != "text/markdown" or self.rendered_html is None:
                msg = "Catalog documentation assets require Markdown identity and sanitized rendered content"
                raise ValueError(msg)
        elif self.rendered_html is not None:
            msg = "Only catalog documentation assets may store rendered content"
            raise ValueError(msg)
        return self

    @field_validator("asset_kind")
    @classmethod
    def _validate_asset_kind(cls, value: str) -> str:
        """Use the shared lower-case identifier grammar for asset identities."""
        if re.fullmatch(_IDENTIFIER_PATTERN, value) is None:
            msg = "Catalog asset kind must use lower-case identifier grammar"
            raise ValueError(msg)
        return value

    @field_validator("digest")
    @classmethod
    def _validate_asset_digest(cls, value: str) -> str:
        """Accept only lower-case SHA-256 content digests."""
        if _SHA256_PATTERN.fullmatch(value) is None:
            msg = "Catalog asset digest must be a lower-case SHA-256 digest"
            raise ValueError(msg)
        return value
