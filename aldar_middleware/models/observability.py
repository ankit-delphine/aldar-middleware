"""Database models for Advanced Observability - Distributed Tracing & Audit Logs."""

from typing import Optional, Dict, Any, List
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Column, String, Integer, Float, DateTime, Boolean, Text, JSON,
    ForeignKey, Index, UniqueConstraint, func, BIGINT, Enum as SQLEnum
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

import enum
from aldar_middleware.database.base import Base


class TraceSampleType(str, enum.Enum):
    """Trace sampling types."""
    FULL = "full"          # 100% sampling
    PARTIAL = "partial"    # 10% sampling
    MINIMAL = "minimal"    # 1% sampling


class TraceStatusType(str, enum.Enum):
    """Trace status types."""
    PENDING = "pending"
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    PARTIAL = "partial"


class DistributedTrace(Base):
    """Root trace for end-to-end request tracing.
    
    Represents a complete request journey across all services and agent calls.
    """
    
    __tablename__ = "distributed_traces"
    
    # Primary Key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Core Trace Information
    correlation_id = Column(String(36), nullable=False, unique=True, index=True)
    trace_id = Column(String(32), nullable=False, unique=True, index=True)  # OpenTelemetry trace ID
    parent_span_id = Column(String(16), nullable=True)
    span_id = Column(String(16), nullable=False)
    
    # Request Information
    user_id = Column(String(255), nullable=True, index=True)
    request_method = Column(String(10), nullable=False)  # GET, POST, etc.
    request_path = Column(String(2048), nullable=False)
    request_endpoint = Column(String(255), nullable=True)  # Matched FastAPI endpoint
    
    # Trace Timing (milliseconds for precision)
    start_time = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, index=True)
    end_time = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)  # Total duration in milliseconds
    
    # Status & Error Information
    status = Column(SQLEnum(TraceStatusType), nullable=False, default=TraceStatusType.PENDING, index=True)
    http_status_code = Column(Integer, nullable=True)
    error_type = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)
    
    # Performance Metrics
    agent_count = Column(Integer, default=0)  # Number of agents called
    database_query_count = Column(Integer, default=0)  # Number of DB queries
    total_agent_time_ms = Column(Integer, default=0)  # Total time in agents (ms)
    total_query_time_ms = Column(Integer, default=0)  # Total time in DB (ms)
    
    # Trace Metadata
    trace_metadata = Column(JSONB, nullable=True)  # Custom metadata
    sampled = Column(Boolean, default=True)  # Was this trace sampled?
    sample_type = Column(SQLEnum(TraceSampleType), nullable=False, default=TraceSampleType.FULL)
    
    # Relationships (lazy-loaded for performance)
    request_response_audit = relationship(
        "RequestResponseAudit",
        back_populates="trace",
        cascade="all, delete-orphan",
        lazy="select"
    )
    database_queries = relationship(
        "DatabaseQueryTrace",
        back_populates="trace",
        cascade="all, delete-orphan",
        lazy="select"
    )
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Indexes for fast queries
    __table_args__ = (
        Index("idx_distributed_traces_correlation_id", "correlation_id"),
        Index("idx_distributed_traces_trace_id", "trace_id"),
        Index("idx_distributed_traces_user_id_created_at", "user_id", "created_at"),
        Index("idx_distributed_traces_status_created_at", "status", "created_at"),
        Index("idx_distributed_traces_created_at", "created_at"),
    )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "correlation_id": self.correlation_id,
            "trace_id": self.trace_id,
            "user_id": self.user_id,
            "request_method": self.request_method,
            "request_path": self.request_path,
            "request_endpoint": self.request_endpoint,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_ms": self.duration_ms,
            "status": self.status.value,
            "http_status_code": self.http_status_code,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "agent_count": self.agent_count,
            "database_query_count": self.database_query_count,
            "total_agent_time_ms": self.total_agent_time_ms,
            "total_query_time_ms": self.total_query_time_ms,
            "metadata": self.trace_metadata,
            "sampled": self.sampled,
            "sample_type": self.sample_type.value,
        }


