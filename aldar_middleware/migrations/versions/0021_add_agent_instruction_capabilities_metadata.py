"""Add instruction, capabilities, history toggle and metadata to agents table

Revision ID: 0021
Revises: 0020
Create Date: 2025-12-18

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add instruction, agent_capabilities, add_history_to_context, and agent_metadata columns to agents table."""
    
    # Add instruction column (Text, nullable)
    op.add_column('agents', sa.Column('instruction', sa.Text(), nullable=True))
    
    # Add agent_capabilities column (Text, nullable)
    op.add_column('agents', sa.Column('agent_capabilities', sa.Text(), nullable=True))
    
    # Add add_history_to_context column (Boolean, default False)
    op.add_column('agents', sa.Column('add_history_to_context', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    
    # Add agent_metadata column (JSON, nullable)
    op.add_column('agents', sa.Column('agent_metadata', postgresql.JSON(), nullable=True))
    
    # Data migration: Copy existing instruction values from agent_configuration to agents table
    op.execute("""
        UPDATE agents
        SET instruction = ac.values->>'instruction'
        FROM agent_configuration ac
        WHERE ac.agent_id = agents.id
        AND ac.configuration_name = 'instruction'
        AND ac.values IS NOT NULL
        AND ac.values->>'instruction' IS NOT NULL
    """)
    
    # Cleanup: Remove old instruction entries from agent_configuration
    # since instruction is now stored in agents table
    op.execute("""
        DELETE FROM agent_configuration
        WHERE configuration_name = 'instruction'
    """)


def downgrade() -> None:
    """Remove instruction, agent_capabilities, add_history_to_context, and agent_metadata columns from agents table.
    
    WARNING: This will permanently delete all data in these columns.
    instruction data will NOT be restored to agent_configuration table.
    Ensure you have a database backup before running this downgrade.
    """
    
    op.drop_column('agents', 'agent_metadata')
    op.drop_column('agents', 'add_history_to_context')
    op.drop_column('agents', 'agent_capabilities')
    op.drop_column('agents', 'instruction')
