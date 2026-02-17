"""Rate limiting and usage quota database models."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, String, Text, Boolean, JSON, Float, Integer, ForeignKey, Index, BigInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class RateLimitConfig(Base):
    """Rate limiting configuration per user/agent/method."""

    __tablename__ = "rate_limit_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Scope of rate limit
    scope_type = Column(String(50), nullable=False)  # "user", "agent", "method"
    agent_id = Column(UUID(as_uuid=True), ForeignKey("user_agents.id"), nullable=True)
    method_id = Column(String(255), nullable=True)  # method name for scope_type="method"
    
    # Rate limit configuration
    requests_per_minute = Column(Integer, nullable=False, default=100)
    concurrent_executions = Column(Integer, nullable=False, default=10)
    requests_per_hour = Column(Integer, nullable=True)
    requests_per_day = Column(Integer, nullable=True)
    
    # Burst and throttling
    burst_size = Column(Integer, nullable=True)  # Allow burst above limit
    throttle_enabled = Column(Boolean, default=True)  # Throttle vs reject
    
    # Status and metadata
    is_active = Column(Boolean, default=True)
    description = Column(Text, nullable=True)
    config_metadata = Column(JSON, nullable=True)
    
    # Audit
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index("idx_rate_limit_configs_user_id", "user_id"),
        Index("idx_rate_limit_configs_scope", "scope_type", "agent_id", "method_id"),
        Index("idx_rate_limit_configs_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<RateLimitConfig(id={self.id}, scope={self.scope_type}, requests_per_min={self.requests_per_minute})>"


class RateLimitUsage(Base):
    """Real-time rate limit usage tracking (time-series data)."""

    __tablename__ = "rate_limit_usage"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    config_id = Column(UUID(as_uuid=True), ForeignKey("rate_limit_configs.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Usage window
    window_type = Column(String(50), nullable=False)  # "minute", "hour", "day"
    window_start = Column(DateTime, nullable=False)  # Start of measurement window
    window_end = Column(DateTime, nullable=False)  # End of measurement window
    
    # Usage metrics
    request_count = Column(Integer, default=0)  # Total requests in window
    concurrent_count = Column(Integer, default=0)  # Current concurrent executions
    throttled_count = Column(Integer, default=0)  # Requests that were throttled
    rejected_count = Column(Integer, default=0)  # Requests that were rejected (429)
    
    # Cost tracking
    total_cost = Column(Float, default=0.0)  # Total cost in this window
    
    # Last updated
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index("idx_rate_limit_usage_config_id", "config_id"),
        Index("idx_rate_limit_usage_user_id", "user_id"),
        Index("idx_rate_limit_usage_window", "window_start", "window_end"),
        Index("idx_rate_limit_usage_user_window", "user_id", "window_start"),
    )

    def __repr__(self) -> str:
        return f"<RateLimitUsage(id={self.id}, window={self.window_type}, requests={self.request_count})>"


class CostModel(Base):
    """Cost model for execution pricing."""

    __tablename__ = "cost_models"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Scope of cost model
    agent_id = Column(UUID(as_uuid=True), ForeignKey("user_agents.id"), nullable=True)
    method_id = Column(String(255), nullable=True)  # Specific method name, or NULL for all methods
    
    # Pricing
    per_execution = Column(Float, nullable=False, default=0.001)  # Base cost per execution
    per_result_kb = Column(Float, nullable=False, default=0.0001)  # Cost per KB of result
    per_token = Column(Float, nullable=True)  # Cost per token (for LLM methods)
    minimum_charge = Column(Float, nullable=False, default=0.001)  # Minimum cost per call
    
    # Discounts and adjustments
    volume_discount_threshold = Column(Integer, nullable=True)  # Executions to reach discount
    volume_discount_percent = Column(Float, nullable=True)  # Discount percentage
    monthly_discount_percent = Column(Float, default=0.0)  # Month-over-month discount
    
    # Status
    is_active = Column(Boolean, default=True)
    effective_from = Column(DateTime, nullable=True)
    effective_to = Column(DateTime, nullable=True)
    
    # Metadata
    currency = Column(String(3), default="USD")
    description = Column(Text, nullable=True)
    cost_metadata = Column(JSON, nullable=True)
    
    # Audit
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index("idx_cost_models_user_id", "user_id"),
        Index("idx_cost_models_agent_method", "agent_id", "method_id"),
        Index("idx_cost_models_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<CostModel(id={self.id}, per_exec={self.per_execution})>"


class UsageQuota(Base):
    """Usage quota tracking and enforcement."""

    __tablename__ = "usage_quotas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Quota period
    quota_type = Column(String(50), nullable=False)  # "monthly", "yearly", "custom"
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    
    # Limits
    max_executions = Column(BigInteger, nullable=True)  # Total executions allowed
    max_cost = Column(Float, nullable=True)  # Total cost limit
    max_concurrent = Column(Integer, nullable=True)  # Max concurrent executions
    
    # Current usage
    executions_used = Column(BigInteger, default=0)
    cost_used = Column(Float, default=0.0)
    
    # Warnings
    warning_threshold_percent = Column(Float, default=80.0)  # 80%
    warning_sent_at_percent = Column(Float, nullable=True)  # At what % was warning sent
    critical_threshold_percent = Column(Float, default=95.0)  # 95%
    critical_sent_at_percent = Column(Float, nullable=True)
    
    # Status
    is_active = Column(Boolean, default=True)
    is_exceeded = Column(Boolean, default=False)
    
    # Metadata
    quota_metadata = Column(JSON, nullable=True)
    
    # Audit
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index("idx_usage_quotas_user_id", "user_id"),
        Index("idx_usage_quotas_period", "period_start", "period_end"),
        Index("idx_usage_quotas_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<UsageQuota(id={self.id}, type={self.quota_type}, usage={self.executions_used})>"


class UserBudget(Base):
    """User spending budget and limits."""

    __tablename__ = "user_budgets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True)
    
    # Budget
    monthly_budget = Column(Float, nullable=True)  # Monthly spending limit
    total_budget = Column(Float, nullable=True)  # Total lifetime budget
    
    # Current usage
    current_month_spent = Column(Float, default=0.0)
    total_spent = Column(Float, default=0.0)
    
    # Budget control
    enforce_limit = Column(Boolean, default=True)  # Stop execution if limit reached
    alert_at_percent = Column(Float, default=75.0)  # Alert when 75% spent
    
    # Alert timestamps
    last_alert_at = Column(DateTime, nullable=True)
    alert_frequency_minutes = Column(Integer, default=60)  # How often to alert
    
    # Status
    is_active = Column(Boolean, default=True)
    blocked_reason = Column(String(255), nullable=True)
    blocked_at = Column(DateTime, nullable=True)
    
    # Reset date
    month_start = Column(DateTime, nullable=True)
    
    # Metadata
    budget_metadata = Column(JSON, nullable=True)
    
    # Audit
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index("idx_user_budgets_user_id", "user_id"),
        Index("idx_user_budgets_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<UserBudget(user_id={self.user_id}, monthly={self.monthly_budget})>"


class UsageReport(Base):
    """Aggregated usage reports (for analytics and billing)."""

    __tablename__ = "usage_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Period
    report_period = Column(String(50), nullable=False)  # "daily", "weekly", "monthly", "yearly"
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    
    # Metrics
    total_executions = Column(BigInteger, default=0)
    total_cost = Column(Float, default=0.0)
    average_cost_per_execution = Column(Float, default=0.0)
    
    # Agent breakdown (JSON)
    agent_usage = Column(JSON, nullable=True)  # {agent_id: {executions, cost}, ...}
    method_usage = Column(JSON, nullable=True)  # {method_id: {executions, cost}, ...}
    
    # Cost breakdown
    cost_by_category = Column(JSON, nullable=True)  # {category: cost, ...}
    
    # Performance metrics
    average_response_time_ms = Column(Float, nullable=True)
    error_rate_percent = Column(Float, default=0.0)
    success_count = Column(BigInteger, default=0)
    error_count = Column(BigInteger, default=0)
    
    # Forecasting
    projected_monthly_cost = Column(Float, nullable=True)
    projected_monthly_executions = Column(BigInteger, nullable=True)
    
    # Status
    is_finalized = Column(Boolean, default=False)  # Final report for billing
    
    # Generated metadata
    report_metadata = Column(JSON, nullable=True)
    
    # Audit
    generated_at = Column(DateTime, default=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index("idx_usage_reports_user_id", "user_id"),
        Index("idx_usage_reports_period", "period_start", "period_end"),
        Index("idx_usage_reports_period_type", "report_period"),
    )

    def __repr__(self) -> str:
        return f"<UsageReport(user_id={self.user_id}, period={self.report_period}, cost={self.total_cost})>"