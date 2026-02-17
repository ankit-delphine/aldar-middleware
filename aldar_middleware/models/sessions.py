"""Session-related database models."""

import uuid

from sqlalchemy import Boolean, BigInteger, Column, DateTime, ForeignKey, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base
from aldar_middleware.utils.timezone import utcnow_naive


class Session(Base):
    """Session model for user-agent interactions."""

    __tablename__ = "sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    public_id = Column(UUID(as_uuid=True), unique=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    agent_id = Column(BigInteger, ForeignKey("agents.id"), nullable=False, index=True)
    session_name = Column(String(255), nullable=True)
    session_state = Column(JSON, nullable=True)  # Current session state
    session_data = Column(JSON, nullable=True)  # Additional session data
    summary = Column(JSON, nullable=True)  # Session summary
    status = Column(String(50), nullable=False, default="active")  # active, completed, archived
    workflow_id = Column(String(255), nullable=True, index=True)
    session_metadata = Column(JSON, nullable=True)  # Additional metadata (renamed from metadata to avoid SQLAlchemy conflict)
    session_type = Column(String(50), nullable=False, default="chat")  # chat, workflow, etc.
    # 2.0 migration fields
    graph_id = Column(String(255), nullable=True, index=True)  # Graph ID from 2.0
    deleted_at = Column(DateTime, nullable=True, index=True)  # Soft delete timestamp
    is_favorite = Column(Boolean, default=False, nullable=False)  # Favorite status (migrated from metadata)
    last_message_interaction_at = Column(DateTime, nullable=True, index=True)  # Last message interaction time
    meeting_id = Column(String(255), nullable=True, index=True)  # Meeting ID
    document_knowledge_agent_id = Column(BigInteger, ForeignKey("agents.id"), nullable=True, index=True)  # Document knowledge agent
    document_my_agent_id = Column(BigInteger, ForeignKey("agents.id"), nullable=True, index=True)  # Document my agent
    started_at = Column(DateTime, default=utcnow_naive, nullable=True, index=True)
    created_at = Column(DateTime, default=utcnow_naive, nullable=False, index=True)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)

    # Relationships
    user = relationship("User", back_populates="sessions")
    agent = relationship(
        "Agent",
        foreign_keys=[agent_id],  # Specify which FK to use (not document_knowledge_agent_id or document_my_agent_id)
        back_populates="sessions",
    )
    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan")
    agent_runs = relationship("AgentRun", back_populates="session", cascade="all, delete-orphan")
    token_usage = relationship("TokenUsage", back_populates="session", cascade="all, delete-orphan")

    # ERD compatibility relationships
    # Note: session_id in runs/memories stores public_id as string
    runs = relationship(
        "Run",
        foreign_keys="Run.session_id",
        primaryjoin="Session.public_id.cast(String) == Run.session_id",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    memories = relationship(
        "Memory",
        foreign_keys="Memory.session_id",
        primaryjoin="Session.public_id.cast(String) == Memory.session_id",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    @hybrid_property
    def session_id(self) -> str:
        """Return id as string for backward compatibility."""
        return str(self.id) if self.id else ""

    @session_id.setter
    def session_id(self, value: str) -> None:
        """Set id from string value."""
        if value:
            try:
                self.id = uuid.UUID(value)
            except (ValueError, TypeError):
                # If it's not a valid UUID, we can't set it
                pass

    def __repr__(self) -> str:
        return (
            f"<Session(id={self.id}, public_id={self.public_id}, "
            f"user_id={self.user_id}, agent_id={self.agent_id})>"
        )
