"""Add external_id, company, is_onboarded, first_logged_in_at, is_custom_query_enabled to users table, 2.0 fields to sessions table, message_id to attachments table, and 2.0 fields to messages table

Revision ID: 0016
Revises: 0015
Create Date: 2025-11-27 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add external_id, company, is_onboarded, first_logged_in_at, and is_custom_query_enabled columns to users table.
    Also add 2.0 migration fields to sessions table, message_id to attachments table, and 2.0 fields to messages table."""
    
    # ===== USERS TABLE =====
    # Add external_id column
    op.add_column('users', sa.Column('external_id', sa.String(255), nullable=True))
    op.create_index(op.f('ix_users_external_id'), 'users', ['external_id'], unique=False)
    
    # Add company column
    op.add_column('users', sa.Column('company', sa.String(255), nullable=True))
    
    # Add is_onboarded column with default False
    op.add_column('users', sa.Column('is_onboarded', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    
    # Add first_logged_in_at column
    op.add_column('users', sa.Column('first_logged_in_at', sa.DateTime(), nullable=True))
    
    # Add is_custom_query_enabled column with default False
    op.add_column('users', sa.Column('is_custom_query_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    
    # ===== SESSIONS TABLE (2.0 Migration Fields) =====
    # Add graph_id column
    op.add_column('sessions', sa.Column('graph_id', sa.String(length=255), nullable=True))
    op.create_index(op.f('ix_sessions_graph_id'), 'sessions', ['graph_id'], unique=False)
    
    # Add deleted_at column for soft delete
    op.add_column('sessions', sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.create_index(op.f('ix_sessions_deleted_at'), 'sessions', ['deleted_at'], unique=False)
    
    # Add is_favorite column (migrated from session_metadata)
    op.add_column('sessions', sa.Column('is_favorite', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.create_index(op.f('ix_sessions_is_favorite'), 'sessions', ['is_favorite'], unique=False)
    
    # Add last_message_interaction_at column
    op.add_column('sessions', sa.Column('last_message_interaction_at', sa.DateTime(), nullable=True))
    op.create_index(op.f('ix_sessions_last_message_interaction_at'), 'sessions', ['last_message_interaction_at'], unique=False)
    
    # Add meeting_id column
    op.add_column('sessions', sa.Column('meeting_id', sa.String(length=255), nullable=True))
    op.create_index(op.f('ix_sessions_meeting_id'), 'sessions', ['meeting_id'], unique=False)
    
    # Add document_knowledge_agent_id column (FK to agents)
    op.add_column('sessions', sa.Column('document_knowledge_agent_id', sa.BigInteger(), nullable=True))
    op.create_index(op.f('ix_sessions_document_knowledge_agent_id'), 'sessions', ['document_knowledge_agent_id'], unique=False)
    op.create_foreign_key(
        op.f('fk_sessions_document_knowledge_agent_id_agents'),
        'sessions', 'agents',
        ['document_knowledge_agent_id'], ['id']
    )
    
    # Add document_my_agent_id column (FK to agents)
    op.add_column('sessions', sa.Column('document_my_agent_id', sa.BigInteger(), nullable=True))
    op.create_index(op.f('ix_sessions_document_my_agent_id'), 'sessions', ['document_my_agent_id'], unique=False)
    op.create_foreign_key(
        op.f('fk_sessions_document_my_agent_id_agents'),
        'sessions', 'agents',
        ['document_my_agent_id'], ['id']
    )
    
    # ===== ATTACHMENTS TABLE (2.0 Migration Field) =====
    # Add message_id column (FK to messages)
    op.add_column('attachments', sa.Column('message_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(op.f('ix_attachments_message_id'), 'attachments', ['message_id'], unique=False)
    op.create_foreign_key(
        op.f('fk_attachments_message_id_messages'),
        'attachments', 'messages',
        ['message_id'], ['id']
    )
    
    # ===== MESSAGES TABLE (2.0 Migration Fields) =====
    # Add document_my_agent_id column (FK to agents)
    op.add_column('messages', sa.Column('document_my_agent_id', sa.BigInteger(), nullable=True))
    op.create_index(op.f('ix_messages_document_my_agent_id'), 'messages', ['document_my_agent_id'], unique=False)
    op.create_foreign_key(
        op.f('fk_messages_document_my_agent_id_agents'),
        'messages', 'agents',
        ['document_my_agent_id'], ['id']
    )
    
    # Add document_knowledge_agent_id column (FK to agents)
    op.add_column('messages', sa.Column('document_knowledge_agent_id', sa.BigInteger(), nullable=True))
    op.create_index(op.f('ix_messages_document_knowledge_agent_id'), 'messages', ['document_knowledge_agent_id'], unique=False)
    op.create_foreign_key(
        op.f('fk_messages_document_knowledge_agent_id_agents'),
        'messages', 'agents',
        ['document_knowledge_agent_id'], ['id']
    )
    
    # Add boolean flags
    op.add_column('messages', sa.Column('is_reply', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('messages', sa.Column('is_refreshed', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('messages', sa.Column('is_sent_directly_to_openai', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('messages', sa.Column('is_internet_search_used', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('messages', sa.Column('has_found_information', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    
    # Add string/text fields
    op.add_column('messages', sa.Column('result_code', sa.String(length=50), nullable=True))
    op.add_column('messages', sa.Column('result_note', sa.Text(), nullable=True))
    op.add_column('messages', sa.Column('message_type', sa.String(length=50), nullable=True))
    op.add_column('messages', sa.Column('selected_agent_type', sa.String(length=50), nullable=True))
    
    # Add datetime fields
    op.add_column('messages', sa.Column('sent_at', sa.DateTime(), nullable=True))
    op.create_index(op.f('ix_messages_sent_at'), 'messages', ['sent_at'], unique=False)
    op.add_column('messages', sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.create_index(op.f('ix_messages_deleted_at'), 'messages', ['deleted_at'], unique=False)
    
    # Add message_metadata JSON column for custom query fields
    op.add_column('messages', sa.Column('message_metadata', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove external_id, company, is_onboarded, first_logged_in_at, and is_custom_query_enabled columns from users table.
    Also remove 2.0 migration fields from sessions table, message_id from attachments table, and 2.0 fields from messages table."""
    
    # ===== SESSIONS TABLE =====
    op.drop_constraint(op.f('fk_sessions_document_my_agent_id_agents'), 'sessions', type_='foreignkey')
    op.drop_index(op.f('ix_sessions_document_my_agent_id'), table_name='sessions')
    op.drop_column('sessions', 'document_my_agent_id')
    
    op.drop_constraint(op.f('fk_sessions_document_knowledge_agent_id_agents'), 'sessions', type_='foreignkey')
    op.drop_index(op.f('ix_sessions_document_knowledge_agent_id'), table_name='sessions')
    op.drop_column('sessions', 'document_knowledge_agent_id')
    
    op.drop_index(op.f('ix_sessions_meeting_id'), table_name='sessions')
    op.drop_column('sessions', 'meeting_id')
    
    op.drop_index(op.f('ix_sessions_last_message_interaction_at'), table_name='sessions')
    op.drop_column('sessions', 'last_message_interaction_at')
    
    op.drop_index(op.f('ix_sessions_is_favorite'), table_name='sessions')
    op.drop_column('sessions', 'is_favorite')
    
    op.drop_index(op.f('ix_sessions_deleted_at'), table_name='sessions')
    op.drop_column('sessions', 'deleted_at')
    
    op.drop_index(op.f('ix_sessions_graph_id'), table_name='sessions')
    op.drop_column('sessions', 'graph_id')
    
    # ===== MESSAGES TABLE =====
    op.drop_column('messages', 'message_metadata')
    op.drop_index(op.f('ix_messages_deleted_at'), table_name='messages')
    op.drop_column('messages', 'deleted_at')
    op.drop_index(op.f('ix_messages_sent_at'), table_name='messages')
    op.drop_column('messages', 'sent_at')
    op.drop_column('messages', 'selected_agent_type')
    op.drop_column('messages', 'message_type')
    op.drop_column('messages', 'result_note')
    op.drop_column('messages', 'result_code')
    op.drop_column('messages', 'has_found_information')
    op.drop_column('messages', 'is_internet_search_used')
    op.drop_column('messages', 'is_sent_directly_to_openai')
    op.drop_column('messages', 'is_refreshed')
    op.drop_column('messages', 'is_reply')
    op.drop_constraint(op.f('fk_messages_document_knowledge_agent_id_agents'), 'messages', type_='foreignkey')
    op.drop_index(op.f('ix_messages_document_knowledge_agent_id'), table_name='messages')
    op.drop_column('messages', 'document_knowledge_agent_id')
    op.drop_constraint(op.f('fk_messages_document_my_agent_id_agents'), 'messages', type_='foreignkey')
    op.drop_index(op.f('ix_messages_document_my_agent_id'), table_name='messages')
    op.drop_column('messages', 'document_my_agent_id')
    
    # ===== ATTACHMENTS TABLE =====
    op.drop_constraint(op.f('fk_attachments_message_id_messages'), 'attachments', type_='foreignkey')
    op.drop_index(op.f('ix_attachments_message_id'), table_name='attachments')
    op.drop_column('attachments', 'message_id')
    
    # ===== USERS TABLE =====
    op.drop_index(op.f('ix_users_external_id'), table_name='users')
    op.drop_column('users', 'is_custom_query_enabled')
    op.drop_column('users', 'first_logged_in_at')
    op.drop_column('users', 'is_onboarded')
    op.drop_column('users', 'company')
    op.drop_column('users', 'external_id')

