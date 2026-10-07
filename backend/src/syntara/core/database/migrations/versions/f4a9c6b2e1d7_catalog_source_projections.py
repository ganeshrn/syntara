"""catalog source projections

Create administrator-configured catalog sources plus immutable reconciliation
and catalog projection records. Source credential, trust-policy, and custom CA
fields store only opaque handles; no secret material is persisted here.

Revision ID: f4a9c6b2e1d7
Revises: e9f2a1b4c6d8
Create Date: 2026-10-06 14:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f4a9c6b2e1d7"
down_revision: str | Sequence[str] | None = "e9f2a1b4c6d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create local source configuration and immutable discovery projections."""
    op.create_table(
        "plugin_sources",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "labels", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("source_id", sa.String(length=63), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("registry_origin", sa.String(length=512), nullable=False),
        sa.Column("index_repository", sa.String(length=255), nullable=False),
        sa.Column("channel", sa.String(length=63), nullable=False),
        sa.Column("repository_scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("credential_ref", sa.String(length=256), nullable=False),
        sa.Column("trust_policy_ref", sa.String(length=256), nullable=False),
        sa.Column("custom_ca_ref", sa.String(length=256), nullable=True),
        sa.Column("mirror_policy", sa.String(length=32), server_default=sa.text("'direct'"), nullable=False),
        sa.Column("reconciliation_interval_seconds", sa.Integer(), server_default=sa.text("300"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("active_reconciliation_id", sa.UUID(), nullable=True),
        sa.CheckConstraint(
            "mirror_policy IN ('direct', 'digest-preserving')",
            name="ck_plugin_sources_mirror_policy",
        ),
        sa.CheckConstraint(
            "reconciliation_interval_seconds BETWEEN 60 AND 86400",
            name="ck_plugin_sources_reconciliation_interval",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", name="uq_plugin_sources_source_id"),
    )
    op.create_index(op.f("ix_plugin_sources_created_at"), "plugin_sources", ["created_at"], unique=False)
    op.create_index(op.f("ix_plugin_sources_id"), "plugin_sources", ["id"], unique=False)
    op.create_index("ix_plugin_sources_enabled", "plugin_sources", ["enabled"], unique=False)

    op.create_table(
        "plugin_source_reconciliations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "labels", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("plugin_source_id", sa.UUID(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("catalog_generation", sa.Integer(), nullable=False),
        sa.Column("catalog_index_digest", sa.String(length=71), nullable=False),
        sa.Column("previous_index_digest", sa.String(length=71), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column(
            "verification_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("initiated_by", sa.UUID(), nullable=True),
        sa.Column("initiation_kind", sa.String(length=16), server_default=sa.text("'manual'"), nullable=False),
        sa.CheckConstraint(
            "state IN ('accepted', 'rejected', 'stale')",
            name="ck_plugin_source_reconciliations_state",
        ),
        sa.CheckConstraint("entry_count >= 0", name="ck_plugin_source_reconciliations_entry_count"),
        sa.CheckConstraint(
            "catalog_generation IS NOT NULL AND catalog_index_digest IS NOT NULL "
            "AND issued_at IS NOT NULL AND expires_at IS NOT NULL "
            "AND ((state = 'accepted' AND failure_code IS NULL) "
            "OR (state <> 'accepted' AND failure_code IS NOT NULL))",
            name="ck_plugin_source_reconciliations_outcome",
        ),
        sa.CheckConstraint(
            "expires_at > issued_at",
            name="ck_plugin_source_reconciliations_time_window",
        ),
        sa.CheckConstraint(
            "initiation_kind IN ('manual', 'scheduled', 'test')",
            name="ck_plugin_source_reconciliations_initiation_kind",
        ),
        sa.ForeignKeyConstraint(["initiated_by"], ["principals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plugin_source_id"], ["plugin_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_plugin_source_reconciliations_created_at"),
        "plugin_source_reconciliations",
        ["created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_plugin_source_reconciliations_id"), "plugin_source_reconciliations", ["id"], unique=False)
    op.create_index(
        op.f("ix_plugin_source_reconciliations_initiated_by"),
        "plugin_source_reconciliations",
        ["initiated_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_plugin_source_reconciliations_plugin_source_id"),
        "plugin_source_reconciliations",
        ["plugin_source_id"],
        unique=False,
    )
    op.create_index(
        "ix_plugin_source_reconciliations_source_created",
        "plugin_source_reconciliations",
        ["plugin_source_id", "created_at"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_plugin_sources_active_reconciliation",
        "plugin_sources",
        "plugin_source_reconciliations",
        ["active_reconciliation_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "catalog_plugin_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "labels", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("reconciliation_id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=63), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("artifact_repository", sa.String(length=255), nullable=False),
        sa.Column("artifact_digest", sa.String(length=71), nullable=False),
        sa.Column("documentation_digest", sa.String(length=71), nullable=False),
        sa.Column("runtime_ownership", sa.String(length=32), nullable=False),
        sa.Column("deprecated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("withdrawn", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("canonical_entry", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "runtime_ownership IN ('platform-runner', 'custom-workload')",
            name="ck_catalog_plugin_versions_runtime_ownership",
        ),
        sa.ForeignKeyConstraint(["reconciliation_id"], ["plugin_source_reconciliations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "reconciliation_id",
            "namespace",
            "name",
            "version",
            name="uq_catalog_plugin_versions_reconciliation_identity",
        ),
    )
    op.create_index(
        op.f("ix_catalog_plugin_versions_created_at"), "catalog_plugin_versions", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_catalog_plugin_versions_id"), "catalog_plugin_versions", ["id"], unique=False)
    op.create_index(
        op.f("ix_catalog_plugin_versions_reconciliation_id"),
        "catalog_plugin_versions",
        ["reconciliation_id"],
        unique=False,
    )
    op.create_index(
        "ix_catalog_plugin_versions_identity",
        "catalog_plugin_versions",
        ["namespace", "name", "version"],
        unique=False,
    )

    op.create_table(
        "catalog_plugin_assets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "labels", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("catalog_plugin_version_id", sa.UUID(), nullable=False),
        sa.Column("asset_kind", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("digest", sa.String(length=71), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.CheckConstraint("size_bytes >= 0", name="ck_catalog_plugin_assets_size_bytes"),
        sa.ForeignKeyConstraint(["catalog_plugin_version_id"], ["catalog_plugin_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "catalog_plugin_version_id",
            "asset_kind",
            name="uq_catalog_plugin_assets_version_kind",
        ),
    )
    op.create_index(
        op.f("ix_catalog_plugin_assets_catalog_plugin_version_id"),
        "catalog_plugin_assets",
        ["catalog_plugin_version_id"],
        unique=False,
    )
    op.create_index(op.f("ix_catalog_plugin_assets_created_at"), "catalog_plugin_assets", ["created_at"], unique=False)
    op.create_index(op.f("ix_catalog_plugin_assets_id"), "catalog_plugin_assets", ["id"], unique=False)


def downgrade() -> None:
    """Remove catalog projections before their source configuration."""
    op.drop_index(op.f("ix_catalog_plugin_assets_id"), table_name="catalog_plugin_assets")
    op.drop_index(op.f("ix_catalog_plugin_assets_created_at"), table_name="catalog_plugin_assets")
    op.drop_index(op.f("ix_catalog_plugin_assets_catalog_plugin_version_id"), table_name="catalog_plugin_assets")
    op.drop_table("catalog_plugin_assets")
    op.drop_index("ix_catalog_plugin_versions_identity", table_name="catalog_plugin_versions")
    op.drop_index(op.f("ix_catalog_plugin_versions_reconciliation_id"), table_name="catalog_plugin_versions")
    op.drop_index(op.f("ix_catalog_plugin_versions_id"), table_name="catalog_plugin_versions")
    op.drop_index(op.f("ix_catalog_plugin_versions_created_at"), table_name="catalog_plugin_versions")
    op.drop_table("catalog_plugin_versions")
    op.drop_constraint("fk_plugin_sources_active_reconciliation", "plugin_sources", type_="foreignkey")
    op.drop_index("ix_plugin_source_reconciliations_source_created", table_name="plugin_source_reconciliations")
    op.drop_index(op.f("ix_plugin_source_reconciliations_plugin_source_id"), table_name="plugin_source_reconciliations")
    op.drop_index(op.f("ix_plugin_source_reconciliations_initiated_by"), table_name="plugin_source_reconciliations")
    op.drop_index(op.f("ix_plugin_source_reconciliations_id"), table_name="plugin_source_reconciliations")
    op.drop_index(op.f("ix_plugin_source_reconciliations_created_at"), table_name="plugin_source_reconciliations")
    op.drop_table("plugin_source_reconciliations")
    op.drop_index("ix_plugin_sources_enabled", table_name="plugin_sources")
    op.drop_index(op.f("ix_plugin_sources_id"), table_name="plugin_sources")
    op.drop_index(op.f("ix_plugin_sources_created_at"), table_name="plugin_sources")
    op.drop_table("plugin_sources")
