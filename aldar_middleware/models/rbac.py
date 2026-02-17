"""
RBAC (Role-Based Access Control) Models
Non-hierarchical role system with explicit permissions and service assignments
"""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    JSON,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from aldar_middleware.database.base import Base

# Association table for many-to-many relationship between role groups and roles
role_group_roles = Table(
    "role_group_roles",
    Base.metadata,
    Column("role_group_id", Integer, ForeignKey("rbac_role_groups.id"), primary_key=True),
    Column("role_id", PGUUID(as_uuid=True), ForeignKey("rbac_roles.id"), primary_key=True),
)

# Association table for many-to-many relationship between users and role groups
user_role_groups = Table(
    "user_role_groups",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("rbac_users.id"), primary_key=True),
    Column("role_group_id", Integer, ForeignKey("rbac_role_groups.id"), primary_key=True),
    Column("granted_by", Integer, ForeignKey("rbac_users.id")),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

# Association table for many-to-many relationship between role groups and agents
role_group_services = Table(
    "role_group_services",
    Base.metadata,
    Column("role_group_id", Integer, ForeignKey("rbac_role_groups.id"), primary_key=True),
    Column("service_id", PGUUID(as_uuid=True), ForeignKey("rbac_agents.id"), primary_key=True),
)

# Association table for many-to-many relationship between roles and agents
role_services = Table(
    "role_services",
    Base.metadata,
    Column("role_id", PGUUID(as_uuid=True), ForeignKey("rbac_roles.id"), primary_key=True),
    Column("service_id", PGUUID(as_uuid=True), ForeignKey("rbac_agents.id"), primary_key=True),
)

# Association table for user-specific role assignments
user_specific_roles = Table(
    "user_specific_roles",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("rbac_users.id"), primary_key=True),
    Column("role_id", PGUUID(as_uuid=True), ForeignKey("rbac_roles.id"), primary_key=True),
    Column("granted_by", Integer, ForeignKey("rbac_users.id")),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

