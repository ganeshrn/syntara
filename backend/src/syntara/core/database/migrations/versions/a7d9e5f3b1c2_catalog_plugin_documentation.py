"""catalog plugin documentation

Store only sanitized rendered documentation and non-secret verification evidence
for immutable catalog plugin assets. Raw publisher Markdown remains in the
verified transient artifact boundary and is never persisted by Syntara.

Revision ID: a7d9e5f3b1c2
Revises: f4a9c6b2e1d7
Create Date: 2026-10-07 15:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a7d9e5f3b1c2"
down_revision: str | Sequence[str] | None = "f4a9c6b2e1d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add safe rendered documentation and verification evidence to catalog assets."""
    op.add_column("catalog_plugin_assets", sa.Column("rendered_html", sa.Text(), nullable=True))
    op.add_column(
        "catalog_plugin_assets",
        sa.Column(
            "verification_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_catalog_plugin_assets_documentation_rendered",
        "catalog_plugin_assets",
        "(asset_kind = 'documentation' AND media_type = 'text/markdown' AND rendered_html IS NOT NULL) "
        "OR (asset_kind <> 'documentation' AND rendered_html IS NULL)",
    )


def downgrade() -> None:
    """Remove the derived catalog documentation representation."""
    op.drop_constraint(
        "ck_catalog_plugin_assets_documentation_rendered",
        "catalog_plugin_assets",
        type_="check",
    )
    op.drop_column("catalog_plugin_assets", "verification_summary")
    op.drop_column("catalog_plugin_assets", "rendered_html")
