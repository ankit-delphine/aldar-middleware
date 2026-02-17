"""Add ERD compatibility tables.

Revision ID: 0014
Revises: 0013
Create Date: 2025-11-20 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create ERD compatibility tables for external team integration."""

    # Add started_at to sessions table
    op.add_column("sessions", sa.Column("started_at", sa.DateTime(), nullable=True))

    # Create runs table (ERD compatibility)
    # Note: session_id stores public_id as string (UUID converted to string)
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(length=255), nullable=False),
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("agent_name", sa.String(length=255), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("content_type", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("model_provider", sa.String(length=100), nullable=True),
        sa.Column(
            "status",
            sa.String(length=50),
            server_default=sa.text("'running'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_runs_agent_id_agents")
        ),
        # Note: session_id references sessions.public_id as string (no FK constraint)
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_runs")),
    )
    op.create_index(op.f("ix_runs_agent_id"), "runs", ["agent_id"], unique=False)
    op.create_index(
        op.f("ix_runs_created_at"), "runs", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_runs_session_id"), "runs", ["session_id"], unique=False
    )

    # Create memories table (ERD compatibility)
    # Note: session_id stores public_id as string (UUID converted to string)
    op.create_table(
        "memories",
        sa.Column("memory_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("memory_content", sa.Text(), nullable=True),
        sa.Column("memory_type", sa.String(length=50), nullable=True),
        # Note: session_id references sessions.public_id as string (no FK constraint)
        sa.PrimaryKeyConstraint("memory_id", name=op.f("pk_memories")),
    )
    op.create_index(
        op.f("ix_memories_session_id"), "memories", ["session_id"], unique=False
    )

    # Create events table (ERD compatibility)
    op.create_table(
        "events",
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("run_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=True),
        sa.Column("agent_id", sa.BigInteger(), nullable=True),
        sa.Column("agent_name", sa.String(length=255), nullable=True),
        sa.Column("session_id", sa.String(length=255), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("model_provider", sa.String(length=100), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("content_type", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.run_id"], name=op.f("fk_events_run_id_runs")
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_events")),
    )
    op.create_index(
        op.f("ix_events_created_at"), "events", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_events_run_id"), "events", ["run_id"], unique=False)

    # Create run_messages table (ERD compatibility)
    op.create_table(
        "run_messages",
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("run_id", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column(
            "from_history",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "stop_after_tool_call",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.run_id"],
            name=op.f("fk_run_messages_run_id_runs"),
        ),
        sa.PrimaryKeyConstraint("message_id", name=op.f("pk_run_messages")),
    )
    op.create_index(
        op.f("ix_run_messages_created_at"),
        "run_messages",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_run_messages_run_id"), "run_messages", ["run_id"], unique=False
    )

    # Create run_metrics table (ERD compatibility)
    op.create_table(
        "run_metrics",
        sa.Column("metrics_id", sa.String(length=255), nullable=False),
        sa.Column("run_id", sa.String(length=255), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("time_to_first_token", sa.Float(), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.run_id"],
            name=op.f("fk_run_metrics_run_id_runs"),
        ),
        sa.PrimaryKeyConstraint("metrics_id", name=op.f("pk_run_metrics")),
        sa.UniqueConstraint("run_id", name=op.f("uq_run_metrics_run_id")),
    )
    op.create_index(
        op.f("ix_run_metrics_run_id"), "run_metrics", ["run_id"], unique=True
    )

    # Create run_inputs table (ERD compatibility)
    op.create_table(
        "run_inputs",
        sa.Column("input_id", sa.String(length=255), nullable=False),
        sa.Column("run_id", sa.String(length=255), nullable=False),
        sa.Column("input_content", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.run_id"],
            name=op.f("fk_run_inputs_run_id_runs"),
        ),
        sa.PrimaryKeyConstraint("input_id", name=op.f("pk_run_inputs")),
        sa.UniqueConstraint("run_id", name=op.f("uq_run_inputs_run_id")),
    )
    op.create_index(
        op.f("ix_run_inputs_run_id"), "run_inputs", ["run_id"], unique=True
    )

    # Create configs table (for global prompts & system config)
    op.create_table(
        "configs",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("system_wide_prompt", sa.Text(), nullable=False),
        sa.Column("system_agent_prompt", sa.Text(), nullable=False),
        sa.Column("user_custom_query_template", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_configs")),
    )
    op.create_index(
        op.f("ix_configs_version"), "configs", ["version"], unique=False
    )

    # Create starter_prompts table
    op.create_table(
        "starter_prompts",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "is_highlighted",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("knowledge_agent_id", sa.String(length=255), nullable=True),
        sa.Column("my_agent_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["my_agent_id"],
            ["agents.id"],
            name=op.f("fk_starter_prompts_my_agent_id_agents"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_starter_prompts")),
    )
    op.create_index(
        op.f("ix_starter_prompts_my_agent_id"),
        "starter_prompts",
        ["my_agent_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop ERD compatibility tables."""

    op.drop_index(
        op.f("ix_starter_prompts_my_agent_id"), table_name="starter_prompts"
    )
    op.drop_table("starter_prompts")
    op.drop_index(op.f("ix_configs_version"), table_name="configs")
    op.drop_table("configs")
    op.drop_index(op.f("ix_run_inputs_run_id"), table_name="run_inputs")
    op.drop_table("run_inputs")
    op.drop_index(op.f("ix_run_metrics_run_id"), table_name="run_metrics")
    op.drop_table("run_metrics")
    op.drop_index(op.f("ix_run_messages_run_id"), table_name="run_messages")
    op.drop_index(
        op.f("ix_run_messages_created_at"), table_name="run_messages"
    )
    op.drop_table("run_messages")
    op.drop_index(op.f("ix_events_run_id"), table_name="events")
    op.drop_index(op.f("ix_events_created_at"), table_name="events")
    op.drop_table("events")
    op.drop_index(op.f("ix_memories_session_id"), table_name="memories")
    op.drop_table("memories")
    op.drop_index(op.f("ix_runs_session_id"), table_name="runs")
    op.drop_index(op.f("ix_runs_created_at"), table_name="runs")
    op.drop_index(op.f("ix_runs_agent_id"), table_name="runs")
    op.drop_table("runs")
    op.drop_column("sessions", "started_at")
