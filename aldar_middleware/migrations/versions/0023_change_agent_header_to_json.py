"""Change agent_header column from TEXT to JSON type.

Revision ID: 0023
Revises: 0022
Create Date: 2025-12-24
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Change agent_header column from TEXT to JSON type."""

    # Check if column exists and is TEXT type
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT data_type
        FROM information_schema.columns
        WHERE table_name = 'agents' AND column_name = 'agent_header'
    """))
    column_info = result.fetchone()

    if column_info and column_info[0] == "text":
        # Convert TEXT to JSON
        # First, try to parse existing TEXT values as JSON
        # If they're already valid JSON strings, convert them
        # If they're plain text, wrap them in a JSON object with "value" key
        # If they're null or empty, keep as null
        op.execute("""
            ALTER TABLE agents
            ALTER COLUMN agent_header TYPE JSON
            USING CASE
                WHEN agent_header IS NULL OR agent_header = '' THEN NULL::json
                WHEN agent_header::text ~ '^[\\s]*[\\[\\{]' THEN agent_header::json
                ELSE json_build_object('value', agent_header)
            END
        """)
    elif column_info and column_info[0] != "json":
        # Column exists but is not TEXT, log a warning but try to convert anyway
        op.execute("""
            ALTER TABLE agents
            ALTER COLUMN agent_header TYPE JSON
            USING CASE
                WHEN agent_header IS NULL THEN NULL::json
                WHEN agent_header::text ~ '^[\\s]*[\\[\\{]' THEN agent_header::json
                ELSE json_build_object('value', agent_header)
            END
        """)


def downgrade() -> None:
    """Revert agent_header column from JSON back to TEXT."""

    # Convert JSON back to TEXT (as string representation)
    op.execute("""
        ALTER TABLE agents
        ALTER COLUMN agent_header TYPE TEXT
        USING CASE
            WHEN agent_header IS NULL THEN NULL
            ELSE agent_header::text
        END
    """)

