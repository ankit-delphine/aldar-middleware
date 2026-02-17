"""Menu and navigation related database models."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, validates

from aldar_middleware.database.base import Base


class Menu(Base):
    """Menu model for navigation items."""

    __tablename__ = "menus"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, unique=True)  # e.g., "chats", "agents", "launchpad"
    display_name = Column(String(100), nullable=False)  # e.g., "Chats", "Agents", "Launchpad"
    icon = Column(String(100), nullable=True)  # Icon class or name
    route = Column(String(200), nullable=True)  # Frontend route
    order = Column(Integer, default=0)  # Display order
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Menu(id={self.id}, name={self.name}, display_name={self.display_name})>"


class LaunchpadApp(Base):
    """Launchpad master table for apps data."""

    __tablename__ = "launchpad_apps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    app_id = Column(String(100), nullable=False, unique=True)  # e.g., "adq-app", "jira-cloud"
    title = Column(String(200), nullable=False)  # e.g., "ADQ App", "Jira Cloud"
    subtitle = Column(String(200), nullable=True)  # e.g., "Abu Dhabi Developmental", "Atlassian.com"
    description = Column(Text, nullable=True)
    tags = Column(JSON, nullable=True)  # Array of tags like ["Communication", "Project management"]
    logo_src = Column(String(500), nullable=True)  # Logo image path
    category = Column(String(50), nullable=False)  # e.g., "trending", "finance", "all"
    url = Column(String(500), nullable=True)  # App URL
    is_active = Column(Boolean, default=True)
    order = Column(Integer, default=0)  # Display order within category
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user_pins = relationship("UserLaunchpadPin", back_populates="app", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<LaunchpadApp(id={self.id}, app_id={self.app_id}, title={self.title})>"


class Agent(Base):
    """Agent master table for agents data."""

    __tablename__ = "agents"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    public_id = Column(UUID(as_uuid=True), unique=True, default=uuid.uuid4, index=True)
    name = Column(String(200), nullable=False)  # Agent name
    intro = Column(Text, nullable=True)  # Agent introduction
    description = Column(Text, nullable=True)  # Agent description
    icon = Column(String(500), nullable=True)  # Agent icon
    mcp_url = Column(String(500), nullable=True)  # MCP server URL
    health_url = Column(String(500), nullable=True)  # Health check URL
    model_name = Column(String(100), nullable=True)  # AI model name
    model_provider = Column(String(100), nullable=True)  # Model provider
    knowledge_sources = Column(JSON, nullable=True)  # Knowledge sources
    is_enabled = Column(Boolean, default=True)
    include_in_teams = Column(Boolean, default=False)  # Include agent in teams
    agent_header = Column(JSON, nullable=True)  # Agent header as JSON (headers dict)
    instruction = Column(Text, nullable=True)  # Agent instruction prompt
    agent_capabilities = Column(Text, nullable=True)  # Routing instruction
    add_history_to_context = Column(Boolean, default=False)  # Include history toggle
    agent_metadata = Column(JSON, nullable=True)  # Agent metadata

    # Health fields
    is_healthy = Column(Boolean, default=True)
    health_status = Column(String(50), nullable=True)  # healthy, unhealthy, degraded, unknown
    last_health_check = Column(DateTime, nullable=True)  # timestamp of last health check

    # Soft delete field
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)  # Soft delete flag

    # Legacy fields for backward compatibility (to be removed)
    agent_id = Column(String(100), nullable=True, unique=True)  # Legacy field
    title = Column(String(200), nullable=True)  # Legacy field
    subtitle = Column(String(200), nullable=True)  # Legacy field
    legacy_tags = Column(JSON, nullable=True)  # Legacy field - moved to agent_tags table
    logo_src = Column(String(500), nullable=True)  # Legacy field
    category = Column(String(50), nullable=True)  # Legacy field
    status = Column(String(20), default="active")  # Legacy field
    methods = Column(JSON, nullable=True)  # Legacy field - moved to agent_tools table
    last_used = Column(DateTime, nullable=True)
    order = Column(Integer, default=0)  # Display order within category

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    # Note: No cascade delete since we use soft delete (is_deleted=True)
    # All related data is preserved in the database
    user_pins = relationship(
        "UserAgentPin", back_populates="agent"
    )

    # New relationships for the schema
    sessions = relationship(
        "Session", 
        foreign_keys="Session.agent_id",  # Specify which FK to use (not document_knowledge_agent_id or document_my_agent_id)
        back_populates="agent"
    )
    messages = relationship(
        "Message",
        foreign_keys="Message.agent_id",  # Specify which FK to use (not document_knowledge_agent_id or document_my_agent_id)
        back_populates="agent"
    )
    agent_runs = relationship(
        "AgentRun", back_populates="agent"
    )
    token_usage = relationship(
        "TokenUsage", back_populates="agent"
    )
    user_access = relationship(
        "UserAgentAccess", back_populates="agent"
    )
    tags = relationship(
        "AgentTag", back_populates="agent"
    )
    configurations = relationship(
        "AgentConfiguration", back_populates="agent"
    )
    tools = relationship(
        "AgentTool", back_populates="agent"
    )
    # ERD compatibility relationship
    runs = relationship(
        "Run", back_populates="agent"
    )
    starter_prompts = relationship(
        "StarterPrompt", back_populates="agent"
    )

    @validates('intro')
    def validate_intro(self, key, value):
        """If intro is not provided, use 'Chat with' followed by the agent's name."""
        if not value and self.name:
            return f"Chat with {self.name}"
        return value

    @validates('instruction')
    def validate_instruction(self, key, value):
        """If instruction is not provided, use 'Chat with' followed by the agent's name."""
        if not value and self.name:
            return f"Chat with {self.name}"
        return value

    def __repr__(self) -> str:
        return f"<Agent(id={self.id}, public_id={self.public_id}, name={self.name})>"


class UserLaunchpadPin(Base):
    """User relationship table for pinned launchpad apps."""

    __tablename__ = "user_launchpad_pins"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    app_id = Column(UUID(as_uuid=True), ForeignKey("launchpad_apps.id"), nullable=False)
    is_pinned = Column(Boolean, default=False)
    order = Column(Integer, default=0)  # Order in pinned apps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User")
    app = relationship("LaunchpadApp", back_populates="user_pins")

    def __repr__(self) -> str:
        return (
            f"<UserLaunchpadPin(id={self.id}, user_id={self.user_id}, "
            f"app_id={self.app_id}, pinned={self.is_pinned})>"
        )


class UserAgentPin(Base):
    """User relationship table for pinned agents."""

    __tablename__ = "user_agent_pins"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    agent_id = Column(BigInteger, ForeignKey("agents.id"), nullable=False)
    is_pinned = Column(Boolean, default=False)
    order = Column(Integer, default=0)  # Order in pinned agents
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User")
    agent = relationship("Agent", back_populates="user_pins")

    def __repr__(self) -> str:
        return (
            f"<UserAgentPin(id={self.id}, user_id={self.user_id}, "
            f"agent_id={self.agent_id}, pinned={self.is_pinned})>"
        )
