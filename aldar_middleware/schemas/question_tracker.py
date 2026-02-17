"""Pydantic schemas for question tracker API."""

from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class QuestionTrackerData(BaseModel):
    """Question tracker data for a specific month."""
    
    count: int = Field(..., description="Current question count for the month", ge=0)
    minimum_threshold: int = Field(..., description="Minimum threshold (constant: 10)", ge=0)
    maximum_threshold: int = Field(..., description="Maximum threshold (constant: 100)", ge=0)
    year: int = Field(..., description="Year (e.g., 2025)")
    month: int = Field(..., description="Month (1-12)", ge=1, le=12)
    is_at_minimum: bool = Field(..., description="Whether count has reached minimum threshold")
    is_at_maximum: bool = Field(..., description="Whether count has reached maximum threshold")
    percentage_used: float = Field(..., description="Percentage of maximum threshold used", ge=0)

    model_config = ConfigDict(from_attributes=True)


class QuestionTrackerResponse(BaseModel):
    """Response model for current month question tracker."""
    
    question_tracker: QuestionTrackerData


class QuestionTrackerHistoryItem(BaseModel):
    """Historical question tracker item."""
    
    year: int = Field(..., description="Year (e.g., 2025)")
    month: int = Field(..., description="Month (1-12)", ge=1, le=12)
    count: int = Field(..., description="Question count for that month", ge=0)
    minimum_threshold: int = Field(..., description="Minimum threshold", ge=0)
    maximum_threshold: int = Field(..., description="Maximum threshold", ge=0)

    model_config = ConfigDict(from_attributes=True)


class QuestionTrackerHistoryResponse(BaseModel):
    """Response model for historical question tracker data."""
    
    history: List[QuestionTrackerHistoryItem] = Field(
        default_factory=list,
        description="List of historical question tracker records"
    )
