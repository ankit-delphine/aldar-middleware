"""User agent access control database models."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, String, Text, ForeignKey, Integer, Boolean, JSON, BigInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class UserAgentAccess(Base):
    """User agent access control model."""

    __tablename__ = "user_agent_access"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    agent_id = Column(BigInteger, ForeignKey("agents.id"), nullable=False, index=True)
    access_level = Column(String(50), nullable=False, default="read")  # read, write, admin
    granted_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    granted_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    expires_at = Column(DateTime, nullable=True)  # Optional expiration
    access_metadata = Column(JSON, nullable=True)  # Additional access metadata (renamed from metadata to avoid SQLAlchemy conflict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", foreign_keys=[user_id], back_populates="agent_access")
    agent = relationship("Agent", back_populates="user_access")
    granted_by_user = relationship("User", foreign_keys=[granted_by])

    def __repr__(self) -> str:
        return f"<UserAgentAccess(id={self.id}, user_id={self.user_id}, agent_id={self.agent_id}, access_level={self.access_level})>"
