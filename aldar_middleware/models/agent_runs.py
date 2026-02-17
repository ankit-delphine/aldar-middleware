"""Agent run-related database models."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, String, Text, ForeignKey, Integer, Boolean, JSON, BigInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class AgentRun(Base):
    """Agent run model for execution tracking."""

    __tablename__ = "agent_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    public_id = Column(UUID(as_uuid=True), unique=True, default=uuid.uuid4, index=True)
    run_id = Column(String(255), unique=True, nullable=False, index=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False, index=True)
    agent_id = Column(BigInteger, ForeignKey("agents.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    parent_run_id = Column(UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=True)
    agent_name = Column(String(255), nullable=True)
    workflow_id = Column(String(255), nullable=True, index=True)
    workflow_step_id = Column(String(255), nullable=True)
    content = Column(Text, nullable=True)  # Generated content
    content_type = Column(String(50), nullable=False, default="text")
    reasoning_content = Column(Text, nullable=True)  # Agent's reasoning process
    status = Column(String(50), nullable=False, default="running")  # running, completed, failed
    error_message = Column(Text, nullable=True)
    execution_time_ms = Column(Integer, nullable=True)  # Execution time in milliseconds
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    session = relationship("Session", back_populates="agent_runs")
    agent = relationship("Agent", back_populates="agent_runs")
    user = relationship("User", back_populates="agent_runs")
    parent_run = relationship("AgentRun", remote_side=[id])
    token_usage = relationship("TokenUsage", back_populates="agent_run", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<AgentRun(id={self.id}, public_id={self.public_id}, run_id={self.run_id}, status={self.status})>"
