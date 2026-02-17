"""add sessions tables

Revision ID: 0012
Revises: 0011
Create Date: 2025-11-07 16:51:53.358707

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create session-centric tables and related resources."""

    op.create_table(
        "metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metrics")),
    )
    op.create_index(op.f("ix_metrics_name"), "metrics", ["name"], unique=False)
    op.create_index(op.f("ix_metrics_timestamp"), "metrics", ["timestamp"], unique=False)

    op.create_table(
        "agent_tools",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("tool_description", sa.Text(), nullable=True),
        sa.Column("tool_url", sa.String(length=500), nullable=True),
        sa.Column("tool_icon", sa.String(length=500), nullable=True),
        sa.Column("tool_color", sa.String(length=20), nullable=True),
        sa.Column("tool_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tool_is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_agent_tools_agent_id_agents")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_tools")),
    )
    op.create_index(op.f("ix_agent_tools_agent_id"), "agent_tools", ["agent_id"], unique=False)
    op.create_index(op.f("ix_agent_tools_tool_name"), "agent_tools", ["tool_name"], unique=False)

    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("session_name", sa.String(length=255), nullable=True),
        sa.Column("session_state", sa.JSON(), nullable=True),
        sa.Column("session_data", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=50), server_default=sa.text("'active'"), nullable=False),
        sa.Column("workflow_id", sa.String(length=255), nullable=True),
        sa.Column("session_metadata", sa.JSON(), nullable=True),
        sa.Column("session_type", sa.String(length=50), server_default=sa.text("'chat'"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_sessions_agent_id_agents")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("public_id", name=op.f("uq_sessions_public_id")),
    )
    op.create_index(op.f("ix_sessions_agent_id"), "sessions", ["agent_id"], unique=False)
    op.create_index(op.f("ix_sessions_created_at"), "sessions", ["created_at"], unique=False)
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], unique=False)
    op.create_index(op.f("ix_sessions_workflow_id"), "sessions", ["workflow_id"], unique=False)

    op.create_table(
        "user_agent_access",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("access_level", sa.String(length=50), server_default=sa.text("'read'"), nullable=False),
        sa.Column("granted_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("granted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("access_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_user_agent_access_agent_id_agents")),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"], name=op.f("fk_user_agent_access_granted_by_users")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_user_agent_access_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_agent_access")),
    )
    op.create_index(op.f("ix_user_agent_access_agent_id"), "user_agent_access", ["agent_id"], unique=False)
    op.create_index(op.f("ix_user_agent_access_user_id"), "user_agent_access", ["user_id"], unique=False)

    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_name", sa.String(length=255), nullable=True),
        sa.Column("workflow_id", sa.String(length=255), nullable=True),
        sa.Column("workflow_step_id", sa.String(length=255), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("content_type", sa.String(length=50), server_default=sa.text("'text'"), nullable=False),
        sa.Column("reasoning_content", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=50), server_default=sa.text("'running'"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("execution_time_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_agent_runs_agent_id_agents")),
        sa.ForeignKeyConstraint(["parent_run_id"], ["agent_runs.id"], name=op.f("fk_agent_runs_parent_run_id_agent_runs")),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name=op.f("fk_agent_runs_session_id_sessions")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_agent_runs_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
        sa.UniqueConstraint("public_id", name=op.f("uq_agent_runs_public_id")),
        sa.UniqueConstraint("run_id", name=op.f("uq_agent_runs_run_id")),
    )
    op.create_index(op.f("ix_agent_runs_agent_id"), "agent_runs", ["agent_id"], unique=False)
    op.create_index(op.f("ix_agent_runs_created_at"), "agent_runs", ["created_at"], unique=False)
    op.create_index(op.f("ix_agent_runs_session_id"), "agent_runs", ["session_id"], unique=False)
    op.create_index(op.f("ix_agent_runs_user_id"), "agent_runs", ["user_id"], unique=False)
    op.create_index(op.f("ix_agent_runs_workflow_id"), "agent_runs", ["workflow_id"], unique=False)

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("content_type", sa.String(length=50), server_default=sa.text("'text'"), nullable=False),
        sa.Column("images", sa.JSON(), nullable=True),
        sa.Column("videos", sa.JSON(), nullable=True),
        sa.Column("audio", sa.JSON(), nullable=True),
        sa.Column("files", sa.JSON(), nullable=True),
        sa.Column("tool_calls", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_messages_agent_id_agents")),
        sa.ForeignKeyConstraint(["parent_message_id"], ["messages.id"], name=op.f("fk_messages_parent_message_id_messages")),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name=op.f("fk_messages_session_id_sessions")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_messages_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint("public_id", name=op.f("uq_messages_public_id")),
    )
    op.create_index(op.f("ix_messages_agent_id"), "messages", ["agent_id"], unique=False)
    op.create_index(op.f("ix_messages_created_at"), "messages", ["created_at"], unique=False)
    op.create_index(op.f("ix_messages_session_id"), "messages", ["session_id"], unique=False)
    op.create_index(op.f("ix_messages_user_id"), "messages", ["user_id"], unique=False)

    op.create_table(
        "agent_usage_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("currency", sa.String(length=10), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column("total_request", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_error", sa.Integer(), server_default="0", nullable=False),
        sa.Column("average_response_time", sa.Float(), nullable=True),
        sa.Column("success_time", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_agent_usage_metrics_agent_id_agents")),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], name=op.f("fk_agent_usage_metrics_agent_run_id_agent_runs")),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], name=op.f("fk_agent_usage_metrics_message_id_messages")),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name=op.f("fk_agent_usage_metrics_session_id_sessions")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_agent_usage_metrics_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_usage_metrics")),
        sa.UniqueConstraint("public_id", name=op.f("uq_agent_usage_metrics_public_id")),
    )
    op.create_index(op.f("ix_agent_usage_metrics_agent_id"), "agent_usage_metrics", ["agent_id"], unique=False)
    op.create_index(op.f("ix_agent_usage_metrics_agent_run_id"), "agent_usage_metrics", ["agent_run_id"], unique=False)
    op.create_index(op.f("ix_agent_usage_metrics_created_at"), "agent_usage_metrics", ["created_at"], unique=False)
    op.create_index(op.f("ix_agent_usage_metrics_message_id"), "agent_usage_metrics", ["message_id"], unique=False)
    op.create_index(op.f("ix_agent_usage_metrics_model_name"), "agent_usage_metrics", ["model_name"], unique=False)
    op.create_index(op.f("ix_agent_usage_metrics_session_id"), "agent_usage_metrics", ["session_id"], unique=False)
    op.create_index(op.f("ix_agent_usage_metrics_user_id"), "agent_usage_metrics", ["user_id"], unique=False)

    op.create_table(
        "mcp_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_type", sa.String(length=50), nullable=False),
        sa.Column("method", sa.String(length=100), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("message_metadata", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("response_time", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["mcp_connections.id"], name=op.f("fk_mcp_messages_connection_id_mcp_connections")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_messages")),
    )


def downgrade() -> None:
    """Drop session-centric tables and related resources."""

    op.drop_table("mcp_messages")
    op.drop_index(op.f("ix_agent_usage_metrics_user_id"), table_name="agent_usage_metrics")
    op.drop_index(op.f("ix_agent_usage_metrics_session_id"), table_name="agent_usage_metrics")
    op.drop_index(op.f("ix_agent_usage_metrics_model_name"), table_name="agent_usage_metrics")
    op.drop_index(op.f("ix_agent_usage_metrics_message_id"), table_name="agent_usage_metrics")
    op.drop_index(op.f("ix_agent_usage_metrics_created_at"), table_name="agent_usage_metrics")
    op.drop_index(op.f("ix_agent_usage_metrics_agent_run_id"), table_name="agent_usage_metrics")
    op.drop_index(op.f("ix_agent_usage_metrics_agent_id"), table_name="agent_usage_metrics")
    op.drop_table("agent_usage_metrics")
    op.drop_index(op.f("ix_messages_user_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_session_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_created_at"), table_name="messages")
    op.drop_index(op.f("ix_messages_agent_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_agent_runs_workflow_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_user_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_session_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_created_at"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_agent_id"), table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index(op.f("ix_user_agent_access_user_id"), table_name="user_agent_access")
    op.drop_index(op.f("ix_user_agent_access_agent_id"), table_name="user_agent_access")
    op.drop_table("user_agent_access")
    op.drop_index(op.f("ix_sessions_workflow_id"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_created_at"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_agent_id"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(op.f("ix_agent_tools_tool_name"), table_name="agent_tools")
    op.drop_index(op.f("ix_agent_tools_agent_id"), table_name="agent_tools")
    op.drop_table("agent_tools")
    op.drop_index(op.f("ix_metrics_timestamp"), table_name="metrics")
    op.drop_index(op.f("ix_metrics_name"), table_name="metrics")
    op.drop_table("metrics")
