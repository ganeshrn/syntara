"""Immutable runtime-binding revisions admitted for installed plugin steps."""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID  # noqa: TC003 - SQLModel resolves table annotations at class construction.

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlmodel import Field

from syntara.core.models.base import BaseResource
from syntara.core.models.base.base_resource import AuditLevel


class InstalledPluginRuntimeBinding(BaseResource, table=True):
    """One immutable RuntimeBinding/workload selection set for one plugin step."""

    __tablename__ = "installed_plugin_runtime_bindings"
    __auditable__: ClassVar[AuditLevel] = AuditLevel.META
    __auditable_fields__: ClassVar[list[str]] = [
        "step_revision_id",
        "binding_digest",
        "binding_index_digest",
        "binding_index_generation",
        "workload_id",
        "default_option",
    ]
    __table_args__ = (
        UniqueConstraint(
            "step_revision_id",
            "binding_digest",
            "binding_index_digest",
            name="uq_installed_plugin_runtime_binding_revision_index",
        ),
        CheckConstraint("binding_index_generation >= 1", name="ck_runtime_binding_index_generation_positive"),
        Index("ix_installed_plugin_runtime_bindings_step", "step_revision_id"),
    )

    step_revision_id: str = Field(
        min_length=1,
        max_length=256,
        sa_column=Column(
            String(256),
            ForeignKey("installed_plugin_actions.step_revision_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        description="Installed step revision that declares this workload.",
    )
    binding_digest: str = Field(min_length=71, max_length=71, sa_type=String(71))  # type: ignore[call-overload]
    binding_index_digest: str = Field(min_length=71, max_length=71, sa_type=String(71))  # type: ignore[call-overload]
    binding_index_generation: int = Field(ge=1, sa_column=Column(Integer, nullable=False))
    workload_id: str = Field(min_length=1, max_length=63, sa_type=String(63))  # type: ignore[call-overload]
    default_option: str = Field(min_length=1, max_length=63, sa_type=String(63))  # type: ignore[call-overload]
    options: list[dict[str, object]] = Field(sa_type=JSONB)
    verification_summary: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    approved_by: UUID | None = Field(
        default=None,
        foreign_key="principals.id",
        ondelete="SET NULL",
        index=True,
    )
