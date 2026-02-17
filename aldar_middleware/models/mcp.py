"""MCP (Model Context Protocol) related database models."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, String, Text, Boolean, JSON, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class MCPConnection(Base):
    """MCP connection model."""

    __tablename__ = "mcp_connections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    server_url = Column(String(500), nullable=False)
    api_key = Column(String(255), nullable=True)
    connection_type = Column(String(50), nullable=False)  # "websocket", "http", "grpc"
    is_active = Column(Boolean, default=True)
    config = Column(JSON, nullable=True)  # Connection configuration
    last_connected = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    messages = relationship("MCPMessage", back_populates="connection", cascade="all, delete-orphan")
    methods = relationship("AgentMethod", back_populates="connection", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<MCPConnection(id={self.id}, name={self.name}, url={self.server_url})>"


class MCPMessage(Base):
    """MCP message model."""

    __tablename__ = "mcp_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id = Column(UUID(as_uuid=True), ForeignKey("mcp_connections.id"), nullable=False)
    message_type = Column(String(50), nullable=False)  # "request", "response", "notification"
    method = Column(String(100), nullable=True)  # MCP method name
    content = Column(Text, nullable=False)
    message_metadata = Column(JSON, nullable=True)  # Additional message metadata
    status = Column(String(20), nullable=False)  # "pending", "success", "error"
    response_time = Column(Integer, nullable=True)  # Response time in milliseconds
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    connection = relationship("MCPConnection", back_populates="messages")

    def __repr__(self) -> str:
        return f"<MCPMessage(id={self.id}, type={self.message_type}, method={self.method})>"


class AgentMethod(Base):
    """Agent method registry for tracking available methods and their schemas."""

    __tablename__ = "agent_methods"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id = Column(UUID(as_uuid=True), ForeignKey("mcp_connections.id"), nullable=False)
    method_name = Column(String(255), nullable=False)  # Full method name, e.g., "tools.calculate"
    display_name = Column(String(255), nullable=False)  # Human-readable name
    description = Column(Text, nullable=True)  # Method description
    parameters_schema = Column(JSON, nullable=True)  # JSON Schema for parameters
    return_type = Column(String(100), nullable=True)  # Return type description
    is_deprecated = Column(Boolean, default=False)
    version = Column(String(50), default="1.0.0")  # Method version
    tags = Column(JSON, nullable=True)  # List of tags for categorization
    additional_metadata = Column(JSON, nullable=True)  # Additional metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    connection = relationship("MCPConnection", back_populates="methods")
    executions = relationship("AgentMethodExecution", back_populates="method", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<AgentMethod(id={self.id}, name={self.method_name}, version={self.version})>"


class AgentMethodExecution(Base):
    """Track executions of agent methods."""

    __tablename__ = "agent_method_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    method_id = Column(UUID(as_uuid=True), ForeignKey("agent_methods.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("user_agents.id"), nullable=True)
    correlation_id = Column(String(255), nullable=True)  # Correlation ID for tracing
    
    # Execution details
    parameters = Column(JSON, nullable=True)  # Input parameters
    result = Column(JSON, nullable=True)  # Method result
    error_message = Column(Text, nullable=True)  # Error if execution failed
    status = Column(String(20), nullable=False)  # "pending", "running", "success", "error"
    
    # Performance metrics
    execution_duration_ms = Column(Integer, nullable=True)  # Duration in milliseconds
    retry_count = Column(Integer, default=0)  # Number of retries
    
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    method = relationship("AgentMethod", back_populates="executions")

    def __repr__(self) -> str:
        return f"<AgentMethodExecution(id={self.id}, method_id={self.method_id}, status={self.status})>"
