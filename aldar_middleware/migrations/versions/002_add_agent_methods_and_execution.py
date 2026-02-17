"""Add agent methods and execution tracking tables

Revision ID: 002
Revises: 001
Create Date: 2025-01-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create agent methods and execution tables (no guards)."""
    # Add mcp_connection_id to user_agents (fk to mcp_connections)
    op.add_column('user_agents', sa.Column('mcp_connection_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_user_agents_mcp_connection_id', 'user_agents', 'mcp_connections', ['mcp_connection_id'], ['id'])

    # agent_methods
    op.create_table(
        'agent_methods',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('connection_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('method_name', sa.String(255), nullable=False),
        sa.Column('display_name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('parameters_schema', sa.JSON(), nullable=True),
        sa.Column('return_type', sa.String(100), nullable=True),
        sa.Column('is_deprecated', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('version', sa.String(50), nullable=False, server_default='1.0.0'),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('additional_metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['connection_id'], ['mcp_connections.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_agent_methods_connection_id'), 'agent_methods', ['connection_id'], unique=False)
    op.create_index(op.f('ix_agent_methods_method_name'), 'agent_methods', ['method_name'], unique=False)

    # agent_method_executions
    op.create_table(
        'agent_method_executions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('method_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('correlation_id', sa.String(255), nullable=True),
        sa.Column('parameters', sa.JSON(), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('execution_duration_ms', sa.Integer(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default=sa.literal(0)),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['agent_id'], ['user_agents.id']),
        sa.ForeignKeyConstraint(['method_id'], ['agent_methods.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_agent_method_executions_method_id'), 'agent_method_executions', ['method_id'], unique=False)
    op.create_index(op.f('ix_agent_method_executions_user_id'), 'agent_method_executions', ['user_id'], unique=False)
    op.create_index(op.f('ix_agent_method_executions_agent_id'), 'agent_method_executions', ['agent_id'], unique=False)
    op.create_index(op.f('ix_agent_method_executions_status'), 'agent_method_executions', ['status'], unique=False)


def downgrade() -> None:
    """Drop created artifacts."""
    op.drop_index(op.f('ix_agent_method_executions_status'), table_name='agent_method_executions')
    op.drop_index(op.f('ix_agent_method_executions_agent_id'), table_name='agent_method_executions')
    op.drop_index(op.f('ix_agent_method_executions_user_id'), table_name='agent_method_executions')
    op.drop_index(op.f('ix_agent_method_executions_method_id'), table_name='agent_method_executions')
    op.drop_table('agent_method_executions')

    op.drop_index(op.f('ix_agent_methods_method_name'), table_name='agent_methods')
    op.drop_index(op.f('ix_agent_methods_connection_id'), table_name='agent_methods')
    op.drop_table('agent_methods')

    op.drop_constraint('fk_user_agents_mcp_connection_id', 'user_agents', type_='foreignkey')
    op.drop_column('user_agents', 'mcp_connection_id')