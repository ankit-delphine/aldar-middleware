"""Web PubSub event listener service for automatically saving agent run events."""

import asyncio
from typing import Dict, Any, Optional
from datetime import datetime
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from aldar_middleware.database.base import get_db
from aldar_middleware.models.sessions import Session
from aldar_middleware.models.agent_runs import AgentRun
from aldar_middleware.models.menu import Agent
from aldar_middleware.models.user import User
from sqlalchemy import func


class WebPubSubEventListener:
    """Background service to listen and process Web PubSub events automatically."""
    
    def __init__(self):
        self.running = False
        self.task: Optional[asyncio.Task] = None
    
    async def process_run_event(self, event_data: Dict[str, Any]) -> bool:
        """
        Process a run event (RunCompleted, RunStarted, etc.) and save to database.
        
        Args:
            event_data: Event data from Web PubSub
            
        Returns:
            True if processed successfully, False otherwise
        """
        try:
            # Extract event type
            event = event_data.get("event") or event_data.get("type") or event_data.get("event_type")
            
            # Process all run-related events (RunCompleted, RunStarted, RunFailed, etc.)
            if not event or not event.startswith("Run"):
                logger.debug(f"Ignoring non-run event: {event}")
                return False
            
            # Extract run data - handle different payload structures
            run_data = event_data.get("data", event_data)
            
            session_id = run_data.get("session_id")
            run_id = run_data.get("run_id")
            content = run_data.get("content")
            status = run_data.get("status", "RUNNING")
            
            if not session_id or not run_id:
                logger.warning(f"Missing session_id or run_id in event {event}")
                return False
            
            # Save to database asynchronously
            async for db in get_db():
                try:
                    # Find session by session_id
                    session = None
                    try:
                        session_uuid = UUID(session_id)
                        result = await db.execute(
                            select(Session).where(Session.id == session_uuid)
                        )
                        session = result.scalar_one_or_none()
                    except ValueError:
                        pass
                    
                    if not session:
                        try:
                            session_uuid = UUID(session_id)
                            result = await db.execute(
                                select(Session).where(Session.public_id == session_uuid)
                            )
                            session = result.scalar_one_or_none()
                        except ValueError:
                            logger.warning(f"Invalid session_id format: {session_id}")
                            return False
                    
                    if not session:
                        logger.warning(f"Session not found: {session_id}")
                        return False
                    
                    # Find or create AgentRun
                    result = await db.execute(
                        select(AgentRun).where(AgentRun.run_id == run_id)
                    )
                    agent_run = result.scalar_one_or_none()
                    
                    if agent_run:
                        # Update existing run
                        agent_run.status = status.lower()
                        agent_run.updated_at = datetime.utcnow()
                        if content:
                            agent_run.content = content
                        if run_data.get("error_message"):
                            agent_run.error_message = run_data.get("error_message")
                    else:
                        # Create new run
                        agent_id = run_data.get("agent_id")
                        agent_name = run_data.get("agent_name")
                        user_id_raw = run_data.get("user_id")
                        
                        # Find agent if agent_id is provided
                        agent_db_id = None
                        if agent_id or agent_name:
                            conditions = []
                            if agent_id:
                                conditions.append(Agent.agent_id == agent_id)
                            if agent_name:
                                conditions.append(Agent.name == agent_name)
                            
                            if conditions:
                                agent_result = await db.execute(
                                    select(Agent).where(or_(*conditions))
                                )
                                agent = agent_result.scalar_one_or_none()
                                if agent:
                                    agent_db_id = agent.id
                        
                        if not agent_db_id:
                            agent_db_id = session.agent_id
                        
                        # Get user_id - handle both UUID and email formats
                        user_id = None
                        if user_id_raw:
                            # Try to parse as UUID first
                            try:
                                user_id = UUID(user_id_raw)
                            except (ValueError, TypeError):
                                # If not a valid UUID, try to find user by email
                                logger.info(f"user_id '{user_id_raw}' is not a UUID, looking up by email")
                                user_result = await db.execute(
                                    select(User).where(func.lower(User.email) == user_id_raw.lower().strip())
                                )
                                user = user_result.scalar_one_or_none()
                                if user:
                                    user_id = user.id
                                    logger.info(f"Found user by email '{user_id_raw}': user_id={user_id}")
                                else:
                                    logger.warning(f"User not found by email '{user_id_raw}', will use session.user_id")
                        
                        # Fall back to session.user_id if not found or not provided
                        if not user_id:
                            user_id = session.user_id
                            logger.info(f"Using session.user_id: {user_id}")
                        
                        agent_run = AgentRun(
                            run_id=run_id,
                            session_id=session.id,
                            agent_id=agent_db_id,
                            user_id=user_id,
                            agent_name=agent_name,
                            content=content,
                            content_type=run_data.get("content_type", "text"),
                            status=status.lower(),
                            error_message=run_data.get("error_message"),
                            created_at=datetime.utcnow(),
                            updated_at=datetime.utcnow()
                        )
                        db.add(agent_run)
                    
                    await db.commit()
                    
                    # SKIPPED: Message table update logic - using agno_sessions table instead for frontend data
                    # The external team is storing all data in agno_sessions table, so we'll use that for frontend
                    # if event == "RunCompleted" and content and status.upper() == "COMPLETED":
                    #     ... (message update logic skipped)
                    
                    logger.info(
                        f"Saved {event} event for run_id={run_id}, "
                        f"session_id={session_id}, status={status}. "
                        f"Note: Message table updates skipped - using agno_sessions table for frontend data."
                    )
                    return True
                    
                except Exception as e:
                    await db.rollback()
                    logger.error(f"Error saving {event} event to database: {str(e)}")
                    return False
                finally:
                    break
            
            return True
            
        except Exception as e:
            logger.error(f"Error processing run event: {str(e)}")
            return False
    
    async def process_event(self, event_data: Dict[str, Any]) -> bool:
        """
        Process any Web PubSub event.
        
        Args:
            event_data: Event data from Web PubSub
            
        Returns:
            True if processed successfully, False otherwise
        """
        return await self.process_run_event(event_data)
    
    async def start_listening(self):
        """Start the background listener (placeholder for future Web PubSub integration)."""
        self.running = True
        logger.info("Web PubSub event listener started (ready to receive events)")
        
        # In a real implementation, this would:
        # 1. Connect to Web PubSub
        # 2. Subscribe to events
        # 3. Process events as they arrive
        # For now, events will be processed via the webhook endpoint
    
    async def stop_listening(self):
        """Stop the background listener."""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("Web PubSub event listener stopped")


# Global instance
webpubsub_listener = WebPubSubEventListener()

