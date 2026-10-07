"""runner profile revisions

Persist administrator-admitted platform-runner profile revisions separately
from plugin artifacts. Installed platform-runner actions pin the exact
revision selected at installation, so a later profile update cannot change an
existing workflow's image route.

Revision ID: b4d6f8a0c2e4
Revises: c3e8a4b7d2f6
Create Date: 2026-10-07 16:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b4d6f8a0c2e4"
down_revision: str | Sequence[str] | None = "c3e8a4b7d2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create immutable profile revisions and pin platform-runner actions to them."""
    op.create_table(
        "runner_profiles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "labels", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("driver", sa.String(length=128), nullable=False),
        sa.Column("active_revision_id", sa.UUID(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("driver", name="uq_runner_profiles_driver"),
    )
    op.create_index(op.f("ix_runner_profiles_created_at"), "runner_profiles", ["created_at"], unique=False)
    op.create_index(op.f("ix_runner_profiles_id"), "runner_profiles", ["id"], unique=False)

    op.create_table(
        "runner_profile_revisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "labels", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("revision_digest", sa.String(length=71), nullable=False),
        sa.Column("runtime_abi", sa.String(length=64), nullable=False),
        sa.Column("image_digest", sa.String(length=512), nullable=False),
        sa.Column(
            "verification_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("admitted_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["admitted_by"], ["principals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["profile_id"], ["runner_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "revision_digest", name="uq_runner_profile_revisions_profile_revision"),
        sa.UniqueConstraint("revision_digest", name="uq_runner_profile_revisions_digest"),
    )
    op.create_index(
        op.f("ix_runner_profile_revisions_admitted_by"),
        "runner_profile_revisions",
        ["admitted_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_runner_profile_revisions_created_at"),
        "runner_profile_revisions",
        ["created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_runner_profile_revisions_id"), "runner_profile_revisions", ["id"], unique=False)
    op.create_index(
        op.f("ix_runner_profile_revisions_profile_id"),
        "runner_profile_revisions",
        ["profile_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_runner_profiles_active_revision",
        "runner_profiles",
        "runner_profile_revisions",
        ["active_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column("installed_plugin_actions", sa.Column("runner_profile_revision_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_installed_plugin_actions_runner_profile_revision",
        "installed_plugin_actions",
        "runner_profile_revisions",
        ["runner_profile_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_installed_plugin_actions_runner_profile_revision_id"),
        "installed_plugin_actions",
        ["runner_profile_revision_id"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_installed_plugin_actions_runner_profile_binding",
        "installed_plugin_actions",
        "(runtime_kind = 'platform-runner' AND runner_profile_revision_id IS NOT NULL) "
        "OR (runtime_kind = 'custom-workload' AND runner_profile_revision_id IS NULL)",
    )


def downgrade() -> None:
    """Remove runner-profile persistence in dependency-safe order."""
    op.drop_constraint(
        "ck_installed_plugin_actions_runner_profile_binding",
        "installed_plugin_actions",
        type_="check",
    )
    op.drop_index(op.f("ix_installed_plugin_actions_runner_profile_revision_id"), table_name="installed_plugin_actions")
    op.drop_constraint(
        "fk_installed_plugin_actions_runner_profile_revision",
        "installed_plugin_actions",
        type_="foreignkey",
    )
    op.drop_column("installed_plugin_actions", "runner_profile_revision_id")
    op.drop_constraint("fk_runner_profiles_active_revision", "runner_profiles", type_="foreignkey")
    op.drop_index(op.f("ix_runner_profile_revisions_profile_id"), table_name="runner_profile_revisions")
    op.drop_index(op.f("ix_runner_profile_revisions_id"), table_name="runner_profile_revisions")
    op.drop_index(op.f("ix_runner_profile_revisions_created_at"), table_name="runner_profile_revisions")
    op.drop_index(op.f("ix_runner_profile_revisions_admitted_by"), table_name="runner_profile_revisions")
    op.drop_table("runner_profile_revisions")
    op.drop_index(op.f("ix_runner_profiles_id"), table_name="runner_profiles")
    op.drop_index(op.f("ix_runner_profiles_created_at"), table_name="runner_profiles")
    op.drop_table("runner_profiles")
