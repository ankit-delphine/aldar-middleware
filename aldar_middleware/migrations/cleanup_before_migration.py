#!/usr/bin/env python3
"""
Cleanup script to delete data from target database before running migration.

This script deletes data from:
- attachments (related to sessions/messages being deleted)
- messages
- sessions
- agno_sessions

Run this before executing data_migration_2_0.py to ensure a clean migration.

Usage:
    python -m aldar_middleware.migrations.cleanup_before_migration
    # OR directly:
    python aldar_middleware/migrations/cleanup_before_migration.py

Note on Windows/Azure Private Link:
    This script uses psycopg (async psycopg2) instead of asyncpg because asyncpg has
    known DNS resolution issues on Windows with Azure Private Link endpoints.
    
    Dependencies:
    - psycopg[binary] or psycopg[c] must be installed
    - Install with: pip install psycopg[binary]
"""

import asyncio
import logging
import sys
import os
import selectors
from urllib.parse import quote_plus

# Add project root to path if running as module
if __name__ == "__main__":
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, project_root)

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database connection settings
CURRENT_DB_HOST = os.getenv("ALDAR_DB_HOST", "psql-aio-adq-dev-uaen.postgres.database.azure.com")
CURRENT_DB_PORT = int(os.getenv("ALDAR_DB_PORT", "5432"))
CURRENT_DB_USER = os.getenv("ALDAR_DB_USER", "psqladmin")
CURRENT_DB_PASS = os.getenv("ALDAR_DB_PASS", "5L7V>Bv<r_Cf1#G!RkmfxlHR")
CURRENT_DB_NAME = os.getenv("ALDAR_DB_BASE", "maindb")

# URL-encode credentials to handle special characters
encoded_user = quote_plus(CURRENT_DB_USER)
encoded_password = quote_plus(CURRENT_DB_PASS)

# Use psycopg for Windows/Azure Private Link compatibility
CURRENT_DB_URL = f"postgresql+psycopg://{encoded_user}:{encoded_password}@{CURRENT_DB_HOST}:{CURRENT_DB_PORT}/{CURRENT_DB_NAME}?application_name=cleanup_before_migration&sslmode=require"


async def get_table_count(session: AsyncSession, table_name: str) -> int:
    """Get count of rows in a table."""
    try:
        result = await session.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
        count = result.scalar()
        return count or 0
    except Exception as e:
        logger.warning(f"Could not get count for {table_name}: {e}")
        return 0


async def confirm_deletion(session: AsyncSession) -> bool:
    """Show what will be deleted and ask for confirmation."""
    logger.info("\n" + "=" * 60)
    logger.info("DELETION PREVIEW")
    logger.info("=" * 60)
    
    tables = [
        ("attachments", "Attachments"),
        ("messages", "Messages"),
        ("sessions", "Sessions"),
        ("agno_sessions", "Agno Sessions"),
        ("starter_prompts", "Starter Prompts"),
    ]
    
    total_rows = 0
    for table_name, display_name in tables:
        count = await get_table_count(session, table_name)
        logger.info(f"  {display_name}: {count:,} rows")
        total_rows += count
    
    logger.info("=" * 60)
    logger.info(f"TOTAL: {total_rows:,} rows will be PERMANENTLY DELETED")
    logger.info("=" * 60)
    
    if total_rows == 0:
        logger.info("\nNo data to delete. Tables are already empty.")
        return False
    
    logger.warning("\n⚠️  WARNING: This action is IRREVERSIBLE!")
    logger.warning("⚠️  All data in the above tables will be PERMANENTLY DELETED!")
    
    response = input("\nType 'DELETE' to confirm deletion, or anything else to cancel: ")
    
    return response.strip() == "DELETE"


async def delete_attachments_for_sessions(session: AsyncSession) -> int:
    """Delete attachments that reference sessions or messages that will be deleted."""
    try:
        # Delete attachments where entity_type = 'session' or entity_type = 'message'
        # This handles both session-level and message-level attachments
        result = await session.execute(
            text("""
                DELETE FROM attachments 
                WHERE entity_type IN ('session', 'message')
                OR message_id IS NOT NULL
            """)
        )
        count = result.rowcount
        await session.commit()
        logger.info(f"  ✓ Deleted {count:,} attachments")
        return count
    except Exception as e:
        logger.error(f"  ✗ Error deleting attachments: {e}")
        await session.rollback()
        raise


async def delete_messages(session: AsyncSession) -> int:
    """Delete all messages."""
    try:
        result = await session.execute(text("DELETE FROM messages"))
        count = result.rowcount
        await session.commit()
        logger.info(f"  ✓ Deleted {count:,} messages")
        return count
    except Exception as e:
        logger.error(f"  ✗ Error deleting messages: {e}")
        await session.rollback()
        raise


