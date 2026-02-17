"""add message to feedback_entity_type enum

Revision ID: 0013
Revises: 0012
Create Date: 2025-11-12 15:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add 'message' value to feedback_entity_type enum."""
    # Add 'message' to the existing enum
    op.execute("ALTER TYPE feedback_entity_type ADD VALUE IF NOT EXISTS 'message'")


def downgrade() -> None:
    """Remove 'message' value from feedback_entity_type enum.
    
    Note: PostgreSQL does not support removing enum values directly.
    This would require recreating the enum type, which is complex and risky.
    For safety, this downgrade is a no-op.
    """
    # PostgreSQL doesn't support removing enum values easily
    # This would require recreating the enum, which is risky
    # So we leave it as a no-op for safety
    pass

