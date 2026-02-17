"""User memory analysis API routes."""

import json
import logging
import uuid
from datetime import datetime
from typing import Dict, Any, Optional
import httpx

from fastapi import APIRouter, Depends, HTTPException, status, Body, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from aldar_middleware.database.base import get_db
from aldar_middleware.models.user import User
from aldar_middleware.models.agno_memory import AgnoMemory
from aldar_middleware.models.sessions import Session
from aldar_middleware.models.messages import Message
from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.orchestration.agno import agno_api_service
from aldar_middleware.settings.context import get_correlation_id, get_user_id
from aldar_middleware.settings import settings
from aldar_middleware.routes.azure_ad_obo import get_user_access_token_auto

logger = logging.getLogger(__name__)

router = APIRouter()


def parse_json_string(json_str: str) -> Dict[str, Any]:
    """
    Parse a JSON string, handling markdown code fences.
    
    Args:
        json_str: JSON string that may be wrapped in markdown code fence (```json\\n...\\n```)
    
    Returns:
        Parsed dictionary or empty dict if parsing fails
    """
    if not isinstance(json_str, str):
        return json_str if isinstance(json_str, dict) else {}
    
    try:
        # Strip markdown code fence if present (```json\n...\n```)
        json_str = json_str.strip()
        if json_str.startswith("```json"):
            # Remove ```json at start and ``` at end
            json_str = json_str[7:]  # Remove ```json
            if json_str.endswith("```"):
                json_str = json_str[:-3]  # Remove trailing ```
            json_str = json_str.strip()
        elif json_str.startswith("```"):
            # Remove generic ``` code fence
            json_str = json_str[3:]  # Remove ```
            if json_str.endswith("```"):
                json_str = json_str[:-3]  # Remove trailing ```
            json_str = json_str.strip()
        
        # Parse the cleaned JSON string
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON string: {e}, original: {json_str[:200]}")
        return {}


# Request/Response models
class AnalyzeUserMemoryRequest(BaseModel):
    """Request model for analyzing user memory."""
    user_input: str = Field(..., description="User input to analyze and store in memory")
    session_id: str = Field(..., description="Session ID for the user conversation")
    message_id: str = Field(..., description="Message ID to store the analysis in message metadata")


class CreateUserMemoryRequest(BaseModel):
    """Request model for creating user memory."""
    topic: list[str] = Field(..., description="Memory topics/categories")
    text: str = Field(..., description="Memory content text")
    confidence: str = Field(default="low", description="Confidence level: high, medium, low")
    session_id: str = Field(..., description="Session ID for the user conversation")
    message_id: str = Field(..., description="Message ID where the memory analysis is stored")


class UpdateUserMemoryRequest(BaseModel):
    """Request model for updating user memory."""
    memory_id: str = Field(..., description="ID of the memory to update")
    topic: list[str] = Field(..., description="Updated memory topics/categories")
    text: str = Field(..., description="Updated memory content text")
    confidence: str = Field(default="low", description="Confidence level: high, medium, low")
    previous_value: Optional[str] = Field(None, description="Previous memory value before update")
    session_id: str = Field(..., description="Session ID for the user conversation")
    message_id: str = Field(..., description="Message ID where the memory analysis is stored")


class DiscardUserMemoryRequest(BaseModel):
    """Request model for discarding user memory."""
    session_id: str = Field(..., description="Session ID for the user conversation")
    message_id: str = Field(..., description="Message ID where the memory analysis is stored")
    memory_text: str = Field(..., description="Memory text to discard from message metadata")


class AnalyzeUserMemoryResponse(BaseModel):
    """Response model for user memory analysis."""
    success: bool = Field(default=True, description="Whether the request was successful")
    data: Optional[Dict[str, Any]] = Field(default=None, description="Response data from AGNO API")
    message: Optional[str] = Field(default=None, description="Optional message")
    message_id: Optional[str] = Field(default=None, description="Message ID that was analyzed")
    correlation_id: Optional[str] = Field(default=None, description="Request correlation ID")


