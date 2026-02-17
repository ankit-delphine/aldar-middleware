import os
import asyncio
import json
from urllib.parse import quote_plus
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import text

# ============================================================
# DB URLS
# ============================================================

MONOLITH_2_0_DB_USER=""
MONOLITH_2_0_DB_PASS=""
MONOLITH_2_0_DB_HOST=""
MONOLITH_2_0_DB_NAME=""
 
ALDAR_DB_USER=""
ALDAR_DB_PASS=""
ALDAR_DB_HOST=""
ALDAR_DB_BASE=""

# URL-encode passwords to handle special characters
MONOLITH_2_0_DB_URL = f"postgresql+asyncpg://{quote_plus(MONOLITH_2_0_DB_USER)}:" \
                     f"{quote_plus(MONOLITH_2_0_DB_PASS)}@" \
                     f"{MONOLITH_2_0_DB_HOST}:5432/" \
                     f"{MONOLITH_2_0_DB_NAME}"

CURRENT_DB_URL = f"postgresql+asyncpg://{quote_plus(ALDAR_DB_USER)}:" \
                 f"{quote_plus(ALDAR_DB_PASS)}@" \
                 f"{ALDAR_DB_HOST}:5432/" \
                 f"{ALDAR_DB_BASE}"

# ============================================================
# SOURCE QUERY WITH CORRECTED RUN STRUCTURE
# ============================================================

SOURCE_QUERY = text("""
WITH message_pairs AS (
    SELECT
        c.id AS session_id,
        u."emailAddress" AS user_email,
        c."name" AS session_name,
        c."createdAt" AS session_created_at,
        c."updatedAt" AS session_updated_at,
        m.id AS message_id,
        m."rawMessage" AS message_content,
        m."isReply" AS is_reply,
        m."createdAt" AS message_created_at,
        ROW_NUMBER() OVER (PARTITION BY c.id ORDER BY m."createdAt") AS msg_num
    FROM public."Conversation" c
    JOIN public."Message" m ON m."conversationId" = c.id
    JOIN public."User" u ON u.id = c."userId"
),
run_groups AS (
    SELECT
        session_id,
        user_email,
        session_name,
        session_created_at,
        session_updated_at,
        -- Create run groups: every pair of user+assistant messages gets same run_num
        CEILING(msg_num::decimal / 2) AS run_num,
        jsonb_agg(
            jsonb_build_object(
                'id', message_id,
                'content', message_content,
                'role', CASE WHEN is_reply = TRUE THEN 'assistant' ELSE 'user' END,
                'created_at', CAST(extract(epoch FROM message_created_at) AS INT)
            )
            ORDER BY message_created_at
        ) AS messages,
        MIN(message_content) FILTER (WHERE is_reply = FALSE) AS user_input,
        MAX(message_content) FILTER (WHERE is_reply = TRUE) AS assistant_output
    FROM message_pairs
    GROUP BY session_id, user_email, session_name, session_created_at, session_updated_at, run_num
)
SELECT
    session_id,
    'agent' AS session_type,
    'mcp-agent-AiQ MCP Agent' AS agent_id,
    user_email AS user_id,
    jsonb_build_object(
        'session_state', '{}'::jsonb,
        'session_metrics', '{}'::jsonb,
        'session_name', session_name
    ) AS session_data,
    '{
      "name": "AiQ MCP Agent",
      "agent_id": "mcp-agent-AiQ MCP Agent"
    }'::jsonb AS agent_data,
    jsonb_agg(
        jsonb_build_object(
            'run_id', gen_random_uuid(),
            'agent_id', 'mcp-agent-AiQ MCP Agent',
            'agent_name', 'AiQ MCP Agent',
            'session_id', session_id,
            'user_id', user_email,
            'content', assistant_output,
            'content_type', 'str',
            'model', 'ADQ-AIQ-PTU-4.1-uaenorth-nonprod',
            'model_provider', 'Azure',
            'metrics', '{}'::jsonb,
            'session_state', '{}'::jsonb,
            'status', 'COMPLETED',
            'input', json_build_object('input_content', user_input),
            'messages', messages
        )
        ORDER BY run_num
    ) AS runs,
    NULL AS summary,
    CAST(extract(epoch FROM session_created_at) AS INT) AS created_at,
    CAST(extract(epoch FROM session_updated_at) AS INT) AS updated_at
FROM run_groups
GROUP BY session_id, user_email, session_name, session_created_at, session_updated_at
ORDER BY session_created_at DESC
""")

