"""Consolidate all non-numbered migrations

Revision ID: 008
Revises: 007
Create Date: 2025-10-24 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '008'
down_revision = '007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Consolidate all non-numbered migrations into one."""
    conn = op.get_bind()
    # Ensure enums exist idempotently
    action_type_enum = postgresql.ENUM(
        'scale_agents',
        'enable_circuit_breaker',
        'reduce_token_usage',
        'reconnect_mcp',
        'optimize_database_queries',
        name='actiontype',
        create_type=True,
    )
    action_type_enum.create(bind=conn, checkfirst=True)

    execution_status_enum = postgresql.ENUM(
        'pending', 'dry_run', 'executing', 'success', 'failed', 'rolled_back',
        name='executionstatus',
        create_type=True,
    )
    execution_status_enum.create(bind=conn, checkfirst=True)

    # From 12e666374318_initial_migration_with_all_models.py
    # Create all initial tables (unconditional)
    op.create_table('alerts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('severity', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('metric_name', sa.String(length=100), nullable=True),
        sa.Column('threshold_value', sa.Float(), nullable=True),
        sa.Column('current_value', sa.Float(), nullable=True),
        sa.Column('alert_metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # users table is created in 000

    # user_agents table is created in 000

    op.create_table('user_permissions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('agent_id', sa.UUID(), nullable=True),
        sa.Column('permission_type', sa.String(length=50), nullable=False),
        sa.Column('resource', sa.String(length=100), nullable=True),
        sa.Column('is_granted', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['agent_id'], ['user_agents.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Note: chats and chat_messages tables were removed as they've been replaced
    # by sessions and messages tables (see migration 0012_add_sessions_tables.py)

    # From phase_5_remediation_schema.py
    # Create remediation tables
    op.create_table(
        'remediation_actions',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(255), nullable=False, unique=True),
        sa.Column('description', sa.String(1000), nullable=True),
        sa.Column('action_type', postgresql.ENUM(name='actiontype', create_type=False), nullable=False),
        sa.Column('service', sa.String(255), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('configuration', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('safety_guardrails', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('trigger_alerts', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_index('idx_action_type', 'remediation_actions', ['action_type'])
    op.create_index('idx_action_enabled', 'remediation_actions', ['enabled'])

    op.create_table(
        'remediation_rules',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(255), nullable=False, unique=True),
        sa.Column('description', sa.String(1000), nullable=True),
        sa.Column('action_id', sa.String(36), nullable=False),
        sa.Column('alert_type', sa.String(255), nullable=False),
        sa.Column('alert_severity', sa.String(50), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('dry_run_first', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('auto_execute', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('requires_approval', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('condition_config', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['action_id'], ['remediation_actions.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_index('idx_rule_enabled', 'remediation_rules', ['enabled'])
    op.create_index('idx_rule_action_id', 'remediation_rules', ['action_id'])

    op.create_table(
        'remediation_executions',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('action_id', sa.String(36), nullable=False),
        sa.Column('alert_id', sa.String(255), nullable=False),
        sa.Column('status', postgresql.ENUM(name='executionstatus', create_type=False), nullable=False, server_default='pending'),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('trigger_reason', sa.String(1000), nullable=True),
        sa.Column('execution_parameters', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('metrics_before', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('metrics_after', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('dry_run_result', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('rolled_back', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('rollback_reason', sa.String(500), nullable=True),
        sa.Column('rollback_at', sa.DateTime(), nullable=True),
        sa.Column('error_message', sa.String(1000), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('impact', sa.String(1000), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['action_id'], ['remediation_actions.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_execution_status', 'remediation_executions', ['status'])
    op.create_index('idx_execution_action_id', 'remediation_executions', ['action_id'])
    op.create_index('idx_execution_alert_id', 'remediation_executions', ['alert_id'])
    op.create_index('idx_execution_created_at', 'remediation_executions', ['created_at'])

    # From 132dd8fce5fe_add_menu_system_tables_only.py
    # Create menu system tables
    op.create_table('menus',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('display_name', sa.String(length=100), nullable=False),
        sa.Column('icon', sa.String(length=100), nullable=True),
        sa.Column('route', sa.String(length=200), nullable=True),
        sa.Column('order', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table('menu_items',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('menu_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('display_name', sa.String(length=100), nullable=False),
        sa.Column('icon', sa.String(length=100), nullable=True),
        sa.Column('route', sa.String(length=200), nullable=True),
        sa.Column('order', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['menu_id'], ['menus.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table('user_menu_preferences',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('menu_id', sa.UUID(), nullable=False),
        sa.Column('is_pinned', sa.Boolean(), nullable=True),
        sa.Column('order', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['menu_id'], ['menus.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table('launchpad_apps',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('app_id', sa.String(length=100), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('subtitle', sa.String(length=200), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('logo_src', sa.String(length=500), nullable=True),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('url', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('app_id', name='uq_launchpad_apps_app_id')
    )

    op.create_table('user_launchpad_preferences',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('app_id', sa.UUID(), nullable=False),
        sa.Column('is_pinned', sa.Boolean(), nullable=True),
        sa.Column('order', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['app_id'], ['launchpad_apps.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Correct table matching model UserLaunchpadPin
    op.create_table('user_launchpad_pins',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('app_id', sa.UUID(), nullable=False),
        sa.Column('is_pinned', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['app_id'], ['launchpad_apps.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Agents master table (align with models.menu.Agent)
    op.create_table('agents',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column('public_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('intro', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('icon', sa.String(length=500), nullable=True),
        sa.Column('mcp_url', sa.String(length=500), nullable=True),
        sa.Column('health_url', sa.String(length=500), nullable=True),
        sa.Column('model_name', sa.String(length=100), nullable=True),
        sa.Column('model_provider', sa.String(length=100), nullable=True),
        sa.Column('knowledge_sources', sa.JSON(), nullable=True),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('is_healthy', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('health_status', sa.String(length=50), nullable=True),
        sa.Column('last_health_check', sa.DateTime(), nullable=True),
        # Legacy fields
        sa.Column('agent_id', sa.String(length=100), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('subtitle', sa.String(length=200), nullable=True),
        sa.Column('legacy_tags', sa.JSON(), nullable=True),
        sa.Column('logo_src', sa.String(length=500), nullable=True),
        sa.Column('category', sa.String(length=50), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
        sa.Column('methods', sa.JSON(), nullable=True),
        sa.Column('last_used', sa.DateTime(), nullable=True),
        sa.Column('order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_agents_public_id', 'agents', ['public_id'], unique=True)

    # User agent pins (align with models.menu.UserAgentPin)
    op.create_table('user_agent_pins',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('agent_id', sa.BigInteger(), nullable=False),
        sa.Column('is_pinned', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Drop all consolidated tables."""

    # Drop menu system tables
    op.drop_table('user_launchpad_pins')
    op.drop_table('user_launchpad_preferences')
    op.drop_table('launchpad_apps')
    op.drop_table('user_menu_preferences')
    op.drop_table('menu_items')
    op.drop_table('menus')

    # Drop remediation tables
    op.drop_index('idx_execution_created_at', table_name='remediation_executions')
    op.drop_index('idx_execution_alert_id', table_name='remediation_executions')
    op.drop_index('idx_execution_action_id', table_name='remediation_executions')
    op.drop_index('idx_execution_status', table_name='remediation_executions')
    op.drop_table('remediation_executions')

    op.drop_index('idx_rule_action_id', table_name='remediation_rules')
    op.drop_index('idx_rule_enabled', table_name='remediation_rules')
    op.drop_table('remediation_rules')

    op.drop_index('idx_action_enabled', table_name='remediation_actions')
    op.drop_index('idx_action_type', table_name='remediation_actions')
    op.drop_table('remediation_actions')

    # Drop enums created in this migration
    postgresql.ENUM(name='executionstatus').drop(bind=op.get_bind(), checkfirst=True)
    postgresql.ENUM(name='actiontype').drop(bind=op.get_bind(), checkfirst=True)

    # Drop initial tables
    # Note: chats and chat_messages tables are no longer created in upgrade()
    op.drop_table('user_permissions')
    op.drop_table('user_agents')
    op.drop_table('users')
    op.drop_table('alerts')
