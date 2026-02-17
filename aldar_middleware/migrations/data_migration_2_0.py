#!/usr/bin/env python3
"""
Standalone data migration script to migrate data from 2.0 database dump to new schema.

This script can be run directly without importing the full application.
It uses raw SQL to avoid dependency issues.

Usage:
    # First, restore 2.0 dump to a temporary database:
    # createdb monolith_2_0_temp
    # pg_restore -d monolith_2_0_temp /path/to/dump-monolith-202511271851.sql
    
    # Then run this script:
    python -m aldar_middleware.migrations.data_migration_2_0
    # OR directly:
    python aldar_middleware/migrations/data_migration_2_0.py

Note on Windows/Azure Private Link:
    This script uses psycopg (async psycopg2) instead of asyncpg because asyncpg has
    known DNS resolution issues on Windows with Azure Private Link endpoints.
    
    If you see connection errors, ensure:
    1. VPN is connected (if required for Azure Private Link)
    2. DNS resolution works: nslookup <hostname>
    3. Firewall allows outbound connections to Azure
    
    Dependencies:
    - psycopg[binary] or psycopg[c] must be installed
    - Install with: pip install psycopg[binary]
"""

import asyncio
import logging
import sys
import os
import ssl
import selectors
import argparse
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import UUID
import json
import socket
from urllib.parse import quote_plus

# Add project root to path if running as module
if __name__ == "__main__":
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, project_root)

from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


def _convert_datetime(dt):
    """Convert timezone-aware datetime to naive datetime."""
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
        except:
            return None
    if isinstance(dt, datetime):
        if dt.tzinfo is not None:
            # Convert to UTC then remove timezone
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    return None

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _resolve_host_to_ip(host: str, port: int, env_name: str) -> str:
    """Resolve hostname to IP address. Returns IP or original hostname if resolution fails."""
    try:
        # getaddrinfo will raise socket.gaierror if resolution fails
        addr_info = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        if addr_info:
            ip_address = addr_info[0][4][0]
            logger.info(f"Resolved {env_name} host '{host}' to IP: {ip_address}")
            return ip_address
        return host
    except socket.gaierror as e:
        logger.warning(
            "Cannot resolve %s host '%s' (port %s): %s. Will try using hostname directly.",
            env_name,
            host,
            port,
            e,
        )
        logger.warning(
            "If connection fails, please check VPN connection, firewall rules, or DNS configuration."
        )
        return host