@router.post(
    "/analyze-user-memory",
    response_model=AnalyzeUserMemoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyze and store user memory",
    description="""
    Analyze user input and store relevant information in user memory.
    
    This endpoint forwards requests to the AGNO API's `/analyze-user-memory` endpoint
    to extract and store user-specific information (preferences, role, interests, etc.)
    from conversation context.
    
    **Features:**
    - Automatically saves high confidence memories to `agno_memories` table
    - Stores analysis results in message metadata
    - Returns status for each memory: "AutoSaved" (high confidence), "" (low confidence)
    
    **Example Request:**
    ```json
    {
        "user_input": "I prefer concise responses and I'm interested in machine learning and cloud architecture",
        "session_id": "xxxxxx-xxxxxx-xxxxxx-xxxxxx-xxxxxx",
        "message_id": "xxxxxx-xxxxxx-xxxxxx-xxxxxx-xxxxxx"
    }
    ```
    
    **Example Response:**
    ```json
    {
        "success": true,
        "data": {
            "status": "success",
            "message": "User memory analysis completed successfully",
            "run_id": "xxxxxx-xxxxxx-xxxxxx-xxxxxx-xxxxxx",
            "session_id": "xxxxxx-xxxxxx-xxxxxx-xxxxxx-xxxxxx",
            "user_id": "user@example.com",
            "agent_id": "user-memory-analyzer",
            "agent_name": "User Memory Analyzer",
            "analysis": {
                "memories": [
                    {
                        "action": "add",
                        "topic": ["preferences"],
                        "text": "User prefers concise responses",
                        "confidence": "high",
                        "status": "AutoSaved",
                        "memory_id": "xxxxxx-xxxxxx-xxxxxx-xxxxxx-xxxxxx"
                    },
                    {
                        "action": "add",
                        "topic": ["interests"],
                        "text": "User is interested in machine learning and cloud architecture",
                        "confidence": "low",
                        "status": ""
                    }
                ]
            },
            "streaming": false
        },
        "message": "User memory analyzed successfully",
        "correlation_id": "4e068eae-feaa-42ea-8246-cc6f59c534ba"
    }
    ```
    
    **Status Values:**
    - `AutoSaved`: High confidence memory automatically saved to database
    - `""` (empty): Low confidence memory, requires manual confirmation via `/create-user-memory`
    - `Saved`: Manually saved via `/create-user-memory` endpoint
    - `N/A`: Auto-save failed
    - `Discarded`: Memory rejected by user
    
    **Note:** The `message_id` is used to store analysis data in the specific message's
    message_metadata. The `session_id` is used for tracking. Neither is sent to the external AGNO API.
    """
)
async def analyze_user_memory(
    request_body: AnalyzeUserMemoryRequest = Body(..., description="User memory analysis request"),
    request: Request = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalyzeUserMemoryResponse:
    """
    Analyze user input and store in memory.
    
    This endpoint integrates with the AGNO API to analyze user input and extract
    relevant information for personalized interactions.
    """
    correlation_id = get_correlation_id() or "analyze-user-memory"
    user_id = str(current_user.id) if hasattr(current_user, 'id') else get_user_id()
    
    # Extract JWT token from Authorization header
    authorization_header = request.headers.get("authorization") if request else None
    
    # Get Azure AD access token for OBO flow
    user_access_token = await get_user_access_token_auto(
        current_user=current_user,
        db=db,
        provided_token=None
    )
    
    if not user_access_token:
        logger.warning(
            f"⚠️  No Azure AD access token available for user {user_id}. "
            "Attempting request without OBO token - may fail if AGNO API requires authentication."
        )
    
    logger.info(
        f"User memory analysis request: user_id={user_id}, "
            f"session_id={request_body.session_id}, message_id={request_body.message_id}, "
            f"correlation_id={correlation_id}, has_auth_header={bool(authorization_header)}, "
    )

    # Check if AGNO API is enabled
    if not settings.agno_api_enabled:
        logger.error("AGNO API is disabled in settings")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User memory analysis is currently unavailable"
        )
    
    # Check if AGNO base URL is configured
    if not settings.agno_base_url:
        logger.error("ALDAR_AGNO_BASE_URL is not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AGNO API base URL is not configured"
        )
    
    try:
        # Prepare request data for AGNO API (only send user_input, not session_id)
        request_data = {
            "user_input": request_body.user_input
        }
        
        logger.debug(
            f"📤 Forwarding to AGNO API: endpoint=/analyze-user-memory, "
            f"internal_session_id={request_body.session_id}"
        )
        
        # Make request to AGNO API with Azure AD token for OBO flow
        response_data = await agno_api_service.make_request(
            endpoint="/analyze-user-memory",
            method="POST",
            data=request_data,
            user_id=user_id,
            authorization_header=authorization_header,
            user_access_token=user_access_token,
        )
        
        logger.info(
            f"✅ User memory analysis successful: user_id={user_id}, "
            f"session_id={request_body.session_id}, correlation_id={correlation_id}"
        )
        
        # Parse the analysis field if it's a JSON string
        if response_data and "analysis" in response_data:
            analysis = parse_json_string(response_data["analysis"])
            if analysis:
                response_data["analysis"] = analysis
                logger.debug("Parsed analysis field from JSON string to object")
            
            if isinstance(analysis, dict) and "memories" in analysis:
                memories = analysis["memories"]
                user_email = current_user.email if hasattr(current_user, 'email') else user_id
                current_timestamp = int(datetime.utcnow().timestamp())
                
                for memory_item in memories:
                    confidence = memory_item.get("confidence", "").lower()
                    
                    # Auto-save memories with high confidence
                    if confidence == "high":
                        try:
                            memory_id = str(uuid.uuid4())
                            new_memory = AgnoMemory(
                                memory_id=memory_id,
                                memory=memory_item.get("text", ""),
                                user_id=user_email,
                                topics=memory_item.get("topic", []),
                                created_at=current_timestamp,
                                updated_at=current_timestamp
                            )
                            db.add(new_memory)
                            await db.commit()
                            
                            memory_item["status"] = "AutoSaved"
                            memory_item["memory_id"] = memory_id
                            logger.info(f" Auto-saved high confidence memory: memory_id={memory_id}, user_id={user_id}")
                        except Exception as db_error:
                            logger.error(f" Failed to auto-save memory: {db_error}")
                            memory_item["status"] = "N/A"
                    else:
                        # Low confidence memories get empty status
                        memory_item["status"] = ""
        
        # Store analysis data in message_metadata for this message_id
        if response_data and "analysis" in response_data and request_body.message_id:
            try:
                # Query message by id
                message_id = uuid.UUID(request_body.message_id)
                
                message_stmt = select(Message).where(Message.id == message_id)
                message_result = await db.execute(message_stmt)
                message = message_result.scalar_one_or_none()
                
                if message:
                    # Initialize message_metadata if it doesn't exist
                    if not message.message_metadata:
                        message.message_metadata = {}
                    
                    # Store analysis data in message_metadata
                    message.message_metadata["memory_analysis"] = response_data["analysis"]
                    
                    # Mark as modified for SQLAlchemy to detect the change
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(message, "message_metadata")
                    
                    await db.commit()
                    logger.info(f" Stored analysis data in message_metadata: message_id={request_body.message_id}")
                else:
                    logger.warning(f" Message not found: message_id={request_body.message_id}")
            except Exception as message_error:
                logger.error(f" Failed to store analysis in message_metadata: {message_error}")
                # Don't fail the request if message update fails
        
        return AnalyzeUserMemoryResponse(
            success=True,
            data=response_data,
            message="User memory analyzed successfully",
            message_id=request_body.message_id,
            correlation_id=correlation_id,
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    
    except ValueError as e:
        # Catch ValueError raised from agno.py for HTTP errors (404, 500, etc.)
        error_str = str(e)
        
        # Extract status code from error message if present
        if "404 Not Found" in error_str:
            error_msg = "External API endpoint not found. The requested resource does not exist."
            status_code_to_use = status.HTTP_404_NOT_FOUND
            error_type = "External API Endpoint Not Found"
        elif "500 Internal Server Error" in error_str or "Server error '5" in error_str:
            error_msg = "External API encountered an internal server error."
            status_code_to_use = status.HTTP_502_BAD_GATEWAY
            error_type = "External API Server Error"
        elif "403 Forbidden" in error_str:
            error_msg = "Access to external API is forbidden. Authorization may have failed."
            status_code_to_use = status.HTTP_502_BAD_GATEWAY
            error_type = "External API Forbidden"
        elif "401 Unauthorized" in error_str:
            error_msg = "External API requires authentication or authentication failed."
            status_code_to_use = status.HTTP_502_BAD_GATEWAY
            error_type = "External API Unauthorized"
        else:
            error_msg = "External API returned an error response."
            status_code_to_use = status.HTTP_502_BAD_GATEWAY
            error_type = "External API Error"
        
        logger.error(
            f" External API error: {error_str[:500]}, "
            f"user_id={user_id}, session_id={request_body.session_id}, "
            f"correlation_id={correlation_id}"
        )
        raise HTTPException(
            status_code=status_code_to_use,
            detail={
                "error": error_type,
                "message": error_msg,
                "details": error_str[:1000] if len(error_str) > 1000 else error_str,
                "message_id": request_body.message_id,
                "correlation_id": correlation_id
            }
        )
    
    except httpx.ConnectError as e:
        error_msg = "External API service is unavailable. Please ensure the service is running and accessible."
        logger.error(
            f" External API connection error: {str(e)}, "
            f"user_id={user_id}, session_id={request_body.session_id}, "
            f"correlation_id={correlation_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "External API Unavailable",
                "message": error_msg,
                "details": str(e),
                "message_id": request_body.message_id,
                "correlation_id": correlation_id
            }
        )
    
    except httpx.TimeoutException as e:
        error_msg = "External API request timed out. The service may be overloaded or unresponsive."
        logger.error(
            f" External API timeout: {str(e)}, "
            f"user_id={user_id}, session_id={request_body.session_id}, "
            f"correlation_id={correlation_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "error": "External API Timeout",
                "message": error_msg,
                "details": str(e),
                "message_id": request_body.message_id,
                "correlation_id": correlation_id
            }
        )
    
    except httpx.HTTPStatusError as e:
        error_detail = getattr(e.response, 'text', 'No error details available')
        logger.error(
            f" External API HTTP error: status={e.response.status_code}, "
            f"user_id={user_id}, session_id={request_body.session_id}, "
            f"correlation_id={correlation_id}, details={error_detail[:200]}"
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "External API Error",
                "message": f"External API returned an error (status {e.response.status_code})",
                "details": error_detail[:500],
                "message_id": request_body.message_id,
                "correlation_id": correlation_id
            }
        )
    
    except httpx.RequestError as e:
        error_msg = str(e) or f"Request error: {type(e).__name__}"
        logger.error(
            f" External API request error: {error_msg}, "
            f"user_id={user_id}, session_id={request_body.session_id}, "
            f"correlation_id={correlation_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "External API Request Failed",
                "message": "Failed to communicate with external API service",
                "details": error_msg,
                "message_id": request_body.message_id,
                "correlation_id": correlation_id
            }
        )
        
    except Exception as e:
        logger.error(
            f" Unexpected error analyzing user memory: {str(e)}, "
            f"user_id={user_id}, session_id={request_body.session_id}, "
            f"correlation_id={correlation_id}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Internal Server Error",
                "message": "An unexpected error occurred while processing your request",
                "details": str(e),
                "message_id": request_body.message_id,
                "correlation_id": correlation_id
            }
        )


