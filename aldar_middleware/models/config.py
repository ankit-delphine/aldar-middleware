"""Config model for global and user-specific configuration prompts."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class Config(Base):
    """
    Config model for storing versioned global and user-specific configuration prompts.
    
    Matches the 2.0 schema for migration compatibility.
    """

    __tablename__ = "configs"

    id = Column(String(255), primary_key=True, nullable=False)
    version = Column(Integer, nullable=False, index=True)
    system_wide_prompt = Column(Text, nullable=False)
    system_agent_prompt = Column(Text, nullable=False)
    user_custom_query_template = Column(Text, nullable=False)
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

    def __repr__(self) -> str:
        return f"<Config(id={self.id}, version={self.version})>"

