"""Chat API routes."""

from typing import List, Dict, Any, Optional, Literal, Tuple, Deque, Union
from uuid import UUID, uuid4, uuid5, NAMESPACE_DNS
import hashlib
import json
from datetime import datetime, timedelta, timezone
from collections import deque
from asyncio import Lock
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, Header
from sqlalchemy import select, desc, func, case, and_, or_, exists, Boolean, cast, text, update, String, literal
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field, model_validator, field_validator, ConfigDict

from aldar_middleware.database.base import get_db
from aldar_middleware.database.redis_client import get_redis
from aldar_middleware.models.user import User
from aldar_middleware.models.sessions import Session
from aldar_middleware.models.messages import Message
from aldar_middleware.models.menu import Agent
from aldar_middleware.models.feedback import FeedbackData, FeedbackEntityType, FeedbackRating
from aldar_middleware.models.attachment import Attachment
from aldar_middleware.models.starter_prompt import StarterPrompt
from aldar_middleware.utils.agent_utils import determine_agent_type
from aldar_middleware.utils.streaming_utils import check_streaming_status
from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.orchestration.blob_storage import BlobStorageService
from aldar_middleware.auth.obo_utils import add_mcp_token_to_jwt
from aldar_middleware.services.ai_service import AIService
from aldar_middleware.services.question_tracker_service import increment_question_count
from aldar_middleware.settings.context import get_correlation_id
from aldar_middleware.settings.settings import settings
from loguru import logger
from aldar_middleware.monitoring import (
    log_chat_session_created,
    log_chat_message,
    log_chat_session_updated,
    log_starting_prompt_chosen,
    log_chat_session_deleted,
    log_chat_favorite_toggled,
    log_conversation_renamed,
    log_conversation_download,
    log_conversation_share,
)
from aldar_middleware.services.postgres_logs_service import postgres_logs_service
import re

router = APIRouter()


# Helper function to transform cancellation messages
def _transform_cancellation_message(content: Optional[str]) -> Optional[str]:
    """
    Transform technical cancellation messages from AGNO API to user-friendly messages.
    
    Args:
        content: The message content from AGNO API
        
    Returns:
        Transformed message or original content if not a cancellation message
    """
    if not content:
        return content
    
    # Pattern to match "Run <run_id> was cancelled" messages
    cancellation_pattern = r"^Run\s+[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\s+was\s+cancelled$"
    
    if re.match(cancellation_pattern, content.strip(), re.IGNORECASE):
        return "Reasoning stopped. Response generation has been cancelled."
    
    return content

GroupLiteral = Literal["all", "favourites", "today", "this_week", "last_week", "this_month", "this_year", "older"]

FAVORITE_METADATA_KEY = "is_favorite"
LEGACY_FAVORITE_METADATA_KEYS: Tuple[str, ...] = ("isFavorite", "is_favourite")
FAVORITE_METADATA_KEYS: Tuple[str, ...] = (FAVORITE_METADATA_KEY,) + LEGACY_FAVORITE_METADATA_KEYS


class FavoriteUpdateRequest(BaseModel):
    """Request model for updating favorite status."""

    is_favorite: bool = Field(..., description="Favourite status for the session")

    @model_validator(mode="before")
    @classmethod
    def _normalize_aliases(cls, values: Any) -> Any:
        if isinstance(values, dict) and FAVORITE_METADATA_KEY not in values:
            for key in LEGACY_FAVORITE_METADATA_KEYS + ("isFavourite",):
                if key in values:
                    values[FAVORITE_METADATA_KEY] = values[key]
                    break
        return values


class UpdateChatSessionRequest(BaseModel):
    """Request model for updating a chat session."""

    title: str = Field(..., min_length=1, max_length=255, description="New chat session title")


class CancelAgentRunRequest(BaseModel):
    """Request model for canceling an agent run."""

    agent_id: str = Field(..., description="The agent's public ID")
    run_id: str = Field(..., description="The agno_run_id from RunStarted event")


class CancelTeamRunRequest(BaseModel):
    """Request model for canceling a team run."""

    team_id: str = Field(..., description="The team's public ID")
    run_id: str = Field(..., description="The agno_run_id from TeamRunStarted event")


class CreateChatSessionPayload(BaseModel):
    """Request payload for creating a chat session."""

    title: Optional[str] = None
    agent_id: Optional[str] = None
    message: Optional[str] = None
    initialMessage: Optional[str] = None
    prompt_id: Optional[str] = Field(None, description="Starter prompt ID if using a starter prompt")
    attachmentIds: Optional[List[UUID]] = Field(default=None, alias="attachmentIds")

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values

        # Normalize attachment identifiers from multiple possible keys
        raw_attachments = None
        for key in ("attachmentIds", "attachment_ids", "attachments"):
            if key in values and values[key] not in (None, ""):
                raw_attachments = values[key]
                break
        if raw_attachments is not None:
            if isinstance(raw_attachments, (str, UUID)):
                raw_attachments = [raw_attachments]
            elif isinstance(raw_attachments, tuple):
                raw_attachments = list(raw_attachments)

            normalized_ids: List[UUID] = []
            for item in raw_attachments or []:
                try:
                    normalized_ids.append(UUID(str(item)))
                except (ValueError, TypeError):
                    raise ValueError(f"Invalid attachment UUID: {item}")

            values["attachmentIds"] = normalized_ids

        # Support alternative field naming conventions
        if "agent_id" not in values and "agentId" in values:
            values["agent_id"] = values["agentId"]
        if "initialMessage" not in values and "initial_message" in values:
            values["initialMessage"] = values["initial_message"]
        if "message" not in values and "initialMessage" in values and values.get("message") is None:
            # Some clients only send initialMessage
            values.setdefault("message", values["initialMessage"])
        if "prompt_id" not in values and "promptId" in values:
            values["prompt_id"] = values["promptId"]
        return values

    @field_validator("attachmentIds")
    @classmethod
    def _dedupe_attachment_ids(cls, value: Optional[List[UUID]]) -> Optional[List[UUID]]:
        if not value:
            return value
        unique: List[UUID] = []
        seen: set[UUID] = set()
        for item in value:
            if item not in seen:
                unique.append(item)
                seen.add(item)
        return unique

    @field_validator("agent_id")
    @classmethod
    def _validate_agent_id(cls, value: Optional[str]) -> Optional[str]:
        # Convert empty string to None (will default to Super Agent)
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return None
        try:
            return str(UUID(str(value)))
        except (ValueError, TypeError):
            raise ValueError("agent_id must be a valid UUID")


class SendChatMessageRequest(BaseModel):
    query: str = Field(..., description="User query to send to the chat")
    agent_id: Optional[str] = Field(None, description="Agent identifier")
    session_id: Optional[UUID] = Field(None, description="Existing session ID if resuming a chat")
    attachments: Optional[List[Dict[str, str]]] = Field(
        default=None,
        description="Attachment descriptors. Only attachment_uuid is required. Other fields (filename, content_type, blob_url) will be automatically fetched from the database.",
    )
    custom_fields: Optional[Dict[str, Any]] = Field(None, description="Custom metadata for the message")

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values

        if "query" not in values or values.get("query") is None:
            raise ValueError("'query' field is required")

        attachments = values.get("attachments")
        if attachments:
            normalized: List[Dict[str, str]] = []
            for item in attachments:
                if not isinstance(item, dict):
                    raise ValueError("Invalid attachment descriptor")
                attachment_uuid = (
                    item.get("attachment_uuid")
                    or item.get("attachmentUuid")
                    or item.get("id")
                )
                if not attachment_uuid:
                    raise ValueError("Attachment descriptor missing attachment_uuid")
                try:
                    UUID(str(attachment_uuid))
                except (ValueError, TypeError):
                    raise ValueError(f"Invalid attachment UUID: {attachment_uuid}")
                # Only store attachment_uuid - other fields will be fetched from database
                normalized.append(
                    {
                        "attachment_uuid": str(attachment_uuid),
                    }
                )
            values["attachments"] = normalized

        if "agent_id" not in values and "agentId" in values:
            values["agent_id"] = values["agentId"]

        # Convert empty string session_id to None
        if "session_id" in values and (values["session_id"] is None or (isinstance(values["session_id"], str) and values["session_id"].strip() == "")):
            values["session_id"] = None

        return values

    @field_validator("attachments")
    @classmethod
    def _validate_attachments(cls, value: Optional[List[Dict[str, str]]]) -> Optional[List[Dict[str, str]]]:
        return value

    @field_validator("agent_id")
    @classmethod
    def _validate_agent_id(cls, value: Optional[str]) -> Optional[str]:
        # Convert empty string to None (will default to Super Agent)
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return None
        try:
            return str(UUID(str(value)))
        except (ValueError, TypeError):
            raise ValueError("agent_id must be a valid UUID")


@router.post(
    "/sessions",
    summary="Create Chat Session",
    description=(
        "Create a new chat session with optional attachment references and initial message. "
        "Attachments must be uploaded first via /api/attachments/upload, which returns attachment IDs."
    ),
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Chat session title (optional)"},
                            "agent_id": {"type": "string", "description": "Agent ID (optional, defaults to Super Agent)"},
                            "message": {"type": "string", "description": "Initial message (optional)"},
                            "initialMessage": {"type": "string", "description": "Alias for message"},
                            "attachmentIds": {
                                "type": "array",
                                "items": {"type": "string", "format": "uuid"},
                                "description": "Attachment IDs returned by /api/attachments/upload"
                            },
                        },
                    }
                }
            }
        }
    }
)
async def create_chat_session(
    payload: Optional[CreateChatSessionPayload] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Create a new chat session using previously uploaded attachments if provided."""
    correlation_id = get_correlation_id()

    await _enforce_chat_rate_limit(current_user)

    payload = payload or CreateChatSessionPayload()
    initial_message = payload.message or payload.initialMessage
    attachment_ids = payload.attachmentIds or []
    prompt_id = payload.prompt_id

    _validate_query_length(initial_message, "message")
    
    # If prompt_id is provided, fetch the starter prompt
    starter_prompt = None
    if prompt_id:
        prompt_result = await db.execute(
            select(StarterPrompt).where(StarterPrompt.id == prompt_id)
        )
        starter_prompt = prompt_result.scalar_one_or_none()
        if not starter_prompt:
            _raise_chat_error(
                status_code=status.HTTP_404_NOT_FOUND,
                error_code="PROMPT_NOT_FOUND",
                error_message="Starter prompt not found",
                details={"field": "prompt_id", "message": f"No prompt found with ID: {prompt_id}"},
            )
        # If no initial message provided, use the prompt text
        if not initial_message:
            initial_message = starter_prompt.prompt

    if len(attachment_ids) > MAX_ATTACHMENTS_PER_SESSION:
        _raise_chat_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="TOO_MANY_FILES",
            error_message=f"A maximum of {MAX_ATTACHMENTS_PER_SESSION} attachments is allowed per session",
            details={"field": "attachmentIds", "message": "Too many attachments provided"},
        )

    attachments_records: List[Attachment] = []
    attachments_metadata: List[Dict[str, Any]] = []
    if attachment_ids:
        result = await db.execute(
            select(Attachment).where(
                Attachment.id.in_(attachment_ids),
                Attachment.is_active == True,
            )
        )
        attachments_records = list(result.scalars())
        found_ids = {att.id for att in attachments_records}
        missing_ids = [str(att_id) for att_id in attachment_ids if att_id not in found_ids]
        if missing_ids:
            _raise_chat_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="VALIDATION_ERROR",
                error_message="One or more attachments could not be found",
                details={
                    "field": "attachmentIds",
                    "message": "Unknown attachment identifiers",
                    "attachments": missing_ids,
                },
            )

        unauthorized_ids = [
            str(att.id)
            for att in attachments_records
            if str(att.user_id) != str(current_user.id) and not getattr(current_user, "is_admin", False)
        ]
        if unauthorized_ids:
            _raise_chat_error(
                status_code=status.HTTP_403_FORBIDDEN,
                error_code="VALIDATION_ERROR",
                error_message="You do not have access to one or more attachments",
                details={"field": "attachmentIds", "message": "Unauthorized attachments", "attachments": unauthorized_ids},
            )

        oversized_ids = [
            str(att.id)
            for att in attachments_records
            if att.file_size and att.file_size > MAX_ATTACHMENT_SIZE_BYTES
        ]
        if oversized_ids:
            _raise_chat_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="FILE_TOO_LARGE",
                error_message="One or more attachments exceed the 50MB size limit",
                details={"field": "attachmentIds", "attachments": oversized_ids},
            )

        for att in attachments_records:
            # Generate SAS URL with 30-minute expiration for frontend
            sas_url = _generate_attachment_sas_url(att.blob_name)
            attachments_metadata.append(
                {
                    "attachment_id": str(att.id),
                    "file_name": att.file_name,
                    "file_size": att.file_size,
                    "content_type": att.content_type,
                    "blob_url": sas_url,  # Use SAS URL with 30-minute expiration
                    "blob_name": att.blob_name,
                    "uploaded_at": att.created_at.isoformat() if att.created_at else None,
                }
            )

    async def get_super_agent() -> Agent:
        """Get Super Agent record, return the Agent object."""
        result = await db.execute(select(Agent).where(Agent.name == "Super Agent"))
        super_agent = result.scalar_one_or_none()
        if not super_agent:
            # Create default Super Agent if it doesn't exist
            super_agent = Agent(
                name="Super Agent",
                agent_id="super-agent",
                description="Default AI assistant",
                is_enabled=True
            )
            db.add(super_agent)
            await db.flush()
        return super_agent

    agent_identifier = payload.agent_id.strip() if payload.agent_id else None
    agent_record: Optional[Agent] = None
    
    if not agent_identifier:
        agent_record = await get_super_agent()
    else:
        try:
            agent_uuid = UUID(agent_identifier)
        except (ValueError, TypeError):
            _raise_chat_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="VALIDATION_ERROR",
                error_message="Agent identifier must be a valid UUID",
                details={"field": "agent_id", "message": "Provide a valid UUID"},
            )

        result = await db.execute(select(Agent).where(Agent.public_id == agent_uuid))
        agent_record = result.scalar_one_or_none()
        if not agent_record:
            _raise_chat_error(
                status_code=status.HTTP_404_NOT_FOUND,
                error_code="AGENT_NOT_FOUND",
                error_message="Agent identifier is invalid",
                details={"field": "agent_id", "message": "No matching agent"},
            )
    
    agent_id = agent_record.id  # BigInteger primary key for Session
    agent_id_str = str(agent_record.public_id)

    # Update last_used timestamp for the agent
    await _update_agent_last_used(agent_id, db)

    if initial_message and (not payload.title or payload.title.lower() in ("string", "null", "")):
        title = initial_message.strip()[:50]
        if len(initial_message.strip()) > 50:
            title += "..."
    else:
        title = payload.title

    final_title = title or "New Chat"

    session_metadata: Dict[str, Any] = {
        "agent_id": agent_id_str,
        "agentId": agent_id_str,
    }
    if attachments_metadata:
        session_metadata["attachments"] = attachments_metadata
        session_metadata["attachment_count"] = len(attachments_metadata)
        session_metadata["attachment_ids"] = [item["attachment_id"] for item in attachments_metadata]

    try:
        session = Session(
            user_id=current_user.id,
            agent_id=agent_id,
            session_name=final_title,
            session_id=str(uuid4()),
            session_metadata=session_metadata,
            session_type="chat",
            status="active",
        )

        db.add(session)
        await db.flush()

        for attachment in attachments_records:
            attachment.entity_type = "session"
            attachment.entity_id = str(session.id)
            attachment.updated_at = datetime.utcnow()
            db.add(attachment)

        await db.commit()
        await db.refresh(session)

        # Log starting prompt chosen if prompt_id was provided
        if prompt_id and starter_prompt:
            agent_type = await determine_agent_type(agent_record, db)
            log_starting_prompt_chosen(
                chat_id=str(session.id),
                session_id=session.session_id,
                user_id=str(current_user.id),
                username=current_user.username or current_user.email,
                prompt_id=prompt_id,
                prompt_text=starter_prompt.prompt,
                correlation_id=correlation_id,
                email=current_user.email,
                role="ADMIN" if current_user.is_admin else "NORMAL",
                department=current_user.azure_department,
                user_entra_id=current_user.azure_ad_id,
                agent_id=agent_id_str,
                agent_name=agent_record.name if agent_record else None,
                agent_type=agent_type,
            )

        # Extract custom fields from initial message if it's a message request
        selected_agent_type = None
        custom_query_about_user = None
        custom_query_topics_of_interest = None
        custom_query_preferred_formatting = None
        is_internet_search_used = None
        is_sent_directly_to_openai = None
        
        # If initial_message exists and has custom fields (from payload)
        if payload and hasattr(payload, 'custom_fields') and payload.custom_fields:
            selected_agent_type = (
                payload.custom_fields.get("selectedAgentType") or
                payload.custom_fields.get("selected_agent_type") or
                payload.custom_fields.get("agentType") or
                payload.custom_fields.get("agent_type")
            )
            custom_query_about_user = payload.custom_fields.get("customQueryAboutUser")
            custom_query_topics_of_interest = payload.custom_fields.get("customQueryTopicsOfInterest")
            custom_query_preferred_formatting = payload.custom_fields.get("customQueryPreferredFormatting")
            is_internet_search_used = payload.custom_fields.get("isInternetSearchUsed")
            is_sent_directly_to_openai = payload.custom_fields.get("isSentDirectlyToOpenAi")

        agent_type = await determine_agent_type(agent_record, db)
        log_chat_session_created(
            chat_id=str(session.id),
            session_id=session.session_id,
            user_id=str(current_user.id),
            username=current_user.username or current_user.email,
            title=session.session_name,
            agent_id=agent_id_str,
            attachments=attachments_metadata,
            initial_message=initial_message,
            correlation_id=correlation_id,
            # User info
            email=current_user.email,
            role="ADMIN" if current_user.is_admin else "NORMAL",
            department=current_user.azure_department,
            user_entra_id=current_user.azure_ad_id,
            # Agent info
            agent_name=agent_record.name if agent_record else None,
            agent_type=agent_type,
            agent_public_id=agent_id_str,
            # Event payload fields
            selected_agent_type=selected_agent_type,
            custom_query_about_user=custom_query_about_user,
            is_internet_search_used=is_internet_search_used,
            is_sent_directly_to_openai=is_sent_directly_to_openai,
            custom_query_topics_of_interest=custom_query_topics_of_interest,
            custom_query_preferred_formatting=custom_query_preferred_formatting,
        )

        message_saved = False
        if initial_message:
            message = Message(
                session_id=session.id,
                user_id=current_user.id,
                agent_id=agent_id,
                content_type="text",
                content=initial_message.strip(),
                role="user",
            )
            db.add(message)
            await db.commit()
            await db.refresh(session)
            message_saved = True

            # Extract custom query fields from payload if available
            custom_query_about_user = None
            custom_query_topics_of_interest = None
            custom_query_preferred_formatting = None
            if payload and hasattr(payload, 'custom_fields') and payload.custom_fields:
                custom_query_about_user = payload.custom_fields.get("customQueryAboutUser")
                custom_query_topics_of_interest = payload.custom_fields.get("customQueryTopicsOfInterest")
                custom_query_preferred_formatting = payload.custom_fields.get("customQueryPreferredFormatting")

            agent_type = await determine_agent_type(agent_record, db)
            log_chat_message(
                chat_id=str(session.id),
                message_id=str(message.id),
                message_type="user",
                role="user",
                content=initial_message.strip(),
                user_id=str(current_user.id),
                username=current_user.username or current_user.email,
                correlation_id=correlation_id,
                # User info
                email=current_user.email,
                role_user="ADMIN" if current_user.is_admin else "NORMAL",
                department=current_user.azure_department,
                user_entra_id=current_user.azure_ad_id,
                # Message info
                conversation_id=str(session.id),
                selected_agent_type=selected_agent_type,
                custom_query_about_user=custom_query_about_user,
                is_internet_search_used=None,  # Will be set when message is processed
                is_sent_directly_to_openai=None,  # Will be set when message is processed
                custom_query_topics_of_interest=custom_query_topics_of_interest,
                custom_query_preferred_formatting=custom_query_preferred_formatting,
                user_input=initial_message.strip(),
                # Agent info
                agent_name=agent_record.name if agent_record else None,
                agent_type=agent_type,
                agent_public_id=agent_id_str,
                # Attachments
                attachments=attachments_metadata if attachments_metadata else None,
            )

        return {
            "success": True,
            "id": str(session.id),
            "session_id": session.session_id,
            "title": session.session_name,
            "agent_id": agent_id_str,
            "attachments": attachments_metadata,
            "attachment_count": len(attachments_metadata),
            "initial_message_saved": message_saved,
            "created_at": session.created_at.isoformat(),
            "correlation_id": correlation_id,
        }
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - fallback safety
        await db.rollback()
        _raise_chat_error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="INTERNAL_ERROR",
            error_message="Failed to create chat session",
            details={"field": "server", "message": str(exc)},
        )


@router.get("/sessions/search")
async def search_chat_sessions(
    q: str = Query(..., min_length=1, description="Search query"),
    agent_id: Optional[List[str]] = Query(
        default=None,
        alias="agent_id",
        description="Filter by agent ID(s). Can provide multiple agent IDs as comma-separated values or multiple query parameters. Accepts UUID (public_id) or BigInteger (id).",
    ),
    date_from: Optional[datetime] = Query(None, description="Filter chats created on or after this date (ISO format)"),
    date_to: Optional[datetime] = Query(None, description="Filter chats created on or before this date (ISO format)"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Search chat sessions by title and message content."""
    correlation_id = get_correlation_id()
    await _enforce_chat_rate_limit(current_user)
    trimmed_query = q.strip()
    if not trimmed_query:
        _raise_chat_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="VALIDATION_ERROR",
            error_message="Search query cannot be empty",
            details={"field": "q", "message": "Provide a non-empty search query"},
        )

    _validate_query_length(trimmed_query, "q")

    search_pattern = f"%{trimmed_query}%"

    filters = [Session.user_id == current_user.id]

    # Date filters (inclusive)
    if date_from:
        # Frontend sends UTC datetime already converted from local time
        # Simple UTC comparison: WHERE created_at >= date_from
        if date_from.tzinfo is not None:
            date_from = date_from.astimezone(timezone.utc).replace(tzinfo=None)
        filters.append(Session.created_at >= date_from)
    if date_to:
        # Frontend sends UTC datetime already converted from local time
        # Simple UTC comparison: WHERE created_at <= date_to
        if date_to.tzinfo is not None:
            date_to = date_to.astimezone(timezone.utc).replace(tzinfo=None)
        filters.append(Session.created_at <= date_to)

    # Agent filter - support multiple agent IDs
    if agent_id:
        agent_ids = agent_id if isinstance(agent_id, list) else [agent_id]
        
        # Separate UUIDs and BigInteger IDs
        uuid_agent_ids: List[UUID] = []
        bigint_agent_ids: List[int] = []
        agent_strs: List[str] = []
        
        for agent_id_val in agent_ids:
            # Try as UUID first
            try:
                uuid_agent_ids.append(UUID(str(agent_id_val)))
            except (ValueError, TypeError):
                # Try as BigInteger
                try:
                    bigint_agent_ids.append(int(agent_id_val))
                except (ValueError, TypeError):
                    # Store as string for metadata lookup
                    agent_strs.append(str(agent_id_val))
        
        agent_conditions = []
        
        # Filter by Agent.public_id (UUID) - need to resolve to Session.agent_id first
        if uuid_agent_ids:
            # Query Agent table to get BigInteger IDs from UUIDs
            agent_result = await db.execute(
                select(Agent).where(Agent.public_id.in_(uuid_agent_ids))
            )
            resolved_bigint_ids = [agent.id for agent in agent_result.scalars().all()]
            if resolved_bigint_ids:
                # Add resolved BigInteger IDs to the list
                bigint_agent_ids.extend(resolved_bigint_ids)
        
        # Filter by Session.agent_id (BigInteger) if we have BigInteger IDs
        if bigint_agent_ids:
            agent_conditions.append(Session.agent_id.in_(bigint_agent_ids))
        
        # Filter by metadata (agent_id or agentId fields) for string IDs
        if agent_strs:
            metadata_conditions = []
            for agent_str in agent_strs:
                metadata_conditions.append(
                    or_(
                        Session.session_metadata["agent_id"].as_string() == agent_str,
                        Session.session_metadata["agentId"].as_string() == agent_str,
                    )
                )
            if metadata_conditions:
                agent_conditions.append(or_(*metadata_conditions))
        
        # Combine all agent conditions with OR
        if agent_conditions:
            filters.append(or_(*agent_conditions))

    title_match_expr = Session.session_name.ilike(search_pattern)
    message_match_exists = exists().where(
        and_(
            Message.session_id == Session.id,
            Message.content.ilike(search_pattern)
        )
    )
    filters.append(or_(title_match_expr, message_match_exists))

    # Aggregate message stats
    message_stats_subq = (
        select(
            Message.session_id.label("session_id"),
            func.count(Message.id).label("message_count"),
            func.max(Message.created_at).label("last_message_at")
        )
        .group_by(Message.session_id)
        .subquery()
    )

    title_score = case((title_match_expr, 1.0), else_=0.0)
    message_score = case((message_match_exists, 1.0), else_=0.0)
    relevance_score_expr = (title_score * 0.6 + message_score * 0.4).label("relevance_score")

    stmt = (
        select(
            Session,
            func.coalesce(message_stats_subq.c.message_count, 0).label("message_count"),
            func.coalesce(message_stats_subq.c.last_message_at, Session.updated_at).label("last_message_at"),
            relevance_score_expr,
        )
        .outerjoin(message_stats_subq, message_stats_subq.c.session_id == Session.id)
        .where(*filters)
        .order_by(desc(relevance_score_expr), desc(func.coalesce(Session.updated_at, Session.created_at)))
        .offset(offset)
        .limit(limit)
    )

    result = await db.execute(stmt)
    rows = result.all()

    session_ids = [row[0].id for row in rows]

    # Count total results matching the filters
    count_stmt = select(func.count()).select_from(Session).where(*filters)
    total_count = (await db.execute(count_stmt)).scalar() or 0

    snippets: Dict[UUID, str] = {}
    first_messages: Dict[UUID, str] = {}
    if session_ids:
        message_stmt = (
            select(Message.session_id, Message.content, Message.created_at)
            .where(Message.session_id.in_(session_ids))
            .order_by(Message.session_id, Message.created_at)
        )
        message_rows = await db.execute(message_stmt)
        q_lower = trimmed_query.lower()
        for chat_id_value, content, _created_at in message_rows:
            if content is None:
                continue
            if chat_id_value not in first_messages:
                first_messages[chat_id_value] = content
            if chat_id_value not in snippets and q_lower in content.lower():
                snippets[chat_id_value] = content

    # Prepare agent lookups
    agent_candidates: Dict[str, Dict[str, Optional[str]]] = {}

    def normalize_agents(metadata: Dict[str, Any]) -> List[Dict[str, Optional[str]]]:
        """Extract agent identifiers from chat metadata."""
        agents: List[Dict[str, Optional[str]]] = []
        primary_agent_id = metadata.get("agent_id") or metadata.get("agentId")
        primary_agent_name = metadata.get("agent_name") or metadata.get("agentName")
        if primary_agent_id:
            agents.append({"agent_id": str(primary_agent_id), "agent_name": primary_agent_name})

        for key in ("agents_involved", "agentsInvolved", "agents_used", "agentsUsed", "agent_history", "agentHistory"):
            existing = metadata.get(key)
            if isinstance(existing, list):
                for item in existing:
                    if isinstance(item, dict):
                        ag_id = (
                            item.get("agent_id")
                            or item.get("agentId")
                            or item.get("id")
                            or item.get("agent")
                        )
                        ag_name = (
                            item.get("agent_name")
                            or item.get("agentName")
                            or item.get("name")
                        )
                        if ag_id:
                            agents.append({"agent_id": str(ag_id), "agent_name": ag_name})
                    elif isinstance(item, str):
                        agents.append({"agent_id": item, "agent_name": None})
        return agents

    for row in rows:
        metadata = row[0].session_metadata or {}
        for agent_entry in normalize_agents(metadata):
            agent_id_val = agent_entry.get("agent_id")
            agent_name_val = agent_entry.get("agent_name")
            if not agent_id_val:
                continue
            if agent_id_val not in agent_candidates or not agent_candidates[agent_id_val].get("agent_name"):
                agent_candidates[agent_id_val] = {
                    "agent_id": agent_id_val,
                    "agent_name": agent_name_val,
                }

    # Fetch missing agent names from Agent table
    missing_agent_ids = [aid for aid, data in agent_candidates.items() if not data.get("agent_name")]
    uuid_ids: List[UUID] = []
    legacy_ids: List[str] = []
    for agent_id_val in missing_agent_ids:
        try:
            uuid_ids.append(UUID(agent_id_val))
        except (ValueError, TypeError):
            legacy_ids.append(agent_id_val)

    agent_lookup: Dict[str, str] = {}
    agent_conditions = []
    if legacy_ids:
        agent_conditions.append(Agent.agent_id.in_(legacy_ids))
    if uuid_ids:
        agent_conditions.append(Agent.public_id.in_(uuid_ids))

    if agent_conditions:
        agent_rows = await db.execute(select(Agent).where(or_(*agent_conditions)))
        for agent_obj in agent_rows.scalars():
            if agent_obj.agent_id:
                agent_lookup[agent_obj.agent_id] = agent_obj.name
            if agent_obj.public_id:
                agent_lookup[str(agent_obj.public_id)] = agent_obj.name

    # Build results
    results_list = []
    q_lower = trimmed_query.lower()

    def build_snippet_text(raw_text: Optional[str]) -> Optional[str]:
        if not raw_text:
            return None
        cleaned = " ".join(raw_text.split())
        if len(cleaned) > 200:
            return cleaned[:197] + "..."
        return cleaned

    for session_obj, message_count, _last_message_at, relevance_score in rows:
        metadata = session_obj.session_metadata or {}

        agent_entries = normalize_agents(metadata)
        # Deduplicate while preserving order
        seen_agent_ids: set[str] = set()
        agents_used: List[Dict[str, str]] = []
        for agent_entry in agent_entries:
            agent_id_val = agent_entry.get("agent_id")
            if not agent_id_val or agent_id_val in seen_agent_ids:
                continue
            seen_agent_ids.add(agent_id_val)
            agent_name_val = agent_entry.get("agent_name") or agent_lookup.get(agent_id_val)
            agents_used.append(
                {
                    "agent_id": agent_id_val,
                    "agent_name": agent_name_val or "Unknown Agent",
                }
            )
        if not agents_used and agent_candidates:
            # Fallback for primary agent metadata
            primary_agent_id = metadata.get("agent_id") or metadata.get("agentId")
            if primary_agent_id:
                agents_used.append(
                    {
                        "agent_id": str(primary_agent_id),
                        "agent_name": agent_lookup.get(str(primary_agent_id), "Unknown Agent"),
                    }
                )

        chat_id_value = session_obj.id
        snippet_text = (
            snippets.get(chat_id_value)
            or first_messages.get(chat_id_value)
            or session_obj.session_name
        )
        snippet_formatted = build_snippet_text(snippet_text)

        title_value = session_obj.session_name or ""
        title_match = 1.0 if title_value and q_lower in title_value.lower() else 0.0
        snippet_match = 1.0 if snippet_text and q_lower in snippet_text.lower() else 0.0
        combined_relevance = max(float(relevance_score or 0.0), 0.6 * title_match + 0.4 * snippet_match)

        is_favorite = metadata.get("is_favorite")
        if is_favorite is None:
            is_favorite = metadata.get("isFavorite")
        if is_favorite is None:
            is_favorite = metadata.get("is_favourite", False)

        results_list.append(
            {
                "session_id": session_obj.session_id,
                "title": session_obj.session_name or "Untitled Chat",
                "snippet": snippet_formatted,
                "created_at": session_obj.created_at.isoformat(),
                "updated_at": (session_obj.updated_at or session_obj.created_at).isoformat(),
                "message_count": int(message_count or 0),
                "is_favorite": bool(is_favorite),
                "agents_used": agents_used,
                "relevance_score": round(min(combined_relevance, 1.0), 4),
            }
        )

    has_more = offset + len(results_list) < total_count
    
    return {
        "success": True,
        "query": trimmed_query,
        "results": results_list,
        "total_count": total_count,
        "has_more": has_more,
        "correlation_id": correlation_id,
    }