# Association table for role inheritance (parent-child roles)
role_parent_roles = Table(
    "role_parent_roles",
    Base.metadata,
    Column("parent_role_id", PGUUID(as_uuid=True), ForeignKey("rbac_roles.id"), primary_key=True),
    Column("child_role_id", PGUUID(as_uuid=True), ForeignKey("rbac_roles.id"), primary_key=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)


class RBACUserAccess(Base):
    """Individual user access for specific apps/services (outside of role groups)"""

    __tablename__ = "rbac_user_access"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("rbac_users.id"), nullable=False, index=True)
    access_name = Column(String(100), nullable=False)
    access_type = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    granted_by = Column(Integer, ForeignKey("rbac_users.id"), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user = relationship("RBACUser", foreign_keys=[user_id], back_populates="individual_access")
    granter = relationship("RBACUser", foreign_keys=[granted_by])

    def __repr__(self):
        return f"<RBACUserAccess(user_id={self.user_id}, access='{self.access_name}', type='{self.access_type}')>"


class RBACRoleGroup(Base):
    """Role Group model - contains multiple roles with their permissions"""

    __tablename__ = "rbac_role_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    roles = relationship("RBACRole", secondary=role_group_roles, back_populates="role_groups", lazy="selectin")
    users = relationship(
        "RBACUser",
        secondary=user_role_groups,
        primaryjoin="RBACRoleGroup.id == user_role_groups.c.role_group_id",
        secondaryjoin="RBACUser.id == user_role_groups.c.user_id",
        back_populates="role_groups",
        lazy="selectin",
    )
    services = relationship("RBACAgent", secondary=role_group_services, back_populates="role_groups", lazy="selectin")

    def __repr__(self):
        return f"<RBACRoleGroup(name='{self.name}')>"


class RBACRole(Base):
    """Role model for non-hierarchical RBAC system"""

    __tablename__ = "rbac_roles"

    id = Column(PGUUID(as_uuid=True), primary_key=True, index=True, default=uuid.uuid4)
    name = Column(String(100), unique=True, nullable=False, index=True)
    level = Column(Integer, nullable=False, index=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    services = relationship("RBACAgent", secondary=role_services, back_populates="roles", lazy="selectin")
    users = relationship(
        "RBACUser",
        secondary=user_specific_roles,
        primaryjoin="RBACRole.id == user_specific_roles.c.role_id",
        secondaryjoin="RBACUser.id == user_specific_roles.c.user_id",
        back_populates="specific_roles",
        lazy="selectin",
    )
    role_groups = relationship("RBACRoleGroup", secondary=role_group_roles, back_populates="roles", lazy="selectin")
    role_permissions = relationship(
        "RBACRolePermission",
        back_populates="role",
        foreign_keys="RBACRolePermission.role_id",
        lazy="selectin",
    )

    parent_roles = relationship(
        "RBACRole",
        secondary=role_parent_roles,
        primaryjoin="RBACRole.id == role_parent_roles.c.child_role_id",
        secondaryjoin="RBACRole.id == role_parent_roles.c.parent_role_id",
        backref="child_roles",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<RBACRole(name='{self.name}', level={self.level})>"


class RBACAgent(Base):
    """Agent model for agent-based access control"""

    __tablename__ = "rbac_agents"

    id = Column(PGUUID(as_uuid=True), primary_key=True, index=True, default=uuid.uuid4)
    name = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    roles = relationship("RBACRole", secondary=role_services, back_populates="services", lazy="selectin")
    role_groups = relationship("RBACRoleGroup", secondary=role_group_services, back_populates="services", lazy="selectin")

    def __repr__(self):
        return f"<RBACAgent(name='{self.name}')>"


# Backward compatibility alias
RBACService = RBACAgent


class RBACUser(Base):
    """User model for RBAC system"""

    __tablename__ = "rbac_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(200), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    specific_roles = relationship(
        "RBACRole",
        secondary=user_specific_roles,
        primaryjoin="RBACUser.id == user_specific_roles.c.user_id",
        secondaryjoin="RBACRole.id == user_specific_roles.c.role_id",
        back_populates="users",
        lazy="selectin",
    )
    role_groups = relationship(
        "RBACRoleGroup",
        secondary=user_role_groups,
        primaryjoin="RBACUser.id == user_role_groups.c.user_id",
        secondaryjoin="RBACRoleGroup.id == user_role_groups.c.role_group_id",
        back_populates="users",
        lazy="selectin",
    )
    individual_access = relationship(
        "RBACUserAccess",
        foreign_keys="RBACUserAccess.user_id",
        back_populates="user",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<RBACUser(username='{self.username}', email='{self.email}')>"


class RBACPermission(Base):
    """Permission model for granular access control"""

    __tablename__ = "rbac_permissions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    resource = Column(String(100), nullable=False)
    action = Column(String(50), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    role_permissions = relationship(
        "RBACRolePermission",
        back_populates="permission",
        foreign_keys="RBACRolePermission.permission_id",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<RBACPermission(name='{self.name}', resource='{self.resource}', action='{self.action}')>"


class RBACRolePermission(Base):
    """Association table for role-permission relationships"""

    __tablename__ = "rbac_role_permissions"

    id = Column(Integer, primary_key=True, index=True)
    role_id = Column(PGUUID(as_uuid=True), ForeignKey("rbac_roles.id"), nullable=False)
    permission_id = Column(Integer, ForeignKey("rbac_permissions.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    role = relationship("RBACRole", back_populates="role_permissions", foreign_keys=[role_id])
    permission = relationship("RBACPermission", back_populates="role_permissions", foreign_keys=[permission_id])

    def __repr__(self):
        return f"<RBACRolePermission(role_id={self.role_id}, permission_id={self.permission_id})>"


class RBACUserSession(Base):
    """User session tracking for RBAC"""

    __tablename__ = "rbac_user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("rbac_users.id"), nullable=False)
    session_token = Column(String(255), unique=True, nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_accessed = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("RBACUser")

    def __repr__(self):
        return f"<RBACUserSession(user_id={self.user_id}, token='{self.session_token[:10]}...')>"


SERVICE_TYPES = [
    "api",
    "database",
    "message_queue",
    "monitoring",
    "file_storage",
    "notification",
    "analytics",
    "reporting",
    "agent",
]

COMMON_PERMISSIONS = [
    ("read", "Read access"),
    ("write", "Write access"),
    ("delete", "Delete access"),
    ("admin", "Administrative access"),
    ("execute", "Execute access"),
    ("manage", "Management access"),
]


class AzureADGroupRoleMapping(Base):
    """Mapping between Azure AD groups and RBAC roles for automatic role assignment"""

    __tablename__ = "azure_ad_group_role_mappings"

    id = Column(Integer, primary_key=True, index=True)
    azure_ad_group_id = Column(String(255), nullable=False, index=True)
    azure_ad_group_name = Column(String(255), nullable=True)
    role_id = Column(PGUUID(as_uuid=True), ForeignKey("rbac_roles.id"), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_by = Column(String(255), nullable=True)

    role = relationship("RBACRole")

    def __repr__(self):
        return f"<AzureADGroupRoleMapping(azure_ad_group_id='{self.azure_ad_group_id}', role_id={self.role_id})>"


class RBACUserPivot(Base):
    """Pivot table mapping users to their Azure AD groups.
    
    This table is updated on every user login with the current list of
    Azure AD group UUIDs the user belongs to. Old entries are replaced with new ones.
    
    Access logic: If a user has at least one AD group UUID that matches an agent's
    assigned AD group UUIDs, the user has access to that agent.
    """

    __tablename__ = "rbac_user_pivot"

    id = Column(PGUUID(as_uuid=True), primary_key=True, index=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    azure_ad_groups = Column(JSON, nullable=False)  # List of Azure AD group UUIDs (strings)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self):
        return f"<RBACUserPivot(email='{self.email}', id='{self.id}', groups={len(self.azure_ad_groups) if self.azure_ad_groups else 0})>"


class RBACAgentPivot(Base):
    """Pivot table mapping agents to their Azure AD groups.
    
    This table stores which Azure AD group UUIDs have access to which agents.
    Updated via API when assigning AD groups to agents.
    
    Multiple agents can share the same AD group UUIDs. If a user has at least
    one matching AD group UUID, they have access to all agents with that group.
    
    Access is determined by intersection: user's AD groups ∩ agent's AD groups.
    If there's any overlap, the user has access to that agent.
    """

    __tablename__ = "rbac_agent_pivot"

    id = Column(PGUUID(as_uuid=True), primary_key=True, index=True, default=uuid.uuid4)
    agent_name = Column(String(255), unique=True, nullable=False, index=True)
    azure_ad_groups = Column(JSON, nullable=False)  # List of Azure AD group UUIDs (strings)
    agent_ad_groups_metadata = Column(JSON, nullable=True)  # List of JSON objects: [{"id": "uuid1", "name": "group1"}, ...]
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self):
        return f"<RBACAgentPivot(agent_name='{self.agent_name}', id='{self.id}', groups={len(self.azure_ad_groups) if self.azure_ad_groups else 0})>"
