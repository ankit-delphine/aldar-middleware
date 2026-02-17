"""Add indexes for chat sessions query performance optimization

Revision ID: 0026
Revises: 0025
Create Date: 2026-01-10

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add performance indexes for chat sessions queries."""
    
    # Index on sessions.user_id for faster user session lookups
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_user_id 
        ON sessions(user_id)
    """)
    
    # Index on sessions.agent_id for faster agent filtering
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_agent_id 
        ON sessions(agent_id)
    """)
    
    # Index on sessions.last_message_interaction_at for faster date filtering
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_last_message_interaction 
        ON sessions(last_message_interaction_at DESC)
    """)
    
    # Index on sessions.updated_at for faster sorting
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_updated_at 
        ON sessions(updated_at DESC)
    """)
    
    # Composite index for user_id + last_message_interaction_at (most common query)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_user_last_interaction 
        ON sessions(user_id, last_message_interaction_at DESC)
    """)
    
    # Note: session_metadata is JSON type, not JSONB, so we cannot create GIN indexes on it
    # The B-tree indexes above on user_id, agent_id, and timestamps will provide
    # significant performance improvements for the most common queries
    
    # Index on messages for session_id and created_at (for last message lookups)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_session_created 
        ON messages(session_id, created_at DESC)
    """)
    
    # Index on messages.content_type for filtering system messages
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_content_type 
        ON messages(content_type)
    """)
    
    # Index on agents.public_id for UUID lookups
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_agents_public_id 
        ON agents(public_id)
    """)
    
    print("✅ Created performance indexes for chat sessions queries")


def downgrade() -> None:
    """Remove performance indexes."""
    
    op.execute("DROP INDEX IF EXISTS idx_sessions_user_id")
    op.execute("DROP INDEX IF EXISTS idx_sessions_agent_id")
    op.execute("DROP INDEX IF EXISTS idx_sessions_last_message_interaction")
    op.execute("DROP INDEX IF EXISTS idx_sessions_updated_at")
    op.execute("DROP INDEX IF EXISTS idx_sessions_user_last_interaction")
    op.execute("DROP INDEX IF EXISTS idx_messages_session_created")
    op.execute("DROP INDEX IF EXISTS idx_messages_content_type")
    op.execute("DROP INDEX IF EXISTS idx_agents_public_id")
    
    print("✅ Removed performance indexes for chat sessions queries")
