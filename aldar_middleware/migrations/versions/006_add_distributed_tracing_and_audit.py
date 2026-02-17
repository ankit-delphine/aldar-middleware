"""Add distributed tracing and audit tables for

Revision ID: 006
Revises: 005_add_rate_limiting_and_quotas
Create Date: 2025-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create distributed tracing and audit tables (no guards)."""
    # Enums (idempotent)
    trace_status = postgresql.ENUM('pending', 'success', 'error', 'timeout', 'partial', name='trace_status_type', create_type=True)
    trace_status.create(bind=op.get_bind(), checkfirst=True)

    trace_sample = postgresql.ENUM('full', 'partial', 'minimal', name='trace_sample_type', create_type=True)
    trace_sample.create(bind=op.get_bind(), checkfirst=True)

    # distributed_traces
    op.create_table(
        'distributed_traces',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column('correlation_id', sa.String(36), nullable=False, unique=True),
        sa.Column('trace_id', sa.String(32), nullable=False, unique=True),
        sa.Column('parent_span_id', sa.String(16), nullable=True),
        sa.Column('span_id', sa.String(16), nullable=False),
        sa.Column('user_id', sa.String(255), nullable=True),
        sa.Column('request_method', sa.String(10), nullable=False),
        sa.Column('request_path', sa.String(2048), nullable=False),
        sa.Column('request_endpoint', sa.String(255), nullable=True),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('status', postgresql.ENUM(name='trace_status_type', create_type=False), nullable=False, server_default='pending'),
        sa.Column('http_status_code', sa.Integer(), nullable=True),
        sa.Column('error_type', sa.String(255), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('agent_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('database_query_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_agent_time_ms', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_query_time_ms', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('trace_metadata', postgresql.JSONB(), nullable=True),
        sa.Column('sampled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('sample_type', postgresql.ENUM(name='trace_sample_type', create_type=False), nullable=False, server_default='full'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('idx_distributed_traces_correlation_id', 'distributed_traces', ['correlation_id'])
    op.create_index('idx_distributed_traces_trace_id', 'distributed_traces', ['trace_id'])
    op.create_index('idx_distributed_traces_user_id_created_at', 'distributed_traces', ['user_id', 'created_at'])
    op.create_index('idx_distributed_traces_status_created_at', 'distributed_traces', ['status', 'created_at'])
    op.create_index('idx_distributed_traces_created_at', 'distributed_traces', ['created_at'])

    # request_response_audits
    op.create_table(
        'request_response_audits',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column('trace_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('correlation_id', sa.String(36), nullable=False),
        sa.Column('request_timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('request_method', sa.String(10), nullable=False),
        sa.Column('request_path', sa.String(2048), nullable=False),
        sa.Column('request_headers', postgresql.JSONB(), nullable=True),
        sa.Column('request_body', sa.Text(), nullable=True),
        sa.Column('request_body_size_bytes', sa.Integer(), nullable=True),
        sa.Column('request_body_truncated', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('response_timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('response_status_code', sa.Integer(), nullable=False),
        sa.Column('response_headers', postgresql.JSONB(), nullable=True),
        sa.Column('response_body', sa.Text(), nullable=True),
        sa.Column('response_body_size_bytes', sa.Integer(), nullable=True),
        sa.Column('response_body_truncated', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('response_time_ms', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.String(255), nullable=True),
        sa.Column('client_ip', sa.String(45), nullable=True),
        sa.Column('pii_masked', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('masking_applied', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['trace_id'], ['distributed_traces.id'], ondelete='CASCADE', name='fk_trace_id'),
    )
    op.create_index('idx_request_response_audits_trace_id', 'request_response_audits', ['trace_id'])
    op.create_index('idx_request_response_audits_correlation_id', 'request_response_audits', ['correlation_id'])
    op.create_index('idx_request_response_audits_user_id_created_at', 'request_response_audits', ['user_id', 'created_at'])
    op.create_index('idx_request_response_audits_status_code_created_at', 'request_response_audits', ['response_status_code', 'created_at'])
    op.create_index('idx_request_response_audits_created_at', 'request_response_audits', ['created_at'])

    # database_query_traces
    op.create_table(
        'database_query_traces',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column('trace_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('correlation_id', sa.String(36), nullable=False),
        sa.Column('query_sql', sa.Text(), nullable=False),
        sa.Column('query_type', sa.String(20), nullable=False),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('rows_affected', sa.Integer(), nullable=True),
        sa.Column('rows_returned', sa.Integer(), nullable=True),
        sa.Column('slow_query', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('slow_threshold_ms', sa.Integer(), nullable=True),
        sa.Column('caller_file', sa.String(255), nullable=True),
        sa.Column('caller_function', sa.String(255), nullable=True),
        sa.Column('caller_line', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='success'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['trace_id'], ['distributed_traces.id'], ondelete='CASCADE', name='fk_db_trace_id'),
    )
    op.create_index('idx_database_query_traces_trace_id', 'database_query_traces', ['trace_id'])
    op.create_index('idx_database_query_traces_correlation_id', 'database_query_traces', ['correlation_id'])
    op.create_index('idx_database_query_traces_slow_query', 'database_query_traces', ['slow_query', 'created_at'])
    op.create_index('idx_database_query_traces_duration_ms', 'database_query_traces', ['duration_ms'])
    op.create_index('idx_database_query_traces_created_at', 'database_query_traces', ['created_at'])


def downgrade() -> None:
    """Drop distributed tracing and audit tables and enums."""
    op.drop_table('database_query_traces')
    op.drop_table('request_response_audits')
    op.drop_table('distributed_traces')
    
    postgresql.ENUM(name='trace_sample_type').drop(bind=op.get_bind(), checkfirst=True)
    postgresql.ENUM(name='trace_status_type').drop(bind=op.get_bind(), checkfirst=True)