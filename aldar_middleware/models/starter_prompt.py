"""StarterPrompt model for agent-specific starter prompts."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class StarterPrompt(Base):
    """
    StarterPrompt model for storing user-created prompts linked to agents.
    
    Matches the 2.0 schema for migration compatibility.
    """

    __tablename__ = "starter_prompts"

    id = Column(String(255), primary_key=True, nullable=False)
    title = Column(String(255), nullable=False)
    prompt = Column(Text, nullable=False)
    is_highlighted = Column(Boolean, nullable=False, default=False)
    is_hide = Column(Boolean, nullable=False, default=False)
    order = Column(Integer, nullable=False, default=0)
    knowledge_agent_id = Column(String(255), nullable=True)  # Legacy field
    my_agent_id = Column(BigInteger, ForeignKey("agents.id"), nullable=True, index=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    agent = relationship("Agent", back_populates="starter_prompts")

    def __repr__(self) -> str:
        return f"<StarterPrompt(id={self.id}, title={self.title}, agent_id={self.my_agent_id})>"

