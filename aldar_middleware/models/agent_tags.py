"""Agent tags database models."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, String, Text, ForeignKey, Integer, Boolean, JSON, BigInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class AgentTag(Base):
    """Agent tag model for categorizing agents."""

    __tablename__ = "agent_tags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(BigInteger, ForeignKey("agents.id"), nullable=False, index=True)
    tag = Column(String(100), nullable=False, index=True)
    tag_type = Column(String(50), nullable=True)  # category, skill, domain, etc.
    description = Column(Text, nullable=True)  # Optional description of the tag
    color = Column(String(20), nullable=True)  # Optional color for UI display
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    agent = relationship("Agent", back_populates="tags")

    def __repr__(self) -> str:
        return f"<AgentTag(id={self.id}, agent_id={self.agent_id}, tag={self.tag})>"
