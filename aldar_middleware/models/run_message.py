"""RunMessage model matching ERD specification for external team compatibility."""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class RunMessage(Base):
    """
    MESSAGE entity matching ERD specification.

    Stores messages exchanged within a run.
    Uses string message_id as primary key for external team compatibility.
    """

    __tablename__ = "run_messages"

    # Primary key as string per ERD specification
    message_id = Column(String(255), primary_key=True, nullable=False, index=True)

    # Foreign key
    run_id = Column(String(255), ForeignKey("runs.run_id"), nullable=False, index=True)

    # Message content
    content = Column(Text, nullable=True)

    # Message flags
    from_history = Column(Boolean, default=False)
    stop_after_tool_call = Column(Boolean, default=False)

    # Role
    role = Column(String(50), nullable=True)  # user, assistant, system, tool

    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    run = relationship("Run", back_populates="messages")

    def __repr__(self) -> str:
        return (
            f"<RunMessage(message_id={self.message_id}, run_id={self.run_id}, "
            f"role={self.role})>"
        )

