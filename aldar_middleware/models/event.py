"""Event model matching ERD specification for external team compatibility."""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class Event(Base):
    """
    EVENT entity matching ERD specification.

    Records specific events that occur during a run.
    Uses string event_id as primary key for external team compatibility.
    """

    __tablename__ = "events"

    # Primary key as string per ERD specification
    event_id = Column(String(255), primary_key=True, nullable=False, index=True)

    # Foreign key
    run_id = Column(String(255), ForeignKey("runs.run_id"), nullable=False, index=True)

    # Event information
    event_type = Column(String(50), nullable=True)

    # Agent information
    agent_id = Column(BigInteger, nullable=True)
    agent_name = Column(String(255), nullable=True)

    # Session information
    session_id = Column(String(255), nullable=True)

    # Model information
    model = Column(String(100), nullable=True)
    model_provider = Column(String(100), nullable=True)

    # Content
    content = Column(Text, nullable=True)
    content_type = Column(String(50), nullable=True)

    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    run = relationship("Run", back_populates="events")

    def __repr__(self) -> str:
        return (
            f"<Event(event_id={self.event_id}, run_id={self.run_id}, "
            f"event_type={self.event_type})>"
        )

