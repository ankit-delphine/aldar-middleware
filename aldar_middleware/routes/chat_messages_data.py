"""Chat messages data API routes - queries from database tables."""

import json
from typing import List, Dict, Any, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from aldar_middleware.database.base import get_db
from aldar_middleware.models.user import User
from aldar_middleware.models.sessions import Session
from aldar_middleware.models.messages import Message
from aldar_middleware.models.menu import Agent
from aldar_middleware.models.feedback import FeedbackData, FeedbackEntityType, FeedbackRating
from aldar_middleware.models.run import Run
from aldar_middleware.models.event import Event
from aldar_middleware.models.run_message import RunMessage
from aldar_middleware.models.run_metrics import RunMetrics
from aldar_middleware.models.run_input import RunInput
from aldar_middleware.models.memory import Memory
from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.settings.context import get_correlation_id
from aldar_middleware.settings.settings import settings
from loguru import logger
from fastapi import HTTPException

router = APIRouter()

# Rate limiting constants
RATE_LIMIT_REQUESTS_PER_MINUTE = settings.rate_limit_requests
RATE_LIMIT_WINDOW_SECONDS = settings.rate_limit_window

# Rate limiting store and lock
from collections import deque
from asyncio import Lock
from datetime import datetime
from typing import Deque

_rate_limit_store: Dict[str, Deque[datetime]] = {}
_rate_limit_lock = Lock()