async def _test_connection(engine, db_name: str, db_host: str) -> bool:
    """Test actual database connection. Returns True if successful."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            logger.info(f"✓ Successfully connected to {db_name} @ {db_host}")
            return True
    except Exception as e:
        error_msg = str(e)
        logger.error(f"✗ Failed to connect to {db_name} @ {db_host}: {e}")
        
        # Provide specific guidance for DNS resolution failures
        if "getaddrinfo failed" in error_msg or "11003" in error_msg:
            logger.error("")
            logger.error("DNS Resolution Issue Detected:")
            logger.error("  - nslookup works, but asyncpg cannot resolve the hostname")
            logger.error("  - This is a known issue with asyncpg on Windows with Azure Private Link")
            logger.error("")
            logger.error("Possible solutions:")
            logger.error("  1. Ensure you're connected to VPN (if required for Azure Private Link)")
            logger.error("  2. Try running as Administrator (Windows DNS cache issue)")
            logger.error("  3. Flush DNS cache: ipconfig /flushdns")
            logger.error("  4. Check if hostname resolves: nslookup %s", db_host)
            logger.error("  5. Verify firewall allows outbound connections to Azure")
            logger.error("")
            logger.error("If DNS resolution works (nslookup succeeds) but asyncpg still fails,")
            logger.error("this may be an asyncpg/Windows compatibility issue with private DNS zones.")
        else:
            logger.error("")
            logger.error("Connection failed. Please check:")
            logger.error("  1. VPN connection (if required)")
            logger.error("  2. Firewall rules")
            logger.error("  3. Database credentials")
            logger.error("  4. Network connectivity")
        
        return False

# 2.0 Database connection
MONOLITH_2_0_DB_HOST = os.getenv("MONOLITH_2_0_DB_HOST", "")
MONOLITH_2_0_DB_PORT = int(os.getenv("MONOLITH_2_0_DB_PORT", "5432"))
MONOLITH_2_0_DB_USER = os.getenv("MONOLITH_2_0_DB_USER", "")
MONOLITH_2_0_DB_PASS = os.getenv("MONOLITH_2_0_DB_PASS", "")
MONOLITH_2_0_DB_NAME = os.getenv("MONOLITH_2_0_DB_NAME", "")


# Note: Using psycopg instead of asyncpg for better Windows/Azure Private Link compatibility
# Current database connection
CURRENT_DB_HOST = os.getenv("ALDAR_DB_HOST", "")
CURRENT_DB_PORT = int(os.getenv("ALDAR_DB_PORT", "5432"))
CURRENT_DB_USER = os.getenv("ALDAR_DB_USER", "")
CURRENT_DB_PASS = os.getenv("ALDAR_DB_PASS", "")
CURRENT_DB_NAME = os.getenv("ALDAR_DB_BASE", "")

# Note: Using psycopg instead of asyncpg for better Windows/Azure Private Link compatibility
CURRENT_DB_URL = f"postgresql+psycopg://{CURRENT_DB_USER}:{CURRENT_DB_PASS}@{CURRENT_DB_HOST}:{CURRENT_DB_PORT}/{CURRENT_DB_NAME}"

# Migration control flags
MIGRATE_STARTER_PROMPTS = os.getenv("MIGRATE_STARTER_PROMPTS", "false").lower() in ("true", "1", "yes")
MIGRATE_CONFIGS = os.getenv("MIGRATE_CONFIGS", "false").lower() in ("true", "1", "yes")

async def get_2_0_users(source_session: AsyncSession) -> List[Dict[str, Any]]:
    """Extract users from 2.0 database."""
    try:
        result = await source_session.execute(
            text('SELECT * FROM "User"')
        )
        rows = result.fetchall()
        columns = result.keys()
        
        users = []
        for row in rows:
            user_dict = dict(zip(columns, row))
            users.append(user_dict)
        
        logger.info(f"Found {len(users)} users in 2.0 database")
        return users
    except Exception as e:
        error_msg = str(e)
        if "getaddrinfo failed" in error_msg or "11003" in error_msg:
            logger.error(
                f"DNS resolution failed when connecting to source database. "
                f"Please check your network connection and VPN status."
            )
        logger.error(f"Error extracting users from 2.0 DB: {e}", exc_info=True)
        return []


async def migrate_user(user_data: Dict[str, Any], target_session: AsyncSession) -> Optional[UUID]:
    """Migrate a single user from 2.0 to new schema using raw SQL."""
    try:
        email = user_data.get("emailAddress")
        if not email:
            return None
        
        # Check if user exists
        result = await target_session.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": email}
        )
        existing = result.fetchone()
        
        # Build preferences JSON
        preferences = {}
        if user_data.get("customQueryAboutUser"):
            preferences["customQueryAboutUser"] = user_data.get("customQueryAboutUser")
        if user_data.get("customQueryPreferredFormatting"):
            preferences["customQueryPreferredFormatting"] = user_data.get("customQueryPreferredFormatting")
        if user_data.get("customQueryTopicsOfInterest"):
            topics = user_data.get("customQueryTopicsOfInterest")
            if isinstance(topics, str) and topics:
                try:
                    topics = json.loads(topics)
                except:
                    if ',' in topics:
                        topics = [t.strip() for t in topics.split(',')]
                    else:
                        topics = [topics] if topics else []
            preferences["customQueryTopicsOfInterest"] = topics if topics else []
        
        first_name = user_data.get("firstName") or ""
        last_name = user_data.get("lastName") or ""
        full_name = f"{first_name} {last_name}".strip() or user_data.get("title", "")
        
        if existing:
            # Skip if user already exists (no duplicates)
            user_id = existing[0]
            logger.debug(f"Skipping duplicate user: {email} -> {user_id}")
            return user_id
        else:
            # Insert new user - preserve original ID from 2.0
            user_id_2_0 = user_data.get("id")
            result = await target_session.execute(
                text("""
                    INSERT INTO users (id, email, first_name, last_name, full_name, username, external_id, 
                                     company, is_onboarded, is_custom_query_enabled, first_logged_in_at, preferences)
                    VALUES (:id, :email, :first_name, :last_name, :full_name, :username, :external_id,
                           :company, :is_onboarded, :is_custom_query_enabled, :first_logged_in_at, :preferences)
                    RETURNING id
                """),
                {
                    "id": user_id_2_0,
                    "email": email,
                    "first_name": first_name,
                    "last_name": last_name,
                    "full_name": full_name,
                    "username": email,
                    "external_id": user_data.get("externalId"),
                    "company": user_data.get("company"),
                    "is_onboarded": user_data.get("isOnboarded", False),
                    "is_custom_query_enabled": user_data.get("isCustomQueryEnabled", False),
                    "first_logged_in_at": _convert_datetime(user_data.get("firstLoggedInAt")),
                    "preferences": json.dumps(preferences) if preferences else None
                }
            )
            user_id = result.fetchone()[0]
            logger.info(f"Created user: {email} -> {user_id}")
        
        await target_session.commit()
        return user_id
        
    except Exception as e:
        logger.error(f"Error migrating user {user_data.get('emailAddress')}: {e}", exc_info=True)
        await target_session.rollback()
        return None


async def migrate_sessions(source_session: AsyncSession, target_session: AsyncSession, user_id_2_0_to_new: Dict[str, UUID]) -> Dict[str, UUID]:
    """Migrate sessions from 2.0 to new schema. Returns mapping of 2.0 session ID to new session UUID."""
    session_id_2_0_to_new = {}
    try:
        result = await source_session.execute(
            text('SELECT * FROM "Conversation"')
        )
        rows = result.fetchall()
        columns = result.keys()
        
        logger.info(f"Found {len(rows)} conversations in 2.0 database")
        
        # Get AiQ MCP Agent DEV ID (public_id: 606770b4-80c2-46a8-b86d-693f74684907)
        # This is the agent to use for all migrated sessions from 2.0 DB
        agent_result = await target_session.execute(
            text("SELECT id FROM agents WHERE public_id = '606770b4-80c2-46a8-b86d-693f74684907' LIMIT 1")
        )
        agent_row = agent_result.fetchone()
        if not agent_row:
            # Fallback: try to find by name
            agent_result = await target_session.execute(
                text("SELECT id FROM agents WHERE name = 'AiQ MCP Agent DEV' LIMIT 1")
            )
            agent_row = agent_result.fetchone()
        default_agent_id = agent_row[0] if agent_row else 1
        
        count = 0
        for row in rows:
            session_data = dict(zip(columns, row))
            new_session_id = await migrate_session(session_data, target_session, user_id_2_0_to_new, default_agent_id)
            if new_session_id:
                session_id_2_0 = session_data.get("id")
                if session_id_2_0:
                    session_id_2_0_to_new[session_id_2_0] = new_session_id
            count += 1
            if count % 100 == 0:
                logger.info(f"Migrated {count}/{len(rows)} sessions...")
        
        return session_id_2_0_to_new
                
    except Exception as e:
        logger.error(f"Error migrating sessions: {e}", exc_info=True)
        return session_id_2_0_to_new


async def migrate_session(session_data: Dict[str, Any], target_session: AsyncSession, user_id_2_0_to_new: Dict[str, UUID], default_agent_id: int) -> Optional[UUID]:
    """Migrate a single session using raw SQL. Returns new session UUID."""
    try:
        user_id_2_0 = session_data.get("userId")
        if not user_id_2_0:
            return None
        
        new_user_id = user_id_2_0_to_new.get(user_id_2_0)
        if not new_user_id:
            logger.debug(f"Skipping session {session_data.get('id')}: user_id {user_id_2_0} not in mapping (mapping has {len(user_id_2_0_to_new)} entries)")
            return None
        
        session_name = session_data.get("name") or "Untitled Chat"
        created_at = _convert_datetime(session_data.get("createdAt")) or datetime.utcnow()
        graph_id = session_data.get("graphId")
        
        # Check if session already exists (check by user_id + session_name + created_at, or graph_id if available)
        if graph_id:
            check_result = await target_session.execute(
                text("SELECT id FROM sessions WHERE user_id = :user_id AND graph_id = :graph_id"),
                {"user_id": new_user_id, "graph_id": graph_id}
            )
        else:
            check_result = await target_session.execute(
                text("SELECT id FROM sessions WHERE user_id = :user_id AND session_name = :session_name AND created_at = :created_at"),
                {"user_id": new_user_id, "session_name": session_name, "created_at": created_at}
            )
        existing = check_result.fetchone()
        
        if existing:
            # Update existing session with new agent instead of skipping
            existing_session_id = existing[0]
            logger.debug(f"Updating existing session: {session_name} (user_id: {new_user_id}) -> {existing_session_id} with new agent")
            await target_session.execute(
                text("""
                    UPDATE sessions 
                    SET agent_id = :agent_id,
                        session_name = :session_name,
                        status = :status,
                        updated_at = :updated_at
                    WHERE id = :id
                """),
                {
                    "id": existing_session_id,
                    "agent_id": default_agent_id,
                    "session_name": session_name,
                    "status": "active" if not session_data.get("deletedAt") else "archived",
                    "updated_at": datetime.utcnow()
                }
            )
            await target_session.commit()
            return existing_session_id
        
        # Preserve original session ID from 2.0 Conversation table
        session_id_2_0 = session_data.get("id")
        result = await target_session.execute(
            text("""
                INSERT INTO sessions (id, public_id, user_id, agent_id, session_name, status, graph_id, deleted_at,
                                    is_favorite, last_message_interaction_at, meeting_id,
                                    document_knowledge_agent_id, document_my_agent_id, created_at, updated_at)
                VALUES (:id, gen_random_uuid(), :user_id, :agent_id, :session_name, :status, :graph_id, :deleted_at,
                       :is_favorite, :last_message_interaction_at, :meeting_id,
                       :document_knowledge_agent_id, :document_my_agent_id, :created_at, :updated_at)
                RETURNING id
            """),
            {
                "id": session_id_2_0,
                "user_id": new_user_id,
                "agent_id": default_agent_id,
                "session_name": session_name,
                "status": "active" if not session_data.get("deletedAt") else "archived",
                "graph_id": graph_id,
                "deleted_at": _convert_datetime(session_data.get("deletedAt")),
                "is_favorite": session_data.get("isFavorite", False),
                "last_message_interaction_at": _convert_datetime(session_data.get("lastMessageInteractionAt")),
                "meeting_id": session_data.get("meetingId"),
                "document_knowledge_agent_id": None,
                "document_my_agent_id": None,
                "created_at": created_at,
                "updated_at": _convert_datetime(session_data.get("updatedAt")) or datetime.utcnow()
            }
        )
        new_session_id = result.fetchone()[0]
        await target_session.commit()
        return new_session_id
        
    except Exception as e:
        logger.debug(f"Error migrating session {session_data.get('id')}: {e}")
        await target_session.rollback()
        return None


async def migrate_messages(source_session: AsyncSession, target_session: AsyncSession, 
                          session_id_2_0_to_new: Dict[str, UUID], user_id_2_0_to_new: Dict[str, UUID]) -> Dict[str, UUID]:
    """Migrate messages from 2.0 to new schema. Returns mapping of 2.0 message ID to new message UUID."""
    message_id_2_0_to_new = {}
    try:
        result = await source_session.execute(
            text('SELECT * FROM "Message"')
        )
        rows = result.fetchall()
        columns = result.keys()
        
        logger.info(f"Found {len(rows)} messages in 2.0 database")
        
        # Get AiQ MCP Agent DEV ID (public_id: 606770b4-80c2-46a8-b86d-693f74684907)
        # This is the agent to use for all migrated sessions/messages from 2.0 DB
        agent_result = await target_session.execute(
            text("SELECT id FROM agents WHERE public_id = '606770b4-80c2-46a8-b86d-693f74684907' LIMIT 1")
        )
        agent_row = agent_result.fetchone()
        if not agent_row:
            # Fallback: try to find by name
            agent_result = await target_session.execute(
                text("SELECT id FROM agents WHERE name = 'AiQ MCP Agent DEV' LIMIT 1")
            )
            agent_row = agent_result.fetchone()
        default_agent_id = agent_row[0] if agent_row else 1
        
        count = 0
        for row in rows:
            message_data = dict(zip(columns, row))
            new_message_id = await migrate_message(message_data, target_session, session_id_2_0_to_new, user_id_2_0_to_new, default_agent_id)
            if new_message_id:
                message_id_2_0 = message_data.get("id")
                if message_id_2_0:
                    message_id_2_0_to_new[message_id_2_0] = new_message_id
            count += 1
            if count % 1000 == 0:
                logger.info(f"Migrated {count}/{len(rows)} messages...")
        
        return message_id_2_0_to_new
                
    except Exception as e:
        logger.error(f"Error migrating messages: {e}", exc_info=True)
        return message_id_2_0_to_new


async def migrate_message(message_data: Dict[str, Any], target_session: AsyncSession,
                         session_id_2_0_to_new: Dict[str, UUID], user_id_2_0_to_new: Dict[str, UUID],
                         default_agent_id: int) -> Optional[UUID]:
    """Migrate a single message using raw SQL. Returns new message UUID."""
    try:
        conversation_id_2_0 = message_data.get("conversationId")
        if not conversation_id_2_0:
            return None
        
        new_session_id = session_id_2_0_to_new.get(conversation_id_2_0)
        if not new_session_id:
            logger.debug(f"Skipping message {message_data.get('id')}: conversation_id {conversation_id_2_0} not in mapping")
            return None
        
        # Get user_id from session (since message doesn't have direct user_id in 2.0)
        # We'll get it from the session
        session_result = await target_session.execute(
            text("SELECT user_id FROM sessions WHERE id = :session_id"),
            {"session_id": new_session_id}
        )
        session_row = session_result.fetchone()
        if not session_row:
            return None
        new_user_id = session_row[0]
        
        # Build message_metadata JSON for custom query fields
        message_metadata = {}
        if message_data.get("customQueryAboutUser"):
            message_metadata["customQueryAboutUser"] = message_data.get("customQueryAboutUser")
        if message_data.get("customQueryPreferredFormatting"):
            message_metadata["customQueryPreferredFormatting"] = message_data.get("customQueryPreferredFormatting")
        if message_data.get("customQueryTopicsOfInterest"):
            topics = message_data.get("customQueryTopicsOfInterest")
            if isinstance(topics, str) and topics:
                try:
                    topics = json.loads(topics)
                except:
                    if ',' in topics:
                        topics = [t.strip() for t in topics.split(',')]
                    else:
                        topics = [topics] if topics else []
            message_metadata["customQueryTopicsOfInterest"] = topics if topics else []
        
        # Map parent message ID (will be updated in second pass)
        new_parent_message_id = None
        
        # Determine role from message data
        # In 2.0: isReply=false means user message, isReply=true means assistant message
        is_reply = message_data.get("isReply", False)
        role = "assistant" if is_reply else "user"
        
        content = message_data.get("rawMessage") or ""
        sent_at = _convert_datetime(message_data.get("sentAt"))
        created_at = _convert_datetime(message_data.get("createdAt")) or datetime.utcnow()
        
        # Check if message already exists (check by session_id + content + sent_at/created_at)
        if sent_at:
            check_result = await target_session.execute(
                text("SELECT id FROM messages WHERE session_id = :session_id AND content = :content AND sent_at = :sent_at"),
                {"session_id": new_session_id, "content": content, "sent_at": sent_at}
            )
        else:
            # If sent_at is None, use created_at for duplicate check
            check_result = await target_session.execute(
                text("SELECT id FROM messages WHERE session_id = :session_id AND content = :content AND sent_at IS NULL AND created_at = :created_at"),
                {"session_id": new_session_id, "content": content, "created_at": created_at}
            )
        existing = check_result.fetchone()
        
        if existing:
            # Update existing message with new agent instead of skipping
            existing_message_id = existing[0]
            logger.debug(f"Updating existing message: {content[:50]}... (session_id: {new_session_id}) -> {existing_message_id} with new agent")
            await target_session.execute(
                text("""
                    UPDATE messages 
                    SET agent_id = :agent_id,
                        role = :role,
                        updated_at = :updated_at
                    WHERE id = :id
                """),
                {
                    "id": existing_message_id,
                    "agent_id": default_agent_id,
                    "role": role,
                    "updated_at": datetime.utcnow()
                }
            )
            await target_session.commit()
            return existing_message_id
        
        # Preserve original message ID from 2.0 Message table
        message_id_2_0 = message_data.get("id")
        result = await target_session.execute(
            text("""
                INSERT INTO messages (id, public_id, session_id, user_id, agent_id, parent_message_id, role, content,
                                    content_type, document_my_agent_id, document_knowledge_agent_id, is_reply,
                                    is_refreshed, result_code, result_note, sent_at, deleted_at,
                                    is_sent_directly_to_openai, message_type, is_internet_search_used,
                                    has_found_information, selected_agent_type, message_metadata, created_at, updated_at)
                VALUES (:id, gen_random_uuid(), :session_id, :user_id, :agent_id, :parent_message_id, :role, :content,
                       :content_type, :document_my_agent_id, :document_knowledge_agent_id, :is_reply,
                       :is_refreshed, :result_code, :result_note, :sent_at, :deleted_at,
                       :is_sent_directly_to_openai, :message_type, :is_internet_search_used,
                       :has_found_information, :selected_agent_type, :message_metadata, :created_at, :updated_at)
                RETURNING id
            """),
            {
                "id": message_id_2_0,
                "session_id": new_session_id,
                "user_id": new_user_id,
                "agent_id": default_agent_id,
                "parent_message_id": new_parent_message_id,
                "role": role,
                "content": content,
                "content_type": "text",
                "document_my_agent_id": None,  # TODO: Map from MyAgent table if needed
                "document_knowledge_agent_id": None,  # TODO: Map from KnowledgeAgent table if needed
                "is_reply": message_data.get("isReply", False),
                "is_refreshed": message_data.get("isRefreshed", False),
                "result_code": str(message_data.get("resultCode", "")) if message_data.get("resultCode") else None,
                "result_note": message_data.get("resultNote"),
                "sent_at": sent_at,
                "deleted_at": _convert_datetime(message_data.get("deletedAt")),
                "is_sent_directly_to_openai": message_data.get("isSentDirectlyToOpenAi", False),
                "message_type": str(message_data.get("type", "")) if message_data.get("type") else None,
                "is_internet_search_used": message_data.get("isInternetSearchUsed", False),
                "has_found_information": message_data.get("hasFoundInformation", False),
                "selected_agent_type": str(message_data.get("selectedAgentType", "")) if message_data.get("selectedAgentType") else None,
                "message_metadata": json.dumps(message_metadata) if message_metadata else None,
                "created_at": _convert_datetime(message_data.get("createdAt")) or datetime.utcnow(),
                "updated_at": _convert_datetime(message_data.get("updatedAt")) or datetime.utcnow()
            }
        )
        new_message_id = result.fetchone()[0]
        await target_session.commit()
        return new_message_id
        
    except Exception as e:
        logger.debug(f"Error migrating message {message_data.get('id')}: {e}")
        await target_session.rollback()
        return None


async def migrate_attachments(source_session: AsyncSession, target_session: AsyncSession,
                             message_id_2_0_to_new: Dict[str, UUID], user_id_2_0_to_new: Dict[str, UUID],
                             source_blob_client: Optional[Any] = None,
                             target_blob_client: Optional[Any] = None) -> Dict[str, int]:
    """Migrate attachments from 2.0 to new schema with blank URLs (no blob transfer).
    
    Returns dict with statistics: {'created': N, 'updated': N, 'skipped': N}
    """
    try:
        result = await source_session.execute(
            text('SELECT * FROM "MessageAttachment"')
        )
        rows = result.fetchall()
        columns = result.keys()
        
        logger.info(f"Found {len(rows)} attachments in 2.0 database")
        logger.info("⚠ Migrating attachments with blank URLs - metadata (file_name, size, etc.) will be preserved")
        
        stats = {'created': 0, 'updated': 0, 'skipped': 0}
        
        for row in rows:
            attachment_data = dict(zip(columns, row))
            result = await migrate_attachment(attachment_data, target_session, message_id_2_0_to_new, 
                                   user_id_2_0_to_new, source_blob_client, target_blob_client)
            
            # result is a tuple: (success: bool, action: str) where action is 'created', 'updated', or 'skipped'
            if result and isinstance(result, tuple):
                success, action = result
                if success:
                    stats[action] = stats.get(action, 0) + 1
            elif result:
                stats['created'] += 1
            else:
                stats['skipped'] += 1
                
            total_processed = sum(stats.values())
            if total_processed % 10 == 0:
                logger.info(f"Processed {total_processed}/{len(rows)} attachments (created: {stats['created']}, updated: {stats['updated']}, skipped: {stats['skipped']})...")
        
        logger.info(f"✓ Attachment migration complete: created={stats['created']}, updated={stats['updated']}, skipped={stats['skipped']}")
        return stats
                
    except Exception as e:
        logger.error(f"Error migrating attachments: {e}", exc_info=True)


async def migrate_attachment(attachment_data: Dict[str, Any], target_session: AsyncSession,
                            message_id_2_0_to_new: Dict[str, UUID], user_id_2_0_to_new: Dict[str, UUID],
                            source_blob_client: Optional[Any] = None,
                            target_blob_client: Optional[Any] = None) -> tuple[bool, str]:
    """Migrate a single attachment using raw SQL with blank URLs (no blob transfer).
    
    Returns tuple (success: bool, action: str) where action is 'created', 'updated', or 'skipped'.
    """
    try:
        message_id_2_0 = attachment_data.get("messageId")
        file_name = attachment_data.get("name", "unknown")
        if not message_id_2_0:
            logger.debug(f"Skipping attachment '{file_name}': no messageId")
            return (False, 'skipped')
        
        new_message_id = message_id_2_0_to_new.get(message_id_2_0)
        if not new_message_id:
            logger.debug(f"Skipping attachment '{file_name}': message_id {message_id_2_0} not migrated")
            return (False, 'skipped')
        
        # Get user_id from message
        message_result = await target_session.execute(
            text("SELECT user_id FROM messages WHERE id = :message_id"),
            {"message_id": new_message_id}
        )
        message_row = message_result.fetchone()
        if not message_row:
            logger.debug(f"Skipping attachment '{file_name}': message {new_message_id} not found in target DB")
            return (False, 'skipped')
        new_user_id = message_row[0]
        
        # Map documentType to content_type
        document_type = attachment_data.get("documentType", "")
        content_type_map = {
            "PDF": "application/pdf",
            "DOCX": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "TXT": "text/plain",
            "IMAGE": "image/jpeg",
            "PNG": "image/png",
            "JPG": "image/jpeg",
            "JPEG": "image/jpeg",
        }
        content_type = content_type_map.get(str(document_type).upper(), "application/octet-stream")
        
        # Get documentId from 2.0 - this is the blob path in 2.0 storage
        document_id_2_0 = attachment_data.get("documentId", "")
        file_size = attachment_data.get("documentSizeInBytes", 0)
        attachment_id_2_0 = attachment_data.get("id")
        
        # Check if attachment already exists by ID first (for re-runs)
        check_result = await target_session.execute(
            text("SELECT id, blob_url, blob_name FROM attachments WHERE id = :id"),
            {"id": attachment_id_2_0}
        )
        existing = check_result.fetchone()
        
        if existing:
            # Attachment already exists - check if URLs need to be blanked
            existing_id, existing_blob_url, existing_blob_name = existing
            needs_update = (existing_blob_url and existing_blob_url != "") or (existing_blob_name and existing_blob_name != "")
            
            if needs_update:
                # Update existing attachment to have blank URLs
                await target_session.execute(
                    text("""
                        UPDATE attachments 
                        SET blob_url = '', blob_name = '', updated_at = :updated_at
                        WHERE id = :id
                    """),
                    {"id": attachment_id_2_0, "updated_at": datetime.utcnow()}
                )
                await target_session.commit()
                logger.debug(f"✓ Updated attachment '{file_name}' (ID: {attachment_id_2_0}) to have blank URLs")
                return (True, 'updated')
            else:
                logger.debug(f"Attachment '{file_name}' (ID: {attachment_id_2_0}) already has blank URLs")
                return (True, 'skipped')
        else:
            # Check for duplicate by message_id + file_name (different ID but same file)
            check_result = await target_session.execute(
                text("SELECT id, blob_url, blob_name FROM attachments WHERE message_id = :message_id AND file_name = :file_name"),
                {"message_id": new_message_id, "file_name": file_name}
            )
            duplicate = check_result.fetchone()
            
            if duplicate:
                # Attachment exists with different ID - update if it has URLs
                duplicate_id, dup_blob_url, dup_blob_name = duplicate
                needs_update = (dup_blob_url and dup_blob_url != "") or (dup_blob_name and dup_blob_name != "")
                
                if needs_update:
                    await target_session.execute(
                        text("""
                            UPDATE attachments 
                            SET blob_url = '', blob_name = '', updated_at = :updated_at
                            WHERE id = :id
                        """),
                        {"id": duplicate_id, "updated_at": datetime.utcnow()}
                    )
                    await target_session.commit()
                    logger.debug(f"✓ Updated duplicate attachment '{file_name}' (ID: {duplicate_id}) to have blank URLs")
                    return (True, 'updated')
                else:
                    logger.debug(f"Duplicate attachment '{file_name}' (ID: {duplicate_id}) already has blank URLs")
                    return (True, 'skipped')
        
        # Skip blob transfer - use blank URLs for migrated attachments
        # Metadata (file_name, size, type) is preserved so attachments appear in API with blank URLs
        blob_url = ""  # Blank URL - frontend should handle gracefully
        new_blob_name = ""  # Blank blob name
        
        logger.debug(f"Creating attachment: '{file_name}' (size: {file_size}, type: {content_type}, doc_id: {document_id_2_0})")
        
        # Insert new attachment record with blank blob location
        await target_session.execute(
            text("""
                INSERT INTO attachments (id, user_id, file_name, file_size, content_type, blob_url, blob_name,
                                       entity_type, entity_id, message_id, is_active, created_at, updated_at)
                VALUES (:id, :user_id, :file_name, :file_size, :content_type, :blob_url, :blob_name,
                       :entity_type, :entity_id, :message_id, :is_active, :created_at, :updated_at)
            """),
            {
                "id": attachment_id_2_0,
                "user_id": new_user_id,
                "file_name": file_name,
                "file_size": file_size,
                "content_type": content_type,
                "blob_url": blob_url,
                "blob_name": new_blob_name,
                "entity_type": "message",
                "entity_id": str(new_message_id),
                "message_id": new_message_id,
                "is_active": attachment_data.get("deletedAt") is None,
                "created_at": _convert_datetime(attachment_data.get("createdAt")) or datetime.utcnow(),
                "updated_at": _convert_datetime(attachment_data.get("updatedAt")) or datetime.utcnow()
            }
        )
        logger.debug(f"✓ Created attachment: '{file_name}' for message {new_message_id}")
        await target_session.commit()
        return (True, 'created')
        
    except Exception as e:
        logger.debug(f"Error migrating attachment '{file_name}' (ID: {attachment_data.get('id')}): {e}")
        await target_session.rollback()
        return (False, 'skipped')


async def update_message_parents(source_session: AsyncSession, target_session: AsyncSession,
                                 message_id_2_0_to_new: Dict[str, UUID]):
    """Update parent_message_id for messages that have parent references."""
    try:
        # Get all messages with previousMessageId
        result = await source_session.execute(
            text('SELECT id, "previousMessageId" FROM "Message" WHERE "previousMessageId" IS NOT NULL')
        )
        rows = result.fetchall()
        
        logger.info(f"Updating {len(rows)} message parent relationships...")
        
        count = 0
        for row in rows:
            message_id_2_0, parent_id_2_0 = row
            new_message_id = message_id_2_0_to_new.get(message_id_2_0)
            new_parent_id = message_id_2_0_to_new.get(parent_id_2_0)
            
            if new_message_id and new_parent_id:
                await target_session.execute(
                    text("UPDATE messages SET parent_message_id = :parent_id WHERE id = :message_id"),
                    {"parent_id": new_parent_id, "message_id": new_message_id}
                )
                count += 1
                if count % 1000 == 0:
                    await target_session.commit()
                    logger.info(f"Updated {count}/{len(rows)} parent relationships...")
        
        await target_session.commit()
        logger.info(f"✓ Updated {count} message parent relationships")
        
    except Exception as e:
        logger.error(f"Error updating message parents: {e}", exc_info=True)
        await target_session.rollback()


async def migrate_starter_prompts(source_session: AsyncSession, target_session: AsyncSession):
    """Migrate starter prompts from 2.0 to new schema."""
    try:
        result = await source_session.execute(
            text('SELECT * FROM "StarterPrompt"')
        )
        rows = result.fetchall()
        columns = result.keys()
        
        logger.info(f"Found {len(rows)} starter prompts in 2.0 database")
        
        count = 0
        for row in rows:
            prompt_data = dict(zip(columns, row))
            await migrate_starter_prompt(prompt_data, target_session)
            count += 1
            if count % 100 == 0:
                logger.info(f"Migrated {count}/{len(rows)} starter prompts...")
                
    except Exception as e:
        logger.error(f"Error migrating starter prompts: {e}", exc_info=True)


async def migrate_starter_prompt(prompt_data: Dict[str, Any], target_session: AsyncSession):
    """Migrate a single starter prompt using raw SQL."""
    try:
        await target_session.execute(
            text("""
                INSERT INTO starter_prompts (id, title, prompt, is_highlighted, "order", 
                                            knowledge_agent_id, my_agent_id, created_at, updated_at)
                VALUES (:id, :title, :prompt, :is_highlighted, :order,
                       :knowledge_agent_id, :my_agent_id, :created_at, :updated_at)
                ON CONFLICT (id) DO NOTHING
            """),
            {
                "id": prompt_data.get("id"),
                "title": prompt_data.get("title", ""),
                "prompt": prompt_data.get("prompt", ""),
                "is_highlighted": prompt_data.get("isHighlighted", False),
                "order": prompt_data.get("order", 0),
                "knowledge_agent_id": prompt_data.get("knowledgeAgentId"),
                "my_agent_id": None,  # TODO: Map from MyAgent table if needed
                "created_at": _convert_datetime(prompt_data.get("createdAt")) or datetime.utcnow(),
                "updated_at": _convert_datetime(prompt_data.get("updatedAt")) or datetime.utcnow()
            }
        )
        await target_session.commit()
        
    except Exception as e:
        logger.debug(f"Error migrating starter prompt {prompt_data.get('id')}: {e}")
        await target_session.rollback()


async def migrate_configs(source_session: AsyncSession, target_session: AsyncSession):
    """Migrate configs from 2.0 to new schema."""
    try:
        result = await source_session.execute(
            text('SELECT * FROM "Config"')
        )
        rows = result.fetchall()
        columns = result.keys()
        
        logger.info(f"Found {len(rows)} configs in 2.0 database")
        
        count = 0
        for row in rows:
            config_data = dict(zip(columns, row))
            await migrate_config(config_data, target_session)
            count += 1
            if count % 100 == 0:
                logger.info(f"Migrated {count}/{len(rows)} configs...")
        
        logger.info(f"✓ Migrated {count} configs")
                
    except Exception as e:
        logger.error(f"Error migrating configs: {e}", exc_info=True)


async def migrate_config(config_data: Dict[str, Any], target_session: AsyncSession):
    """Migrate a single config using raw SQL."""
    try:
        config_id = config_data.get("id")
        
        # Check if config already exists
        check_result = await target_session.execute(
            text("SELECT id FROM configs WHERE id = :id"),
            {"id": config_id}
        )
        existing = check_result.fetchone()
        
        if existing:
            # Skip if config already exists (no duplicates)
            logger.debug(f"Skipping duplicate config: {config_id}")
            return
        
        await target_session.execute(
            text("""
                INSERT INTO configs (id, version, system_wide_prompt, system_agent_prompt,
                                    user_custom_query_template, created_at, updated_at)
                VALUES (:id, :version, :system_wide_prompt, :system_agent_prompt,
                       :user_custom_query_template, :created_at, :updated_at)
                ON CONFLICT (id) DO NOTHING
            """),
            {
                "id": config_id,
                "version": config_data.get("version", 1),
                "system_wide_prompt": config_data.get("systemWidePrompt", ""),
                "system_agent_prompt": config_data.get("systemAgentPrompt", ""),
                "user_custom_query_template": config_data.get("userCustomQueryTemplate", ""),
                "created_at": _convert_datetime(config_data.get("createdAt")) or datetime.utcnow(),
                "updated_at": _convert_datetime(config_data.get("updatedAt")) or datetime.utcnow()
            }
        )
        await target_session.commit()
        
    except Exception as e:
        logger.debug(f"Error migrating config {config_data.get('id')}: {e}")
        await target_session.rollback()


async def _create_engine_with_fallback(host: str, port: int, user: str, password: str, db_name: str, env_name: str):
    """Create engine using psycopg (better Windows/Azure Private Link compatibility)."""
    # Resolve IP for logging/info purposes
    host_ip = _resolve_host_to_ip(host, port, env_name)
    
    # URL-encode credentials to handle special characters (like @, #, etc.) in passwords
    encoded_user = quote_plus(user)
    encoded_password = quote_plus(password)
    
    # Use psycopg instead of asyncpg for better Windows DNS handling
    # psycopg uses libpq which handles Azure Private Link DNS better
    hostname_url = f"postgresql+psycopg://{encoded_user}:{encoded_password}@{host}:{port}/{db_name}"
    
    logger.info(f"Attempting connection to {env_name} using hostname: {host}")
    if host_ip != host:
        logger.info(f"  (Resolved IP: {host_ip})")
    
    try:
        # psycopg handles SSL automatically for Azure PostgreSQL
        # For Azure, SSL is required, so we use sslmode=require
        # Add application_name and sslmode to connection string for psycopg
        hostname_url_with_params = f"{hostname_url}?application_name=data_migration_2_0&sslmode=require"
        
        try:
            engine = create_async_engine(
                hostname_url_with_params,
                echo=False,
                connect_args={
                    "connect_timeout": 10
                },
                pool_pre_ping=True,
                pool_recycle=3600,
                pool_reset_on_return='commit'
            )
        except Exception as driver_error:
            error_str = str(driver_error).lower()
            if "psycopg" in error_str or "driver" in error_str or "no module" in error_str:
                logger.error("")
                logger.error("=" * 60)
                logger.error("ERROR: psycopg driver not found!")
                logger.error("=" * 60)
                logger.error("This script requires psycopg (async psycopg2) for Windows/Azure compatibility.")
                logger.error("")
                logger.error("Install with one of:")
                logger.error("  pip install psycopg[binary]")
                logger.error("  pip install psycopg[c]")
                logger.error("")
                logger.error("The [binary] variant includes pre-compiled binaries (recommended).")
                logger.error("The [c] variant requires a C compiler.")
                logger.error("=" * 60)
                raise SystemExit("Please install psycopg: pip install psycopg[binary]")
            raise
        
        # Test connection
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        
        logger.info(f"✓ Successfully connected to {env_name} using hostname")
        return engine, host
    except Exception as e:
        error_msg = str(e)
        logger.error(f"✗ Failed to connect to {env_name} using hostname: {e}")
        
        # If hostname fails and we have an IP, try IP address as fallback
        if host_ip != host:
            logger.warning(f"Trying IP address fallback for {env_name}: {host_ip}")
            # Use already encoded credentials
            ip_url = f"postgresql+psycopg://{encoded_user}:{encoded_password}@{host_ip}:{port}/{db_name}"
            
            try:
                # When using IP, we still require SSL but may need to handle hostname verification
                # Add application_name and sslmode to connection string for psycopg
                ip_url_with_params = f"{ip_url}?application_name=data_migration_2_0&sslmode=require"
                
                engine = create_async_engine(
                    ip_url_with_params,
                    echo=False,
                    connect_args={
                        "connect_timeout": 10
                    },
                    pool_pre_ping=True,
                    pool_recycle=3600,
                    pool_reset_on_return='commit'
                )
                
                # Test connection
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                
                logger.info(f"✓ Successfully connected to {env_name} using IP address: {host_ip}")
                logger.warning(f"  Note: Using IP address instead of hostname")
                return engine, host_ip
            except Exception as ip_error:
                logger.error(f"IP address fallback also failed for {env_name}: {ip_error}")
                logger.error("")
                logger.error("Connection troubleshooting:")
                logger.error("  1. Verify VPN is connected (if required)")
                logger.error("  2. Check firewall rules allow outbound to Azure")
                logger.error("  3. Verify database credentials are correct")
                logger.error("  4. Try: nslookup " + host)
                raise ip_error
        else:
            # IP resolution failed, provide troubleshooting info
            logger.error("")
            logger.error("Connection troubleshooting:")
            logger.error("  1. Verify VPN is connected (if required)")
            logger.error("  2. Check firewall rules allow outbound to Azure")
            logger.error("  3. Verify database credentials are correct")
            logger.error("  4. DNS resolution failed - check network configuration")
            raise


async def main(migrate_starter_prompts: bool = None, migrate_configs: bool = None):
    """Main migration function."""
    # Override global flags if command-line arguments provided
    global MIGRATE_STARTER_PROMPTS, MIGRATE_CONFIGS
    if migrate_starter_prompts is not None:
        MIGRATE_STARTER_PROMPTS = migrate_starter_prompts
    if migrate_configs is not None:
        MIGRATE_CONFIGS = migrate_configs
    
    logger.info("=" * 60)
    logger.info("Starting 2.0 Data Migration")
    logger.info("=" * 60)
    logger.info(f"Source DB: {MONOLITH_2_0_DB_NAME} @ {MONOLITH_2_0_DB_HOST}")
    logger.info(f"Target DB: {CURRENT_DB_NAME} @ {CURRENT_DB_HOST}")
    logger.info("")
    logger.info("Migration Options:")
    logger.info(f"  - Migrate Starter Prompts: {MIGRATE_STARTER_PROMPTS}")
    logger.info(f"  - Migrate Configs: {MIGRATE_CONFIGS}")
    logger.info("=" * 60)
    
    logger.info("Creating database connections...")
    
    try:
        # Create engines with fallback to IP if hostname fails
        source_engine, source_conn_host = await _create_engine_with_fallback(
            MONOLITH_2_0_DB_HOST, MONOLITH_2_0_DB_PORT,
            MONOLITH_2_0_DB_USER, MONOLITH_2_0_DB_PASS, MONOLITH_2_0_DB_NAME,
            "MONOLITH_2_0_DB"
        )
        
        target_engine, target_conn_host = await _create_engine_with_fallback(
            CURRENT_DB_HOST, CURRENT_DB_PORT,
            CURRENT_DB_USER, CURRENT_DB_PASS, CURRENT_DB_NAME,
            "CURRENT_DB"
        )
        
        logger.info(f"✓ Both database connections established successfully")
        logger.info(f"  Source: {source_conn_host}")
        logger.info(f"  Target: {target_conn_host}")
        
    except Exception as e:
        logger.error(f"Cannot proceed with migration: failed to create database connections")
        logger.error(f"Error: {e}")
        sys.exit(1)
    
    source_session_maker = sessionmaker(source_engine, class_=AsyncSession, expire_on_commit=False)
    target_session_maker = sessionmaker(target_engine, class_=AsyncSession, expire_on_commit=False)
    
    # Skip Blob Storage initialization - blobs will NOT be transferred
    # Per client requirement: old migrated documents should have blank URLs
    source_blob_client = None
    target_blob_client = None
    logger.info("⚠ Blob Storage clients NOT initialized - attachments will use blank URLs (no blob transfer)")
    
    try:
        async with source_session_maker() as source_session, target_session_maker() as target_session:
            # Step 1: Migrate users
            logger.info("\nStep 1: Migrating users...")
            users_2_0 = await get_2_0_users(source_session)
            user_id_2_0_to_new = {}
            
            for user_data in users_2_0:
                user_id_2_0 = user_data.get("id")
                if user_id_2_0:
                    user_id = await migrate_user(user_data, target_session)
                    if user_id:
                        user_id_2_0_to_new[user_id_2_0] = user_id
            
            logger.info(f"✓ Migrated {len(user_id_2_0_to_new)} users")
            
            # Step 2: Migrate sessions
            logger.info("\nStep 2: Migrating sessions...")
            session_id_2_0_to_new = await migrate_sessions(source_session, target_session, user_id_2_0_to_new)
            logger.info(f"✓ Migrated {len(session_id_2_0_to_new)} sessions")
            
            # Step 3: Migrate messages
            logger.info("\nStep 3: Migrating messages...")
            message_id_2_0_to_new = await migrate_messages(source_session, target_session, session_id_2_0_to_new, user_id_2_0_to_new)
            logger.info(f"✓ Migrated {len(message_id_2_0_to_new)} messages")
            
            # Step 4: Update parent_message_id for messages (second pass)
            logger.info("\nStep 4: Updating message parent relationships...")
            await update_message_parents(source_session, target_session, message_id_2_0_to_new)
            logger.info("✓ Message parent relationships updated")
            
            # Step 5: Migrate attachments
            logger.info("\nStep 5: Migrating attachments...")
            attachment_stats = await migrate_attachments(source_session, target_session, message_id_2_0_to_new, 
                                    user_id_2_0_to_new, source_blob_client, target_blob_client)
            logger.info(f"✓ Attachments migration completed:")
            logger.info(f"  - Created: {attachment_stats.get('created', 0)}")
            logger.info(f"  - Updated (URLs blanked): {attachment_stats.get('updated', 0)}")
            logger.info(f"  - Skipped (already blank): {attachment_stats.get('skipped', 0)}")
            
            # Step 6: Migrate starter prompts (optional)
            if MIGRATE_STARTER_PROMPTS:
                logger.info("\nStep 6: Migrating starter prompts...")
                await migrate_starter_prompts(source_session, target_session)
                logger.info("✓ Starter prompts migration completed")
            else:
                logger.info("\nStep 6: Skipping starter prompts migration (MIGRATE_STARTER_PROMPTS=false)")
            
            # Step 7: Migrate configs (optional)
            if MIGRATE_CONFIGS:
                logger.info("\nStep 7: Migrating configs...")
                await migrate_configs(source_session, target_session)
                logger.info("✓ Configs migration completed")
            else:
                logger.info("\nStep 7: Skipping configs migration (MIGRATE_CONFIGS=false)")
            
            logger.info("\n" + "=" * 60)
            logger.info("Migration completed successfully!")
            logger.info("=" * 60)
    finally:
        await source_engine.dispose()
        await target_engine.dispose()


if __name__ == "__main__":
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Migrate data from 2.0 database to new schema',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Default migration (all steps)
  python -m aldar_middleware.migrations.data_migration_2_0
  
  # Skip starter prompts
  python -m aldar_middleware.migrations.data_migration_2_0 --no-starter-prompts
  
  # Skip configs
  python -m aldar_middleware.migrations.data_migration_2_0 --no-configs
  
  # Using environment variables
  MIGRATE_STARTER_PROMPTS=false python -m aldar_middleware.migrations.data_migration_2_0
        '''
    )
    parser.add_argument(
        '--no-starter-prompts',
        action='store_true',
        help='Skip starter prompts migration'
    )
    parser.add_argument(
        '--starter-prompts',
        dest='with_starter_prompts',
        action='store_true',
        help='Include starter prompts migration (default)'
    )
    parser.add_argument(
        '--no-configs',
        action='store_true',
        help='Skip configs migration'
    )
    parser.add_argument(
        '--configs',
        dest='with_configs',
        action='store_true',
        help='Include configs migration (default)'
    )
    
    args = parser.parse_args()
    
    # Determine flags from arguments
    migrate_starter_prompts = None
    if args.no_starter_prompts:
        migrate_starter_prompts = False
    elif args.with_starter_prompts:
        migrate_starter_prompts = True
    
    migrate_configs = None
    if args.no_configs:
        migrate_configs = False
    elif args.with_configs:
        migrate_configs = True
    
    # On Windows, psycopg requires SelectorEventLoop instead of the default ProactorEventLoop
    # This is a known issue: https://github.com/psycopg/psycopg/issues/503
    if sys.platform == "win32":
        # Use SelectorEventLoop on Windows for psycopg compatibility
        selector = selectors.SelectSelector()
        loop = asyncio.SelectorEventLoop(selector)
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(main(migrate_starter_prompts, migrate_configs))
        finally:
            loop.close()
    else:
        asyncio.run(main(migrate_starter_prompts, migrate_configs))