class RequestResponseAudit(Base):
    """Audit log for HTTP request/response pairs.
    
    Stores complete request/response data for audit trail, debugging, and compliance.
    """
    
    __tablename__ = "request_response_audits"
    
    # Primary Key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Foreign Key to Trace
    trace_id = Column(UUID(as_uuid=True), ForeignKey("distributed_traces.id"), nullable=False, index=True)
    
    # Request Information
    correlation_id = Column(String(36), nullable=False, index=True)
    request_timestamp = Column(DateTime(timezone=True), nullable=False)
    request_method = Column(String(10), nullable=False)
    request_path = Column(String(2048), nullable=False)
    request_headers = Column(JSONB, nullable=True)  # Sanitized headers (PII removed)
    request_body = Column(Text, nullable=True)  # Request body (truncated/masked if configured)
    request_body_size_bytes = Column(Integer, nullable=True)
    request_body_truncated = Column(Boolean, default=False)
    
    # Response Information
    response_timestamp = Column(DateTime(timezone=True), nullable=False)
    response_status_code = Column(Integer, nullable=False, index=True)
    response_headers = Column(JSONB, nullable=True)  # Sanitized headers
    response_body = Column(Text, nullable=True)  # Response body (truncated/masked if configured)
    response_body_size_bytes = Column(Integer, nullable=True)
    response_body_truncated = Column(Boolean, default=False)
    
    # Performance
    response_time_ms = Column(Integer, nullable=False)
    
    # Audit Trail
    user_id = Column(String(255), nullable=True, index=True)
    client_ip = Column(String(45), nullable=True)  # IPv4 or IPv6
    
    # PII Masking Information
    pii_masked = Column(Boolean, default=True)  # Were PII patterns masked?
    masking_applied = Column(JSONB, nullable=True)  # {"emails": true, "tokens": true, ...}
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, index=True)
    
    # Indexes
    __table_args__ = (
        Index("idx_request_response_audits_trace_id", "trace_id"),
        Index("idx_request_response_audits_correlation_id", "correlation_id"),
        Index("idx_request_response_audits_user_id_created_at", "user_id", "created_at"),
        Index("idx_request_response_audits_status_code_created_at", "response_status_code", "created_at"),
        Index("idx_request_response_audits_created_at", "created_at"),
    )
    
    # Relationship
    trace = relationship("DistributedTrace", back_populates="request_response_audit")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "correlation_id": self.correlation_id,
            "request": {
                "method": self.request_method,
                "path": self.request_path,
                "headers": self.request_headers,
                "body": self.request_body,
                "body_size_bytes": self.request_body_size_bytes,
                "body_truncated": self.request_body_truncated,
                "timestamp": self.request_timestamp.isoformat() if self.request_timestamp else None,
            },
            "response": {
                "status_code": self.response_status_code,
                "headers": self.response_headers,
                "body": self.response_body,
                "body_size_bytes": self.response_body_size_bytes,
                "body_truncated": self.response_body_truncated,
                "timestamp": self.response_timestamp.isoformat() if self.response_timestamp else None,
            },
            "performance": {
                "response_time_ms": self.response_time_ms,
            },
            "audit": {
                "user_id": self.user_id,
                "client_ip": self.client_ip,
                "pii_masked": self.pii_masked,
                "masking_applied": self.masking_applied,
            },
        }


class DatabaseQueryTrace(Base):
    """Trace for database queries.
    
    Logs all database queries with timing, parameters, and execution details.
    """
    
    __tablename__ = "database_query_traces"
    
    # Primary Key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Foreign Key to Trace
    trace_id = Column(UUID(as_uuid=True), ForeignKey("distributed_traces.id"), nullable=False, index=True)
    
    # Query Information
    correlation_id = Column(String(36), nullable=False, index=True)
    query_sql = Column(Text, nullable=False)  # SQL statement (sanitized)
    query_type = Column(String(20), nullable=False)  # SELECT, INSERT, UPDATE, DELETE, etc.
    
    # Query Timing
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=False)
    
    # Execution Details
    rows_affected = Column(Integer, nullable=True)  # For INSERT/UPDATE/DELETE
    rows_returned = Column(Integer, nullable=True)  # For SELECT
    
    # Performance Classification
    slow_query = Column(Boolean, default=False)  # Executed slower than threshold
    slow_threshold_ms = Column(Integer, nullable=True)  # The threshold used for classification
    
    # Stack Trace Information
    caller_file = Column(String(255), nullable=True)
    caller_function = Column(String(255), nullable=True)
    caller_line = Column(Integer, nullable=True)
    
    # Status
    status = Column(String(20), nullable=False, default="success")  # success, error, timeout
    error_message = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, index=True)
    
    # Indexes
    __table_args__ = (
        Index("idx_database_query_traces_trace_id", "trace_id"),
        Index("idx_database_query_traces_correlation_id", "correlation_id"),
        Index("idx_database_query_traces_slow_query", "slow_query", "created_at"),
        Index("idx_database_query_traces_duration_ms", "duration_ms"),
        Index("idx_database_query_traces_created_at", "created_at"),
    )
    
    # Relationship
    trace = relationship("DistributedTrace", back_populates="database_queries")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "correlation_id": self.correlation_id,
            "query": {
                "sql": self.query_sql,
                "type": self.query_type,
            },
            "timing": {
                "start_time": self.start_time.isoformat() if self.start_time else None,
                "end_time": self.end_time.isoformat() if self.end_time else None,
                "duration_ms": self.duration_ms,
            },
            "execution": {
                "rows_affected": self.rows_affected,
                "rows_returned": self.rows_returned,
            },
            "performance": {
                "slow_query": self.slow_query,
                "slow_threshold_ms": self.slow_threshold_ms,
            },
            "caller": {
                "file": self.caller_file,
                "function": self.caller_function,
                "line": self.caller_line,
            },
            "status": self.status,
            "error_message": self.error_message,
        }