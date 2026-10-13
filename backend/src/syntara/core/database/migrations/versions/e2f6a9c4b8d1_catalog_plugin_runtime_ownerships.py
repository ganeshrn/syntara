"""represent catalog plugin runtime ownership as a capability list

Revision ID: e2f6a9c4b8d1
Revises: c1e4d8a9f2b6
Create Date: 2026-10-11 23:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e2f6a9c4b8d1"
down_revision: str | Sequence[str] | None = "c1e4d8a9f2b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME_OWNERSHIPS_CHECK = """runtime_ownerships IN (
    '[\"platform-runner\"]'::jsonb,
    '[\"custom-workload\"]'::jsonb,
    '[\"custom-workload\", \"platform-runner\"]'::jsonb
)"""


def upgrade() -> None:
    """Replace the single runtime value with the canonical ownership list."""
    op.drop_constraint(
        "ck_catalog_plugin_versions_runtime_ownership",
        "catalog_plugin_versions",
        type_="check",
    )
    op.alter_column(
        "catalog_plugin_versions",
        "runtime_ownership",
        new_column_name="runtime_ownerships",
        existing_type=sa.String(length=32),
    )
    op.alter_column(
        "catalog_plugin_versions",
        "runtime_ownerships",
        existing_type=sa.String(length=32),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using="jsonb_build_array(runtime_ownerships)",
    )
    op.create_check_constraint(
        "ck_catalog_plugin_versions_runtime_ownerships",
        "catalog_plugin_versions",
        _RUNTIME_OWNERSHIPS_CHECK,
    )


def downgrade() -> None:
    """Restore the historical scalar runtime column for a database rollback."""
    op.drop_constraint(
        "ck_catalog_plugin_versions_runtime_ownerships",
        "catalog_plugin_versions",
        type_="check",
    )
    op.alter_column(
        "catalog_plugin_versions",
        "runtime_ownerships",
        type_=sa.String(length=32),
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using="runtime_ownerships ->> 0",
    )
    op.alter_column(
        "catalog_plugin_versions",
        "runtime_ownerships",
        new_column_name="runtime_ownership",
        existing_type=sa.String(length=32),
    )
    op.create_check_constraint(
        "ck_catalog_plugin_versions_runtime_ownership",
        "catalog_plugin_versions",
        "runtime_ownership IN ('platform-runner', 'custom-workload')",
    )
