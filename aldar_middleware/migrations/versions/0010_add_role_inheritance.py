"""Add role inheritance support for RBAC

Revision ID: 0010
Revises: 009
Create Date: 2025-11-04 15:35:00.000000

This migration adds role inheritance functionality:
- Creates role_parent_roles association table
- Enables roles to inherit services from parent roles
- Supports recursive inheritance with circular dependency prevention
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0010"
down_revision = '009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add role_parent_roles table for role inheritance.
    
    This allows roles to inherit services from parent roles, enabling:
    - Child roles automatically get all services from parent roles
    - Recursive inheritance (grandparent services included)
    - Flexible role composition and hierarchy
    """
    op.create_table(
        'role_parent_roles',
        sa.Column('parent_role_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('child_role_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['child_role_id'], ['rbac_roles.id'], name='fk_role_parent_roles_child_role_id'),
        sa.ForeignKeyConstraint(['parent_role_id'], ['rbac_roles.id'], name='fk_role_parent_roles_parent_role_id'),
        sa.PrimaryKeyConstraint('parent_role_id', 'child_role_id', name='pk_role_parent_roles')
    )
    
    # Create indexes for better query performance
    op.create_index('ix_role_parent_roles_child_role_id', 'role_parent_roles', ['child_role_id'], unique=False)
    op.create_index('ix_role_parent_roles_parent_role_id', 'role_parent_roles', ['parent_role_id'], unique=False)


def downgrade() -> None:
    """Remove role inheritance support."""
    # Drop indexes first
    op.drop_index('ix_role_parent_roles_parent_role_id', table_name='role_parent_roles')
    op.drop_index('ix_role_parent_roles_child_role_id', table_name='role_parent_roles')
    
    # Drop the table
    op.drop_table('role_parent_roles')

