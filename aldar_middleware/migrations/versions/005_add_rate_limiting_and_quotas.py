"""Add rate limiting and usage quotas tables.

Revision ID: 005
Revises: 004
Create Date: 2025-01-15 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create rate limiting and quotas tables."""
    # Create RateLimitConfig table
    op.create_table(
        "rate_limit_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_type", sa.String(length=50), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("method_id", sa.String(length=255), nullable=True),
        sa.Column("requests_per_minute", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("concurrent_executions", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("requests_per_hour", sa.Integer(), nullable=True),
        sa.Column("requests_per_day", sa.Integer(), nullable=True),
        sa.Column("burst_size", sa.Integer(), nullable=True),
        sa.Column("throttle_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("config_metadata", sa.JSON(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["user_agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_rate_limit_configs_user_id", "rate_limit_configs", ["user_id"])
    op.create_index("idx_rate_limit_configs_scope", "rate_limit_configs", ["scope_type", "agent_id", "method_id"])
    op.create_index("idx_rate_limit_configs_active", "rate_limit_configs", ["is_active"])

    # Create RateLimitUsage table
    op.create_table(
        "rate_limit_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("window_type", sa.String(length=50), nullable=False),
        sa.Column("window_start", sa.DateTime(), nullable=False),
        sa.Column("window_end", sa.DateTime(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("concurrent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("throttled_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cost", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["config_id"], ["rate_limit_configs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_rate_limit_usage_config_id", "rate_limit_usage", ["config_id"])
    op.create_index("idx_rate_limit_usage_user_id", "rate_limit_usage", ["user_id"])
    op.create_index("idx_rate_limit_usage_window", "rate_limit_usage", ["window_start", "window_end"])
    op.create_index("idx_rate_limit_usage_user_window", "rate_limit_usage", ["user_id", "window_start"])

    # Create CostModel table
    op.create_table(
        "cost_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("method_id", sa.String(length=255), nullable=True),
        sa.Column("per_execution", sa.Float(), nullable=False, server_default="0.001"),
        sa.Column("per_result_kb", sa.Float(), nullable=False, server_default="0.0001"),
        sa.Column("per_token", sa.Float(), nullable=True),
        sa.Column("minimum_charge", sa.Float(), nullable=False, server_default="0.001"),
        sa.Column("volume_discount_threshold", sa.Integer(), nullable=True),
        sa.Column("volume_discount_percent", sa.Float(), nullable=True),
        sa.Column("monthly_discount_percent", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("effective_from", sa.DateTime(), nullable=True),
        sa.Column("effective_to", sa.DateTime(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cost_metadata", sa.JSON(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["user_agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_cost_models_user_id", "cost_models", ["user_id"])
    op.create_index("idx_cost_models_agent_method", "cost_models", ["agent_id", "method_id"])
    op.create_index("idx_cost_models_active", "cost_models", ["is_active"])

    # Create UsageQuota table
    op.create_table(
        "usage_quotas",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quota_type", sa.String(length=50), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("max_executions", sa.BigInteger(), nullable=True),
        sa.Column("max_cost", sa.Float(), nullable=True),
        sa.Column("max_concurrent", sa.Integer(), nullable=True),
        sa.Column("executions_used", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_used", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("warning_threshold_percent", sa.Float(), nullable=False, server_default="80.0"),
        sa.Column("warning_sent_at_percent", sa.Float(), nullable=True),
        sa.Column("critical_threshold_percent", sa.Float(), nullable=False, server_default="95.0"),
        sa.Column("critical_sent_at_percent", sa.Float(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_exceeded", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("quota_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_usage_quotas_user_id", "usage_quotas", ["user_id"])
    op.create_index("idx_usage_quotas_period", "usage_quotas", ["period_start", "period_end"])
    op.create_index("idx_usage_quotas_active", "usage_quotas", ["is_active"])

    # Create UserBudget table
    op.create_table(
        "user_budgets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("monthly_budget", sa.Float(), nullable=True),
        sa.Column("total_budget", sa.Float(), nullable=True),
        sa.Column("current_month_spent", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("total_spent", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("enforce_limit", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("alert_at_percent", sa.Float(), nullable=False, server_default="75.0"),
        sa.Column("last_alert_at", sa.DateTime(), nullable=True),
        sa.Column("alert_frequency_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("blocked_reason", sa.String(length=255), nullable=True),
        sa.Column("blocked_at", sa.DateTime(), nullable=True),
        sa.Column("month_start", sa.DateTime(), nullable=True),
        sa.Column("budget_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_budgets_user_id"),
    )
    op.create_index("idx_user_budgets_user_id", "user_budgets", ["user_id"])
    op.create_index("idx_user_budgets_active", "user_budgets", ["is_active"])

    # Create UsageReport table
    op.create_table(
        "usage_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("report_period", sa.String(length=50), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("total_executions", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_cost", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("average_cost_per_execution", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("agent_usage", sa.JSON(), nullable=True),
        sa.Column("method_usage", sa.JSON(), nullable=True),
        sa.Column("cost_by_category", sa.JSON(), nullable=True),
        sa.Column("average_response_time_ms", sa.Float(), nullable=True),
        sa.Column("error_rate_percent", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("success_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("projected_monthly_cost", sa.Float(), nullable=True),
        sa.Column("projected_monthly_executions", sa.BigInteger(), nullable=True),
        sa.Column("is_finalized", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("report_metadata", sa.JSON(), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_usage_reports_user_id", "usage_reports", ["user_id"])
    op.create_index("idx_usage_reports_period", "usage_reports", ["period_start", "period_end"])
    op.create_index("idx_usage_reports_period_type", "usage_reports", ["report_period"])


def downgrade() -> None:
    """Drop all rate limiting and quotas tables."""
    # Drop tables in reverse order of creation
    op.drop_table("usage_reports")
    op.drop_table("user_budgets")
    op.drop_table("usage_quotas")
    op.drop_table("cost_models")
    op.drop_table("rate_limit_usage")
    op.drop_table("rate_limit_configs")