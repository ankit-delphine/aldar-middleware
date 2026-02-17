"""Token usage and agent metrics database models."""

import uuid
from datetime import datetime
from typing import Optional
from decimal import Decimal

from sqlalchemy import Column, DateTime, String, Text, ForeignKey, Integer, Boolean, JSON, Numeric, BigInteger, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class TokenUsage(Base):
    """Agent usage metrics model (formerly token_usage)."""

    __tablename__ = "agent_usage_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    public_id = Column(UUID(as_uuid=True), unique=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=True, index=True)
    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id"), nullable=True, index=True)
    agent_id = Column(BigInteger, ForeignKey("agents.id"), nullable=False, index=True)
    agent_run_id = Column(UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=True, index=True)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    cost = Column(Numeric(10, 6), nullable=True)  # Cost in currency units
    currency = Column(String(10), nullable=False, default="USD")
    model_name = Column(String(100), nullable=False, index=True)
    # Additional aggregate metrics
    total_request = Column(Integer, nullable=False, default=0)
    total_error = Column(Integer, nullable=False, default=0)
    average_response_time = Column(Float, nullable=True)
    success_time = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    user = relationship("User", back_populates="token_usage")
    session = relationship("Session", back_populates="token_usage")
    message = relationship("Message", back_populates="token_usage")
    agent = relationship("Agent", back_populates="token_usage")
    agent_run = relationship("AgentRun", back_populates="token_usage")

    def __repr__(self) -> str:
        return f"<TokenUsage(id={self.id}, public_id={self.public_id}, total_tokens={self.total_tokens}, model={self.model_name})>"


# Removed AgentUsageMetrics per updated requirements. TokenUsage remains for cost/LLM tracking.
