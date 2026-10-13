"""add plugin connection configuration and encrypted credential reference

Revision ID: c1e4d8a9f2b6
Revises: d1b0d925adbe
Create Date: 2026-10-08 08:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c1e4d8a9f2b6"
down_revision: str | Sequence[str] | None = "d1b0d925adbe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add non-secret configuration and opaque encrypted-secret references."""
    op.add_column(
        "plugin_connections",
        sa.Column(
            "configuration",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("plugin_connections", sa.Column("credential_type_revision_id", sa.UUID(), nullable=True))
    op.add_column("plugin_connections", sa.Column("secret_id", sa.UUID(), nullable=True))
    op.create_index(
        op.f("ix_plugin_connections_credential_type_revision_id"),
        "plugin_connections",
        ["credential_type_revision_id"],
        unique=False,
    )
    op.create_index(op.f("ix_plugin_connections_secret_id"), "plugin_connections", ["secret_id"], unique=False)
    op.create_foreign_key(
        "fk_plugin_connections_credential_type_revision",
        "plugin_connections",
        "installed_plugin_credential_types",
        ["credential_type_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_plugin_connections_secret",
        "plugin_connections",
        "secrets",
        ["secret_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Remove the connection setup fields."""
    op.drop_constraint("fk_plugin_connections_secret", "plugin_connections", type_="foreignkey")
    op.drop_constraint("fk_plugin_connections_credential_type_revision", "plugin_connections", type_="foreignkey")
    op.drop_index(op.f("ix_plugin_connections_secret_id"), table_name="plugin_connections")
    op.drop_index(op.f("ix_plugin_connections_credential_type_revision_id"), table_name="plugin_connections")
    op.drop_column("plugin_connections", "secret_id")
    op.drop_column("plugin_connections", "credential_type_revision_id")
    op.drop_column("plugin_connections", "configuration")
