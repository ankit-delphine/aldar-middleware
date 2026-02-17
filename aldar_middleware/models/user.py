"""User-related database models."""

from uuid import UUID


import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, DateTime, String, Text, ForeignKey, BigInteger, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base

if TYPE_CHECKING:
    from aldar_middleware.models.user_agent_access import UserAgentAccess


class User(Base):
    """User model."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, index=True, nullable=False)
    username = Column(String(100), unique=True, index=True, nullable=True)  # Optional for Azure AD users
    full_name = Column(String(255), nullable=True)  # Combined first and last name
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)
    
    # Azure AD fields
    azure_ad_id = Column(String(255), unique=True, nullable=True, index=True)
    azure_tenant_id = Column(String(255), nullable=True)
    azure_upn = Column(String(255), nullable=True)  # User Principal Name
    azure_display_name = Column(String(255), nullable=True)
    azure_department = Column(String(255), nullable=True)
    azure_job_title = Column(String(255), nullable=True)
    azure_ad_refresh_token = Column(Text, nullable=True)  # Store Azure AD refresh token
    
    # Additional fields
    password_hash = Column(String(255), nullable=True)  # For non-Azure AD users
    preferences = Column(JSON, nullable=True)  # User preferences
    total_tokens_used = Column(BigInteger, default=0)  # Total tokens used across all sessions
    external_id = Column(String(255), nullable=True, index=True)  # External system ID
    company = Column(String(255), nullable=True)  # User's company
    is_onboarded = Column(Boolean, default=False)  # Onboarding status
    is_custom_query_enabled = Column(Boolean, default=False, nullable=False)  # Custom query feature enabled
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)
    first_logged_in_at = Column(DateTime, nullable=True)  # First login timestamp

    # Relationships
    agents = relationship(
        "UserAgent", back_populates="user", cascade="all, delete-orphan",
    )
    permissions = relationship(
        "UserPermission", back_populates="user", cascade="all, delete-orphan",
    )
    group_memberships = relationship(
        "UserGroupMembership", back_populates="user", cascade="all, delete-orphan",
    )
    
    # New relationships for the schema
    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="user", cascade="all, delete-orphan")
    agent_runs = relationship("AgentRun", back_populates="user", cascade="all, delete-orphan")
    token_usage = relationship("TokenUsage", back_populates="user", cascade="all, delete-orphan")
    # Specify foreign_keys because UserAgentAccess has two foreign keys to users
    # (user_id and granted_by). Use lambda to avoid circular import.
    agent_access = relationship(
        "UserAgentAccess",
        foreign_keys=lambda: [
            __import__(
                "aldar_middleware.models.user_agent_access",
                fromlist=["UserAgentAccess"],
            )
            .UserAgentAccess.user_id
        ],
        back_populates="user",
        cascade="all, delete-orphan",
    )
    question_trackers = relationship(
        "UserQuestionTracker", back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def groups(self):
        """Get groups from memberships."""
        return [membership.group for membership in self.group_memberships if membership.is_active and membership.group.is_active]

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email})>"


class UserAgent(Base):
    """User Agent model for AI agents."""

    __tablename__ = "user_agents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    mcp_connection_id = Column(
        UUID(as_uuid=True), ForeignKey("mcp_connections.id"), nullable=True,
    )  # Link to MCP server
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    agent_type = Column(String(50), nullable=False)  # e.g., "chatbot", "assistant"
    agent_config = Column(JSON, nullable=True)  # JSON configuration for the agent
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="agents")
    permissions = relationship(
        "UserPermission", back_populates="agent", cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<UserAgent(id={self.id}, name={self.name}, type={self.agent_type})>"


class UserPermission(Base):
    """User permission model for agent access control."""

    __tablename__ = "user_permissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("user_agents.id"), nullable=True)
    permission_type = Column(String(50), nullable=False)  # e.g., "read", "write"
    resource = Column(String(100), nullable=True)  # Specific resource or "*" for all
    is_granted = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="permissions")
    agent = relationship("UserAgent", back_populates="permissions")

    def __repr__(self) -> str:
        return (
            f"<UserPermission(id={self.id}, user_id={self.user_id}, "
            f"permission={self.permission_type})>"
        )


class UserGroup(Base):
    """User group model for organizing users."""

    __tablename__ = "user_groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    azure_ad_group_id = Column(
        String(255), unique=True, nullable=True, index=True,
    )  # Azure AD group ID
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    memberships = relationship(
        "UserGroupMembership", back_populates="group", cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<UserGroup(id={self.id}, name={self.name})>"


class UserGroupMembership(Base):
    """User group membership model."""

    __tablename__ = "user_group_memberships"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    group_id = Column(UUID(as_uuid=True), ForeignKey("user_groups.id"), nullable=False)
    role = Column(String(50), default="member")  # e.g., "member", "admin", "owner"
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="group_memberships")
    group = relationship("UserGroup", back_populates="memberships")

    def __repr__(self) -> str:
        return (
            f"<UserGroupMembership(id={self.id}, user_id={self.user_id}, "
            f"group_id={self.group_id})>"
        )
