"""Add user groups and memberships

Revision ID: 007_add_user_groups_and_memberships
Revises: phase_5_remediation_schema
Create Date: 2025-01-24 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '007'
down_revision = '006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create user_groups table
    op.create_table('user_groups',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('azure_ad_group_id', sa.String(length=255), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('azure_ad_group_id'),
        sa.UniqueConstraint('name')
    )
    op.create_index(op.f('ix_user_groups_azure_ad_group_id'), 'user_groups', ['azure_ad_group_id'], unique=False)
    op.create_index(op.f('ix_user_groups_name'), 'user_groups', ['name'], unique=False)

    # Create user_group_memberships table
    op.create_table('user_group_memberships',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('group_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('role', sa.String(length=50), nullable=False, server_default='member'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['group_id'], ['user_groups.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_group_memberships_group_id'), 'user_group_memberships', ['group_id'], unique=False)
    op.create_index(op.f('ix_user_group_memberships_user_id'), 'user_group_memberships', ['user_id'], unique=False)


def downgrade() -> None:
    # Drop user_group_memberships table
    op.drop_index(op.f('ix_user_group_memberships_user_id'), table_name='user_group_memberships')
    op.drop_index(op.f('ix_user_group_memberships_group_id'), table_name='user_group_memberships')
    op.drop_table('user_group_memberships')

    # Drop user_groups table
    op.drop_index(op.f('ix_user_groups_name'), table_name='user_groups')
    op.drop_index(op.f('ix_user_groups_azure_ad_group_id'), table_name='user_groups')
    op.drop_table('user_groups')
