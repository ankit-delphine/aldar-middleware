"""Agent tools database models."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, String, Text, ForeignKey, Integer, Boolean, JSON, BigInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class AgentTool(Base):
    """Agent tool model for managing agent tools."""

    __tablename__ = "agent_tools"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(BigInteger, ForeignKey("agents.id"), nullable=False, index=True)
    tool_name = Column(String(100), nullable=False, index=True)
    tool_description = Column(Text, nullable=True)
    tool_url = Column(String(500), nullable=True)
    tool_icon = Column(String(500), nullable=True)
    tool_color = Column(String(20), nullable=True)
    tool_order = Column(Integer, default=0)
    tool_is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    agent = relationship("Agent", back_populates="tools")

    def __repr__(self) -> str:
        return f"<AgentTool(id={self.id}, agent_id={self.agent_id}, tool_name={self.tool_name})>"
