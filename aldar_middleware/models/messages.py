"""Message-related database models."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, String, Text, ForeignKey, Integer, Boolean, JSON, BigInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class Message(Base):
    """Message model for session communications."""

    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    public_id = Column(UUID(as_uuid=True), unique=True, default=uuid.uuid4, index=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    agent_id = Column(BigInteger, ForeignKey("agents.id"), nullable=False, index=True)
    parent_message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id"), nullable=True)
    role = Column(String(50), nullable=False)  # user, assistant, system, tool
    content = Column(Text, nullable=True)  # Text content
    content_type = Column(String(50), nullable=False, default="text")  # text, image, video, audio, file
    images = Column(JSON, nullable=True)  # Image attachments metadata
    videos = Column(JSON, nullable=True)  # Video attachments metadata
    audio = Column(JSON, nullable=True)  # Audio attachments metadata
    files = Column(JSON, nullable=True)  # File attachments metadata
    tool_calls = Column(JSON, nullable=True)  # Tool calls made by the agent
    # 2.0 migration fields
    document_my_agent_id = Column(BigInteger, ForeignKey("agents.id"), nullable=True, index=True)  # Document my agent
    document_knowledge_agent_id = Column(BigInteger, ForeignKey("agents.id"), nullable=True, index=True)  # Document knowledge agent
    is_reply = Column(Boolean, default=False, nullable=False)  # Is this a reply message
    is_refreshed = Column(Boolean, default=False, nullable=False)  # Is this message refreshed
    result_code = Column(String(50), nullable=True)  # Result code
    result_note = Column(Text, nullable=True)  # Result note/description
    sent_at = Column(DateTime, nullable=True, index=True)  # When message was sent
    deleted_at = Column(DateTime, nullable=True, index=True)  # Soft delete timestamp
    is_sent_directly_to_openai = Column(Boolean, default=False, nullable=False)  # Sent directly to OpenAI
    message_type = Column(String(50), nullable=True)  # Message type (different from content_type)
    is_internet_search_used = Column(Boolean, default=False, nullable=False)  # Internet search was used
    has_found_information = Column(Boolean, default=False, nullable=False)  # Information was found
    selected_agent_type = Column(String(50), nullable=True)  # Selected agent type
    message_metadata = Column(JSON, nullable=True)  # Additional metadata for custom query fields
    # Custom query fields stored in message_metadata JSON:
    # - customQueryAboutUser: Optional[str]
    # - customQueryPreferredFormatting: Optional[str]
    # - customQueryTopicsOfInterest: Optional[List[str]]
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    session = relationship("Session", back_populates="messages")
    user = relationship("User", back_populates="messages")
    agent = relationship(
        "Agent",
        foreign_keys=[agent_id],  # Specify which FK to use (not document_knowledge_agent_id or document_my_agent_id)
        back_populates="messages",
    )
    parent_message = relationship("Message", remote_side=[id])
    token_usage = relationship("TokenUsage", back_populates="message", cascade="all, delete-orphan")
    attachments = relationship("Attachment", foreign_keys="Attachment.message_id", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Message(id={self.id}, public_id={self.public_id}, role={self.role}, session_id={self.session_id})>"
