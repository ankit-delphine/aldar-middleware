"""Context memory API routes."""

import logging
import re
from typing import Optional, List, Set
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, or_, func, text
from sqlalchemy.sql import cast
from sqlalchemy.types import String

from aldar_middleware.database.base import get_db
from aldar_middleware.models.user import User
from aldar_middleware.models.agno_memory import AgnoMemory
from aldar_middleware.models.sessions import Session
from aldar_middleware.models.messages import Message
from aldar_middleware.auth.dependencies import get_current_user
from sqlalchemy.orm.attributes import flag_modified
from aldar_middleware.schemas.context_memory import (
    ContextMemoryResponse,
    ContextMemoryListResponse,
    DeleteMemoryResponse,
    BulkDeleteMemoryRequest,
    BulkDeleteMemoryResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _capitalize_first_letter(text: str) -> str:
    """Capitalize the first alphabetical character in a string."""
    for index, char in enumerate(text):
        if char.isalpha():
            return f"{text[:index]}{char.upper()}{text[index + 1:]}"
    return text


def _format_memory_for_response(memory_text: Optional[str], current_user: User) -> str:
    """Format memory text for API response by removing user-identifying prefixes."""
    if memory_text is None:
        return ""

    formatted = memory_text.strip()
    if not formatted:
        return formatted

    static_prefixes = [
        r"user(?:'s)?",
        r"client(?:'s)?",
    ]

    dynamic_prefixes: List[str] = []
    if current_user.full_name:
        dynamic_prefixes.append(current_user.full_name.strip())
    if current_user.first_name and current_user.last_name:
        dynamic_prefixes.append(f"{current_user.first_name.strip()} {current_user.last_name.strip()}")
    if current_user.first_name:
        dynamic_prefixes.append(current_user.first_name.strip())

    normalized_dynamic_prefixes = [prefix for prefix in dynamic_prefixes if prefix]

    changed = True
    while changed and formatted:
        changed = False

        for prefix_pattern in static_prefixes:
            updated = re.sub(
                rf"^\s*{prefix_pattern}\b[\s:,-]*",
                "",
                formatted,
                flags=re.IGNORECASE,
            )
            if updated != formatted:
                formatted = updated.lstrip()
                changed = True
                break

        if changed:
            continue

        for prefix in normalized_dynamic_prefixes:
            updated = re.sub(
                rf"^\s*{re.escape(prefix)}\b[\s:,-]*",
                "",
                formatted,
                flags=re.IGNORECASE,
            )
            if updated != formatted:
                formatted = updated.lstrip()
                changed = True
                break

    return _capitalize_first_letter(formatted)


async def _mark_memories_discarded_in_messages(
    db: AsyncSession,
    user: User,
    memory_ids: Set[str],
) -> int:
    """Mark memory status as discarded in message metadata for given memory IDs."""
    messages_query = select(Message).where(
        Message.message_metadata.isnot(None)
    ).join(Session).where(Session.user_id == user.id)
    messages_result = await db.execute(messages_query)
    messages = messages_result.scalars().all()

    updated_messages = 0
    for message in messages:
        if not message.message_metadata or not isinstance(message.message_metadata, dict):
            continue

        memory_analysis = message.message_metadata.get("memory_analysis")
        if not memory_analysis or not isinstance(memory_analysis, dict):
            continue

        memories = memory_analysis.get("memories")
        if not memories or not isinstance(memories, list):
            continue

        message_updated = False
        for memory_item in memories:
            if memory_item.get("memory_id") in memory_ids:
                memory_item["status"] = "discarded"
                message_updated = True

        if message_updated:
            flag_modified(message, "message_metadata")
            updated_messages += 1

    return updated_messages


@router.get(
    "/context-memory",
    response_model=ContextMemoryListResponse,
    summary="Get user's context memory",
    description="Retrieve all context memories for the logged-in user with optional search and topic filtering"
)
async def get_context_memories(
    search: Optional[str] = Query(None, description="Search in memory content"),
    topic: Optional[str] = Query(
        None,
        description="Filter by topic (e.g., 'department', 'occupation', 'organization')"
    ),
    category_search: Optional[str] = Query(None, description="Search in category/topic names"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(100, ge=1, le=1000, description="Number of items per page"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ContextMemoryListResponse:
    """
    Get context memories for the current user.
    
    Args:
        search: Optional search query to filter memories by content
        topic: Optional topic to filter memories (e.g., 'department', 'occupation')
        page: Page number (1-indexed)
        limit: Number of items per page
        current_user: Current authenticated user
        db: Database session
        
    Returns:
        Paginated list of context memories with metadata
    """
    try:
        logger.info(
            f" Fetching context memories for user: {current_user.email}, "
            f"category_search={category_search}, topic={topic}, search={search}, "
            f"page={page}, limit={limit}"
        )
        
        # Build base query filtering by user email
        query = select(AgnoMemory).where(
            AgnoMemory.user_id == current_user.email
        )
        
        # Apply search filter on memory content if provided
        if search:
            query = query.where(
                cast(AgnoMemory.memory, String).ilike(f"%{search}%")
            )
        
        # Apply topic filter if provided
        # Topics are stored as JSON array, so we need to check if the topic exists in the array
        if topic:
            # PostgreSQL: Check if the topics JSON array contains the specified topic
            # We use the @> operator which checks if left JSON contains right JSON
            query = query.where(
                text("topics::jsonb @> :topic_array")
            ).params(topic_array=f'["{topic}"]')
        
        # Apply category_search filter if provided (searches within topics)
        if category_search:
            # Split comma-separated categories and search for any match
            categories = [cat.strip() for cat in category_search.split(',')]
            
            # Build OR condition for multiple categories
            if len(categories) == 1:
                # Single category - simple ILIKE search
                query = query.where(
                    text("EXISTS (SELECT 1 FROM jsonb_array_elements_text(topics) AS topic WHERE topic ILIKE :category_search)")
                ).params(category_search=f"%{categories[0]}%")
            else:
                # Multiple categories - search for any match using OR
                # Build SQL with multiple ILIKE conditions
                or_conditions = " OR ".join([f"topic ILIKE :cat{i}" for i in range(len(categories))])
                query = query.where(
                    text(f"EXISTS (SELECT 1 FROM jsonb_array_elements_text(topics) AS topic WHERE {or_conditions})")
                ).params(**{f"cat{i}": f"%{cat}%" for i, cat in enumerate(categories)})
        
        # Get total count before pagination
        from sqlalchemy import func as sql_func
        count_query = select(sql_func.count()).select_from(AgnoMemory).where(
            AgnoMemory.user_id == current_user.email
        )
        
        # Apply same filters to count query
        if search:
            count_query = count_query.where(
                cast(AgnoMemory.memory, String).ilike(f"%{search}%")
            )
        if topic:
            count_query = count_query.where(
                text("topics::jsonb @> :topic_array")
            ).params(topic_array=f'["{topic}"]')
        if category_search:
            # Split comma-separated categories and search for any match
            categories = [cat.strip() for cat in category_search.split(',')]
            
            # Build OR condition for multiple categories
            if len(categories) == 1:
                # Single category - simple ILIKE search
                count_query = count_query.where(
                    text("EXISTS (SELECT 1 FROM jsonb_array_elements_text(topics) AS topic WHERE topic ILIKE :category_search)")
                ).params(category_search=f"%{categories[0]}%")
            else:
                # Multiple categories - search for any match using OR
                or_conditions = " OR ".join([f"topic ILIKE :cat{i}" for i in range(len(categories))])
                count_query = count_query.where(
                    text(f"EXISTS (SELECT 1 FROM jsonb_array_elements_text(topics) AS topic WHERE {or_conditions})")
                ).params(**{f"cat{i}": f"%{cat}%" for i, cat in enumerate(categories)})
        
        count_result = await db.execute(count_query)
        total = count_result.scalar() or 0
        
        # Calculate pagination
        total_pages = (total + limit - 1) // limit if total > 0 and limit > 0 else 1
        offset = (page - 1) * limit
        
        # Order by updated_at descending (most recent first), then created_at
        query = query.order_by(
            AgnoMemory.updated_at.desc().nulls_last(),
            AgnoMemory.created_at.desc().nulls_last()
        )
        
        # Apply pagination
        query = query.limit(limit).offset(offset)
        
        # Execute query
        result = await db.execute(query)
        memories = result.scalars().all()
        
        # Transform to response models
        memory_responses = []
        for memory in memories:
            # Convert Unix timestamps to datetime objects
            updated_at = None
            if memory.updated_at:
                updated_at = datetime.fromtimestamp(memory.updated_at, tz=timezone.utc)
            
            created_at = None
            if memory.created_at:
                created_at = datetime.fromtimestamp(memory.created_at, tz=timezone.utc)
            
            memory_responses.append(
                ContextMemoryResponse(
                    user_id=memory.user_id,
                    memory_id=memory.memory_id,
                    memory=_format_memory_for_response(memory.memory, current_user),
                    topics=memory.topics,
                    updated_at=updated_at,
                    created_at=created_at
                )
            )
        
        logger.info(
            f"Retrieved {len(memory_responses)} context memories for user {current_user.email} "
            f"(page {page}/{total_pages}, total: {total})"
            + (f" with search '{search}'" if search else "")
            + (f" filtered by topic '{topic}'" if topic else "")
        )
        
        # Collect ALL unique topics/categories from ALL user's memories (regardless of filters)
        # This ensures the category dropdown shows all available categories
        all_categories_query = select(AgnoMemory.topics).where(
            AgnoMemory.user_id == current_user.email
        ).where(
            AgnoMemory.topics.isnot(None)
        )
        all_categories_result = await db.execute(all_categories_query)
        all_topics_rows = all_categories_result.scalars().all()
        
        all_categories = set()
        for topics in all_topics_rows:
            if topics:  # topics is a list
                all_categories.update(topics)
        
        # Convert to sorted list
        category_list = sorted(list(all_categories))
        
        return ContextMemoryListResponse(
            success=True,
            total=total,
            page=page,
            limit=limit,
            total_pages=total_pages,
            category=category_list,
            memories=memory_responses,
            filtered_by_topic=topic,
            search_query=search
        )
        
    except Exception as e:
        logger.error(f"Error retrieving context memories for user {current_user.email}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve context memories: {str(e)}"
        )


@router.delete(
    "/context-memory/{memory_id}",
    response_model=DeleteMemoryResponse,
    summary="Delete a context memory",
    description="Delete a specific context memory by ID (only if it belongs to the current user)"
)
async def delete_context_memory(
    memory_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> DeleteMemoryResponse:
    """
    Delete a context memory.
    
    Args:
        memory_id: UUID string of the memory to delete
        current_user: Current authenticated user
        db: Database session
        
    Returns:
        Success message with deleted memory ID
        
    Raises:
        HTTPException: If memory not found or doesn't belong to user
    """
    try:
        # First, verify the memory exists and belongs to the current user
        query = select(AgnoMemory).where(
            AgnoMemory.memory_id == memory_id,
            AgnoMemory.user_id == current_user.email
        )
        result = await db.execute(query)
        memory = result.scalar_one_or_none()
        
        if not memory:
            logger.warning(
                f"Memory {memory_id} not found or doesn't belong to user {current_user.email}"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Memory with ID {memory_id} not found or you don't have permission to delete it"
            )
        
        # Delete the memory from agno_memories table
        delete_stmt = delete(AgnoMemory).where(
            AgnoMemory.memory_id == memory_id,
            AgnoMemory.user_id == current_user.email
        )
        await db.execute(delete_stmt)
        
        # Update message_metadata to mark memory as "discarded" instead of removing
        updated_messages = await _mark_memories_discarded_in_messages(
            db=db,
            user=current_user,
            memory_ids={memory_id},
        )
        
        await db.commit()
        
        logger.info(
            f"Deleted context memory {memory_id} for user {current_user.email}. "
            f"Updated {updated_messages} messages to status='discarded'."
        )
        
        return DeleteMemoryResponse(
            success=True,
            message="Context memory deleted successfully",
            memory_id=memory_id
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error deleting context memory {memory_id}: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete context memory: {str(e)}"
        )


@router.delete(
    "/context-memory",
    response_model=BulkDeleteMemoryResponse,
    summary="Delete multiple context memories",
    description="Delete multiple context memories in a single request (only those belonging to the current user)",
)
async def bulk_delete_context_memories(
    request_body: BulkDeleteMemoryRequest = Body(..., description="Memory IDs to delete"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BulkDeleteMemoryResponse:
    """Delete multiple context memories for the current user."""
    try:
        requested_ids = [str(memory_id) for memory_id in request_body.memory_ids]

        # Remove duplicates while preserving order
        unique_requested_ids = list(dict.fromkeys(requested_ids))

        # Find memories that exist and belong to current user
        existing_query = select(AgnoMemory.memory_id).where(
            AgnoMemory.memory_id.in_(unique_requested_ids),
            AgnoMemory.user_id == current_user.email,
        )
        existing_result = await db.execute(existing_query)
        existing_ids = [row[0] for row in existing_result.all()]
        existing_id_set = set(existing_ids)

        if not existing_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No matching memories found or you don't have permission to delete them",
            )

        # Delete all found memories in one statement
        delete_stmt = delete(AgnoMemory).where(
            AgnoMemory.memory_id.in_(existing_ids),
            AgnoMemory.user_id == current_user.email,
        )
        await db.execute(delete_stmt)

        # Mark deleted memories as discarded in related message metadata
        updated_messages = await _mark_memories_discarded_in_messages(
            db=db,
            user=current_user,
            memory_ids=existing_id_set,
        )

        await db.commit()

        not_found_ids = [memory_id for memory_id in unique_requested_ids if memory_id not in existing_id_set]

        logger.info(
            f"Bulk deleted {len(existing_ids)} context memories for user {current_user.email}. "
            f"Requested={len(unique_requested_ids)}, not_found={len(not_found_ids)}, "
            f"updated_messages={updated_messages}."
        )

        return BulkDeleteMemoryResponse(
            success=True,
            message="Context memories deleted successfully",
            deleted_count=len(existing_ids),
            deleted_memory_ids=[UUID(memory_id) for memory_id in existing_ids],
            not_found_memory_ids=[UUID(memory_id) for memory_id in not_found_ids],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error bulk deleting context memories for user {current_user.email}: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete context memories: {str(e)}",
        )
