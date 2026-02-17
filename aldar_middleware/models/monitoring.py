"""Monitoring-related database models."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, String, Text, Boolean, JSON, Float, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class Metric(Base):
    """Metric model for monitoring."""

    __tablename__ = "metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, index=True)
    value = Column(Float, nullable=False)
    unit = Column(String(20), nullable=True)  # e.g., "count", "ms", "bytes"
    labels = Column(JSON, nullable=True)  # Metric labels/tags
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    source = Column(String(50), nullable=True)  # Source of the metric

    def __repr__(self) -> str:
        return f"<Metric(id={self.id}, name={self.name}, value={self.value})>"


class Alert(Base):
    """Alert model for monitoring."""

    __tablename__ = "alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    severity = Column(String(20), nullable=False)  # "low", "medium", "high", "critical"
    status = Column(String(20), nullable=False)  # "active", "resolved", "acknowledged"
    metric_name = Column(String(100), nullable=True)
    threshold_value = Column(Float, nullable=True)
    current_value = Column(Float, nullable=True)
    alert_metadata = Column(JSON, nullable=True)  # Additional alert metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<Alert(id={self.id}, name={self.name}, severity={self.severity})>"


# Note: AgentHealthStatus and AgentHealthCheckHistory models have been removed
# as per requirements - agent health check logs will be stored in Cosmos DB


class CircuitBreakerState(Base):
    """Track circuit breaker state for agents and methods."""

    __tablename__ = "circuit_breaker_state"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("user_agents.id"), nullable=False, index=True)
    method_id = Column(UUID(as_uuid=True), ForeignKey("agent_methods.id"), nullable=True, index=True)
    
    # Circuit breaker state
    state = Column(String(20), nullable=False)  # "CLOSED", "OPEN", "HALF_OPEN"
    failure_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    
    # Configuration
    failure_threshold = Column(Integer, default=5)  # Failures before opening
    success_threshold = Column(Integer, default=2)  # Successes before closing
    timeout_seconds = Column(Integer, default=60)  # Timeout between attempts
    backoff_multiplier = Column(Float, default=2.0)  # Exponential backoff multiplier
    
    # Tracking
    last_state_change = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_failure_time = Column(DateTime, nullable=True)
    opened_at = Column(DateTime, nullable=True)  # When circuit was opened
    
    # Metadata
    breaker_metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<CircuitBreakerState(agent_id={self.agent_id}, state={self.state})>"


class DegradationEvent(Base):
    """Track service degradation events."""

    __tablename__ = "degradation_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("user_agents.id"), nullable=False, index=True)
    
    # Degradation details
    degradation_type = Column(String(50), nullable=False)  # "timeout", "fallback", "reduced_functionality"
    reason = Column(Text, nullable=True)
    severity = Column(String(20), nullable=False)  # "minor", "moderate", "major"
    
    # Resolution
    resolution_status = Column(String(20), default="pending")  # "pending", "resolved", "escalated"
    resolved_at = Column(DateTime, nullable=True)
    fallback_action = Column(String(100), nullable=True)  # Action taken for degradation
    
    # Metadata
    degradation_metadata = Column(JSON, nullable=True)
    user_notification_sent = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<DegradationEvent(agent_id={self.agent_id}, type={self.degradation_type})>"
