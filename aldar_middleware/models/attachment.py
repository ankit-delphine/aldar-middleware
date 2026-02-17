"""Attachment database models for file uploads."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, String, Text, ForeignKey, BigInteger, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class Attachment(Base):
    """Attachment model for storing uploaded files."""

    __tablename__ = "attachments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    file_name = Column(String(255), nullable=False)
    file_size = Column(BigInteger(), nullable=False)
    content_type = Column(String(100), nullable=True)
    blob_url = Column(Text(), nullable=False)  # Full blob URL with SAS token
    blob_name = Column(String(500), nullable=False)  # Azure blob path
    entity_type = Column(String(50), nullable=True)  # 'agent', 'chat', 'feedback', etc.
    entity_id = Column(String(255), nullable=True, index=True)  # Reference to entity (agent_id, chat_id, etc.)
    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id"), nullable=True, index=True)  # Reference to message (2.0 migration)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    message = relationship("Message", foreign_keys=[message_id], overlaps="attachments")

    def __repr__(self) -> str:
        return f"<Attachment(id={self.id}, file_name={self.file_name}, user_id={self.user_id})>"

