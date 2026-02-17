"""Celery tasks for AIQ Backend."""

import time
import asyncio
import sys
import platform
import threading
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from celery import current_task
from loguru import logger
import httpx

from aldar_middleware.queue.celery_app import celery_app
from aldar_middleware.services.ai_service import AIService
from aldar_middleware.orchestration.mcp import MCPService
from aldar_middleware.orchestration.azure_service_bus import azure_service_bus
from aldar_middleware.database.base import async_session, engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy import select, update, and_, or_
from aldar_middleware.models.menu import Agent


# Create a separate session factory for Celery tasks to avoid connection issues
def get_celery_session():
    """Get a fresh database session for Celery tasks."""
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )()


@celery_app.task(bind=True)
def process_chat_message(self, chat_id: str, user_id: str, message: str) -> Dict[str, Any]:
    """Process chat message asynchronously."""
    try:
        logger.info(f"Processing chat message for chat {chat_id}")
        
        # Initialize services
        ai_service = AIService()
        mcp_service = MCPService()
        
        # Generate AI response
        response = ai_service.generate_response(
            user_message=message,
            chat_id=chat_id,
            user_id=user_id
        )
        
        # Update task progress
        self.update_state(
            state="PROGRESS",
            meta={"status": "Processing complete", "response": response}
        )
        
        return {
            "status": "success",
            "response": response,
            "processed_at": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error processing chat message: {e}")
        self.update_state(
            state="FAILURE",
            meta={"error": str(e)}
        )
        raise


@celery_app.task(bind=True)
def send_notification(self, user_id: str, message: str, notification_type: str = "info") -> Dict[str, Any]:
    """Send notification to user."""
    try:
        logger.info(f"Sending notification to user {user_id}")
        
        # Here you would integrate with your notification service
        # For now, just log the notification
        logger.info(f"Notification sent: {message} to user {user_id}")
        
        return {
            "status": "success",
            "user_id": user_id,
            "message": message,
            "type": notification_type,
            "sent_at": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error sending notification: {e}")
        self.update_state(
            state="FAILURE",
            meta={"error": str(e)}
        )
        raise


@celery_app.task(bind=True)
def process_mcp_request(self, connection_id: str, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Process MCP request asynchronously."""
    try:
        logger.info(f"Processing MCP request: {method}")
        
        mcp_service = MCPService()
        result = mcp_service.send_message(
            connection_id=connection_id,
            method=method,
            params=params
        )
        
        return {
            "status": "success",
            "result": result,
            "processed_at": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error processing MCP request: {e}")
        self.update_state(
            state="FAILURE",
            meta={"error": str(e)}
        )
        raise


@celery_app.task(bind=True)
def cleanup_old_data(self, days: int = 30) -> Dict[str, Any]:
    """Cleanup old data from database."""
    try:
        logger.info(f"Cleaning up data older than {days} days")
        
        # Here you would implement cleanup logic
        # For example, delete old chat messages, expired sessions, etc.
        
        return {
            "status": "success",
            "cleaned_at": datetime.utcnow().isoformat(),
            "days": days
        }
        
    except Exception as e:
        logger.error(f"Error cleaning up data: {e}")
        self.update_state(
            state="FAILURE",
            meta={"error": str(e)}
        )
        raise


@celery_app.task(bind=True)
def generate_analytics_report(self, start_date: str, end_date: str) -> Dict[str, Any]:
    """Generate analytics report."""
    try:
        logger.info(f"Generating analytics report from {start_date} to {end_date}")
        
        # Here you would implement analytics generation
        # For example, user activity, chat statistics, etc.
        
        return {
            "status": "success",
            "report_generated_at": datetime.utcnow().isoformat(),
            "period": {"start": start_date, "end": end_date}
        }
        
    except Exception as e:
        logger.error(f"Error generating analytics report: {e}")
        self.update_state(
            state="FAILURE",
            meta={"error": str(e)}
        )
        raise


@celery_app.task(bind=True)
def process_azure_service_bus_message(self, message_body: Dict[str, Any]) -> Dict[str, Any]:
    """Process message from Azure Service Bus."""
    try:
        logger.info(f"Processing Azure Service Bus message: {message_body}")
        
        # Extract message data
        message_type = message_body.get("type", "unknown")
        payload = message_body.get("payload", {})
        
        # Process based on message type
        if message_type == "chat_message":
            result = process_chat_message.delay(
                chat_id=payload.get("chat_id"),
                user_id=payload.get("user_id"),
                message=payload.get("message")
            )
        elif message_type == "notification":
            result = send_notification.delay(
                user_id=payload.get("user_id"),
                message=payload.get("message"),
                notification_type=payload.get("type", "info")
            )
        else:
            logger.warning(f"Unknown message type: {message_type}")
            result = None
        
        return {
            "status": "success",
            "message_type": message_type,
            "processed_at": datetime.utcnow().isoformat(),
            "task_id": result.id if result else None
        }
        
    except Exception as e:
        logger.error(f"Error processing Azure Service Bus message: {e}")
        self.update_state(
            state="FAILURE",
            meta={"error": str(e)}
        )
        raise


@celery_app.task(bind=True)
def send_azure_service_bus_message(self, message_body: Dict[str, Any], message_type: str = "default") -> Dict[str, Any]:
    """Send message to Azure Service Bus."""
    try:
        logger.info(f"Sending message to Azure Service Bus: {message_type}")
        
        # Use the Azure Service Bus service
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        result = loop.run_until_complete(
            azure_service_bus.send_message(message_body, message_type)
        )
        
        loop.close()
        
        return {
            "status": "success" if result else "failed",
            "message_type": message_type,
            "sent_at": datetime.utcnow().isoformat(),
            "azure_service_bus_result": result
        }
        
    except Exception as e:
        logger.error(f"Error sending message to Azure Service Bus: {e}")
        self.update_state(
            state="FAILURE",
            meta={"error": str(e)}
        )
        raise


@celery_app.task(bind=True)
def health_check_azure_service_bus(self) -> Dict[str, Any]:
    """Health check for Azure Service Bus connection."""
    try:
        logger.info("Performing Azure Service Bus health check")
        
        # Use the Azure Service Bus service for health check
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        result = loop.run_until_complete(
            azure_service_bus.health_check()
        )
        
        loop.close()
        
        return result
        
    except Exception as e:
        logger.error(f"Azure Service Bus health check failed: {e}")
        self.update_state(
            state="FAILURE",
            meta={"error": str(e)}
        )
        raise


# Global persistent event loop for Celery worker (one per worker process)
_worker_event_loop = None
_loop_lock = None

def _get_or_create_worker_loop():
    """Get or create a persistent event loop for the worker process."""
    global _worker_event_loop, _loop_lock
    
    if _loop_lock is None:
        _loop_lock = threading.Lock()
    
    with _loop_lock:
        if _worker_event_loop is None or _worker_event_loop.is_closed():
            _worker_event_loop = _create_platform_event_loop()
            asyncio.set_event_loop(_worker_event_loop)
            logger.debug("Created new persistent event loop for worker")
        return _worker_event_loop


def _run_async_safely(coro):
    """
    Safely run async code in a Celery task across all platforms (Linux, Windows, macOS).
    
    Handles event loop creation/cleanup properly to avoid:
    - SIGSEGV on macOS with prefork workers
    - Event loop conflicts on Windows
    - Thread safety issues on Linux
    - "attached to a different loop" errors with asyncpg/SQLAlchemy
    
    Uses a persistent event loop per worker process to ensure database connections
    remain valid across multiple task executions.
    """
    current_platform = platform.system().lower()
    
    try:
        # Use a persistent event loop per worker process
        # This ensures database connections remain valid across tasks
        loop = _get_or_create_worker_loop()
        
        # Check if loop is running (shouldn't be in Celery worker context)
        if loop.is_running():
            # If loop is running, we need to use nest_asyncio or create a new one
            logger.warning("Event loop is already running, creating new loop")
            new_loop = _create_platform_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                return new_loop.run_until_complete(coro)
            finally:
                _cleanup_event_loop(new_loop)
                asyncio.set_event_loop(loop)  # Restore persistent loop
        else:
            # Use the persistent loop
            return loop.run_until_complete(coro)
                
    except Exception as e:
        logger.error(f"Error in _run_async_safely on {current_platform}: {e}", exc_info=True)
        raise


def _create_platform_event_loop():
    """
    Create an event loop appropriate for the current platform.
    
    - Windows: Uses SelectorEventLoop (better for async database operations)
    - macOS/Linux: Uses default event loop policy
    """
    current_platform = platform.system().lower()
    
    if current_platform == "windows":
        # On Windows, use SelectorEventLoop for better compatibility with async database operations
        # ProactorEventLoop (default on Windows) can have issues with some async libraries
        try:
            import selectors
            selector = selectors.SelectSelector()
            loop = asyncio.SelectorEventLoop(selector)
            logger.debug("Using SelectorEventLoop on Windows")
            return loop
        except Exception as e:
            logger.warning(f"Failed to create SelectorEventLoop on Windows: {e}, using default")
            return asyncio.new_event_loop()
    else:
        # macOS and Linux: use default event loop
        return asyncio.new_event_loop()


def _cleanup_event_loop(loop):
    """
    Safely cleanup an event loop, canceling pending tasks.
    Works across all platforms.
    """
    if loop is None or loop.is_closed():
        return
    
    try:
        # Cancel any pending tasks
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        if pending:
            logger.debug(f"Canceling {len(pending)} pending tasks")
            for task in pending:
                if not task.done():
                    task.cancel()
            
            # Wait for cancellation to complete (with timeout)
            try:
                loop.run_until_complete(
                    asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True),
                        timeout=2.0
                    )
                )
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for task cancellation")
            except Exception as e:
                logger.debug(f"Error during task cancellation: {e}")
    except Exception as e:
        logger.debug(f"Error cleaning up pending tasks: {e}")
    
    # Close the loop
    try:
        if not loop.is_closed():
            loop.close()
    except Exception as e:
        logger.debug(f"Error closing event loop: {e}")


@celery_app.task(bind=True, name="check_agent_health_periodic")
def check_agent_health_periodic(self) -> Dict[str, Any]:
    """
    Periodic task to check agent health every 30 minutes.
    Checks both mcp_server_link and agent_health_url.
    Response codes 200 and 401 are considered healthy.
    """
    # Log immediately when task is called (before any processing)
    print("=" * 80)
    print("🏥 TASK RECEIVED: check_agent_health_periodic")
    print(f"Task ID: {self.request.id}")
    print(f"Task Name: {self.request.task}")
    print("=" * 80)
    
    logger.info("=" * 80)
    logger.info("🏥 STARTING AGENT HEALTH CHECK TASK")
    logger.info(f"Task ID: {self.request.id}")
    logger.info(f"Task Name: {self.request.task}")
    logger.info("=" * 80)
    
    try:
        # Update task state to PROGRESS
        self.update_state(
            state="PROGRESS",
            meta={"status": "Checking agent health", "progress": 0}
        )
        
        # Safely run async health check
        logger.info("🔄 Executing async health check for all agents...")
        print("🔄 Executing async health check for all agents...")
        
        result = _run_async_safely(_check_all_agents_health())
        
        logger.info("=" * 80)
        logger.info("✅ AGENT HEALTH CHECK TASK COMPLETED")
        logger.info(f"Results: {result}")
        logger.info("=" * 80)
        
        print("=" * 80)
        print("✅ AGENT HEALTH CHECK TASK COMPLETED")
        print(f"Results: {result}")
        print("=" * 80)
        
        return result
        
    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"❌ ERROR IN AGENT HEALTH CHECK TASK: {e}", exc_info=True)
        logger.error("=" * 80)
        
        print("=" * 80)
        print(f"❌ ERROR IN AGENT HEALTH CHECK TASK: {e}")
        print("=" * 80)
        
        self.update_state(
            state="FAILURE",
            meta={"error": str(e)}
        )
        raise


async def _check_agent_health(agent: Agent) -> tuple[str, Optional[datetime]]:
    """
    Check health of a single agent by calling mcp_server_link and agent_health_url.
    
    Logic:
    - If both URLs exist, check both
    - If only one URL exists, check that one
    - Status 200, 401, or 403 = healthy (endpoint exists and responds)
    - If at least one URL is healthy, agent is healthy
    - Timeout: 15 seconds (increased from 5 seconds for slow endpoints)
    
    Healthy Status Codes:
    - 200: OK (successful response)
    - 401: Unauthorized (endpoint exists, needs authentication)
    - 403: Forbidden (endpoint exists, access denied but responding)
    
    Returns:
        Tuple of (health_status, last_health_check_time)
        health_status: "healthy", "unhealthy", or "unknown"
    """
    health_status = "unknown"
    last_health_check = datetime.utcnow()
    
    # Collect URLs to check: mcp_url and health_url (if they exist)
    urls_to_check = []
    if agent.mcp_url:
        urls_to_check.append(("mcp_url", agent.mcp_url))
    if agent.health_url:
        urls_to_check.append(("health_url", agent.health_url))
    
    if not urls_to_check:
        logger.debug(f"Agent {agent.id} ({agent.name}) has no URLs to check (mcp_url or health_url)")
        return "unknown", last_health_check
    
    logger.info(f"Agent {agent.id} ({agent.name}): Checking {len(urls_to_check)} URL(s) - {[name for name, _ in urls_to_check]}")
    
    # Check each URL and track results
    healthy_found = False
    unhealthy_found = False
    url_results = []
    
    # Prepare headers from agent_header field if available
    headers = {}
    if agent.agent_header and isinstance(agent.agent_header, dict):
        headers.update(agent.agent_header)
        logger.debug(f"Agent {agent.id} ({agent.name}): Using custom headers from agent_header")
    
    for url_name, url in urls_to_check:
        try:
            # Increased timeout to 15 seconds for slow endpoints
            # Some MCP servers or health endpoints might take longer to respond
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Try GET request to the URL with headers if available
                response = await client.get(url, headers=headers if headers else None)
                status_code = response.status_code
                
                # Healthy status codes:
                # - 200: OK (successful response)
                # - 401: Unauthorized (endpoint exists, just needs auth - this is healthy)
                # - 403: Forbidden (endpoint exists and responds, but access denied - also healthy)
                # - 404: Not Found (endpoint doesn't exist - unhealthy)
                # - 5xx: Server errors (unhealthy)
                is_healthy = status_code in [200, 401, 403]
                url_results.append((url_name, url, is_healthy, status_code))
                
                if is_healthy:
                    healthy_found = True
                    logger.info(f"Agent {agent.id} ({agent.name}) {url_name} check passed: {url} returned {status_code}")
                else:
                    unhealthy_found = True
                    logger.warning(f"Agent {agent.id} ({agent.name}) {url_name} check failed: {url} returned {status_code}")
                    
        except httpx.TimeoutException:
            unhealthy_found = True
            url_results.append((url_name, url, False, "timeout"))
            logger.warning(f"Agent {agent.id} ({agent.name}) {url_name} check timeout: {url}")
        except Exception as e:
            unhealthy_found = True
            url_results.append((url_name, url, False, f"error: {str(e)}"))
            logger.warning(f"Agent {agent.id} ({agent.name}) {url_name} check error: {url} - {str(e)}")
    
    # Determine final status based on results
    # If at least one URL is healthy, agent is considered healthy
    # This allows agents to work even if one endpoint is down
    if healthy_found:
        health_status = "healthy"
        logger.info(f"Agent {agent.id} ({agent.name}) is HEALTHY - at least one URL passed")
    elif unhealthy_found:
        health_status = "unhealthy"
        logger.warning(f"Agent {agent.id} ({agent.name}) is UNHEALTHY - all URLs failed")
    else:
        health_status = "unknown"
        logger.warning(f"Agent {agent.id} ({agent.name}) status is UNKNOWN - no results")
    
    return health_status, last_health_check


async def _check_all_agents_health() -> Dict[str, Any]:
    """
    Check health of all enabled (active) agents and update database.
    
    Only checks agents where is_enabled = True.
    Disabled (inactive) agents are skipped to avoid unnecessary health checks.
    """
    checked_count = 0
    healthy_count = 0
    unhealthy_count = 0
    unknown_count = 0
    
    # Use a fresh session for this task
    db = get_celery_session()
    try:
        # Get only enabled (active) agents - skip disabled/inactive agents and drafted agents
        result = await db.execute(
            select(Agent).where(
                and_(
                    Agent.is_enabled == True,
                    or_(
                        Agent.status.ilike('active'),  # Case-insensitive match for 'active'
                        Agent.status.is_(None)  # Include NULL status for backward compatibility
                    )
                )
            )
        )
        agents = result.scalars().all()
        
        logger.info(f"Checking health for {len(agents)} enabled (active) agents (disabled agents skipped)")
        
        for agent in agents:
            try:
                health_status, last_health_check = await _check_agent_health(agent)
                
                # Update agent health status in database
                await db.execute(
                    update(Agent)
                    .where(Agent.id == agent.id)
                    .values(
                        is_healthy=(health_status == "healthy"),
                        health_status=health_status,
                        last_health_check=last_health_check,
                        updated_at=datetime.utcnow()
                    )
                )
                
                checked_count += 1
                if health_status == "healthy":
                    healthy_count += 1
                elif health_status == "unhealthy":
                    unhealthy_count += 1
                else:
                    unknown_count += 1
                    
            except Exception as e:
                logger.error(f"Error checking health for agent {agent.id}: {str(e)}", exc_info=True)
                try:
                    # Mark as unknown on error
                    await db.execute(
                        update(Agent)
                        .where(Agent.id == agent.id)
                        .values(
                            health_status="unknown",
                            last_health_check=datetime.utcnow(),
                            updated_at=datetime.utcnow()
                        )
                    )
                except Exception as update_error:
                    logger.error(f"Failed to update agent {agent.id} status to unknown: {update_error}")
                unknown_count += 1
        
        await db.commit()
        logger.info(f"Agent health check completed: {checked_count} checked, {healthy_count} healthy, {unhealthy_count} unhealthy, {unknown_count} unknown")
        
        return {
            "status": "success",
            "checked_at": datetime.utcnow().isoformat(),
            "checked_count": checked_count,
            "healthy_count": healthy_count,
            "unhealthy_count": unhealthy_count,
            "unknown_count": unknown_count
        }
        
    except Exception as e:
        try:
            await db.rollback()
        except Exception as rollback_error:
            logger.error(f"Error during rollback: {rollback_error}")
        logger.error(f"Error in agent health check: {str(e)}", exc_info=True)
        raise
    finally:
        # Ensure session is properly closed
        try:
            await db.close()
        except Exception as close_error:
            logger.error(f"Error closing database session: {close_error}")
