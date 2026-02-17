"""Add include_in_teams and agent_header to agents table

Revision ID: 0018
Revises: 0017
Create Date: 2025-01-XX

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add include_in_teams and agent_header columns to agents table."""
    
    # Add include_in_teams column with default False
    op.add_column('agents', sa.Column('include_in_teams', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    
    # Add agent_header column (nullable Text for JSON headers)
    op.add_column('agents', sa.Column('agent_header', sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove include_in_teams and agent_header columns from agents table."""
    
    op.drop_column('agents', 'agent_header')
    op.drop_column('agents', 'include_in_teams')