@router.patch("/sessions/{session_id}")
async def update_chat_session_title(
    session_id: UUID,
    request: UpdateChatSessionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Update chat session title."""
    correlation_id = get_correlation_id()
    new_title = request.title.strip()

    await _enforce_chat_rate_limit(current_user)

    if not new_title:
        _raise_chat_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="VALIDATION_ERROR",
            error_message="Title cannot be empty",
            details={"field": "title", "message": "Provide a non-empty title"},
        )

    result = await db.execute(
        select(Session).where(
            Session.id == UUID(str(session_id)),
            Session.user_id == current_user.id
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        _raise_chat_error(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="SESSION_NOT_FOUND",
            error_message="Chat session not found",
            details={"field": "session_id", "message": "Specified session does not exist"},
        )

    _ensure_session_active(session)

    # Get old title before update
    old_title = session.session_name or "Untitled"

    # Mark that user has manually renamed this session - prevent agno_sessions from overwriting
    metadata = session.session_metadata or {}
    metadata["title_manually_renamed"] = True
    metadata["title_renamed_at"] = datetime.utcnow().isoformat()

    # Preserve updated_at by using direct SQL update
    current_updated_at = session.updated_at
    await db.execute(
        update(Session)
        .where(Session.id == session.id)
        .values(
            session_name=new_title,
            session_metadata=metadata,  # Update metadata to mark as manually renamed
            updated_at=current_updated_at  # Preserve original timestamp
        )
    )
    await db.flush()  # Ensure the update is written to the database
    await db.commit()  # Commit the transaction
    # Expire the session object to force reload from database
    db.expire(session)
    await db.refresh(session)

    # Log conversation rename with all required fields
    log_conversation_renamed(
        chat_id=str(session.id),
        session_id=session.session_id,
        user_id=str(current_user.id),
        username=current_user.username or current_user.email,
        old_title=old_title or "Untitled",
        new_title=new_title,
        correlation_id=correlation_id,
        email=current_user.email,
        role="ADMIN" if current_user.is_admin else "NORMAL",
        department=current_user.azure_department,
        user_entra_id=current_user.azure_ad_id,
    )

    return {
        "success": True,
        "session_id": session.session_id,
        "title": session.session_name,
    }


@router.get("/sessions")
async def get_chat_sessions(
    group: GroupLiteral = Query("all", description="Group identifier for sessions"),
    limit: int = Query(10, ge=1, le=10),
    offset: int = Query(0, ge=0),
    agent_id: Optional[List[UUID]] = Query(
        default=None,
        description="Filter by agent ID(s). Can provide multiple agent IDs as comma-separated values or multiple query parameters.",
    ),
    search: Optional[str] = Query(None, description="Search by chat title"),
    date_from: Optional[datetime] = Query(None, description="Start date filter (ISO format)"),
    date_to: Optional[datetime] = Query(None, description="End date filter (ISO format)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve chat sessions using grouping, filters, and pagination."""
    correlation_id = get_correlation_id()
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seven_days_ago = now - timedelta(days=7)
    month_start = today_start.replace(day=1)
    one_month_ago = month_start - timedelta(days=1)  # End of previous month

    activity_field = func.coalesce(Session.last_message_interaction_at, Session.updated_at, Session.created_at)
    filters = [Session.user_id == current_user.id]

    await _enforce_chat_rate_limit(current_user)

    if search:
        search_value = search.strip()
        if search_value:
            _validate_query_length(search_value, "search")
            filters.append(Session.session_name.ilike(f"%{search_value}%"))

    if agent_id:
        # Handle multiple agent IDs
        agent_ids = agent_id if isinstance(agent_id, list) else [agent_id]
        agent_strs = [str(aid) for aid in agent_ids]
        
        # Build OR conditions for each agent ID (checking both agent_id and agentId fields)
        agent_conditions = []
        for agent_str in agent_strs:
            agent_conditions.append(
                or_(
                    Session.session_metadata["agent_id"].as_string() == agent_str,
                    Session.session_metadata["agentId"].as_string() == agent_str,
                )
            )
        
        # Combine all agent conditions with OR (session matches if it has any of the agent IDs)
        if agent_conditions:
            filters.append(or_(*agent_conditions))

    # Store date filters separately - they will be applied in _build_grouped_session_summary
    # using the correct subquery (sort_activity) to match grouping logic
    date_from_naive_session = None
    date_to_naive_session = None
    if date_from:
        # Frontend sends UTC datetime already converted from local time
        if date_from.tzinfo is not None:
            date_from_naive_session = date_from.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            date_from_naive_session = date_from
    if date_to:
        # Frontend sends UTC datetime already converted from local time
        if date_to.tzinfo is not None:
            date_to_naive_session = date_to.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            date_to_naive_session = date_to

    if group == "all":
        # Calculate new date ranges for grouped sessions
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        previous_7_days_start = (today_start - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        previous_7_days_end = (today_start - timedelta(microseconds=1))
        # Previous 30 Days: From start of current year to 7 days ago
        current_year = now.year
        previous_30_days_start = datetime(current_year, 1, 1, 0, 0, 0, 0)
        previous_30_days_end = previous_7_days_start
        # Previous Year: Actual previous calendar year
        previous_year_start = datetime(current_year - 1, 1, 1, 0, 0, 0, 0)
        previous_year_end = datetime(current_year, 1, 1, 0, 0, 0, 0)
        older_end = previous_year_start
        
        # Date filters will be applied in _build_grouped_session_summary using sort_activity
        groups_response, grouped_total, grouped_has_more = await _build_grouped_session_summary(
            db=db,
            base_filters=filters,
            activity_field=activity_field,
            limit=limit,
            offset=offset,
            today_start=today_start,
            today_end=today_end,
            previous_7_days_start=previous_7_days_start,
            previous_7_days_end=previous_7_days_end,
            previous_30_days_start=previous_30_days_start,
            previous_30_days_end=previous_30_days_end,
            previous_year_start=previous_year_start,
            previous_year_end=previous_year_end,
            older_end=older_end,
            date_from=date_from_naive_session,
            date_to=date_to_naive_session,
            current_user=current_user,
        )
        global_total_stmt = select(func.count()).select_from(Session).where(*filters)
        overall_total = (await db.execute(global_total_stmt)).scalar() or 0
        return {
            "success": True,
            "group": group,
            "sessions": groups_response,
            "has_more": grouped_has_more,
            "total_count": overall_total or grouped_total,
            "correlation_id": correlation_id,
        }

    if group == "favourites":
        filters.append(_favorite_flag_clause())
    elif group == "today":
        filters.append(activity_field >= today_start)
    elif group == "this_week":
        # Chats within the last 7 days (excluding today)
        filters.append(
            and_(
                activity_field >= seven_days_ago,
                activity_field < today_start,
            )
        )
    elif group == "last_week":
        # Keep last_week for backward compatibility, but it's not used in the new logic
        # This would be chats from 7-14 days ago
        last_week_start = seven_days_ago - timedelta(days=7)
        filters.append(
            and_(
                activity_field >= last_week_start,
                activity_field < seven_days_ago,
            )
        )
    elif group == "this_month":
        # Chats older than 7 days but within the current month
        filters.append(
            and_(
                activity_field >= month_start,
                activity_field < seven_days_ago,
            )
        )
    elif group == "this_year":
        # For backward compatibility, this_year maps to previous_year logic
        # Use actual previous calendar year
        current_year = now.year
        year_start = datetime(current_year - 1, 1, 1, 0, 0, 0, 0)
        year_end = datetime(current_year, 1, 1, 0, 0, 0, 0)
        filters.append(
            and_(
                activity_field >= year_start,
                activity_field < year_end,
            )
        )
    elif group == "older":
        # Chats from before the previous calendar year
        current_year = now.year
        older_end = datetime(current_year - 1, 1, 1, 0, 0, 0, 0)  # Start of previous year
        filters.append(activity_field < older_end)

    message_stats_subq = (
        select(
            Message.session_id.label("session_id"),
            func.count(Message.id).label("message_count"),
            func.max(Message.created_at).label("last_message_at"),
        )
        .where(Message.content_type != "system")
        .group_by(Message.session_id)
        .subquery()
    )

    last_message_subq = (
        select(
            Message.session_id.label("session_id"),
            Message.content.label("content"),
            Message.created_at.label("created_at"),
            Message.content_type.label("message_type"),
            func.row_number()
            .over(
                partition_by=Message.session_id,
                order_by=Message.created_at.desc(),
            )
            .label("row_number"),
        )
        .where(Message.content_type != "system")
        .subquery()
    )

    activity_coalesce = func.coalesce(
        message_stats_subq.c.last_message_at, Session.updated_at, Session.created_at
    )

    stmt = (
        select(
            Session,
            func.coalesce(message_stats_subq.c.message_count, 0).label("message_count"),
            activity_coalesce.label("last_activity_at"),
            last_message_subq.c.content.label("last_message_content"),
        )
        .outerjoin(message_stats_subq, message_stats_subq.c.session_id == Session.id)
        .outerjoin(
            last_message_subq,
            and_(
                last_message_subq.c.session_id == Session.id,
                last_message_subq.c.row_number == 1,
            ),
        )
        .where(*filters)
        .order_by(desc(activity_coalesce))
        .offset(offset)
        .limit(limit)
    )

    result = await db.execute(stmt)
    rows = result.all()

    count_stmt = select(func.count()).select_from(Session).where(*filters)
    total_count = (await db.execute(count_stmt)).scalar() or 0

    agents_lookup: Dict[str, str] = {}
    agent_candidates: Dict[str, Dict[str, Optional[str]]] = {}

    def normalize_agents(metadata: Dict[str, Any]) -> List[Dict[str, Optional[str]]]:
        agents: List[Dict[str, Optional[str]]] = []
        primary_agent_id = metadata.get("agent_id") or metadata.get("agentId")
        primary_agent_name = metadata.get("agent_name") or metadata.get("agentName")
        primary_agent_type = (
            metadata.get("agent_type")
            or metadata.get("agentType")
            or metadata.get("agent_role")
        )
        if primary_agent_id:
            agents.append(
                {
                    "agent_id": str(primary_agent_id),
                    "agent_name": primary_agent_name,
                    "agent_type": primary_agent_type,
                }
            )

        for key in ("agents_involved", "agentsInvolved", "agents_used", "agentsUsed", "agent_history", "agentHistory"):
            existing = metadata.get(key)
            if isinstance(existing, list):
                for item in existing:
                    if isinstance(item, dict):
                        ag_id = (
                            item.get("agent_id")
                            or item.get("agentId")
                            or item.get("id")
                            or item.get("agent")
                        )
                        ag_name = (
                            item.get("agent_name")
                            or item.get("agentName")
                            or item.get("name")
                        )
                        ag_type = (
                            item.get("agent_type")
                            or item.get("agentType")
                            or item.get("type")
                            or item.get("role")
                        )
                        if ag_id:
                            agents.append(
                                {
                                    "agent_id": str(ag_id),
                                    "agent_name": ag_name,
                                    "agent_type": ag_type,
                                }
                            )
                    elif isinstance(item, str):
                        agents.append(
                            {
                                "agent_id": item,
                                "agent_name": None,
                                "agent_type": None,
                            }
                        )
        return agents

    for row in rows:
        metadata = row[0].session_metadata or {}
        for agent_entry in normalize_agents(metadata):
            agent_id_val = agent_entry.get("agent_id")
            agent_name_val = agent_entry.get("agent_name")
            if not agent_id_val:
                continue
            if (
                agent_id_val not in agent_candidates
                or not agent_candidates[agent_id_val].get("agent_name")
            ):
                agent_candidates[agent_id_val] = {
                    "agent_id": agent_id_val,
                    "agent_name": agent_name_val,
                }

    missing_agent_ids = [aid for aid, data in agent_candidates.items() if not data.get("agent_name")]
    if missing_agent_ids:
        uuid_ids: List[UUID] = []
        legacy_ids: List[str] = []
        for candidate in missing_agent_ids:
            try:
                uuid_ids.append(UUID(candidate))
            except (ValueError, TypeError):
                legacy_ids.append(candidate)

        agent_conditions = []
        if legacy_ids:
            agent_conditions.append(Agent.agent_id.in_(legacy_ids))
        if uuid_ids:
            agent_conditions.append(Agent.public_id.in_(uuid_ids))

        if agent_conditions:
            agent_rows = await db.execute(select(Agent).where(or_(*agent_conditions)))
            for agent_obj in agent_rows.scalars():
                if agent_obj.agent_id:
                    agents_lookup[agent_obj.agent_id] = agent_obj.name
                if agent_obj.public_id:
                    agents_lookup[str(agent_obj.public_id)] = agent_obj.name

    def build_preview(content: Optional[str]) -> Optional[str]:
        if not content:
            return None
        reduced = " ".join(content.split())
        return reduced[:197] + "..." if len(reduced) > 200 else reduced

    session_records: List[Tuple[Dict[str, Any], bool, Optional[datetime]]] = []
    sessions: List[Dict[str, Any]] = []
    for session_obj, message_count, last_activity_at, last_message_content in rows:
        metadata = session_obj.session_metadata or {}
        is_favorite = metadata.get("is_favorite")
        if is_favorite is None:
            is_favorite = metadata.get("isFavorite")
        if is_favorite is None:
            is_favorite = metadata.get("is_favourite", False)

        agent_entries = normalize_agents(metadata)
        seen_agent_ids: set[str] = set()
        agents_used: List[Dict[str, Optional[str]]] = []
        for agent_entry in agent_entries:
            agent_id_val = agent_entry.get("agent_id")
            if not agent_id_val or agent_id_val in seen_agent_ids:
                continue
            seen_agent_ids.add(agent_id_val)
            agent_name_val = agent_entry.get("agent_name") or agents_lookup.get(agent_id_val)
            agent_type_val = agent_entry.get("agent_type") or "unknown"
            agents_used.append(
                {
                    "agent_id": agent_id_val,
                    "agent_name": agent_name_val or "Unknown Agent",
                    "agent_type": agent_type_val,
                }
            )

        preview_content = build_preview(last_message_content)
        last_activity_dt = last_activity_at or session_obj.updated_at or session_obj.created_at

        session_payload = {
            "session_id": str(session_obj.public_id),
            "title": session_obj.session_name or "Untitled Chat",
            "created_at": session_obj.created_at.isoformat(),
            "updated_at": (
                last_activity_dt.isoformat()
                if last_activity_dt
                else session_obj.created_at.isoformat()
            ),
            "message_count": int(message_count or 0),
            "is_favorite": bool(is_favorite),
            "last_message_preview": preview_content,
            "agents_used": agents_used,
        }
        session_records.append((session_payload, bool(is_favorite), last_activity_dt))

        if group != "all":
            sessions.append(session_payload)

    if group != "all":
        has_more = offset + len(sessions) < total_count
        return {
            "success": True,
            "group": group,
            "sessions": sessions,
            "has_more": has_more,
            "total_count": total_count,
            "correlation_id": correlation_id,
        }

    grouped_sessions: Dict[str, List[Dict[str, Any]]] = {
        "favourites": [],
        "today": [],
        "this_week": [],
        "last_week": [],
        "this_month": [],
        "older": [],
    }

    for session_payload, is_favorite_flag, last_activity_dt in session_records:
        if is_favorite_flag:
            grouped_sessions["favourites"].append(session_payload)

        if last_activity_dt is None:
            grouped_sessions["older"].append(session_payload)
            continue

        if last_activity_dt >= today_start:
            grouped_sessions["today"].append(session_payload)
        elif last_activity_dt >= seven_days_ago:
            # Within the last 7 days (excluding today)
            grouped_sessions["this_week"].append(session_payload)
        elif last_activity_dt >= month_start:
            # Older than 7 days but within the current month
            grouped_sessions["this_month"].append(session_payload)
        else:
            # Beyond 1 month (before the start of current month)
            grouped_sessions["older"].append(session_payload)

    return {
        "success": True,
        "group": group,
        "sessions": grouped_sessions,
        "has_more": offset + len(session_records) < total_count,
        "total_count": total_count,
        "correlation_id": correlation_id,
    }


@router.get("/sessions/groups")
async def get_chat_sessions_group(
    limit: int = Query(10, ge=1, le=100, description="Max sessions per group"),
    offset: int = Query(0, ge=0, description="Offset applied per group"),
    group_id: Optional[str] = Query(None, description="Specific group ID to paginate (e.g., 'this-week', 'today'). If provided, only this group will be paginated, others return first page."),
    page: Optional[int] = Query(None, ge=1, description="Page number (alternative to offset). If provided, offset is calculated as (page - 1) * limit"),
    agent_id: Optional[List[Union[UUID, str]]] = Query(
        default=None,
        description="Filter by agent ID(s). Can provide multiple agent IDs as comma-separated values or multiple query parameters. Accepts UUID (public_id) or string IDs. Matches sessions where the agent appears as the primary agent OR in agents_involved (delegated agents).",
    ),
    agent_name: Optional[List[str]] = Query(
        default=None,
        description="Filter by agent name(s). Can provide multiple agent names as comma-separated values or multiple query parameters. Matches agents in agents_involved array.",
    ),
    search: Optional[str] = Query(None, description="Search by chat title"),
    date_from: Optional[datetime] = Query(None, description="Start date filter (ISO format)"),
    date_to: Optional[datetime] = Query(None, description="End date filter (ISO format)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Return grouped chat sessions with counts and paginated entries."""
    correlation_id = get_correlation_id()
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    # Previous 7 Days: 7 days ago to yesterday (excluding today)
    previous_7_days_start = (today_start - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    previous_7_days_end = (today_start - timedelta(microseconds=1))  # End of yesterday

    # Previous 30 Days: From start of current year to 7 days ago (excluding Today + Last 7 Days)
    # This covers the entire current year except today and last 7 days
    current_year = now.year
    previous_30_days_start = datetime(current_year, 1, 1, 0, 0, 0, 0)  # Start of current year
    previous_30_days_end = previous_7_days_start  # Start of previous 7 days (exclusive)

    # Previous Year: Actual previous calendar year (e.g., Jan 1, 2024 to Dec 31, 2024 if current year is 2025)
    current_year = now.year
    previous_year_start = datetime(current_year - 1, 1, 1, 0, 0, 0, 0)
    previous_year_end = datetime(current_year, 1, 1, 0, 0, 0, 0)  # Start of current year (exclusive)

    # Older: Before the previous calendar year
    older_end = previous_year_start  # Start of previous year (exclusive)

    activity_field = func.coalesce(Session.last_message_interaction_at, Session.updated_at, Session.created_at)
    base_filters: List[Any] = [Session.user_id == current_user.id]

    await _enforce_chat_rate_limit(current_user)

    if search:
        search_value = search.strip()
        if search_value:
            _validate_query_length(search_value, "search")
            base_filters.append(Session.session_name.ilike(f"%{search_value}%"))

    if agent_id:
        # Handle multiple agent IDs - OPTIMIZED VERSION
        agent_ids = agent_id if isinstance(agent_id, list) else [agent_id]

        # Separate UUIDs and BigInteger IDs
        uuid_agent_ids: List[UUID] = []
        bigint_agent_ids: List[int] = []

        for agent_id_val in agent_ids:
            # Try as UUID first
            try:
                uuid_agent_ids.append(UUID(str(agent_id_val)))
            except (ValueError, TypeError):
                # Try as BigInteger
                try:
                    bigint_agent_ids.append(int(agent_id_val))
                except (ValueError, TypeError):
                    # Ignore invalid agent IDs
                    logger.warning(f"Invalid agent_id format: {agent_id_val}")

        agent_conditions = []

        # Filter by Agent.public_id (UUID) - matches PRIMARY agent
        if uuid_agent_ids:
            agent_subquery = select(Agent.id).where(Agent.public_id.in_(uuid_agent_ids))
            agent_conditions.append(Session.agent_id.in_(agent_subquery))

        # Filter by Session.agent_id (BigInteger) for direct BigInteger IDs
        if bigint_agent_ids:
            agent_conditions.append(Session.agent_id.in_(bigint_agent_ids))

        # SIMPLIFIED: Check session_metadata for agent_id/agentId instead of complex JSONB queries
        # This is much faster than scanning agno_sessions JSONB arrays
        if uuid_agent_ids:
            for uuid_val in uuid_agent_ids:
                uuid_str = str(uuid_val)
                # Check agent_id or agentId in session_metadata
                agent_conditions.append(
                    or_(
                        Session.session_metadata["agent_id"].as_string() == uuid_str,
                        Session.session_metadata["agentId"].as_string() == uuid_str,
                    )
                )

        # NEW: Check agno_sessions.runs JSONB for agents_involved
        # This allows filtering by agents that are delegated/involved but not primary
        # Execute a separate query first to get session_ids from agno_sessions
        if uuid_agent_ids or bigint_agent_ids:
            from sqlalchemy import text as sql_text
            
            # Build a list of agent IDs to search for (as strings)
            agent_ids_to_search = [str(uuid_val) for uuid_val in uuid_agent_ids] + [str(bigint_val) for bigint_val in bigint_agent_ids]
            
            if agent_ids_to_search:
                # OPTIMIZATION: First get the user's session IDs to limit the agno_sessions scan
                # This reduces the dataset from all sessions to just this user's sessions
                user_sessions_query = select(Session.id, Session.public_id).where(
                    Session.user_id == current_user.id
                )
                user_sessions_result = await db.execute(user_sessions_query)
                user_session_ids = []
                for row in user_sessions_result.all():
                    user_session_ids.append(str(row[0]))  # id
                    user_session_ids.append(str(row[1]))  # public_id
                
                if user_session_ids:
                    # Query agno_sessions only for this user's sessions
                    # Use regex for better performance than multiple ILIKE conditions
                    regex_pattern = '|'.join([agent_id_str for agent_id_str in agent_ids_to_search])
                    
                    # Use ANY to efficiently match session_ids in the list
                    agno_query = sql_text("""
                        SELECT DISTINCT session_id 
                        FROM agno_sessions 
                        WHERE session_id = ANY(:session_ids)
                        AND runs::text ~ :regex_pattern
                    """)
                    
                    # Execute with user's session IDs and regex pattern
                    agno_result = await db.execute(agno_query, {
                        "session_ids": user_session_ids,
                        "regex_pattern": regex_pattern
                    })
                    agno_session_ids = [row[0] for row in agno_result.all()]
                else:
                    agno_session_ids = []
                
                logger.info(f"Agent filtering: Found {len(agno_session_ids)} sessions in agno_sessions with target agents")
                
                # Add condition to match these session_ids
                if agno_session_ids:
                    # Convert string session_ids to UUIDs for comparison
                    # agno_sessions.session_id can be either Session.id or Session.public_id
                    uuid_session_ids = []
                    for sid in agno_session_ids:
                        try:
                            uuid_session_ids.append(UUID(str(sid)))
                        except (ValueError, TypeError):
                            # Skip invalid UUIDs
                            logger.warning(f"Invalid UUID in agno_sessions: {sid}")
                    
                    if uuid_session_ids:
                        # Match against both Session.id and Session.public_id
                        agent_conditions.append(
                            or_(
                                Session.id.in_(uuid_session_ids),
                                Session.public_id.in_(uuid_session_ids)
                            )
                        )

        # Combine all agent conditions with OR (session matches if agent appears anywhere)
        logger.info(f"Agent filtering: Total agent_conditions: {len(agent_conditions)}")
        if agent_conditions:
            base_filters.append(or_(*agent_conditions))
            logger.info(f"Agent filtering: Added OR filter with {len(agent_conditions)} conditions to base_filters")
    
    # Filter by agent_name - OPTIMIZED: check only in metadata
    # This avoids expensive Run and Event table scans
    if agent_name:
        agent_names = agent_name if isinstance(agent_name, list) else [agent_name]
        agent_name_conditions = []
        
        # Filter by Session.session_metadata["agent_name"] or ["agentName"]
        for name in agent_names:
            if name:
                name_lower = name.lower().strip()
                agent_name_conditions.append(
                    or_(
                        func.lower(Session.session_metadata["agent_name"].as_string()) == name_lower,
                        func.lower(Session.session_metadata["agentName"].as_string()) == name_lower,
                    )
                )
        
        # Combine all agent_name conditions with OR (session matches if it has any of the agent names)
        if agent_name_conditions:
            base_filters.append(or_(*agent_name_conditions))

    # Store date filters separately - they will be applied in _build_grouped_session_summary
    # using the correct subquery (sort_activity) to match grouping logic
    date_from_naive = None
    date_to_naive = None
    if date_from:
        # Frontend sends UTC datetime already converted from local time
        if date_from.tzinfo is not None:
            date_from_naive = date_from.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            date_from_naive = date_from

    if date_to:
        # Frontend sends UTC datetime already converted from local time
        if date_to.tzinfo is not None:
            date_to_naive = date_to.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            date_to_naive = date_to

    # If page is provided, calculate offset from page number
    actual_offset = offset
    if page is not None:
        actual_offset = (page - 1) * limit

    groups_response, grouped_total, grouped_has_more = await _build_grouped_session_summary(
        db=db,
        base_filters=base_filters,
        activity_field=activity_field,  # Pass activity_field for internal use
        limit=limit,
        offset=actual_offset,
        group_id=group_id,  # Pass group_id for group-specific pagination
        today_start=today_start,
        today_end=today_end,
        previous_7_days_start=previous_7_days_start,
        previous_7_days_end=previous_7_days_end,
        previous_30_days_start=previous_30_days_start,
        previous_30_days_end=previous_30_days_end,
        previous_year_start=previous_year_start,
        previous_year_end=previous_year_end,
        older_end=older_end,
        date_from=date_from_naive,  # Pass date filters to apply using correct subquery
        date_to=date_to_naive,
        current_user=current_user,
    )

    return {
        "success": True,
        "groups": groups_response,
        "total_count": grouped_total,
        "has_more": grouped_has_more,
        "correlation_id": correlation_id,
    }


@router.get("/sessions/{session_id}")
async def get_chat_session(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Get chat session details with agents involved."""
    correlation_id = get_correlation_id()

    await _enforce_chat_rate_limit(current_user)

    # Get session with agent join
    result = await db.execute(
        select(Session, Agent)
        .join(Agent, Session.agent_id == Agent.id)
        .where(
            Session.id == UUID(str(session_id)),
            Session.user_id == current_user.id
        )
    )
    row = result.first()

    if not row:
        _raise_chat_error(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="SESSION_NOT_FOUND",
            error_message="Chat session not found",
            details={"field": "session_id", "message": "Specified session does not exist"},
        )

    session, agent_obj = row
    _ensure_session_active(session)

    # Sync title from agno_sessions (same as messages endpoint does)
    await _sync_session_title_from_agno(session, db)

    # Extract metadata
    metadata = session.session_metadata.copy() if session.session_metadata else {}
    
    # Get primary agent info from the joined Agent object
    primary_agent_id = str(agent_obj.public_id) if agent_obj and agent_obj.public_id else None
    primary_agent_name = agent_obj.name if agent_obj else None
    
    # Fallback to metadata if agent not found in join
    if not primary_agent_id:
        agent_id_from_metadata = metadata.get("agent_id") or metadata.get("agentId")
        if agent_id_from_metadata:
            primary_agent_id = str(agent_id_from_metadata)
    if not primary_agent_name:
        primary_agent_name = metadata.get("agent_name") or metadata.get("agentName")
    
    is_favorite = metadata.get("is_favorite")
    if is_favorite is None:
        is_favorite = metadata.get("isFavorite")
    if is_favorite is None:
        is_favorite = metadata.get("is_favourite", False)
    attachments = metadata.get("attachments", [])
    attachment_count = metadata.get("attachment_count", len(attachments))
    
    last_message_result = await db.execute(
        select(func.max(Message.created_at)).where(Message.session_id == session.id)
    )
    last_message_at = last_message_result.scalar()
    if not last_message_at:
        last_message_at = session.updated_at
    
    # Extract agents_involved from runs (similar to grouped sessions endpoint)
    agents_involved: List[Dict[str, Optional[str]]] = []
    
    try:
        # First, try to get data from agno_sessions table (fastest and most complete)
        # This is the same approach as the groups endpoint
        # Try BOTH session.session_id and session.public_id (agno_sessions might use either)
        session_id_str = str(session.session_id)
        session_public_id_str = str(session.public_id)
        logger.info(f"Querying agno_sessions for session_id={session_id_str} or public_id={session_public_id_str}")
        
        agno_query = text("""
            SELECT runs
            FROM agno_sessions
            WHERE session_id IN (:session_id, :public_id)
            ORDER BY created_at DESC
            LIMIT 1
        """)
        
        agno_result = await db.execute(agno_query, {
            "session_id": session_id_str,
            "public_id": session_public_id_str
        })
        agno_row = agno_result.fetchone()
        
        logger.info(f"agno_sessions query result: found={agno_row is not None}")
        
        agents_map: Dict[str, Dict[str, Optional[str]]] = {}
        
        if agno_row and agno_row[0]:
            runs_data = agno_row[0]
            logger.info(f"agno_sessions runs_data type: {type(runs_data)}, is_list: {isinstance(runs_data, list)}")
            
            # Parse runs data if it's a string
            if isinstance(runs_data, str):
                try:
                    runs_data = json.loads(runs_data)
                    logger.info(f"Parsed runs_data from JSON string, length: {len(runs_data) if isinstance(runs_data, list) else 'not a list'}")
                except Exception as e:
                    logger.warning(f"Failed to parse runs_data JSON: {str(e)}")
                    runs_data = []
            
            # Process runs to extract agents
            if isinstance(runs_data, list) and len(runs_data) > 0:
                logger.info(f"Processing {len(runs_data)} runs from agno_sessions")

                # Process ALL runs, not just the last one (to capture all agents involved)
                for run_idx, run_data in enumerate(runs_data):
                    if not isinstance(run_data, dict):
                        continue

                    logger.info(f"Processing run {run_idx + 1}/{len(runs_data)}: {run_data.get('run_id', 'unknown')}")

                    # Extract team info (only from first occurrence)
                    team_id = run_data.get("team_id") or metadata.get("team_id") or metadata.get("teamId")
                    team_name = run_data.get("team_name") or metadata.get("team_name") or metadata.get("teamName")

                    # Skip adding team if there's a primary agent (they represent the same super agent)
                    # The primary agent will be added later, so we avoid duplication
                    if (team_id or team_name) and not primary_agent_id:
                        # Add team/super agent if present
                        # Use team_name as key to avoid duplicates when same team has different IDs across runs
                        team_key = f"team_{team_name}" if team_name else str(team_id)
                        if team_key not in agents_map:  # Only add once
                            agents_map[team_key] = {
                                "agent_id": str(team_id) if team_id else None,
                                "agent_public_id": None,
                                "agent_name": team_name or "Team",
                                "role": "super_agent",
                            }

                    # Extract agents from member_responses
                    member_responses = run_data.get("member_responses") or run_data.get("memberResponses") or []
                    if isinstance(member_responses, list):
                        for member in member_responses:
                            if isinstance(member, dict):
                                member_agent_id = member.get("agent_id") or member.get("agentId")
                                member_agent_public_id = member.get("agent_public_id") or member.get("agentPublicId")
                                member_agent_name = member.get("agent_name") or member.get("agentName")

                                agent_key = str(member_agent_id) if member_agent_id else str(member_agent_public_id) if member_agent_public_id else None
                                if agent_key and agent_key not in agents_map:
                                    agents_map[agent_key] = {
                                        "agent_id": str(member_agent_id) if member_agent_id else None,
                                        "agent_public_id": str(member_agent_public_id) if member_agent_public_id else None,
                                        "agent_name": member_agent_name,
                                    }

                    # Extract agents from events
                    events = run_data.get("events") or []
                    if isinstance(events, list):
                        for event in events:
                            if isinstance(event, dict):
                                event_agent_id = event.get("agent_id") or event.get("agentId")
                                event_agent_name = event.get("agent_name") or event.get("agentName")

                                if event_agent_id or event_agent_name:
                                    agent_key = str(event_agent_id) if event_agent_id else f"name_{event_agent_name}"

                                    if agent_key not in agents_map:
                                        agents_map[agent_key] = {
                                            "agent_id": str(event_agent_id) if event_agent_id else None,
                                            "agent_public_id": None,
                                            "agent_name": event_agent_name,
                                        }
            
            agents_involved = list(agents_map.values())
            logger.info(f"Extracted {len(agents_involved)} agents from agno_sessions runs")
        else:
            logger.warning(f"No agno_sessions data found for session {session_public_id_str}")
        
        # ALWAYS ensure the primary agent is included in agents_involved
        # Check if primary agent is already in the list
        primary_agent_exists = False
        if primary_agent_id:
            for agent in agents_involved:
                if agent.get("agent_id") == primary_agent_id or agent.get("agent_public_id") == primary_agent_id:
                    primary_agent_exists = True
                    break
        
        # If primary agent not found in agents_involved, add it at the beginning
        if not primary_agent_exists and (primary_agent_id or primary_agent_name):
            agents_involved.insert(0, {
                "agent_id": primary_agent_id,
                "agent_public_id": primary_agent_id,
                "agent_name": primary_agent_name,
            })
            logger.info(f"Added primary agent to agents_involved (was missing)")
        
        logger.info(f"Final agents_involved count: {len(agents_involved)}")
    
    except Exception as e:
        logger.error(f"Error extracting agents_involved for session {session_id}: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        # Fallback to primary agent if extraction fails
        agents_involved = []
        if primary_agent_id or primary_agent_name:
            agents_involved = [{
                "agent_id": primary_agent_id,
                "agent_public_id": primary_agent_id,
                "agent_name": primary_agent_name,
            }]
    
    return {
        "success": True,
        "id": str(session.id),
        "session_id": session.session_id,
        "title": session.session_name,
        "createdAt": session.created_at.isoformat(),
        "created_at": session.created_at.isoformat(),  # Alias for consistency
        "agent_id": primary_agent_id,
        "agentId": primary_agent_id,  # Alias for consistency
        "agent_name": primary_agent_name,
        "agentName": primary_agent_name,  # Alias for consistency
        "is_favorite": bool(is_favorite),
        "isFavorite": bool(is_favorite),  # Alias for consistency
        "attachments": attachments,
        "attachmentCount": attachment_count,
        "agents_involved": agents_involved,
        "session_metadata": metadata,
        "last_message_at": last_message_at.isoformat() if last_message_at else None,
        "correlation_id": correlation_id
    }


@router.patch("/sessions/{session_id}/favourite")
async def mark_chat_session_favourite(
    session_id: UUID,
    request: Optional[FavoriteUpdateRequest] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Mark a chat session as favourite by session identifier."""
    is_favorite_value = request.is_favorite if request and request.is_favorite is not None else True

    await _enforce_chat_rate_limit(current_user)

    result = await db.execute(
        select(Session).where(
            Session.id == UUID(str(session_id)),
            Session.user_id == current_user.id
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        _raise_chat_error(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="SESSION_NOT_FOUND",
            error_message="Chat session not found",
            details={"field": "session_id", "message": "Specified session does not exist"},
        )

    _ensure_session_active(session)

    metadata = session.session_metadata or {}
    
    # Update metadata with favorite status
    metadata["is_favorite"] = is_favorite_value
    metadata.pop("isFavorite", None)
    metadata.pop("is_favourite", None)
    # Clean up the stored timestamp from metadata if it exists
    metadata.pop("original_updated_at_before_favorite", None)
    
    # Preserve updated_at by using direct SQL update
    current_updated_at = session.updated_at
    await db.execute(
        update(Session)
        .where(Session.id == session.id)
        .values(
            session_metadata=metadata,
            updated_at=current_updated_at  # Preserve original timestamp
        )
    )
    await db.commit()
    await db.refresh(session)

    correlation_id = get_correlation_id()
    log_chat_favorite_toggled(
        chat_id=str(session.id),
        session_id=session.session_id,
        user_id=str(current_user.id),
        username=current_user.username or current_user.email,
        is_favorite=is_favorite_value,
        correlation_id=correlation_id,
        email=current_user.email,
        role="ADMIN" if current_user.is_admin else "NORMAL",
        department=current_user.azure_department,
        user_entra_id=current_user.azure_ad_id,
    )

    return {
        "success": True,
        "session_id": session.session_id,
        "is_favorite": is_favorite_value,
        "correlation_id": correlation_id,
    }


@router.delete("/sessions/{session_id}")
async def delete_chat_session_by_session_id(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Delete a chat session and all associated messages using session identifier."""
    await _enforce_chat_rate_limit(current_user)

    result = await db.execute(
        select(Session).where(
            Session.id == session_id,
            Session.user_id == current_user.id,
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        _raise_chat_error(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="SESSION_NOT_FOUND",
            error_message="Chat session not found",
            details={"field": "session_id", "message": "Specified session does not exist"},
        )

    correlation_id = get_correlation_id()
    await db.delete(session)
    await db.commit()

    log_chat_session_deleted(
        chat_id=str(session.id),
        session_id=session.session_id,
        user_id=str(current_user.id),
        username=current_user.username or current_user.email,
        correlation_id=correlation_id,
        email=current_user.email,
        role="ADMIN" if current_user.is_admin else "NORMAL",
        department=current_user.azure_department,
        user_entra_id=current_user.azure_ad_id,
    )

    # Use the session's title (session_name) in the response message
    title = session.session_name or "Untitled Chat"

    return {
        "success": True,
        "message": f"Successfully Deleted {title}",
        "correlation_id": correlation_id,
    }


def _generate_deterministic_message_id(
    session_id: str,
    run_id: Optional[str],
    role: str,
    content: str,
    created_at: Optional[datetime]
) -> str:
    """
    Generate a deterministic UUID for a message based on its content and metadata.
    This ensures the same message always gets the same ID, even if it doesn't have an ID in agno_sessions.
    """
    # Create a unique string from message attributes
    created_at_str = created_at.isoformat() if created_at else "unknown"
    message_key = f"{session_id}|{run_id or 'no-run'}|{role}|{content[:200]}|{created_at_str}"
    
    # Generate deterministic UUID using uuid5 (SHA-1 based)
    # Use a fixed namespace UUID for message IDs
    MESSAGE_NAMESPACE = UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')  # Standard DNS namespace
    message_uuid = uuid5(MESSAGE_NAMESPACE, message_key)
    return str(message_uuid)


def _construct_events_from_run_data(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Construct basic events from run data when events are not available from AGNO API.
    
    This is used for agent-specific runs where events might not be stored in agno_sessions
    or available from AGNO API.
    """
    events = []
    run_id = run.get("run_id")
    status = run.get("status", "").upper()
    created_at = run.get("created_at")
    agent_id = run.get("agent_id")
    agent_name = run.get("agent_name")
    session_id = run.get("session_id")
    content = run.get("content")
    metrics = run.get("metrics", {})
    
    # Convert created_at to timestamp if it's a datetime or string
    if isinstance(created_at, datetime):
        created_at_ts = int(created_at.timestamp())
    elif isinstance(created_at, (int, float)):
        created_at_ts = int(created_at)
    else:
        created_at_ts = int(datetime.now(timezone.utc).timestamp())
    
    # Construct RunStarted event
    events.append({
        "created_at": created_at_ts,
        "event": "RunStarted",
        "run_id": run_id,
        "session_id": session_id,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "model": run.get("model"),
        "model_provider": run.get("model_provider")
    })
    
    # If run is completed, add RunContentCompleted and RunCompleted events
    if status == "COMPLETED":
        # Estimate completion time (use duration from metrics if available)
        duration = metrics.get("duration", 0)
        completed_at_ts = created_at_ts + int(duration) if duration else created_at_ts + 1
        
        events.append({
            "created_at": completed_at_ts - 1,
            "event": "RunContentCompleted",
            "run_id": run_id,
            "session_id": session_id
        })
        
        events.append({
            "created_at": completed_at_ts,
            "event": "RunCompleted",
            "run_id": run_id,
            "session_id": session_id,
            "content": content,
            "content_type": run.get("content_type", "str"),
            "metrics": metrics
        })
    elif status == "FAILED":
        # Add RunFailed event
        events.append({
            "created_at": created_at_ts + 1,
            "event": "RunFailed",
            "run_id": run_id,
            "session_id": session_id,
            "error_message": run.get("error_message")
        })
    
    return events


def _generate_attachment_sas_url(blob_name: str) -> str:
    """
    Generate a SAS URL for an attachment with configurable expiration.
    
    Args:
        blob_name: Azure blob path
        
    Returns:
        SAS URL with expiration time from settings (default: 30 minutes)
    """
    blob_service = BlobStorageService()
    return blob_service.generate_blob_access_url(
        blob_name=blob_name,
        visibility="public",
        expiry_hours=settings.chat_attachment_sas_token_expiry_hours,
    )


@router.get("/sessions/{session_id}/messages")
async def get_chat_messages_by_session(
    http_request: Request,
    session_id: UUID,
    limit: int = Query(10, ge=1, le=20),
    before_message_id: Optional[UUID] = Query(None, description="Return messages created before this message ID"),
    include_system: bool = Query(False, description="Include system messages"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client = Depends(get_redis)
) -> Dict[str, Any]:
    """Retrieve chat messages for a session with pagination and rich metadata from agno_sessions table."""
    correlation_id = get_correlation_id()

    await _enforce_chat_rate_limit(current_user)

    # First verify session exists in our sessions table
    # Use Session.id directly since session_id is a hybrid property that returns str(id)
    # The session_id parameter from the URL is a UUID, so we can match it directly to Session.id
    session_result = await db.execute(
        select(Session).where(
            Session.id == session_id,
            Session.user_id == current_user.id,
        )
    )
    session = session_result.scalar_one_or_none()
    
    # If session not found in Session table, check if it exists in agno_sessions
    # This handles cases where sessions are created via AGNO but not yet synced to Session table
    if not session:
        # Check if session exists in agno_sessions
        agno_check_query = text("""
            SELECT session_id, user_id
            FROM agno_sessions
            WHERE session_id = :session_id
            AND (user_id = :user_email OR user_id = :user_uuid)
            LIMIT 1
        """)
        agno_check_params = {
            "session_id": str(session_id),
            "user_email": current_user.email,
            "user_uuid": str(current_user.id)
        }
        agno_check_result = await db.execute(agno_check_query, agno_check_params)
        agno_check_row = agno_check_result.first()
        
        if agno_check_row:
            # Session exists in agno_sessions but not in Session table
            # Create a minimal Session record to allow the request to proceed
            # This can happen when sessions are created via AGNO team routing
            logger.info(
                f"Session {session_id} found in agno_sessions but not in Session table. "
                f"Creating minimal Session record for user {current_user.email}"
            )
            
            # Get agent_id (default to Super Agent)
            async def get_super_agent() -> Agent:
                """Get Super Agent record, return the Agent object."""
                result = await db.execute(select(Agent).where(Agent.name == "Super Agent"))
                super_agent = result.scalar_one_or_none()
                if not super_agent:
                    # Create default Super Agent if it doesn't exist
                    super_agent = Agent(
                        name="Super Agent",
                        agent_id="super-agent",
                        description="Default AI assistant",
                        is_enabled=True
                    )
                    db.add(super_agent)
                    await db.flush()
                return super_agent
            
            super_agent = await get_super_agent()
            agent_id = super_agent.id if super_agent else None
            
            if not agent_id:
                _raise_chat_error(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    error_code="INTERNAL_ERROR",
                    error_message="Failed to resolve agent",
                    details={"field": "server", "message": "Super Agent not found"},
                )
            
            # Create a minimal Session record
            session = Session(
                id=session_id,  # Use the session_id as the id
                user_id=current_user.id,
                agent_id=agent_id,
                session_name="Chat Session",
                session_metadata={"agno_session_id": str(session_id)},
                session_type="chat",
                status="active",
            )
            db.add(session)
            try:
                await db.commit()
                await db.refresh(session)
                logger.info(f"Created Session record for session_id={session_id}")
            except Exception as e:
                await db.rollback()
                logger.error(f"Failed to create Session record: {str(e)}")
                # If we can't create the Session, we can still try to proceed with agno_sessions lookup
                # But we need a session object, so we'll create a temporary one
                session = Session(
                    id=session_id,
                    user_id=current_user.id,
                    agent_id=agent_id,
                    session_name="Chat Session",
                    session_metadata={"agno_session_id": str(session_id)},
                    session_type="chat",
                    status="active",
                )
        else:
            # Session doesn't exist in either table
            _raise_chat_error(
                status_code=status.HTTP_404_NOT_FOUND,
                error_code="SESSION_NOT_FOUND",
                error_message="Chat session not found",
                details={"field": "session_id", "message": "Specified session does not exist"},
            )

    _ensure_session_active(session)

    # Check if there's an active stream in Redis for this session.
    # IMPORTANT: Do not return early here. We still need to return existing messages
    # and attach streaming_info for the in-progress message.
    active_streaming_info = await check_streaming_status(redis_client, session_id)
    if active_streaming_info:
        logger.info(
            f"Active stream detected for session {session_id}: "
            f"stream_id={active_streaming_info.get('stream_id')}, "
            f"status={active_streaming_info.get('status')}"
        )

    def _build_streaming_info_payload() -> Optional[Dict[str, Any]]:
        """Build streaming_info payload from active Redis stream data."""
        if not active_streaming_info:
            return None

        return {
            "stream_id": active_streaming_info.get("stream_id"),
            "status": active_streaming_info.get("status", "streaming"),
            "message": "Streaming was interrupted during initiation but is still in progress. Please wait for completion and refresh after few secs to view the full reasoning and response.",
        }

    def _map_active_stream_to_user_message(messages: List[Dict[str, Any]]) -> None:
        """Map active stream_id to the most recent user message when missing.

        If streaming is active and no user message currently has that stream_id,
        assign it to the most recent user message.
        Also add/update an in-progress assistant placeholder carrying streaming_info.
        """
        if not active_streaming_info or not messages:
            return

        active_stream_id = active_streaming_info.get("stream_id")
        if not active_stream_id:
            return

        per_message_streaming_info = {
            "stream_id": active_stream_id,
            "status": active_streaming_info.get("status", "streaming"),
            "message": "Streaming was interrupted during initiation but is still in progress. Please wait for completion and refresh after few secs to view the full reasoning and response.",
        }

        user_with_active_stream_exists = any(
            msg.get("type") == "user" and msg.get("stream_id") == active_stream_id
            for msg in messages
        )
        if user_with_active_stream_exists:
            for msg in reversed(messages):
                if msg.get("type") == "user" and msg.get("stream_id") == active_stream_id:
                    msg["streaming_info"] = per_message_streaming_info
                    break
        else:
            for msg in reversed(messages):
                if msg.get("type") == "user":
                    msg["stream_id"] = active_stream_id
                    msg["streaming_info"] = per_message_streaming_info
                    logger.info(
                        f"Mapped active stream_id={active_stream_id} to latest user message "
                        f"message_id={msg.get('message_id')}"
                    )
                    break

        # Ensure an assistant placeholder exists for the active stream inside messages
        assistant_with_active_stream_exists = any(
            msg.get("type") == "assistant" and msg.get("stream_id") == active_stream_id
            for msg in messages
        )

        if assistant_with_active_stream_exists:
            for msg in reversed(messages):
                if msg.get("type") == "assistant" and msg.get("stream_id") == active_stream_id:
                    msg["streaming_info"] = per_message_streaming_info
                    break
        else:
            assistant_placeholder = {
                "message_id": f"streaming-{active_stream_id}",
                "type": "assistant",
                "content": "",
                "attachments": [],
                "custom_fields": {},
                "agents_involved": [],
                "timestamp": datetime.utcnow().isoformat(),
                "stream_id": active_stream_id,
                "run_id": active_streaming_info.get("run_id"),
                "agent_id": None,
                "agent_public_id": None,
                "agent_name": None,
                "team_id": None,
                "team_name": None,
                "local_message_id": None,
                "streaming_info": per_message_streaming_info,
            }
            messages.append(assistant_placeholder)

    # Get session_id string for agno_sessions lookup
    # IMPORTANT: Based on database analysis, agno_sessions.session_id stores sessions.id (NOT public_id)
    # messages.session_id also stores sessions.id
    # So we should prioritize session.id over session.public_id
    
    # Priority order:
    # 1. workflow_id (if set, it might contain the agno session_id)
    # 2. session_metadata.agno_session_id or session_metadata.session_id
    # 3. session.id (PRIMARY - this is what agno_sessions.session_id stores)
    # 4. session.public_id (fallback - in case some old records use it)
    
    agno_session_id = None
    
    # Priority 1: Check workflow_id first
    if session.workflow_id:
        agno_session_id = str(session.workflow_id)
        logger.info(f"Using workflow_id as agno_session_id: {agno_session_id}")
    
    # Priority 2: Check session metadata for explicit agno_session_id
    if not agno_session_id and session.session_metadata:
        explicit_agno_id = session.session_metadata.get("agno_session_id") or session.session_metadata.get("session_id")
        if explicit_agno_id:
            agno_session_id = str(explicit_agno_id)
            logger.info(f"Using session_metadata agno_session_id: {agno_session_id}")
    
    # Priority 3: Use session.id (PRIMARY - matches agno_sessions.session_id)
    if not agno_session_id:
        agno_session_id = session.session_id  # This is str(session.id)
        logger.info(f"Using session.id (via session_id property) as agno_session_id: {agno_session_id}")
    
    logger.info(
        f"Looking up agno_sessions with agno_session_id={agno_session_id} "
        f"(session.id={session.id}, session.public_id={session.public_id}, workflow_id={session.workflow_id})"
    )
    
    # Query agno_sessions table using raw SQL
    try:
        # Query agno_sessions table - it has session_id (string) and user_id (email or UUID)
        # Try multiple strategies with the determined agno_session_id
        agno_row = None
        
        # List of session_id candidates to try (in order of priority)
        # agno_sessions.session_id stores sessions.id, so prioritize id over public_id
        session_id_candidates = [agno_session_id]
        
        # Also try id and public_id if they're different from agno_session_id
        # Priority: id first (matches agno_sessions), then public_id (fallback)
        if session.id and str(session.id) != agno_session_id:
            session_id_candidates.append(str(session.id))
        if session.public_id and str(session.public_id) != agno_session_id:
            session_id_candidates.append(str(session.public_id))
        
        # Try each candidate
        for candidate_id in session_id_candidates:
            if not candidate_id:
                continue
                
            # Strategy 1: Try session_id only (session_id should be unique)
            query = text("""
                SELECT session_id, user_id, runs, session_data
                FROM agno_sessions
                WHERE session_id = :session_id
                ORDER BY created_at DESC
                LIMIT 1
            """)
            params = {
                "session_id": str(candidate_id)
            }
            logger.info(f"Querying agno_sessions with session_id={candidate_id} (session_id only)")
            result = await db.execute(query, params)
            agno_row = result.first()
            
            if agno_row:
                logger.info(f"Found agno_sessions with session_id={candidate_id}")
                agno_session_id = candidate_id  # Update to the one that worked
                break
            
            # Strategy 2: If not found, try with user email
            if not agno_row:
                query = text("""
                    SELECT session_id, user_id, runs, session_data
                    FROM agno_sessions
                    WHERE session_id = :session_id AND user_id = :user_email
                    ORDER BY created_at DESC
                    LIMIT 1
                """)
                params = {
                    "session_id": str(candidate_id),
                    "user_email": current_user.email
                }
                logger.info(f"Querying agno_sessions with session_id={candidate_id}, user_email={current_user.email}")
                result = await db.execute(query, params)
                agno_row = result.first()
                
                if agno_row:
                    logger.info(f"Found agno_sessions with session_id={candidate_id} and user_email")
                    agno_session_id = candidate_id  # Update to the one that worked
                    break
            
            # Strategy 3: If still not found, try with user UUID
            if not agno_row:
                query = text("""
                    SELECT session_id, user_id, runs, session_data
                    FROM agno_sessions
                    WHERE session_id = :session_id AND user_id = :user_uuid
                    ORDER BY created_at DESC
                    LIMIT 1
                """)
                params = {
                    "session_id": str(candidate_id),
                    "user_uuid": str(current_user.id)
                }
                logger.info(f"Querying agno_sessions with session_id={candidate_id}, user_uuid={current_user.id}")
                result = await db.execute(query, params)
                agno_row = result.first()
                
                if agno_row:
                    logger.info(f"Found agno_sessions with session_id={candidate_id} and user_uuid")
                    agno_session_id = candidate_id  # Update to the one that worked
                    break
        
        # If no agno_row found after trying all candidates, check for local messages
        # Do NOT fallback to latest session - when a specific session_id is requested,
        # we should only return data for that session, not a different one
        if not agno_row:
            logger.warning(
                f"agno_sessions not found for user {current_user.email} (id={current_user.id}), "
                f"session_id={session_id}, agno_session_id={agno_session_id}. "
                f"Tried: session_id only, session_id+email, session_id+UUID. "
                f"Checking for local messages in messages table."
            )
            
            # Check for local messages in messages table for this specific session
            local_messages_result = await db.execute(
                select(Message).where(
                    Message.session_id == session.id,
                    Message.user_id == current_user.id
                ).order_by(Message.created_at)
            )
            local_messages = list(local_messages_result.scalars().all())
            
            if not local_messages:
                # No messages in either agno_sessions or messages table
                
                return {
                    "success": True,
                    "session_id": session.session_id,
                    "session_name": session.session_name,
                    "messages": [],
                    "has_more": False,
                    "oldest_message_id": None,
                    "newest_message_id": None,
                    "correlation_id": correlation_id,
                    "runs_summary": [],
                }
            
            # Convert local messages to API format
            # First, fetch attachments for these messages
            attachment_message_ids = []
            message_id_to_public_id: Dict[str, str] = {}
            
            for msg in local_messages:
                attachment_message_ids.append(msg.id)
                attachment_message_ids.append(msg.public_id)
                message_id_to_public_id[str(msg.id)] = str(msg.public_id)
            
            attachments_map: Dict[str, List[Dict[str, Any]]] = {}
            
            if attachment_message_ids:
                attachments_query = select(Attachment).where(
                    and_(
                        Attachment.message_id.in_(attachment_message_ids),
                        Attachment.is_active == True,
                    )
                )
                attachments_result = await db.execute(attachments_query)
                attachments_list = attachments_result.scalars().all()
                
                logger.info(
                    f"Early return path - Attachment lookup: "
                    f"message_ids_checked={len(attachment_message_ids)}, "
                    f"attachments_found={len(attachments_list)}"
                )
                
                for attachment in attachments_list:
                    message_id_str = str(attachment.message_id)
                    public_id_str = message_id_to_public_id.get(message_id_str, message_id_str)
                    
                    # Use plain URL for frontend (not SAS URL)
                    # SAS URLs are only sent to external API, not to frontend
                    plain_url = attachment.blob_url
                    
                    attachment_data = {
                        "attachment_uuid": str(attachment.id),
                        "filename": attachment.file_name,
                        "url": plain_url,  # Plain URL for frontend
                    }
                    
                    for key in [message_id_str, public_id_str]:
                        if key not in attachments_map:
                            attachments_map[key] = []
                        if attachment_data not in attachments_map[key]:
                            attachments_map[key].append(attachment_data)
            
            formatted_messages = []
            for msg in local_messages:
                if not include_system and msg.role == "system":
                    continue
                
                # Get attachments for this message
                msg_attachments = attachments_map.get(str(msg.public_id), [])
                    
                formatted_msg = {
                    "message_id": str(msg.public_id),
                    "type": msg.role,
                    "content": _transform_cancellation_message(msg.content) if msg.content else "",  # Transform cancelled message
                    "attachments": msg_attachments,
                    "custom_fields": {},
                    "agents_involved": [],
                    "timestamp": msg.created_at.isoformat() if msg.created_at else None,
                    "stream_id": None,
                    "run_id": None,
                    "agent_id": str(msg.agent_id) if msg.agent_id else None,
                    "agent_public_id": None,
                    "agent_name": None,
                    "team_id": None,
                    "team_name": None,
                }
                
                # Try to extract stream_id from tool_calls if present
                if msg.tool_calls:
                    try:
                        tool_calls_data = msg.tool_calls if isinstance(msg.tool_calls, dict) else {}
                        formatted_msg["stream_id"] = tool_calls_data.get("stream_id") or tool_calls_data.get("streamId")
                    except Exception:
                        pass
                
                # Include assistant_response for assistant messages if it exists in message_metadata
                if msg.role == "assistant" and msg.message_metadata and isinstance(msg.message_metadata, dict):
                    if "assistant_response" in msg.message_metadata:
                        formatted_msg["assistant_response"] = msg.message_metadata["assistant_response"]
                
                formatted_messages.append(formatted_msg)
            
            # Sort by timestamp (oldest first)
            formatted_messages.sort(key=lambda x: x["timestamp"] or "")

            # Map active stream_id to latest user message when needed
            _map_active_stream_to_user_message(formatted_messages)
            
            return {
                "success": True,
                "session_id": session.session_id,
                "session_name": session.session_name,
                "messages": formatted_messages,
                "has_more": False,
                "oldest_message_id": str(formatted_messages[0]["message_id"]) if formatted_messages else None,
                "newest_message_id": str(formatted_messages[-1]["message_id"]) if formatted_messages else None,
                "correlation_id": correlation_id,
                "runs_summary": [],
            }
        
        # Extract runs and session_data from agno_sessions
        # Raw SQL result is accessed by index: [0]=session_id, [1]=user_id, [2]=runs, [3]=session_data
        runs_data = None
        session_data = None
        agno_session_id_from_row = None
        agno_user_id_from_row = None
        
        if hasattr(agno_row, '_mapping'):
            # Row object with column mapping
            runs_data = agno_row._mapping.get('runs') if 'runs' in agno_row._mapping else None
            session_data = agno_row._mapping.get('session_data') if 'session_data' in agno_row._mapping else None
            agno_session_id_from_row = agno_row._mapping.get('session_id')
            agno_user_id_from_row = agno_row._mapping.get('user_id')
        
        if not runs_data or session_data is None:
            # Try by index (runs is 3rd column, index 2; session_data is 4th column, index 3)
            try:
                if runs_data is None:
                    runs_data = agno_row[2] if len(agno_row) > 2 else None
                if session_data is None:
                    session_data = agno_row[3] if len(agno_row) > 3 else None
                if agno_session_id_from_row is None:
                    agno_session_id_from_row = agno_row[0] if len(agno_row) > 0 else None
                if agno_user_id_from_row is None:
                    agno_user_id_from_row = agno_row[1] if len(agno_row) > 1 else None
            except (IndexError, TypeError):
                # Try as tuple/list
                if isinstance(agno_row, (tuple, list)) and len(agno_row) > 2:
                    if runs_data is None:
                        runs_data = agno_row[2] if len(agno_row) > 2 else None
                    if session_data is None:
                        session_data = agno_row[3] if len(agno_row) > 3 else None
                    if agno_session_id_from_row is None:
                        agno_session_id_from_row = agno_row[0] if len(agno_row) > 0 else None
                    if agno_user_id_from_row is None:
                        agno_user_id_from_row = agno_row[1] if len(agno_row) > 1 else None
        
        # Extract session_name from session_data and update sessions table
        agno_session_name = None
        if session_data:
            try:
                if isinstance(session_data, dict):
                    agno_session_name = session_data.get("session_name")
                elif isinstance(session_data, str):
                    session_data_dict = json.loads(session_data)
                    agno_session_name = session_data_dict.get("session_name")
            except Exception as e:
                logger.warning(f"Error extracting session_name from session_data: {str(e)}")
        
        # Update sessions table with session_name from agno_sessions if it exists and is different
        # BUT ONLY if user hasn't manually renamed it (check metadata flag)
        metadata = session.session_metadata or {}
        is_manually_renamed = metadata.get("title_manually_renamed", False)
        
        if agno_session_name and agno_session_name != session.session_name and not is_manually_renamed:
            try:
                session.session_name = agno_session_name
                await db.commit()
                await db.refresh(session)
                logger.info(
                    f"Updated session.session_name to '{agno_session_name}' "
                    f"for session_id={session.id}"
                )
            except Exception as e:
                await db.rollback()
                logger.warning(
                    f"Failed to update session.session_name: {str(e)}. "
                    f"Continuing without update."
                )
        elif is_manually_renamed and agno_session_name and agno_session_name != session.session_name:
            logger.debug(
                f"Skipping session_name update from agno_sessions for session_id={session.id} "
                f"because it was manually renamed by user"
            )
        
        # Use extracted values or fallback to session/current_user
        final_session_id = agno_session_id_from_row or session.session_id
        final_user_id = agno_user_id_from_row or str(current_user.id)
        
        logger.info(
            f"Fetched agno_sessions data for user {current_user.email}, "
            f"session_id={session_id}, has_runs={runs_data is not None}, "
            f"agno_session_id={final_session_id}, agno_user_id={final_user_id}"
        )
        
        # Also check local messages table for messages that might not be in agno_sessions
        # (e.g., user messages sent via POST /message endpoint)
        local_messages_result = await db.execute(
            select(Message).where(
                Message.session_id == session.id,
                Message.user_id == current_user.id
            ).order_by(Message.created_at)
        )
        local_messages = list(local_messages_result.scalars().all())
        
        logger.info(
            f"Found {len(local_messages)} local messages in messages table "
            f"for session_id={session.id} (session_id={session.session_id})"
        )
        
        if not runs_data and not local_messages:
            return {
                "success": True,
                "session_id": session.session_id,
                "session_name": session.session_name,
                "messages": [],
                "has_more": False,
                "oldest_message_id": None,
                "newest_message_id": None,
                "correlation_id": correlation_id,
            }
        
        # Create a map of local messages by content+role+timestamp for matching with AGNO messages
        # This allows us to use existing local message IDs instead of generating new ones
        local_message_map: Dict[str, Message] = {}  # key -> Message
        for local_msg in local_messages:
            # Create a key from content, role, and timestamp for matching
            local_content = (local_msg.content or "").strip()
            local_role = local_msg.role
            local_timestamp = local_msg.created_at.isoformat() if local_msg.created_at else None
            # Use content prefix (first 200 chars) for matching
            content_key = local_content[:200] if local_content else ""
            map_key = f"{local_role}|{content_key}|{local_timestamp}"
            local_message_map[map_key] = local_msg
        
        logger.info(
            f"Created local message map with {len(local_message_map)} messages for ID matching"
        )
        
        # Track which local message IDs have been matched to AGNO messages
        # This prevents matching the same local message to multiple AGNO messages
        matched_local_message_ids = set()
        
        # Extract all messages from all runs
        all_messages: List[Dict[str, Any]] = []
        runs_summary: List[Dict[str, Any]] = []  # Track runs for summary
        
        # Handle both list of runs and single run object
        runs_list = runs_data if isinstance(runs_data, list) else [runs_data] if runs_data else []
        
        logger.info(
            f"Extracting messages from agno_sessions: "
            f"runs_data_type={type(runs_data).__name__}, "
            f"runs_list_length={len(runs_list)}"
        )
        
        # Collect all agent_ids from runs and events for batch lookup
        # This ensures we always show current agent names from agents table, not historical names
        agent_ids_in_runs = set()
        for run in runs_list:
            if isinstance(run, dict):
                agent_id = run.get("agent_id")
                if agent_id:
                    agent_ids_in_runs.add(str(agent_id))
                
                # Also collect from events
                events = run.get("events", [])
                if isinstance(events, list):
                    for event in events:
                        if isinstance(event, dict):
                            event_agent_id = event.get("agent_id")
                            if event_agent_id:
                                agent_ids_in_runs.add(str(event_agent_id))
                
                # Also collect from member_responses
                member_responses = run.get("member_responses") or run.get("memberResponses") or []
                if isinstance(member_responses, list):
                    for member in member_responses:
                        if isinstance(member, dict):
                            member_agent_id = member.get("agent_id") or member.get("agentId")
                            if member_agent_id:
                                agent_ids_in_runs.add(str(member_agent_id))
        
        # Batch lookup current agent names from agents table
        runs_agent_names_map: Dict[str, str] = {}  # agent_id/public_id -> current_name
        
        if agent_ids_in_runs:
            try:
                from uuid import UUID as UUIDType
                # Try to look up agents by public_id (UUID)
                valid_uuids = []
                for aid in agent_ids_in_runs:
                    try:
                        valid_uuids.append(UUIDType(aid))
                    except (ValueError, TypeError):
                        pass
                
                if valid_uuids:
                    runs_agents_query = select(Agent).where(Agent.public_id.in_(valid_uuids))
                    runs_agents_result = await db.execute(runs_agents_query)
                    runs_agents_list = runs_agents_result.scalars().all()
                    
                    for agent in runs_agents_list:
                        runs_agent_names_map[str(agent.public_id)] = agent.name
                    
                    logger.info(
                        f"Looked up {len(runs_agent_names_map)} current agent names from agents table "
                        f"for runs_summary and events"
                    )
            except Exception as e:
                logger.warning(f"Failed to look up agent names for runs: {e}")
        
        # Build a set of all run_ids to identify parent runs
        all_run_ids = set()
        for run in runs_list:
            if isinstance(run, dict):
                run_id = run.get("run_id")
                if run_id:
                    all_run_ids.add(str(run_id))
        
        # Filter out child runs (runs with parent_run_id that exists in our runs list)
        # For team runs, we only want to show top-level messages, not internal delegated agent messages
        child_run_ids = set()
        for run in runs_list:
            if isinstance(run, dict):
                parent_run_id = run.get("parent_run_id")
                if parent_run_id and str(parent_run_id) in all_run_ids:
                    child_run_id = run.get("run_id")
                    if child_run_id:
                        child_run_ids.add(str(child_run_id))
        
        logger.info(
            f"Filtering runs: total={len(runs_list)}, "
            f"child_runs={len(child_run_ids)}, "
            f"child_run_ids={list(child_run_ids)}"
        )
        
        for idx, run in enumerate(runs_list):
            if not isinstance(run, dict):
                logger.warning(f"Run {idx} is not a dict, skipping")
                continue
            
            # Check if this is a child run (delegated agent run within a team run)
            # Skip messages from child runs - they are internal to team workflow
            run_id = run.get("run_id")
            if run_id and str(run_id) in child_run_ids:
                logger.info(
                    f"Skipping child run {idx}: run_id={run_id}, "
                    f"parent_run_id={run.get('parent_run_id')}"
                )
                # Don't add child runs to runs_summary - only parent runs
                continue  # Skip processing messages from child runs
            
            # Check if this is a team run (has team_id or team_name) and agent_id is null
            # For team runs, we should skip agent_id processing
            is_team_run = bool(run.get("team_id") or run.get("team_name"))
            agent_id = run.get("agent_id")
            # If it's a team run and agent_id is null/empty, skip agent processing
            if is_team_run and not agent_id:
                agent_id = None
            
            logger.info(
                f"Processing run {idx}: run_id={run.get('run_id')}, "
                f"agent_id={agent_id}, team_id={run.get('team_id')}, "
                f"team_name={run.get('team_name')}, status={run.get('status')}"
            )
            
            # Track run summary (for frontend to show conversation groups)
            run_id = run.get("run_id")
            if run_id:
                # Extract events from the run
                run_events = run.get("events", [])
                if not isinstance(run_events, list):
                    run_events = []
                
                # If no events found, construct basic events from run data for agent-specific runs
                # Only construct events for agent runs, not team runs
                if not run_events and run.get("status") and not is_team_run:
                    run_events = _construct_events_from_run_data(run)
                
                # Extract input_content from input.input_content
                # input_content can be either a string or an array of message objects
                input_data = run.get("input", {})
                input_content = None
                if isinstance(input_data, dict):
                    input_content_raw = input_data.get("input_content")
                    if isinstance(input_content_raw, str):
                        # Direct string value
                        input_content = input_content_raw
                    elif isinstance(input_content_raw, list) and len(input_content_raw) > 0:
                        # Array of message objects - extract first user message
                        for msg_obj in input_content_raw:
                            if isinstance(msg_obj, dict):
                                msg_role = msg_obj.get("role", "")
                                if msg_role == "user":
                                    # Found user message, extract content
                                    input_content = msg_obj.get("content", "")
                                    break
                                # If no role specified, assume first item is user input
                                if input_content is None:
                                    input_content = msg_obj.get("content", "")
                
                # Extract content (assistant answer)
                content = run.get("content")
                
                # Extract member_responses if present (for team runs with delegated agents)
                member_responses = run.get("member_responses") or run.get("memberResponses") or []
                
                # Look up current agent name from agents table
                current_agent_name = None
                if agent_id:
                    agent_id_str = str(agent_id)
                    current_agent_name = runs_agent_names_map.get(agent_id_str)
                    if not current_agent_name:
                        # Fallback to AGNO name if not found in lookup
                        current_agent_name = run.get("agent_name")
                        logger.warning(
                            f"Agent {agent_id_str} not found in agents table, "
                            f"using historical name from AGNO: {current_agent_name}"
                        )
                
                runs_summary.append({
                    "run_id": str(run_id),
                    "team_id": run.get("team_id"),
                    "team_name": run.get("team_name"),
                    "session_id": final_session_id,
                    "user_id": final_user_id,
                    "input_content": input_content,  # User input
                    "content": _transform_cancellation_message(content),  # Transform cancelled message - Assistant answer
                    "agent_id": str(agent_id) if agent_id else None,
                    "agent_name": current_agent_name if (not is_team_run or agent_id) else None,
                    "status": run.get("status"),
                    "created_at": run.get("created_at"),
                    "message_count": 2,  # Always 2 messages per run: 1 user + 1 assistant
                    "events": run_events,  # Include events array
                    "member_responses": member_responses if isinstance(member_responses, list) else [],  # Include member_responses for agent tracking
                })
            
            # IMPORTANT: Skip processing run.messages array entirely!
            # The messages array contains conversation history from PREVIOUS runs,
            # which causes duplicate messages to appear in the response.
            # Instead, we ONLY extract:
            # 1. User message from run.input.input_content
            # 2. Assistant message from run.content
            # This ensures exactly ONE user-assistant pair per run.
            
            # Create user message from input_content
            if input_content:
                run_created_at = run.get("created_at")
                if isinstance(run_created_at, (int, float)):
                    user_msg_timestamp = datetime.fromtimestamp(run_created_at)
                elif isinstance(run_created_at, str):
                    try:
                        user_msg_timestamp = datetime.fromisoformat(run_created_at.replace('Z', '+00:00'))
                    except:
                        user_msg_timestamp = datetime.now()
                else:
                    user_msg_timestamp = datetime.now()

                # Generate deterministic ID for user message
                user_msg_id = _generate_deterministic_message_id(
                    session_id=agno_session_id or session.session_id,
                    run_id=run.get("run_id"),
                    role="user",
                    content=input_content,
                    created_at=user_msg_timestamp
                )

                # Add user message
                all_messages.append({
                    "id": user_msg_id,
                    "role": "user",
                    "content": input_content,
                    "created_at": user_msg_timestamp,
                    "run_id": run.get("run_id"),
                    "agent_id": agent_id if not is_team_run or agent_id else None,
                    "agent_name": run.get("agent_name") if not is_team_run or agent_id else None,
                    "team_id": run.get("team_id"),
                    "team_name": run.get("team_name"),
                    "message_data": {"role": "user", "content": input_content},
                    "local_message_id": None,  # Will be populated later from local_messages mapping
                })
            
            # Create assistant message from run.content
            run_content = run.get("content")
            if run_content and isinstance(run_content, str) and run_content.strip():
                run_created_at = run.get("created_at")
                if isinstance(run_created_at, (int, float)):
                    assistant_msg_timestamp = datetime.fromtimestamp(run_created_at)
                elif isinstance(run_created_at, str):
                    try:
                        assistant_msg_timestamp = datetime.fromisoformat(run_created_at.replace('Z', '+00:00'))
                    except:
                        assistant_msg_timestamp = datetime.now()
                else:
                    assistant_msg_timestamp = datetime.now()
                
                # Generate deterministic ID for assistant message
                assistant_msg_id = _generate_deterministic_message_id(
                    session_id=agno_session_id or session.session_id,
                    run_id=run.get("run_id"),
                    role="assistant",
                    content=run_content,
                    created_at=assistant_msg_timestamp
                )
                
                # Add assistant message
                all_messages.append({
                    "id": assistant_msg_id,
                    "role": "assistant",
                    "content": _transform_cancellation_message(run_content),  # Transform cancelled message
                    "created_at": assistant_msg_timestamp,
                    "run_id": run.get("run_id"),
                    "agent_id": None,  # Team run, no specific agent
                    "agent_name": None,
                    "team_id": run.get("team_id"),
                    "team_name": run.get("team_name"),
                    "message_data": {
                        "role": "assistant",
                        "content": _transform_cancellation_message(run_content),  # Transform cancelled message
                        "created_at": run_created_at,
                    },
                    "local_message_id": None,  # Will be populated later from local_messages mapping
                })
        
        # Create mapping between agno messages and local database messages
        # This allows us to include local_message_id in the response
        def find_local_message_match(agno_msg: Dict[str, Any], local_messages: List) -> Optional[str]:
            """Find matching local message ID for an agno message based on role, content, and timestamp."""
            agno_role = agno_msg.get("role")
            agno_content = (agno_msg.get("content") or "").strip()
            agno_timestamp = agno_msg.get("created_at")
            
            # Clean content for comparison (remove additional context added by AGNO)
            agno_content_clean = agno_content.split("\n\n<additional context>")[0].strip()
            
            best_match = None
            best_match_score = 0
            
            for local_msg in local_messages:
                # Must match role
                if local_msg.role != agno_role:
                    continue
                
                local_content = (local_msg.content or "").strip()
                local_content_clean = local_content.split("\n\n<additional context>")[0].strip()
                
                # Calculate similarity score
                score = 0
                
                # Exact content match (high score)
                if local_content_clean == agno_content_clean:
                    score += 100
                # Partial content match (first 200 chars)
                elif local_content_clean[:200] == agno_content_clean[:200]:
                    score += 50
                # Content substring match
                elif agno_content_clean in local_content_clean or local_content_clean in agno_content_clean:
                    score += 25
                
                # Timestamp proximity (within 5 seconds)
                if agno_timestamp and local_msg.created_at:
                    time_diff = abs((agno_timestamp - local_msg.created_at).total_seconds())
                    if time_diff <= 5:
                        score += 30
                    elif time_diff <= 30:
                        score += 10
                
                if score > best_match_score:
                    best_match_score = score
                    best_match = str(local_msg.id)
            
            # Only return match if score is high enough (at least partial content match)
            return best_match if best_match_score >= 25 else None
        
        # Map local message IDs to agno messages
        agno_to_local_id_map: Dict[str, str] = {}  # agno_message_id -> local_message_id
        used_local_message_ids = set()
        
        for msg_data in all_messages:
            agno_msg_id = msg_data.get("id")
            if not agno_msg_id:
                continue
            
            # First check if this agno message ID already matches a local message ID or public_id
            exact_match = False
            for local_msg in local_messages:
                if str(local_msg.id) == str(agno_msg_id) or str(local_msg.public_id) == str(agno_msg_id):
                    agno_to_local_id_map[str(agno_msg_id)] = str(local_msg.id)
                    used_local_message_ids.add(str(local_msg.id))
                    exact_match = True
                    break
            
            # If no exact ID match, try content-based matching
            if not exact_match:
                local_msg_id = find_local_message_match(msg_data, local_messages)
                if local_msg_id and local_msg_id not in used_local_message_ids:
                    agno_to_local_id_map[str(agno_msg_id)] = local_msg_id
                    used_local_message_ids.add(local_msg_id)
        
        # Add local messages from messages table to the list
        # These are messages that were created directly in our database (e.g., user messages from POST /message)
        # If we have AGNO messages, prefer those and skip duplicate local messages
        # But map local message IDs to AGNO message IDs for feedback lookup
        has_agno_messages = any(m.get("run_id") is not None for m in all_messages)
        local_to_agno_id_map: Dict[str, str] = {}  # local_message_id -> agno_message_id
        
        # IMPORTANT: If we have agno_sessions data, skip ALL local messages
        # The agno_sessions data is now the source of truth (input_content + content)
        # Local messages are only relevant if there's NO agno data, or for very recent messages
        # that haven't been synced to agno yet
        if has_agno_messages:
            active_stream_id = (
                active_streaming_info.get("stream_id")
                if active_streaming_info and isinstance(active_streaming_info, dict)
                else None
            )

            latest_local_user_msg = None
            if active_stream_id:
                local_user_messages = [m for m in local_messages if m.role == "user" and m.created_at]
                if local_user_messages:
                    latest_local_user_msg = max(local_user_messages, key=lambda m: m.created_at)

            # Find the latest timestamp in agno messages
            latest_agno_timestamp = max(
                (m.get("created_at") for m in all_messages if m.get("run_id") and m.get("created_at")),
                default=None
            )
            
            # Only add local messages that are NEWER than the latest agno message
            # This handles cases where user sent a new message that hasn't been processed by agno yet
            for local_msg in local_messages:
                local_msg_id = str(local_msg.id)
                
                # Skip if we already used this local message ID for an AGNO message
                if local_msg_id in used_local_message_ids:
                    continue
                
                # Skip if we already have this message from AGNO (by ID)
                if any(str(m.get("id")) == local_msg_id for m in all_messages):
                    continue

                local_tool_calls = local_msg.tool_calls if isinstance(local_msg.tool_calls, dict) else {}
                local_stream_id = local_tool_calls.get("stream_id") or local_tool_calls.get("streamId")
                is_active_stream_user_message = (
                    local_msg.role == "user"
                    and active_stream_id
                    and local_stream_id == active_stream_id
                )
                is_latest_active_user_fallback = (
                    local_msg.role == "user"
                    and active_stream_id
                    and latest_local_user_msg is not None
                    and str(local_msg.id) == str(latest_local_user_msg.id)
                )
                
                # Only include local messages that are newer than the latest agno message
                if latest_agno_timestamp and local_msg.created_at:
                    if local_msg.created_at <= latest_agno_timestamp:
                        # Keep current streaming user input even if timestamp is equal/older,
                        # because AGNO may not have persisted that latest turn yet.
                        if is_active_stream_user_message or is_latest_active_user_fallback:
                            logger.info(
                                f"Including active streaming user message from local DB "
                                f"message_id={local_msg_id}, stream_id={active_stream_id}"
                            )
                        else:
                            # This local message is older than or equal to agno data, skip it
                            # It's likely a duplicate or superseded by agno data
                            continue
                
                # This local message is newer than agno data, add it
                message_tool_calls = local_tool_calls.copy() if isinstance(local_tool_calls, dict) else {}
                if active_stream_id and local_msg.role == "user" and not (
                    message_tool_calls.get("stream_id") or message_tool_calls.get("streamId")
                ):
                    message_tool_calls["stream_id"] = active_stream_id

                all_messages.append({
                    "id": str(local_msg.public_id),  # Use public_id for API
                    "role": local_msg.role,
                    "content": _transform_cancellation_message(local_msg.content) if local_msg.content else "",  # Transform cancelled message
                    "created_at": local_msg.created_at,
                    "run_id": None,  # Local messages don't have run_id
                    "agent_id": str(local_msg.agent_id) if local_msg.agent_id else None,
                    "agent_name": None,  # Will be populated from agent lookup if needed
                    "message_data": {
                        "role": local_msg.role,
                        "content": _transform_cancellation_message(local_msg.content),  # Transform cancelled message
                        "tool_calls": message_tool_calls,
                    },
                    "local_message_id": local_msg_id,  # Internal ID for database lookup
                    "local_msg_obj": local_msg,  # Reference to the local message object for metadata access
                })
        else:
            # No agno messages - add all local messages (fallback to old behavior)
            for local_msg in local_messages:
                local_msg_id = str(local_msg.id)
                
                # Skip if we already used this local message ID for an AGNO message
                if local_msg_id in used_local_message_ids:
                    continue
                
                # Skip if we already have this message from AGNO (by ID)
                if any(str(m.get("id")) == local_msg_id for m in all_messages):
                    continue
                
                # Convert local message to same format as AGNO messages
                all_messages.append({
                    "id": str(local_msg.public_id),  # Use public_id for API
                    "role": local_msg.role,
                    "content": _transform_cancellation_message(local_msg.content) if local_msg.content else "",  # Transform cancelled message
                    "created_at": local_msg.created_at,
                    "run_id": None,  # Local messages don't have run_id
                    "agent_id": str(local_msg.agent_id) if local_msg.agent_id else None,
                    "agent_name": None,  # Will be populated from agent lookup if needed
                    "message_data": {
                        "role": local_msg.role,
                        "content": _transform_cancellation_message(local_msg.content),  # Transform cancelled message
                        "tool_calls": local_msg.tool_calls or {},
                    },
                    "local_message_id": local_msg_id,  # Internal ID for database lookup
                    "local_msg_obj": local_msg,  # Reference to the local message object for metadata access
                })
        
        # Deduplicate messages by message_id, keeping only the latest one based on timestamp
        # This prevents the same message from appearing multiple times in the response
        deduplication_map: Dict[str, Dict[str, Any]] = {}  # message_id -> message with latest timestamp
        
        for msg in all_messages:
            msg_id = str(msg.get("id")) if msg.get("id") else None
            if not msg_id:
                # Skip messages without ID (shouldn't happen, but handle gracefully)
                continue
            
            msg_timestamp = msg.get("created_at")
            
            # If we haven't seen this message_id, or this one has a later timestamp, keep it
            if msg_id not in deduplication_map:
                deduplication_map[msg_id] = msg
            else:
                existing_msg = deduplication_map[msg_id]
                existing_timestamp = existing_msg.get("created_at")
                
                # Compare timestamps - keep the one with the latest timestamp
                if msg_timestamp and existing_timestamp:
                    if msg_timestamp > existing_timestamp:
                        deduplication_map[msg_id] = msg
                elif msg_timestamp and not existing_timestamp:
                    # New message has timestamp, existing doesn't - keep new one
                    deduplication_map[msg_id] = msg
                # If new message has no timestamp but existing does, keep existing (already in map)
        
        # Replace all_messages with deduplicated list (by message_id)
        all_messages = list(deduplication_map.values())
        
        # Second pass: Deduplicate by content + role + timestamp + run_id
        # This handles cases where the same content appears with different message_ids
        # IMPORTANT: We MUST preserve run_id to ensure each run has its own user-assistant pair
        # Without this, regenerations would lose their messages
        content_deduplication_map: Dict[str, Dict[str, Any]] = {}  # content_key -> message with latest timestamp
        
        def get_content_key(msg: Dict[str, Any]) -> str:
            """Generate a key for content-based deduplication.

            Deduplicate messages by content + run_id to preserve conversation pairs.
            This ensures:
            - Each run maintains its own user-assistant pair
            - Messages from different runs are NOT deduplicated (preserves regenerations)
            - Same message with different IDs within same run is deduplicated
            - Preserves proper user-assistant-user-assistant conversation flow
            """
            role = msg.get("role", "")
            content = (msg.get("content") or "").strip()
            # Remove additional context for matching (it's added by AGNO)
            content_clean = content.split("\n\n<additional context>")[0].strip()
            # Use first 500 chars for matching (to handle very long messages)
            content_prefix = content_clean[:500] if content_clean else ""
            # Include run_id in the key to preserve messages from different runs
            run_id = str(msg.get("run_id")) if msg.get("run_id") else "no_run"

            # Deduplicate by content + run_id
            # This prevents duplicate messages within same run while preserving messages across different runs
            return f"{role}|{content_prefix}|{run_id}"
        
        for msg in all_messages:
            content_key = get_content_key(msg)
            msg_timestamp = msg.get("created_at")
            
            if content_key not in content_deduplication_map:
                # First time seeing this content, keep it
                content_deduplication_map[content_key] = msg
            else:
                existing_msg = content_deduplication_map[content_key]
                existing_timestamp = existing_msg.get("created_at")
                
                # Check if timestamps are the same or within 5 seconds (same message, different IDs)
                if msg_timestamp and existing_timestamp:
                    time_diff = abs((msg_timestamp - existing_timestamp).total_seconds())
                    if time_diff <= 5.0:  # Within 5 seconds, consider it a duplicate
                        # Keep the one with the latest timestamp
                        if msg_timestamp >= existing_timestamp:
                            content_deduplication_map[content_key] = msg
                        # If existing is newer, keep existing (already in map)
                    else:
                        # Timestamps are more than 5 seconds apart, treat as different messages
                        # Keep the latest one
                        if msg_timestamp > existing_timestamp:
                            content_deduplication_map[content_key] = msg
                elif msg_timestamp and not existing_timestamp:
                    # New message has timestamp, existing doesn't - keep new one
                    content_deduplication_map[content_key] = msg
                # If new message has no timestamp but existing does, keep existing (already in map)
        
        # Replace all_messages with content-deduplicated list
        all_messages = list(content_deduplication_map.values())

        # Sort by timestamp to ensure chronological order for regeneration detection
        all_messages.sort(key=lambda x: x["created_at"] if x["created_at"] else datetime.min)

        # Fourth pass: Remove duplicate assistant responses from regenerations
        # After deduplicating user messages, we may have multiple assistant responses
        # for the same user query (from different regeneration runs).
        # Keep only the LATEST assistant response in each consecutive sequence.
        # IMPORTANT: Skip this pass to preserve ALL assistant messages and maintain
        # conversation flow. The UI can handle multiple responses for comparison.
        # Users expect to see regenerations, not have them automatically hidden.

        # NOTE: This pass is disabled because it was removing assistant messages
        # that appeared before user messages (from regeneration flows where the
        # user message was deduplicated), breaking the user-assistant-user-assistant flow.
        # The correct behavior is to show all unique responses and let the user decide.

        # all_messages remains unchanged - no filtering of assistant responses

        # Third pass: For team runs, deduplicate partial responses that are subsets of combined responses
        # This handles cases where parallel delegations result in individual child responses AND a combined parent response
        # We want to keep only the combined response and remove the individual partial responses
        # Note: Local messages have run_id=None, synthesized messages have actual run_id, so we check ALL assistant messages
        assistant_messages = [m for m in all_messages if m.get("role") == "assistant"]
        if len(assistant_messages) > 1:
            # Find messages where content is a substring of another message's content
            messages_to_remove = set()
            for i, msg1 in enumerate(assistant_messages):
                content1 = (msg1.get("content") or "").strip()
                msg_id1 = str(msg1.get("id"))
                
                if not content1:
                    continue
                    
                for j, msg2 in enumerate(assistant_messages):
                    if i == j:
                        continue
                    
                    content2 = (msg2.get("content") or "").strip()
                    msg_id2 = str(msg2.get("id"))
                    
                    if not content2:
                        continue
                    
                    # Check if content1 is a substring of content2 (msg1 is partial, msg2 is combined)
                    # This detects when individual agent responses are contained within a combined team response
                    if content1 in content2 and len(content1) < len(content2):
                        # msg1 is a partial response contained in msg2, mark for removal
                        messages_to_remove.add(msg_id1)
            
            # Remove partial messages
            if messages_to_remove:
                all_messages = [m for m in all_messages if str(m.get("id")) not in messages_to_remove]
        
        # Sort messages by created_at (oldest first)
        all_messages.sort(key=lambda x: x["created_at"] if x["created_at"] else datetime.min)
        
        # Handle pagination
        total_messages = len(all_messages)
        
        # Apply before_message_id filter if provided
        if before_message_id:
            before_msg_str = str(before_message_id)
            try:
                before_idx = next(i for i, m in enumerate(all_messages) if str(m["id"]) == before_msg_str)
                all_messages = all_messages[:before_idx]
            except StopIteration:
                # Message not found, return empty
                all_messages = []
        
        # Apply limit
        has_more = len(all_messages) > limit

        if has_more:
                all_messages = all_messages[-limit:]  # Get last N messages
        
        # Transform to frontend format
        async def extract_attachments(metadata: Dict[str, Any], tool_calls_data: Optional[Dict[str, Any]] = None) -> List[Dict[str, Optional[str]]]:
            # First check metadata.attachments
            attachments = metadata.get("attachments") or metadata.get("attachment_list") or []
            
            # Also check tool_calls.attachments (from local Message table)
            if tool_calls_data and isinstance(tool_calls_data, dict):
                tool_calls_attachments = tool_calls_data.get("attachments") or []
                if tool_calls_attachments and isinstance(tool_calls_attachments, list):
                    # Merge with existing attachments, avoiding duplicates
                    existing_uuids = {str(item.get("attachment_id") or item.get("attachment_uuid") or "") for item in attachments if isinstance(item, dict)}
                    for att in tool_calls_attachments:
                        if isinstance(att, dict):
                            att_uuid = str(att.get("attachment_id") or att.get("attachment_uuid") or "")
                            if att_uuid and att_uuid not in existing_uuids:
                                attachments.append(att)
                                existing_uuids.add(att_uuid)
            
            formatted: List[Dict[str, Optional[str]]] = []
            if isinstance(attachments, list):
                for item in attachments:
                    if isinstance(item, dict):
                        attachment_uuid = str(item.get("attachment_id") or item.get("attachment_uuid") or item.get("id") or "")
                        filename = item.get("filename") or item.get("file_name") or item.get("name")
                        url = None
                        
                        # For frontend, use plain URL (not SAS URL)
                        # SAS URLs are only sent to external API, not to frontend
                        if attachment_uuid:
                            try:
                                att_result = await db.execute(
                                    select(Attachment).where(
                                        and_(
                                            Attachment.id == UUID(attachment_uuid),
                                            Attachment.is_active == True
                                        )
                                    )
                                )
                                attachment = att_result.scalar_one_or_none()
                                if attachment:
                                    # Use plain URL from database (not SAS URL)
                                    url = attachment.blob_url
                            except (ValueError, TypeError):
                                # Invalid UUID, skip
                                pass
                        
                        # Fallback to existing URL if we couldn't get from database
                        if not url:
                            url = item.get("blob_url") or item.get("download_url") or item.get("url") or None
                        
                        formatted.append(
                            {
                                "attachment_uuid": attachment_uuid,
                                "filename": filename,
                                "url": url,
                            }
                        )
                    elif isinstance(item, str):
                        # Handle string format attachments from agno_sessions (e.g., "attachment_uuid='...' download_url='...'")
                        # Try to parse the string format
                        try:
                            import re
                            uuid_match = re.search(r"attachment_uuid=['\"]([^'\"]+)['\"]", item)
                            filename_match = re.search(r"filename=['\"]([^'\"]+)['\"]", item)
                            download_url_match = re.search(r"download_url=['\"]([^'\"]+)['\"]", item)
                            blob_url_match = re.search(r"blob_url=['\"]([^'\"]+)['\"]", item)
                            if uuid_match:
                                attachment_uuid = uuid_match.group(1)
                                url = None
                                
                                # For frontend, use plain URL (not SAS URL)
                                # SAS URLs are only sent to external API, not to frontend
                                try:
                                    att_result = await db.execute(
                                        select(Attachment).where(
                                            and_(
                                                Attachment.id == UUID(attachment_uuid),
                                                Attachment.is_active == True
                                            )
                                        )
                                    )
                                    attachment = att_result.scalar_one_or_none()
                                    if attachment:
                                        # Use plain URL from database (not SAS URL)
                                        url = attachment.blob_url
                                except (ValueError, TypeError):
                                    # Invalid UUID, skip
                                    pass
                                
                                # Fallback to existing URL if we couldn't get from database
                                if not url:
                                    if download_url_match:
                                        url = download_url_match.group(1)
                                    elif blob_url_match:
                                        url = blob_url_match.group(1)
                                
                                formatted.append({
                                    "attachment_uuid": attachment_uuid,
                                    "filename": filename_match.group(1) if filename_match else None,
                                    "url": url,
                                })
                        except Exception:
                            # If parsing fails, skip this item
                            pass
            return [att for att in formatted if att.get("attachment_uuid")]

        async def extract_agents(metadata: Dict[str, Any]) -> List[Dict[str, Optional[str]]]:
            """Extract agents from metadata and look up latest agent names from agents table.
            
            The agent_id in the metadata is actually the agent's public_id (UUID).
            We need to look up the current agent name from the agents table to ensure
            we're showing the latest name, not the historical name from when the message was created.
            """
            agents = metadata.get("agents_involved") or metadata.get("agentsInvolved") or []
            formatted: List[Dict[str, Optional[str]]] = []
            
            # Collect all agent_ids for batch lookup
            agent_ids_to_lookup = []
            
            if isinstance(agents, list):
                for item in agents:
                    if isinstance(item, dict):
                        agent_id = item.get("agent_id") or item.get("agentId") or item.get("id")
                        if agent_id:
                            agent_ids_to_lookup.append(str(agent_id))
            
            # Batch lookup agent names from agents table
            agent_names_map: Dict[str, str] = {}  # agent_id/public_id -> current_name
            
            if agent_ids_to_lookup:
                try:
                    from uuid import UUID as UUIDType
                    # Try to look up agents by public_id (UUID)
                    valid_uuids = []
                    for aid in agent_ids_to_lookup:
                        try:
                            valid_uuids.append(UUIDType(aid))
                        except (ValueError, TypeError):
                            pass
                    
                    if valid_uuids:
                        agents_query = select(Agent).where(Agent.public_id.in_(valid_uuids))
                        agents_result = await db.execute(agents_query)
                        agents_list = agents_result.scalars().all()
                        
                        for agent in agents_list:
                            agent_names_map[str(agent.public_id)] = agent.name
                        
                        logger.info(
                            f"Looked up {len(agent_names_map)} agent names from agents table "
                            f"for agents_involved array"
                        )
                except Exception as e:
                    logger.warning(f"Failed to look up agent names: {e}")
            
            # Now format the agents list with current names
            if isinstance(agents, list):
                for item in agents:
                    if isinstance(item, dict):
                        agent_id = item.get("agent_id") or item.get("agentId") or item.get("id")
                        agent_id_str = str(agent_id) if agent_id else None
                        
                        # Look up current agent name from agents table
                        # If not found, fall back to the name from metadata (historical name)
                        current_agent_name = None
                        if agent_id_str and agent_id_str in agent_names_map:
                            current_agent_name = agent_names_map[agent_id_str]
                        
                        # Fall back to metadata name if lookup failed
                        if not current_agent_name:
                            current_agent_name = item.get("agent_name") or item.get("agentName") or item.get("name")
                        
                        formatted.append(
                            {
                                "agent_id": agent_id,
                                "agent_public_id": None,  # We don't have this in metadata
                                "agent_name": current_agent_name,  # Use current name from agents table
                                "role": item.get("role"),
                                "action": item.get("action") or item.get("activity"),
                            }
                        )
            return formatted

        def extract_custom_fields(metadata: Dict[str, Any]) -> Dict[str, Any]:
            custom_fields = metadata.get("custom_fields") or metadata.get("customFields")
            return custom_fields if isinstance(custom_fields, dict) else {}

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

        # Fetch feedback for all messages in one query
        # Normalize message IDs to ensure consistent matching (lowercase, strip whitespace)
        # Also include local message IDs that map to AGNO messages for feedback lookup
        message_ids = []
        message_id_map: Dict[str, str] = {}  # normalized_id -> original_id
        
        for msg_data in all_messages:
            msg_id = msg_data.get("id")
            if msg_id:
                original_id = str(msg_id)
                normalized_id = original_id.lower().strip()
                message_ids.append(normalized_id)
                message_id_map[normalized_id] = original_id
        
        # Also add local message IDs that map to AGNO messages (for feedback lookup)
        # Feedback might be stored against local message IDs, but we're showing AGNO message IDs
        for local_id, agno_id in local_to_agno_id_map.items():
            normalized_local_id = local_id.lower().strip()
            if normalized_local_id not in message_ids:
                message_ids.append(normalized_local_id)
                message_id_map[normalized_local_id] = local_id
        
        feedback_map: Dict[str, Dict[str, Any]] = {}
        
        if message_ids:
            # Query feedback with case-insensitive matching to handle any existing non-normalized feedback
            # Use func.lower() for case-insensitive comparison
            # Note: message_ids are already normalized (lowercase, stripped)
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
            
            # Create map: message_id -> feedback data
            # Map FeedbackRating to reaction format
            rating_to_reaction = {
                FeedbackRating.THUMBS_UP: "like",
                FeedbackRating.THUMBS_DOWN: "dislike",
                FeedbackRating.NEUTRAL: None,
            }
            
            for feedback in feedback_list:
                reaction = rating_to_reaction.get(feedback.rating, None)
                # Use normalized entity_id for lookup (case-insensitive)
                normalized_feedback_id = feedback.entity_id.lower().strip()
                feedback_data = {
                    "reaction": reaction,
                    "comment": feedback.comment,
                    "feedback_id": str(feedback.feedback_id),
                    "created_at": feedback.created_at.isoformat() if feedback.created_at else None,
                    "metadata": feedback.metadata_json or None,
                }
                
                # Store feedback under the normalized feedback ID
                feedback_map[normalized_feedback_id] = feedback_data
                
                # If this feedback is for a local message ID that maps to an AGNO message ID,
                # also store it under the AGNO message ID so we can find it when processing AGNO messages
                original_feedback_id = feedback.entity_id
                for local_id, agno_id in local_to_agno_id_map.items():
                    if str(local_id).lower().strip() == normalized_feedback_id:
                        # Map feedback to AGNO message ID
                        normalized_agno_id = str(agno_id).lower().strip()
                        feedback_map[normalized_agno_id] = feedback_data
                        break
            
            logger.info(
                f"Feedback lookup for session {session_id}: "
                f"total_message_ids={len(message_ids)}, "
                f"sample_ids={message_ids[:3]}, "
                f"found_feedback_count={len(feedback_list)}, "
                f"feedback_entity_ids={[f.entity_id for f in feedback_list][:3]}, "
                f"feedback_map_keys={list(feedback_map.keys())[:3]}, "
                f"user_id={str(current_user.id)}"
            )

        # Resolve agent information for all messages
        # Collect unique agent identifiers from messages
        # Skip agent_id for team runs (where agent_id is null and team_id/team_name exists)
        agent_identifiers = set()
        for msg_data in all_messages:
            agent_id = msg_data.get("agent_id")
            # Only collect agent_id if it exists (not null/empty)
            # Team runs with null agent_id will have agent_id=None, so they'll be skipped automatically
            if agent_id:
                agent_identifiers.add(str(agent_id))
        
        # Batch lookup agents by various identifiers (id, public_id, name, agent_id legacy field)
        agent_map: Dict[str, Dict[str, Any]] = {}  # agent_identifier -> {public_id, name}
        if agent_identifiers:
            from uuid import UUID as UUIDType
            
            # Try to resolve each agent identifier
            for agent_id_str in agent_identifiers:
                agent_info = None
                
                # Try as BigInteger ID
                try:
                    agent_id_int = int(agent_id_str)
                    result = await db.execute(
                        select(Agent).where(Agent.id == agent_id_int)
                    )
                    agent = result.scalar_one_or_none()
                    if agent:
                        agent_info = {
                            "id": str(agent.id),  # BigInteger ID
                            "public_id": str(agent.public_id),  # UUID
                            "name": agent.name
                        }
                except (ValueError, TypeError):
                    pass
                
                # Try as UUID (public_id)
                if not agent_info:
                    try:
                        agent_uuid = UUIDType(agent_id_str)
                        result = await db.execute(
                            select(Agent).where(Agent.public_id == agent_uuid)
                        )
                        agent = result.scalar_one_or_none()
                        if agent:
                            agent_info = {
                                "id": str(agent.id),  # BigInteger ID
                                "public_id": str(agent.public_id),  # UUID
                                "name": agent.name
                            }
                    except (ValueError, TypeError):
                        pass
                
                # Try as agent name
                if not agent_info:
                    result = await db.execute(
                        select(Agent).where(Agent.name == agent_id_str)
                    )
                    agent = result.scalar_one_or_none()
                    if agent:
                        agent_info = {
                            "id": str(agent.id),  # BigInteger ID
                            "public_id": str(agent.public_id),  # UUID
                            "name": agent.name
                        }
                
                # Try as legacy agent_id field
                if not agent_info:
                    result = await db.execute(
                        select(Agent).where(Agent.agent_id == agent_id_str)
                    )
                    agent = result.scalar_one_or_none()
                    if agent:
                        agent_info = {
                            "id": str(agent.id),  # BigInteger ID
                            "public_id": str(agent.public_id),  # UUID
                            "name": agent.name
                        }
                
                if agent_info:
                    agent_map[agent_id_str] = agent_info
                else:
                    logger.warning(f"Could not resolve agent identifier: {agent_id_str}")
        
        logger.info(
            f"Resolved {len(agent_map)} agents from {len(agent_identifiers)} unique identifiers"
        )
        
        # Fetch attachments for all messages in one query
        # For migrated sessions: attachments are linked to messages.id, but agno_sessions uses different message IDs
        # Strategy: Fetch ALL attachments for the session, then match by content/timestamp
        attachment_message_ids = []  # List of message UUIDs (both public_id and id)
        message_id_to_public_id: Dict[str, str] = {}  # message.id -> public_id for reverse lookup
        message_content_to_local_msg: Dict[str, Any] = {}  # content hash -> local message
        
        for local_msg in local_messages:
            # Add both id and public_id for attachment lookup
            attachment_message_ids.append(local_msg.id)
            attachment_message_ids.append(local_msg.public_id)
            message_id_to_public_id[str(local_msg.id)] = str(local_msg.public_id)
            
            # Build content hash for matching migrated messages
            content_key = f"{local_msg.role}:{(local_msg.content or '').strip()[:100]}"
            message_content_to_local_msg[content_key] = local_msg
        
        attachments_map: Dict[str, List[Dict[str, Any]]] = {}  # message_id -> list of attachments
        session_attachments_by_content: Dict[str, List[Dict[str, Any]]] = {}  # content hash -> attachments
        
        # Query attachments table - fetch by session to catch migrated attachments
        # First try by message_ids (for non-migrated messages)
        if attachment_message_ids:
            attachments_query = select(Attachment).where(
                and_(
                    Attachment.message_id.in_(attachment_message_ids),
                    Attachment.is_active == True,
                )
            )
            attachments_result = await db.execute(attachments_query)
            attachments_list = list(attachments_result.scalars().all())
        else:
            attachments_list = []
        
        # Also fetch all attachments for this session (to catch migrated data where message IDs don't match)
        if session.id:
            session_attachments_query = select(Attachment).join(Message).where(
                and_(
                    Message.session_id == session.id,
                    Attachment.is_active == True,
                )
            )
            session_attachments_result = await db.execute(session_attachments_query)
            session_attachments_list = list(session_attachments_result.scalars().all())
            
            # Merge with existing attachments (avoid duplicates)
            existing_ids = {str(att.id) for att in attachments_list}
            for att in session_attachments_list:
                if str(att.id) not in existing_ids:
                    attachments_list.append(att)
                    existing_ids.add(str(att.id))
        
        logger.info(
            f"Attachment lookup for session {session_id}: "
            f"message_ids_checked={len(attachment_message_ids)}, "
            f"attachments_found_total={len(attachments_list)}"
        )
        
        # Create map: message_id -> list of attachments
        # Also create content-based map for migrated messages where IDs don't match
        for attachment in attachments_list:
            # Get the public_id for this message (to match with what's shown in the API)
            message_id_str = str(attachment.message_id)
            public_id_str = message_id_to_public_id.get(message_id_str, message_id_str)
            
            # Use plain URL for frontend (not SAS URL)
            # SAS URLs are only sent to external API, not to frontend
            plain_url = attachment.blob_url
            
            attachment_data = {
                "attachment_uuid": str(attachment.id),
                "attachment_id": str(attachment.id),
                "filename": attachment.file_name,
                "file_name": attachment.file_name,
                "blob_url": plain_url,  # Plain URL for frontend
                "download_url": plain_url,  # Plain URL for frontend
                "url": plain_url,  # Plain URL for frontend
                "content_type": attachment.content_type,
                "file_size": attachment.file_size,
            }
            
            # Store under both the actual message_id and the public_id
            for key in [message_id_str, public_id_str]:
                if key not in attachments_map:
                    attachments_map[key] = []
                # Avoid duplicates
                if attachment_data not in attachments_map[key]:
                    attachments_map[key].append(attachment_data)
            
            # Also store by content hash for migrated messages (where agno_sessions message ID != messages.id)
            # Get the local message that has this attachment
            content_key = f"{message_id_str[:100]}"  # Use message_id as temporary key
            local_msg = message_content_to_local_msg.get(content_key)
            if not local_msg:
                # Search for local message by attachment.message_id
                for lm in local_messages:
                    if str(lm.id) == message_id_str or str(lm.public_id) == message_id_str:
                        local_msg = lm
                        break
            
            if local_msg:
                # Create content-based key for matching with agno_sessions messages
                content_hash = f"{local_msg.role}:{(local_msg.content or '').strip()[:100]}"
                if content_hash not in session_attachments_by_content:
                    session_attachments_by_content[content_hash] = []
                if attachment_data not in session_attachments_by_content[content_hash]:
                    session_attachments_by_content[content_hash].append(attachment_data)
        
        # Build a map of run_id -> stream_id from assistant messages
        # This allows us to use the same stream_id for user messages in the same run
        run_id_to_stream_id: Dict[str, str] = {}
        for msg_data in all_messages:
            if msg_data.get("role") == "assistant":
                # Try to get stream_id from message_data
                msg = msg_data.get("message_data", {})
                tool_calls = msg.get("tool_calls") if isinstance(msg, dict) else {}
                
                # If not found in message_data, try to get from local_messages
                if not isinstance(tool_calls, dict) or not tool_calls.get("stream_id"):
                    message_id_str = str(msg_data.get("id")) if msg_data.get("id") else None
                    if message_id_str:
                        for local_msg in local_messages:
                            if str(local_msg.id) == message_id_str:
                                if local_msg.tool_calls and isinstance(local_msg.tool_calls, dict):
                                    tool_calls = local_msg.tool_calls
                                    break
                
                if isinstance(tool_calls, dict) and tool_calls.get("stream_id"):
                    run_id = str(msg_data.get("run_id")) if msg_data.get("run_id") else None
                    if run_id:
                        run_id_to_stream_id[run_id] = tool_calls.get("stream_id")
        
        messages_payload: List[Dict[str, Any]] = []
        logger.info(f"Starting to build messages_payload from {len(all_messages)} messages")
        for idx, msg_data in enumerate(all_messages):
            # Handle both AGNO messages (with message_data) and local messages
            msg = msg_data.get("message_data", {})
            if not msg:
                # If no message_data, create from msg_data itself (for local messages)
                msg = {
                    "role": msg_data.get("role"),
                    "content": msg_data.get("content"),
                    "tool_calls": msg_data.get("message_data", {}).get("tool_calls") if isinstance(msg_data.get("message_data"), dict) else {}
                }
            
            # Get metadata from message_data - it should contain tool_calls, stream_id, etc.
            # For AGNO messages, metadata is typically in the message object itself
            # For local messages, stream_id is stored in tool_calls
            metadata = {}
            if isinstance(msg_data.get("message_data"), dict):
                # Use the full message_data as metadata (it contains tool_calls, stream_id, etc.)
                metadata = msg_data["message_data"]
            elif isinstance(msg, dict):
                # Fallback: use msg as metadata
                metadata = msg
            
            # Extract stream_id from tool_calls if present (for local messages stored in DB)
            # tool_calls can be in message_data or directly in msg
            # This is the PRIMARY source for stream_id - it's stored when messages are sent
            tool_calls = metadata.get("tool_calls") or msg.get("tool_calls") or {}
            
            # Normalize tool_calls: if it's a list, convert to dict or use empty dict
            # tool_calls from AGNO API might be a list, but we need it as a dict for stream_id lookup
            if isinstance(tool_calls, list):
                # If tool_calls is a list, we can't extract stream_id from it directly
                # Set to empty dict and rely on fallback mechanisms
                tool_calls = {}
            
            # Always check local messages for tool_calls (which contains attachments and stream_id)
            # This ensures attachments from local messages are always included, even if they weren't in AGNO message data
            message_id_str = str(msg_data.get("id")) if msg_data.get("id") else None
            local_msg_found = False
            if message_id_str:
                try:
                    # Try to find the message in local_messages list (already loaded) by ID
                    for local_msg in local_messages:
                        if str(local_msg.id) == message_id_str:
                            if local_msg.tool_calls and isinstance(local_msg.tool_calls, dict):
                                local_tool_calls = local_msg.tool_calls
                                # Merge local tool_calls with existing tool_calls
                                # This ensures we get attachments and stream_id from local messages
                                if not isinstance(tool_calls, dict):
                                    tool_calls = {}
                                # Preserve existing stream_id if present, but merge everything else
                                existing_stream_id = tool_calls.get("stream_id") or tool_calls.get("streamId")
                                tool_calls.update(local_tool_calls)
                                # If we had an existing stream_id and local doesn't have one, preserve it
                                if existing_stream_id and not (local_tool_calls.get("stream_id") or local_tool_calls.get("streamId")):
                                    tool_calls["stream_id"] = existing_stream_id
                                local_msg_found = True
                                break
                except Exception as e:
                    logger.warning(f"Error looking up local message for tool_calls: {e}")
            
            # If not found by ID, try to match by content and role (for cases where AGNO message ID differs from local ID)
            # This is critical for merging attachments from local messages that weren't matched during initial processing
            if not local_msg_found:
                try:
                    msg_role = msg_data.get("role", "")
                    msg_content = (msg_data.get("content", "") or "").strip()
                    # Remove additional context for matching (AGNO adds this)
                    msg_content_clean = msg_content.split("\n\n<additional context>")[0].strip()
                    msg_timestamp = msg_data.get("created_at")
                    
                    # Try to find a local message with matching content and role
                    # Check ALL local messages, even if they were matched before (they might have attachments we need)
                    for local_msg in local_messages:
                        # Only skip if this exact local message was matched to THIS specific AGNO message
                        # But we still want to check it if it has attachments we need
                        if local_msg.role != msg_role:
                            continue
                        
                        local_content = (local_msg.content or "").strip()
                        # Check if content matches (AGNO might have additional context)
                        if not (msg_content_clean and local_content):
                            continue
                        
                        # Match if content is exactly the same or one contains the other
                        content_matches = (
                            msg_content_clean == local_content or 
                            msg_content_clean.startswith(local_content) or
                            local_content.startswith(msg_content_clean)
                        )
                        
                        if not content_matches:
                            continue
                        
                        # Check if local message has attachments - if so, prioritize it
                        local_has_attachments = False
                        if local_msg.tool_calls and isinstance(local_msg.tool_calls, dict):
                            local_has_attachments = len(local_msg.tool_calls.get("attachments", [])) > 0
                        
                        # For exact content matches, use a very lenient time window (24 hours)
                        # This handles cases where timestamps might be in different timezones
                        # or there's processing delay
                        # If local message has attachments, be even more lenient
                        time_window = 86400  # 24 hours for all messages
                        if local_has_attachments and msg_content_clean == local_content:
                            time_window = 172800  # 48 hours if local has attachments and content matches exactly
                        
                        should_match = False
                        time_diff = None
                        
                        if msg_timestamp and local_msg.created_at:
                            # Ensure both are datetime objects
                            if isinstance(msg_timestamp, str):
                                try:
                                    msg_timestamp = datetime.fromisoformat(msg_timestamp.replace('Z', '+00:00'))
                                except:
                                    pass
                            
                            if isinstance(msg_timestamp, datetime) and isinstance(local_msg.created_at, datetime):
                                time_diff = abs((msg_timestamp - local_msg.created_at).total_seconds())
                                # If content matches exactly, be very lenient with timestamp
                                if msg_content_clean == local_content:
                                    should_match = time_diff < time_window
                                else:
                                    # For partial matches, use stricter time window (1 hour)
                                    should_match = time_diff < 3600
                        else:
                            # If timestamps are missing, match by content only (especially if local has attachments)
                            should_match = True
                        
                        # If local has attachments and content matches exactly, always match regardless of timestamp
                        # (within reason - still check if timestamps are way off)
                        if local_has_attachments and msg_content_clean == local_content:
                            if time_diff is None or time_diff < 172800:  # 48 hours max
                                should_match = True
                        
                        if should_match:
                            # Found a match by content - merge tool_calls (especially for attachments)
                            if local_msg.tool_calls and isinstance(local_msg.tool_calls, dict):
                                local_tool_calls = local_msg.tool_calls
                                attachments_count = len(local_tool_calls.get("attachments", []))
                                
                                # Always merge tool_calls to get attachments and other metadata
                                if not isinstance(tool_calls, dict):
                                    tool_calls = {}
                                existing_stream_id = tool_calls.get("stream_id") or tool_calls.get("streamId")
                                tool_calls.update(local_tool_calls)
                                if existing_stream_id and not (local_tool_calls.get("stream_id") or local_tool_calls.get("streamId")):
                                    tool_calls["stream_id"] = existing_stream_id
                                
                                time_diff_str = f", time_diff={time_diff:.1f}s" if time_diff is not None else ""
                                logger.info(
                                    f"Merged tool_calls from local message (matched by content) {local_msg.id} "
                                    f"into message {message_id_str}, attachments_count={attachments_count}{time_diff_str}"
                                )
                                local_msg_found = True
                                break
                except Exception as e:
                    logger.warning(f"Error looking up local message by content for tool_calls: {e}")
            
            # Final fallback: try to match by stream_id if we still haven't found attachments
            # This helps when content matching fails but messages share the same stream_id
            if not local_msg_found:
                try:
                    # Get stream_id from metadata or msg_data
                    current_stream_id = metadata.get("stream_id") or metadata.get("streamId") or msg_data.get("stream_id")
                    if current_stream_id:
                        msg_role = msg_data.get("role", "")
                        for local_msg in local_messages:
                            if local_msg.role != msg_role:
                                continue
                            
                            # Check if local message has the same stream_id and has attachments
                            if local_msg.tool_calls and isinstance(local_msg.tool_calls, dict):
                                local_stream_id = local_msg.tool_calls.get("stream_id") or local_msg.tool_calls.get("streamId")
                                if local_stream_id == current_stream_id:
                                    local_tool_calls = local_msg.tool_calls
                                    attachments_count = len(local_tool_calls.get("attachments", []))
                                    
                                    if attachments_count > 0:
                                        # Merge tool_calls to get attachments
                                        if not isinstance(tool_calls, dict):
                                            tool_calls = {}
                                        existing_stream_id = tool_calls.get("stream_id") or tool_calls.get("streamId")
                                        tool_calls.update(local_tool_calls)
                                        if existing_stream_id and not (local_tool_calls.get("stream_id") or local_tool_calls.get("streamId")):
                                            tool_calls["stream_id"] = existing_stream_id
                                        
                                        logger.info(
                                            f"Merged tool_calls from local message (matched by stream_id) {local_msg.id} "
                                            f"into message {message_id_str}, attachments_count={attachments_count}, "
                                            f"stream_id={current_stream_id}"
                                        )
                                        local_msg_found = True
                                        break
                except Exception as e:
                    logger.warning(f"Error looking up local message by stream_id for tool_calls: {e}")
            
            if isinstance(tool_calls, dict):
                # Update metadata with merged tool_calls so extract_attachments can find attachments
                metadata["tool_calls"] = tool_calls
                # Prioritize stream_id from tool_calls over ALL other sources
                # This is the source of truth since it's stored when the message is sent
                if tool_calls.get("stream_id"):
                    metadata["stream_id"] = tool_calls.get("stream_id")
                if tool_calls.get("streamId"):
                    metadata["streamId"] = tool_calls.get("streamId")
            
            # Only use fallback sources if stream_id is NOT found in tool_calls
            # (tool_calls is the source of truth for stream_id)
            if not metadata.get("stream_id") and not metadata.get("streamId"):
                # First, try to get stream_id from assistant message in the same run
                # User and assistant messages in the same conversation should share the same stream_id
                run_id = str(msg_data.get("run_id")) if msg_data.get("run_id") else None
                if run_id and run_id in run_id_to_stream_id:
                    metadata["stream_id"] = run_id_to_stream_id[run_id]
                elif msg_data.get("stream_id"):
                    metadata["stream_id"] = msg_data.get("stream_id")
                elif msg_data.get("streamId"):
                    metadata["streamId"] = msg_data.get("streamId")
                # Also check if stream_id is in the run (for AGNO messages, stream_id might be at run level)
                elif msg_data.get("run_id"):
                    # For AGNO messages, stream_id might be the session_id or run_id
                    # Use session session_id as stream_id if available (fallback only)
                    stream_id_from_session = session.session_id if session else None
                    if stream_id_from_session:
                        metadata["stream_id"] = stream_id_from_session
            
            # Format content (clean whitespace while preserving markdown)
            # IMPORTANT: We preserve the FULL user/assistant content - only the 
            # <additional context> metadata section is removed
            raw_content = msg_data.get("content", "") or msg.get("content", "")
            # Transform cancellation messages from AGNO API to user-friendly format
            raw_content = _transform_cancellation_message(raw_content)
            formatted_content = format_content(raw_content)  # Full content preserved
            
            # Get feedback for this message
            # Normalize message ID for lookup (lowercase, strip whitespace)
            message_id_str = str(msg_data["id"]) if msg_data.get("id") else None
            normalized_msg_id = message_id_str.lower().strip() if message_id_str else None
            feedback_data = feedback_map.get(normalized_msg_id) if normalized_msg_id else None
            
            # Debug logging for feedback lookup
            if message_id_str:
                logger.info(
                    f"Processing message: id={message_id_str}, normalized={normalized_msg_id}, "
                    f"feedback_found={feedback_data is not None}"
                )
                if feedback_data:
                    logger.info(
                        f"✓ Feedback found for {message_id_str}: "
                        f"reaction={feedback_data.get('reaction')}, comment={feedback_data.get('comment')}"
                    )
                else:
                    logger.warning(
                        f"✗ No feedback found for {message_id_str} (normalized: {normalized_msg_id}). "
                        f"Available feedback keys: {list(feedback_map.keys())[:5]}, "
                        f"In message_ids list: {normalized_msg_id in message_ids if normalized_msg_id else False}"
                    )
                
            # Ensure we have a message_id (should always be set now due to deterministic generation)
            if not message_id_str:
                # Fallback: generate deterministic ID if somehow missing
                message_id_str = _generate_deterministic_message_id(
                    session_id=session.session_id,
                    run_id=msg_data.get("run_id"),
                    role=msg_data["role"],
                    content=raw_content,
                    created_at=msg_data.get("created_at")
                )
                logger.warning(
                    f"Message ID was None, generated deterministic ID: {message_id_str}"
                )
            
            # Always include user and assistant messages, even if content is empty
            # (system messages are already filtered above based on include_system flag)
            msg_role = msg_data.get("role", "")
            if msg_role not in ["system", "user", "assistant", "tool"]:
                logger.warning(f"Skipping message {idx} with unknown role: {msg_role}")
                continue
            
            # Resolve agent information
            agent_id_raw = msg_data.get("agent_id")
            resolved_agent_id = None  # BigInteger ID
            resolved_agent_public_id = None  # UUID public_id
            resolved_agent_name = None  # Will be looked up from agents table (not from AGNO)
            
            if agent_id_raw:
                agent_id_str = str(agent_id_raw)
                agent_info = agent_map.get(agent_id_str)
                if agent_info:
                    resolved_agent_id = agent_info.get("id")  # BigInteger ID
                    resolved_agent_public_id = agent_info.get("public_id")  # UUID
                    # ALWAYS use the current name from agents table, not from AGNO
                    resolved_agent_name = agent_info.get("name")
                else:
                    # Fallback: try to resolve by the agent_id_str itself
                    # It might already be a public_id or id
                    try:
                        # Try as BigInteger
                        agent_id_int = int(agent_id_str)
                        result = await db.execute(
                            select(Agent).where(Agent.id == agent_id_int)
                        )
                        agent = result.scalar_one_or_none()
                        if agent:
                            resolved_agent_id = str(agent.id)
                            resolved_agent_public_id = str(agent.public_id)
                            # Use current name from database
                            resolved_agent_name = agent.name
                    except (ValueError, TypeError):
                        try:
                            # Try as UUID
                            from uuid import UUID as UUIDType
                            agent_uuid = UUIDType(agent_id_str)
                            result = await db.execute(
                                select(Agent).where(Agent.public_id == agent_uuid)
                            )
                            agent = result.scalar_one_or_none()
                            if agent:
                                resolved_agent_id = str(agent.id)
                                resolved_agent_public_id = str(agent.public_id)
                                # Use current name from database
                                resolved_agent_name = agent.name
                        except (ValueError, TypeError):
                            # If we can't resolve, use original as fallback
                            resolved_agent_id = agent_id_str
                            # Fallback to AGNO name only if we couldn't look up the agent
                            resolved_agent_name = msg_data.get("agent_name")
            
            # Only include feedback for assistant messages, not user messages
            include_feedback = msg_role == "assistant"
            
            # Merge attachments from metadata/tool_calls and from attachments table
            metadata_attachments = await extract_attachments(metadata, tool_calls if isinstance(tool_calls, dict) else None)
            db_attachments = attachments_map.get(message_id_str, [])
            
            # Also check content-based map for migrated messages where message IDs don't match
            if not db_attachments and formatted_content:
                content_hash = f"{msg_role}:{formatted_content.strip()[:100]}"
                content_based_attachments = session_attachments_by_content.get(content_hash, [])
                if content_based_attachments:
                    logger.info(f"Found {len(content_based_attachments)} attachments for message via content matching: {content_hash[:50]}...")
                    db_attachments = content_based_attachments
            
            # Merge attachments, avoiding duplicates
            all_attachments = list(metadata_attachments)  # Start with metadata attachments
            existing_uuids = {att.get("attachment_uuid") for att in all_attachments if att.get("attachment_uuid")}
            for db_att in db_attachments:
                att_uuid = db_att.get("attachment_uuid")
                if att_uuid and att_uuid not in existing_uuids:
                    all_attachments.append(db_att)
                    existing_uuids.add(att_uuid)
            
            # Get message_metadata from local database if available
            local_message_metadata = None
            # First check if this message has a direct reference to local_msg_obj (for pure local messages)
            local_msg_obj = msg_data.get("local_msg_obj")
            if local_msg_obj and hasattr(local_msg_obj, "message_metadata"):
                if local_msg_obj.message_metadata and isinstance(local_msg_obj.message_metadata, dict):
                    local_message_metadata = local_msg_obj.message_metadata
            else:
                # Otherwise, try to find it via agno_to_local_id_map (for AGNO messages mapped to local)
                local_message_id = agno_to_local_id_map.get(message_id_str)
                if local_message_id:
                    for local_msg in local_messages:
                        if str(local_msg.id) == local_message_id:
                            # Get message_metadata from local database
                            if local_msg.message_metadata and isinstance(local_msg.message_metadata, dict):
                                local_message_metadata = local_msg.message_metadata
                            break
            
            # Build base message payload
            message_payload = {
                        "message_id": message_id_str,
                "type": msg_role,
                        "content": formatted_content,
                "attachments": all_attachments,
                "custom_fields": extract_custom_fields(metadata),
                "agents_involved": await extract_agents(metadata),
                "timestamp": msg_data["created_at"].isoformat() if msg_data.get("created_at") else None,
                "stream_id": metadata.get("stream_id") or metadata.get("streamId"),
                        # Include run information for grouping multiple conversations
                        "run_id": str(msg_data.get("run_id")) if msg_data.get("run_id") else None,
                "agent_id": resolved_agent_id,  # BigInteger ID
                "agent_public_id": resolved_agent_public_id,  # UUID public_id
                "agent_name": resolved_agent_name,  # Agent name
                # Include team information for team runs (when agent_id is null)
                "team_id": msg_data.get("team_id"),  # Include team_id for team runs
                "team_name": msg_data.get("team_name"),  # Include team_name for team runs
                # Include local_message_id to map agno messages to local database messages
                "local_message_id": agno_to_local_id_map.get(message_id_str),  # Local database message ID
            }
            
            # Include message_metadata for user messages (mode, custom fields, etc.)
            if msg_role == "user" and local_message_metadata:
                message_payload["message_metadata"] = local_message_metadata
            
            # Include assistant_response for assistant messages if it exists in message_metadata
            if msg_role == "assistant" and local_message_metadata and "assistant_response" in local_message_metadata:
                message_payload["assistant_response"] = local_message_metadata["assistant_response"]
            
            # Only include feedback field for assistant messages
            if include_feedback:
                message_payload["feedback"] = feedback_data if feedback_data else None
            
            messages_payload.append(message_payload)
        
        logger.info(f"Built messages_payload with {len(messages_payload)} messages (from {len(all_messages)} total)")

        # Check if any runs have empty events and fetch from AGNO API if needed
        runs_without_events = [
            run for run in runs_summary 
            if not run.get("events") or len(run.get("events", [])) == 0
        ]
        
        if runs_without_events:
            logger.info(
                f"Found {len(runs_without_events)} runs without events. "
                f"Attempting to fetch events from AGNO API for session_id={agno_session_id}"
            )
            try:
                from aldar_middleware.orchestration.agno import agno_service
                
                # Extract authorization header from request to forward to AGNO API
                auth_header = None
                if http_request:
                    auth_header = http_request.headers.get("authorization")
                
                # Fetch session runs from AGNO API
                agno_runs_response = await agno_service.get_session_runs(
                    agno_session_id, 
                    user_id=str(current_user.id),
                    authorization_header=auth_header  # Forward user's auth token
                )
                
                # Extract runs from response (handle different response structures)
                agno_runs = []
                if isinstance(agno_runs_response, dict):
                    # Try different possible keys for runs data
                    agno_runs = (
                        agno_runs_response.get("runs") or 
                        agno_runs_response.get("data", {}).get("runs") or
                        agno_runs_response.get("data") or
                        []
                    )
                    if not isinstance(agno_runs, list):
                        agno_runs = [agno_runs] if agno_runs else []
                
                logger.info(
                    f"AGNO API returned {len(agno_runs)} runs. "
                    f"Response keys: {list(agno_runs_response.keys()) if isinstance(agno_runs_response, dict) else 'N/A'}"
                )
                
                # Log full response structure for debugging (first 1000 chars to avoid huge logs)
                response_str = str(agno_runs_response)[:1000]
                logger.info(
                    f"AGNO API response structure (first 1000 chars): {response_str}"
                )
                
                # Create a map of run_id -> events from AGNO response
                agno_events_map = {}
                for agno_run in agno_runs:
                    if isinstance(agno_run, dict):
                        run_id = agno_run.get("run_id")
                        # Check for events in various possible locations
                        events = (
                            agno_run.get("events") or
                            agno_run.get("run_events") or
                            agno_run.get("event_history") or
                            []
                        )
                        if run_id:
                            if events:
                                agno_events_map[str(run_id)] = events
                                logger.info(
                                    f"✓ Found {len(events)} events for run_id={run_id} from AGNO API"
                                )
                            else:
                                logger.warning(
                                    f"✗ No events found for run_id={run_id} in AGNO API response. "
                                    f"Run keys: {list(agno_run.keys())}"
                                )
                                # Log a sample of the run structure for debugging
                                run_sample = {k: str(v)[:100] if not isinstance(v, (dict, list)) else type(v).__name__ 
                                             for k, v in list(agno_run.items())[:5]}
                
                # Update runs_summary with events from AGNO API
                for run_summary in runs_summary:
                    run_id = run_summary.get("run_id")
                    if run_id and not run_summary.get("events"):
                        events_from_agno = agno_events_map.get(str(run_id))
                        if events_from_agno:
                            run_summary["events"] = events_from_agno
                            logger.info(
                                f"Populated events for run_id={run_id} from AGNO API: "
                                f"{len(events_from_agno)} events"
                            )
                
                # Also check for member_responses in AGNO response and update runs_summary
                for agno_run in agno_runs:
                    if isinstance(agno_run, dict):
                        run_id = agno_run.get("run_id")
                        member_responses = agno_run.get("member_responses") or agno_run.get("memberResponses") or []
                        if run_id and member_responses:
                            # Find corresponding run in runs_summary and update it
                            for run_summary in runs_summary:
                                if str(run_summary.get("run_id")) == str(run_id):
                                    run_summary["member_responses"] = member_responses
                                    logger.info(
                                        f"Populated member_responses for run_id={run_id} from AGNO API: "
                                        f"{len(member_responses)} members"
                                    )
                                    break
                
                logger.info(
                    f"Successfully fetched events from AGNO API. "
                    f"Updated {len([r for r in runs_summary if r.get('events')])} runs with events."
                )
            except Exception as e:
                error_str = str(e)
                # Check if it's a 404 (session not found in AGNO API)
                if "404" in error_str or "not found" in error_str.lower():
                    logger.info(
                        f"Session {agno_session_id} not found in AGNO API (this is expected for agent-specific runs). "
                        f"Events may not be available from AGNO API for this session type."
                    )
                else:
                    logger.warning(
                        f"Failed to fetch events from AGNO API for session_id={agno_session_id}: {error_str}. "
                        f"Continuing without events."
                    )
                # Continue without events - don't fail the request
        
        # Update agent names in events to show current names from agents table
        # This ensures events always show the latest agent name, not historical names
        for run_summary in runs_summary:
            events = run_summary.get("events", [])
            if isinstance(events, list):
                for event in events:
                    if isinstance(event, dict):
                        event_agent_id = event.get("agent_id")
                        if event_agent_id:
                            agent_id_str = str(event_agent_id)
                            current_name = runs_agent_names_map.get(agent_id_str)
                            if current_name:
                                # Update event with current agent name from agents table
                                event["agent_name"] = current_name
            
            # Also update agent names in member_responses
            member_responses = run_summary.get("member_responses", [])
            if isinstance(member_responses, list):
                for member in member_responses:
                    if isinstance(member, dict):
                        member_agent_id = member.get("agent_id") or member.get("agentId")
                        if member_agent_id:
                            agent_id_str = str(member_agent_id)
                            current_name = runs_agent_names_map.get(agent_id_str)
                            if current_name:
                                # Update member with current agent name from agents table
                                member["agent_name"] = current_name
                                if "agentName" in member:
                                    member["agentName"] = current_name

        # Build a map of run_id -> list of agents involved in that run
        # This extracts agents from runs_summary (member_responses and events)
        # Do this AFTER fetching events from AGNO API so we have complete data
        run_id_to_agents: Dict[str, List[Dict[str, Optional[str]]]] = {}
        
        async def extract_agents_from_run(run_data: Dict[str, Any]) -> List[Dict[str, Optional[str]]]:
            """Extract all unique agents from a run's member_responses and events."""
            agents_map: Dict[str, Dict[str, Optional[str]]] = {}  # agent_id -> agent info
            
            # First, include the team/team_name as the Super Agent if it's a team run
            team_id = run_data.get("team_id")
            team_name = run_data.get("team_name")
            if team_id or team_name:
                # Use team_id as key, or team_name if no team_id
                team_key = str(team_id) if team_id else f"team_{team_name}" if team_name else None
                if team_key:
                    agents_map[team_key] = {
                        "agent_id": str(team_id) if team_id else None,
                        "agent_public_id": None,
                        "agent_name": team_name or "Team",
                        "role": "super_agent",  # Mark as super agent/team
                    }
            
            # Extract from member_responses
            member_responses = run_data.get("member_responses") or run_data.get("memberResponses") or []
            if isinstance(member_responses, list):
                for member in member_responses:
                    if isinstance(member, dict):
                        agent_id = member.get("agent_id") or member.get("agentId")
                        agent_public_id = member.get("agent_public_id") or member.get("agentPublicId")
                        agent_name = member.get("agent_name") or member.get("agentName")
                        
                        # Use agent_id or agent_public_id as key
                        agent_key = str(agent_id) if agent_id else str(agent_public_id) if agent_public_id else None
                        if agent_key and (agent_id or agent_public_id or agent_name):
                            if agent_key not in agents_map:
                                agents_map[agent_key] = {
                                    "agent_id": str(agent_id) if agent_id else None,
                                    "agent_public_id": str(agent_public_id) if agent_public_id else None,
                                    "agent_name": agent_name,
                                }
            
            # Extract from events (look for events with agent_id and agent_name)
            events = run_data.get("events") or []
            if isinstance(events, list):
                for event in events:
                    if isinstance(event, dict):
                        agent_id = event.get("agent_id") or event.get("agentId")
                        agent_public_id = event.get("agent_public_id") or event.get("agentPublicId")
                        agent_name = event.get("agent_name") or event.get("agentName")
                        
                        # Use agent_id or agent_public_id as key
                        agent_key = str(agent_id) if agent_id else str(agent_public_id) if agent_public_id else None
                        if agent_key and (agent_id or agent_public_id or agent_name):
                            if agent_key not in agents_map:
                                agents_map[agent_key] = {
                                    "agent_id": str(agent_id) if agent_id else None,
                                    "agent_public_id": str(agent_public_id) if agent_public_id else None,
                                    "agent_name": agent_name,
                                }
                            # Update agent_name if we have a better one
                            elif agent_name and not agents_map[agent_key].get("agent_name"):
                                agents_map[agent_key]["agent_name"] = agent_name
            
            # Also include the main agent from the run if present (for non-team runs)
            run_agent_id = run_data.get("agent_id")
            run_agent_name = run_data.get("agent_name")
            if (run_agent_id or run_agent_name) and not team_id:
                # Only add main agent if it's not a team run (team runs use team_name instead)
                agent_key = str(run_agent_id) if run_agent_id else "main"
                if agent_key not in agents_map:
                    agents_map[agent_key] = {
                        "agent_id": str(run_agent_id) if run_agent_id else None,
                        "agent_public_id": None,
                        "agent_name": run_agent_name,
                    }
                elif run_agent_name and not agents_map[agent_key].get("agent_name"):
                    agents_map[agent_key]["agent_name"] = run_agent_name
            
            # Normalize agent_ids: convert string-based IDs to UUIDs
            # Collect all string agent_ids that need lookup (e.g., "mcp-agent-New Weather MCP")
            string_agent_ids_to_lookup = []
            for agent_info in agents_map.values():
                agent_id = agent_info.get("agent_id")
                agent_public_id = agent_info.get("agent_public_id")
                
                # If we don't have a public_id and agent_id looks like a string (not UUID), look it up
                if not agent_public_id and agent_id:
                    try:
                        # Try to parse as UUID - if it fails, it's a string ID
                        UUID(agent_id)
                    except (ValueError, AttributeError):
                        # It's a string ID like "mcp-agent-New Weather MCP"
                        string_agent_ids_to_lookup.append(agent_id)
            
            # Look up UUIDs for string agent_ids from the Agent table
            if string_agent_ids_to_lookup:
                try:
                    # First try direct lookup by agent_id
                    agent_lookup_query = select(Agent.agent_id, Agent.public_id, Agent.name).where(
                        Agent.agent_id.in_(string_agent_ids_to_lookup)
                    )
                    agent_lookup_result = await db.execute(agent_lookup_query)
                    agent_lookup_map = {
                        str(row[0]): {"public_id": str(row[1]), "name": row[2]}
                        for row in agent_lookup_result.all()
                    }
                    
                    # For IDs not found, try extracting agent name and lookup by name
                    # e.g., "mcp-agent-New Weather MCP" -> "New Weather MCP"
                    not_found_ids = [aid for aid in string_agent_ids_to_lookup if aid not in agent_lookup_map]
                    if not_found_ids:
                        agent_names_to_lookup = []
                        id_to_name_map = {}
                        for string_id in not_found_ids:
                            # Skip team IDs (they don't correspond to agents table)
                            if string_id.startswith("user-team-"):
                                continue
                            # Extract name from "mcp-agent-{AgentName}" format
                            if string_id.startswith("mcp-agent-"):
                                agent_name = string_id.replace("mcp-agent-", "", 1)
                                agent_names_to_lookup.append(agent_name)
                                id_to_name_map[agent_name] = string_id
                        
                        if agent_names_to_lookup:
                            name_lookup_query = select(Agent.name, Agent.public_id).where(
                                Agent.name.in_(agent_names_to_lookup)
                            )
                            name_lookup_result = await db.execute(name_lookup_query)
                            for row in name_lookup_result.all():
                                agent_name = row[0]
                                public_id = str(row[1])
                                # Map the original string ID to the UUID
                                original_string_id = id_to_name_map.get(agent_name)
                                if original_string_id:
                                    agent_lookup_map[original_string_id] = {
                                        "public_id": public_id,
                                        "name": agent_name
                                    }
                            
                    
                    # Update agents_map with UUIDs
                    for agent_info in agents_map.values():
                        agent_id = agent_info.get("agent_id")
                        if agent_id and agent_id in agent_lookup_map:
                            lookup_data = agent_lookup_map[agent_id]
                            # Replace agent_id with the UUID (public_id)
                            agent_info["agent_id"] = lookup_data["public_id"]
                            agent_info["agent_public_id"] = lookup_data["public_id"]
                            # Update agent_name if not already set
                            if not agent_info.get("agent_name") and lookup_data["name"]:
                                agent_info["agent_name"] = lookup_data["name"]
                except Exception as e:
                    logger.warning(f"Failed to normalize agent_ids: {str(e)}")
                    # Continue with original agent_ids if lookup fails
            
            return list(agents_map.values())
        
        # Build the run_id -> agents map from runs_summary
        for run_data in runs_summary:
            run_id = run_data.get("run_id")
            if run_id:
                agents_list = await extract_agents_from_run(run_data)
                if agents_list:
                    run_id_to_agents[str(run_id)] = agents_list
        
        # Update messages_payload with agents_involved from runs_summary
        for message_payload in messages_payload:
            run_id = message_payload.get("run_id")
            
            # Get existing agents from metadata
            existing_agents = message_payload.get("agents_involved") or []
            if not isinstance(existing_agents, list):
                existing_agents = []
            
            # Merge agents, avoiding duplicates
            # Use agent_id or agent_public_id as unique identifier
            merged_agents_map: Dict[str, Dict[str, Optional[str]]] = {}
            
            # Add existing agents first
            for agent in existing_agents:
                if isinstance(agent, dict):
                    agent_key = str(agent.get("agent_id") or agent.get("agent_public_id") or "")
                    if agent_key:
                        merged_agents_map[agent_key] = agent
            
            # Add agents from run if run_id exists in map
            if run_id and run_id in run_id_to_agents:
                run_agents = run_id_to_agents[run_id]
                # Add run agents (they take precedence if there's a conflict)
                for agent in run_agents:
                    if isinstance(agent, dict):
                        agent_key = str(agent.get("agent_id") or agent.get("agent_public_id") or "")
                        if agent_key:
                            merged_agents_map[agent_key] = agent
                        elif agent.get("agent_name"):
                            # If no ID, use name as key (less ideal but better than nothing)
                            merged_agents_map[agent.get("agent_name")] = agent
            
            # Also add team/team_name as Super Agent if present in message (even if not in runs_summary)
            team_id = message_payload.get("team_id")
            team_name = message_payload.get("team_name")
            if team_id or team_name:
                team_key = str(team_id) if team_id else f"team_{team_name}" if team_name else None
                if team_key and team_key not in merged_agents_map:
                    merged_agents_map[team_key] = {
                        "agent_id": str(team_id) if team_id else None,
                        "agent_public_id": None,
                        "agent_name": team_name or "Team",
                        "role": "super_agent",  # Mark as super agent/team
                    }
            
            # Update message payload with merged agents
            message_payload["agents_involved"] = list(merged_agents_map.values())

        # Map active stream_id to latest user message when needed
        _map_active_stream_to_user_message(messages_payload)

        oldest_id = messages_payload[0]["message_id"] if messages_payload else None
        newest_id = messages_payload[-1]["message_id"] if messages_payload else None

        logger.info(
            f"Returning {len(messages_payload)} messages from agno_sessions "
            f"for session_id={session_id}, has_more={has_more}"
        )

        return {
            "success": True,
            "session_id": session.session_id,
            "session_name": session.session_name,
            "messages": messages_payload,
            "has_more": has_more,
            "oldest_message_id": oldest_id,
            "newest_message_id": newest_id,
            "correlation_id": correlation_id,
            # Include runs summary for frontend to group conversations
            "runs_summary": runs_summary if runs_summary else None,
            "total_runs": len(runs_summary),
        }
            
    except Exception as e:
        logger.error(f"Error fetching messages from agno_sessions: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        # Fallback to empty response on error
        
        return {
            "success": True,
            "session_id": session.session_id if session else None,
            "session_name": session.session_name if session else None,
            "messages": [],
            "has_more": False,
            "oldest_message_id": None,
            "newest_message_id": None,
            "correlation_id": correlation_id,
        }


class MessageFeedbackRequest(BaseModel):
    """Request model for message feedback (like/dislike)."""
    
    reaction: Optional[Literal["like", "dislike"]] = Field(
        None, 
        description="Reaction: 'like' or 'dislike'. Set to null to remove feedback."
    )
    comment: Optional[str] = Field(None, max_length=5000, description="Optional feedback comment")
    session_id: Optional[str] = Field(None, description="Optional session identifier")

class AgentFeedbackMetadata(BaseModel):
    """Agent metadata to store with feedback."""
    agent_id: Optional[str] = Field(None, description="Internal agent numeric ID (string)")
    agent_name: Optional[str] = Field(None, description="Agent display name")
    agent_public_id: Optional[str] = Field(None, description="Agent public UUID")
    agent_thumbnail: Optional[str] = Field(None, description="Agent thumbnail/icon URL")

class ExtendedMessageFeedbackRequest(MessageFeedbackRequest):
    """Extended request that can include agent metadata."""
    agent: Optional[AgentFeedbackMetadata] = Field(None, description="Optional agent metadata to store in feedback")


async def _build_agent_feedback_metadata(
    db: AsyncSession,
    request: "ExtendedMessageFeedbackRequest",
) -> Optional[Dict[str, Any]]:
    """Construct agent metadata object for feedback from request and session lookup."""
    try:
        metadata_agent: Dict[str, Any] = {}

        agent_payload = getattr(request, "agent", None)
        if agent_payload:
            if agent_payload.agent_id is not None:
                metadata_agent["agent_id"] = str(agent_payload.agent_id)
            if agent_payload.agent_name is not None:
                metadata_agent["agent_name"] = agent_payload.agent_name
            if agent_payload.agent_public_id is not None:
                metadata_agent["agent_public_id"] = str(agent_payload.agent_public_id)
            if agent_payload.agent_thumbnail is not None:
                metadata_agent["agent_thumbnail"] = agent_payload.agent_thumbnail

        # If any field missing, try to resolve via session_id
        if request.session_id:
            try:
                session_uuid = UUID(str(request.session_id))
                session_result = await db.execute(select(Session).where(Session.id == session_uuid))
                session_obj = session_result.scalar_one_or_none()
                if session_obj and session_obj.agent_id:
                    agent_result = await db.execute(select(Agent).where(Agent.id == session_obj.agent_id))
                    agent_obj = agent_result.scalar_one_or_none()
                    if agent_obj:
                        metadata_agent.setdefault("agent_id", str(agent_obj.id))
                        metadata_agent.setdefault("agent_name", agent_obj.name)
                        metadata_agent.setdefault("agent_public_id", str(agent_obj.public_id))
                        if getattr(agent_obj, "icon", None):
                            metadata_agent.setdefault("agent_thumbnail", agent_obj.icon)
            except Exception:
                pass

        return metadata_agent if metadata_agent else None
    except Exception:
        return None


async def _log_message_feedback(
    db: AsyncSession,
    message_id: str,
    feedback_id: str,
    feedback_type: str,  # "Positive" or "Negative"
    comment: Optional[str],
    user_id: str,
    user_email: str,
    user_role: str,
    user_department: Optional[str],
    user_entra_id: Optional[str],
    correlation_id: Optional[str],
) -> None:
    """Log USER_MESSAGE_FEEDBACK_CREATED event to user_logs table."""
    try:
        import uuid
        from aldar_middleware.database.base import async_session
        
        # Try to get message to extract session_id
        conversation_id = None
        agent_id = None
        agent_public_id = None
        agent_name = None
        custom_query_about_user = None
        custom_query_topics_of_interest = None
        custom_query_preferred_formatting = None
        selected_agent_type = None
        is_internet_search_used = None
        is_sent_directly_to_openai = None
        
        # Try to find message by ID (try both UUID and string formats)
        try:
            message_uuid = UUID(message_id)
            message_query = select(Message).where(Message.id == message_uuid)
        except (ValueError, TypeError):
            # If not a valid UUID, try to find by string match
            message_query = select(Message).where(cast(Message.id, String) == message_id)
        
        message_result = await db.execute(message_query)
        message = message_result.scalar_one_or_none()
        
        if message and message.session_id:
            conversation_id = str(message.session_id)
            
            # Get session to extract metadata
            session_query = select(Session).where(Session.id == message.session_id)
            session_result = await db.execute(session_query)
            session = session_result.scalar_one_or_none()
            
            if session and session.session_metadata:
                metadata = session.session_metadata
                
                # Extract custom queries
                custom_query_about_user = metadata.get("custom_query_about_user")
                custom_query_topics_of_interest = metadata.get("custom_query_topics_of_interest")
                custom_query_preferred_formatting = metadata.get("custom_query_preferred_formatting")
                selected_agent_type = metadata.get("selected_agent_type")
                is_internet_search_used = metadata.get("is_internet_search_used")
                is_sent_directly_to_openai = metadata.get("is_sent_directly_to_openai")
                
                # Get agent information
                agent_id_from_metadata = metadata.get("agent_id") or metadata.get("agentId")
                if agent_id_from_metadata:
                    try:
                        # Try to get agent details - first try as integer ID
                        agent = None
                        if isinstance(agent_id_from_metadata, int) or (isinstance(agent_id_from_metadata, str) and agent_id_from_metadata.isdigit()):
                            agent_query = select(Agent).where(Agent.id == int(agent_id_from_metadata))
                            agent_result = await db.execute(agent_query)
                            agent = agent_result.scalar_one_or_none()
                        else:
                            # Try as UUID public_id
                            try:
                                agent_uuid = UUID(str(agent_id_from_metadata))
                                agent_query = select(Agent).where(Agent.public_id == agent_uuid)
                                agent_result = await db.execute(agent_query)
                                agent = agent_result.scalar_one_or_none()
                            except (ValueError, TypeError):
                                pass
                        
                        if agent:
                            agent_id = str(agent.id)
                            agent_public_id = str(agent.public_id)
                            agent_name = agent.name
                    except Exception:
                        pass
        
        # Build agent object if available
        agent_object = None
        if agent_id or agent_public_id or agent_name:
            agent_object = {}
            if agent_public_id:
                agent_object["agentPublicId"] = agent_public_id
            if agent_id:
                agent_object["agentId"] = agent_id  # AiQ 2.5 Agent ID
            if agent_name:
                agent_object["agentName"] = agent_name
        
        # Build custom parameters
        custom_parameters = {}
        if selected_agent_type:
            custom_parameters["mode"] = selected_agent_type
        if is_internet_search_used is not None:
            custom_parameters["webSearch"] = is_internet_search_used
        if agent_id:
            custom_parameters["agentId"] = agent_id  # AiQ 2.5 Agent ID
        
        # Build event payload
        event_payload = {
            "messageId": message_id,
            "feedbackId": feedback_id,
            "feedbackType": feedback_type,
        }
        
        if conversation_id:
            event_payload["conversationId"] = conversation_id
        
        if comment:
            event_payload["feedbackComment"] = comment
        
        if custom_query_about_user:
            event_payload["customQueryAboutUser"] = custom_query_about_user
        
        if custom_query_topics_of_interest:
            event_payload["customQueryTopicsOfInterest"] = custom_query_topics_of_interest
        
        if custom_query_preferred_formatting:
            event_payload["customQueryPreferredFormatting"] = custom_query_preferred_formatting
        
        if custom_parameters:
            event_payload["customParameters"] = custom_parameters
        
        if agent_object:
            event_payload["agent"] = agent_object
        
        # Build user log entry in 3.0 format
        user_log_entry = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "eventType": "USER_MESSAGE_FEEDBACK_CREATED",
            "eventPayload": event_payload,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "userId": user_id,
            "email": user_email or "N/A",
            "role": user_role or "NORMAL",
            "department": user_department,
            "userEntraId": user_entra_id,
            "correlationId": correlation_id,
        }
        
        # Write to PostgreSQL user_logs table (async, don't wait)
        async def write_to_postgres():
            try:
                async with async_session() as db_session:
                    await postgres_logs_service.write_user_log(db_session, user_log_entry)
            except Exception as e:
                logger.warning(f"Failed to write USER_MESSAGE_FEEDBACK_CREATED to PostgreSQL (non-blocking): {e}")
        
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(write_to_postgres())
            else:
                loop.run_until_complete(write_to_postgres())
        except Exception as e:
            logger.warning(f"Failed to schedule PostgreSQL write for USER_MESSAGE_FEEDBACK_CREATED (non-blocking): {e}")
    except Exception as e:
        logger.warning(f"Error preparing USER_MESSAGE_FEEDBACK_CREATED log entry (non-blocking): {e}")


@router.post("/messages/{message_id}/feedback")
async def submit_message_feedback(
    message_id: str,
    request: ExtendedMessageFeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Submit like/dislike feedback for a message with optional comment.
    
    - **message_id**: Message ID (from URL path)
    - **reaction**: "like" or "dislike" or null (to remove feedback)
    - **comment**: Optional feedback comment (max 5000 chars)
    - **session_id**: Optional session identifier (stored in feedback_data table)
    
    User can only select one option at a time (like OR dislike).
    If reaction is null, existing feedback will be deleted.
    """
    correlation_id = get_correlation_id()
    
    await _enforce_chat_rate_limit(current_user)
    
    user_id = str(current_user.id)
    user_email = current_user.email
    
    # Normalize message_id for consistent storage and lookup (lowercase, strip whitespace)
    normalized_message_id = message_id.lower().strip() if message_id else None
    if not normalized_message_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid message_id"
        )
    
    try:
        from aldar_middleware.services.feedback_service import FeedbackService
        
        feedback_service = FeedbackService(db)
        
        # Check if feedback already exists for this message by this user
        # Use normalized message_id for lookup
        existing_feedback_query = select(FeedbackData).where(
            and_(
                FeedbackData.entity_type == FeedbackEntityType.MESSAGE,
                FeedbackData.entity_id == normalized_message_id,
                FeedbackData.user_id == user_id,
                FeedbackData.deleted_at.is_(None),
            )
        )
        existing_result = await db.execute(existing_feedback_query)
        existing_feedback = existing_result.scalar_one_or_none()
        
        # If reaction is null, delete existing feedback
        if request.reaction is None:
            if existing_feedback:
                # Soft delete
                existing_feedback.deleted_at = datetime.utcnow()
                await db.commit()
                logger.info(
                    f"Deleted message feedback: message_id={normalized_message_id}, user={user_email}"
                )
                return {
                    "success": True,
                    "message": "Feedback removed successfully",
                    "reaction": None,
                    "correlation_id": correlation_id,
                }
            else:
                # No feedback to delete
                return {
                    "success": True,
                    "message": "No feedback to remove",
                    "reaction": None,
                    "correlation_id": correlation_id,
                }
        
        # Map reaction to FeedbackRating
        # "like" -> THUMBS_UP, "dislike" -> THUMBS_DOWN
        rating_map = {
            "like": FeedbackRating.THUMBS_UP,
            "dislike": FeedbackRating.THUMBS_DOWN,
        }
        rating = rating_map.get(request.reaction)
        
        if not rating:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid reaction: {request.reaction}. Must be 'like' or 'dislike'"
            )
        
        if existing_feedback:
            # Update existing feedback (user can change from like to dislike or vice versa)
            existing_feedback.rating = rating
            existing_feedback.comment = request.comment
            if request.session_id is not None:
                existing_feedback.session_id = request.session_id
            # Merge agent metadata into metadata_json
            agent_meta = await _build_agent_feedback_metadata(db, request)
            if agent_meta:
                metadata_json = dict(existing_feedback.metadata_json or {})
                metadata_json["agent"] = agent_meta
                existing_feedback.metadata_json = metadata_json
                try:
                    flag_modified(existing_feedback, "metadata_json")
                except Exception:
                    pass
            existing_feedback.updated_at = datetime.utcnow()
            await db.commit()
            await db.refresh(existing_feedback)
            
            logger.info(
                f"Updated message feedback: message_id={normalized_message_id}, "
                f"reaction={request.reaction}, user={user_email}"
            )
            
            # Log USER_MESSAGE_FEEDBACK_CREATED (also for updates)
            try:
                await _log_message_feedback(
                    db=db,
                    message_id=normalized_message_id,
                    feedback_id=str(existing_feedback.feedback_id),
                    feedback_type="Positive" if request.reaction == "like" else "Negative",
                    comment=existing_feedback.comment,
                    user_id=user_id,
                    user_email=user_email,
                    user_role="ADMIN" if current_user.is_admin else "NORMAL",
                    user_department=current_user.azure_department,
                    user_entra_id=current_user.azure_ad_id,
                    correlation_id=correlation_id,
                )
            except Exception as e:
                logger.error(f"Failed to log message feedback: {e}", exc_info=True)
            
            return {
                "success": True,
                "message": "Feedback updated successfully",
                "feedback_id": str(existing_feedback.feedback_id),
                "reaction": request.reaction,
                "comment": existing_feedback.comment,
                "correlation_id": correlation_id,
            }
        else:
            # Check user's feedback count limit (max 500 per user) before creating new feedback
            from sqlalchemy import func
            feedback_count_query = select(func.count(FeedbackData.feedback_id)).where(
                and_(
                    FeedbackData.user_id == user_id,
                    FeedbackData.deleted_at.is_(None)  # Only count non-deleted feedback
                )
            )
            feedback_count_result = await db.execute(feedback_count_query)
            current_feedback_count = feedback_count_result.scalar_one() or 0
            
            if current_feedback_count >= 500:
                logger.warning(
                    f"Feedback limit exceeded for user {user_email}",
                    extra={
                        "correlation_id": correlation_id,
                        "user_id": user_id,
                        "current_count": current_feedback_count,
                        "limit": 500,
                    },
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Feedback limit reached. Maximum 500 feedback items allowed per user. Current count: {current_feedback_count}",
                )
            
            # Create new feedback
            # Build metadata for new feedback
            agent_meta = await _build_agent_feedback_metadata(db, request)
            metadata_payload = {"agent": agent_meta} if agent_meta else None

            feedback = await feedback_service.create_feedback(
                user_id=user_id,
                user_email=user_email,
                entity_id=normalized_message_id,  # Use normalized ID for storage
                entity_type=FeedbackEntityType.MESSAGE,
                rating=rating,
                comment=request.comment,
                correlation_id=correlation_id,
                session_id=request.session_id,
                metadata=metadata_payload,
            )
            
            await db.commit()
            
            logger.info(
                f"Created message feedback: message_id={normalized_message_id}, "
                f"reaction={request.reaction}, user={user_email}"
            )
            
            # Log USER_MESSAGE_FEEDBACK_CREATED
            try:
                await _log_message_feedback(
                    db=db,
                    message_id=normalized_message_id,
                    feedback_id=str(feedback.feedback_id),
                    feedback_type="Positive" if request.reaction == "like" else "Negative",
                    comment=feedback.comment,
                    user_id=user_id,
                    user_email=user_email,
                    user_role="ADMIN" if current_user.is_admin else "NORMAL",
                    user_department=current_user.azure_department,
                    user_entra_id=current_user.azure_ad_id,
                    correlation_id=correlation_id,
                )
            except Exception as e:
                logger.error(f"Failed to log message feedback: {e}", exc_info=True)
            
            return {
                "success": True,
                "message": "Feedback submitted successfully",
                "feedback_id": str(feedback.feedback_id),
                "reaction": request.reaction,
                "comment": feedback.comment,
                "correlation_id": correlation_id,
            }
            
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(
            f"Error submitting message feedback: {str(e)}, "
            f"message_id={message_id}, user={user_email}"
        )
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to submit feedback: {str(e)}"
        )


@router.delete("/messages/{message_id}/feedback")
async def delete_message_feedback(
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Delete feedback for a message (remove like/dislike).
    """
    correlation_id = get_correlation_id()
    
    await _enforce_chat_rate_limit(current_user)
    
    user_id = str(current_user.id)
    
    # Normalize message_id for consistent lookup (lowercase, strip whitespace)
    normalized_message_id = message_id.lower().strip() if message_id else None
    if not normalized_message_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid message_id"
        )
    
    try:
        # Find feedback for this message by this user
        feedback_query = select(FeedbackData).where(
            and_(
                FeedbackData.entity_type == FeedbackEntityType.MESSAGE,
                FeedbackData.entity_id == normalized_message_id,
                FeedbackData.user_id == user_id,
                FeedbackData.deleted_at.is_(None),
            )
        )
        result = await db.execute(feedback_query)
        feedback = result.scalar_one_or_none()
        
        if not feedback:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Feedback not found for this message"
            )
        
        # Soft delete
        feedback.deleted_at = datetime.utcnow()
        await db.commit()
        
        logger.info(
            f"Deleted message feedback: message_id={normalized_message_id}, user={current_user.email}"
        )
        
        return {
            "success": True,
            "message": "Feedback deleted successfully",
            "correlation_id": correlation_id,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(
            f"Error deleting message feedback: {str(e)}, message_id={message_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete feedback: {str(e)}"
        )


@router.post("/message")
async def send_chat_message(
    request: SendChatMessageRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    correlation_id = get_correlation_id()

    await _enforce_chat_rate_limit(current_user)
    _validate_query_length(request.query, "query")

    # Increment question count for the user (monthly tracking)
    # Note: This will be committed together with the chat message in the main transaction
    # If tracking fails, we log it but don't block the request
    try:
        await increment_question_count(current_user.id, db)
    except Exception as e:
        # Log error but continue - tracker is non-critical
        logger.error(f"Failed to increment question count for user {current_user.id}: {e}")
        # Don't rollback here - let the main transaction handle commit/rollback at the end

    attachments_metadata: List[Dict[str, Any]] = []
    attachment_ids: List[UUID] = []
    if request.attachments:
        attachment_ids = [UUID(item["attachment_uuid"]) for item in request.attachments]
        if len(attachment_ids) > MAX_ATTACHMENTS_PER_SESSION:
            _raise_chat_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="TOO_MANY_FILES",
                error_message=f"A maximum of {MAX_ATTACHMENTS_PER_SESSION} attachments is allowed per message",
                details={"field": "attachments", "message": "Too many attachments provided"},
            )
        result = await db.execute(
            select(Attachment).where(
                Attachment.id.in_(attachment_ids),
                Attachment.is_active == True,
            )
        )
        attachment_records = list(result.scalars())
        found_ids = {att.id for att in attachment_records}
        missing_ids = [str(att_id) for att_id in attachment_ids if att_id not in found_ids]
        if missing_ids:
            _raise_chat_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="VALIDATION_ERROR",
                error_message="One or more attachments could not be found",
                details={"field": "attachments", "attachments": missing_ids},
            )
        unauthorized = [
            str(att.id)
            for att in attachment_records
            if str(att.user_id) != str(current_user.id) and not getattr(current_user, "is_admin", False)
        ]
        if unauthorized:
            _raise_chat_error(
                status_code=status.HTTP_403_FORBIDDEN,
                error_code="VALIDATION_ERROR",
                error_message="You do not have access to one or more attachments",
                details={"field": "attachments", "attachments": unauthorized},
            )
        oversized = [
            str(att.id)
            for att in attachment_records
            if att.file_size and att.file_size > MAX_ATTACHMENT_SIZE_BYTES
        ]
        if oversized:
            _raise_chat_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="FILE_TOO_LARGE",
                error_message="One or more attachments exceed the 50MB size limit",
                details={"field": "attachments", "attachments": oversized},
            )
        for att in attachment_records:
            # Generate SAS URL with 30-minute expiration for frontend
            sas_url = _generate_attachment_sas_url(att.blob_name)
            attachments_metadata.append(
                {
                    "attachment_id": str(att.id),
                    "file_name": att.file_name,
                    "file_size": att.file_size,
                    "content_type": att.content_type,
                    "blob_url": sas_url,  # Use SAS URL with 30-minute expiration
                    "blob_name": att.blob_name,
                    "uploaded_at": att.created_at.isoformat() if att.created_at else None,
                }
            )

    try:
        agent_id, agent_id_str = await _resolve_agent(request.agent_id, db)

        # Update last_used timestamp for the agent
        await _update_agent_last_used(agent_id, db)

        session: Optional[Session] = None
        session_uuid = request.session_id
        if session_uuid:
            result = await db.execute(
                select(Session).where(
                    Session.id == UUID(str(session_uuid)),
                    Session.user_id == current_user.id,
                )
            )
            session = result.scalar_one_or_none()
            if not session:
                _raise_chat_error(
                    status_code=status.HTTP_404_NOT_FOUND,
                    error_code="SESSION_NOT_FOUND",
                    error_message="Chat session not found",
                    details={"field": "session_id", "message": "Specified session does not exist"},
                )
            _ensure_session_active(session)
            # Update last_used for the agent associated with this session
            if session.agent_id:
                await _update_agent_last_used(session.agent_id, db)
        else:
            title = request.query.strip()[:50]
            if len(request.query.strip()) > 50:
                title += "..."
            session = Session(
                user_id=current_user.id,
                agent_id=agent_id,
                session_name=title or "New Chat",
                session_metadata={"agent_id": agent_id_str, "agentId": agent_id_str},
                session_type="chat",
                status="active",
            )
            db.add(session)
            await db.flush()  # Flush to get session.id and session.session_id
            # Note: Session will be committed with the user_message below
            
            # Get agent record for logging
            agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent_record = agent_result.scalar_one_or_none()
            
            # Check if the query matches a starter prompt (for logging USER_CONVERSATION_STARTING_PROMPT_CHOSEN)
            matched_starter_prompt = None
            if request.query:
                # Query all starter prompts and check if any prompt text matches the query
                prompts_result = await db.execute(select(StarterPrompt))
                all_prompts = prompts_result.scalars().all()
                
                # Normalize query for comparison (strip whitespace, normalize multiple spaces to single space)
                import re
                normalized_query = re.sub(r'\s+', ' ', request.query.strip()).lower()
                
                for prompt in all_prompts:
                    # Normalize prompt text the same way
                    normalized_prompt = re.sub(r'\s+', ' ', prompt.prompt.strip()).lower()
                    
                    # Check if query exactly matches the prompt text (case-insensitive, whitespace-normalized)
                    if normalized_prompt == normalized_query:
                        matched_starter_prompt = prompt
                        logger.info(f"Matched starter prompt: {prompt.id} - {prompt.title}")
                        break
            
            # Log starting prompt chosen if query matches a starter prompt
            if matched_starter_prompt:
                log_starting_prompt_chosen(
                    chat_id=str(session.id),
                    session_id=session.session_id,
                    user_id=str(current_user.id),
                    username=current_user.username or current_user.email,
                    prompt_id=matched_starter_prompt.id,
                    prompt_text=matched_starter_prompt.prompt,
                    correlation_id=correlation_id,
                    email=current_user.email,
                    role="ADMIN" if current_user.is_admin else "NORMAL",
                    department=current_user.azure_department,
                    user_entra_id=current_user.azure_ad_id,
                    agent_id=agent_id_str,
                    agent_name=agent_record.name if agent_record else None,
                    agent_type=await determine_agent_type(agent_record, db),
                )
            
            # Extract custom fields from request if available
            selected_agent_type = None
            custom_query_about_user = None
            custom_query_topics_of_interest = None
            custom_query_preferred_formatting = None
            is_internet_search_used = None
            is_sent_directly_to_openai = None
            if request.custom_fields:
                selected_agent_type = (
                    request.custom_fields.get("selectedAgentType") or
                    request.custom_fields.get("selected_agent_type") or
                    request.custom_fields.get("agentType") or
                    request.custom_fields.get("agent_type")
                )
                custom_query_about_user = request.custom_fields.get("customQueryAboutUser")
                custom_query_topics_of_interest = request.custom_fields.get("customQueryTopicsOfInterest")
                custom_query_preferred_formatting = request.custom_fields.get("customQueryPreferredFormatting")
                is_internet_search_used = request.custom_fields.get("isInternetSearchUsed")
                is_sent_directly_to_openai = request.custom_fields.get("isSentDirectlyToOpenAi")
            
            agent_type = await determine_agent_type(agent_record, db)
            log_chat_session_created(
                chat_id=str(session.id),
                session_id=session.session_id,
                user_id=str(current_user.id),
                username=current_user.username or current_user.email,
                title=session.session_name,
                agent_id=agent_id_str,
                attachments=attachments_metadata,
                initial_message=request.query,
                correlation_id=correlation_id,
                # User info
                email=current_user.email,
                role="ADMIN" if current_user.is_admin else "NORMAL",
                department=current_user.azure_department,
                user_entra_id=current_user.azure_ad_id,
                # Agent info
                agent_name=agent_record.name if agent_record else None,
                agent_type=agent_type,
                agent_public_id=agent_id_str,
                # Event payload fields
                selected_agent_type=selected_agent_type,
                custom_query_about_user=custom_query_about_user,
                is_internet_search_used=is_internet_search_used,
                is_sent_directly_to_openai=is_sent_directly_to_openai,
                custom_query_topics_of_interest=custom_query_topics_of_interest,
                custom_query_preferred_formatting=custom_query_preferred_formatting,
            )

        user_metadata: Dict[str, Any] = {}
        if attachments_metadata:
            user_metadata["attachments"] = attachments_metadata
        if request.custom_fields:
            user_metadata["custom_fields"] = request.custom_fields

        # Extract selected_agent_type and custom query fields from custom_fields
        selected_agent_type = None
        message_metadata = None
        if request.custom_fields:
            # Extract selectedAgentType (case-insensitive)
            selected_agent_type = (
                request.custom_fields.get("selectedAgentType") or
                request.custom_fields.get("selected_agent_type") or
                request.custom_fields.get("agentType") or
                request.custom_fields.get("agent_type")
            )
            
            # Store ALL custom_fields in message_metadata
            # This includes mode, custom query fields, and any other data user sends
            message_metadata = dict(request.custom_fields)  # Copy all custom fields

        user_message = Message(
            session_id=session.id,
            user_id=current_user.id,
            agent_id=agent_id,
            content_type="text",
            content=request.query,
            role="user",
            tool_calls=user_metadata or None,
            selected_agent_type=selected_agent_type,
            message_metadata=message_metadata,
        )
        db.add(user_message)
        
        # Update session's last_message_interaction_at to current time
        # This ensures the session moves to the correct time-based group (today, previous-7-days, etc.)
        # when a new message is sent in an old chat
        # Use datetime.utcnow() for timezone-naive datetime (Session model uses TIMESTAMP WITHOUT TIME ZONE)
        now_utc = datetime.utcnow()
        session.last_message_interaction_at = now_utc
        session.updated_at = now_utc
        
        await db.commit()
        await db.refresh(session)
        await db.refresh(user_message)  # Refresh to get the user_message.id

        # Extract custom query fields from message_metadata
        custom_query_about_user = None
        custom_query_topics_of_interest = None
        custom_query_preferred_formatting = None
        if message_metadata:
            custom_query_about_user = message_metadata.get("customQueryAboutUser")
            custom_query_topics_of_interest = message_metadata.get("customQueryTopicsOfInterest")
            custom_query_preferred_formatting = message_metadata.get("customQueryPreferredFormatting")
        
        # Get agent record for logging (if not already fetched)
        if 'agent_record' not in locals() or agent_record is None:
            agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent_record = agent_result.scalar_one_or_none()
        
        # Get agent info if available
        agent_name = None
        agent_type = None
        agent_public_id = agent_id_str if agent_id_str else None
        if agent_record:
            agent_name = agent_record.name
            agent_type = await determine_agent_type(agent_record, db)
        
        log_chat_message(
            chat_id=str(session.id),
            message_id=str(user_message.id),
            message_type="user",
            role="user",
            content=request.query,
            user_id=str(current_user.id),
            username=current_user.username or current_user.email,
            correlation_id=correlation_id,
            # User info
            email=current_user.email,
            role_user="ADMIN" if current_user.is_admin else "NORMAL",
            department=current_user.azure_department,
            user_entra_id=current_user.azure_ad_id,
            # Message info
            conversation_id=str(session.id),
            selected_agent_type=selected_agent_type,
            custom_query_about_user=custom_query_about_user,
            is_internet_search_used=user_message.is_internet_search_used,
            is_sent_directly_to_openai=user_message.is_sent_directly_to_openai,
            custom_query_topics_of_interest=custom_query_topics_of_interest,
            attachments=attachments_metadata if attachments_metadata else None,
            custom_query_preferred_formatting=custom_query_preferred_formatting,
            user_input=request.query,
            # Agent info
            agent_name=agent_name,
            agent_type=agent_type,
            agent_public_id=agent_public_id,
        )

        # Check if agent_id is empty/null - if so, use query-team endpoint instead of query-agent
        # Check the original request.agent_id, not the resolved one (since _resolve_agent always returns Super Agent)
        use_query_team = False
        agent_record = None
        agent_name = None
        obo_token_for_jwt = None  # Store OBO token to embed in user's JWT token

        # Check if original request had an agent_id (empty string or None means no agent selected)
        original_agent_id = request.agent_id
        has_agent_id = original_agent_id and original_agent_id.strip()
        
        if has_agent_id and agent_id and agent_id_str:
            agent_result = await db.execute(
                select(Agent).where(Agent.id == agent_id)
            )
            agent_record = agent_result.scalar_one_or_none()
            
            # If agent not found, fallback to Super Agent (default agent)
            if not agent_record:
                logger.warning(
                    f"Agent with id={agent_id} not found, falling back to Super Agent, "
                    f"correlation_id={correlation_id}"
                )
                super_agent_result = await db.execute(
                    select(Agent).where(Agent.name == "Super Agent")
                )
                agent_record = super_agent_result.scalar_one_or_none()
                if agent_record:
                    agent_id = agent_record.id  # Update agent_id to Super Agent's id
                    session.agent_id = agent_id  # Update session's agent_id
                    await db.commit()
            
            agent_name = agent_record.name if agent_record else None
        else:
            # No agent_id provided - use query-team endpoint
            use_query_team = True
            logger.info(
                f"No agent_id provided, using query-team endpoint, "
                f"correlation_id={correlation_id}"
            )
        
        # Use query-agent if agent exists and we have agent name
        # Use query-team if no agent_id was provided
        response_text = None
        stream_id = None
        response_type = "stream"
        error_text = None
        external_api_error = None  # Track errors from external AGNO API
        
        if use_query_team:
            # Call query-team endpoint when no agent is selected
            try:
                from aldar_middleware.orchestration.agno import agno_service
                
                user_id_str = str(current_user.id)
                session_id_str = session.session_id
                
                # Extract authorization header from request to forward to AGNO API
                auth_header = http_request.headers.get("authorization")
                
                # Generate unique stream_id per message/request
                stream_id = str(uuid4())
                
                # Prepare custom_fields if provided
                custom_fields = request.custom_fields if request.custom_fields else None
                
                # Prepare stream_config
                stream_config = {"stream": True}
                
                # Prepare user_context with preferences from database
                # Only include preferences if enable_for_new_messages is True
                user_context = {
                    "user_email": current_user.email,
                    "user_name": {
                        "user_name": current_user.username or current_user.email
                    }
                }
                
                # Check if preferences should be included based on enable_for_new_messages
                enable_for_new_messages = False
                if current_user.preferences and isinstance(current_user.preferences, dict):
                    enable_for_new_messages = current_user.preferences.get("enable_for_new_messages", False)
                
                # Only include preferences if enable_for_new_messages is True
                if enable_for_new_messages:
                    user_preferences = {}
                    if current_user.preferences and isinstance(current_user.preferences, dict):
                        # Extract the specific preference fields that should be sent to external API
                        user_preferences = {
                            "preferred_formatting": current_user.preferences.get("preferred_formatting", ""),
                            "topics_of_interest": current_user.preferences.get("topics_of_interest", ""),
                            "about_user": current_user.preferences.get("about_user", ""),
                            "enable_for_new_messages": current_user.preferences.get("enable_for_new_messages", False)
                        }
                    user_context["preferences"] = user_preferences
                    logger.info(
                        f"✓ User preferences included in user_context for query-team "
                        f"(enable_for_new_messages=True), correlation_id={correlation_id}"
                    )
                else:
                    logger.info(
                        f"⊘ User preferences NOT included in user_context for query-team "
                        f"(enable_for_new_messages=False), correlation_id={correlation_id}"
                    )
                
                # Prepare attachments in the format required by query-team endpoint
                # External API requires: attachment_uuid, download_url (mapped from blob_url), filename, content_type
                # Frontend only sends attachment_uuid, we fetch full details and send to external API
                team_attachments = []
                if attachments_metadata:
                    for att_meta in attachments_metadata:
                        download_url = att_meta.get("blob_url") or ""
                        # Verify SAS URL being sent to external API
                        is_sas_url = "?" in download_url and ("sig=" in download_url or "sv=" in download_url)
                        
                        # Extract SAS token parameters for verification
                        sas_params = ""
                        if is_sas_url and "?" in download_url:
                            url_parts = download_url.split("?", 1)
                            if len(url_parts) > 1:
                                sas_params = url_parts[1]
                                # Extract key SAS parameters
                                params_dict = {}
                                for param in sas_params.split("&"):
                                    if "=" in param:
                                        key, value = param.split("=", 1)
                                        if key in ["sv", "se", "sig"]:
                                            params_dict[key] = value[:20] + "..." if len(value) > 20 else value
                        
                        logger.info(
                            f"📎 Sending attachment to external API: "
                            f"attachment_uuid={att_meta.get('attachment_id')}, "
                            f"filename={att_meta.get('file_name')}, "
                            f"is_sas_url={is_sas_url}, "
                            f"base_url={download_url.split('?')[0] if download_url else 'empty'}, "
                            f"sas_params={params_dict if is_sas_url else 'none'}"
                        )
                        
                        # SAS URL verified - no additional logging needed in production
                        
                        team_attachments.append({
                            "attachment_uuid": att_meta.get("attachment_id"),
                            "download_url": download_url,  # This now contains SAS URL with 30-min expiry
                            "filename": att_meta.get("file_name") or "",
                            "content_type": att_meta.get("content_type") or "application/octet-stream"
                        })
                
                logger.info(
                    f"Using query-team endpoint, "
                    f"stream_id={stream_id}, session_id={session_id_str}, "
                    f"correlation_id={correlation_id}"
                )
                
                # Get Azure AD user access token for OBO exchange
                # Auto-refresh from user's stored refresh token (automatic flow)
                user_access_token_local = None
                if current_user.azure_ad_refresh_token:
                    # AUTO-REFRESH: Try to get Azure AD token from refresh token (automatic flow)
                    logger.info("🔄 Auto-refreshing Azure AD token from stored refresh token for OBO exchange...")
                    try:
                        from aldar_middleware.auth.azure_ad import azure_ad_auth
                        from aldar_middleware.settings import settings
                        
                        # Refresh token with OBO scope
                        refresh_data = {
                            "client_id": settings.azure_client_id,
                            "client_secret": settings.azure_client_secret,
                            "refresh_token": current_user.azure_ad_refresh_token,
                            "grant_type": "refresh_token",
                            "scope": f"openid profile offline_access {settings.azure_client_id}/.default"
                        }
                        
                        import httpx
                        async with httpx.AsyncClient() as client:
                            response = await client.post(
                                f"{azure_ad_auth.authority}/oauth2/v2.0/token",
                                data=refresh_data
                            )
                            
                            if response.status_code == 200:
                                token_response = response.json()
                                user_access_token_local = token_response.get("access_token")
                                if user_access_token_local:
                                    logger.info("✓ Azure AD user access token auto-obtained from refresh token for OBO exchange")
                                    # Update refresh token if new one provided
                                    new_refresh_token = token_response.get("refresh_token")
                                    if new_refresh_token:
                                        current_user.azure_ad_refresh_token = new_refresh_token
                                        await db.commit()
                                else:
                                    logger.warning("⚠️  Refresh token response did not contain access_token")
                            else:
                                logger.warning(f"⚠️  Failed to auto-refresh Azure AD token: {response.status_code} - {response.text}")
                    except Exception as e:
                        logger.warning(f"⚠️  Failed to auto-refresh Azure AD token from refresh token: {e}")
                else:
                    logger.warning("⚠️  No Azure AD refresh token stored for user - OBO token exchange will be skipped")
                
                if user_access_token_local:
                    logger.info(f"✓ Azure AD user access token available for OBO exchange (length: {len(user_access_token_local)})")
                else:
                    logger.warning("⚠️  No Azure AD user access token available - OBO token exchange will be skipped")
                
                # IMPORTANT: Create JWT token with MCP token and ARIA token embedded BEFORE calling AGNO API
                # The AGNO API expects the JWT token to have mcp_token and aria_token fields
                jwt_token_with_mcp = None
                if auth_header and user_access_token_local:
                    try:
                        from aldar_middleware.auth.obo_utils import exchange_token_obo, exchange_token_aria, add_mcp_token_to_jwt, decode_token_without_verification
                        
                        # Extract JWT token from authorization header
                        jwt_token = auth_header[7:] if auth_header.startswith("Bearer ") else auth_header
                        
                        # Check if JWT already has mcp_token and aria_token fields
                        try:
                            decoded_jwt = decode_token_without_verification(jwt_token)
                            if "mcp_token" in decoded_jwt and "aria_token" in decoded_jwt:
                                logger.info("✓ JWT token already contains mcp_token and aria_token fields, using as-is")
                                jwt_token_with_mcp = jwt_token
                            else:
                                # JWT doesn't have mcp_token/aria_token, need to add them
                                logger.info(" JWT token doesn't have mcp_token/aria_token fields, creating new JWT with tokens...")
                                
                                # Exchange for OBO token (MCP)
                                obo_token = await exchange_token_obo(user_access_token_local)
                                logger.info(f"✓ MCP token acquired: {len(obo_token) if obo_token else 0} chars")
                                
                                # Exchange for ARIA token
                                aria_token = await exchange_token_aria(user_access_token_local)
                                logger.info(f"✓ ARIA token acquired: {len(aria_token) if aria_token else 0} chars")
                                
                                # Add mcp_token and aria_token to JWT
                                logger.info(f" Passing to JWT: mcp_token={len(obo_token) if obo_token else 0} chars, aria_token={len(aria_token) if aria_token else 0} chars")
                                jwt_token_with_mcp = add_mcp_token_to_jwt(jwt_token, obo_token, aria_token)
                        except Exception as e:
                            logger.warning(f"  Could not decode JWT token: {e}, will try to create new one with MCP and ARIA tokens")
                            # Try to create new JWT with MCP and ARIA tokens anyway
                            obo_token = await exchange_token_obo(user_access_token_local)
                            aria_token = await exchange_token_aria(user_access_token_local)
                            logger.info(f" Exception path - Passing to JWT: mcp_token={len(obo_token) if obo_token else 0} chars, aria_token={len(aria_token) if aria_token else 0} chars")
                            jwt_token_with_mcp = add_mcp_token_to_jwt(jwt_token, obo_token, aria_token)
                            logger.info(f"✓ Created new JWT token with MCP and ARIA tokens embedded")
                    except Exception as e:
                        logger.warning(f"  Failed to create JWT with MCP/ARIA tokens: {e}, will use original JWT")
                        jwt_token_with_mcp = None
                
                # Use JWT with MCP token if available, otherwise use original
                final_auth_header = f"Bearer {jwt_token_with_mcp}" if jwt_token_with_mcp else auth_header
                
                # Call AGNO API /query-team endpoint
                # System will automatically: exchange OBO token, create MCP token, and send in Authorization header
                logger.info(f"📞 Calling query_team with user_access_token: {'Yes' if user_access_token_local else 'No'}")
                logger.info(f"   Using JWT with MCP token: {'Yes' if jwt_token_with_mcp else 'No'}")
                agno_response = await agno_service.query_team(
                    message=request.query,
                    stream_id=stream_id,
                    session_id=session_id_str,
                    user_id=user_id_str,
                    team_id=None,  # Will use Super Agent's public_id automatically
                    db=db,  # Pass database session to fetch Super Agent
                    attachments=team_attachments if team_attachments else None,
                    custom_fields=custom_fields,
                    user_context=user_context,
                    stream_config=stream_config,
                    authorization_header=final_auth_header,  # Use JWT with MCP token embedded
                    user_access_token=user_access_token_local,  # Azure AD token (for OBO exchange and MCP token creation)
                    obo_token=None  # Auto-generate from user_access_token
                )
                
                # Extract MCP token (OBO token) from AGNO service response
                obo_token_from_response = None
                if isinstance(agno_response, dict) and "mcp_token" in agno_response:
                    obo_token_from_response = agno_response.pop("mcp_token", None)
                    logger.info(f"✓ OBO token extracted from AGNO service response")
                
                # Get OBO token that was used (from exchange or cache)
                # We need to get the actual OBO token to embed in user's JWT
                from aldar_middleware.auth.obo_utils import exchange_token_obo
                try:
                    if user_access_token_local:
                        obo_token_for_jwt = await exchange_token_obo(user_access_token_local)
                    else:
                        obo_token_for_jwt = None
                        logger.warning("⚠️  No user_access_token available, cannot add mcp_token to JWT")
                except Exception as e:
                    logger.warning(f"⚠️  Failed to get OBO token for JWT: {e}")
                    obo_token_for_jwt = None
                
                # Extract stream_id and session_id from response
                stream_id_from_response = agno_response.get("stream_id") or stream_id
                session_id_from_response = agno_response.get("session_id") or session_id_str
                
                # IMPORTANT: Always use our database session.session_id, not the one from AGNO response
                # Update stream_id and response_type for successful query-team call
                stream_id = stream_id_from_response
                response_type = "stream"
                
                # Store stream_id in the user message's tool_calls metadata
                if user_message and user_message.tool_calls:
                    user_message.tool_calls["stream_id"] = stream_id
                    user_message.tool_calls["streamId"] = stream_id
                    flag_modified(user_message, "tool_calls")
                    await db.commit()
                elif user_message:
                    user_message.tool_calls = {
                        "stream_id": stream_id,
                        "streamId": stream_id
                    }
                    await db.commit()
                
                logger.info(
                    f"Query-team called successfully, "
                    f"stream_id={stream_id}, agno_session_id={session_id_from_response}, "
                    f"db_session_id={session_id_str}, correlation_id={correlation_id}"
                )
                
                # Log warning if AGNO returned a different session_id
                if session_id_from_response != session_id_str:
                    logger.warning(
                        f"AGNO API returned different session_id ({session_id_from_response}) "
                        f"than our database session_id ({session_id_str}). "
                        f"Using database session_id in response. correlation_id={correlation_id}"
                    )
                
                # Set ai_response to None since we're using external API
                ai_response = None
                
            except Exception as e:
                # Capture the external API error for reporting
                error_message = str(e) if str(e) else f"{type(e).__name__}: Connection or timeout error"
                if not error_message or error_message.strip() == "":
                    error_message = f"{type(e).__name__}: Request failed (likely timeout or connection error)"
                
                external_api_error = {
                    "source": "AGNO_API",
                    "message": error_message,
                    "agent_name": None,
                    "agent_id": None,
                    "endpoint_type": "query-team",
                    "error_type": type(e).__name__
                }
                
                logger.error(
                    f"Error calling external AGNO API (query-team): {error_message}, "
                    f"error_type={type(e).__name__}, correlation_id={correlation_id}, exception={repr(e)}"
                )
                
                # Fallback to AIService if query-team fails
                ai_service = AIService()
                ai_response = await ai_service.generate_response(
                    user_message=request.query,
                    chat_id=str(session.id),
                    user_id=str(current_user.id),
                )
                response_text = ai_response.get("content")
                response_type = "direct"
                error_text = None
        elif agent_record and agent_name:
            try:
                # Call query-agent endpoint internally (via AGNO service)
                from aldar_middleware.orchestration.agno import agno_service
                
                user_id_str = str(current_user.id)
                session_id_str = session.session_id
                
                # Extract authorization header from request to forward to AGNO API
                auth_header = http_request.headers.get("authorization")
                
                # Generate unique stream_id per message/request
                # This allows multiple concurrent streams per session
                # Each message gets its own stream_id for tracking
                stream_id = str(uuid4())
                
                # Prepare custom_fields if provided
                custom_fields = request.custom_fields if request.custom_fields else None
                
                # Prepare stream_config
                stream_config = {"stream": True}
                
                # Prepare user_context with preferences from database
                # Only include preferences if enable_for_new_messages is True
                user_context = {
                    "user_email": current_user.email,
                    "user_name": {
                        "user_name": current_user.username or current_user.email
                    }
                }
                
                # Check if preferences should be included based on enable_for_new_messages
                enable_for_new_messages = False
                if current_user.preferences and isinstance(current_user.preferences, dict):
                    enable_for_new_messages = current_user.preferences.get("enable_for_new_messages", False)
                
                # Only include preferences if enable_for_new_messages is True
                if enable_for_new_messages:
                    user_preferences = {}
                    if current_user.preferences and isinstance(current_user.preferences, dict):
                        # Extract the specific preference fields that should be sent to external API
                        user_preferences = {
                            "preferred_formatting": current_user.preferences.get("preferred_formatting", ""),
                            "topics_of_interest": current_user.preferences.get("topics_of_interest", ""),
                            "about_user": current_user.preferences.get("about_user", ""),
                            "enable_for_new_messages": current_user.preferences.get("enable_for_new_messages", False)
                        }
                    user_context["preferences"] = user_preferences
                    logger.info(
                        f"✓ User preferences included in user_context for query-agent "
                        f"(enable_for_new_messages=True), correlation_id={correlation_id}"
                    )
                else:
                    logger.info(
                        f"⊘ User preferences NOT included in user_context for query-agent "
                        f"(enable_for_new_messages=False), correlation_id={correlation_id}"
                    )
                
                # Prepare attachments in the format required by query-agent endpoint
                # External API requires: attachment_uuid, download_url (mapped from blob_url), filename, content_type
                # Frontend only sends attachment_uuid, we fetch full details and send to external API
                agent_attachments = []
                if attachments_metadata:
                    for att_meta in attachments_metadata:
                        download_url = att_meta.get("blob_url") or ""
                        # Verify SAS URL being sent to external API
                        is_sas_url = "?" in download_url and ("sig=" in download_url or "sv=" in download_url)
                        
                        # Extract SAS token parameters for verification
                        sas_params = ""
                        if is_sas_url and "?" in download_url:
                            url_parts = download_url.split("?", 1)
                            if len(url_parts) > 1:
                                sas_params = url_parts[1]
                                # Extract key SAS parameters
                                params_dict = {}
                                for param in sas_params.split("&"):
                                    if "=" in param:
                                        key, value = param.split("=", 1)
                                        if key in ["sv", "se", "sig"]:
                                            params_dict[key] = value[:20] + "..." if len(value) > 20 else value
                        
                        logger.info(
                            f"📎 Sending attachment to external API: "
                            f"attachment_uuid={att_meta.get('attachment_id')}, "
                            f"filename={att_meta.get('file_name')}, "
                            f"is_sas_url={is_sas_url}, "
                            f"base_url={download_url.split('?')[0] if download_url else 'empty'}, "
                            f"sas_params={params_dict if is_sas_url else 'none'}"
                        )
                        
                        # SAS URL verified - no additional logging needed in production
                        
                        agent_attachments.append({
                            "attachment_uuid": att_meta.get("attachment_id"),
                            "download_url": download_url,  # This now contains SAS URL with 30-min expiry
                            "filename": att_meta.get("file_name") or "",
                            "content_type": att_meta.get("content_type") or "application/octet-stream"
                        })
                
                logger.info(
                    f"Using query-agent endpoint for agent={agent_name}, "
                    f"stream_id={stream_id}, session_id={session_id_str}, "
                    f"correlation_id={correlation_id}"
                )
                
                # Get Azure AD user access token for OBO exchange
                # Auto-refresh from user's stored refresh token (automatic flow)
                user_access_token_local = None
                if current_user.azure_ad_refresh_token:
                    # AUTO-REFRESH: Try to get Azure AD token from refresh token (automatic flow)
                    logger.info("🔄 Auto-refreshing Azure AD token from stored refresh token for OBO exchange...")
                    try:
                        from aldar_middleware.auth.azure_ad import azure_ad_auth
                        from aldar_middleware.settings import settings
                        
                        # Refresh token with OBO scope
                        refresh_data = {
                            "client_id": settings.azure_client_id,
                            "client_secret": settings.azure_client_secret,
                            "refresh_token": current_user.azure_ad_refresh_token,
                            "grant_type": "refresh_token",
                            "scope": f"openid profile offline_access {settings.azure_client_id}/.default"
                        }
                        
                        import httpx
                        async with httpx.AsyncClient() as client:
                            response = await client.post(
                                f"{azure_ad_auth.authority}/oauth2/v2.0/token",
                                data=refresh_data
                            )
                            
                            if response.status_code == 200:
                                token_response = response.json()
                                user_access_token = token_response.get("access_token")
                                if user_access_token:
                                    user_access_token_local = user_access_token  # Fix: assign to the correct variable
                                    logger.info("✓ Azure AD user access token auto-obtained from refresh token for OBO exchange")
                                    # Update refresh token if new one provided
                                    new_refresh_token = token_response.get("refresh_token")
                                    if new_refresh_token:
                                        current_user.azure_ad_refresh_token = new_refresh_token
                                        await db.commit()
                                else:
                                    logger.warning("⚠️  Refresh token response did not contain access_token")
                            else:
                                logger.warning(f"⚠️  Failed to auto-refresh Azure AD token: {response.status_code} - {response.text}")
                    except Exception as e:
                        logger.warning(f"⚠️  Failed to auto-refresh Azure AD token from refresh token: {e}")
                else:
                    logger.warning("⚠️  No Azure AD refresh token stored for user - OBO token exchange will be skipped")
                
                if user_access_token_local:
                    logger.info(f"✓ Azure AD user access token available for OBO exchange (length: {len(user_access_token_local)})")
                else:
                    logger.warning("⚠️  No Azure AD user access token available - OBO token exchange will be skipped")
                
                # IMPORTANT: Create JWT token with MCP token and ARIA token embedded BEFORE calling AGNO API
                # The AGNO API expects the JWT token to have mcp_token and aria_token fields
                jwt_token_with_mcp = None
                if auth_header and user_access_token_local:
                    try:
                        from aldar_middleware.auth.obo_utils import exchange_token_obo, exchange_token_aria, add_mcp_token_to_jwt, decode_token_without_verification
                        
                        # Extract JWT token from authorization header
                        jwt_token = auth_header[7:] if auth_header.startswith("Bearer ") else auth_header
                        
                        # Check if JWT already has mcp_token and aria_token fields
                        try:
                            decoded_jwt = decode_token_without_verification(jwt_token)
                            if "mcp_token" in decoded_jwt and "aria_token" in decoded_jwt:
                                logger.info("✓ JWT token already contains mcp_token and aria_token fields, using as-is")
                                jwt_token_with_mcp = jwt_token
                            else:
                                # JWT doesn't have mcp_token/aria_token, need to add them
                                logger.info("🔄 JWT token doesn't have mcp_token/aria_token fields, creating new JWT with tokens...")
                                
                                # Exchange for OBO token (MCP)
                                obo_token = await exchange_token_obo(user_access_token_local)
                                logger.info(f"✓ MCP token acquired: {len(obo_token) if obo_token else 0} chars")
                                
                                # Exchange for ARIA token
                                aria_token = await exchange_token_aria(user_access_token_local)
                                logger.info(f"✓ ARIA token acquired: {len(aria_token) if aria_token else 0} chars")
                                
                                # Add mcp_token and aria_token to JWT
                                logger.info(f"📦 Passing to JWT: mcp_token={len(obo_token) if obo_token else 0} chars, aria_token={len(aria_token) if aria_token else 0} chars")
                                jwt_token_with_mcp = add_mcp_token_to_jwt(jwt_token, obo_token, aria_token)
                        except Exception as e:
                            logger.warning(f"⚠️  Could not decode JWT token: {e}, will try to create new one with MCP and ARIA tokens")
                            # Try to create new JWT with MCP and ARIA tokens anyway
                            obo_token = await exchange_token_obo(user_access_token_local)
                            aria_token = await exchange_token_aria(user_access_token_local)
                            logger.info(f"📦 Exception path - Passing to JWT: mcp_token={len(obo_token) if obo_token else 0} chars, aria_token={len(aria_token) if aria_token else 0} chars")
                            jwt_token_with_mcp = add_mcp_token_to_jwt(jwt_token, obo_token, aria_token)
                            logger.info(f"✓ Created new JWT token with MCP and ARIA tokens embedded")
                    except Exception as e:
                        logger.warning(f"⚠️  Failed to create JWT with MCP/ARIA tokens: {e}, will use original JWT")
                        jwt_token_with_mcp = None
                
                # Use JWT with MCP token if available, otherwise use original
                final_auth_header = f"Bearer {jwt_token_with_mcp}" if jwt_token_with_mcp else auth_header
                
                # Call AGNO API /query-agent endpoint (works for all agents including Super Agent)
                # System will automatically: exchange OBO token, create MCP token, and send in Authorization header
                logger.info(f"📞 Calling query_agent with user_access_token: {'Yes' if user_access_token_local else 'No'}")
                logger.info(f"   Using JWT with MCP token: {'Yes' if jwt_token_with_mcp else 'No'}")
                agno_response = await agno_service.query_agent(
                    agent_name=agent_name,
                    query=request.query,
                    stream_id=stream_id,
                    session_id=session_id_str,
                    agent_id=agent_id_str,  # Pass agent_id to external DAT API
                    user_id=user_id_str,
                    attachments=agent_attachments if agent_attachments else None,
                    custom_fields=custom_fields,
                    user_context=user_context,
                    stream_config=stream_config,
                    authorization_header=final_auth_header,  # Use JWT with MCP token embedded
                    user_access_token=user_access_token_local,  # Azure AD token (for OBO exchange and MCP token creation)
                    obo_token=None  # Auto-generate from user_access_token
                )
                
                # Extract MCP token (OBO token) from AGNO service response
                obo_token_from_response = None
                if isinstance(agno_response, dict) and "mcp_token" in agno_response:
                    obo_token_from_response = agno_response.pop("mcp_token", None)
                    logger.info(f"✓ OBO token extracted from AGNO service response (query-agent)")
                
                # Get OBO token that was used (from exchange or cache)
                # We need to get the actual OBO token to embed in user's JWT
                from aldar_middleware.auth.obo_utils import exchange_token_obo
                try:
                    if user_access_token_local:
                        obo_token_for_jwt = await exchange_token_obo(user_access_token_local)
                    else:
                        obo_token_for_jwt = None
                        logger.warning("⚠️  No user_access_token available, cannot add mcp_token to JWT")
                except Exception as e:
                    logger.warning(f"⚠️  Failed to get OBO token for JWT: {e}")
                    obo_token_for_jwt = None
                
                # Extract stream_id and session_id from response
                stream_id_from_response = agno_response.get("stream_id") or stream_id
                session_id_from_response = agno_response.get("session_id") or session_id_str
                
                # IMPORTANT: Always use our database session.session_id, not the one from AGNO response
                # The AGNO API may return a different session_id, but we must use our own session
                # Update stream_id and response_type for successful query-agent call
                stream_id = stream_id_from_response
                response_type = "stream"
                
                # Store stream_id in the user message's tool_calls metadata
                # This allows us to retrieve it later when fetching messages
                if user_message and user_message.tool_calls:
                    user_message.tool_calls["stream_id"] = stream_id
                    user_message.tool_calls["streamId"] = stream_id  # Support both naming conventions
                    flag_modified(user_message, "tool_calls")
                    await db.commit()
                elif user_message:
                    # If tool_calls is None, create it with stream_id
                    user_message.tool_calls = {
                        "stream_id": stream_id,
                        "streamId": stream_id
                    }
                    await db.commit()
                
                logger.info(
                    f"Query-agent called successfully for agent={agent_name}, "
                    f"stream_id={stream_id}, agno_session_id={session_id_from_response}, "
                    f"db_session_id={session_id_str}, correlation_id={correlation_id}"
                )
                
                # Log warning if AGNO returned a different session_id (we'll still use ours)
                if session_id_from_response != session_id_str:
                    logger.warning(
                        f"AGNO API returned different session_id ({session_id_from_response}) "
                        f"than our database session_id ({session_id_str}). "
                        f"Using database session_id in response. correlation_id={correlation_id}"
                )
                
                # Set ai_response to None since we're using external API
                ai_response = None
                
            except Exception as e:
                # Capture the external API error for reporting
                error_message = str(e) if str(e) else f"{type(e).__name__}: Connection or timeout error"
                if not error_message or error_message.strip() == "":
                    error_message = f"{type(e).__name__}: Request failed (likely timeout or connection error)"
                
                agent_id_str = str(agent_record.public_id) if agent_record else None
                
                external_api_error = {
                    "source": "AGNO_API",
                    "message": error_message,
                    "agent_name": agent_name,
                    "agent_id": agent_id_str,
                    "endpoint_type": "query-agent",
                    "error_type": type(e).__name__
                }
                
                logger.error(
                    f"Error calling external AGNO API (query-agent): {error_message}, "
                    f"error_type={type(e).__name__}, agent={agent_name}, "
                    f"agent_id={agent_id_str}, correlation_id={correlation_id}, exception={repr(e)}"
                )
                
                # Fallback to AIService if query-agent fails
                ai_service = AIService()
                ai_response = await ai_service.generate_response(
                    user_message=request.query,
                    chat_id=str(session.id),
                    user_id=str(current_user.id),
                )
                response_text = ai_response.get("content")
                stream_id = ai_response.get("stream_id") or ai_response.get("streamId")
                response_type = "stream" if stream_id else "direct"
                error_text = ai_response.get("error")
        else:
            # Fallback to AIService if agent not found
            ai_service = AIService()
            ai_response = await ai_service.generate_response(
                user_message=request.query,
                chat_id=str(session.id),
                user_id=str(current_user.id),
            )
        
        # Only process ai_response if it exists (i.e., we used AIService fallback)
        if ai_response is not None:
            response_text = ai_response.get("content")
            if stream_id is None:
                stream_id = ai_response.get("stream_id") or ai_response.get("streamId")
            if response_type != "stream":
                response_type = "stream" if stream_id else "direct"
            error_text = ai_response.get("error")

        assistant_metadata: Dict[str, Any] = {}
        if request.custom_fields and request.custom_fields.get("agents_involved"):
            assistant_metadata["agents_involved"] = request.custom_fields["agents_involved"]

        ai_message_id: Optional[str] = None
        if response_type == "direct" and response_text:
            # Only create AI message for direct (non-streaming) responses
            ai_message = Message(
                session_id=session.id,
                user_id=current_user.id,
                agent_id=agent_id,
                content_type="text",
                content=response_text,
                role="assistant",
                tool_calls=assistant_metadata or None,
            )
            db.add(ai_message)
            # Update session's last_message_interaction_at when AI responds
            # This ensures the session stays in the correct time-based group
            # Use datetime.utcnow() for timezone-naive datetime (Session model uses TIMESTAMP WITHOUT TIME ZONE)
            now_utc = datetime.utcnow()
            session.last_message_interaction_at = now_utc
            session.updated_at = now_utc
            await db.commit()
            await db.refresh(ai_message)
            ai_message_id = str(ai_message.id)

            log_chat_message(
                chat_id=str(session.id),
                message_id=ai_message_id,
                message_type="assistant",
                role="assistant",
                content=response_text,
                user_id=str(current_user.id),
                username=current_user.username or current_user.email,
                tokens_used=ai_response.get("tokens_used") if ai_response else None,
                processing_time=ai_response.get("processing_time") if ai_response else None,
                correlation_id=correlation_id,
            )
        elif response_type == "stream" and stream_id:
            # Create placeholder assistant message so clients have a message_id immediately
            assistant_metadata.update(
                {
                    "stream_id": stream_id,
                    "status": ai_response.get("status") if ai_response else "streaming",
                }
            )
            ai_message = Message(
                session_id=session.id,
                user_id=current_user.id,
                agent_id=agent_id,
                content_type="stream",
                content=None,
                role="assistant",
                tool_calls=assistant_metadata,
            )
            db.add(ai_message)
            # Update session's last_message_interaction_at when AI responds (streaming)
            # This ensures the session stays in the correct time-based group
            # Use datetime.utcnow() for timezone-naive datetime (Session model uses TIMESTAMP WITHOUT TIME ZONE)
            now_utc = datetime.utcnow()
            session.last_message_interaction_at = now_utc
            session.updated_at = now_utc
            await db.commit()
            await db.refresh(ai_message)
            ai_message_id = str(ai_message.id)

            log_chat_message(
                chat_id=str(session.id),
                message_id=ai_message_id,
                message_type="assistant",
                role="assistant",
                content=None,
                user_id=str(current_user.id),
                username=current_user.username or current_user.email,
                tokens_used=ai_response.get("tokens_used") if ai_response else None,
                processing_time=ai_response.get("processing_time") if ai_response else None,
                correlation_id=correlation_id,
            )

        if attachments_metadata and attachment_ids:
            result = await db.execute(select(Attachment).where(Attachment.id.in_(attachment_ids)))
            for attachment in result.scalars():
                attachment.entity_type = "chat"
                attachment.entity_id = str(session.id)
                attachment.updated_at = datetime.utcnow()
                db.add(attachment)
            await db.commit()

        # Ensure session is refreshed from database to get latest state
        await db.refresh(session)
        
        # CRITICAL: Always use our database session.session_id, never the AGNO response session_id
        # This ensures the session_id in the response matches what's stored in our database
        db_session_id = session.session_id
        
        # Get user message ID (the message that was just sent)
        user_message_id = str(user_message.id)
        
        logger.info(
            f"Returning response with session_id={db_session_id}, stream_id={stream_id}, "
            f"user_message_id={user_message_id}, ai_message_id={ai_message_id}, correlation_id={correlation_id}"
        )

        # Get original JWT token from Authorization header
        original_jwt_token = None
        if http_request:
            auth_header = http_request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                original_jwt_token = auth_header[7:]
            elif auth_header:
                original_jwt_token = auth_header
        
        # Add mcp_token to original JWT token if we have OBO token
        updated_jwt_token = None
        if original_jwt_token and obo_token_for_jwt:
            try:
                logger.info("🔄 Adding mcp_token field to user's JWT token...")
                updated_jwt_token = add_mcp_token_to_jwt(original_jwt_token, obo_token_for_jwt)
            except Exception as e:
                logger.warning(f"⚠️  Failed to add mcp_token to JWT token: {e}")
                updated_jwt_token = None
        
        response_dict = {
            "success": True,
            "session_id": db_session_id,  # Always use database session.session_id
            "message_id": user_message_id,  # User message ID (the message that was just sent)
            "ai_message_id": ai_message_id,  # Assistant message ID (None for streaming until complete)
            "response_type": response_type,
            "stream_id": stream_id,
            "response": response_text if response_type == "direct" else None,
            "error": error_text,
            "external_api_error": external_api_error,  # Error from AGNO API if it failed
            "correlation_id": correlation_id,
        }
        
        # SECURITY: Do NOT include tokens in chat API responses
        # Tokens should only be returned by authentication endpoints
        # The mcp_token is already embedded in the user's JWT token stored in their session
        # Users should use their existing JWT token from auth endpoints
        if updated_jwt_token:
            logger.info("JWT token updated with mcp_token (not included in response for security)")
        
        return response_dict
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        await db.rollback()
        _raise_chat_error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="INTERNAL_ERROR",
            error_message="Failed to process chat message",
            details={"field": "server", "message": str(exc)},
        )


@router.get("/response/{stream_id}")
async def get_stream_response(
    stream_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Fetch aggregate response information for a streamed chat interaction."""
    correlation_id = get_correlation_id()

    await _enforce_chat_rate_limit(current_user)

    # First, try to find by stream_id in messages
    stream_filter = or_(
        Message.tool_calls["stream_id"].as_string() == stream_id,
        Message.tool_calls["streamId"].as_string() == stream_id,
    )

    stmt = (
        select(Message, Session)
        .join(Session, Message.session_id == Session.id)
        .where(stream_filter, Session.user_id == current_user.id)
        .order_by(Message.created_at)
    )

    result = await db.execute(stmt)
    rows = result.all()

    # If not found in messages, try to find by session_id (stream_id might be session_id)
    session = None
    messages: List[Message] = []
    if not rows:
        # Try to find session by stream_id (which might be session_id)
        from aldar_middleware.models.agent_runs import AgentRun
        from uuid import UUID
        
        try:
            session_uuid = UUID(stream_id)
            # Try as session id
            result = await db.execute(
                select(Session).where(
                    Session.id == session_uuid,
                    Session.user_id == current_user.id
                )
            )
            session = result.scalar_one_or_none()
        except ValueError:
            pass
        
        # If session found, get the latest completed run
        if session:
            result = await db.execute(
                select(AgentRun)
                .where(
                    AgentRun.session_id == session.id,
                    AgentRun.status == "completed"
                )
                .order_by(AgentRun.updated_at.desc())
                .limit(1)
            )
            agent_run = result.scalar_one_or_none()
            
            if agent_run and agent_run.content:
                # Return response from saved AgentRun
                return {
                    "success": True,
                    "stream_id": stream_id,
                    "status": "completed",
                    "response": agent_run.content,
                    "agents_involved": [{
                        "agent_id": str(agent_run.agent_id) if agent_run.agent_id else None,
                        "agent_name": agent_run.agent_name
                    }] if agent_run.agent_name else [],
                    "error": None,
                    "started_at": agent_run.created_at.isoformat() if agent_run.created_at else None,
                    "completed_at": agent_run.updated_at.isoformat() if agent_run.updated_at else None,
                    "correlation_id": correlation_id,
                }
    
    if not rows and not session:
        _raise_chat_error(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="STREAM_EXPIRED",
            error_message="Stream not found or has expired",
            details={"field": "stream_id", "message": "The requested stream is unavailable"},
        )

    if rows:
        messages: List[Message] = [row[0] for row in rows]
        session: Session = rows[0][1]
    elif session:
        messages = []

    if session:
        _ensure_session_active(session)

    stream_status = "in_progress"
    response_text: Optional[str] = None
    error_message: Optional[str] = None
    agents_accumulator: Dict[str, Dict[str, Any]] = {}

    for message in messages:
        metadata = message.tool_calls or {}
        agents = metadata.get("agents_involved") or metadata.get("agentsInvolved") or []
        if isinstance(agents, list):
            _merge_agent_activity(agents_accumulator, agents)

        message_status = metadata.get("status") or metadata.get("stream_status") or metadata.get("streamStatus")
        if message_status:
            stream_status = str(message_status).lower()

        if metadata.get("error"):
            error_message = metadata.get("error")
            stream_status = "failed"

        if message.content_type in ("assistant", "agent", "text"):
            response_text = message.content
            if not message_status and stream_status != "failed":
                stream_status = "completed"

    # If no response from messages, try to get from AgentRun
    if not response_text and session:
        from aldar_middleware.models.agent_runs import AgentRun
        
        result = await db.execute(
            select(AgentRun)
            .where(
                AgentRun.session_id == session.id,
                AgentRun.status == "completed"
            )
            .order_by(AgentRun.updated_at.desc())
            .limit(1)
        )
        agent_run = result.scalar_one_or_none()
        
        if agent_run and agent_run.content:
            response_text = agent_run.content
            stream_status = "completed"
            if agent_run.agent_name:
                agents_accumulator[agent_run.agent_name] = {
                    "agent_id": str(agent_run.agent_id) if agent_run.agent_id else None,
                    "agent_name": agent_run.agent_name
                }

    started_at = messages[0].created_at.isoformat() if messages and messages[0].created_at else None
    completed_at = None
    if stream_status == "completed":
        if messages and messages[-1].created_at:
            completed_at = messages[-1].created_at.isoformat()
        elif session:
            # Get from AgentRun if available
            from aldar_middleware.models.agent_runs import AgentRun
            result = await db.execute(
                select(AgentRun)
                .where(
                    AgentRun.session_id == session.id,
                    AgentRun.status == "completed"
                )
                .order_by(AgentRun.updated_at.desc())
                .limit(1)
            )
            agent_run = result.scalar_one_or_none()
            if agent_run and agent_run.updated_at:
                completed_at = agent_run.updated_at.isoformat()

    if stream_status == "failed" and not error_message:
        error_message = "Stream failed"

    return {
        "success": True,
        "stream_id": stream_id,
        "status": stream_status,
        "response": response_text,
        "agents_involved": list(agents_accumulator.values()),
        "error": error_message,
        "started_at": started_at,
        "completed_at": completed_at,
        "correlation_id": correlation_id,
    }


@router.post("/agents/runs/cancel")
async def cancel_agent_run(
    cancel_request: CancelAgentRunRequest,
    request: Request = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Cancel a running agent execution (distributed cancellation).
    
    This endpoint:
    - Calls Agno's native cancel mechanism to update the session table
    - Inserts a cancellation request into our database for distributed polling
    - ALWAYS updates runs_agent status to 'cancelled' (force=True)
    
    Key fix: Even if the run completed quickly before cancellation arrived, we still insert
    into cancellation_requests and force-update runs_agent.
    
    It automatically handles:
    - Azure AD token refresh from stored refresh token
    - OBO (On-Behalf-Of) token exchange
    - MCP token creation and embedding in JWT
    - Authorization header forwarding
    
    Request Body:
        agent_id: The agent's public ID
        run_id: The agno_run_id from RunStarted event
        
    Args:
        cancel_request: Request body with agent_id and run_id
        request: FastAPI request object (for extracting headers)
        current_user: Current authenticated user
        db: Database session
        
    Returns:
        Cancellation status
    """
    correlation_id = get_correlation_id()
    
    # Extract agent_id and run_id from request body
    agent_id = cancel_request.agent_id
    run_id = cancel_request.run_id
    
    try:
        await _enforce_chat_rate_limit(current_user)
        
        # Extract authorization header from request to forward to AGNO API
        auth_header = None
        if request:
            auth_header = request.headers.get("authorization")
        
        # Get Azure AD user access token for OBO exchange
        # Auto-refresh from user's stored refresh token (automatic flow)
        user_access_token_local = None
        if hasattr(current_user, 'azure_ad_refresh_token') and current_user.azure_ad_refresh_token:
            # AUTO-REFRESH: Try to get Azure AD token from refresh token (automatic flow)
            logger.info(" Auto-refreshing Azure AD token from stored refresh token for OBO exchange...")
            try:
                from aldar_middleware.auth.azure_ad import azure_ad_auth
                import httpx
                
                # Refresh token with OBO scope
                refresh_data = {
                    "client_id": settings.azure_client_id,
                    "client_secret": settings.azure_client_secret,
                    "refresh_token": current_user.azure_ad_refresh_token,
                    "grant_type": "refresh_token",
                    "scope": f"openid profile offline_access {settings.azure_client_id}/.default"
                }
                
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{azure_ad_auth.authority}/oauth2/v2.0/token",
                        data=refresh_data
                    )
                    
                    if response.status_code == 200:
                        token_response = response.json()
                        user_access_token_local = token_response.get("access_token")
                        if user_access_token_local:
                            logger.info("✓ Azure AD user access token auto-obtained from refresh token for OBO exchange")
                            # Update refresh token if new one provided
                            new_refresh_token = token_response.get("refresh_token")
                            if new_refresh_token:
                                current_user.azure_ad_refresh_token = new_refresh_token
                                await db.commit()
                        else:
                            logger.warning(" Refresh token response did not contain access_token")
                    else:
                        logger.warning(f" Failed to auto-refresh Azure AD token: {response.status_code} - {response.text}")
            except Exception as e:
                logger.warning(f" Failed to auto-refresh Azure AD token from refresh token: {e}")
        else:
            logger.warning(" No Azure AD refresh token stored for user - OBO token exchange will be skipped")
        
        if user_access_token_local:
            logger.info(f"✓ Azure AD user access token available for OBO exchange (length: {len(user_access_token_local)})")
        else:
            logger.warning(" No Azure AD user access token available - OBO token exchange will be skipped")
        
        # First, check agent run status
        from aldar_middleware.orchestration.agno import agno_service
        
        logger.info(f" Checking agent run status before cancellation...")
        logger.info(f"   agent_id={agent_id}, run_id={run_id}, user_id={str(current_user.id)}, correlation_id={correlation_id}")
        
        try:
            run_status = await agno_service.get_agent_run_status(
                agent_id=agent_id,
                run_id=run_id,
                user_id=str(current_user.id),
                authorization_header=auth_header,
                user_access_token=user_access_token_local,
                obo_token=None  # Auto-generate from user_access_token
            )
            logger.info(f"✓ Agent run status retrieved: {run_status.get('status', 'unknown')}")
        except Exception as status_error:
            logger.warning(f" Failed to get agent run status (proceeding with cancellation): {status_error}")
            run_status = None
        
        # Call AGNO API to cancel agent run
        # System will automatically: exchange OBO token, create MCP token, and send in Authorization header
        logger.info(f" Calling cancel_agent_run with user_access_token: {'Yes' if user_access_token_local else 'No'}")
        logger.info(f"   agent_id={agent_id}, run_id={run_id}, user_id={str(current_user.id)}, correlation_id={correlation_id}")
        
        data = await agno_service.cancel_agent_run(
            agent_id=agent_id,
            run_id=run_id,
            user_id=str(current_user.id),
            authorization_header=auth_header,
            user_access_token=user_access_token_local,
            obo_token=None  # Auto-generate from user_access_token
        )
        
        logger.info(f" Agent run cancelled successfully: agent_id={agent_id}, run_id={run_id}, correlation_id={correlation_id}")
        
        return {
            "success": True,
            "message": "Reasoning stopped. Response generation has been cancelled.",
            "agent_id": agent_id,
            "run_id": run_id,
            "run_status": run_status,
            "correlation_id": correlation_id,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error canceling agent run: {str(e)}, agent_id={agent_id}, run_id={run_id}, correlation_id={correlation_id}")
        _raise_chat_error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="CANCEL_FAILED",
            error_message="Failed to cancel agent run",
            details={"field": "server", "message": str(e)},
        )


@router.post("/teams/runs/cancel")
async def cancel_team_run(
    cancel_request: CancelTeamRunRequest,
    request: Request = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Cancel a running team execution (distributed cancellation).
    
    This endpoint:
    - Calls Agno's native cancel mechanism to update the session table
    - Inserts a cancellation request into our database for distributed polling
    - ALWAYS updates runs_agent status to 'cancelled' (force=True)
    
    Key fix: Even if the run completed quickly before cancellation arrived, we still insert
    into cancellation_requests and force-update runs_agent.
    
    It automatically handles:
    - Azure AD token refresh from stored refresh token
    - OBO (On-Behalf-Of) token exchange
    - MCP token creation and embedding in JWT
    - Authorization header forwarding
    
    Note: Cancellation may not be immediate for all operations.
    
    Request Body:
        team_id: The team's public ID
        run_id: The agno_run_id from TeamRunStarted event
        
    Args:
        cancel_request: Request body with team_id and run_id
        request: FastAPI request object (for extracting headers)
        current_user: Current authenticated user
        db: Database session
        
    Returns:
        Cancellation status
    """
    correlation_id = get_correlation_id()
    
    # Extract team_id and run_id from request body
    team_id = cancel_request.team_id
    run_id = cancel_request.run_id
    
    try:
        await _enforce_chat_rate_limit(current_user)
        
        # Extract authorization header from request to forward to AGNO API
        auth_header = None
        if request:
            auth_header = request.headers.get("authorization")
        
        # Get Azure AD user access token for OBO exchange
        # Auto-refresh from user's stored refresh token (automatic flow)
        user_access_token_local = None
        if hasattr(current_user, 'azure_ad_refresh_token') and current_user.azure_ad_refresh_token:
            # AUTO-REFRESH: Try to get Azure AD token from refresh token (automatic flow)
            logger.info(" Auto-refreshing Azure AD token from stored refresh token for OBO exchange...")
            try:
                from aldar_middleware.auth.azure_ad import azure_ad_auth
                import httpx
                
                # Refresh token with OBO scope
                refresh_data = {
                    "client_id": settings.azure_client_id,
                    "client_secret": settings.azure_client_secret,
                    "refresh_token": current_user.azure_ad_refresh_token,
                    "grant_type": "refresh_token",
                    "scope": f"openid profile offline_access {settings.azure_client_id}/.default"
                }
                
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{azure_ad_auth.authority}/oauth2/v2.0/token",
                        data=refresh_data
                    )
                    
                    if response.status_code == 200:
                        token_response = response.json()
                        user_access_token_local = token_response.get("access_token")
                        if user_access_token_local:
                            logger.info("✓ Azure AD user access token auto-obtained from refresh token for OBO exchange")
                            # Update refresh token if new one provided
                            new_refresh_token = token_response.get("refresh_token")
                            if new_refresh_token:
                                current_user.azure_ad_refresh_token = new_refresh_token
                                await db.commit()
                        else:
                            logger.warning("  Refresh token response did not contain access_token")
                    else:
                        logger.warning(f"  Failed to auto-refresh Azure AD token: {response.status_code} - {response.text}")
            except Exception as e:
                logger.warning(f"  Failed to auto-refresh Azure AD token from refresh token: {e}")
        else:
            logger.warning("  No Azure AD refresh token stored for user - OBO token exchange will be skipped")
        
        if user_access_token_local:
            logger.info(f"✓ Azure AD user access token available for OBO exchange (length: {len(user_access_token_local)})")
        else:
            logger.warning("  No Azure AD user access token available - OBO token exchange will be skipped")
        
        # First, check team run status
        from aldar_middleware.orchestration.agno import agno_service
        
        logger.info(f" Checking team run status before cancellation...")
        logger.info(f"   team_id={team_id}, run_id={run_id}, user_id={str(current_user.id)}, correlation_id={correlation_id}")
        
        try:
            run_status = await agno_service.get_team_run_status(
                team_id=team_id,
                run_id=run_id,
                user_id=str(current_user.id),
                authorization_header=auth_header,
                user_access_token=user_access_token_local,
                obo_token=None  # Auto-generate from user_access_token
            )
            logger.info(f"✓ Team run status retrieved: {run_status.get('status', 'unknown')}")
        except Exception as status_error:
            logger.warning(f" Failed to get team run status (proceeding with cancellation): {status_error}")
            run_status = None
        
        # Call AGNO API to cancel team run
        # System will automatically: exchange OBO token, create MCP token, and send in Authorization header
        logger.info(f" Calling cancel_team_run with user_access_token: {'Yes' if user_access_token_local else 'No'}")
        logger.info(f"   team_id={team_id}, run_id={run_id}, user_id={str(current_user.id)}, correlation_id={correlation_id}")
        
        data = await agno_service.cancel_team_run(
            team_id=team_id,
            run_id=run_id,
            user_id=str(current_user.id),
            authorization_header=auth_header,
            user_access_token=user_access_token_local,
            obo_token=None  # Auto-generate from user_access_token
        )
        
        logger.info(f" Team run cancelled successfully: team_id={team_id}, run_id={run_id}, correlation_id={correlation_id}")
        
        return {
            "success": True,
            "message": "Reasoning stopped. Response generation has been cancelled.",
            "team_id": team_id,
            "run_id": run_id,
            "run_status": run_status,
            "correlation_id": correlation_id,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error canceling team run: {str(e)}, team_id={team_id}, run_id={run_id}, correlation_id={correlation_id}")
        _raise_chat_error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="CANCEL_FAILED",
            error_message="Failed to cancel team run",
            details={"field": "server", "message": str(e)},
        )


def _count_unique_agents(metadata: Dict[str, Any]) -> int:
    agent_ids: set[str] = set()
    primary_agent = metadata.get("agent_id") or metadata.get("agentId")
    if primary_agent:
        agent_ids.add(str(primary_agent))

    for key in ("agents_used", "agentsUsed", "agent_history", "agentHistory"):
        entries = metadata.get(key)
        if isinstance(entries, list):
            for item in entries:
                candidate: Optional[str] = None
                if isinstance(item, dict):
                    candidate = (
                        item.get("agent_id")
                        or item.get("agentId")
                        or item.get("id")
                        or item.get("agent")
                    )
                elif isinstance(item, str):
                    candidate = item
                if candidate:
                    agent_ids.add(str(candidate))
    return len(agent_ids)


def _merge_agent_activity(
    accumulator: Dict[str, Dict[str, Any]],
    agents: List[Dict[str, Optional[str]]],
    default_role: Optional[str] = None,
) -> None:
    for agent in agents:
        agent_id = agent.get("agent_id") or agent.get("agentId") or agent.get("id")
        if not agent_id:
            continue
        agent_key = str(agent_id)
        entry = accumulator.setdefault(
            agent_key,
            {
                "agent_id": agent_key,
                "agent_name": agent.get("agent_name") or agent.get("agentName") or agent.get("name"),
                "role": agent.get("role") or default_role,
                "actions": [],
            },
        )
        if not entry.get("agent_name") and agent.get("agent_name"):
            entry["agent_name"] = agent.get("agent_name")
        if not entry.get("role") and agent.get("role"):
            entry["role"] = agent.get("role")
        action = agent.get("action") or agent.get("activity")
        if action and action not in entry["actions"]:
            entry["actions"].append(action)


async def _update_agent_last_used(agent_id: int, db: AsyncSession) -> None:
    """Update the last_used timestamp for an agent."""
    try:
        result = await db.execute(
            update(Agent)
            .where(Agent.id == agent_id)
            .values(last_used=datetime.utcnow())
        )
        await db.flush()  # Flush to update without committing (commit happens in caller)
        rows_affected = result.rowcount
        if rows_affected > 0:
            logger.info(f"Updated last_used for agent {agent_id}")
        else:
            logger.warning(f"No agent found with id {agent_id} to update last_used")
    except Exception as e:
        # Log error but don't fail the request if last_used update fails
        logger.warning(f"Failed to update last_used for agent {agent_id}: {str(e)}")


async def _resolve_agent(agent_identifier: Optional[str], db: AsyncSession) -> Tuple[int, str]:
    """Resolve agent identifier to (agent.id, agent_id_string) tuple."""
    if not agent_identifier:
        result = await db.execute(select(Agent).where(Agent.name == "Super Agent"))
        super_agent = result.scalar_one_or_none()
        if super_agent:
            return (super_agent.id, str(super_agent.public_id))
        # Create default if it doesn't exist
        default_agent = Agent(name="Super Agent", agent_id="super-agent", is_enabled=True)
        db.add(default_agent)
        await db.flush()
        return (default_agent.id, str(default_agent.public_id))

    trimmed = agent_identifier.strip()
    if not trimmed:
        return await _resolve_agent(None, db)

    try:
        agent_uuid = UUID(trimmed)
    except (ValueError, TypeError):
        _raise_chat_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="VALIDATION_ERROR",
            error_message="Agent identifier must be a valid UUID",
            details={"field": "agent_id", "message": "Provide a valid UUID"},
        )

    agent_query = select(Agent).where(Agent.public_id == agent_uuid)
    agent = (await db.execute(agent_query)).scalar_one_or_none()
    if agent:
        return (agent.id, str(agent.public_id))

    _raise_chat_error(
        status_code=status.HTTP_404_NOT_FOUND,
        error_code="AGENT_NOT_FOUND",
        error_message="Agent identifier is invalid",
        details={"field": "agent_id", "message": "No matching agent"},
    )


async def _batch_resolve_agent_names(
    agent_ids: List[str],
    db: AsyncSession,
    cache: Dict[str, str]
) -> Dict[str, str]:
    """
    Resolve multiple agent IDs to names in a single batch query.
    Updates the provided cache and returns the resolved names.
    """
    # Find agent IDs not in cache
    missing_ids = [aid for aid in agent_ids if aid not in cache]
    
    if not missing_ids:
        return cache
    
    try:
        from uuid import UUID as UUIDType
        uuid_ids: List[UUID] = []
        legacy_ids: List[str] = []
        
        for agent_id_str in missing_ids:
            try:
                uuid_ids.append(UUIDType(agent_id_str))
            except (ValueError, TypeError):
                legacy_ids.append(agent_id_str)
        
        # Query by UUID (public_id)
        if uuid_ids:
            agent_result = await db.execute(
                select(Agent).where(Agent.public_id.in_(uuid_ids))
            )
            for agent in agent_result.scalars().all():
                cache[str(agent.public_id)] = agent.name
        
        # Query by legacy agent_id field
        if legacy_ids:
            agent_result = await db.execute(
                select(Agent).where(Agent.agent_id.in_(legacy_ids))
            )
            for agent in agent_result.scalars().all():
                cache[agent.agent_id] = agent.name
    except Exception as e:
        logger.warning(f"Failed to batch resolve agent names: {str(e)}")
        # Rollback the transaction to prevent invalid state
        await db.rollback()
    
    return cache


async def _build_grouped_session_summary(
    *,
    db: AsyncSession,
    base_filters: List[Any],
    activity_field: Any,  # Use the same activity_field expression from the endpoint
    limit: int,
    offset: int,
    group_id: Optional[str] = None,
    today_start: datetime,
    today_end: datetime,
    previous_7_days_start: datetime,
    previous_7_days_end: datetime,
    previous_30_days_start: datetime,
    previous_30_days_end: datetime,
    previous_year_start: datetime,
    previous_year_end: datetime,
    older_end: datetime,
    date_from: Optional[datetime] = None,  # Date filter - applied using sort_activity
    date_to: Optional[datetime] = None,  # Date filter - applied using sort_activity
    current_user: Optional[User] = None,
) -> Tuple[List[Dict[str, Any]], int, bool]:
    # Use the activity_field passed from the endpoint to ensure consistency with date filters
    message_stats_subq = (
        select(
            Message.session_id.label("session_id"),
            func.count(Message.id).label("message_count"),
            func.max(Message.created_at).label("last_message_at"),
        )
        .where(Message.content_type != "system")
        .group_by(Message.session_id)
        .subquery()
    )

    # Use the same activity calculation for grouping as we use for sorting/display
    # This ensures sessions are grouped based on their actual last activity (last message or session update)
    # Priority: last_message_at (from Message table) > last_message_interaction_at > updated_at > created_at
    sort_activity = func.coalesce(message_stats_subq.c.last_message_at, activity_field)
    
    # Apply date filters using sort_activity (includes Message timestamps) to match grouping logic
    # This ensures date filters use the same activity calculation as grouping
    if date_from:
        base_filters.append(sort_activity >= date_from)
    if date_to:
        base_filters.append(sort_activity <= date_to)
    
    favourite_clause = _favorite_flag_clause()
    not_favourite_clause = _not_favorite_flag_clause()

    groups_map: Dict[str, List[Any]] = {
        "favourites": [
            favourite_clause
        ],
        "today": [
            and_(
                sort_activity >= today_start,
                sort_activity <= today_end
            ),
            not_favourite_clause  # Exclude favorites from time-based groups
        ],
        "previous_7_days": [
            and_(
                sort_activity >= previous_7_days_start,
                sort_activity <= previous_7_days_end
            ),
            not_favourite_clause  # Exclude favorites from time-based groups
        ],
        "previous_30_days": [
            and_(
                sort_activity >= previous_30_days_start,
                sort_activity < previous_30_days_end
            ),
            not_favourite_clause  # Exclude favorites from time-based groups
        ],
        "previous_year": [
            and_(
                sort_activity >= previous_year_start,
                sort_activity < previous_year_end
            ),
            not_favourite_clause  # Exclude favorites from time-based groups
        ],
        "older": [
            sort_activity < older_end,
            not_favourite_clause  # Exclude favorites from time-based groups
        ],
    }

    # Group titles and ID mapping
    group_titles = {
        "favourites": "Favourites",
        "today": "Today",
        "previous_7_days": "Previous 7 Days",
        "previous_30_days": "Previous 30 Days",
        "previous_year": "Previous Year",
        "older": "Older",
    }
    
    # Map internal group names to API group IDs (use hyphens for consistency)
    group_id_map = {
        "favourites": "favourites",
        "today": "today",
        "previous_7_days": "previous-7-days",
        "previous_30_days": "previous-30-days",
        "previous_year": "previous-year",
        "older": "older",
    }
    
    # Reverse map: API group ID -> internal group name
    api_id_to_internal = {v: k for k, v in group_id_map.items()}
    
    # If group_id is provided, find the internal group name
    target_internal_group = None
    if group_id:
        target_internal_group = api_id_to_internal.get(group_id) or group_id
    
    results: List[Dict[str, Any]] = []
    global_has_more = False
    total_count = 0
    # sort_activity is already defined above in groups_map section
    # Calculate page number from offset and limit
    page = (offset // limit) + 1 if limit > 0 else 1
    
    # OPTIMIZED: Cache for agent names to avoid repeated queries
    # This will be populated lazily as we encounter agents
    agent_name_map: Dict[str, str] = {}

    for group_name, extra_filters in groups_map.items():
        # Combine base_filters (includes date filters) with group-specific filters
        filters = list(base_filters) + extra_filters
        # Date filters in base_filters use sort_activity (includes Message timestamps)
        # Join with message_stats_subq so sort_activity can be used in WHERE clause
        count_stmt = (
            select(func.count())
            .select_from(Session)
            .outerjoin(message_stats_subq, message_stats_subq.c.session_id == Session.id)
            .where(*filters)
        )
        group_total = (await db.execute(count_stmt)).scalar() or 0
        total_count += group_total
        

        items: List[Dict[str, Any]] = []
        
        # Apply pagination only to the target group, or all groups if no group_id specified
        group_offset = offset if (target_internal_group is None or group_name == target_internal_group) else 0
        group_limit = limit if (target_internal_group is None or group_name == target_internal_group) else limit
        
        if group_total > 0:
            # Select session_name explicitly to ensure we get fresh data from database
            stmt = (
                select(
                    Session,
                    Agent,
                    Session.session_name,  # Explicitly select session_name to ensure fresh data
                    sort_activity.label("last_activity"),
                    func.coalesce(message_stats_subq.c.message_count, 0).label("message_count"),
                )
                .join(Agent, Session.agent_id == Agent.id)
                .outerjoin(message_stats_subq, message_stats_subq.c.session_id == Session.id)
                .where(*filters)
                .order_by(desc(sort_activity))
                .offset(group_offset)
                .limit(group_limit)
            )

            rows = await db.execute(stmt)
            rows_list = list(rows)  # Convert to list so we can iterate twice if needed

            # Collect session IDs that might have multiple agents
            # Check runs for all sessions with agent info, not just team runs
            # This ensures we catch Super Agent sessions that delegate to multiple agents
            session_ids_to_check: List[str] = []
            session_metadata_map: Dict[str, Dict[str, Any]] = {}

            for row in rows_list:
                # Unpack: Session, Agent, session_name (explicitly selected), last_activity, message_count
                session_obj, agent_obj, session_name_fresh, last_activity, _msg_count = row
                metadata = session_obj.session_metadata or {}
                session_id_str = str(session_obj.session_id)
                session_public_id_str = str(session_obj.public_id)
                
                # Extract agent_id and agent_name from metadata (for checking)
                agent_id = metadata.get("agent_id") or metadata.get("agentId")
                agent_name = metadata.get("agent_name") or metadata.get("agentName")
                
                # If agent_name not in metadata, resolve from Agent table
                if agent_id and not agent_name:
                    agent_name = agent_name_map.get(str(agent_id))
                
                # Check if this might be a team run (has team_id/team_name in metadata)
                team_id = metadata.get("team_id") or metadata.get("teamId")
                team_name = metadata.get("team_name") or metadata.get("teamName")
                
                # Store metadata for later use
                session_metadata_map[session_id_str] = metadata
                session_metadata_map[session_public_id_str] = metadata
                
                # Check for runs if:
                # 1. It's a team run (has team_id/team_name)
                # 2. Agent name suggests it's a router/team/super agent
                # 3. Has agent_id (might delegate to other agents)
                should_check_runs = (
                    team_id or 
                    team_name or 
                    (agent_name and ("router" in agent_name.lower() or "team" in agent_name.lower() or "super" in agent_name.lower())) or
                    agent_id  # Check runs for any session with an agent (they might delegate)
                )
                
                if should_check_runs:
                    session_ids_to_check.append(session_id_str)
                    session_ids_to_check.append(session_public_id_str)
            
            # Batch query runs for sessions that might have multiple agents
            # Query the most recent run for each session to get events and member_responses
            runs_map: Dict[str, Dict[str, Any]] = {}  # session_id -> run data
            
            # Create a map of all session IDs (both id and public_id) for efficient lookup
            session_id_mapping: Dict[str, Tuple[str, str]] = {}  # session_id -> (session.id, session.public_id)
            for row in rows_list:
                session_obj, agent_obj, _, _, _ = row
                session_id_str = str(session_obj.session_id)
                session_public_id_str = str(session_obj.public_id)
                session_id_mapping[session_id_str] = (session_id_str, session_public_id_str)
                session_id_mapping[session_public_id_str] = (session_id_str, session_public_id_str)
            
            if session_ids_to_check:
                try:
                    from aldar_middleware.models.run import Run
                    from aldar_middleware.models.event import Event
                    
                    # OPTIMIZATION: Batch query runs for all sessions at once instead of N+1 queries
                    # Collect unique session IDs to query
                    unique_session_ids = set()
                    for session_id_check in session_ids_to_check:
                        if session_id_check in session_id_mapping:
                            session_id_str, session_public_id_str = session_id_mapping[session_id_check]
                            unique_session_ids.add(session_id_str)
                            unique_session_ids.add(session_public_id_str)
                        else:
                            unique_session_ids.add(session_id_check)
                    
                    # Batch query: Get most recent run for each session
                    # Use a window function approach or subquery to get latest run per session
                    from sqlalchemy import distinct
                    runs_subq = (
                        select(
                            Run.run_id,
                            Run.session_id,
                            Run.agent_id,
                            Run.agent_name,
                            Run.content,
                            func.row_number().over(
                                partition_by=Run.session_id,
                                order_by=desc(Run.created_at)
                            ).label("rn")
                        )
                        .where(Run.session_id.in_(list(unique_session_ids)))
                        .subquery()
                    )
                    
                    runs_query = select(runs_subq).where(runs_subq.c.rn == 1)
                    runs_result = await db.execute(runs_query)
                    runs_list = list(runs_result.all())
                    
                    # Collect run_ids to batch query events
                    run_ids = [run.run_id for run in runs_list if run.run_id]
                    
                    # OPTIMIZATION: Batch query all events at once
                    events_map: Dict[str, List[Event]] = {}
                    if run_ids:
                        events_query = select(Event).where(
                            Event.run_id.in_(run_ids)
                        ).order_by(Event.created_at)
                        events_result = await db.execute(events_query)
                        all_events = list(events_result.scalars().all())
                        
                        # Group events by run_id
                        for event in all_events:
                            if event.run_id not in events_map:
                                events_map[event.run_id] = []
                            events_map[event.run_id].append(event)
                    
                    # Process each run
                    for run_row in runs_list:
                        run_id = run_row.run_id
                        session_id_for_run = run_row.session_id
                        events = events_map.get(run_id, [])
                        
                        # Convert events to dict format
                        events_data = []
                        for event in events:
                            event_data = {
                                "created_at": int(event.created_at.timestamp()) if event.created_at else None,
                                "event": event.event_type,
                                "agent_id": str(event.agent_id) if event.agent_id else None,
                                "agent_name": event.agent_name,
                            }
                            # Try to extract team info and other data from content if it's JSON
                            if event.content:
                                try:
                                    import json
                                    content_data = json.loads(event.content) if isinstance(event.content, str) else event.content
                                    if isinstance(content_data, dict):
                                        # Extract team info
                                        event_data["team_id"] = content_data.get("team_id") or content_data.get("teamId")
                                        event_data["team_name"] = content_data.get("team_name") or content_data.get("teamName")
                                        # Also check if agent info is in content (sometimes events store it there)
                                        if not event_data["agent_id"]:
                                            event_data["agent_id"] = str(content_data.get("agent_id") or content_data.get("agentId") or "")
                                        if not event_data["agent_name"]:
                                            event_data["agent_name"] = content_data.get("agent_name") or content_data.get("agentName")
                                except:
                                    pass
                            events_data.append(event_data)
                        
                        # Get member_responses from run content (if stored as JSON)
                        member_responses = []
                        if run_row.content:
                            try:
                                import json
                                content_data = json.loads(run_row.content) if isinstance(run_row.content, str) else run_row.content
                                if isinstance(content_data, dict):
                                    member_responses = content_data.get("member_responses") or content_data.get("memberResponses") or []
                            except:
                                pass
                        
                        # Try to get team info from run content or events
                        team_id_from_run = None
                        team_name_from_run = None
                        
                        # Check run content for team info
                        if run_row.content:
                            try:
                                import json
                                content_data = json.loads(run_row.content) if isinstance(run_row.content, str) else run_row.content
                                if isinstance(content_data, dict):
                                    team_id_from_run = content_data.get("team_id") or content_data.get("teamId")
                                    team_name_from_run = content_data.get("team_name") or content_data.get("teamName")
                            except:
                                pass
                        
                        # Extract team info from events
                        for event in events_data:
                            if event.get("team_id") and not team_id_from_run:
                                team_id_from_run = event.get("team_id")
                            if event.get("team_name") and not team_name_from_run:
                                team_name_from_run = event.get("team_name")
                        
                        run_data_entry = {
                            "run_id": run_id,
                            "team_id": team_id_from_run,
                            "team_name": team_name_from_run,
                            "agent_id": str(run_row.agent_id) if run_row.agent_id else None,
                            "agent_name": run_row.agent_name,
                            "events": events_data,
                            "member_responses": member_responses if isinstance(member_responses, list) else [],
                        }
                        
                        # Store with all possible session ID formats for easy lookup
                        # IMPORTANT: Append to list, don't overwrite! A session can have multiple runs
                        if session_id_for_run in session_id_mapping:
                            session_id_str, session_public_id_str = session_id_mapping[session_id_for_run]
                            # Initialize list if not exists, then append
                            if session_id_str not in runs_map:
                                runs_map[session_id_str] = []
                            if session_public_id_str not in runs_map:
                                runs_map[session_public_id_str] = []
                            runs_map[session_id_str].append(run_data_entry)
                            runs_map[session_public_id_str].append(run_data_entry)
                        else:
                            # Fallback: store with the session_id_for_run key
                            if session_id_for_run not in runs_map:
                                runs_map[session_id_for_run] = []
                            runs_map[session_id_for_run].append(run_data_entry)
                except Exception as e:
                    logger.warning(f"Error querying runs for sessions: {str(e)}")
                    # Continue without runs data - don't break the request
            
            # For sessions that don't have complete run data, try querying agno_sessions table first
            # This is faster than AGNO API and has complete data with member_responses
            sessions_needing_data: List[Tuple[str, str, Dict[str, Any]]] = []  # List of (session_id, session_public_id, metadata) tuples
            for row in rows_list:
                session_obj, agent_obj, _, _, _ = row
                session_id_str = str(session_obj.session_id)
                session_public_id_str = str(session_obj.public_id)
                metadata = session_obj.session_metadata or {}
                
                # Check if this is a team run or Super Agent session
                agent_id = metadata.get("agent_id") or metadata.get("agentId")
                agent_name = metadata.get("agent_name") or metadata.get("agentName")
                team_id = metadata.get("team_id") or metadata.get("teamId")
                team_name = metadata.get("team_name") or metadata.get("teamName")
                
                # Check if agent_name suggests it's a Super Agent/team
                is_super_agent = agent_name and ("super" in agent_name.lower() or "router" in agent_name.lower() or "team" in agent_name.lower())
                
                # Check if we already have run data with member_responses or events with multiple agents
                runs_list = runs_map.get(session_id_str) or runs_map.get(session_public_id_str)
                has_member_responses = False
                unique_agents_in_events = set()

                # Check ALL runs for member_responses and collect unique agents from events
                if runs_list:
                    if not isinstance(runs_list, list):
                        runs_list = [runs_list]  # Handle legacy single run format

                    for run_data in runs_list:
                        if isinstance(run_data, dict):
                            # Check for member_responses
                            member_responses = run_data.get("member_responses", [])
                            if member_responses and len(member_responses) > 0:
                                has_member_responses = True

                            # Check events for multiple agents
                            events = run_data.get("events", [])
                            for event in events:
                                if isinstance(event, dict):
                                    event_agent_id = event.get("agent_id") or event.get("agentId")
                                    event_agent_name = event.get("agent_name") or event.get("agentName")
                                    if event_agent_id:
                                        unique_agents_in_events.add(str(event_agent_id))
                                    elif event_agent_name:
                                        unique_agents_in_events.add(event_agent_name)

                has_multiple_agents = len(unique_agents_in_events) > 1
                
                # If it's a team run, Super Agent, or we don't have complete data, try to get it from agno_sessions
                # Also check sessions with agent_id that might delegate (like Super Agent)
                if (team_id or team_name or is_super_agent or agent_id) and not (has_member_responses or has_multiple_agents):
                    sessions_needing_data.append((session_id_str, session_public_id_str, metadata))
            
            # First, try querying agno_sessions table for runs data (faster than API)
            # OPTIMIZATION: Batch query all agno_sessions at once instead of N+1 queries
            if sessions_needing_data:
                try:
                    from sqlalchemy import text
                    
                    # Limit to avoid too many queries, but batch them all at once
                    limited_sessions = sessions_needing_data[:50]  # Increased limit since we're batching
                    
                    # Collect all session IDs to query (both id and public_id)
                    all_session_ids_to_query = set()
                    session_id_to_metadata_map = {}  # Map to track which metadata belongs to which session
                    
                    for session_id_str, session_public_id_str, metadata in limited_sessions:
                        all_session_ids_to_query.add(session_id_str)
                        all_session_ids_to_query.add(session_public_id_str)
                        # Store metadata for both IDs
                        session_id_to_metadata_map[session_id_str] = (session_id_str, session_public_id_str, metadata)
                        session_id_to_metadata_map[session_public_id_str] = (session_id_str, session_public_id_str, metadata)
                    
                    if all_session_ids_to_query:
                        # Batch query: Get all agno_sessions in one query
                        # Use DISTINCT ON to get the most recent row per session_id
                        # Note: PostgreSQL requires session_id in ORDER BY when using DISTINCT ON
                        session_ids_list = list(all_session_ids_to_query)
                        
                        # Use IN clause with tuple for better compatibility
                        # Build placeholders for IN clause
                        placeholders = ','.join([f':session_id_{i}' for i in range(len(session_ids_list))])
                        agno_batch_query = text(f"""
                            SELECT DISTINCT ON (session_id) 
                                session_id, user_id, runs
                            FROM agno_sessions
                            WHERE session_id IN ({placeholders})
                            ORDER BY session_id, created_at DESC
                        """)
                        
                        # Build params dict with indexed keys
                        agno_batch_params = {f'session_id_{i}': sid for i, sid in enumerate(session_ids_list)}
                        agno_batch_result = await db.execute(agno_batch_query, agno_batch_params)
                        agno_rows = agno_batch_result.all()
                        
                        # Process all results
                        for agno_row in agno_rows:
                            agno_session_id = agno_row[0]
                            runs_data = agno_row[2]  # runs column
                            
                            # Get the corresponding session metadata
                            session_info = session_id_to_metadata_map.get(agno_session_id)
                            if not session_info:
                                continue
                            
                            session_id_str, session_public_id_str, metadata = session_info
                            
                            if runs_data and isinstance(runs_data, list):
                                # Store ALL runs from agno_sessions, not just the first one with data
                                # This ensures all agents involved are captured
                                runs_map[session_id_str] = runs_data
                                runs_map[session_public_id_str] = runs_data
                except Exception as e:
                    logger.warning(f"Error batch querying agno_sessions table: {str(e)}")
            
            # For sessions that still don't have member_responses, try fetching from AGNO API
            # This ensures we get complete agent information for Super Agent sessions
            sessions_needing_agno_fetch: List[Tuple[str, str]] = []  # List of (session_id, session_public_id) tuples
            for row in rows_list:
                session_obj, agent_obj, _, _, _ = row
                session_id_str = str(session_obj.session_id)
                session_public_id_str = str(session_obj.public_id)
                metadata = session_obj.session_metadata or {}
                
                # Check if this is a team run or Super Agent session
                agent_name = metadata.get("agent_name") or metadata.get("agentName")
                team_id = metadata.get("team_id") or metadata.get("teamId")
                team_name = metadata.get("team_name") or metadata.get("teamName")
                
                # Check if agent_name suggests it's a Super Agent/team
                is_super_agent = agent_name and ("super" in agent_name.lower() or "router" in agent_name.lower() or "team" in agent_name.lower())
                
                # Check if we already have run data with member_responses
                runs_list = runs_map.get(session_id_str) or runs_map.get(session_public_id_str)
                has_member_responses = False
                if runs_list:
                    if not isinstance(runs_list, list):
                        runs_list = [runs_list]
                    # Check if ANY run has member_responses
                    for run_data in runs_list:
                        if isinstance(run_data, dict) and run_data.get("member_responses"):
                            has_member_responses = True
                            break

                # If it's a team run or Super Agent but we don't have member_responses, fetch from AGNO API
                if (team_id or team_name or is_super_agent) and not has_member_responses and current_user:
                    sessions_needing_agno_fetch.append((session_id_str, session_public_id_str))
            
            # Fetch from AGNO API for sessions that need it (limit to avoid too many API calls)
            if sessions_needing_agno_fetch:
                try:
                    from aldar_middleware.orchestration.agno import agno_service
                    
                    # Limit to first 10 sessions to avoid too many API calls
                    # In production, you might want to batch these or use a different strategy
                    for session_id_str, session_public_id_str in sessions_needing_agno_fetch[:10]:
                        try:
                            # Try with session.public_id first (AGNO uses public_id)
                            agno_session_id = session_public_id_str
                            
                            # Fetch session runs from AGNO API
                            user_id_str = str(current_user.id) if current_user else None
                            agno_runs_response = await agno_service.get_session_runs(
                                agno_session_id,
                                user_id=user_id_str,
                                authorization_header=None  # Groups endpoint doesn't have request object
                            )
                            
                            # Extract runs from response
                            agno_runs = []
                            if isinstance(agno_runs_response, dict):
                                agno_runs = (
                                    agno_runs_response.get("runs") or 
                                    agno_runs_response.get("data", {}).get("runs") or
                                    agno_runs_response.get("data") or
                                    []
                                )
                                if not isinstance(agno_runs, list):
                                    agno_runs = [agno_runs] if agno_runs else []
                            
                            # Store ALL runs from AGNO API for this session
                            # This ensures all agents involved are captured
                            if agno_runs and isinstance(agno_runs, list):
                                runs_map[session_id_str] = agno_runs
                                runs_map[session_public_id_str] = agno_runs
                        except Exception as e:
                            # Log but continue - don't fail the entire request if one session fails
                            continue
                except Exception as e:
                    logger.warning(f"Error fetching from AGNO API for sessions: {str(e)}")
                    # Continue without AGNO data - don't break the request
            
            # Helper function to extract agents from run data (same as in messages endpoint)
            async def extract_agents_from_session_run(
                run_data: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]],
                metadata: Dict[str, Any],
                primary_agent_id: Optional[str] = None
            ) -> List[Dict[str, Optional[str]]]:
                """Extract all unique agents from a session's run data and metadata.

                Args:
                    run_data: Either a single run dict or a list of runs
                    metadata: Session metadata
                    primary_agent_id: The primary agent ID for the session (to avoid duplicating the super agent)

                Returns:
                    List of unique agents involved in the session
                """
                agents_map: Dict[str, Dict[str, Optional[str]]] = {}

                # DEBUG: Log extraction start
                num_runs = len(run_data) if isinstance(run_data, list) else (1 if run_data else 0)
                logger.info(f"[AGENT_EXTRACTION] Starting extraction: {num_runs} runs, primary_agent_id={primary_agent_id}")

                # First, include the team/team_name as the Super Agent if it's a team run
                # Extract from first run or metadata
                team_id = metadata.get("team_id") or metadata.get("teamId")
                team_name = metadata.get("team_name") or metadata.get("teamName")

                # If run_data is provided, check it for team info too
                if run_data:
                    if isinstance(run_data, dict):
                        team_id = team_id or run_data.get("team_id")
                        team_name = team_name or run_data.get("team_name")
                    elif isinstance(run_data, list) and len(run_data) > 0:
                        # Check first run for team info
                        first_run = run_data[0]
                        if isinstance(first_run, dict):
                            team_id = team_id or first_run.get("team_id")
                            team_name = team_name or first_run.get("team_name")

                # Skip adding team if there's a primary agent (they represent the same super agent)
                # The primary agent will be added by the caller, so we avoid duplication
                if (team_id or team_name) and not primary_agent_id:
                    # Use team_name as key to avoid duplicates when same team has different IDs across runs
                    team_key = f"team_{team_name}" if team_name else str(team_id) if team_id else None
                    if team_key:
                        agents_map[team_key] = {
                            "agent_id": str(team_id) if team_id else None,
                            "agent_public_id": None,
                            "agent_name": team_name or "Team",
                            "role": "super_agent",
                        }

                if not run_data:
                    return list(agents_map.values())

                # Normalize run_data to always be a list for consistent processing
                runs_list = run_data if isinstance(run_data, list) else [run_data]

                # Process ALL runs to extract all agents involved
                for run in runs_list:
                    if not isinstance(run, dict):
                        continue

                    # Extract from member_responses
                    member_responses = run.get("member_responses") or run.get("memberResponses") or []
                    if isinstance(member_responses, list):
                        for member in member_responses:
                            if isinstance(member, dict):
                                agent_id = member.get("agent_id") or member.get("agentId")
                                agent_public_id = member.get("agent_public_id") or member.get("agentPublicId")
                                agent_name = member.get("agent_name") or member.get("agentName")

                                agent_key = str(agent_id) if agent_id else str(agent_public_id) if agent_public_id else None
                                if agent_key and (agent_id or agent_public_id or agent_name):
                                    if agent_key not in agents_map:
                                        agents_map[agent_key] = {
                                            "agent_id": str(agent_id) if agent_id else None,
                                            "agent_public_id": str(agent_public_id) if agent_public_id else None,
                                            "agent_name": agent_name,
                                        }

                    # Extract from events - collect all unique agents
                    events = run.get("events") or []
                    if isinstance(events, list):
                        for event in events:
                            if isinstance(event, dict):
                                event_agent_id = event.get("agent_id") or event.get("agentId")
                                event_agent_name = event.get("agent_name") or event.get("agentName")

                                # Only process events that have agent information
                                if event_agent_id or event_agent_name:
                                    # Use agent_id as primary key if available, otherwise use agent_name
                                    agent_key = str(event_agent_id) if event_agent_id else None

                                    # If no agent_id, try to use agent_name as key (but check for duplicates by name)
                                    if not agent_key and event_agent_name:
                                        # Check if we already have this agent by name
                                        existing_by_name = None
                                        for key, agent_info in agents_map.items():
                                            if agent_info.get("agent_name") == event_agent_name:
                                                existing_by_name = key
                                                break
                                        if existing_by_name:
                                            # Update existing entry if we now have an agent_id
                                            if event_agent_id:
                                                # Remove old entry and add with agent_id as key
                                                old_agent = agents_map.pop(existing_by_name)
                                                old_agent["agent_id"] = str(event_agent_id)
                                                agents_map[str(event_agent_id)] = old_agent
                                            continue
                                        else:
                                            # Use name as key if no agent_id
                                            agent_key = f"name_{event_agent_name}"

                                    if agent_key and agent_key not in agents_map:
                                        agents_map[agent_key] = {
                                            "agent_id": str(event_agent_id) if event_agent_id else None,
                                            "agent_public_id": None,
                                            "agent_name": event_agent_name,
                                        }
                                    elif agent_key and agent_key in agents_map:
                                        # Update existing entry if we have better info (e.g., got agent_id)
                                        existing_agent = agents_map[agent_key]
                                        if event_agent_id and not existing_agent.get("agent_id"):
                                            existing_agent["agent_id"] = str(event_agent_id)
                                        if event_agent_name and not existing_agent.get("agent_name"):
                                            existing_agent["agent_name"] = event_agent_name

                    # Also include the main agent from the run if present (for non-team runs)
                    run_agent_id = run.get("agent_id")
                    run_agent_name = run.get("agent_name")
                    if (run_agent_id or run_agent_name) and not team_id:
                        agent_key = str(run_agent_id) if run_agent_id else "main"
                        if agent_key not in agents_map:
                            agents_map[agent_key] = {
                                "agent_id": str(run_agent_id) if run_agent_id else None,
                                "agent_public_id": None,
                                "agent_name": run_agent_name,
                            }
                
                # DEBUG: Log agents_map before normalization
                logger.info(f"[AGENT_EXTRACTION] Before normalization, agents_map has {len(agents_map)} entries:")
                for key, agent_info in agents_map.items():
                    logger.info(f"  Key='{key}': agent_id={agent_info.get('agent_id')}, name={agent_info.get('agent_name')}")

                # Normalize agent_ids: convert string-based IDs to UUIDs
                # Collect all string agent_ids that need lookup (e.g., "mcp-agent-New Weather MCP")
                string_agent_ids_to_lookup = []
                for agent_info in agents_map.values():
                    agent_id = agent_info.get("agent_id")
                    agent_public_id = agent_info.get("agent_public_id")
                    
                    # If we don't have a public_id and agent_id looks like a string (not UUID), look it up
                    if not agent_public_id and agent_id:
                        try:
                            # Try to parse as UUID - if it fails, it's a string ID
                            UUID(agent_id)
                        except (ValueError, AttributeError):
                            # It's a string ID like "mcp-agent-New Weather MCP"
                            string_agent_ids_to_lookup.append(agent_id)
                
                # DEBUG: Log string agent_ids that need lookup
                logger.info(f"[AGENT_EXTRACTION] String agent_ids needing lookup: {string_agent_ids_to_lookup}")

                # Look up UUIDs for string agent_ids from the Agent table
                if string_agent_ids_to_lookup:
                    try:
                        # First try direct lookup by agent_id
                        agent_lookup_query = select(Agent.agent_id, Agent.public_id, Agent.name).where(
                            Agent.agent_id.in_(string_agent_ids_to_lookup)
                        )
                        agent_lookup_result = await db.execute(agent_lookup_query)
                        agent_lookup_map = {
                            str(row[0]): {"public_id": str(row[1]), "name": row[2]}
                            for row in agent_lookup_result.all()
                        }
                        
                        
                        # For IDs not found, try extracting agent name and lookup by name
                        # e.g., "mcp-agent-New Weather MCP" -> "New Weather MCP"
                        not_found_ids = [aid for aid in string_agent_ids_to_lookup if aid not in agent_lookup_map]
                        if not_found_ids:
                            agent_names_to_lookup = []
                            id_to_name_map = {}
                            for string_id in not_found_ids:
                                # Skip team IDs (they don't correspond to agents table)
                                if string_id.startswith("user-team-"):
                                    continue
                                # Extract name from "mcp-agent-{AgentName}" format
                                if string_id.startswith("mcp-agent-"):
                                    agent_name = string_id.replace("mcp-agent-", "", 1)
                                    agent_names_to_lookup.append(agent_name)
                                    id_to_name_map[agent_name] = string_id
                            
                            if agent_names_to_lookup:
                                # DEBUG: Log name extraction
                                logger.info(f"[AGENT_EXTRACTION] Extracted names to lookup: {agent_names_to_lookup}")
                                logger.info(f"[AGENT_EXTRACTION] ID to name mapping: {id_to_name_map}")

                                name_lookup_query = select(Agent.name, Agent.public_id).where(
                                    Agent.name.in_(agent_names_to_lookup)
                                )
                                name_lookup_result = await db.execute(name_lookup_query)
                                name_lookup_rows = name_lookup_result.all()

                                # DEBUG: Log lookup results
                                logger.info(f"[AGENT_EXTRACTION] Name lookup found {len(name_lookup_rows)} matches")
                                for row in name_lookup_rows:
                                    logger.info(f"  Found: name={row[0]}, public_id={row[1]}")

                                for row in name_lookup_rows:
                                    agent_name = row[0]
                                    public_id = str(row[1])
                                    # Map the original string ID to the UUID
                                    original_string_id = id_to_name_map.get(agent_name)
                                    if original_string_id:
                                        agent_lookup_map[original_string_id] = {
                                            "public_id": public_id,
                                            "name": agent_name
                                        }
                        
                        # Update agents_map with UUIDs
                        for agent_info in agents_map.values():
                            agent_id = agent_info.get("agent_id")
                            if agent_id and agent_id in agent_lookup_map:
                                lookup_data = agent_lookup_map[agent_id]
                                # Replace agent_id with the UUID (public_id)
                                agent_info["agent_id"] = lookup_data["public_id"]
                                agent_info["agent_public_id"] = lookup_data["public_id"]
                                # Update agent_name if not already set
                                if not agent_info.get("agent_name") and lookup_data["name"]:
                                    agent_info["agent_name"] = lookup_data["name"]
                    except Exception as e:
                        logger.warning(f"Failed to normalize agent_ids: {str(e)}")
                        # Rollback the transaction to prevent invalid state
                        await db.rollback()
                        # Continue with original agent_ids if lookup fails

                # DEBUG: Log final agents list
                final_agents = list(agents_map.values())
                logger.info(f"[AGENT_EXTRACTION] Final agents list ({len(final_agents)} agents):")
                for agent in final_agents:
                    logger.info(f"  - {agent.get('agent_name')}: agent_id={agent.get('agent_id')}, public_id={agent.get('agent_public_id')}")

                return final_agents
            
            # Now build session items with agents_involved
            temp_items = []  # Store items temporarily before resolving agent names
            all_agent_ids_in_group: List[str] = []  # Collect all agent IDs for batch resolution
            
            for row in rows_list:
                # Unpack: Session, Agent, session_name (explicitly selected), last_activity, message_count
                try:
                    session_obj, agent_obj, session_name_fresh, last_activity, _msg_count = row
                except (ValueError, TypeError) as e:
                    # Fallback: if row structure is different, try to handle it
                    logger.warning(f"Row unpacking error: {e}, row length: {len(row) if hasattr(row, '__len__') else 'unknown'}")
                    # Try alternative unpacking
                    if len(row) >= 3:
                        session_obj, agent_obj = row[0], row[1]
                        session_name_fresh = row[2] if len(row) > 2 else None
                        last_activity = row[3] if len(row) > 3 else None
                        _msg_count = row[4] if len(row) > 4 else 0
                    else:
                        logger.error(f"Invalid row structure: {row}")
                        continue
                
                metadata = session_obj.session_metadata or {}
                last_activity_dt = last_activity or session_obj.updated_at or session_obj.created_at
                session_id_str = str(session_obj.session_id)
                session_public_id_str = str(session_obj.public_id)

                # Use the ACTUAL primary agent from Session.agent_id (database column via join), not metadata
                # This ensures the response shows the same agent that's actually stored in the database
                primary_agent_id = str(agent_obj.public_id) if agent_obj and agent_obj.public_id else None
                primary_agent_name = agent_obj.name if agent_obj else None

                # Check if session is favorite
                is_favorite = False
                for key in FAVORITE_METADATA_KEYS:
                    favorite_value = metadata.get(key)
                    if favorite_value:
                        if isinstance(favorite_value, bool):
                            is_favorite = favorite_value
                        elif isinstance(favorite_value, str):
                            is_favorite = favorite_value.lower() == "true"
                        if is_favorite:
                            break

                # Extract agents_involved from run data
                run_data = runs_map.get(session_id_str) or runs_map.get(session_public_id_str)

                # DEBUG: Log for specific session
                if session_id_str == "19c5beb8-3cdf-46e8-ba37-f52d1f99f203":
                    logger.info(f"[DEBUG_SESSION] Before extraction: primary_agent_id={primary_agent_id}, primary_agent_name={primary_agent_name}")
                    logger.info(f"[DEBUG_SESSION] run_data type={type(run_data)}, len={len(run_data) if run_data else 0}")

                agents_involved = await extract_agents_from_session_run(run_data, metadata, primary_agent_id)

                # ALWAYS ensure the primary agent is included in agents_involved
                # Check if primary agent is already in the list
                primary_agent_exists = False
                if primary_agent_id:
                    for agent in agents_involved:
                        if agent.get("agent_id") == primary_agent_id or agent.get("agent_public_id") == primary_agent_id:
                            primary_agent_exists = True
                            break
                
                # If primary agent not found in agents_involved, add it
                if not primary_agent_exists and (primary_agent_id or primary_agent_name):
                    agents_involved.insert(0, {
                        "agent_id": primary_agent_id,
                        "agent_public_id": primary_agent_id,
                        "agent_name": primary_agent_name,
                    })

                # DEBUG: Log final agents_involved for specific session
                if str(session_obj.session_id) == "19c5beb8-3cdf-46e8-ba37-f52d1f99f203":
                    logger.info(f"[DEBUG_SESSION] Session {session_obj.session_id}: agents_involved count={len(agents_involved)}")
                    for agent in agents_involved:
                        logger.info(f"  Agent: {agent.get('agent_name')} (id={agent.get('agent_id')})")

                # Collect all agent IDs for batch resolution
                for agent in agents_involved:
                    agent_id = agent.get("agent_id") or agent.get("agent_public_id")
                    if agent_id:
                        all_agent_ids_in_group.append(str(agent_id))

                # Use explicitly selected session_name_fresh (from database query) 
                # Fallback to session_obj.session_name if session_name_fresh is None
                # This ensures we always get the latest value from the database
                final_title = session_name_fresh if session_name_fresh is not None else (session_obj.session_name or "Untitled Chat")
                
                temp_items.append(
                    {
                        "id": session_obj.session_id,
                        "title": final_title,  # Use explicitly selected session_name for fresh data
                        "createdAt": last_activity_dt.isoformat() if last_activity_dt else session_obj.created_at.isoformat(),
                        "agentId": primary_agent_id,
                        "agentName": primary_agent_name,
                        "isFavorite": is_favorite,
                        "agents_involved": agents_involved if agents_involved else [],  # Add agents_involved field
                    }
                )
            
            # OPTIMIZATION: Batch resolve agent names for all agents in this group
            if all_agent_ids_in_group:
                await _batch_resolve_agent_names(all_agent_ids_in_group, db, agent_name_map)
            
            # Update agent names from resolved cache
            for item in temp_items:
                for agent in item.get("agents_involved", []):
                    agent_id = agent.get("agent_id") or agent.get("agent_public_id")
                    if agent_id and str(agent_id) in agent_name_map:
                        agent["agent_name"] = agent_name_map[str(agent_id)]
            
            # Add items to the group
            items.extend(temp_items)

        # Calculate has_more and page for this group
        group_has_more = group_offset + group_limit < group_total
        if group_has_more:
            global_has_more = True
        
        # Calculate page number for this group
        group_page = (group_offset // group_limit) + 1 if group_limit > 0 else 1

        # Hide empty groups (except favourites which should always be shown)
        # This keeps the response clean and only shows groups with actual data
        if group_name != "favourites" and group_total == 0:
            continue

        results.append({
            "id": group_id_map.get(group_name, group_name.replace("_", "-")),
            "title": group_titles.get(group_name, group_name.replace("_", " ").title()),
            "items": items,
            "totalItems": group_total,
            "perPage": group_limit,
            "page": group_page,
            "hasMore": group_has_more,
        })

    return results, total_count, global_has_more


# Chat configuration - loaded from environment variables via settings
# These can be overridden via .env file with ALDAR_ prefix:
# ALDAR_MAX_QUERY_LENGTH, ALDAR_MAX_ATTACHMENTS_PER_SESSION, etc.
MAX_QUERY_LENGTH = settings.max_query_length
MAX_ATTACHMENTS_PER_SESSION = settings.max_attachments_per_session
MAX_ATTACHMENT_SIZE_BYTES = settings.max_attachment_size_bytes
RATE_LIMIT_REQUESTS_PER_MINUTE = settings.rate_limit_requests
RATE_LIMIT_WINDOW_SECONDS = settings.rate_limit_window
# SESSION_TTL_HOURS removed - session expiration is disabled

_rate_limit_store: Dict[str, Deque[datetime]] = {}
_rate_limit_lock = Lock()


def _chat_error_response(
    *,
    error_message: str,
    error_code: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
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
    raise HTTPException(
        status_code=status_code,
        detail=_chat_error_response(
            error_message=error_message,
            error_code=error_code,
            details=details,
        ),
    )


def _validate_query_length(value: Optional[str], field_name: str) -> None:
    if value and len(value) > MAX_QUERY_LENGTH:
        _raise_chat_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="VALIDATION_ERROR",
            error_message=f"{field_name} exceeds maximum length of {MAX_QUERY_LENGTH} characters",
            details={"field": field_name, "message": f"Length exceeds {MAX_QUERY_LENGTH} characters"},
        )


async def _sync_session_title_from_agno(
    session: Session,
    db: AsyncSession
) -> None:
    """
    Sync session title from agno_sessions table.
    Updates the sessions table if title has changed and wasn't manually renamed.
    
    This function modifies the session object in-place and commits the change.
    """
    try:
        # Query agno_sessions for this session
        agno_query = text("""
            SELECT session_data
            FROM agno_sessions
            WHERE session_id = :session_id
            ORDER BY created_at DESC
            LIMIT 1
        """)
        
        result = await db.execute(
            agno_query,
            {"session_id": str(session.public_id)}
        )
        row = result.fetchone()
        
        if not row:
            return
        
        session_data = row[0]
        agno_session_name = None
        
        # Extract session_name from session_data
        if session_data:
            try:
                if isinstance(session_data, dict):
                    agno_session_name = session_data.get("session_name")
                elif isinstance(session_data, str):
                    session_data_dict = json.loads(session_data)
                    agno_session_name = session_data_dict.get("session_name")
            except Exception as e:
                logger.warning(f"Error extracting session_name from session_data: {str(e)}")
                return
        
        # Update sessions table if title changed and not manually renamed
        metadata = session.session_metadata or {}
        is_manually_renamed = metadata.get("title_manually_renamed", False)
        
        if agno_session_name and agno_session_name != session.session_name and not is_manually_renamed:
            try:
                session.session_name = agno_session_name
                await db.commit()
                await db.refresh(session)
                logger.info(
                    f"Synced session.session_name to '{agno_session_name}' "
                    f"for session_id={session.id} from agno_sessions"
                )
            except Exception as e:
                await db.rollback()
                logger.warning(f"Failed to sync session_name: {str(e)}")
        elif is_manually_renamed:
            logger.debug(
                f"Skipping title sync for session_id={session.id} "
                f"(manually renamed by user)"
            )
        
    except Exception as e:
        logger.warning(f"Error syncing title from agno_sessions: {str(e)}")


def _ensure_session_active(session: Session) -> None:
    # Session expiration check disabled - sessions are always allowed
    # if session.created_at and datetime.utcnow() - session.created_at > timedelta(hours=SESSION_TTL_HOURS):
    #     _raise_chat_error(
    #         status_code=status.HTTP_404_NOT_FOUND,
    #         error_code="SESSION_NOT_FOUND",
    #         error_message="Chat session has expired",
    #         details={"field": "session_id", "message": "Session exceeded 24-hour retention"},
    #     )
    pass


async def _enforce_chat_rate_limit(user: User) -> None:
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
                error_message="Rate limit exceeded. Please wait before making more requests.",
                details={"field": "rate_limit", "message": "Maximum 100 requests per minute"},
            )
        bucket.append(now)


def _favorite_flag_clause():
    """Build a SQL expression that matches sessions marked as favourite."""
    expressions = [
        or_(
            func.lower(Session.session_metadata[key].as_string()) == "true",
            cast(Session.session_metadata.op('->>')(key), Boolean).is_(True),
        )
        for key in FAVORITE_METADATA_KEYS
    ]
    return or_(*expressions)


def _not_favorite_flag_clause():
    """Build a SQL expression that matches sessions NOT marked as favourite.
    
    A session is NOT a favorite if NONE of the favorite keys are set to true.
    This uses explicit checks that handle NULL values (when key doesn't exist).
    """
    # For each favorite key, ensure it's not true
    not_favorite_conditions = []
    for key in FAVORITE_METADATA_KEYS:
        # Extract the value using ->> which returns NULL if key doesn't exist
        key_value_raw = Session.session_metadata.op('->>')(key)
        key_value_str = func.lower(key_value_raw)
        key_value_bool = cast(key_value_raw, Boolean)
        
        # Key is NOT true if:
        # 1. Key doesn't exist (extracted value is NULL) - handled by COALESCE
        # 2. Key exists but string value is not "true"
        # 3. Key exists but boolean value is not True
        key_not_true = and_(
            # String value is not "true" (or NULL/empty)
            func.coalesce(key_value_str, "") != "true",
            # Boolean value is not True (or NULL/False)
            func.coalesce(key_value_bool, False).is_(False),
        )
        not_favorite_conditions.append(key_not_true)
    
    # A session is NOT a favorite if ALL favorite keys are not true
    # Since favorite clause uses OR (any key true = favorite),
    # NOT favorite means ALL keys must not be true
    if not_favorite_conditions:
        return and_(*not_favorite_conditions)
    else:
        # Fallback: always true condition
        return Session.id.isnot(None)