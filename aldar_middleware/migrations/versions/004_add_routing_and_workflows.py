"""Add routing and workflow tables.

Revision ID: 004_add_routing_and_workflows
Revises: 003
Create Date: 2025-01-XX XX:XX:XX.XXXXXX

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create routing and workflow tables (no guards)."""
    # agent_capabilities
    op.create_table(
        "agent_capabilities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("capability_name", sa.String(255), nullable=False),
        sa.Column("capability_category", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), default=50.0),
        sa.Column("accuracy_score", sa.Float(), nullable=True),
        sa.Column("latency_score", sa.Float(), nullable=True),
        sa.Column("cost_score", sa.Float(), nullable=True),
        sa.Column("availability_score", sa.Float(), nullable=True),
        sa.Column("tags", postgresql.JSON(), nullable=True),
        sa.Column("capability_metadata", postgresql.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("created_at", sa.DateTime(), default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), default=sa.func.now(), onupdate=sa.func.now()),
        sa.ForeignKeyConstraint(["agent_id"], ["user_agents.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_agent_capabilities_agent_id", "agent_capabilities", ["agent_id"])
    op.create_index("idx_agent_capabilities_user_id", "agent_capabilities", ["user_id"])
    op.create_index("idx_agent_capabilities_capability_name", "agent_capabilities", ["capability_name"])

    # routing_policies
    op.create_table(
        "routing_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("rules", postgresql.JSON(), nullable=False),
        sa.Column("is_default", sa.Boolean(), default=False),
        sa.Column("priority", sa.Integer(), default=0),
        sa.Column("enabled", sa.Boolean(), default=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), default=sa.func.now(), onupdate=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_routing_policies_user_id", "routing_policies", ["user_id"])
    op.create_index("idx_routing_policies_enabled", "routing_policies", ["enabled"])
    op.create_index("idx_routing_policies_default", "routing_policies", ["is_default"])

    # routing_executions
    op.create_table(
        "routing_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_context", postgresql.JSON(), nullable=True),
        sa.Column("selected_agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selected_reason", sa.String(255), nullable=True),
        sa.Column("candidate_agents", postgresql.JSON(), nullable=True),
        sa.Column("scoring_criteria", postgresql.JSON(), nullable=True),
        sa.Column("scores", postgresql.JSON(), nullable=True),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), default="success"),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["policy_id"], ["routing_policies.id"]),
        sa.ForeignKeyConstraint(["selected_agent_id"], ["user_agents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_routing_executions_user_id", "routing_executions", ["user_id"])
    op.create_index("idx_routing_executions_policy_id", "routing_executions", ["policy_id"])
    op.create_index("idx_routing_executions_created_at", "routing_executions", ["created_at"])

    # workflows
    op.create_table(
        "workflows",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.String(50), default="1.0.0"),
        sa.Column("definition", postgresql.JSON(), nullable=False),
        sa.Column("tags", postgresql.JSON(), nullable=True),
        sa.Column("metadata", postgresql.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("is_template", sa.Boolean(), default=False),
        sa.Column("created_at", sa.DateTime(), default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), default=sa.func.now(), onupdate=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_workflows_user_id", "workflows", ["user_id"])
    op.create_index("idx_workflows_is_active", "workflows", ["is_active"])
    op.create_index("idx_workflows_is_template", "workflows", ["is_template"])

    # workflow_executions
    op.create_table(
        "workflow_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), default="pending"),
        sa.Column("inputs", postgresql.JSON(), nullable=True),
        sa.Column("outputs", postgresql.JSON(), nullable=True),
        sa.Column("execution_plan", postgresql.JSON(), nullable=True),
        sa.Column("total_duration_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), default=sa.func.now()),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_workflow_executions_workflow_id", "workflow_executions", ["workflow_id"])
    op.create_index("idx_workflow_executions_user_id", "workflow_executions", ["user_id"])
    op.create_index("idx_workflow_executions_status", "workflow_executions", ["status"])
    op.create_index("idx_workflow_executions_created_at", "workflow_executions", ["created_at"])
    op.create_index("idx_workflow_executions_correlation_id", "workflow_executions", ["correlation_id"])

    # workflow_steps
    op.create_table(
        "workflow_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_id", sa.String(255), nullable=False),
        sa.Column("step_name", sa.String(255), nullable=False),
        sa.Column("step_type", sa.String(50), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("method_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(20), default="pending"),
        sa.Column("inputs", postgresql.JSON(), nullable=True),
        sa.Column("outputs", postgresql.JSON(), nullable=True),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), default=sa.func.now()),
        sa.ForeignKeyConstraint(["execution_id"], ["workflow_executions.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["user_agents.id"]),
        sa.ForeignKeyConstraint(["method_id"], ["agent_methods.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_workflow_steps_execution_id", "workflow_steps", ["execution_id"]) 
    op.create_index("idx_workflow_steps_step_id", "workflow_steps", ["step_id"]) 
    op.create_index("idx_workflow_steps_status", "workflow_steps", ["status"]) 
    op.create_index("idx_workflow_steps_agent_id", "workflow_steps", ["agent_id"]) 


def downgrade() -> None:
    """Drop routing and workflow tables."""
    op.drop_index("idx_workflow_steps_agent_id", table_name="workflow_steps")
    op.drop_index("idx_workflow_steps_status", table_name="workflow_steps")
    op.drop_index("idx_workflow_steps_step_id", table_name="workflow_steps")
    op.drop_index("idx_workflow_steps_execution_id", table_name="workflow_steps")
    op.drop_table("workflow_steps")

    op.drop_index("idx_workflow_executions_correlation_id", table_name="workflow_executions")
    op.drop_index("idx_workflow_executions_created_at", table_name="workflow_executions")
    op.drop_index("idx_workflow_executions_status", table_name="workflow_executions")
    op.drop_index("idx_workflow_executions_user_id", table_name="workflow_executions")
    op.drop_index("idx_workflow_executions_workflow_id", table_name="workflow_executions")
    op.drop_table("workflow_executions")

    op.drop_index("idx_workflows_is_template", table_name="workflows")
    op.drop_index("idx_workflows_is_active", table_name="workflows")
    op.drop_index("idx_workflows_user_id", table_name="workflows")
    op.drop_table("workflows")

    op.drop_index("idx_routing_executions_created_at", table_name="routing_executions")
    op.drop_index("idx_routing_executions_policy_id", table_name="routing_executions")
    op.drop_index("idx_routing_executions_user_id", table_name="routing_executions")
    op.drop_table("routing_executions")

    op.drop_index("idx_routing_policies_default", table_name="routing_policies")
    op.drop_index("idx_routing_policies_enabled", table_name="routing_policies")
    op.drop_index("idx_routing_policies_user_id", table_name="routing_policies")
    op.drop_table("routing_policies")

    op.drop_index("idx_agent_capabilities_capability_name", table_name="agent_capabilities")
    op.drop_index("idx_agent_capabilities_user_id", table_name="agent_capabilities")
    op.drop_index("idx_agent_capabilities_agent_id", table_name="agent_capabilities")
    op.drop_table("agent_capabilities")