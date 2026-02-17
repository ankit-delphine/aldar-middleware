"""Create base tables for users, user_agents, and mcp_connections

Revision ID: 000
Revises: 
Create Date: 2025-01-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '000'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create essential base tables (no guards)."""
    # users
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('username', sa.String(100), nullable=True),
        sa.Column('full_name', sa.String(255), nullable=True),
        sa.Column('first_name', sa.String(100), nullable=True),
        sa.Column('last_name', sa.String(100), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('is_verified', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('is_admin', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        # Azure AD fields
        sa.Column('azure_ad_id', sa.String(255), nullable=True),
        sa.Column('azure_tenant_id', sa.String(255), nullable=True),
        sa.Column('azure_upn', sa.String(255), nullable=True),
        sa.Column('azure_display_name', sa.String(255), nullable=True),
        sa.Column('azure_department', sa.String(255), nullable=True),
        sa.Column('azure_job_title', sa.String(255), nullable=True),
        sa.Column('azure_ad_refresh_token', sa.Text(), nullable=True),
        # Additional fields
        sa.Column('password_hash', sa.String(255), nullable=True),
        sa.Column('preferences', sa.JSON(), nullable=True),
        sa.Column('total_tokens_used', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column('last_login', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
        sa.UniqueConstraint('username'),
        sa.UniqueConstraint('azure_ad_id'),
    )
    # Helpful indexes
    op.create_index('ix_users_email', 'users', ['email'], unique=True)
    op.create_index('ix_users_username', 'users', ['username'], unique=True)
    op.create_index('ix_users_azure_ad_id', 'users', ['azure_ad_id'], unique=True)

    # mcp_connections
    op.create_table(
        'mcp_connections',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('server_url', sa.String(500), nullable=False),
        sa.Column('api_key', sa.String(255), nullable=True),
        sa.Column('connection_type', sa.String(50), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('last_connected', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # user_agents
    op.create_table(
        'user_agents',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('agent_type', sa.String(50), nullable=False),
        sa.Column('agent_config', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Drop base tables in reverse dependency order."""
    op.drop_table('user_agents')
    op.drop_table('mcp_connections')
    op.drop_table('users')
