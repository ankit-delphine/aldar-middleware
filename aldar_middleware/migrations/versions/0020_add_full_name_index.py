"""Add index on users.full_name for faster sorting

Revision ID: 0020
Revises: 0019
Create Date: 2025-12-17

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add index on users.full_name for faster sorting in admin users endpoint."""
    
    # Create index on full_name for faster sorting
    op.create_index(
        "ix_users_full_name",
        "users",
        ["full_name"],
        unique=False
    )


def downgrade() -> None:
    """Drop index on users.full_name."""
    
    op.drop_index("ix_users_full_name", table_name="users")
