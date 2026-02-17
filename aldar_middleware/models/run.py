"""Run model matching ERD specification for external team compatibility."""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class Run(Base):
    """
    RUN entity matching ERD specification.

    Represents a single execution or run of an agent.
    Uses string run_id as primary key for external team compatibility.
    """

    __tablename__ = "runs"

    # Primary key as string per ERD specification
    run_id = Column(String(255), primary_key=True, nullable=False, index=True)

    # Foreign keys
    agent_id = Column(BigInteger, ForeignKey("agents.id"), nullable=False, index=True)
    # Note: session_id stores Session.public_id as string (UUID converted to string) for ERD compatibility
    session_id = Column(String(255), nullable=False, index=True)

    # Agent information
    agent_name = Column(String(255), nullable=True)

    # Content fields
    content = Column(Text, nullable=True)
    content_type = Column(String(50), nullable=True)

    # Model information
    model = Column(String(100), nullable=True)
    model_provider = Column(String(100), nullable=True)

    # Status
    status = Column(String(50), nullable=False, default="running")  # running, completed, failed

    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    agent = relationship("Agent", back_populates="runs")
    # Note: session relationship uses public_id converted to string
    session = relationship(
        "Session",
        foreign_keys=[session_id],
        primaryjoin="Session.public_id.cast(String) == Run.session_id",
        back_populates="runs",
    )
    events = relationship("Event", back_populates="run", cascade="all, delete-orphan")
    messages = relationship(
        "RunMessage", back_populates="run", cascade="all, delete-orphan"
    )
    metrics = relationship(
        "RunMetrics", back_populates="run", uselist=False, cascade="all, delete-orphan"
    )
    input_data = relationship(
        "RunInput", back_populates="run", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Run(run_id={self.run_id}, agent_id={self.agent_id}, status={self.status})>"