@router.post(
    "/create-user-memory",
    response_model=AnalyzeUserMemoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create user memory",
    description="""
    Create a new user memory entry.
    
    This endpoint is used when the action is "add" from the analyze-user-memory response.
    Stores data directly in the agno_memories table and updates message metadata status to "Saved".
    
    **Message Metadata Update:**
    - Updates the status in message.message_metadata["memory_analysis"]["memories"]
    - Changes status from "" or "AutoSaved" to "Saved"
    - Adds memory_id to the memory item for reference
    
    **Example Request:**
    ```json
    {
        "topic": ["occupation", "department"],
        "text": "User is a Lead Consultant in the Data & AI department",
        "confidence": "high",
        "session_id": "xxxxxx-xxxxxx-xxxxxx-xxxxxx-xxxxxx",
        "message_id": "xxxxxx-xxxxxx-xxxxxx-xxxxxx-xxxxxx"
    }
    ```
    
    **Example Response:**
    ```json
    {
        "success": true,
        "data": {
            "session_id": "xxxxxx-xxxxxx-xxxxxx-xxxxxx-xxxxxx",
            "memory_id": "xxxxxx-xxxxxx-xxxxxx-xxxxxx-xxxxxx",
            "memory": "User is a Lead Consultant in the Data & AI department",
            "confidence": "high",
            "topics": ["occupation", "department"],
            "status": "created"
        },
        "message": "User memory created successfully",
        "correlation_id": "abc123-def456"
    }
    ```
    """
)
async def create_user_memory(
    request_body: CreateUserMemoryRequest = Body(..., description="Create user memory request"),
    request: Request = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalyzeUserMemoryResponse:
    """
    Create a new user memory entry.
    
    This endpoint is used after analyzing user input to store new memories.
    Stores data directly in the agno_memories table without calling external API.
    """
    correlation_id = get_correlation_id() or "create-user-memory"
    user_id = str(current_user.id) if hasattr(current_user, 'id') else get_user_id()
    user_email = current_user.email if hasattr(current_user, 'email') else user_id
    
    logger.info(
        f" Create user memory request: user_id={user_id}, "
        f"topic={request_body.topic}, session_id={request_body.session_id}, "
        f"correlation_id={correlation_id}"
    )
    
    try:
        # Generate new memory ID as string
        memory_id = str(uuid.uuid4())
        current_timestamp = int(datetime.utcnow().timestamp())
        
        # Create new memory in database (store text directly in JSONB column)
        new_memory = AgnoMemory(
            memory_id=memory_id,
            memory=request_body.text,  # Store as plain string in JSONB
            user_id=user_email,
            topics=request_body.topic,
            created_at=current_timestamp,
            updated_at=current_timestamp
        )
        db.add(new_memory)
        await db.commit()
        
        logger.info(f" User memory created in database: memory_id={memory_id}, user_id={user_id}")
        
        # Update status in message_metadata from "" to "Saved"
        if request_body.message_id:
            try:
                message_id = uuid.UUID(request_body.message_id)
                message_stmt = select(Message).where(Message.id == message_id)
                message_result = await db.execute(message_stmt)
                message = message_result.scalar_one_or_none()
                
                if message and message.message_metadata and "memory_analysis" in message.message_metadata:
                    memory_analysis = parse_json_string(message.message_metadata["memory_analysis"])
                    memories = memory_analysis.get("memories", []) if memory_analysis else []
                    
                    # Find and update the matching memory status
                    for memory_item in memories:
                        # Match by text and update status to "Saved" (allows recovering discarded memories)
                        if memory_item.get("text") == request_body.text:
                            current_status = memory_item.get("status", "")
                            if current_status in ["", "AutoSaved", "discarded"]:
                                memory_item["status"] = "Saved"
                                memory_item["memory_id"] = memory_id
                                
                                # Mark as modified for SQLAlchemy
                                from sqlalchemy.orm.attributes import flag_modified
                                flag_modified(message, "message_metadata")
                                
                                await db.commit()
                                logger.info(f" Updated memory status from '{current_status}' to 'Saved' in message_metadata: message_id={request_body.message_id}")
                                break
            except Exception as message_error:
                logger.warning(f" Failed to update message_metadata: {message_error}")
                # Don't fail the request if message update fails
        
        # Format response with session_id for traceability
        formatted_response = {
            "session_id": request_body.session_id,
            "memory_id": memory_id,
            "memory": request_body.text,
            "confidence": request_body.confidence,
            "topics": request_body.topic,
            "status": "created"
        }
        
        return AnalyzeUserMemoryResponse(
            success=True,
            data=formatted_response,
            message="User memory created successfully",
            correlation_id=correlation_id,
        )
        
    except Exception as e:
        logger.error(
            f" Error creating user memory: {str(e)}, "
            f"user_id={user_id}, session_id={request_body.session_id}, "
            f"correlation_id={correlation_id}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create user memory: {str(e)}"
        )


@router.put(
    "/update-user-memory",
    response_model=AnalyzeUserMemoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Update user memory",
    description="""
    Update an existing user memory entry.
    
    This endpoint is used when the action is "update" from the analyze-user-memory response.
    Updates data directly in the agno_memories table and updates message metadata status to "Saved".
    
    **Message Metadata Update:**
    - Updates the status in message.message_metadata["memory_analysis"]["memories"]
    - Changes status from "" or "AutoSaved" or "discarded" to "Saved"
    - Adds/updates memory_id in the memory item for reference
    
    **Example Request:**
    ```json
    {
        "memory_id": "b0be6ef6-7288-498a-92b3-e544ccda3ea3",
        "topic": ["occupation"],
        "text": "User is a Principal Consultant in the Data & AI department",
        "confidence": "high",
        "previous_value": "User works as a Consultant in the Data & AI department.",
        "session_id": "xxxxxx-xxxxxx-xxxxxx-xxxxxx-xxxxxx",
        "message_id": "xxxxxx-xxxxxx-xxxxxx-xxxxxx-xxxxxx"
    }
    ```
    
    **Example Response:**
    ```json
    {
        "success": true,
        "data": {
            "session_id": "82a24763-60a5-415a-a694-4ddb7a783f60",
            "memory_id": "b0be6ef6-7288-498a-92b3-e544ccda3ea3",
            "memory": "User is a Principal Consultant in the Data & AI department",
            "confidence": "high",
            "topics": ["occupation"],
            "previous_value": "User works as a Consultant in the Data & AI department.",
            "status": "updated"
        },
        "message": "User memory updated successfully",
        "correlation_id": "abc123-def456"
    }
    ```
    """,
)
async def update_user_memory(
    request_body: UpdateUserMemoryRequest = Body(..., description="Update user memory request"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalyzeUserMemoryResponse:
    """
    Update an existing user memory entry.
    
    Updates data directly in the agno_memories table without calling external API.
    """
    correlation_id = get_correlation_id() or "update-user-memory"
    user_id = str(current_user.id) if hasattr(current_user, 'id') else get_user_id()
    
    logger.info(
        f"📝 Update user memory request: user_id={user_id}, "
        f"memory_id={request_body.memory_id}, topic={request_body.topic}, "
        f"session_id={request_body.session_id}, correlation_id={correlation_id}"
    )
    
    try:
        current_timestamp = int(datetime.utcnow().timestamp())
        memory_id = request_body.memory_id  # Use string directly, no UUID conversion
        
        # Check if memory exists
        select_stmt = select(AgnoMemory).where(AgnoMemory.memory_id == memory_id)
        result = await db.execute(select_stmt)
        existing_memory = result.scalar_one_or_none()
        
        if not existing_memory:
            logger.error(f"❌ Memory not found: memory_id={memory_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Memory with ID {memory_id} not found"
            )
        
        # Get previous memory value for response (stored as JSONB string)
        previous_value = str(existing_memory.memory) if existing_memory.memory else ""
        
        # Update memory in database (store text directly in JSONB column)
        update_stmt = (
            update(AgnoMemory)
            .where(AgnoMemory.memory_id == memory_id)
            .values(
                memory=request_body.text,  # Store as plain string in JSONB
                topics=request_body.topic,
                updated_at=current_timestamp
            )
        )
        
        await db.execute(update_stmt)
        await db.commit()
        
        logger.info(f"✅ User memory updated in database: memory_id={memory_id}, user_id={user_id}")
        
        # Update status in message_metadata to "Saved"
        if request_body.message_id:
            try:
                message_id = uuid.UUID(request_body.message_id)
                message_stmt = select(Message).where(Message.id == message_id)
                message_result = await db.execute(message_stmt)
                message = message_result.scalar_one_or_none()
                
                if message and message.message_metadata and "memory_analysis" in message.message_metadata:
                    memory_analysis = parse_json_string(message.message_metadata["memory_analysis"])
                    memories = memory_analysis.get("memories", []) if memory_analysis else []
                    
                    # Find and update the matching memory status
                    for memory_item in memories:
                        # Match by text or previous_memory_id and update status to "Saved"
                        if (memory_item.get("text") == request_body.text or 
                            memory_item.get("previous_memory_id") == memory_id):
                            current_status = memory_item.get("status", "")
                            if current_status in ["", "AutoSaved", "discarded"]:
                                memory_item["status"] = "Saved"
                                memory_item["memory_id"] = memory_id
                                
                                # Mark as modified for SQLAlchemy
                                from sqlalchemy.orm.attributes import flag_modified
                                flag_modified(message, "message_metadata")
                                
                                await db.commit()
                                logger.info(f" Updated memory status from '{current_status}' to 'Saved' in message_metadata: message_id={request_body.message_id}")
                                break
            except Exception as message_error:
                logger.warning(f" Failed to update message_metadata: {message_error}")
                # Don't fail the request if message update fails
        
        # Format response with session_id for traceability
        formatted_response = {
            "session_id": request_body.session_id,
            "memory_id": memory_id,
            "memory": request_body.text,
            "confidence": request_body.confidence,
            "topics": request_body.topic,
            "previous_value": previous_value,
            "status": "updated"
        }
        
        return AnalyzeUserMemoryResponse(
            success=True,
            data=formatted_response,
            message="User memory updated successfully",
            message_id=request_body.message_id,
            correlation_id=correlation_id,
        )
        
    except HTTPException:
        raise
        
    except Exception as e:
        logger.error(
            f" Error updating user memory: {str(e)}, "
            f"user_id={user_id}, memory_id={request_body.memory_id}, "
            f"session_id={request_body.session_id}, correlation_id={correlation_id}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update user memory: {str(e)}"
        )


@router.post(
    "/discard-user-memory",
    response_model=AnalyzeUserMemoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Discard user memory from message",
    description="""
    Discard/mark a user memory as discarded in message metadata.
    
    This endpoint is used when the user chooses to discard a memory suggestion
    from the UI. It updates the memory status to "discarded" in the message metadata.
    
    **Features:**
    - Updates memory status to "discarded" in message metadata
    - Does NOT delete the memory from the message metadata
    - Does NOT delete the memory from database if it was already saved
    - Only affects the memory status in message metadata
    
    **Example Request:**
    ```json
    {
        "session_id": "xxxxxx-xxxxxx-xxxxxx-xxxxxx-xxxxxx",
        "message_id": "xxxxxx-xxxxxx-xxxxxx-xxxxxx-xxxxxx",
        "memory_text": "User is interested in machine learning and cloud architecture"
    }
    ```
    
    **Example Response:**
    ```json
    {
        "success": true,
        "data": {
            "session_id": "xxxxxx-xxxxxx-xxxxxx-xxxxxx-xxxxxx",
            "message_id": "xxxxxx-xxxxxx-xxxxxx-xxxxxx-xxxxxx",
            "memory_text": "User is interested in machine learning and cloud architecture",
            "status": "discarded"
        },
        "message": "User memory discarded successfully",
        "correlation_id": "abc123-def456"
    }
    ```
    
    **Note:** This endpoint marks the memory as discarded in message metadata. If the memory was already
    saved to the database (status="AutoSaved" or "Saved"), it will remain in the database.
    Use the delete endpoint if you want to remove saved memories from the database.
    """
)
async def discard_user_memory(
    request_body: DiscardUserMemoryRequest = Body(..., description="Discard user memory request"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalyzeUserMemoryResponse:
    """
    Discard a user memory from message metadata.
    
    This endpoint updates the memory status to "discarded" in the message metadata without
    deleting it from the message metadata or database.
    """
    correlation_id = get_correlation_id() or "discard-user-memory"
    user_id = str(current_user.id) if hasattr(current_user, 'id') else get_user_id()
    
    logger.info(
        f" Discard user memory request: user_id={user_id}, "
        f"session_id={request_body.session_id}, message_id={request_body.message_id}, "
        f"memory_text={request_body.memory_text[:50]}..., correlation_id={correlation_id}"
    )
    
    try:
        # Query message by id
        message_id = uuid.UUID(request_body.message_id)
        message_stmt = select(Message).where(Message.id == message_id)
        message_result = await db.execute(message_stmt)
        message = message_result.scalar_one_or_none()
        
        if not message:
            logger.error(f" Message not found: message_id={request_body.message_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message with ID {request_body.message_id} not found"
            )
        
        # Check if message has memory analysis data
        if not message.message_metadata or "memory_analysis" not in message.message_metadata:
            logger.error(f" No memory analysis found in message: message_id={request_body.message_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No memory analysis found in this message"
            )
        
        memory_analysis = parse_json_string(message.message_metadata["memory_analysis"])
        memories = memory_analysis.get("memories", []) if memory_analysis else []
        memory_found = False
        
        # Find the matching memory and update status to "discarded"
        for memory_item in memories:
            if memory_item.get("text") == request_body.memory_text:
                current_status = memory_item.get("status", "")
                
                if current_status in ["AutoSaved", "Saved"]:
                    logger.warning(
                        f" Memory already saved to database: message_id={request_body.message_id}, "
                        f"status={current_status}. Marking as discarded in message metadata but not deleting from database."
                    )
                
                # Update status to "discarded" instead of removing
                memory_item["status"] = "discarded"
                memory_found = True
                break
        
        # Update the message metadata
        if memory_found:
            # Mark as modified for SQLAlchemy
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(message, "message_metadata")
            
            await db.commit()
            logger.info(
                f" Memory status updated to 'discarded' in message_metadata: "
                f"message_id={request_body.message_id}"
            )
        
        if not memory_found:
            logger.error(
                f" Memory not found in message: message_id={request_body.message_id}, "
                f"memory_text={request_body.memory_text[:50]}..."
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Memory not found in message metadata"
            )
        
        # Format response
        formatted_response = {
            "session_id": request_body.session_id,
            "message_id": request_body.message_id,
            "memory_text": request_body.memory_text,
            "status": "discarded"
        }
        
        return AnalyzeUserMemoryResponse(
            success=True,
            data=formatted_response,
            message="User memory discarded successfully",
            correlation_id=correlation_id,
        )
        
    except HTTPException:
        raise
        
    except Exception as e:
        logger.error(
            f" Error discarding user memory: {str(e)}, "
            f"user_id={user_id}, session_id={request_body.session_id}, "
            f"correlation_id={correlation_id}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to discard user memory: {str(e)}"
        )
