"""Routing and orchestration database models."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, String, Text, Boolean, JSON, Float, Integer, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class AgentCapability(Base):
    """Agent capability registry for intelligent routing."""

    __tablename__ = "agent_capabilities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("user_agents.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Capability identification
    capability_name = Column(String(255), nullable=False)  # e.g., "data_analysis", "nlp", "summarization"
    capability_category = Column(String(100), nullable=False)  # e.g., "analysis", "nlp", "generation"
    description = Column(Text, nullable=True)
    
    # Scoring (0-100)
    score = Column(Float, default=50.0)  # Self-assessed capability score
    accuracy_score = Column(Float, nullable=True)  # Based on historical performance
    latency_score = Column(Float, nullable=True)  # Inverse of avg response time
    cost_score = Column(Float, nullable=True)  # Inverse of execution cost
    availability_score = Column(Float, nullable=True)  # Uptime percentage
    
    # Metadata
    tags = Column(JSON, nullable=True)  # Array of tags for categorization
    capability_metadata = Column(JSON, nullable=True)  # Additional capability metadata
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<AgentCapability(id={self.id}, agent_id={self.agent_id}, capability={self.capability_name})>"


class RoutingPolicy(Base):
    """Routing policies for intelligent agent selection."""

    __tablename__ = "routing_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Policy identification
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    
    # Policy configuration
    rules = Column(JSON, nullable=False)  # Routing rules configuration
    is_default = Column(Boolean, default=False)  # Use as default when no policy specified
    priority = Column(Integer, default=0)  # Execution order for multiple policies
    enabled = Column(Boolean, default=True)
    
    # Audit
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    executions = relationship("RoutingExecution", back_populates="policy", cascade="all, delete-orphan")

    # Indexes
    __table_args__ = (
        Index("idx_routing_policies_user_id", "user_id"),
        Index("idx_routing_policies_enabled", "enabled"),
        Index("idx_routing_policies_default", "is_default"),
    )

    def __repr__(self) -> str:
        return f"<RoutingPolicy(id={self.id}, name={self.name}, user_id={self.user_id})>"


class RoutingExecution(Base):
    """Track routing decisions for analytics."""

    __tablename__ = "routing_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    policy_id = Column(UUID(as_uuid=True), ForeignKey("routing_policies.id"), nullable=True)
    
    # Routing context
    request_context = Column(JSON, nullable=True)  # Input request details
    selected_agent_id = Column(UUID(as_uuid=True), ForeignKey("user_agents.id"), nullable=False)
    selected_reason = Column(String(255), nullable=True)  # Why this agent was selected
    
    # Analysis data
    candidate_agents = Column(JSON, nullable=True)  # List of candidates and scores
    scoring_criteria = Column(JSON, nullable=True)  # Criteria used for scoring
    scores = Column(JSON, nullable=True)  # Detailed scores for each candidate
    
    # Performance
    response_time_ms = Column(Integer, nullable=True)
    status = Column(String(20), default="success")  # "success", "error"
    error_reason = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    policy = relationship("RoutingPolicy", back_populates="executions")

    # Indexes
    __table_args__ = (
        Index("idx_routing_executions_user_id", "user_id"),
        Index("idx_routing_executions_policy_id", "policy_id"),
        Index("idx_routing_executions_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<RoutingExecution(id={self.id}, selected_agent_id={self.selected_agent_id})>"


class Workflow(Base):
    """Workflow definitions for multi-step orchestration."""

    __tablename__ = "workflows"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Workflow identification
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    version = Column(String(50), default="1.0.0")
    
    # Definition
    definition = Column(JSON, nullable=False)  # Complete workflow DSL
    tags = Column(JSON, nullable=True)  # Categorization tags
    workflow_metadata = Column(JSON, nullable=True)  # Additional metadata
    
    # Status
    is_active = Column(Boolean, default=True)
    is_template = Column(Boolean, default=False)  # Reusable template
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    executions = relationship("WorkflowExecution", back_populates="workflow", cascade="all, delete-orphan")

    # Indexes
    __table_args__ = (
        Index("idx_workflows_user_id", "user_id"),
        Index("idx_workflows_is_active", "is_active"),
        Index("idx_workflows_is_template", "is_template"),
    )

    def __repr__(self) -> str:
        return f"<Workflow(id={self.id}, name={self.name}, version={self.version})>"


class WorkflowExecution(Base):
    """Track workflow executions."""

    __tablename__ = "workflow_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Execution tracking
    correlation_id = Column(String(255), nullable=False, index=True)
    status = Column(String(20), default="pending")  # "pending", "running", "success", "error", "cancelled"
    
    # Data
    inputs = Column(JSON, nullable=True)  # Workflow input data
    outputs = Column(JSON, nullable=True)  # Final results
    execution_plan = Column(JSON, nullable=True)  # Expanded steps with dependencies
    
    # Performance
    total_duration_ms = Column(Integer, nullable=True)
    
    # Timestamps
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    workflow = relationship("Workflow", back_populates="executions")
    steps = relationship("WorkflowStep", back_populates="execution", cascade="all, delete-orphan")

    # Indexes
    __table_args__ = (
        Index("idx_workflow_executions_workflow_id", "workflow_id"),
        Index("idx_workflow_executions_user_id", "user_id"),
        Index("idx_workflow_executions_status", "status"),
        Index("idx_workflow_executions_created_at", "created_at"),
        Index("idx_workflow_executions_correlation_id", "correlation_id"),
    )

    def __repr__(self) -> str:
        return f"<WorkflowExecution(id={self.id}, workflow_id={self.workflow_id}, status={self.status})>"


class WorkflowStep(Base):
    """Track individual step executions within a workflow."""

    __tablename__ = "workflow_steps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id = Column(UUID(as_uuid=True), ForeignKey("workflow_executions.id"), nullable=False)
    
    # Step identification (from workflow definition)
    step_id = Column(String(255), nullable=False)  # e.g., "step_1"
    step_name = Column(String(255), nullable=False)
    step_type = Column(String(50), nullable=False)  # "agent_call", "condition", "parallel", "switch"
    
    # Agent information
    agent_id = Column(UUID(as_uuid=True), ForeignKey("user_agents.id"), nullable=True)
    method_id = Column(UUID(as_uuid=True), ForeignKey("agent_methods.id"), nullable=True)
    
    # Execution data
    status = Column(String(20), default="pending")  # "pending", "running", "success", "error", "skipped"
    inputs = Column(JSON, nullable=True)
    outputs = Column(JSON, nullable=True)
    error_reason = Column(Text, nullable=True)
    
    # Performance
    duration_ms = Column(Integer, nullable=True)
    
    # Timestamps
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    execution = relationship("WorkflowExecution", back_populates="steps")

    # Indexes
    __table_args__ = (
        Index("idx_workflow_steps_execution_id", "execution_id"),
        Index("idx_workflow_steps_step_id", "step_id"),
        Index("idx_workflow_steps_status", "status"),
        Index("idx_workflow_steps_agent_id", "agent_id"),
    )

    def __repr__(self) -> str:
        return f"<WorkflowStep(id={self.id}, step_id={self.step_id}, status={self.status})>"