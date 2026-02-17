"""Add feedback system tables

Revision ID: 001
Revises: 000
Create Date: 2025-01-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001'
down_revision = '000'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create feedback system tables (no guards)."""
    # Create enums (idempotent via checkfirst)
    feedback_entity_type_enum = postgresql.ENUM(
        'session', 'chat', 'response', 'agent', 'application', 'final_response',
        name='feedback_entity_type',
        create_type=True,
    )
    feedback_entity_type_enum.create(bind=op.get_bind(), checkfirst=True)
    
    feedback_rating_enum = postgresql.ENUM(
        'thumbs_up', 'thumbs_down', 'neutral',
        name='feedback_rating',
        create_type=True,
    )
    feedback_rating_enum.create(bind=op.get_bind(), checkfirst=True)

    # Create feedback_data table
    op.create_table(
        'feedback_data',
        sa.Column('feedback_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', sa.String(255), nullable=False),
        sa.Column('user_email', sa.String(255), nullable=True),
        sa.Column('entity_id', sa.String(255), nullable=False),
        sa.Column('entity_type', postgresql.ENUM(name='feedback_entity_type', create_type=False), nullable=False),
        sa.Column('agent_id', sa.String(255), nullable=True),
        sa.Column('rating', postgresql.ENUM(name='feedback_rating', create_type=False), nullable=False),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('metadata_json', postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('correlation_id', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('feedback_id', name=op.f('pk_feedback_data'))
    )

    # Create indexes for feedback_data
    op.create_index('ix_feedback_data_feedback_id', 'feedback_data', ['feedback_id'], unique=False)
    op.create_index('ix_feedback_data_user_id', 'feedback_data', ['user_id'], unique=False)
    op.create_index('ix_feedback_data_entity_id', 'feedback_data', ['entity_id'], unique=False)
    op.create_index('ix_feedback_data_entity_type', 'feedback_data', ['entity_type'], unique=False)
    op.create_index('ix_feedback_data_agent_id', 'feedback_data', ['agent_id'], unique=False)
    op.create_index('ix_feedback_data_correlation_id', 'feedback_data', ['correlation_id'], unique=False)
    op.create_index('ix_feedback_data_created_at', 'feedback_data', ['created_at'], unique=False)
    op.create_index('ix_feedback_data_deleted_at', 'feedback_data', ['deleted_at'], unique=False)
    
    # Composite indexes
    op.create_index('ix_feedback_user_entity', 'feedback_data', ['user_id', 'entity_id'], unique=False)
    op.create_index('ix_feedback_type_date', 'feedback_data', ['entity_type', 'created_at'], unique=False)
    op.create_index('ix_feedback_user_date', 'feedback_data', ['user_id', 'created_at'], unique=False)

    # Create feedback_files table
    op.create_table(
        'feedback_files',
        sa.Column('file_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('feedback_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('file_name', sa.String(500), nullable=False),
        sa.Column('file_url', sa.Text(), nullable=False),
        sa.Column('file_size', sa.BigInteger(), nullable=True),
        sa.Column('content_type', sa.String(100), nullable=True),
        sa.Column('blob_name', sa.String(500), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['feedback_id'], ['feedback_data.feedback_id'], name=op.f('fk_feedback_files_feedback_id_feedback_data'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('file_id', name=op.f('pk_feedback_files'))
    )

    # Create indexes for feedback_files
    op.create_index('ix_feedback_files_file_id', 'feedback_files', ['file_id'], unique=False)
    op.create_index('ix_feedback_files_feedback_id', 'feedback_files', ['feedback_id'], unique=False)
    op.create_index('uq_feedback_files_blob_name', 'feedback_files', ['blob_name'], unique=True)


def downgrade() -> None:
    """Drop feedback system tables."""
    # Drop tables
    op.drop_table('feedback_files')
    op.drop_table('feedback_data')

    # Drop enums
    postgresql.ENUM(name='feedback_rating').drop(bind=op.get_bind(), checkfirst=True)
    postgresql.ENUM(name='feedback_entity_type').drop(bind=op.get_bind(), checkfirst=True)