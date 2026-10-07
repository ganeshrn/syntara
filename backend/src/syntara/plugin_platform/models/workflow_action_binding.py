"""Durable immutable action-route bindings for published workflow revisions.

This table is intentionally narrower than a workflow node. It records the
server-resolved action route selected when a workflow revision is published,
without storing inputs, credentials, connection details, policy identifiers,
or any publisher-controlled execution route.
"""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID  # noqa: TC003 - SQLModel resolves table annotations at class construction.

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import Field

from syntara.core.models.base import BaseResource
from syntara.core.models.base.base_resource import AuditLevel


class WorkflowActionBinding(BaseResource, table=True):
    """One immutable installed-action route selected by a workflow revision.

    The workflow definition continues to own its structured input values. This
    record holds only server-resolved immutable route evidence, so a later
    registry-generation change cannot replace an already published image or
    runtime ABI.
    """

    __tablename__ = "workflow_action_bindings"
    __auditable__: ClassVar[AuditLevel] = AuditLevel.META
    __auditable_fields__: ClassVar[list[str]] = [
        "workflow_version_id",
        "node_id",
        "step_revision_id",
        "plugin_digest",
        "runtime_kind",
        "runtime_abi",
        "image_digest",
        "integration_type_revision_id",
        "plugin_connection_id",
        "runner_profile_revision_id",
        "registry_generation",
    ]
    __table_args__ = (
        UniqueConstraint("workflow_version_id", "node_id", name="uq_workflow_action_bindings_version_node"),
        CheckConstraint("registry_generation >= 1", name="ck_workflow_action_bindings_generation_positive"),
        Index("ix_workflow_action_bindings_step_revision", "step_revision_id"),
    )

    workflow_version_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("workflow_versions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        description="Published workflow revision that owns this immutable route.",
    )
    node_id: str = Field(
        min_length=1,
        max_length=128,
        sa_type=String(128),  # type: ignore[call-overload]
        description="Stable workflow-step identifier within the workflow revision.",
    )
    step_revision_id: str = Field(
        min_length=1,
        max_length=256,
        sa_column=Column(
            String(256),
            ForeignKey("installed_plugin_actions.step_revision_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        description="Immutable installed plugin-action revision selected at publication.",
    )
    plugin_digest: str = Field(
        min_length=71,
        max_length=71,
        sa_type=String(71),  # type: ignore[call-overload]
        description="Verified plugin artifact digest captured at publication.",
    )
    runtime_kind: str = Field(
        min_length=1,
        max_length=32,
        sa_type=String(32),  # type: ignore[call-overload]
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
        description="Server-resolved immutable image route; never workflow supplied.",
    )
    integration_type_revision_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey(
                "installed_plugin_integration_types.id",
                name="fk_workflow_action_bindings_integration_type_revision",
                ondelete="RESTRICT",
            ),
            nullable=True,
            index=True,
        ),
        description="Optional installed integration-type revision frozen with this action route.",
    )
    plugin_connection_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey(
                "plugin_connections.id",
                name="fk_workflow_action_bindings_plugin_connection",
                ondelete="RESTRICT",
            ),
            nullable=True,
            index=True,
        ),
        description="Optional project connection identity selected and verified at publication.",
    )
    runner_profile_revision_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("runner_profile_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    registry_generation: int = Field(
        ge=1,
        sa_column=Column(Integer, nullable=False),
        description="Active registry generation that admitted the action at publication.",
    )
