"""User settings database model."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID

from aldar_middleware.database.base import Base


class UserSettings(Base):
    """User settings model for storing user-specific preferences."""

    __tablename__ = "user_settings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(255), unique=True, nullable=False, index=True)  # Email of the user
    add_memories_to_context = Column(Boolean, nullable=False, default=True)  # Whether to add memories to context
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
