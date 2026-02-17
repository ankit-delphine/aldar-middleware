"""Feedback API routes."""

import csv
import io
import logging
from datetime import datetime
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.database.base import get_db
from aldar_middleware.models.feedback import FeedbackEntityType, FeedbackRating
from aldar_middleware.models.user import User
from aldar_middleware.schemas.feedback import (
    FeedbackCreateRequest,
    FeedbackResponse,
    FeedbackAnalyticsSummary,
    FeedbackTrendsResponse,
    FeedbackExportRow,
    PaginatedResponse,
    ErrorResponse,
)
from aldar_middleware.monitoring.prometheus import (
    FEEDBACK_CREATED,
    FEEDBACK_FAILED,
    FEEDBACK_FILES_UPLOADED,
    FEEDBACK_FILE_SIZE,
)
from aldar_middleware.orchestration.blob_storage import BlobStorageService
from aldar_middleware.services.feedback_analytics import FeedbackAnalyticsService
from aldar_middleware.services.feedback_service import FeedbackService
from aldar_middleware.settings.context import get_correlation_id

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        413: {"model": ErrorResponse, "description": "File too large"},
        422: {"model": ErrorResponse, "description": "Validation error"},
    },
)
async def create_feedback(
    entity_id: str = Form(...),
    entity_type: FeedbackEntityType = Form(...),
    rating: FeedbackRating = Form(...),
    comment: Optional[str] = Form(None),
    metadata: Optional[str] = Form(None),
    session_id: Optional[str] = Form(None),
    files: list[UploadFile] = File(default=[]),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackResponse:
    """
    Create new feedback with optional file attachments.

    - **entity_id**: ID of the entity being rated
    - **entity_type**: Type of entity (session, chat, response, agent, application, final_response)
    - **rating**: Feedback rating (thumbs_up, thumbs_down, neutral)
    - **comment**: Optional feedback comment (max 5000 chars)
    - **metadata**: Optional JSON metadata
    - **session_id**: Optional session identifier
    - **files**: Optional file attachments (max 5 files, 10MB each)
    """
    correlation_id = get_correlation_id()
    user_id = str(current_user.id)
    user_email = current_user.email

    if not user_id:
        logger.warning(
            "Missing user_id in token",
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user information",
        )

    # Validate file count
    if len(files) > 5:
        FEEDBACK_FAILED.labels(reason="too_many_files").inc()
        logger.warning(
            "Too many files submitted",
            extra={
                "correlation_id": correlation_id,
                "file_count": len(files),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 5 files allowed per feedback",
        )

    # Parse metadata
    metadata_dict = {}
    if metadata:
        try:
            import json
            metadata_dict = json.loads(metadata)
        except Exception:
            FEEDBACK_FAILED.labels(reason="invalid_metadata").inc()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid metadata JSON",
            )

    try:
        # Initialize services
        feedback_service = FeedbackService(db)
        blob_service = BlobStorageService()

        # Check user's feedback count limit (max 500 per user)
        from aldar_middleware.models.feedback import FeedbackData
        from sqlalchemy import and_, func, select
        
        feedback_count_query = select(func.count(FeedbackData.feedback_id)).where(
            and_(
                FeedbackData.user_id == user_id,
                FeedbackData.deleted_at.is_(None)  # Only count non-deleted feedback
            )
        )
        feedback_count_result = await db.execute(feedback_count_query)
        current_feedback_count = feedback_count_result.scalar_one() or 0
        
        if current_feedback_count >= 500:
            FEEDBACK_FAILED.labels(reason="feedback_limit_exceeded").inc()
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

        # Create feedback entry
        feedback = await feedback_service.create_feedback(
            user_id=user_id,
            user_email=user_email,
            entity_id=entity_id,
            entity_type=entity_type,
            rating=rating,
            comment=comment,
            metadata=metadata_dict,
            correlation_id=correlation_id,
            session_id=session_id,
        )

        # Upload files
        for upload_file in files:
            if not upload_file.filename:
                continue

            try:
                file_content = await upload_file.read()
                file_url, blob_name, file_size = await blob_service.upload_feedback_file(
                    file_name=upload_file.filename,
                    file_content=file_content,
                    content_type=upload_file.content_type or "application/octet-stream",
                    feedback_id=str(feedback.feedback_id),
                    user_id=user_id,
                )

                await feedback_service.add_file_to_feedback(
                    feedback_id=feedback.feedback_id,
                    file_name=upload_file.filename,
                    file_url=file_url,
                    blob_name=blob_name,
                    file_size=file_size,
                    content_type=upload_file.content_type,
                )

            except ValueError as e:
                FEEDBACK_FAILED.labels(reason="file_validation_error").inc()
                logger.warning(
                    f"File validation error: {str(e)}",
                    extra={
                        "correlation_id": correlation_id,
                        "file_name": upload_file.filename,
                    },
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"File error: {str(e)}",
                )
            except Exception as e:
                FEEDBACK_FAILED.labels(reason="upload_error").inc()
                logger.error(
                    f"File upload error: {str(e)}",
                    extra={
                        "correlation_id": correlation_id,
                        "file_name": upload_file.filename,
                    },
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="File upload failed",
                )

        # Commit transaction
        await db.commit()
        await db.refresh(feedback)

        # Record metrics
        FEEDBACK_CREATED.labels(
            entity_type=entity_type.value, rating=rating.value
        ).inc()
        
        for upload_file in files:
            if upload_file.filename:
                FEEDBACK_FILE_SIZE.observe(len(await upload_file.read()))
                await upload_file.seek(0)
        
        FEEDBACK_FILES_UPLOADED.labels(status="success").inc(len(files))

        logger.info(
            "Feedback created successfully",
            extra={
                "correlation_id": correlation_id,
                "feedback_id": str(feedback.feedback_id),
                "file_count": len(files),
            },
        )

        return FeedbackResponse.model_validate(feedback)

    except HTTPException:
        raise
    except Exception as e:
        FEEDBACK_FAILED.labels(reason="unknown_error").inc()
        logger.error(
            f"Failed to create feedback: {str(e)}",
            extra={"correlation_id": correlation_id},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create feedback",
        )


@router.get(
    "/{feedback_id}",
    response_model=FeedbackResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        404: {"model": ErrorResponse, "description": "Feedback not found"},
    },
)
async def get_feedback(
    feedback_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackResponse:
    """
    Retrieve a specific feedback entry.
    
    Users can only view their own feedback. Admins can view all feedback.
    """
    correlation_id = get_correlation_id()
    user_id = str(current_user.id)

    try:
        feedback_service = FeedbackService(db)
        
        # Check if user is admin (has admin privileges)
        is_admin = getattr(current_user, "is_admin", False)
        
        # If not admin, filter to user's own feedback
        filter_user_id = None if is_admin else user_id
        
        feedback = await feedback_service.get_feedback(
            feedback_id=feedback_id,
            user_id=filter_user_id,
        )

        if not feedback:
            logger.warning(
                "Feedback not found",
                extra={
                    "correlation_id": correlation_id,
                    "feedback_id": feedback_id,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Feedback not found",
            )

        # Fetch agent information - from feedback.agent_id or derive from entity
        feedback_dict = FeedbackResponse.model_validate(feedback).model_dump()
        
        # Fetch user full name
        from aldar_middleware.models.user import User
        from aldar_middleware.models.menu import Agent
        from aldar_middleware.models.messages import Message
        from aldar_middleware.models.sessions import Session
        from aldar_middleware.models.attachment import Attachment
        from sqlalchemy import select, or_
        from uuid import UUID as UUIDType
        
        # Helper function to check if string is UUID
        def _is_uuid(value: str) -> bool:
            """Check if a string is a valid UUID."""
            if not value or not isinstance(value, str):
                return False
            try:
                UUIDType(value)
                return True
            except (ValueError, AttributeError):
                return False
        
        # Helper function to resolve attachment ID to blob URL
        async def _resolve_attachment_id_to_url(attachment_id: str) -> Optional[str]:
            """Resolve an attachment ID to a blob URL."""
            try:
                result = await db.execute(
                    select(Attachment).where(
                        Attachment.id == UUIDType(attachment_id),
                        Attachment.is_active == True
                    )
                )
                attachment = result.scalar_one_or_none()
                if attachment and attachment.blob_url:
                    return attachment.blob_url
                else:
                    logger.warning(f"Attachment not found or has no blob_url: {attachment_id}")
                    return None
            except ValueError:
                logger.warning(f"Invalid UUID format: {attachment_id}")
                return None
            except Exception as e:
                logger.error(f"Failed to resolve attachment ID {attachment_id}: {str(e)}")
                return None
        
        try:
            user_uuid = UUIDType(feedback.user_id)
            user_query = select(User).where(User.id == user_uuid)
            user_result = await db.execute(user_query)
            user = user_result.scalar_one_or_none()
            if user:
                # Get full_name, fallback to azure_display_name or construct from first_name + last_name
                full_name = user.full_name
                if not full_name:
                    if user.azure_display_name:
                        full_name = user.azure_display_name
                    elif user.first_name or user.last_name:
                        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
                feedback_dict["user_full_name"] = full_name
                
                # Get profile photo URL - check preferences first, then fallback to proxy endpoint
                from aldar_middleware.utils.user_utils import get_profile_photo_url
                profile_photo = get_profile_photo_url(user)
                if not profile_photo and user.azure_ad_id:
                    from aldar_middleware.settings import settings
                    profile_photo = f"{settings.api_prefix}/auth/users/{user.id}/profile-photo"
                
                feedback_dict["user_profile_photo"] = profile_photo
                feedback_dict["user_department"] = user.azure_department
                feedback_dict["user_job_title"] = user.azure_job_title
                feedback_dict["user_company"] = user.company
                feedback_dict["user_external_id"] = user.external_id
                feedback_dict["user_azure_display_name"] = user.azure_display_name
                feedback_dict["user_azure_upn"] = user.azure_upn
            else:
                feedback_dict["user_full_name"] = None
                feedback_dict["user_profile_photo"] = None
                feedback_dict["user_department"] = None
                feedback_dict["user_job_title"] = None
                feedback_dict["user_company"] = None
                feedback_dict["user_external_id"] = None
                feedback_dict["user_azure_display_name"] = None
                feedback_dict["user_azure_upn"] = None
        except (ValueError, TypeError):
            feedback_dict["user_full_name"] = None
            feedback_dict["user_profile_photo"] = None
            feedback_dict["user_department"] = None
            feedback_dict["user_job_title"] = None
            feedback_dict["user_company"] = None
            feedback_dict["user_external_id"] = None
            feedback_dict["user_azure_display_name"] = None
            feedback_dict["user_azure_upn"] = None
        except Exception as e:
            logger.debug(f"Error fetching user information: {str(e)}")
            feedback_dict["user_full_name"] = None
            feedback_dict["user_profile_photo"] = None
            feedback_dict["user_department"] = None
            feedback_dict["user_job_title"] = None
            feedback_dict["user_company"] = None
            feedback_dict["user_external_id"] = None
            feedback_dict["user_azure_display_name"] = None
            feedback_dict["user_azure_upn"] = None
        
        resolved_agent_id = None
        
        # First, try to get agent_id from feedback
        if feedback.agent_id:
            resolved_agent_id = feedback.agent_id
        # If not, derive from message
        elif feedback.entity_type == FeedbackEntityType.MESSAGE:
            try:
                entity_uuid = UUIDType(feedback.entity_id)
                message_query = select(Message).where(
                    or_(
                        Message.id == entity_uuid,
                        Message.public_id == entity_uuid
                    )
                )
                message_result = await db.execute(message_query)
                message = message_result.scalar_one_or_none()
                if message:
                    resolved_agent_id = str(message.agent_id)
            except Exception as e:
                logger.debug(f"Error fetching message for agent_id: {str(e)}")
        # Or derive from session
        elif feedback.entity_type == FeedbackEntityType.SESSION:
            try:
                # Try UUID lookup first
                try:
                    entity_uuid = UUIDType(feedback.entity_id)
                    session_query = select(Session).where(
                        or_(
                            Session.id == entity_uuid,
                            Session.public_id == entity_uuid
                        )
                    )
                    session_result = await db.execute(session_query)
                    session = session_result.scalar_one_or_none()
                    if session:
                        resolved_agent_id = str(session.agent_id)
                except (ValueError, TypeError):
                    # If not a valid UUID, try case-insensitive string match
                    normalized_entity_id = feedback.entity_id.lower().strip()
                    # Try to find by comparing normalized strings
                    all_sessions_query = select(Session)
                    all_sessions_result = await db.execute(all_sessions_query)
                    all_sessions = all_sessions_result.scalars().all()
                    for sess in all_sessions:
                        if (str(sess.id).lower().strip() == normalized_entity_id or 
                            str(sess.public_id).lower().strip() == normalized_entity_id):
                            resolved_agent_id = str(sess.agent_id)
                            break
            except Exception as e:
                logger.debug(f"Error fetching session for agent_id: {str(e)}")
        
        # Fetch agent information if we have an agent_id
        if resolved_agent_id:
            try:
                # Try to match by public_id (UUID) first, then by id (BigInteger)
                uuid_agent_id = None
                bigint_agent_id = None
                
                try:
                    uuid_agent_id = UUIDType(resolved_agent_id)
                except (ValueError, TypeError):
                    try:
                        bigint_agent_id = int(resolved_agent_id)
                    except (ValueError, TypeError):
                        pass
                
                conditions = []
                if uuid_agent_id:
                    conditions.append(Agent.public_id == uuid_agent_id)
                if bigint_agent_id:
                    conditions.append(Agent.id == bigint_agent_id)
                
                if conditions:
                    agent_query = select(Agent).where(or_(*conditions))
                    agent_result = await db.execute(agent_query)
                    agent = agent_result.scalar_one_or_none()
                    
                    if agent:
                        feedback_dict["agent_id"] = str(resolved_agent_id)
                        feedback_dict["agent_name"] = agent.name
                        feedback_dict["agent_public_id"] = str(agent.public_id)
                        
                        # Resolve agent_thumbnail: if it's a UUID, resolve to blob URL; otherwise use as-is
                        agent_icon = agent.icon
                        if agent_icon:
                            if _is_uuid(agent_icon):
                                # It's an attachment ID, resolve to blob URL
                                resolved_url = await _resolve_attachment_id_to_url(agent_icon)
                                feedback_dict["agent_thumbnail"] = resolved_url if resolved_url else agent_icon
                            else:
                                # It's already a URL, use it directly
                                feedback_dict["agent_thumbnail"] = agent_icon
                        else:
                            feedback_dict["agent_thumbnail"] = None
                    else:
                        feedback_dict["agent_name"] = None
                        feedback_dict["agent_public_id"] = None
                        feedback_dict["agent_thumbnail"] = None
                else:
                    feedback_dict["agent_name"] = None
                    feedback_dict["agent_public_id"] = None
                    feedback_dict["agent_thumbnail"] = None
            except Exception as e:
                logger.warning(f"Error fetching agent information: {str(e)}")
                feedback_dict["agent_name"] = None
                feedback_dict["agent_public_id"] = None
                feedback_dict["agent_thumbnail"] = None
        else:
            feedback_dict["agent_name"] = None
            feedback_dict["agent_public_id"] = None
            feedback_dict["agent_thumbnail"] = None

        # Do not override empty comments; leave as null/empty

        return FeedbackResponse(**feedback_dict)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to retrieve feedback: {str(e)}",
            extra={
                "correlation_id": correlation_id,
                "feedback_id": feedback_id,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve feedback",
        )


@router.get(
    "",
    response_model=PaginatedResponse[FeedbackResponse],
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
    },
)
async def list_feedback(
    entity_type: Optional[FeedbackEntityType] = Query(None),
    entity_id: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    rating: Optional[FeedbackRating] = Query(None),
    search: Optional[str] = Query(None, description="Search across user_id, user_email, user_full_name, comment, agent_name, and metadata_json.agent.agent_name fields"),
    date_from: Optional[datetime] = Query(None, description="Filter feedback from this date (ISO format)"),
    date_to: Optional[datetime] = Query(None, description="Filter feedback to this date (ISO format)"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    sort_by: Optional[str] = Query(None, description="Sort by field: user_email, user_full_name, date, comment"),
    sort_order: Optional[str] = Query("DESC", description="Sort order: ASC or DESC"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[FeedbackResponse]:
    """
    List feedback with optional filters.
    
    Users see only their own feedback. Admins see all feedback.
    """
    correlation_id = get_correlation_id()
    user_id = str(current_user.id)

    try:
        feedback_service = FeedbackService(db)
        
        # Check if user is admin
        is_admin = getattr(current_user, "is_admin", False)
        
        # If not admin, filter to user's own feedback
        filter_user_id = None if is_admin else user_id
        
        feedback_list, total = await feedback_service.list_feedback(
            user_id=filter_user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            agent_id=agent_id,
            rating=rating,
            date_from=date_from,
            date_to=date_to,
            search=search,  # Add search parameter
            page=page,
            limit=limit,
            exclude_user_id=is_admin,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        # Fetch agent information for feedback items
        from aldar_middleware.models.menu import Agent
        from aldar_middleware.models.messages import Message
        from aldar_middleware.models.sessions import Session
        from aldar_middleware.models.attachment import Attachment
        from sqlalchemy import select, or_
        from uuid import UUID as UUIDType
        
        # Helper function to check if string is UUID
        def _is_uuid(value: str) -> bool:
            """Check if a string is a valid UUID."""
            if not value or not isinstance(value, str):
                return False
            try:
                UUIDType(value)
                return True
            except (ValueError, AttributeError):
                return False
        
        # Collect all attachment IDs upfront for batch resolution
        attachment_ids_to_fetch = set()
        
        agent_ids_to_fetch = set()
        message_ids_to_fetch = set()
        session_ids_to_fetch = set()
        
        # Collect agent_ids from feedback and entity_ids for messages/sessions
        for feedback in feedback_list:
            if feedback.agent_id:
                agent_ids_to_fetch.add(feedback.agent_id)
            elif feedback.entity_type == FeedbackEntityType.MESSAGE:
                # If agent_id is null but entity_type is message, fetch from message
                message_ids_to_fetch.add(feedback.entity_id)
            elif feedback.entity_type == FeedbackEntityType.SESSION:
                # If agent_id is null but entity_type is session, fetch from session
                session_ids_to_fetch.add(feedback.entity_id)
        
        # Fetch agents in batch
        agents_map = {}
        if agent_ids_to_fetch:
            try:
                # Try to match by public_id (UUID) first
                uuid_agent_ids = []
                bigint_agent_ids = []
                for agent_id_str in agent_ids_to_fetch:
                    try:
                        uuid_agent_ids.append(UUIDType(agent_id_str))
                    except (ValueError, TypeError):
                        try:
                            bigint_agent_ids.append(int(agent_id_str))
                        except (ValueError, TypeError):
                            pass
                
                conditions = []
                if uuid_agent_ids:
                    conditions.append(Agent.public_id.in_(uuid_agent_ids))
                if bigint_agent_ids:
                    conditions.append(Agent.id.in_(bigint_agent_ids))
                
                if conditions:
                    agent_query = select(Agent).where(or_(*conditions))
                    agent_result = await db.execute(agent_query)
                    agents = agent_result.scalars().all()
                    
                    # Create maps for both public_id and id lookups
                    # Also collect attachment IDs from agent icons
                    for agent in agents:
                        agents_map[str(agent.public_id)] = agent
                        agents_map[str(agent.id)] = agent
                        if agent.icon and _is_uuid(agent.icon):
                            attachment_ids_to_fetch.add(agent.icon)
            except Exception as e:
                logger.warning(f"Error fetching agent information: {str(e)}")
        
        # Fetch agent_ids from messages (simplified - no fallback)
        messages_agent_map = {}
        if message_ids_to_fetch:
            try:
                message_uuid_ids = []
                for msg_id in message_ids_to_fetch:
                    try:
                        message_uuid_ids.append(UUIDType(msg_id))
                    except (ValueError, TypeError):
                        pass
                
                if message_uuid_ids:
                    message_query = select(Message).where(
                        or_(
                            Message.id.in_(message_uuid_ids),
                            Message.public_id.in_(message_uuid_ids)
                        )
                    )
                    message_result = await db.execute(message_query)
                    messages = message_result.scalars().all()
                    
                    for message in messages:
                        messages_agent_map[str(message.id)] = message.agent_id
                        messages_agent_map[str(message.public_id)] = message.agent_id
                        if message.agent_id:
                            agent_ids_to_fetch.add(str(message.agent_id))
            except Exception as e:
                logger.warning(f"Error fetching message agent information: {str(e)}")
        
        # Fetch agent_ids from sessions (simplified - no fallback)
        sessions_agent_map = {}
        if session_ids_to_fetch:
            try:
                session_uuid_ids = []
                for sess_id in session_ids_to_fetch:
                    try:
                        session_uuid_ids.append(UUIDType(sess_id))
                    except (ValueError, TypeError):
                        pass
                
                if session_uuid_ids:
                    session_query = select(Session).where(
                        or_(
                            Session.id.in_(session_uuid_ids),
                            Session.public_id.in_(session_uuid_ids)
                        )
                    )
                    session_result = await db.execute(session_query)
                    sessions = session_result.scalars().all()
                    
                    for session in sessions:
                        sessions_agent_map[str(session.id)] = session.agent_id
                        sessions_agent_map[str(session.public_id)] = session.agent_id
                        if session.agent_id:
                            agent_ids_to_fetch.add(str(session.agent_id))
            except Exception as e:
                logger.warning(f"Error fetching session agent information: {str(e)}")
        
        # Fetch agents for newly discovered agent_ids from messages/sessions
        if agent_ids_to_fetch:
            try:
                uuid_agent_ids = []
                bigint_agent_ids = []
                for agent_id_str in agent_ids_to_fetch:
                    if agent_id_str not in agents_map:  # Only fetch if not already fetched
                        try:
                            uuid_agent_ids.append(UUIDType(agent_id_str))
                        except (ValueError, TypeError):
                            try:
                                bigint_agent_ids.append(int(agent_id_str))
                            except (ValueError, TypeError):
                                pass
                
                conditions = []
                if uuid_agent_ids:
                    conditions.append(Agent.public_id.in_(uuid_agent_ids))
                if bigint_agent_ids:
                    conditions.append(Agent.id.in_(bigint_agent_ids))
                
                if conditions:
                    agent_query = select(Agent).where(or_(*conditions))
                    agent_result = await db.execute(agent_query)
                    agents = agent_result.scalars().all()
                    
                    # Create maps for both public_id and id lookups
                    for agent in agents:
                        agents_map[str(agent.public_id)] = agent
                        agents_map[str(agent.id)] = agent
                        if agent.icon and _is_uuid(agent.icon):
                            attachment_ids_to_fetch.add(agent.icon)
            except Exception as e:
                logger.warning(f"Error fetching agent information (second batch): {str(e)}")
        
        # Batch fetch all attachments
        attachments_map = {}
        if attachment_ids_to_fetch:
            try:
                attachment_uuid_ids = []
                for att_id in attachment_ids_to_fetch:
                    try:
                        attachment_uuid_ids.append(UUIDType(att_id))
                    except (ValueError, TypeError):
                        pass
                
                if attachment_uuid_ids:
                    attachment_query = select(Attachment).where(
                        Attachment.id.in_(attachment_uuid_ids),
                        Attachment.is_active == True
                    )
                    attachment_result = await db.execute(attachment_query)
                    attachments = attachment_result.scalars().all()
                    
                    for attachment in attachments:
                        if attachment.blob_url:
                            attachments_map[str(attachment.id)] = attachment.blob_url
            except Exception as e:
                logger.warning(f"Error batch fetching attachments: {str(e)}")
        
        # Fetch user information for all feedback items
        from aldar_middleware.models.user import User
        users_map = {}
        user_ids_to_fetch = set()
        for feedback in feedback_list:
            if feedback.user_id:
                user_ids_to_fetch.add(feedback.user_id)
        
        if user_ids_to_fetch:
            try:
                from uuid import UUID as UUIDType
                user_uuid_ids = []
                for user_id_str in user_ids_to_fetch:
                    try:
                        user_uuid_ids.append(UUIDType(user_id_str))
                    except (ValueError, TypeError):
                        pass
                
                if user_uuid_ids:
                    user_query = select(User).where(User.id.in_(user_uuid_ids))
                    user_result = await db.execute(user_query)
                    users = user_result.scalars().all()
                    
                    for user in users:
                        # Get full_name, fallback to azure_display_name or construct from first_name + last_name
                        full_name = user.full_name
                        if not full_name:
                            if user.azure_display_name:
                                full_name = user.azure_display_name
                            elif user.first_name or user.last_name:
                                full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
                        
                        # Get profile photo URL - check preferences first, then fallback to proxy endpoint
                        from aldar_middleware.utils.user_utils import get_profile_photo_url
                        profile_photo = get_profile_photo_url(user)
                        if not profile_photo and user.azure_ad_id:
                            from aldar_middleware.settings import settings
                            profile_photo = f"{settings.api_prefix}/auth/users/{user.id}/profile-photo"
                        
                        users_map[str(user.id)] = {
                            "full_name": full_name,
                            "email": user.email,
                            "profile_photo": profile_photo,
                            "department": user.azure_department,
                            "job_title": user.azure_job_title,
                            "company": user.company,
                            "external_id": user.external_id,
                            "azure_display_name": user.azure_display_name,
                            "azure_upn": user.azure_upn
                        }
            except Exception as e:
                logger.warning(f"Error fetching user information: {str(e)}")
        
        # Build response with agent information (OPTIMIZED - no per-item queries)
        feedback_responses = []
        for feedback in feedback_list:
            feedback_dict = FeedbackResponse.model_validate(feedback).model_dump()
            
            # Add user information
            user_info = users_map.get(str(feedback.user_id))
            if user_info:
                feedback_dict["user_full_name"] = user_info.get("full_name")
                feedback_dict["user_profile_photo"] = user_info.get("profile_photo")
                feedback_dict["user_department"] = user_info.get("department")
                feedback_dict["user_job_title"] = user_info.get("job_title")
                feedback_dict["user_company"] = user_info.get("company")
                feedback_dict["user_external_id"] = user_info.get("external_id")
                feedback_dict["user_azure_display_name"] = user_info.get("azure_display_name")
                feedback_dict["user_azure_upn"] = user_info.get("azure_upn")
            else:
                feedback_dict["user_full_name"] = None
                feedback_dict["user_profile_photo"] = None
                feedback_dict["user_department"] = None
                feedback_dict["user_job_title"] = None
                feedback_dict["user_company"] = None
                feedback_dict["user_external_id"] = None
                feedback_dict["user_azure_display_name"] = None
                feedback_dict["user_azure_upn"] = None
            
            # Determine agent_id - from feedback, or derived from message/session (simplified)
            resolved_agent_id = None
            if feedback.agent_id:
                resolved_agent_id = feedback.agent_id
            elif feedback.entity_type == FeedbackEntityType.MESSAGE:
                resolved_agent_id = messages_agent_map.get(feedback.entity_id)
            elif feedback.entity_type == FeedbackEntityType.SESSION:
                resolved_agent_id = sessions_agent_map.get(feedback.entity_id)
            
            # Add agent information if available
            if resolved_agent_id and str(resolved_agent_id) in agents_map:
                agent = agents_map[str(resolved_agent_id)]
                feedback_dict["agent_id"] = str(resolved_agent_id)
                feedback_dict["agent_name"] = agent.name
                feedback_dict["agent_public_id"] = str(agent.public_id)
                
                # Resolve agent_thumbnail from pre-fetched attachments map
                agent_icon = agent.icon
                if agent_icon:
                    if _is_uuid(agent_icon):
                        # Look up in batch-fetched attachments
                        feedback_dict["agent_thumbnail"] = attachments_map.get(agent_icon, agent_icon)
                    else:
                        feedback_dict["agent_thumbnail"] = agent_icon
                else:
                    feedback_dict["agent_thumbnail"] = None
            else:
                feedback_dict["agent_name"] = None
                feedback_dict["agent_public_id"] = None
                feedback_dict["agent_thumbnail"] = None

            feedback_responses.append(FeedbackResponse(**feedback_dict))

        # Total count is already calculated correctly in the service with search filters applied
        total_pages = (total + limit - 1) // limit if total > 0 else 0

        return PaginatedResponse(
            items=feedback_responses,
            total=total,
            page=page,
            limit=limit,
            total_pages=total_pages,
        )

    except Exception as e:
        logger.error(
            f"Failed to list feedback: {str(e)}",
            extra={"correlation_id": correlation_id},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list feedback",
        )


@router.delete(
    "/{feedback_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        404: {"model": ErrorResponse, "description": "Feedback not found"},
    },
)
async def delete_feedback(
    feedback_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Soft delete feedback.
    
    Users can delete their own feedback. Admins can delete any feedback.
    """
    correlation_id = get_correlation_id()
    user_id = str(current_user.id)

    try:
        feedback_service = FeedbackService(db)
        
        # Check if user is admin
        is_admin = getattr(current_user, "is_admin", False)
        
        # If not admin, filter to user's own feedback
        filter_user_id = None if is_admin else user_id
        
        deleted = await feedback_service.soft_delete_feedback(
            feedback_id=feedback_id,
            user_id=filter_user_id,
        )

        if not deleted:
            logger.warning(
                "Feedback not found for deletion",
                extra={
                    "correlation_id": correlation_id,
                    "feedback_id": feedback_id,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Feedback not found",
            )

        await db.commit()

        logger.info(
            "Feedback deleted",
            extra={
                "correlation_id": correlation_id,
                "feedback_id": feedback_id,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to delete feedback: {str(e)}",
            extra={
                "correlation_id": correlation_id,
                "feedback_id": feedback_id,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete feedback",
        )


@router.get(
    "/analytics/summary",
    response_model=FeedbackAnalyticsSummary,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
    },
)
async def get_analytics_summary(
    entity_type: Optional[FeedbackEntityType] = Query(None),
    agent_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackAnalyticsSummary:
    """
    Get feedback analytics summary.
    
    Requires admin role.
    """
    correlation_id = get_correlation_id()

    # Check admin role
    if not getattr(current_user, "is_admin", False):
        logger.warning(
            "Unauthorized analytics access",
            extra={
                "correlation_id": correlation_id,
                "user_id": str(current_user.id),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    try:
        analytics_service = FeedbackAnalyticsService(db)
        summary = await analytics_service.get_analytics_summary(
            entity_type=entity_type,
            agent_id=agent_id,
        )

        return FeedbackAnalyticsSummary.model_validate(summary)

    except Exception as e:
        logger.error(
            f"Failed to get analytics summary: {str(e)}",
            extra={"correlation_id": correlation_id},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get analytics",
        )


@router.get(
    "/analytics/trends",
    response_model=list[FeedbackTrendsResponse],
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
    },
)
async def get_feedback_trends(
    days_back: int = Query(7, ge=1, le=90),
    entity_type: Optional[FeedbackEntityType] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[FeedbackTrendsResponse]:
    """
    Get feedback trends over time.
    
    Requires admin role.
    """
    correlation_id = get_correlation_id()

    # Check admin role
    if not getattr(current_user, "is_admin", False):
        logger.warning(
            "Unauthorized analytics access",
            extra={
                "correlation_id": correlation_id,
                "user_id": str(current_user.id),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    try:
        analytics_service = FeedbackAnalyticsService(db)
        trends = await analytics_service.get_trends(
            entity_type=entity_type,
            days_back=days_back,
        )

        return [FeedbackTrendsResponse.model_validate(t) for t in trends]

    except Exception as e:
        logger.error(
            f"Failed to get trends: {str(e)}",
            extra={"correlation_id": correlation_id},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get trends",
        )


@router.get(
    "/export/csv",
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
    },
)
async def export_feedback_csv(
    entity_type: Optional[FeedbackEntityType] = Query(None),
    entity_id: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    rating: Optional[FeedbackRating] = Query(None),
    date_from: Optional[datetime] = Query(None, description="Filter feedback from this date (ISO format)"),
    date_to: Optional[datetime] = Query(None, description="Filter feedback to this date (ISO format)"),
    sort_by: Optional[str] = Query(None, description="Sort by field: user_email, user_full_name, date, comment"),
    sort_order: Optional[str] = Query("DESC", description="Sort order: ASC or DESC"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Export feedback as CSV.
    
    Users export only their own feedback. Admins can export all feedback.
    """
    correlation_id = get_correlation_id()
    user_id = str(current_user.id)

    try:
        feedback_service = FeedbackService(db)
        
        # Check if user is admin
        is_admin = getattr(current_user, "is_admin", False)
        
        # If not admin, filter to user's own feedback
        filter_user_id = None if is_admin else user_id
        
        feedback_list, _ = await feedback_service.list_feedback(
            user_id=filter_user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            agent_id=agent_id,
            rating=rating,
            date_from=date_from,
            date_to=date_to,
            page=1,
            limit=10000,
            exclude_user_id=is_admin,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        # Create CSV
        csv_buffer = io.StringIO()
        csv_writer = csv.DictWriter(
            csv_buffer,
            fieldnames=[
                "feedback_id",
                "user_id",
                "user_email",
                "entity_id",
                "entity_type",
                "agent_id",
                "rating",
                "comment",
                "file_count",
                "correlation_id",
                "created_at",
                "updated_at",
            ],
        )

        csv_writer.writeheader()
        for feedback in feedback_list:
            csv_writer.writerow({
                "feedback_id": str(feedback.feedback_id),
                "user_id": feedback.user_id,
                "user_email": feedback.user_email or "",
                "entity_id": feedback.entity_id,
                "entity_type": feedback.entity_type.value,
                "agent_id": feedback.agent_id or "",
                "rating": feedback.rating.value,
                # Fallback: export feedback_id in comment when original comment is missing
                "comment": ((feedback.comment or str(feedback.feedback_id)) or "").replace("\n", " "),
                "file_count": len(feedback.files),
                "correlation_id": feedback.correlation_id or "",
                "created_at": feedback.created_at.isoformat(),
                "updated_at": feedback.updated_at.isoformat(),
            })

        csv_data = csv_buffer.getvalue()

        logger.info(
            "Feedback exported as CSV",
            extra={
                "correlation_id": correlation_id,
                "row_count": len(feedback_list),
            },
        )

        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=feedback.csv"},
        )

    except Exception as e:
        logger.error(
            f"Failed to export feedback: {str(e)}",
            extra={"correlation_id": correlation_id},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to export feedback",
        )