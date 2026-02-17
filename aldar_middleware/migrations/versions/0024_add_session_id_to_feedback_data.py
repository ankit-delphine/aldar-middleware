"""Add session_id column to feedback_data table.

Revision ID: 0024
Revises: 0023
Create Date: 2025-12-29
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add session_id column to feedback_data table."""
    
    # Check if column already exists
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'feedback_data' AND column_name = 'session_id'
    """))
    column_exists = result.fetchone() is not None
    
    if not column_exists:
        # Add session_id column
        op.add_column(
            'feedback_data',
            sa.Column('session_id', sa.String(length=255), nullable=True)
        )
        
        # Create index for session_id
        op.create_index(
            op.f('ix_feedback_data_session_id'),
            'feedback_data',
            ['session_id'],
            unique=False
        )


def downgrade() -> None:
    """Remove session_id column from feedback_data table."""
    
    # Drop index first
    op.drop_index(
        op.f('ix_feedback_data_session_id'),
        table_name='feedback_data'
    )
    
    # Drop column
    op.drop_column('feedback_data', 'session_id')
