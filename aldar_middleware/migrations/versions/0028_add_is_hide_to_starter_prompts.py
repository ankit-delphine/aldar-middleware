"""add_is_hide_to_starter_prompts

Revision ID: 0028
Revises: 0027
Create Date: 2026-02-13

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add is_hide column to starter_prompts."""
    op.add_column(
        "starter_prompts",
        sa.Column("is_hide", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    """Remove is_hide column from starter_prompts."""
    op.drop_column("starter_prompts", "is_hide")
