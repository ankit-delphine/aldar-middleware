"""Feedback system schemas and request/response models."""

from datetime import datetime
from typing import Dict, List, Optional, TypeVar, Generic
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from aldar_middleware.models.feedback import FeedbackEntityType, FeedbackRating

T = TypeVar('T')


class FeedbackFileResponse(BaseModel):
    """Response model for a feedback file."""

    file_id: UUID
    file_name: str
    file_size: Optional[int] = None
    content_type: Optional[str] = None
    file_url: str
    uploaded_at: datetime

    class Config:
        """Pydantic config."""

        from_attributes = True


class FeedbackCreateRequest(BaseModel):
    """Request model for creating feedback."""

    entity_id: str = Field(..., min_length=1, max_length=255)
    entity_type: FeedbackEntityType
    rating: FeedbackRating
    comment: Optional[str] = Field(None, max_length=5000)
    metadata_json: Optional[Dict] = Field(default_factory=dict)
    session_id: Optional[str] = Field(None, max_length=255)

    @field_validator("entity_id")
    @classmethod
    def validate_entity_id(cls, v: str) -> str:
        """Validate entity_id is not empty."""
        if not v.strip():
            raise ValueError("entity_id cannot be empty")
        return v.strip()

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, v: Optional[str]) -> Optional[str]:
        """Validate comment length."""
        if v and len(v) > 5000:
            raise ValueError("comment cannot exceed 5000 characters")
        return v


class FeedbackUpdateRequest(BaseModel):
    """Request model for updating feedback."""

    comment: Optional[str] = Field(None, max_length=5000)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, v: Optional[str]) -> Optional[str]:
        """Validate comment length."""
        if v and len(v) > 5000:
            raise ValueError("comment cannot exceed 5000 characters")
        return v


class FeedbackResponse(BaseModel):
    """Response model for feedback."""

    feedback_id: UUID
    user_id: str
    user_email: Optional[str] = None
    user_full_name: Optional[str] = None
    user_profile_photo: Optional[str] = None
    user_department: Optional[str] = None
    user_job_title: Optional[str] = None
    user_company: Optional[str] = None
    user_external_id: Optional[str] = None
    user_azure_display_name: Optional[str] = None
    user_azure_upn: Optional[str] = None
    entity_id: str
    entity_type: FeedbackEntityType
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    agent_public_id: Optional[str] = None
    agent_thumbnail: Optional[str] = None
    rating: FeedbackRating
    comment: Optional[str] = None
    metadata_json: Dict = Field(default_factory=dict)
    correlation_id: Optional[str] = None
    files: List[FeedbackFileResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    session_id: Optional[str] = None

    class Config:
        """Pydantic config."""

        from_attributes = True


class FeedbackQueryParams(BaseModel):
    """Query parameters for feedback listing."""

    entity_type: Optional[FeedbackEntityType] = None
    entity_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    rating: Optional[FeedbackRating] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    page: int = Field(1, ge=1)
    limit: int = Field(20, ge=1, le=100)

    class Config:
        """Pydantic config."""

        from_attributes = True


class FeedbackAnalyticsByEntityType(BaseModel):
    """Analytics breakdown by entity type."""

    entity_type: FeedbackEntityType
    total_count: int
    positive_count: int
    negative_count: int
    neutral_count: int
    positive_ratio: float


class FeedbackAnalyticsByDate(BaseModel):
    """Analytics breakdown by date."""

    date: str
    total_count: int
    positive_count: int
    negative_count: int
    neutral_count: int
    positive_ratio: float


class FeedbackAnalyticsByAgent(BaseModel):
    """Analytics breakdown by agent."""

    agent_id: Optional[str]
    total_count: int
    positive_count: int
    negative_count: int
    neutral_count: int
    positive_ratio: float


class FeedbackAnalyticsSummary(BaseModel):
    """Analytics summary response."""

    total_count: int
    positive_count: int
    negative_count: int
    neutral_count: int
    positive_ratio: float
    average_sentiment_score: float
    by_entity_type: List[FeedbackAnalyticsByEntityType]
    by_date: List[FeedbackAnalyticsByDate]
    by_agent: List[FeedbackAnalyticsByAgent]
    date_range_from: datetime
    date_range_to: datetime


class FeedbackTrendsResponse(BaseModel):
    """Trends analytics response."""

    metric_name: str
    trend_direction: str  # "up", "down", "stable"
    current_value: float
    previous_value: float
    change_percent: float
    period: str


class FeedbackExportRow(BaseModel):
    """Single row for CSV export."""

    feedback_id: str
    user_id: str
    user_email: Optional[str]
    entity_id: str
    entity_type: str
    agent_id: Optional[str]
    rating: str
    comment: Optional[str]
    file_count: int
    correlation_id: Optional[str]
    created_at: str
    updated_at: str


class ErrorResponse(BaseModel):
    """Error response model."""

    detail: str
    correlation_id: Optional[str] = None
    error_code: Optional[str] = None


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated response wrapper."""

    items: List[T]
    total: int
    page: int
    limit: int
    total_pages: int