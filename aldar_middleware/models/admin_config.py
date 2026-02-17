"""Admin Configuration model for storing key-value configuration settings."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, String, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


def _utcnow():
    """Get current UTC datetime (timezone-naive for PostgreSQL TIMESTAMP WITHOUT TIME ZONE)."""
    return datetime.utcnow()


class AdminConfig(Base):
    """Admin configuration table for storing system-wide key-value settings."""

    __tablename__ = "admin_config"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key = Column(String(255), nullable=False, unique=True, index=True)
    value = Column(JSONB, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    # Relationships
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])

    def __repr__(self) -> str:
        return f"<AdminConfig(id={self.id}, key={self.key})>"
