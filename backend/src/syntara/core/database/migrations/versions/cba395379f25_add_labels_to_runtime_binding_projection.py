"""add labels to runtime binding projection

Revision ID: cba395379f25
Revises: f7c2b8d1a604
Create Date: 2026-11-03 20:14:25.134962

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "cba395379f25"
down_revision: str | Sequence[str] | None = "f7c2b8d1a604"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the common resource labels column omitted by the initial migration."""
    op.add_column(
        "installed_plugin_runtime_bindings",
        sa.Column(
            "labels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Remove the common resource labels column."""
    op.drop_column("installed_plugin_runtime_bindings", "labels")
