"""Add complete RBAC system with UUID IDs and Azure AD mappings

Revision ID: 009
Revises: 008
Create Date: 2025-10-30 00:00:00.000000

This migration consolidates:
- Complete RBAC system (roles, services, permissions, users)
- UUID conversion for role_id and service_id
- Azure AD group to role mappings
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '009'
down_revision = '008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Azure PostgreSQL doesn't allow uuid-ossp in many tiers; rely on app-side UUIDs
    
    # ### PART 1: Create RBAC core tables with UUID primary keys ###
    
    # Create RBAC roles table with UUID
    op.create_table('rbac_roles',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('level', sa.Integer(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_rbac_roles'))
    )
    op.create_index(op.f('ix_rbac_roles_id'), 'rbac_roles', ['id'], unique=False)
    op.create_index(op.f('ix_rbac_roles_level'), 'rbac_roles', ['level'], unique=False)
    op.create_index(op.f('ix_rbac_roles_name'), 'rbac_roles', ['name'], unique=True)
    
    # Create RBAC services table with UUID
    op.create_table('rbac_services',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('service_type', sa.String(length=50), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_rbac_services'))
    )
    op.create_index(op.f('ix_rbac_services_id'), 'rbac_services', ['id'], unique=False)
    op.create_index(op.f('ix_rbac_services_name'), 'rbac_services', ['name'], unique=True)
    
    # Create RBAC users table
    op.create_table('rbac_users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=100), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('full_name', sa.String(length=200), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_rbac_users'))
    )
    op.create_index(op.f('ix_rbac_users_email'), 'rbac_users', ['email'], unique=True)
    op.create_index(op.f('ix_rbac_users_id'), 'rbac_users', ['id'], unique=False)
    op.create_index(op.f('ix_rbac_users_username'), 'rbac_users', ['username'], unique=True)
    
    # Create RBAC permissions table
    op.create_table('rbac_permissions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('resource', sa.String(length=100), nullable=False),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_rbac_permissions'))
    )
    op.create_index(op.f('ix_rbac_permissions_id'), 'rbac_permissions', ['id'], unique=False)
    op.create_index(op.f('ix_rbac_permissions_name'), 'rbac_permissions', ['name'], unique=True)
    
    # Create role-services association table with UUID foreign keys
    op.create_table('role_services',
        sa.Column('role_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('service_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(['role_id'], ['rbac_roles.id'], name=op.f('fk_role_services_role_id_rbac_roles')),
        sa.ForeignKeyConstraint(['service_id'], ['rbac_services.id'], name=op.f('fk_role_services_service_id_rbac_services')),
        sa.PrimaryKeyConstraint('role_id', 'service_id', name=op.f('pk_role_services'))
    )
    op.create_index(op.f('ix_role_services_role_id'), 'role_services', ['role_id'], unique=False)
    op.create_index(op.f('ix_role_services_service_id'), 'role_services', ['service_id'], unique=False)
    
    # Create user-specific roles association table with UUID role_id
    op.create_table('user_specific_roles',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('granted_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['granted_by'], ['rbac_users.id'], name=op.f('fk_user_specific_roles_granted_by_rbac_users')),
        sa.ForeignKeyConstraint(['role_id'], ['rbac_roles.id'], name=op.f('fk_user_specific_roles_role_id_rbac_roles')),
        sa.ForeignKeyConstraint(['user_id'], ['rbac_users.id'], name=op.f('fk_user_specific_roles_user_id_rbac_users')),
        sa.PrimaryKeyConstraint('user_id', 'role_id', name=op.f('pk_user_specific_roles'))
    )
    op.create_index(op.f('ix_user_specific_roles_user_id'), 'user_specific_roles', ['user_id'], unique=False)
    op.create_index(op.f('ix_user_specific_roles_role_id'), 'user_specific_roles', ['role_id'], unique=False)
    op.create_index(op.f('ix_user_specific_roles_granted_by'), 'user_specific_roles', ['granted_by'], unique=False)
    
    # Create role-permissions association table with UUID role_id
    op.create_table('rbac_role_permissions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('role_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('permission_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['permission_id'], ['rbac_permissions.id'], name=op.f('fk_rbac_role_permissions_permission_id_rbac_permissions')),
        sa.ForeignKeyConstraint(['role_id'], ['rbac_roles.id'], name=op.f('fk_rbac_role_permissions_role_id_rbac_roles')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_rbac_role_permissions'))
    )
    op.create_index(op.f('ix_rbac_role_permissions_id'), 'rbac_role_permissions', ['id'], unique=False)
    op.create_index(op.f('ix_rbac_role_permissions_role_id'), 'rbac_role_permissions', ['role_id'], unique=False)
    op.create_index(op.f('ix_rbac_role_permissions_permission_id'), 'rbac_role_permissions', ['permission_id'], unique=False)
    
    # Create user sessions table
    op.create_table('rbac_user_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('session_token', sa.String(length=255), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('last_accessed', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['rbac_users.id'], name=op.f('fk_rbac_user_sessions_user_id_rbac_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_rbac_user_sessions'))
    )
    op.create_index(op.f('ix_rbac_user_sessions_id'), 'rbac_user_sessions', ['id'], unique=False)
    op.create_index(op.f('ix_rbac_user_sessions_session_token'), 'rbac_user_sessions', ['session_token'], unique=True)
    
    # ### PART 2: Role Groups System ###
    
    # Create role groups table
    op.create_table('rbac_role_groups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_rbac_role_groups'))
    )
    op.create_index(op.f('ix_rbac_role_groups_id'), 'rbac_role_groups', ['id'], unique=False)
    op.create_index(op.f('ix_rbac_role_groups_name'), 'rbac_role_groups', ['name'], unique=True)
    
    # Create role group to roles association table with UUID role_id
    op.create_table('role_group_roles',
        sa.Column('role_group_id', sa.Integer(), nullable=False),
        sa.Column('role_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(['role_group_id'], ['rbac_role_groups.id'], name=op.f('fk_role_group_roles_role_group_id_rbac_role_groups')),
        sa.ForeignKeyConstraint(['role_id'], ['rbac_roles.id'], name=op.f('fk_role_group_roles_role_id_rbac_roles')),
        sa.PrimaryKeyConstraint('role_group_id', 'role_id', name=op.f('pk_role_group_roles'))
    )
    op.create_index(op.f('ix_role_group_roles_role_group_id'), 'role_group_roles', ['role_group_id'], unique=False)
    op.create_index(op.f('ix_role_group_roles_role_id'), 'role_group_roles', ['role_id'], unique=False)
    
    # Create role group to services association table with UUID service_id
    op.create_table('role_group_services',
        sa.Column('role_group_id', sa.Integer(), nullable=False),
        sa.Column('service_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(['role_group_id'], ['rbac_role_groups.id'], name=op.f('fk_role_group_services_role_group_id_rbac_role_groups')),
        sa.ForeignKeyConstraint(['service_id'], ['rbac_services.id'], name=op.f('fk_role_group_services_service_id_rbac_services')),
        sa.PrimaryKeyConstraint('role_group_id', 'service_id', name=op.f('pk_role_group_services'))
    )
    op.create_index(op.f('ix_role_group_services_role_group_id'), 'role_group_services', ['role_group_id'], unique=False)
    op.create_index(op.f('ix_role_group_services_service_id'), 'role_group_services', ['service_id'], unique=False)
    
    # Create user to role groups association table
    op.create_table('user_role_groups',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role_group_id', sa.Integer(), nullable=False),
        sa.Column('granted_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['granted_by'], ['rbac_users.id'], name=op.f('fk_user_role_groups_granted_by_rbac_users')),
        sa.ForeignKeyConstraint(['role_group_id'], ['rbac_role_groups.id'], name=op.f('fk_user_role_groups_role_group_id_rbac_role_groups')),
        sa.ForeignKeyConstraint(['user_id'], ['rbac_users.id'], name=op.f('fk_user_role_groups_user_id_rbac_users')),
        sa.PrimaryKeyConstraint('user_id', 'role_group_id', name=op.f('pk_user_role_groups'))
    )
    op.create_index(op.f('ix_user_role_groups_user_id'), 'user_role_groups', ['user_id'], unique=False)
    op.create_index(op.f('ix_user_role_groups_role_group_id'), 'user_role_groups', ['role_group_id'], unique=False)
    op.create_index(op.f('ix_user_role_groups_granted_by'), 'user_role_groups', ['granted_by'], unique=False)
    
    # ### PART 3: Individual User Access ###
    
    # Create individual user access table
    op.create_table('rbac_user_access',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('access_name', sa.String(length=100), nullable=False),
        sa.Column('access_type', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('granted_by', sa.Integer(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['granted_by'], ['rbac_users.id'], name=op.f('fk_rbac_user_access_granted_by_rbac_users')),
        sa.ForeignKeyConstraint(['user_id'], ['rbac_users.id'], name=op.f('fk_rbac_user_access_user_id_rbac_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_rbac_user_access'))
    )
    op.create_index(op.f('ix_rbac_user_access_id'), 'rbac_user_access', ['id'], unique=False)
    op.create_index(op.f('ix_rbac_user_access_user_id'), 'rbac_user_access', ['user_id'], unique=False)
    
    # ### PART 4: Azure AD Group Role Mappings ###
    
    # Create Azure AD group to role mappings table with UUID role_id
    op.create_table(
        'azure_ad_group_role_mappings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('azure_ad_group_id', sa.String(length=255), nullable=False),
        sa.Column('azure_ad_group_name', sa.String(length=255), nullable=True),
        sa.Column('role_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['role_id'], ['rbac_roles.id'], name='fk_azure_ad_group_role_mappings_role_id'),
    )
    op.create_index('ix_azure_ad_group_role_mappings_id', 'azure_ad_group_role_mappings', ['id'])
    op.create_index('ix_azure_ad_group_role_mappings_azure_ad_group_id', 'azure_ad_group_role_mappings', ['azure_ad_group_id'])


def downgrade() -> None:
    """Drop all RBAC tables."""
    
    # Drop Azure AD mappings
    op.drop_index('ix_azure_ad_group_role_mappings_azure_ad_group_id', table_name='azure_ad_group_role_mappings')
    op.drop_index('ix_azure_ad_group_role_mappings_id', table_name='azure_ad_group_role_mappings')
    op.drop_table('azure_ad_group_role_mappings')
    
    # Drop individual user access
    op.drop_index(op.f('ix_rbac_user_access_user_id'), table_name='rbac_user_access')
    op.drop_index(op.f('ix_rbac_user_access_id'), table_name='rbac_user_access')
    op.drop_table('rbac_user_access')
    
    # Drop role groups system
    op.drop_index(op.f('ix_user_role_groups_granted_by'), table_name='user_role_groups')
    op.drop_index(op.f('ix_user_role_groups_role_group_id'), table_name='user_role_groups')
    op.drop_index(op.f('ix_user_role_groups_user_id'), table_name='user_role_groups')
    op.drop_table('user_role_groups')
    
    op.drop_index(op.f('ix_role_group_services_service_id'), table_name='role_group_services')
    op.drop_index(op.f('ix_role_group_services_role_group_id'), table_name='role_group_services')
    op.drop_table('role_group_services')
    
    op.drop_index(op.f('ix_role_group_roles_role_id'), table_name='role_group_roles')
    op.drop_index(op.f('ix_role_group_roles_role_group_id'), table_name='role_group_roles')
    op.drop_table('role_group_roles')
    
    op.drop_index(op.f('ix_rbac_role_groups_name'), table_name='rbac_role_groups')
    op.drop_index(op.f('ix_rbac_role_groups_id'), table_name='rbac_role_groups')
    op.drop_table('rbac_role_groups')
    
    # Drop core RBAC tables
    op.drop_index(op.f('ix_rbac_user_sessions_session_token'), table_name='rbac_user_sessions')
    op.drop_index(op.f('ix_rbac_user_sessions_id'), table_name='rbac_user_sessions')
    op.drop_table('rbac_user_sessions')
    
    op.drop_index(op.f('ix_rbac_role_permissions_permission_id'), table_name='rbac_role_permissions')
    op.drop_index(op.f('ix_rbac_role_permissions_role_id'), table_name='rbac_role_permissions')
    op.drop_index(op.f('ix_rbac_role_permissions_id'), table_name='rbac_role_permissions')
    op.drop_table('rbac_role_permissions')
    
    op.drop_index(op.f('ix_user_specific_roles_granted_by'), table_name='user_specific_roles')
    op.drop_index(op.f('ix_user_specific_roles_role_id'), table_name='user_specific_roles')
    op.drop_index(op.f('ix_user_specific_roles_user_id'), table_name='user_specific_roles')
    op.drop_table('user_specific_roles')
    
    op.drop_index(op.f('ix_role_services_service_id'), table_name='role_services')
    op.drop_index(op.f('ix_role_services_role_id'), table_name='role_services')
    op.drop_table('role_services')
    
    op.drop_index(op.f('ix_rbac_permissions_name'), table_name='rbac_permissions')
    op.drop_index(op.f('ix_rbac_permissions_id'), table_name='rbac_permissions')
    op.drop_table('rbac_permissions')
    
    op.drop_index(op.f('ix_rbac_users_username'), table_name='rbac_users')
    op.drop_index(op.f('ix_rbac_users_email'), table_name='rbac_users')
    op.drop_index(op.f('ix_rbac_users_id'), table_name='rbac_users')
    op.drop_table('rbac_users')
    
    op.drop_index(op.f('ix_rbac_services_name'), table_name='rbac_services')
    op.drop_index(op.f('ix_rbac_services_id'), table_name='rbac_services')
    op.drop_table('rbac_services')
    
    op.drop_index(op.f('ix_rbac_roles_name'), table_name='rbac_roles')
    op.drop_index(op.f('ix_rbac_roles_level'), table_name='rbac_roles')
    op.drop_index(op.f('ix_rbac_roles_id'), table_name='rbac_roles')
    op.drop_table('rbac_roles')

