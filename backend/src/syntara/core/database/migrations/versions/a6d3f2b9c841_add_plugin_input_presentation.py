"""add optional input presentation to installed plugin actions

Revision ID: a6d3f2b9c841
Revises: e2f6a9c4b8d1
Create Date: 2026-10-27 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a6d3f2b9c841"
down_revision: str | Sequence[str] | None = "e2f6a9c4b8d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist an optional renderer-versioned declarative input presentation."""
    op.add_column(
        "installed_plugin_actions",
        sa.Column(
            "input_presentation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Remove installed action input presentation metadata."""
    op.drop_column("installed_plugin_actions", "input_presentation")