# ============================================================
# CHECK IF SESSION EXISTS
# ============================================================

CHECK_SESSION_EXISTS_QUERY = text("""
SELECT 1 FROM agno_sessions WHERE session_id = :session_id
""")

# ============================================================
# UPDATE QUERY - Only updates runs column
# ============================================================

UPDATE_QUERY = text("""
UPDATE agno_sessions 
SET 
    runs = :runs,
    updated_at = :updated_at
WHERE session_id = :session_id
""")

# ============================================================
# MIGRATION LOGIC
# ============================================================

async def update_sessions():
    print("🔄 Starting UPDATE migration (No Delete)...")
    print("ℹ️  This will ONLY UPDATE existing sessions, not delete anything")
    
    source_engine = create_async_engine(MONOLITH_2_0_DB_URL)
    target_engine = create_async_engine(CURRENT_DB_URL)

    SourceSession = sessionmaker(source_engine, class_=AsyncSession)
    TargetSession = sessionmaker(target_engine, class_=AsyncSession)

    print("📥 Fetching data from source database...")
    try:
        async with SourceSession() as source_session:
            result = await source_session.execute(SOURCE_QUERY)
            rows = result.mappings().all()
    except Exception as e:
        print(f"\n❌ Failed to connect to source database!")
        print(f"   Error: {e}")
        print(f"\n   Host: {MONOLITH_2_0_DB_HOST}")
        print(f"   Database: {MONOLITH_2_0_DB_NAME}")
        print(f"   User: {MONOLITH_2_0_DB_USER}")
        print("\n⚠️  Common issues:")
        print("   1. NOT CONNECTED TO VPN - Most likely cause!")
        print("   2. IP address not whitelisted in Azure PostgreSQL firewall")
        print("   3. Incorrect hostname or database credentials")
        print("   4. Network/DNS issues")
        print("\n💡 Try: Connect to your organization's VPN and run again")
        await source_engine.dispose()
        await target_engine.dispose()
        raise
    
    print(f"✅ Retrieved {len(rows)} records from source")

    if not rows:
        print("ℹ️  No records to process")
        await source_engine.dispose()
        await target_engine.dispose()
        return

    updated_count = 0
    skipped_count = 0
    not_found_count = 0

    print("📤 Updating existing sessions in target database...")
    async with TargetSession() as target_session:
        for idx, row in enumerate(rows, 1):
            session_id = row["session_id"]
            
            # Check if session exists in target database
            check_result = await target_session.execute(
                CHECK_SESSION_EXISTS_QUERY,
                {"session_id": session_id}
            )
            exists = check_result.scalar() is not None

            if not exists:
                not_found_count += 1
                if not_found_count <= 5:  # Only show first 5
                    print(f"⚠️  [{idx}/{len(rows)}] Session not found: {session_id} (skipping)")
                continue

            # Convert runs to JSON string
            row_data = dict(row)
            runs = row_data.get('runs')
            
            if isinstance(runs, list):
                old_runs_count = 1  # Assume old structure had 1 run
                new_runs_count = len(runs)
                
                # Only update if structure changed (more than 1 run)
                if new_runs_count == 1:
                    skipped_count += 1
                    continue
                
                runs_json = json.dumps(runs)
                
                # Update only the runs column
                await target_session.execute(
                    UPDATE_QUERY,
                    {
                        "session_id": session_id,
                        "runs": runs_json,
                        "updated_at": row_data.get('updated_at')
                    }
                )
                updated_count += 1
                
                if updated_count <= 10 or updated_count % 100 == 0:
                    print(f"✅ [{idx}/{len(rows)}] Updated session {session_id}: {old_runs_count} run → {new_runs_count} runs")
            else:
                skipped_count += 1

        await target_session.commit()

    await source_engine.dispose()
    await target_engine.dispose()

    print(f"\n✅ Migration completed:")
    print(f"   - Updated: {updated_count} sessions (runs structure fixed)")
    print(f"   - Skipped: {skipped_count} sessions (already correct)")
    print(f"   - Not Found: {not_found_count} sessions (not in target DB)")
    print(f"   - Total processed: {len(rows)} records")
    print(f"\n💡 All existing sessions preserved - only 'runs' column updated!")

# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    asyncio.run(update_sessions())
