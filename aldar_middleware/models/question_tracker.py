"""User question tracker model for monthly question counting."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, Integer, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base

if TYPE_CHECKING:
    from aldar_middleware.models.user import User


class UserQuestionTracker(Base):
    """Track user questions on a monthly basis with thresholds."""

    __tablename__ = "user_question_tracker"

    # Constants for thresholds
    DEFAULT_MINIMUM_THRESHOLD = 10
    DEFAULT_MAXIMUM_THRESHOLD = 100

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Month tracking
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)  # 1-12
    
    # Question count
    question_count = Column(Integer, default=0, nullable=False)
    
    # Thresholds (constant values)
    minimum_threshold = Column(Integer, default=DEFAULT_MINIMUM_THRESHOLD, nullable=False)
    maximum_threshold = Column(Integer, default=DEFAULT_MAXIMUM_THRESHOLD, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationship
    user = relationship("User", back_populates="question_trackers")

    # Table constraints and indexes
    __table_args__ = (
        # Unique constraint: one record per user per month (automatically creates index)
        UniqueConstraint("user_id", "year", "month", name="uq_user_question_tracker_user_month"),
        # Index for reporting by time period across all users
        Index("idx_user_question_tracker_year_month", "year", "month"),
        # Note: user_id and (user_id, year, month) indexes are provided by the unique constraint
    )

    def increment_count(self) -> int:
        """
        Increment the question count by 1.
        
        Returns:
            int: The new question count
        """
        self.question_count += 1
        self.updated_at = datetime.utcnow()
        return self.question_count

    def is_at_minimum(self) -> bool:
        """
        Check if the question count has reached the minimum threshold.
        
        Returns:
            bool: True if count >= minimum_threshold
        """
        return self.question_count >= self.minimum_threshold

    def is_at_maximum(self) -> bool:
        """
        Check if the question count has reached the maximum threshold.
        
        Returns:
            bool: True if count >= maximum_threshold
        """
        return self.question_count >= self.maximum_threshold

    def percentage_used(self) -> float:
        """
        Calculate the percentage of maximum threshold used.
        
        Returns:
            float: Percentage (0-100+) of maximum threshold used
        """
        if self.maximum_threshold == 0:
            return 0.0
        return (self.question_count / self.maximum_threshold) * 100.0

    def __repr__(self) -> str:
        return (
            f"<UserQuestionTracker(id={self.id}, user_id={self.user_id}, "
            f"year={self.year}, month={self.month}, count={self.question_count})>"
        )
