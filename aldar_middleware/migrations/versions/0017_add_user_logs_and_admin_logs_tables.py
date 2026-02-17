"""add_user_logs_and_admin_logs_tables

Revision ID: 0017
Revises: 0016
Create Date: 2024-12-20

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create user_logs table
    op.create_table(
        "user_logs",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("action_type", sa.String(100), nullable=False),  # e.g., USER_CONVERSATION_CREATED, USER_MESSAGE_CREATED
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("correlation_id", sa.String(255), nullable=True),
        sa.Column("log_data", postgresql.JSONB, nullable=False),
    )
    
    # Create indexes for user_logs
    op.create_index("idx_user_logs_timestamp", "user_logs", ["timestamp"])
    op.create_index("idx_user_logs_action_type", "user_logs", ["action_type"])
    op.create_index("idx_user_logs_user_id", "user_logs", ["user_id"])
    op.create_index("idx_user_logs_email", "user_logs", ["email"])
    op.create_index("idx_user_logs_correlation_id", "user_logs", ["correlation_id"])
    op.create_index("idx_user_logs_created_at", "user_logs", ["created_at"])
    op.create_index("idx_user_logs_user_timestamp", "user_logs", ["user_id", "timestamp"])
    op.create_index("idx_user_logs_action_timestamp", "user_logs", ["action_type", "timestamp"])
    op.create_index("idx_user_logs_log_data_gin", "user_logs", ["log_data"], postgresql_using="gin")
    
    # Create admin_logs table
    op.create_table(
        "admin_logs",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column("action_type", sa.String(100), nullable=True),  # e.g., USERS_LOGS_EXPORTED, KNOWLEDGE_AGENT_UPDATED
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("correlation_id", sa.String(255), nullable=True),
        sa.Column("module", sa.String(255), nullable=True),
        sa.Column("function", sa.String(255), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("log_data", postgresql.JSONB, nullable=False),
    )
    
    # Create indexes for admin_logs
    op.create_index("idx_admin_logs_timestamp", "admin_logs", ["timestamp"])
    op.create_index("idx_admin_logs_level", "admin_logs", ["level"])
    op.create_index("idx_admin_logs_action_type", "admin_logs", ["action_type"])
    op.create_index("idx_admin_logs_user_id", "admin_logs", ["user_id"])
    op.create_index("idx_admin_logs_email", "admin_logs", ["email"])
    op.create_index("idx_admin_logs_correlation_id", "admin_logs", ["correlation_id"])
    op.create_index("idx_admin_logs_module", "admin_logs", ["module"])
    op.create_index("idx_admin_logs_level_timestamp", "admin_logs", ["level", "timestamp"])
    op.create_index("idx_admin_logs_action_timestamp", "admin_logs", ["action_type", "timestamp"])
    op.create_index("idx_admin_logs_user_timestamp", "admin_logs", ["user_id", "timestamp"])
    op.create_index("idx_admin_logs_log_data_gin", "admin_logs", ["log_data"], postgresql_using="gin")
    
    # Note: GIN index for message text search requires pg_trgm extension
    # This can be added manually if needed: CREATE EXTENSION IF NOT EXISTS pg_trgm;
    # op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    # op.create_index("idx_admin_logs_message_gin", "admin_logs", ["message"], postgresql_using="gin", postgresql_ops={"message": "gin_trgm_ops"})


def downgrade() -> None:
    # Drop indexes first
    op.drop_index("idx_admin_logs_log_data_gin", table_name="admin_logs")
    op.drop_index("idx_admin_logs_user_timestamp", table_name="admin_logs")
    op.drop_index("idx_admin_logs_action_timestamp", table_name="admin_logs")
    op.drop_index("idx_admin_logs_level_timestamp", table_name="admin_logs")
    op.drop_index("idx_admin_logs_module", table_name="admin_logs")
    op.drop_index("idx_admin_logs_correlation_id", table_name="admin_logs")
    op.drop_index("idx_admin_logs_email", table_name="admin_logs")
    op.drop_index("idx_admin_logs_user_id", table_name="admin_logs")
    op.drop_index("idx_admin_logs_action_type", table_name="admin_logs")
    op.drop_index("idx_admin_logs_level", table_name="admin_logs")
    op.drop_index("idx_admin_logs_timestamp", table_name="admin_logs")
    
    op.drop_table("admin_logs")
    
    op.drop_index("idx_user_logs_log_data_gin", table_name="user_logs")
    op.drop_index("idx_user_logs_action_timestamp", table_name="user_logs")
    op.drop_index("idx_user_logs_user_timestamp", table_name="user_logs")
    op.drop_index("idx_user_logs_created_at", table_name="user_logs")
    op.drop_index("idx_user_logs_correlation_id", table_name="user_logs")
    op.drop_index("idx_user_logs_email", table_name="user_logs")
    op.drop_index("idx_user_logs_user_id", table_name="user_logs")
    op.drop_index("idx_user_logs_action_type", table_name="user_logs")
    op.drop_index("idx_user_logs_timestamp", table_name="user_logs")
    
    op.drop_table("user_logs")

