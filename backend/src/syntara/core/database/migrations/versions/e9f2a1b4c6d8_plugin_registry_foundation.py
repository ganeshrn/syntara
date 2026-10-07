"""plugin registry foundation

Create the immutable installed-plugin/action records and the singleton active
generation pointer.  The records are intentionally source- and secret-free:
OCI verification, signatures, credentials, and registry authentication remain
outside this persistence migration.

Revision ID: e9f2a1b4c6d8
Revises: d5b8c2f04e71
Create Date: 2026-10-06 13:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e9f2a1b4c6d8"
down_revision: str | Sequence[str] | None = "d5b8c2f04e71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REGISTRY_STATE_ID = "f6c7a37c-6a15-4d99-bde0-52e3b1d1ef63"


def upgrade() -> None:
    """Create immutable installed-plugin registry tables and bootstrap state."""
    op.create_table(
        "plugin_registry_generations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "labels", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("change_reason", sa.String(length=128), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.CheckConstraint("generation >= 1", name="ck_plugin_registry_generations_positive"),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("generation", name="uq_plugin_registry_generations_generation"),
    )
    op.create_index(
        op.f("ix_plugin_registry_generations_created_at"),
        "plugin_registry_generations",
        ["created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_plugin_registry_generations_id"), "plugin_registry_generations", ["id"], unique=False)
    op.create_index(
        op.f("ix_plugin_registry_generations_generation"),
        "plugin_registry_generations",
        ["generation"],
        unique=False,
    )

    op.create_table(
        "plugin_registry_state",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("is_singleton", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("active_generation_id", sa.UUID(), nullable=True),
        sa.Column("next_generation", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("is_singleton = true", name="ck_plugin_registry_state_singleton"),
        sa.CheckConstraint("next_generation >= 1", name="ck_plugin_registry_state_next_generation"),
        sa.ForeignKeyConstraint(["active_generation_id"], ["plugin_registry_generations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("is_singleton", name="uq_plugin_registry_state_singleton"),
    )

    op.create_table(
        "installed_plugins",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "labels", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("namespace", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=63), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("artifact_repository", sa.String(length=255), nullable=False),
        sa.Column("artifact_digest", sa.String(length=71), nullable=False),
        sa.Column("canonical_descriptor", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "verification_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("installed_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["installed_by"], ["principals.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_digest", name="uq_installed_plugins_artifact_digest"),
        sa.UniqueConstraint("namespace", "name", "version", name="uq_installed_plugins_identity_version"),
    )
    op.create_index(op.f("ix_installed_plugins_created_at"), "installed_plugins", ["created_at"], unique=False)
    op.create_index(op.f("ix_installed_plugins_id"), "installed_plugins", ["id"], unique=False)
    op.create_index(op.f("ix_installed_plugins_installed_by"), "installed_plugins", ["installed_by"], unique=False)
    op.create_index(op.f("ix_installed_plugins_name"), "installed_plugins", ["name"], unique=False)
    op.create_index("ix_installed_plugins_namespace_name", "installed_plugins", ["namespace", "name"], unique=False)
    op.create_index(op.f("ix_installed_plugins_namespace"), "installed_plugins", ["namespace"], unique=False)

    op.create_table(
        "installed_plugin_actions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "labels", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("plugin_installation_id", sa.UUID(), nullable=False),
        sa.Column("target_name", sa.String(length=63), nullable=False),
        sa.Column("qualified_name", sa.String(length=191), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("revision_digest", sa.String(length=71), nullable=False),
        sa.Column("step_revision_id", sa.String(length=256), nullable=False),
        sa.Column("runtime_kind", sa.String(length=32), nullable=False),
        sa.Column("runtime_abi", sa.String(length=64), nullable=False),
        sa.Column("image_digest", sa.String(length=512), nullable=False),
        sa.Column("input_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("canonical_definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["plugin_installation_id"], ["installed_plugins.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plugin_installation_id", "target_name", name="uq_installed_plugin_actions_target"),
        sa.UniqueConstraint("step_revision_id", name="uq_installed_plugin_actions_step_revision"),
    )
    op.create_index(
        op.f("ix_installed_plugin_actions_created_at"), "installed_plugin_actions", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_installed_plugin_actions_id"), "installed_plugin_actions", ["id"], unique=False)
    op.create_index(
        op.f("ix_installed_plugin_actions_plugin_installation_id"),
        "installed_plugin_actions",
        ["plugin_installation_id"],
        unique=False,
    )
    op.create_index(
        "ix_installed_plugin_actions_qualified_name", "installed_plugin_actions", ["qualified_name"], unique=False
    )

    op.create_table(
        "plugin_generation_members",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("generation_id", sa.UUID(), nullable=False),
        sa.Column("plugin_installation_id", sa.UUID(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(["generation_id"], ["plugin_registry_generations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plugin_installation_id"], ["installed_plugins.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("generation_id", "plugin_installation_id", name="uq_plugin_generation_member"),
    )
    op.create_index(
        op.f("ix_plugin_generation_members_generation_id"), "plugin_generation_members", ["generation_id"], unique=False
    )
    op.create_index(
        op.f("ix_plugin_generation_members_plugin_installation_id"),
        "plugin_generation_members",
        ["plugin_installation_id"],
        unique=False,
    )
    op.create_index(
        "ix_plugin_generation_members_plugin", "plugin_generation_members", ["plugin_installation_id"], unique=False
    )

    op.execute(
        sa.text(
            "INSERT INTO plugin_registry_state (id, is_singleton, active_generation_id, next_generation) "
            "VALUES (CAST(:id AS uuid), true, NULL, 1)"
        ).bindparams(id=_REGISTRY_STATE_ID)
    )


def downgrade() -> None:
    """Remove the registry foundation in dependency-safe order."""
    op.drop_index("ix_plugin_generation_members_plugin", table_name="plugin_generation_members")
    op.drop_index(op.f("ix_plugin_generation_members_plugin_installation_id"), table_name="plugin_generation_members")
    op.drop_index(op.f("ix_plugin_generation_members_generation_id"), table_name="plugin_generation_members")
    op.drop_table("plugin_generation_members")
    op.drop_index("ix_installed_plugin_actions_qualified_name", table_name="installed_plugin_actions")
    op.drop_index(op.f("ix_installed_plugin_actions_plugin_installation_id"), table_name="installed_plugin_actions")
    op.drop_index(op.f("ix_installed_plugin_actions_id"), table_name="installed_plugin_actions")
    op.drop_index(op.f("ix_installed_plugin_actions_created_at"), table_name="installed_plugin_actions")
    op.drop_table("installed_plugin_actions")
    op.drop_index(op.f("ix_installed_plugins_namespace"), table_name="installed_plugins")
    op.drop_index("ix_installed_plugins_namespace_name", table_name="installed_plugins")
    op.drop_index(op.f("ix_installed_plugins_name"), table_name="installed_plugins")
    op.drop_index(op.f("ix_installed_plugins_installed_by"), table_name="installed_plugins")
    op.drop_index(op.f("ix_installed_plugins_id"), table_name="installed_plugins")
    op.drop_index(op.f("ix_installed_plugins_created_at"), table_name="installed_plugins")
    op.drop_table("installed_plugins")
    op.drop_table("plugin_registry_state")
    op.drop_index(op.f("ix_plugin_registry_generations_generation"), table_name="plugin_registry_generations")
    op.drop_index(op.f("ix_plugin_registry_generations_id"), table_name="plugin_registry_generations")
    op.drop_index(op.f("ix_plugin_registry_generations_created_at"), table_name="plugin_registry_generations")
    op.drop_table("plugin_registry_generations")
