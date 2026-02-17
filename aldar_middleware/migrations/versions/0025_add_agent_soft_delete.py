"""Add soft delete column to agents table.

Revision ID: 0025
Revises: 0024
Create Date: 2025-12-31
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add is_deleted column to agents table for soft delete functionality."""
    
    # Check if column already exists
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'agents' AND column_name = 'is_deleted'
    """))
    column_exists = result.fetchone() is not None
    
    if not column_exists:
        # Add is_deleted column with default False
        op.add_column(
            'agents',
            sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='false')
        )
        
        # Create index for is_deleted for better query performance
        op.create_index(
            op.f('ix_agents_is_deleted'),
            'agents',
            ['is_deleted'],
            unique=False
        )


def downgrade() -> None:
    """Remove is_deleted column from agents table."""
    
    # Drop index first
    op.drop_index(
        op.f('ix_agents_is_deleted'),
        table_name='agents'
    )
    
    # Drop column
    op.drop_column('agents', 'is_deleted')
