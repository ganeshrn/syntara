"""explicit local-PoC catalog-source profile

Persist a source profile so the intentionally unsafe loopback development
route is visible and cannot be confused with the standard HTTPS/signed route.

Revision ID: c3e8a4b7d2f6
Revises: a7d9e5f3b1c2
Create Date: 2026-10-07 16:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3e8a4b7d2f6"
down_revision: str | Sequence[str] | None = "a7d9e5f3b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add an explicit source profile with the safe standard default."""
    op.add_column(
        "plugin_sources",
        sa.Column("source_mode", sa.String(length=16), server_default=sa.text("'standard'"), nullable=False),
    )
    op.create_check_constraint(
        "ck_plugin_sources_source_mode",
        "plugin_sources",
        "source_mode IN ('standard', 'local-poc')",
    )


def downgrade() -> None:
    """Remove the local-PoC profile marker."""
    op.drop_constraint("ck_plugin_sources_source_mode", "plugin_sources", type_="check")
    op.drop_column("plugin_sources", "source_mode")
