#!/bin/bash
# Script to restore 2.0 dump and run data migration
# 
# This is a convenience script for LOCAL DEVELOPMENT ONLY.
# For staging/production, use the manual steps in MIGRATION_2_0_GUIDE.md
#
# Usage:
#   # From project root:
#   ./aldar_middleware/migrations/migrate_2_0_data.sh [DUMP_FILE_PATH]
#
#   # OR from migrations folder:
#   cd aldar_middleware/migrations
#   ./migrate_2_0_data.sh [DUMP_FILE_PATH]
#
# Example:
#   ./aldar_middleware/migrations/migrate_2_0_data.sh /Users/groot/Desktop/dump-monolith.sql

set -e

# Configuration (can be overridden by environment variables)
DUMP_FILE="${1:-${DUMP_FILE:-/Users/groot/Desktop/dump-monolith.sql}}"
TEMP_DB="${MONOLITH_2_0_DB_NAME:-monolith_2_0_temp}"
DB_USER="${MONOLITH_2_0_DB_USER:-groot}"
DB_HOST="${MONOLITH_2_0_DB_HOST:-localhost}"
DB_PORT="${MONOLITH_2_0_DB_PORT:-5432}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=========================================="
echo "2.0 Data Migration Setup (LOCAL DEV ONLY)"
echo "=========================================="
echo ""
echo "Dump file: $DUMP_FILE"
echo "Temp DB: $TEMP_DB"
echo "Host: $DB_HOST:$DB_PORT"
echo ""

# Check if dump file exists
if [ ! -f "$DUMP_FILE" ]; then
    echo -e "${RED}Error: Dump file not found: $DUMP_FILE${NC}"
    echo "Usage: $0 [DUMP_FILE_PATH]"
    exit 1
fi

# Step 1: Create temporary database
echo -e "${GREEN}Step 1: Creating temporary database '$TEMP_DB'...${NC}"
createdb -h $DB_HOST -p $DB_PORT -U $DB_USER $TEMP_DB 2>/dev/null && echo "Database created" || echo "Database already exists or error creating"

# Step 2: Restore dump
echo -e "${GREEN}Step 2: Restoring 2.0 dump to temporary database...${NC}"
echo "This may take a few minutes..."

# Detect dump format and use appropriate restore command
if file "$DUMP_FILE" | grep -q "PostgreSQL custom database dump"; then
    # Custom format dump - use pg_restore
    echo "Detected custom format dump, using pg_restore..."
    pg_restore -h $DB_HOST -p $DB_PORT -U $DB_USER -d $TEMP_DB -v "$DUMP_FILE" 2>&1 | grep -v "error: could not execute query" || true
    echo "Dump restored (some permission errors are normal and can be ignored)"
else
    # SQL format dump - use psql
    echo "Detected SQL format dump, using psql..."
    psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $TEMP_DB -f "$DUMP_FILE" > /dev/null 2>&1 || {
        echo -e "${YELLOW}Warning: Some errors during restore (this may be normal)${NC}"
    }
fi

# Step 3: Verify restore
echo ""
echo -e "${GREEN}Step 3: Verifying restore...${NC}"
USER_COUNT=$(psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $TEMP_DB -t -c 'SELECT COUNT(*) FROM "User";' 2>/dev/null | xargs)
CONV_COUNT=$(psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $TEMP_DB -t -c 'SELECT COUNT(*) FROM "Conversation";' 2>/dev/null | xargs)

if [ -n "$USER_COUNT" ] && [ -n "$CONV_COUNT" ]; then
    echo "✓ Found $USER_COUNT users and $CONV_COUNT conversations"
else
    echo -e "${YELLOW}Warning: Could not verify restore. Continuing anyway...${NC}"
fi

# Step 4: Run data migration
echo ""
echo -e "${GREEN}Step 4: Running data migration script...${NC}"
echo "Make sure environment variables are set if needed (see MIGRATION_2_0_GUIDE.md)"
echo ""

# Activate virtual environment if it exists
# Try from current directory, then from project root
if [ -d "venv" ]; then
    echo "Activating virtual environment..."
    source venv/bin/activate
elif [ -d "../../venv" ]; then
    echo "Activating virtual environment from project root..."
    source ../../venv/bin/activate
fi

# Run migration script (works from any directory)
python -m aldar_middleware.migrations.data_migration_2_0

MIGRATION_EXIT_CODE=$?

if [ $MIGRATION_EXIT_CODE -eq 0 ]; then
    echo ""
    echo -e "${GREEN}=========================================="
    echo "Migration completed successfully!"
    echo "==========================================${NC}"
    echo ""
    echo "To clean up, drop the temporary database:"
    echo "  dropdb -h $DB_HOST -p $DB_PORT -U $DB_USER $TEMP_DB"
else
    echo ""
    echo -e "${RED}=========================================="
    echo "Migration failed with exit code: $MIGRATION_EXIT_CODE"
    echo "==========================================${NC}"
    echo ""
    echo "Check the error messages above for details."
    exit $MIGRATION_EXIT_CODE
fi

