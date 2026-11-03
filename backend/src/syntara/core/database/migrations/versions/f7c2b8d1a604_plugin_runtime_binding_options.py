"""Store immutable runtime-binding options separately from plugin artifacts.

Revision ID: f7c2b8d1a604
Revises: a6d3f2b9c841
Create Date: 2026-11-03 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f7c2b8d1a604"
down_revision: str | Sequence[str] | None = "a6d3f2b9c841"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add immutable binding choices and workflow selection evidence."""
    op.create_table(
        "installed_plugin_runtime_bindings",
        sa.Column("step_revision_id", sa.String(length=256), nullable=False),
        sa.Column("binding_digest", sa.String(length=71), nullable=False),
        sa.Column("binding_index_digest", sa.String(length=71), nullable=False),
        sa.Column("binding_index_generation", sa.Integer(), nullable=False),
        sa.Column("workload_id", sa.String(length=63), nullable=False),
        sa.Column("default_option", sa.String(length=63), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "verification_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "binding_index_generation >= 1",
            name="ck_runtime_binding_index_generation_positive",
        ),
        sa.ForeignKeyConstraint(["approved_by"], ["principals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["step_revision_id"],
            ["installed_plugin_actions.step_revision_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "step_revision_id",
            "binding_digest",
            "binding_index_digest",
            name="uq_installed_plugin_runtime_binding_revision_index",
        ),
    )
    op.create_index(
        "ix_installed_plugin_runtime_bindings_step",
        "installed_plugin_runtime_bindings",
        ["step_revision_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_installed_plugin_runtime_bindings_approved_by"),
        "installed_plugin_runtime_bindings",
        ["approved_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_installed_plugin_runtime_bindings_id"),
        "installed_plugin_runtime_bindings",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_installed_plugin_runtime_bindings_created_at"),
        "installed_plugin_runtime_bindings",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_installed_plugin_runtime_bindings_updated_at"),
        "installed_plugin_runtime_bindings",
        ["updated_at"],
        unique=False,
    )

    op.add_column(
        "workflow_action_bindings",
        sa.Column("runtime_binding_digest", sa.String(length=71), nullable=True),
    )
    op.add_column(
        "workflow_action_bindings",
        sa.Column("binding_index_digest", sa.String(length=71), nullable=True),
    )
    op.add_column(
        "workflow_action_bindings",
        sa.Column("workload_id", sa.String(length=63), nullable=True),
    )
    op.add_column(
        "workflow_action_bindings",
        sa.Column("workload_option", sa.String(length=63), nullable=True),
    )


def downgrade() -> None:
    """Remove the proof-of-concept runtime binding projection."""
    op.drop_column("workflow_action_bindings", "workload_option")
    op.drop_column("workflow_action_bindings", "workload_id")
    op.drop_column("workflow_action_bindings", "binding_index_digest")
    op.drop_column("workflow_action_bindings", "runtime_binding_digest")
    op.drop_index(
        op.f("ix_installed_plugin_runtime_bindings_updated_at"),
        table_name="installed_plugin_runtime_bindings",
    )
    op.drop_index(
        op.f("ix_installed_plugin_runtime_bindings_created_at"),
        table_name="installed_plugin_runtime_bindings",
    )
    op.drop_index(
        op.f("ix_installed_plugin_runtime_bindings_id"),
        table_name="installed_plugin_runtime_bindings",
    )
    op.drop_index(
        op.f("ix_installed_plugin_runtime_bindings_approved_by"),
        table_name="installed_plugin_runtime_bindings",
    )
    op.drop_index(
        "ix_installed_plugin_runtime_bindings_step",
        table_name="installed_plugin_runtime_bindings",
    )
    op.drop_table("installed_plugin_runtime_bindings")
