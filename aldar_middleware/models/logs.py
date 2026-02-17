"""Log models for storing user and admin logs in PostgreSQL with JSONB."""

import uuid
from sqlalchemy import Column, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from aldar_middleware.database.base import Base


class UserLog(Base):
    """
    User activity logs table.
    
    Stores user activity events (USER_CONVERSATION_CREATED, USER_MESSAGE_CREATED, etc.)
    in 3.0 format as JSONB for fast queries.
    """
    
    __tablename__ = "user_logs"
    
    # Primary key
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    
    # Timestamp for sorting and filtering
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, index=True, server_default=func.now())
    
    # Action type for filtering (e.g., USER_CONVERSATION_CREATED, USER_MESSAGE_CREATED)
    action_type = Column(String(100), nullable=False, index=True)
    
    # User identification
    user_id = Column(String(36), nullable=True, index=True)
    email = Column(String(255), nullable=True, index=True)
    
    # Correlation ID for tracking
    correlation_id = Column(String(255), nullable=True, index=True)
    
    # Full log data as JSONB (3.0 format)
    log_data = Column(JSONB, nullable=False)
    
    # Indexes for common queries
    __table_args__ = (
        Index('idx_user_logs_timestamp', 'timestamp'),
        Index('idx_user_logs_action_type', 'action_type'),
        Index('idx_user_logs_user_id', 'user_id'),
        Index('idx_user_logs_email', 'email'),
        Index('idx_user_logs_correlation_id', 'correlation_id'),
        Index('idx_user_logs_created_at', 'created_at'),
        # Composite index for common query patterns
        Index('idx_user_logs_user_timestamp', 'user_id', 'timestamp'),
        Index('idx_user_logs_action_timestamp', 'action_type', 'timestamp'),
        # GIN index for JSONB queries
        Index('idx_user_logs_log_data_gin', 'log_data', postgresql_using='gin'),
    )
    
    def __repr__(self) -> str:
        return f"<UserLog(id={self.id}, action_type={self.action_type}, timestamp={self.timestamp})>"


class AdminLog(Base):
    """
    Admin/Application logs table.
    
    Stores all application logs (INFO, WARNING, ERROR, etc.)
    with full details as JSONB for comprehensive logging.
    """
    
    __tablename__ = "admin_logs"
    
    # Primary key
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    
    # Timestamp for sorting and filtering
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True, server_default=func.now())
    
    # Log level for filtering
    level = Column(String(20), nullable=False, index=True)  # INFO, WARNING, ERROR, DEBUG
    
    # Action type for admin actions (e.g., USERS_LOGS_EXPORTED, KNOWLEDGE_AGENT_UPDATED)
    action_type = Column(String(100), nullable=True, index=True)
    
    # User identification (if available)
    user_id = Column(String(36), nullable=True, index=True)
    email = Column(String(255), nullable=True, index=True)
    username = Column(String(255), nullable=True, index=True)
    
    # Correlation ID for tracking
    correlation_id = Column(String(255), nullable=True, index=True)
    
    # Module/function info
    module = Column(String(255), nullable=True, index=True)
    function = Column(String(255), nullable=True)
    
    # Log message (for text search)
    message = Column(Text, nullable=True)
    
    # Full log data as JSONB (all details)
    log_data = Column(JSONB, nullable=False)
    
    # Indexes for common queries
    __table_args__ = (
        Index('idx_admin_logs_timestamp', 'timestamp'),
        Index('idx_admin_logs_level', 'level'),
        Index('idx_admin_logs_action_type', 'action_type'),
        Index('idx_admin_logs_user_id', 'user_id'),
        Index('idx_admin_logs_email', 'email'),
        Index('idx_admin_logs_correlation_id', 'correlation_id'),
        Index('idx_admin_logs_module', 'module'),
        # Composite index for common query patterns
        Index('idx_admin_logs_level_timestamp', 'level', 'timestamp'),
        Index('idx_admin_logs_action_timestamp', 'action_type', 'timestamp'),
        Index('idx_admin_logs_user_timestamp', 'user_id', 'timestamp'),
        # GIN index for JSONB queries
        Index('idx_admin_logs_log_data_gin', 'log_data', postgresql_using='gin'),
        # Full text search index on message
        Index('idx_admin_logs_message_gin', 'message', postgresql_using='gin',
              postgresql_ops={'message': 'gin_trgm_ops'}),
    )
    
    def __repr__(self) -> str:
        return f"<AdminLog(id={self.id}, level={self.level}, timestamp={self.timestamp})>"

