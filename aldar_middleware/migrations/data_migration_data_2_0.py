import os
import asyncio
import json
from urllib.parse import quote_plus
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import text

# ============================================================
# DB URLS (as you defined)
# ============================================================
# 2.0 Database connection

MONOLITH_2_0_DB_USER=""
MONOLITH_2_0_DB_PASS=""
MONOLITH_2_0_DB_HOST=""
MONOLITH_2_0_DB_NAME=""

# 3.0 Database connection

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
# SOURCE QUERY (MONOLITH 2.0)
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
    '606770b4-80c2-46a8-b86d-693f74684907' AS agent_id,
    user_email AS user_id,
    jsonb_build_object(
        'session_state', '{}'::jsonb,
        'session_metrics', '{}'::jsonb,
        'session_name', session_name
    ) AS session_data,
    '{
      "name": "AiQ MCP Agent DEV",
      "agent_id": "606770b4-80c2-46a8-b86d-693f74684907"
    }'::jsonb AS agent_data,
    jsonb_agg(
        jsonb_build_object(
            'run_id', gen_random_uuid(),
            'agent_id', '606770b4-80c2-46a8-b86d-693f74684907',
            'agent_name', 'AiQ MCP Agent DEV',
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
# TARGET INSERT (CURRENT DB)
# ============================================================

CHECK_SESSION_EXISTS_QUERY = text("""
SELECT 
    session_id,
    agent_id,
    agent_data,
    runs
FROM agno_sessions 
WHERE session_id = :session_id
""")

INSERT_QUERY = text("""
INSERT INTO agno_sessions (
    session_id,
    session_type,
    agent_id,
    user_id,
    session_data,
    agent_data,
    runs,
    summary,
    created_at,
    updated_at
)
VALUES (
    :session_id,
    :session_type,
    :agent_id,
    :user_id,
    :session_data,
    :agent_data,
    :runs,
    :summary,
    :created_at,
    :updated_at
)
ON CONFLICT (session_id) DO UPDATE SET
    agent_id = EXCLUDED.agent_id,
    agent_data = EXCLUDED.agent_data,
    runs = EXCLUDED.runs,
    session_data = EXCLUDED.session_data,
    updated_at = EXCLUDED.updated_at
""")

# ============================================================
# MIGRATION LOGIC
# ============================================================

async def migrate_data():
    print("🔄 Starting migration...")
    
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
        print("ℹ️  No records to migrate")
        await source_engine.dispose()
        await target_engine.dispose()
        return

    inserted_count = 0
    updated_count = 0
    skipped_count = 0  # Already up-to-date

    print("📤 Processing data migration...")
    async with TargetSession() as target_session:
        for idx, row in enumerate(rows, 1):
            session_id = row["session_id"]
            
            # Check if session_id already exists and get current data
            check_result = await target_session.execute(
                CHECK_SESSION_EXISTS_QUERY,
                {"session_id": session_id}
            )
            existing = check_result.fetchone()
            
            # Convert dict/list fields to JSON strings for JSONB columns
            row_data = dict(row)
            new_agent_id = row_data.get('agent_id')
            
            # Prepare new data for comparison
            if isinstance(row_data.get('session_data'), dict):
                new_session_data = json.dumps(row_data['session_data'])
                row_data['session_data'] = new_session_data
            else:
                new_session_data = row_data.get('session_data')
                
            if isinstance(row_data.get('agent_data'), dict):
                new_agent_data = json.dumps(row_data['agent_data'])
                row_data['agent_data'] = new_agent_data
            else:
                new_agent_data = row_data.get('agent_data')
                
            if isinstance(row_data.get('runs'), list):
                new_runs = json.dumps(row_data['runs'])
                row_data['runs'] = new_runs
            else:
                new_runs = row_data.get('runs')
            
            # Check if data needs update
            needs_update = False
            if existing:
                # Fetch existing data
                existing_session_id, existing_agent_id, existing_agent_data, existing_runs = existing
                
                # Convert existing JSONB to string for comparison
                if existing_agent_data:
                    existing_agent_data_str = json.dumps(existing_agent_data) if isinstance(existing_agent_data, dict) else str(existing_agent_data)
                else:
                    existing_agent_data_str = None
                    
                if existing_runs:
                    existing_runs_str = json.dumps(existing_runs) if isinstance(existing_runs, (list, dict)) else str(existing_runs)
                else:
                    existing_runs_str = None
                
                # Compare agent_id, agent_data, and runs
                # Check if agent info needs update (main migration goal)
                agent_id_changed = str(existing_agent_id) != str(new_agent_id)
                agent_data_changed = existing_agent_data_str != new_agent_data
                runs_changed = existing_runs_str != new_runs
                
                needs_update = agent_id_changed or agent_data_changed or runs_changed
                
                if needs_update:
                    # Data is different - UPDATE needed
                    await target_session.execute(INSERT_QUERY, row_data)
                    updated_count += 1
                    
                    if updated_count % 10 == 0 or idx % 50 == 0:
                        changes = []
                        if agent_id_changed:
                            changes.append(f"agent_id: {existing_agent_id} → {new_agent_id}")
                        if agent_data_changed:
                            changes.append("agent_data")
                        if runs_changed:
                            changes.append("runs")
                        print(f"🔄 [{idx}/{len(rows)}] Updating session {session_id[:8]}... ({', '.join(changes)})")
                else:
                    # Data is already up-to-date - SKIP
                    skipped_count += 1
                    if skipped_count % 20 == 0 or idx % 100 == 0:
                        print(f"⏭️  [{idx}/{len(rows)}] Skipped session {session_id[:8]}... (already up-to-date)")
            else:
                # New record - INSERT
                await target_session.execute(INSERT_QUERY, row_data)
                inserted_count += 1
                
                if inserted_count % 10 == 0 or idx % 50 == 0:
                    print(f"✅ [{idx}/{len(rows)}] Inserted new session {session_id[:8]}...")

        await target_session.commit()

    await source_engine.dispose()
    await target_engine.dispose()

    print(f"\n{'='*60}")
    print(f"✅ Migration completed successfully!")
    print(f"{'='*60}")
    print(f"   📊 Summary:")
    print(f"   - ✨ Inserted (new records):        {inserted_count}")
    print(f"   - 🔄 Updated (changed records):     {updated_count}")
    print(f"   - ⏭️  Skipped (already up-to-date): {skipped_count}")
    print(f"   - 📝 Total processed:               {len(rows)}")
    print(f"{'='*60}")
    
    if skipped_count == len(rows):
        print(f"✨ Perfect! All records are already up-to-date.")
        print(f"   No database writes needed - migration was idempotent.")
    elif updated_count > 0:
        print(f"💡 {updated_count} records were updated with new agent information.")
    elif inserted_count > 0:
        print(f"🎉 {inserted_count} new records were successfully migrated!")
    
    print(f"\n🎯 No duplicates created - safe to re-run anytime!")

# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    asyncio.run(migrate_data()) 