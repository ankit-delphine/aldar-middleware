"""Add health monitoring and circuit breaker tables.

Revision ID: 003
Revises: 002
Create Date: 2024-01-10 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create health monitoring and circuit breaker tables (no guards)."""
    # agent_health_status
    op.create_table(
        'agent_health_status',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('last_check', sa.DateTime(), nullable=True),
        sa.Column('response_time_ms', sa.Integer(), nullable=True),
        sa.Column('uptime_percent', sa.Float(), server_default=sa.text('100.0'), nullable=False),
        sa.Column('total_checks', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('successful_checks', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('consecutive_failures', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('avg_response_time_ms', sa.Float(), nullable=True),
        sa.Column('max_response_time_ms', sa.Integer(), nullable=True),
        sa.Column('health_metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['user_agents.id'], name='fk_agent_health_status_agent_id_user_agents'),
        sa.PrimaryKeyConstraint('id', name='pk_agent_health_status'),
        sa.Index('ix_agent_health_status_agent_id', 'agent_id'),
    )

    # agent_health_check_history
    op.create_table(
        'agent_health_check_history',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('check_timestamp', sa.DateTime(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('response_time_ms', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('check_details', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['user_agents.id'], name='fk_agent_health_check_history_agent_id_user_agents'),
        sa.PrimaryKeyConstraint('id', name='pk_agent_health_check_history'),
        sa.Index('ix_agent_health_check_history_agent_id', 'agent_id'),
        sa.Index('ix_agent_health_check_history_check_timestamp', 'check_timestamp'),
    )

    # degradation_events
    op.create_table(
        'degradation_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('degradation_type', sa.String(50), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('severity', sa.String(20), nullable=False),
        sa.Column('resolution_status', sa.String(20), server_default='pending', nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('fallback_action', sa.String(100), nullable=True),
        sa.Column('degradation_metadata', sa.JSON(), nullable=True),
        sa.Column('user_notification_sent', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['user_agents.id'], name='fk_degradation_events_agent_id_user_agents'),
        sa.PrimaryKeyConstraint('id', name='pk_degradation_events'),
        sa.Index('ix_degradation_events_agent_id', 'agent_id'),
    )

    # circuit_breaker_state
    op.create_table(
        'circuit_breaker_state',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('method_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('failure_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('success_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('failure_threshold', sa.Integer(), server_default=sa.text('5'), nullable=False),
        sa.Column('success_threshold', sa.Integer(), server_default=sa.text('2'), nullable=False),
        sa.Column('timeout_seconds', sa.Integer(), server_default=sa.text('60'), nullable=False),
        sa.Column('backoff_multiplier', sa.Float(), server_default=sa.text('2.0'), nullable=False),
        sa.Column('last_state_change', sa.DateTime(), nullable=False),
        sa.Column('last_failure_time', sa.DateTime(), nullable=True),
        sa.Column('opened_at', sa.DateTime(), nullable=True),
        sa.Column('breaker_metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['user_agents.id'], name='fk_circuit_breaker_state_agent_id_user_agents'),
        sa.ForeignKeyConstraint(['method_id'], ['agent_methods.id'], name='fk_circuit_breaker_state_method_id_agent_methods'),
        sa.PrimaryKeyConstraint('id', name='pk_circuit_breaker_state'),
        sa.Index('ix_circuit_breaker_state_agent_id', 'agent_id'),
        sa.Index('ix_circuit_breaker_state_method_id', 'method_id'),
    )


def downgrade() -> None:
    """Drop health monitoring and circuit breaker tables."""
    op.drop_table('circuit_breaker_state')
    op.drop_table('degradation_events')
    op.drop_table('agent_health_check_history')
    op.drop_table('agent_health_status')