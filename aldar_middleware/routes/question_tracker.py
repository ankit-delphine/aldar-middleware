"""Question tracker API routes."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from aldar_middleware.database.base import get_db
from aldar_middleware.models.user import User
from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.services.question_tracker_service import (
    get_or_create_user_tracker,
    get_user_tracker_history,
)
from aldar_middleware.schemas.question_tracker import (
    QuestionTrackerResponse,
    QuestionTrackerData,
    QuestionTrackerHistoryResponse,
    QuestionTrackerHistoryItem,
)


router = APIRouter()


@router.get("/question-tracker", response_model=QuestionTrackerResponse)
async def get_question_tracker(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> QuestionTrackerResponse:
    """
    Get the current month's question tracker for the authenticated user.
    
    Returns:
        QuestionTrackerResponse: Current month's question count and thresholds
    """
    # Get or create the current month's tracker
    tracker = await get_or_create_user_tracker(current_user.id, db)
    await db.commit()  # Commit if a new tracker was created
    
    # Build response
    tracker_data = QuestionTrackerData(
        count=tracker.question_count,
        minimum_threshold=tracker.minimum_threshold,
        maximum_threshold=tracker.maximum_threshold,
        year=tracker.year,
        month=tracker.month,
        is_at_minimum=tracker.is_at_minimum(),
        is_at_maximum=tracker.is_at_maximum(),
        percentage_used=tracker.percentage_used(),
    )
    
    return QuestionTrackerResponse(question_tracker=tracker_data)


@router.get("/question-tracker/history", response_model=QuestionTrackerHistoryResponse)
async def get_question_tracker_history_endpoint(
    limit: int = Query(
        default=12,
        ge=1,
        le=100,
        description="Maximum number of months to retrieve (default: 12)"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> QuestionTrackerHistoryResponse:
    """
    Get historical question tracker data for the authenticated user.
    
    Args:
        limit: Maximum number of months to retrieve (1-100, default: 12)
    
    Returns:
        QuestionTrackerHistoryResponse: List of historical question tracker records
    """
    # Get historical data
    history = await get_user_tracker_history(current_user.id, db, limit=limit)
    
    # Build response
    history_items = [
        QuestionTrackerHistoryItem(
            year=tracker.year,
            month=tracker.month,
            count=tracker.question_count,
            minimum_threshold=tracker.minimum_threshold,
            maximum_threshold=tracker.maximum_threshold,
        )
        for tracker in history
    ]
    
    return QuestionTrackerHistoryResponse(history=history_items)