async def delete_sessions(session: AsyncSession) -> int:
    """Delete all sessions."""
    try:
        result = await session.execute(text("DELETE FROM sessions"))
        count = result.rowcount
        await session.commit()
        logger.info(f"  ✓ Deleted {count:,} sessions")
        return count
    except Exception as e:
        logger.error(f"  ✗ Error deleting sessions: {e}")
        await session.rollback()
        raise


async def delete_agno_sessions(session: AsyncSession) -> int:
    """Delete all agno_sessions."""
    try:
        result = await session.execute(text("DELETE FROM agno_sessions"))
        count = result.rowcount
        await session.commit()
        logger.info(f"  ✓ Deleted {count:,} agno_sessions")
        return count
    except Exception as e:
        logger.error(f"  ✗ Error deleting agno_sessions: {e}")
        await session.rollback()
        raise


async def delete_starter_prompts(session: AsyncSession) -> int:
    """Delete all starter_prompts."""
    try:
        result = await session.execute(text("DELETE FROM starter_prompts"))
        count = result.rowcount
        await session.commit()
        logger.info(f"  ✓ Deleted {count:,} starter_prompts")
        return count
    except Exception as e:
        logger.error(f"  ✗ Error deleting starter_prompts: {e}")
        await session.rollback()
        raise


async def main():
    """Main cleanup function."""
    logger.info("=" * 60)
    logger.info("DATABASE CLEANUP BEFORE MIGRATION")
    logger.info("=" * 60)
    logger.info(f"Target DB: {CURRENT_DB_NAME} @ {CURRENT_DB_HOST}")
    logger.info("=" * 60)
    
    logger.info("\nConnecting to database...")
    
    try:
        engine = create_async_engine(
            CURRENT_DB_URL,
            echo=False,
            connect_args={"connect_timeout": 10},
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_reset_on_return='commit'
        )
        
        # Test connection
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        
        logger.info("✓ Successfully connected to database")
        
    except Exception as e:
        logger.error(f"✗ Failed to connect to database: {e}")
        logger.error("\nPlease check:")
        logger.error("  1. VPN connection (if required)")
        logger.error("  2. Database credentials")
        logger.error("  3. Network connectivity")
        sys.exit(1)
    
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    try:
        async with session_maker() as session:
            # Show what will be deleted and get confirmation
            if not await confirm_deletion(session):
                logger.info("\nCleanup cancelled by user.")
                return
            
            logger.info("\n" + "=" * 60)
            logger.info("Starting deletion process...")
            logger.info("=" * 60)
            
            # Delete in correct order to respect foreign key constraints
            
            # Step 1: Delete attachments
            logger.info("\nStep 1: Deleting attachments...")
            attachments_count = await delete_attachments_for_sessions(session)
            
            # Step 2: Delete messages
            logger.info("\nStep 2: Deleting messages...")
            messages_count = await delete_messages(session)
            
            # Step 3: Delete sessions
            logger.info("\nStep 3: Deleting sessions...")
            sessions_count = await delete_sessions(session)
            
            # Step 4: Delete agno_sessions
            logger.info("\nStep 4: Deleting agno_sessions...")
            agno_sessions_count = await delete_agno_sessions(session)
            
            # Step 5: Delete starter_prompts
            logger.info("\nStep 5: Deleting starter_prompts...")
            starter_prompts_count = await delete_starter_prompts(session)
            
            # Summary
            total_deleted = attachments_count + messages_count + sessions_count + agno_sessions_count + starter_prompts_count
            
            logger.info("\n" + "=" * 60)
            logger.info("CLEANUP COMPLETED SUCCESSFULLY!")
            logger.info("=" * 60)
            logger.info(f"  Attachments:     {attachments_count:,} deleted")
            logger.info(f"  Messages:        {messages_count:,} deleted")
            logger.info(f"  Sessions:        {sessions_count:,} deleted")
            logger.info(f"  Agno Sessions:   {agno_sessions_count:,} deleted")
            logger.info(f"  Starter Prompts: {starter_prompts_count:,} deleted")
            logger.info("=" * 60)
            logger.info(f"  TOTAL:          {total_deleted:,} rows deleted")
            logger.info("=" * 60)
            logger.info("\nYou can now run the migration script:")
            logger.info("  python -m aldar_middleware.migrations.data_migration_2_0")
            logger.info("=" * 60)
            
    except Exception as e:
        logger.error(f"\n✗ Cleanup failed: {e}")
        logger.error("\nThe database may be in an inconsistent state.")
        logger.error("Please review the error and try again.")
        sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    # On Windows, psycopg requires SelectorEventLoop instead of the default ProactorEventLoop
    if sys.platform == "win32":
        selector = selectors.SelectSelector()
        loop = asyncio.SelectorEventLoop(selector)
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(main())
        finally:
            loop.close()
    else:
        asyncio.run(main())
