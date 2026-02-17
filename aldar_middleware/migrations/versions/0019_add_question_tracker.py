"""Add user question tracker table

Revision ID: 0019
Revises: 0018
Create Date: 2025-12-16

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create user_question_tracker table with indexes."""
    
    # Create user_question_tracker table
    op.create_table(
        "user_question_tracker",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("minimum_threshold", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("maximum_threshold", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_user_question_tracker_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_question_tracker")),
        sa.UniqueConstraint("user_id", "year", "month", name="uq_user_question_tracker_user_month")
    )
    
    # Create index for time-based queries across all users
    # Note: user_id index is provided by the unique constraint
    op.create_index(
        "idx_user_question_tracker_year_month",
        "user_question_tracker",
        ["year", "month"],
        unique=False
    )


def downgrade() -> None:
    """Drop user_question_tracker table and indexes."""
    
    # Drop index
    op.drop_index("idx_user_question_tracker_year_month", table_name="user_question_tracker")
    
    # Drop table (unique constraint index will be dropped automatically)
    op.drop_table("user_question_tracker")
