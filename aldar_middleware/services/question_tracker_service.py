"""Question tracker service for managing user question counts."""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from aldar_middleware.models.question_tracker import UserQuestionTracker


async def increment_question_count(
    user_id: UUID,
    db: AsyncSession,
) -> UserQuestionTracker:
    """
    Increment the question count for the current user and month.
    
    This function handles:
    - Auto-reset when entering a new month (creates new record)
    - Incrementing the count for existing records using atomic operations
    - Logging warnings when thresholds are reached (soft limits)
    
    Note: Caller is responsible for committing the transaction.
    
    Args:
        user_id: The user's UUID
        db: Database session
    
    Returns:
        UserQuestionTracker: The updated or created tracker record
    """
    now = datetime.utcnow()
    current_year = now.year
    current_month = now.month
    
    # Use SELECT FOR UPDATE to lock the row and prevent race conditions
    result = await db.execute(
        select(UserQuestionTracker)
        .where(
            UserQuestionTracker.user_id == user_id,
            UserQuestionTracker.year == current_year,
            UserQuestionTracker.month == current_month,
        )
        .with_for_update()
    )
    tracker = result.scalar_one_or_none()
    
    # If no record exists for current month, create one (auto-reset)
    if not tracker:
        tracker = UserQuestionTracker(
            user_id=user_id,
            year=current_year,
            month=current_month,
            question_count=1,  # Start at 1 since this is the first question
            minimum_threshold=UserQuestionTracker.DEFAULT_MINIMUM_THRESHOLD,
            maximum_threshold=UserQuestionTracker.DEFAULT_MAXIMUM_THRESHOLD,
        )
        db.add(tracker)
        await db.flush()  # Get the ID but don't commit yet
        logger.info(
            f"Created new question tracker for user {user_id} - "
            f"{current_year}/{current_month:02d}"
        )
        new_count = 1
    else:
        # Increment the count atomically
        tracker.question_count += 1
        tracker.updated_at = datetime.utcnow()
        new_count = tracker.question_count
    
    # Log milestone/warning messages (soft limits - no blocking)
    if new_count == tracker.minimum_threshold:
        logger.info(
            f"User {user_id} reached minimum threshold: {new_count}/{tracker.maximum_threshold} "
            f"questions in {current_year}/{current_month:02d}"
        )
    elif new_count == tracker.maximum_threshold:
        logger.warning(
            f"User {user_id} reached maximum threshold: {new_count}/{tracker.maximum_threshold} "
            f"questions in {current_year}/{current_month:02d} (soft limit - allowing continuation)"
        )
    elif new_count > tracker.maximum_threshold and new_count % 10 == 0:
        # Log every 10 questions after exceeding max threshold
        logger.warning(
            f"User {user_id} exceeded maximum threshold: {new_count}/{tracker.maximum_threshold} "
            f"questions in {current_year}/{current_month:02d} (soft limit - allowing continuation)"
        )
    
    return tracker


async def get_user_tracker(
    user_id: UUID,
    db: AsyncSession,
) -> Optional[UserQuestionTracker]:
    """
    Get the current month's question tracker for a user.
    
    Args:
        user_id: The user's UUID
        db: Database session
    
    Returns:
        Optional[UserQuestionTracker]: The current month's tracker or None if not found
    """
    now = datetime.utcnow()
    current_year = now.year
    current_month = now.month
    
    result = await db.execute(
        select(UserQuestionTracker).where(
            UserQuestionTracker.user_id == user_id,
            UserQuestionTracker.year == current_year,
            UserQuestionTracker.month == current_month,
        )
    )
    return result.scalar_one_or_none()


async def get_or_create_user_tracker(
    user_id: UUID,
    db: AsyncSession,
) -> UserQuestionTracker:
    """
    Get or create the current month's question tracker for a user.
    
    Note: Caller is responsible for committing the transaction.
    
    Args:
        user_id: The user's UUID
        db: Database session
    
    Returns:
        UserQuestionTracker: The current month's tracker (created if not found)
    """
    tracker = await get_user_tracker(user_id, db)
    
    if not tracker:
        now = datetime.utcnow()
        tracker = UserQuestionTracker(
            user_id=user_id,
            year=now.year,
            month=now.month,
            question_count=0,
            minimum_threshold=UserQuestionTracker.DEFAULT_MINIMUM_THRESHOLD,
            maximum_threshold=UserQuestionTracker.DEFAULT_MAXIMUM_THRESHOLD,
        )
        db.add(tracker)
        await db.flush()  # Flush to get the ID, but don't commit yet
        logger.info(
            f"Created new question tracker for user {user_id} - "
            f"{now.year}/{now.month:02d}"
        )
    
    return tracker


async def get_user_tracker_history(
    user_id: UUID,
    db: AsyncSession,
    limit: int = 12,
) -> List[UserQuestionTracker]:
    """
    Get historical question tracker data for a user.
    
    Args:
        user_id: The user's UUID
        db: Database session
        limit: Maximum number of months to retrieve (default: 12)
    
    Returns:
        List[UserQuestionTracker]: List of tracker records, ordered by most recent first
    """
    result = await db.execute(
        select(UserQuestionTracker)
        .where(UserQuestionTracker.user_id == user_id)
        .order_by(desc(UserQuestionTracker.year), desc(UserQuestionTracker.month))
        .limit(limit)
    )
    return list(result.scalars().all())
