#!/usr/bin/env python3
"""
Fix attachment linkages after migration.

This script updates attachments to ensure they're linked to the correct messages
based on the actual message content and timestamps.

Usage:
    python -m aldar_middleware.migrations.fix_attachment_linkage
"""

import asyncio
import logging
import sys
import os
from datetime import datetime
from typing import Dict, List, Any
from urllib.parse import quote_plus

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

# Database connection
CURRENT_DB_HOST = os.getenv("ALDAR_DB_HOST", "psql-aio-adq-dev-uaen.postgres.database.azure.com")
CURRENT_DB_PORT = int(os.getenv("ALDAR_DB_PORT", "5432"))
CURRENT_DB_USER = os.getenv("ALDAR_DB_USER", "psqladmin")
CURRENT_DB_PASS = os.getenv("ALDAR_DB_PASS", "5L7V>Bv<r_Cf1#G!RkmfxlHR")
CURRENT_DB_NAME = os.getenv("ALDAR_DB_BASE", "maindb")

encoded_user = quote_plus(CURRENT_DB_USER)
encoded_password = quote_plus(CURRENT_DB_PASS)
CURRENT_DB_URL = f"postgresql+psycopg://{encoded_user}:{encoded_password}@{CURRENT_DB_HOST}:{CURRENT_DB_PORT}/{CURRENT_DB_NAME}?application_name=fix_attachment_linkage&sslmode=require"


async def fix_orphaned_attachments(session: AsyncSession):
    """
    Fix attachments that are linked to wrong messages.
    
    Strategy:
    1. Find all attachments with entity_type='message'
    2. For each attachment, find the correct message by:
       - Matching timestamp proximity (within 1 second)
       - Matching session context
       - Matching user_id
    3. Update the attachment's message_id to the correct message
    """
    try:
        # Get all attachments with their current linkage info
        result = await session.execute(text("""
            SELECT 
                a.id as attachment_id,
                a.message_id as current_message_id,
                a.user_id,
                a.file_name,
                a.created_at as attachment_created_at,
                m.id as message_exists,
                m.session_id as current_session_id,
                m.role as current_message_role,
                m.created_at as current_message_created_at
            FROM attachments a
            LEFT JOIN messages m ON a.message_id = m.id
            WHERE a.entity_type = 'message' AND a.is_active = true
            ORDER BY a.created_at DESC
        """))
        
        attachments = result.fetchall()
        logger.info(f"Found {len(attachments)} active attachments to check")
        
        fixed_count = 0
        issue_count = 0
        
        for att in attachments:
            attachment_id = att[0]
            current_message_id = att[1]
            user_id = att[2]
            file_name = att[3]
            attachment_created_at = att[4]
            message_exists = att[5]
            current_session_id = att[6]
            current_message_role = att[7]
            current_message_created_at = att[8]
            
            # If message doesn't exist, we have an orphaned attachment
            if not message_exists:
                issue_count += 1
                logger.warning(f"Orphaned attachment: {file_name} (id: {attachment_id}) - message_id {current_message_id} doesn't exist")
                
                # Try to find the correct message by timestamp and user
                find_result = await session.execute(text("""
                    SELECT id, session_id, created_at, sent_at, role
                    FROM messages
                    WHERE user_id = :user_id
                      AND role = 'user'
                      AND ABS(EXTRACT(EPOCH FROM (created_at - :attachment_created_at))) < 5
                    ORDER BY ABS(EXTRACT(EPOCH FROM (created_at - :attachment_created_at)))
                    LIMIT 1
                """), {
                    "user_id": user_id,
                    "attachment_created_at": attachment_created_at
                })
                
                correct_message = find_result.fetchone()
                
                if correct_message:
                    correct_message_id = correct_message[0]
                    logger.info(f"  Found likely correct message: {correct_message_id}")
                    
                    # Update attachment
                    await session.execute(text("""
                        UPDATE attachments
                        SET message_id = :correct_message_id,
                            entity_id = :correct_message_id_str,
                            updated_at = NOW()
                        WHERE id = :attachment_id
                    """), {
                        "correct_message_id": correct_message_id,
                        "correct_message_id_str": str(correct_message_id),
                        "attachment_id": attachment_id
                    })
                    
                    await session.commit()
                    fixed_count += 1
                    logger.info(f"  ✓ Fixed: {file_name} now linked to message {correct_message_id}")
                else:
                    logger.warning(f"  ✗ Could not find matching message for {file_name}")
            else:
                # Message exists, but let's verify it's a user message
                # (attachments should be on user messages, not assistant messages)
                if current_message_role != 'user':
                    issue_count += 1
                    logger.warning(f"Attachment on non-user message: {file_name} is on {current_message_role} message")
                    
                    # Find the user message in the same session around the same time
                    find_result = await session.execute(text("""
                        SELECT id, created_at, sent_at
                        FROM messages
                        WHERE session_id = :session_id
                          AND role = 'user'
                          AND ABS(EXTRACT(EPOCH FROM (created_at - :attachment_created_at))) < 5
                        ORDER BY ABS(EXTRACT(EPOCH FROM (created_at - :attachment_created_at)))
                        LIMIT 1
                    """), {
                        "session_id": current_session_id,
                        "attachment_created_at": attachment_created_at
                    })
                    
                    correct_message = find_result.fetchone()
                    
                    if correct_message:
                        correct_message_id = correct_message[0]
                        logger.info(f"  Found correct user message: {correct_message_id}")
                        
                        # Update attachment
                        await session.execute(text("""
                            UPDATE attachments
                            SET message_id = :correct_message_id,
                                entity_id = :correct_message_id_str,
                                updated_at = NOW()
                            WHERE id = :attachment_id
                        """), {
                            "correct_message_id": correct_message_id,
                            "correct_message_id_str": str(correct_message_id),
                            "attachment_id": attachment_id
                        })
                        
                        await session.commit()
                        fixed_count += 1
                        logger.info(f"  ✓ Fixed: {file_name} moved to user message {correct_message_id}")
                    else:
                        logger.warning(f"  ✗ Could not find user message in session for {file_name}")
        
        logger.info(f"\nSummary:")
        logger.info(f"  Total attachments checked: {len(attachments)}")
        logger.info(f"  Issues found: {issue_count}")
        logger.info(f"  Fixed: {fixed_count}")
        logger.info(f"  Remaining issues: {issue_count - fixed_count}")
        
        return fixed_count
        
    except Exception as e:
        logger.error(f"Error fixing attachments: {e}", exc_info=True)
        await session.rollback()
        return 0