def _chat_error_response(
    *,
    error_message: str,
    error_code: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Format chat error response."""
    return {
        "success": False,
        "error": error_message,
        "error_code": error_code,
        "details": details or None,
    }


def _raise_chat_error(
    *,
    status_code: int,
    error_code: str,
    error_message: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Raise HTTP exception with formatted chat error."""
    raise HTTPException(
        status_code=status_code,
        detail=_chat_error_response(
            error_message=error_message,
            error_code=error_code,
            details=details,
        ),
    )


def _ensure_session_active(session: Session) -> None:
    """Ensure session is active (currently no expiration check)."""
    pass


async def _enforce_chat_rate_limit(user: User) -> None:
    """Enforce rate limiting for chat requests."""
    user_key = str(user.id)
    now = datetime.utcnow()
    async with _rate_limit_lock:
        bucket = _rate_limit_store.setdefault(user_key, deque())
        while bucket and (now - bucket[0]).total_seconds() > RATE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()
        if not bucket:
            _rate_limit_store.pop(user_key, None)
            bucket = _rate_limit_store.setdefault(user_key, deque())
        if len(bucket) >= RATE_LIMIT_REQUESTS_PER_MINUTE:
            _raise_chat_error(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                error_code="RATE_LIMIT_EXCEEDED",
                error_message=f"Rate limit exceeded. Maximum {RATE_LIMIT_REQUESTS_PER_MINUTE} requests per {RATE_LIMIT_WINDOW_SECONDS} seconds.",
                details={"field": "rate_limit", "message": "Too many requests"},
            )
        bucket.append(now)


def _construct_events_from_run_data(
    run: Run,
    session_id_str: str,
    metrics: Optional[RunMetrics] = None
) -> List[Dict[str, Any]]:
    """Construct basic events from run data when events are not available in database.
    
    This is used for agent-specific runs where events might not be stored in the events table.
    """
    events = []
    run_id = run.run_id
    status = (run.status or "").upper()
    created_at = run.created_at
    
    # Convert created_at to timestamp
    if created_at:
        created_at_ts = int(created_at.timestamp())
    else:
        from datetime import timezone
        created_at_ts = int(datetime.now(timezone.utc).timestamp())
    
    # Get metrics data
    metrics_dict = {}
    if metrics:
        metrics_dict = {
            "input_tokens": metrics.input_tokens or 0,
            "output_tokens": metrics.output_tokens or 0,
            "total_tokens": metrics.total_tokens or 0,
            "time_to_first_token": metrics.time_to_first_token,
            "duration": metrics.duration,
        }
    
    # Construct RunStarted event
    events.append({
        "created_at": created_at_ts,
        "event": "RunStarted",
        "run_id": run_id,
        "session_id": session_id_str,
        "agent_id": str(run.agent_id) if run.agent_id else None,
        "agent_name": run.agent_name,
        "model": run.model,
        "model_provider": run.model_provider,
        "content": "",
        "content_type": run.content_type or "text",
    })
    
    # If run is completed, add RunContentCompleted and RunCompleted events
    if status == "COMPLETED":
        # Estimate completion time (use duration from metrics if available)
        duration = metrics_dict.get("duration", 0)
        if duration:
            completed_at_ts = created_at_ts + int(duration)
        else:
            completed_at_ts = created_at_ts + 1
        
        events.append({
            "created_at": completed_at_ts - 1,
            "event": "RunContentCompleted",
            "run_id": run_id,
            "session_id": session_id_str,
            "agent_id": str(run.agent_id) if run.agent_id else None,
            "agent_name": run.agent_name,
            "model": run.model,
            "model_provider": run.model_provider,
            "content": "",
            "content_type": run.content_type or "text",
            "metrics": metrics_dict if metrics_dict else None,
        })
        
        events.append({
            "created_at": completed_at_ts,
            "event": "RunCompleted",
            "run_id": run_id,
            "session_id": session_id_str,
            "agent_id": str(run.agent_id) if run.agent_id else None,
            "agent_name": run.agent_name,
            "model": run.model,
            "model_provider": run.model_provider,
            "content": run.content or "",
            "content_type": run.content_type or "text",
            "metrics": metrics_dict if metrics_dict else None,
        })
    elif status == "FAILED":
        # Add RunFailed event
        events.append({
            "created_at": created_at_ts + 1,
            "event": "RunFailed",
            "run_id": run_id,
            "session_id": session_id_str,
            "agent_id": str(run.agent_id) if run.agent_id else None,
            "agent_name": run.agent_name,
            "model": run.model,
            "model_provider": run.model_provider,
            "content": run.content or "",
            "content_type": run.content_type or "text",
        })
    
    return events


@router.get("/sessions/{session_id}/messages-data")
async def get_chat_messages_data_by_session(
    session_id: UUID,
    limit: int = Query(10, ge=1, le=20),
    before_message_id: Optional[UUID] = Query(None, description="Return messages created before this message ID"),
    include_system: bool = Query(False, description="Include system messages"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Retrieve chat messages for a session with pagination and rich metadata from database tables."""
    correlation_id = get_correlation_id()

    await _enforce_chat_rate_limit(current_user)

    # Verify session exists and belongs to user
    session_result = await db.execute(
        select(Session).where(
            Session.id == session_id,
            Session.user_id == current_user.id,
        )
    )
    session = session_result.scalar_one_or_none()
    
    if not session:
        _raise_chat_error(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="SESSION_NOT_FOUND",
            error_message="Chat session not found",
            details={"field": "session_id", "message": "Specified session does not exist"},
        )

    _ensure_session_active(session)

    try:
        # Get session_id string for querying ERD tables (they use public_id as string)
        session_id_str = str(session.public_id)
        session_id_alt = str(session.id)  # Also try with session.id
        
        logger.info(
            f"Querying messages-data for session_id={session_id}, "
            f"public_id={session_id_str}, id={session_id_alt}"
        )
        
        # Query messages from messages table
        messages_query = select(Message).where(
            Message.session_id == session.id,
            Message.user_id == current_user.id
        )
        
        if not include_system:
            messages_query = messages_query.where(Message.role != "system")
        
        if before_message_id:
            # Get the timestamp of the before_message_id
            before_msg_result = await db.execute(
                select(Message.created_at).where(Message.id == before_message_id)
            )
            before_timestamp = before_msg_result.scalar_one_or_none()
            if before_timestamp:
                messages_query = messages_query.where(Message.created_at < before_timestamp)
        
        messages_query = messages_query.order_by(Message.created_at.desc()).limit(limit + 1)
        
        messages_result = await db.execute(messages_query)
        all_messages = list(messages_result.scalars().all())
        
        # Check if there are more messages
        has_more = len(all_messages) > limit
        if has_more:
            all_messages = all_messages[:limit]
        
        # Reverse to get oldest first
        all_messages.reverse()
        
        # Query runs from runs table for this session
        # Try both public_id and id as session_id might be stored as either
        runs_query = select(Run).where(
            (Run.session_id == session_id_str) | (Run.session_id == session_id_alt)
        ).order_by(Run.created_at.desc())
        
        runs_result = await db.execute(runs_query)
        runs = list(runs_result.scalars().all())
        
        logger.info(
            f"Found {len(runs)} runs for session_id={session_id_str} "
            f"(also tried {session_id_alt})"
        )
        
        # Build runs_summary with events, metrics, and inputs
        runs_summary: List[Dict[str, Any]] = []
        # Create a map of run_id -> run data for easy lookup
        runs_map: Dict[str, Dict[str, Any]] = {}  # run_id -> run data including events, team info
        
        for run in runs:
            # Query events for this run
            events_query = select(Event).where(
                Event.run_id == run.run_id
            ).order_by(Event.created_at)
            
            events_result = await db.execute(events_query)
            events = list(events_result.scalars().all())
            
            logger.info(
                f"Run {run.run_id}: Found {len(events)} events. "
                f"Event types: {[e.event_type for e in events] if events else 'none'}"
            )
            
            # If no events found and this is an agent run (not a team run), construct basic events from run data
            is_team_run = False
            if run.agent_name:
                agent_name_lower = run.agent_name.lower()
                is_team_run = "router" in agent_name_lower or "team" in agent_name_lower
            
            # Query metrics for this run (needed for constructing events if events are missing)
            metrics_query = select(RunMetrics).where(
                RunMetrics.run_id == run.run_id
            )
            metrics_result = await db.execute(metrics_query)
            metrics = metrics_result.scalar_one_or_none()
            
            logger.info(
                f"Run {run.run_id}: Found metrics={metrics is not None}, "
                f"metrics_id={metrics.metrics_id if metrics else None}, "
                f"input_tokens={metrics.input_tokens if metrics else None}, "
                f"output_tokens={metrics.output_tokens if metrics else None}, "
                f"total_tokens={metrics.total_tokens if metrics else None}"
            )
            
            # Query input for this run
            input_query = select(RunInput).where(
                RunInput.run_id == run.run_id
            )
            input_result = await db.execute(input_query)
            run_input = input_result.scalar_one_or_none()
            
            # Query run_messages for this run
            run_messages_query = select(RunMessage).where(
                RunMessage.run_id == run.run_id
            ).order_by(RunMessage.created_at)
            
            run_messages_result = await db.execute(run_messages_query)
            run_messages = list(run_messages_result.scalars().all())
            
            # Extract team_id and team_name from run FIRST (before processing events)
            # This way events can inherit team info from the run
            team_id = None
            team_name = None
            
            # Check if agent_name suggests it's a team/router
            if run.agent_name:
                agent_name_lower = run.agent_name.lower()
                if "router" in agent_name_lower or "team" in agent_name_lower:
                    team_name = run.agent_name
                    # Generate team_id from team_name (e.g., "Multi-Agent Router" -> "multi-agent-router")
                    team_id = agent_name_lower.replace(" ", "-")
            
            # Try parsing run content as JSON for team info
            if not team_id and run.content:
                try:
                    run_content_json = json.loads(run.content)
                    if isinstance(run_content_json, dict):
                        if "team_id" in run_content_json:
                            team_id = run_content_json["team_id"]
                        if "team_name" in run_content_json:
                            team_name = run_content_json["team_name"]
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass
            
            # Format events
            events_payload = []
            
            # If no events found in database and this is an agent run (not team), construct events from run data
            # Always construct events for agent runs if no events exist in database
            if not events and not is_team_run:
                logger.info(
                    f"Run {run.run_id}: No events found in database. "
                    f"Constructing events from run data for agent run. "
                    f"Run status: {run.status}, Agent: {run.agent_name}"
                )
                events_payload = _construct_events_from_run_data(
                    run=run,
                    session_id_str=session_id_str,
                    metrics=metrics
                )
                logger.info(
                    f"Run {run.run_id}: Constructed {len(events_payload)} events: "
                    f"{[e.get('event') for e in events_payload]}"
                )
            elif events:
                # Process events from database
                for idx, event in enumerate(events):
                    # Try to parse event content as JSON to extract additional fields
                    event_data: Dict[str, Any] = {
                        "created_at": int(event.created_at.timestamp()) if event.created_at else None,
                        "event": event.event_type or "Unknown",
                        "run_id": event.run_id,
                        "session_id": event.session_id or session_id_str,
                        "agent_id": str(event.agent_id) if event.agent_id else None,
                        "agent_name": event.agent_name,
                        "team_id": team_id,  # Use team_id from run (can be overridden by event content)
                        "team_name": team_name,  # Use team_name from run (can be overridden by event content)
                        "model": event.model,
                        "model_provider": event.model_provider,
                        "content": event.content,
                        "content_type": event.content_type,
                    }
                    
                    # Try to parse content as JSON to extract session_summary, metrics, team info
                    if event.content:
                        try:
                            # Try to parse as JSON
                            content_json = json.loads(event.content)
                            if isinstance(content_json, dict):
                                # Extract session_summary if present
                                if "session_summary" in content_json:
                                    event_data["session_summary"] = content_json["session_summary"]
                                # Extract metrics if present
                                if "metrics" in content_json:
                                    event_data["metrics"] = content_json["metrics"]
                                # Extract team info if present
                                if "team_id" in content_json:
                                    event_data["team_id"] = content_json["team_id"]
                                if "team_name" in content_json:
                                    event_data["team_name"] = content_json["team_name"]
                        except (json.JSONDecodeError, ValueError, TypeError):
                            # Content is not JSON, keep as is
                            pass
                    
                    # For TeamSessionSummaryCompleted events, always check Session.summary field
                    # Session.summary is the primary source for session_summary in this event type
                    if event.event_type == "TeamSessionSummaryCompleted":
                        # Refresh session from database to get latest summary (summary might be updated after event creation)
                        await db.refresh(session, ["summary", "updated_at", "session_data", "session_state"])
                        
                        # Prefer session_summary from event content if available, otherwise use Session.summary
                        if "session_summary" not in event_data:
                            session_summary_data = None
                            
                            # First, try Session.summary field
                            if session.summary:
                                logger.debug(
                                    f"Session.summary found for session {session.id}: "
                                    f"type={type(session.summary)}, value={str(session.summary)[:100]}"
                                )
                                session_summary_data = session.summary
                            
                            # If not found in summary, check session_data
                            if not session_summary_data and session.session_data:
                                if isinstance(session.session_data, dict) and "session_summary" in session.session_data:
                                    session_summary_data = session.session_data["session_summary"]
                                    logger.debug(
                                        f"Found session_summary in session_data for session {session.id}"
                                    )
                            
                            # If not found, check session_state
                            if not session_summary_data and session.session_state:
                                if isinstance(session.session_state, dict) and "session_summary" in session.session_state:
                                    session_summary_data = session.session_state["session_summary"]
                                    logger.debug(
                                        f"Found session_summary in session_state for session {session.id}"
                                    )
                            
                            # Format session summary to match expected structure
                            if session_summary_data:
                                if isinstance(session_summary_data, dict):
                                    # If summary is already in the right format, use it
                                    if "summary" in session_summary_data or "topics" in session_summary_data:
                                        event_data["session_summary"] = session_summary_data
                                        logger.info(
                                            f"Added session_summary to TeamSessionSummaryCompleted event "
                                            f"from Session (already formatted) for run {run.run_id}"
                                        )
                                    else:
                                        # If it's a different format, wrap it
                                        event_data["session_summary"] = {
                                            "summary": str(session_summary_data.get("summary", session_summary_data)),
                                            "topics": session_summary_data.get("topics", []),
                                            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
                                        }
                                        logger.info(
                                            f"Added session_summary to TeamSessionSummaryCompleted event "
                                            f"from Session (wrapped) for run {run.run_id}"
                                        )
                                else:
                                    # If summary is a string, convert to expected format
                                    event_data["session_summary"] = {
                                        "summary": str(session_summary_data),
                                        "topics": [],
                                        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
                                    }
                                    logger.info(
                                        f"Added session_summary to TeamSessionSummaryCompleted event "
                                        f"from Session (string converted) for run {run.run_id}"
                                    )
                            else:
                                logger.warning(
                                    f"TeamSessionSummaryCompleted event for run {run.run_id} but "
                                    f"session_summary not found in Session.summary, session_data, or session_state "
                                    f"for session {session.id}. Event content: {event.content[:100] if event.content else 'empty'}"
                                )
                        else:
                            logger.debug(
                                f"TeamSessionSummaryCompleted event for run {run.run_id} already has "
                                f"session_summary from event content"
                            )
                    
                    # If metrics are available from RunMetrics, add them to completion events
                    # Only add if metrics not already present from content JSON
                    if metrics and "metrics" not in event_data:
                        # Add metrics to completion events
                        event_types_with_metrics = [
                            "TeamRunCompleted", 
                            "RunCompleted",
                            "TeamRunContentCompleted",
                            "RunContentCompleted"
                        ]
                        # Check if this is a completion event
                        is_completion_event = event.event_type in event_types_with_metrics
                        # Or if it's the last event and the run is completed
                        is_last_completion = (idx == len(events) - 1 and run.status == "COMPLETED")
                        
                        if is_completion_event or is_last_completion:
                            event_data["metrics"] = {
                                "input_tokens": metrics.input_tokens or 0,
                                "output_tokens": metrics.output_tokens or 0,
                                "total_tokens": metrics.total_tokens or 0,
                            }
                            logger.info(
                                f"Added metrics to event {event.event_type} for run {run.run_id}: "
                                f"input={metrics.input_tokens}, output={metrics.output_tokens}, "
                                f"total={metrics.total_tokens}"
                            )
                    elif not metrics and idx == len(events) - 1 and run.status == "COMPLETED":
                        # Log if metrics are missing for completed run
                        logger.warning(
                            f"Run {run.run_id} is COMPLETED but no metrics found in RunMetrics table. "
                            f"Last event: {event.event_type}"
                        )
                    
                    events_payload.append(event_data)
            
            # Format metrics
            metrics_payload = None
            if metrics:
                metrics_payload = {
                    "input_tokens": metrics.input_tokens or 0,
                    "output_tokens": metrics.output_tokens or 0,
                    "total_tokens": metrics.total_tokens or 0,
                    "time_to_first_token": metrics.time_to_first_token,
                    "duration": metrics.duration,
                }
                logger.info(
                    f"Run {run.run_id}: Created metrics_payload from RunMetrics table with "
                    f"input_tokens={metrics_payload['input_tokens']}, "
                    f"output_tokens={metrics_payload['output_tokens']}, "
                    f"total_tokens={metrics_payload['total_tokens']}"
                )
            else:
                # Fallback: Try to extract metrics from event content (in case metrics are stored there)
                logger.warning(
                    f"Run {run.run_id}: No metrics found in RunMetrics table. "
                    f"Run status={run.status}. Trying to extract from events..."
                )
                # Look for metrics in event content (check all events, prefer completion events)
                for event_data in events_payload:
                    if event_data.get("metrics"):
                        event_metrics = event_data.get("metrics")
                        # Ensure it's a dict with the right structure
                        if isinstance(event_metrics, dict):
                            metrics_payload = {
                                "input_tokens": event_metrics.get("input_tokens", 0),
                                "output_tokens": event_metrics.get("output_tokens", 0),
                                "total_tokens": event_metrics.get("total_tokens", 0),
                                "time_to_first_token": event_metrics.get("time_to_first_token"),
                                "duration": event_metrics.get("duration"),
                            }
                            logger.info(
                                f"Run {run.run_id}: Found metrics in event {event_data.get('event')}: "
                                f"input_tokens={metrics_payload['input_tokens']}, "
                                f"output_tokens={metrics_payload['output_tokens']}, "
                                f"total_tokens={metrics_payload['total_tokens']}"
                            )
                            break
            
            # Get agent information
            agent_id = str(run.agent_id) if run.agent_id else None
            agent_name = run.agent_name
            agent_public_id = None
            
            if run.agent_id:
                agent_result = await db.execute(
                    select(Agent).where(Agent.id == run.agent_id)
                )
                agent = agent_result.scalar_one_or_none()
                if agent:
                    agent_public_id = str(agent.public_id)
                    if not agent_name:
                        agent_name = agent.name
            
            # Count messages in this run
            message_count = len(run_messages)
            
            run_data = {
                "run_id": run.run_id,
                "agent_id": agent_id,
                "agent_name": agent_name,
                "agent_public_id": agent_public_id,
                "team_id": team_id,
                "team_name": team_name,
                "status": run.status,
                "created_at": int(run.created_at.timestamp()) if run.created_at else None,
                "message_count": message_count,
                "events": events_payload,
                "metrics": metrics_payload,
                "input": run_input.input_content if run_input else None,
                "content": run.content,
                "content_type": run.content_type,
                "model": run.model,
                "model_provider": run.model_provider,
            }
            runs_summary.append(run_data)
            # Store in map for easy lookup
            runs_map[run.run_id] = run_data
        
        # Query memories for this session (try both session_id formats)
        memories_query = select(Memory).where(
            (Memory.session_id == session_id_str) | (Memory.session_id == session_id_alt)
        ).order_by(Memory.memory_id)
        
        memories_result = await db.execute(memories_query)
        memories = list(memories_result.scalars().all())
        
        # Format memories
        memories_payload = []
        for memory in memories:
            memories_payload.append({
                "memory_id": memory.memory_id,
                "memory_content": memory.memory_content,
                "memory_type": memory.memory_type,
            })
        
        # Format messages payload
        messages_payload: List[Dict[str, Any]] = []
        
        def format_content(content: Optional[str]) -> str:
            """Format message content - clean whitespace while preserving markdown.
            
            Preserves ALL user/assistant content and only removes the <additional context>
            metadata section that is added by AGNO.
            """
            if not content:
                return ""
            
            # Remove additional context section (added by AGNO) from content
            # This section contains metadata that shouldn't be shown to users
            # IMPORTANT: We preserve ALL content before this section - nothing is lost
            if "<additional context>" in content:
                # Split on the additional context marker and take everything BEFORE it
                # This preserves the complete user/assistant message content
                parts = content.split("\n\n<additional context>")
                if parts:
                    content = parts[0]  # Take full content before additional context
                    # Only strip trailing whitespace, preserve leading content
                    content = content.rstrip()
            
            # Remove excessive whitespace but preserve intentional line breaks
            # Split by newlines, strip each line, then rejoin
            lines = content.split('\n')
            cleaned_lines = []
            prev_empty = False
            
            for line in lines:
                stripped = line.rstrip()  # Remove trailing whitespace
                
                # Preserve single empty lines (for markdown paragraphs)
                if not stripped:
                    if not prev_empty:
                        cleaned_lines.append("")
                        prev_empty = True
                else:
                    cleaned_lines.append(stripped)
                    prev_empty = False
            
            # Remove leading/trailing empty lines
            while cleaned_lines and not cleaned_lines[0]:
                cleaned_lines.pop(0)
            while cleaned_lines and not cleaned_lines[-1]:
                cleaned_lines.pop()
            
            return '\n'.join(cleaned_lines)
        
        # Fetch feedback for all messages
        message_ids = [str(msg.id) for msg in all_messages]
        feedback_map: Dict[str, Dict[str, Any]] = {}
        
        if message_ids:
            normalized_message_ids = [m.lower().strip() for m in message_ids]
            feedback_query = select(FeedbackData).where(
                and_(
                    FeedbackData.entity_type == FeedbackEntityType.MESSAGE,
                    func.lower(FeedbackData.entity_id).in_(normalized_message_ids),
                    FeedbackData.user_id == str(current_user.id),
                    FeedbackData.deleted_at.is_(None),
                )
            )
            feedback_result = await db.execute(feedback_query)
            feedback_list = feedback_result.scalars().all()
            
            rating_to_reaction = {
                FeedbackRating.THUMBS_UP: "like",
                FeedbackRating.THUMBS_DOWN: "dislike",
                FeedbackRating.NEUTRAL: None,
            }
            
            for feedback in feedback_list:
                reaction = rating_to_reaction.get(feedback.rating, None)
                normalized_feedback_id = feedback.entity_id.lower().strip()
                feedback_map[normalized_feedback_id] = {
                    "reaction": reaction,
                    "comment": feedback.comment,
                    "feedback_id": str(feedback.feedback_id),
                    "created_at": feedback.created_at.isoformat() if feedback.created_at else None,
                }
        
        # Query all run_messages for all runs in this session (once, outside the loop)
        all_run_messages_map: Dict[str, List[RunMessage]] = {}  # run_id -> list of RunMessage
        if runs:
            all_run_ids = [run.run_id for run in runs]
            all_run_messages_query = select(RunMessage).where(
                RunMessage.run_id.in_(all_run_ids)
            )
            all_run_messages_result = await db.execute(all_run_messages_query)
            all_run_messages = list(all_run_messages_result.scalars().all())
            
            # Group by run_id
            for run_msg in all_run_messages:
                if run_msg.run_id not in all_run_messages_map:
                    all_run_messages_map[run_msg.run_id] = []
                all_run_messages_map[run_msg.run_id].append(run_msg)
        
        # Build message payloads
        for msg in all_messages:
            # Get agent information
            agent_id = str(msg.agent_id) if msg.agent_id else None
            agent_name = None
            agent_public_id = None
            
            if msg.agent_id:
                agent_result = await db.execute(
                    select(Agent).where(Agent.id == msg.agent_id)
                )
                agent = agent_result.scalar_one_or_none()
                if agent:
                    agent_public_id = str(agent.public_id)
                    agent_name = agent.name
            
            # Extract attachments from message
            attachments = []
            if msg.files:
                for file_info in msg.files if isinstance(msg.files, list) else []:
                    if isinstance(file_info, dict):
                        attachments.append({
                            "attachment_uuid": str(file_info.get("attachment_id") or file_info.get("id") or ""),
                            "filename": file_info.get("filename") or file_info.get("file_name"),
                            "url": file_info.get("blob_url") or file_info.get("download_url") or file_info.get("url"),
                        })
            
            # Get feedback
            message_id_str = str(msg.id)
            normalized_msg_id = message_id_str.lower().strip()
            feedback_data = feedback_map.get(normalized_msg_id)
            
            # Extract stream_id from tool_calls if present
            stream_id = None
            if msg.tool_calls and isinstance(msg.tool_calls, dict):
                stream_id = msg.tool_calls.get("stream_id") or msg.tool_calls.get("streamId")
            
            # Find run_id for this message by matching with runs
            # Strategy 1: Try to match via run_messages (exact content match)
            # Strategy 2: Match by timestamp proximity to run created_at
            run_id = None
            team_id = None
            team_name = None
            
            # Strategy 1: Match via run_messages
            best_match = None
            best_time_diff = None
            for run_id_key, run_messages_list in all_run_messages_map.items():
                for run_msg in run_messages_list:
                    if run_msg.role == msg.role:
                        # Try exact content match first
                        if run_msg.content == msg.content:
                            # Calculate time difference
                            if msg.created_at and run_msg.created_at:
                                time_diff = abs((msg.created_at - run_msg.created_at).total_seconds())
                                if best_time_diff is None or time_diff < best_time_diff:
                                    best_match = run_msg
                                    best_time_diff = time_diff
                                    run_id = run_id_key
                            elif not msg.created_at and not run_msg.created_at:
                                # Both missing timestamps, use this match
                                best_match = run_msg
                                run_id = run_id_key
                                break
                        # Try partial content match (in case content was formatted)
                        elif (run_msg.content and msg.content and 
                              (run_msg.content in msg.content or msg.content in run_msg.content)):
                            if msg.created_at and run_msg.created_at:
                                time_diff = abs((msg.created_at - run_msg.created_at).total_seconds())
                                if time_diff < 60:  # Within 60 seconds
                                    if best_time_diff is None or time_diff < best_time_diff:
                                        best_match = run_msg
                                        best_time_diff = time_diff
                                        run_id = run_id_key
                if best_match and not msg.created_at and not best_match.created_at:
                    break
            
            # Strategy 2: If no match found, try matching by timestamp proximity to run
            if not run_id and msg.created_at and runs:
                for run in runs:
                    if run.created_at:
                        # Check if message timestamp is within reasonable range of run (e.g., within 5 minutes)
                        time_diff = abs((msg.created_at - run.created_at).total_seconds())
                        if time_diff < 300:  # 5 minutes
                            # Use the closest run
                            if best_time_diff is None or time_diff < best_time_diff:
                                run_id = run.run_id
                                best_time_diff = time_diff
            
            # Extract team_id and team_name from the matched run
            if run_id and run_id in runs_map:
                run_data = runs_map[run_id]
                # Get team info from run data (already extracted from events)
                team_id = run_data.get("team_id")
                team_name = run_data.get("team_name")
                
                # If still not found, try to extract from events
                if not team_id or not team_name:
                    events_list = run_data.get("events", [])
                    for event_data in events_list:
                        if event_data.get("team_id"):
                            team_id = event_data.get("team_id")
                        if event_data.get("team_name"):
                            team_name = event_data.get("team_name")
                        if team_id and team_name:
                            break
            
            # Format content (clean whitespace while preserving markdown)
            # IMPORTANT: We preserve the FULL user/assistant content - only the 
            # <additional context> metadata section is removed
            raw_content = msg.content or ""
            formatted_content = format_content(raw_content)  # Full content preserved
            
            message_payload = {
                "message_id": message_id_str,
                "type": msg.role,
                "content": formatted_content,
                "attachments": attachments,
                "custom_fields": {},
                "agents_involved": [],
                "timestamp": msg.created_at.isoformat() if msg.created_at else None,
                "stream_id": stream_id,
                "run_id": run_id,
                "agent_id": agent_id,
                "agent_public_id": agent_public_id,
                "agent_name": agent_name,
                "team_id": team_id,
                "team_name": team_name,
            }
            
            # Only include feedback for assistant messages
            if msg.role == "assistant":
                message_payload["feedback"] = feedback_data if feedback_data else None
            
            messages_payload.append(message_payload)
        
        oldest_id = messages_payload[0]["message_id"] if messages_payload else None
        newest_id = messages_payload[-1]["message_id"] if messages_payload else None
        
        return {
            "success": True,
            "session_id": session.session_id,
            "messages": messages_payload,
            "has_more": has_more,
            "oldest_message_id": oldest_id,
            "newest_message_id": newest_id,
            "correlation_id": correlation_id,
            "runs_summary": runs_summary if runs_summary else [],
            "total_runs": len(runs_summary),
            "memories": memories_payload,
        }
        
    except Exception as e:
        logger.error(f"Error fetching messages from database: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        # Fallback to empty response on error
        return {
            "success": True,
            "session_id": session.session_id,
            "messages": [],
            "has_more": False,
            "oldest_message_id": None,
            "newest_message_id": None,
            "correlation_id": correlation_id,
            "runs_summary": [],
            "total_runs": 0,
            "memories": [],
        }

