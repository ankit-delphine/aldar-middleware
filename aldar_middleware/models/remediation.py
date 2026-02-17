"""Database models: Automated Remediation System."""

from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import (
    Column, String, Boolean, DateTime, Integer, Float, Enum, ForeignKey,
    JSON, Index, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import relationship
import uuid

from aldar_middleware.database.base import Base


class ActionType(str, PyEnum):
    """Types of remediation actions."""
    SCALE_AGENTS = "scale_agents"
    ENABLE_CIRCUIT_BREAKER = "enable_circuit_breaker"
    REDUCE_TOKEN_USAGE = "reduce_token_usage"
    RECONNECT_MCP = "reconnect_mcp"
    OPTIMIZE_DATABASE_QUERIES = "optimize_database_queries"


class ExecutionStatus(str, PyEnum):
    """Status of a remediation execution."""
    PENDING = "pending"
    DRY_RUN = "dry_run"
    EXECUTING = "executing"
    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class RemediationAction(Base):
    """Pre-configured remediation actions available in the system."""

    __tablename__ = "remediation_actions"
    __table_args__ = (
        Index("idx_action_type", "action_type"),
        Index("idx_action_enabled", "enabled"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False, unique=True)
    description = Column(String(1000), nullable=True)
    
    # Action type (use PG enum 'actiontype' with values from enum values)
    action_type = Column(
        PG_ENUM(ActionType, name="actiontype", values_callable=lambda x: [e.value for e in x], create_type=False),
        nullable=False,
    )
    
    # Service this action applies to
    service = Column(String(255), nullable=False)  # "agents", "mcp", "database", etc.
    
    # Action enabled/disabled
    enabled = Column(Boolean, default=True, nullable=False)
    
    # Configuration for the action
    configuration = Column(JSON, nullable=True)  # {'min_replicas': 1, 'max_replicas': 10, ...}
    
    # Safety guardrails specific to this action
    safety_guardrails = Column(JSON, nullable=True)  # {
    #   'max_executions_per_hour': 5,
    #   'cooldown_minutes': 5,
    #   'requires_dry_run': True,
    #   'auto_rollback_if_failed': True,
    #   'rollback_timeout_seconds': 30
    # }
    
    # Which alerts trigger this action
    trigger_alerts = Column(JSON, nullable=True)  # ['extreme_latency', 'very_high_error_rate']
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    rules = relationship("RemediationRule", back_populates="action", cascade="all, delete-orphan")
    executions = relationship("RemediationExecution", back_populates="action", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<RemediationAction(id={self.id}, name={self.name}, type={self.action_type})>"


class RemediationRule(Base):
    """Rules linking alerts to remediation actions."""

    __tablename__ = "remediation_rules"
    __table_args__ = (
        Index("idx_rule_enabled", "enabled"),
        Index("idx_rule_action_id", "action_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False, unique=True)
    description = Column(String(1000), nullable=True)
    
    # Which action this rule triggers
    action_id = Column(String(36), ForeignKey("remediation_actions.id"), nullable=False)
    
    # Condition: which alert triggers this rule
    alert_type = Column(String(255), nullable=False)  # 'extreme_latency', 'very_high_error_rate', etc.
    alert_severity = Column(String(50), nullable=False)  # 'critical', 'warning'
    
    # Rule enabled/disabled
    enabled = Column(Boolean, default=True, nullable=False)
    
    # Should we dry-run before executing?
    dry_run_first = Column(Boolean, default=True, nullable=False)
    
    # Auto-execute or require approval?
    auto_execute = Column(Boolean, default=True, nullable=False)
    
    # Required approval for this rule
    requires_approval = Column(Boolean, default=False, nullable=False)
    
    # Condition parameters (thresholds, etc.)
    condition_config = Column(JSON, nullable=True)  # {
    #   'min_latency_ms': 5000,
    #   'max_latency_ms': 20000,
    #   'min_error_rate': 0.5,
    #   'max_replicas_to_scale': 8
    # }
    
    # Order of execution (if multiple rules fire)
    priority = Column(Integer, default=100, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    action = relationship("RemediationAction", back_populates="rules")

    def __repr__(self) -> str:
        return f"<RemediationRule(id={self.id}, name={self.name}, alert_type={self.alert_type})>"


class RemediationExecution(Base):
    """Audit trail of all remediation executions."""

    __tablename__ = "remediation_executions"
    __table_args__ = (
        Index("idx_execution_status", "status"),
        Index("idx_execution_action_id", "action_id"),
        Index("idx_execution_alert_id", "alert_id"),
        Index("idx_execution_created_at", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Which action was executed
    action_id = Column(String(36), ForeignKey("remediation_actions.id"), nullable=False)
    
    # Which alert triggered this
    alert_id = Column(String(255), nullable=False)  # Grafana alert ID or similar
    
    # Execution status (use PG enum 'executionstatus' with values from enum values)
    status = Column(
        PG_ENUM(ExecutionStatus, name="executionstatus", values_callable=lambda x: [e.value for e in x], create_type=False),
        default=ExecutionStatus.PENDING,
        nullable=False,
    )
    
    # Execution details
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    # Reason for execution
    trigger_reason = Column(String(1000), nullable=True)
    
    # Parameters used for this execution
    execution_parameters = Column(JSON, nullable=True)  # {'new_replicas': 4, 'previous_replicas': 2, ...}
    
    # Metrics before and after
    metrics_before = Column(JSON, nullable=True)  # {'latency_ms': 5000, 'error_rate': 0.1, ...}
    metrics_after = Column(JSON, nullable=True)
    
    # Dry-run simulation result
    dry_run_result = Column(JSON, nullable=True)  # {'predicted_outcome': 'latency improved 30%', ...}
    
    # Was it rolled back?
    rolled_back = Column(Boolean, default=False, nullable=False)
    rollback_reason = Column(String(500), nullable=True)
    rollback_at = Column(DateTime, nullable=True)
    
    # Error message if failed
    error_message = Column(String(1000), nullable=True)
    
    # Success impact
    success = Column(Boolean, default=False, nullable=False)
    impact = Column(String(1000), nullable=True)  # "Latency reduced by 40%", etc.
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    action = relationship("RemediationAction", back_populates="executions")

    def __repr__(self) -> str:
        return f"<RemediationExecution(id={self.id}, status={self.status}, action_id={self.action_id})>"