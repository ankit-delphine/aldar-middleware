"""Add RBAC pivot tables and rename services to agents

Revision ID: 0015
Revises: 0014
Create Date: 2025-01-15 00:00:00.000000

This consolidated migration performs the following operations:
1. Creates rbac_user_pivot table with UUID id and email column
   - Maps users (by email) to their Azure AD groups (updated on every login)
2. Creates rbac_agent_pivot table with UUID id and agent_ad_groups_metadata
   - Maps agents to their Azure AD groups (updated via API)
   - Includes metadata column for AD group info
3. Renames rbac_services table to rbac_agents
   - Updates all foreign key constraints in association tables
4. Removes service_type column from rbac_agents table

Access control is based on Azure AD group intersection:
- Users have a list of AD group UUIDs (synced on login)
- Agents have a list of AD group UUIDs (assigned via API)
- Access is granted if user's AD groups ∩ agent's AD groups is non-empty
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create RBAC pivot tables and rename services to agents."""
    
    # ========== PART 1: Create rbac_user_pivot table ==========
    # Stores email -> list of Azure AD group UUIDs (updated on every login)
    op.create_table(
        'rbac_user_pivot',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('azure_ad_groups', postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_rbac_user_pivot'))
    )
    op.create_index(
        op.f('ix_rbac_user_pivot_id'),
        'rbac_user_pivot',
        ['id'],
        unique=False
    )
    op.create_index(
        op.f('ix_rbac_user_pivot_email'),
        'rbac_user_pivot',
        ['email'],
        unique=True
    )
    
    # ========== PART 2: Create rbac_agent_pivot table ==========
    # Stores agent_name -> list of Azure AD group UUIDs (updated via API)
    op.create_table(
        'rbac_agent_pivot',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('agent_name', sa.String(length=255), nullable=False),
        sa.Column('azure_ad_groups', postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column(
            'agent_ad_groups_metadata',
            postgresql.JSON,
            nullable=True,
            comment='List of JSON objects with AD group metadata: [{"id": "uuid", "name": "group_name"}, ...]'
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_rbac_agent_pivot'))
    )
    op.create_index(
        op.f('ix_rbac_agent_pivot_id'),
        'rbac_agent_pivot',
        ['id'],
        unique=False
    )
    op.create_index(
        op.f('ix_rbac_agent_pivot_agent_name'),
        'rbac_agent_pivot',
        ['agent_name'],
        unique=True
    )
    
    # ========== PART 3: Rename rbac_services table to rbac_agents ==========
    op.rename_table('rbac_services', 'rbac_agents')
    
    # Update foreign key constraints in role_services association table
    op.drop_constraint(
        'fk_role_services_service_id_rbac_services',
        'role_services',
        type_='foreignkey'
    )
    op.create_foreign_key(
        'fk_role_services_service_id_rbac_agents',
        'role_services',
        'rbac_agents',
        ['service_id'],
        ['id'],
        ondelete='CASCADE'
    )
    
    # Update foreign key constraints in role_group_services association table
    op.drop_constraint(
        'fk_role_group_services_service_id_rbac_services',
        'role_group_services',
        type_='foreignkey'
    )
    op.create_foreign_key(
        'fk_role_group_services_service_id_rbac_agents',
        'role_group_services',
        'rbac_agents',
        ['service_id'],
        ['id'],
        ondelete='CASCADE'
    )
    
    # ========== PART 4: Remove service_type column from rbac_agents ==========
    # Check if column exists before dropping (in case it was already removed)
    op.execute("""
        DO $$ 
        BEGIN
            IF EXISTS (
                SELECT 1 
                FROM information_schema.columns 
                WHERE table_name='rbac_agents' AND column_name='service_type'
            ) THEN
                ALTER TABLE rbac_agents DROP COLUMN service_type;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    """Revert all RBAC pivot table changes."""
    
    # ========== PART 4 REVERT: Add service_type column back to rbac_agents ==========
    op.add_column(
        'rbac_agents',
        sa.Column('service_type', sa.String(50), nullable=False, server_default='agent')
    )
    
    # ========== PART 3 REVERT: Rename rbac_agents back to rbac_services ==========
    # Revert foreign key constraints
    op.drop_constraint(
        'fk_role_group_services_service_id_rbac_agents',
        'role_group_services',
        type_='foreignkey'
    )
    op.create_foreign_key(
        'fk_role_group_services_service_id_rbac_services',
        'role_group_services',
        'rbac_services',
        ['service_id'],
        ['id'],
        ondelete='CASCADE'
    )
    
    op.drop_constraint(
        'fk_role_services_service_id_rbac_agents',
        'role_services',
        type_='foreignkey'
    )
    op.create_foreign_key(
        'fk_role_services_service_id_rbac_services',
        'role_services',
        'rbac_services',
        ['service_id'],
        ['id'],
        ondelete='CASCADE'
    )
    
    # Rename the table back
    op.rename_table('rbac_agents', 'rbac_services')
    
    # ========== PART 2 REVERT: Drop rbac_agent_pivot table ==========
    op.drop_index(op.f('ix_rbac_agent_pivot_agent_name'), table_name='rbac_agent_pivot')
    op.drop_index(op.f('ix_rbac_agent_pivot_id'), table_name='rbac_agent_pivot')
    op.drop_table('rbac_agent_pivot')
    
    # ========== PART 1 REVERT: Drop rbac_user_pivot table ==========
    op.drop_index(op.f('ix_rbac_user_pivot_email'), table_name='rbac_user_pivot')
    op.drop_index(op.f('ix_rbac_user_pivot_id'), table_name='rbac_user_pivot')
    op.drop_table('rbac_user_pivot')

