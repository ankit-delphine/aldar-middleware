"""Add agent_configuration, agent_tags, and attachments tables

Revision ID: 0011
Revises: 0010
Create Date: 2025-11-05 16:00:00.000000

This migration adds:
- agent_configuration table for storing agent custom configurations (instruction, route_through_orchestrator, custom_feature_toggle, custom_feature_dropdown)
- agent_tags table for storing agent categories/tags
- attachments table for generic file uploads (reusable across chat, agents, feedback, etc.)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create agent_configuration and agent_tags tables."""
    # agent_configuration table
    op.create_table(
        'agent_configuration',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('agent_id', sa.BigInteger(), nullable=False),
        sa.Column('configuration_name', sa.String(100), nullable=False),
        sa.Column('type', sa.String(50), nullable=False),  # boolean, string, number, array, object
        sa.Column('values', postgresql.JSON(), nullable=True),  # values of the configuration
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_agent_configuration_agent_id', 'agent_configuration', ['agent_id'], unique=False)
    op.create_index('ix_agent_configuration_configuration_name', 'agent_configuration', ['configuration_name'], unique=False)
    op.create_index('ix_agent_configuration_agent_id_configuration_name', 'agent_configuration', ['agent_id', 'configuration_name'], unique=True)

    # agent_tags table
    op.create_table(
        'agent_tags',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('agent_id', sa.BigInteger(), nullable=False),
        sa.Column('tag', sa.String(100), nullable=False),
        sa.Column('tag_type', sa.String(50), nullable=True),  # category, skill, domain, etc.
        sa.Column('description', sa.Text(), nullable=True),  # Optional description of the tag
        sa.Column('color', sa.String(20), nullable=True),  # Optional color for UI display
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_agent_tags_agent_id', 'agent_tags', ['agent_id'], unique=False)
    op.create_index('ix_agent_tags_tag', 'agent_tags', ['tag'], unique=False)
    op.create_index('ix_agent_tags_agent_id_tag', 'agent_tags', ['agent_id', 'tag'], unique=True)

    # attachments table
    op.create_table(
        'attachments',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('file_name', sa.String(255), nullable=False),
        sa.Column('file_size', sa.BigInteger(), nullable=False),
        sa.Column('content_type', sa.String(100), nullable=True),
        sa.Column('blob_url', sa.Text(), nullable=False),  # Full blob URL with SAS token
        sa.Column('blob_name', sa.String(500), nullable=False),  # Azure blob path
        sa.Column('entity_type', sa.String(50), nullable=True),  # 'agent', 'chat', 'feedback', etc.
        sa.Column('entity_id', sa.String(255), nullable=True),  # Reference to entity
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_attachments_user_id', 'attachments', ['user_id'], unique=False)
    op.create_index('ix_attachments_entity_id', 'attachments', ['entity_id'], unique=False)
    op.create_index('ix_attachments_entity_type_entity_id', 'attachments', ['entity_type', 'entity_id'], unique=False)


def downgrade() -> None:
    """Drop agent_configuration, agent_tags, and attachments tables."""
    op.drop_index('ix_attachments_entity_type_entity_id', table_name='attachments')
    op.drop_index('ix_attachments_entity_id', table_name='attachments')
    op.drop_index('ix_attachments_user_id', table_name='attachments')
    op.drop_table('attachments')
    
    op.drop_index('ix_agent_tags_agent_id_tag', table_name='agent_tags')
    op.drop_index('ix_agent_tags_tag', table_name='agent_tags')
    op.drop_index('ix_agent_tags_agent_id', table_name='agent_tags')
    op.drop_table('agent_tags')
    
    op.drop_index('ix_agent_configuration_agent_id_configuration_name', table_name='agent_configuration')
    op.drop_index('ix_agent_configuration_configuration_name', table_name='agent_configuration')
    op.drop_index('ix_agent_configuration_agent_id', table_name='agent_configuration')
    op.drop_table('agent_configuration')

