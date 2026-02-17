"""Feedback system database models."""

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    String,
    Text,
    ForeignKey,
    BigInteger,
    JSON,
    Index,
    Boolean,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class FeedbackEntityType(str, Enum):
    """Entity types that can receive feedback."""

    SESSION = "session"
    CHAT = "chat"
    RESPONSE = "response"
    AGENT = "agent"
    APPLICATION = "application"
    FINAL_RESPONSE = "final_response"
    MESSAGE = "message"  # For individual message like/dislike feedback


class FeedbackRating(str, Enum):
    """Feedback rating options."""

    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"
    NEUTRAL = "neutral"


class FeedbackData(Base):
    """Main feedback data model."""

    __tablename__ = "feedback_data"

    feedback_id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
    )
    user_id = Column(String(255), nullable=False, index=True)
    user_email = Column(String(255), nullable=True)
    entity_id = Column(String(255), nullable=False, index=True)
    entity_type = Column(
        PG_ENUM(FeedbackEntityType, name="feedback_entity_type", values_callable=lambda x: [e.value for e in x], create_type=False),
        nullable=False,
        index=True,
    )
    agent_id = Column(String(255), nullable=True, index=True)
    session_id = Column(String(255), nullable=True, index=True)
    rating = Column(
        PG_ENUM(FeedbackRating, name="feedback_rating", values_callable=lambda x: [e.value for e in x], create_type=False),
        nullable=False,
    )
    comment = Column(Text, nullable=True)
    metadata_json = Column(JSON, default={}, nullable=False)
    correlation_id = Column(String(255), nullable=True, index=True)
    created_at = Column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    deleted_at = Column(DateTime, nullable=True, index=True)

    # Relationships
    files = relationship(
        "FeedbackFile",
        back_populates="feedback",
        cascade="all, delete-orphan",
        lazy="select",
    )

    # Composite indexes for common queries
    __table_args__ = (
        Index("ix_feedback_user_entity", "user_id", "entity_id"),
        Index("ix_feedback_type_date", "entity_type", "created_at"),
        Index("ix_feedback_user_date", "user_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<FeedbackData(id={self.feedback_id}, "
            f"entity_type={self.entity_type}, rating={self.rating})>"
        )


class FeedbackFile(Base):
    """File attachment for feedback."""

    __tablename__ = "feedback_files"

    file_id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
    )
    feedback_id = Column(
        UUID(as_uuid=True),
        ForeignKey("feedback_data.feedback_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_name = Column(String(500), nullable=False)
    file_url = Column(Text, nullable=False)
    file_size = Column(BigInteger, nullable=True)
    content_type = Column(String(100), nullable=True)
    blob_name = Column(String(500), nullable=True, unique=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    feedback = relationship("FeedbackData", back_populates="files")

    def __repr__(self) -> str:
        return f"<FeedbackFile(id={self.file_id}, name={self.file_name})>"