async def verify_attachments(session: AsyncSession):
    """Verify attachment linkages are correct."""
    try:
        # Check for orphaned attachments
        result = await session.execute(text("""
            SELECT COUNT(*)
            FROM attachments a
            LEFT JOIN messages m ON a.message_id = m.id
            WHERE a.entity_type = 'message' 
              AND a.is_active = true 
              AND m.id IS NULL
        """))
        orphaned_count = result.scalar()
        
        # Check for attachments on assistant messages
        result = await session.execute(text("""
            SELECT COUNT(*)
            FROM attachments a
            JOIN messages m ON a.message_id = m.id
            WHERE a.entity_type = 'message' 
              AND a.is_active = true 
              AND m.role != 'user'
        """))
        wrong_role_count = result.scalar()
        
        logger.info("\nVerification Results:")
        logger.info(f"  Orphaned attachments (no message): {orphaned_count}")
        logger.info(f"  Attachments on non-user messages: {wrong_role_count}")
        
        if orphaned_count == 0 and wrong_role_count == 0:
            logger.info("  ✓ All attachments are properly linked!")
        else:
            logger.warning(f"  ✗ Found {orphaned_count + wrong_role_count} attachment issues")
        
    except Exception as e:
        logger.error(f"Error verifying attachments: {e}", exc_info=True)


async def main():
    """Main function."""
    logger.info("=" * 60)
    logger.info("Attachment Linkage Fix Script")
    logger.info("=" * 60)
    logger.info(f"Database: {CURRENT_DB_NAME} @ {CURRENT_DB_HOST}")
    logger.info("=" * 60)
    
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
        logger.info("✓ Database connection established")
        
        session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        
        async with session_maker() as session:
            # Verify current state
            logger.info("\nStep 1: Verifying current attachment state...")
            await verify_attachments(session)
            
            # Fix issues
            logger.info("\nStep 2: Fixing attachment linkages...")
            fixed_count = await fix_orphaned_attachments(session)
            
            # Verify again
            logger.info("\nStep 3: Verifying fixes...")
            await verify_attachments(session)
            
            logger.info("\n" + "=" * 60)
            if fixed_count > 0:
                logger.info(f"✓ Fixed {fixed_count} attachment linkage issues!")
            else:
                logger.info("No issues found to fix.")
            logger.info("=" * 60)
        
        await engine.dispose()
        
    except Exception as e:
        logger.error(f"Failed to complete attachment fix: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    if sys.platform == "win32":
        import selectors
        selector = selectors.SelectSelector()
        loop = asyncio.SelectorEventLoop(selector)
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(main())
        finally:
            loop.close()
    else:
        asyncio.run(main())
