"""Agent configuration database models."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, String, Text, ForeignKey, Integer, Boolean, JSON, BigInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class AgentConfiguration(Base):
    """Agent configuration model for storing custom fields and settings."""

    __tablename__ = "agent_configuration"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(BigInteger, ForeignKey("agents.id"), nullable=False, index=True)
    configuration_name = Column(String(100), nullable=False, index=True)
    type = Column(String(50), nullable=False)  # boolean, string, number, array, object
    values = Column(JSON, nullable=True)  # values of the configuration
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    agent = relationship("Agent", back_populates="configurations")

    def __repr__(self) -> str:
        return f"<AgentConfiguration(id={self.id}, agent_id={self.agent_id}, name={self.configuration_name})>"
