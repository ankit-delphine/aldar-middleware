"""add_admin_config_table

Revision ID: 0022
Revises: 0021
Create Date: 2025-12-24

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Check if table exists (might have been created with TEXT column)
    # If it exists, alter the column; if not, create fresh
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'admin_config')"
    ))
    table_exists = result.scalar()
    
    if table_exists:
        # Alter existing column from TEXT to JSONB
        op.execute("ALTER TABLE admin_config ALTER COLUMN value TYPE JSONB USING value::jsonb")
    else:
        # Create new table with JSONB
        op.create_table('admin_config',
            sa.Column('id', sa.UUID(), nullable=False),
            sa.Column('key', sa.String(length=255), nullable=False),
            sa.Column('value', postgresql.JSONB(), nullable=True),
            sa.Column('created_by', sa.UUID(), nullable=True),
            sa.Column('updated_by', sa.UUID(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_admin_config_created_by_users'),
            sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name='fk_admin_config_updated_by_users'),
            sa.PrimaryKeyConstraint('id', name='pk_admin_config')
        )
        op.create_index('ix_admin_config_key', 'admin_config', ['key'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_admin_config_key', table_name='admin_config')
    op.drop_table('admin_config')
