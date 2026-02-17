"""
RBAC Service Layer
Handles non-hierarchical roles, explicit permissions, and service assignments
"""

from typing import List, Dict, Optional, Set, Tuple, Any
from uuid import UUID
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, or_, select, func, delete
from sqlalchemy.orm import selectinload
from aldar_middleware.models.rbac import (
    RBACRole, RBACUser, RBACPermission, 
    RBACRolePermission, RBACUserSession, RBACRoleGroup, RBACUserAccess,
    user_specific_roles, role_services, user_role_groups, role_group_roles, role_group_services,
    role_parent_roles, RBACUserPivot
)
from aldar_middleware.models.user import User
from aldar_middleware.models.menu import Agent
from aldar_middleware.schemas.rbac import (
    RoleCreate, RoleUpdate, UserCreate, UserUpdate,
    ServiceCreate, ServiceUpdate, PermissionCreate,
    UserRoleResponse, RoleServiceAssignment
)
from aldar_middleware.exceptions import RBACError, PermissionDeniedError
import logging

logger = logging.getLogger(__name__)


class RBACServiceLayer:
    """Main RBAC service for role and permission management"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # Role Management - REMOVED (using AD group-based access control instead)

    # User Management
    async def create_user(self, user_data: UserCreate) -> RBACUser:
        """Create a new user"""
        # Check if username or email already exists
        result = await self.db.execute(
            select(RBACUser).where(
                or_(RBACUser.username == user_data.username, RBACUser.email == user_data.email)
            )
        )
        existing_user = result.scalar_one_or_none()
        if existing_user:
            raise RBACError(f"User with username '{user_data.username}' or email '{user_data.email}' already exists")
        
        user = RBACUser(
            username=user_data.username,
            email=user_data.email,
            full_name=user_data.full_name,
            is_active=user_data.is_active
        )
        
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        
        logger.info(f"Created user: {user.username}")
        return user

    async def get_user(self, user_id: int) -> Optional[RBACUser]:
        """Get user by ID"""
        result = await self.db.execute(
            select(RBACUser).where(RBACUser.id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_user_by_username(self, username: str) -> Optional[RBACUser]:
        """Get user by username"""
        result = await self.db.execute(
            select(RBACUser).where(RBACUser.username == username)
        )
        return result.scalar_one_or_none()




    # Service Management - DEPRECATED
    # Agents are now created via /api/v1/admin/agent endpoint
    # These methods are kept for backward compatibility but should not be used
    # The RBAC system now directly queries the agents table

    # Role-related functions removed - using AD group-based access control instead
    # Permission and Access Control - REMOVED (role-based, replaced with AD group intersection)
    # Role Group Management - REMOVED (all role group functions removed)
    # Permission checking via roles - REMOVED
    # User-role assignment - REMOVED
    # Azure AD role mapping - REMOVED
    # Role inheritance - REMOVED

    # Individual User Access Management
    async def grant_individual_access(self, username: str, access_name: str, access_type: str, 
                               description: str = None, granted_by: str = None, 
                               expires_at: Optional[datetime] = None) -> RBACUserAccess:
        """Grant individual access to a user for a specific app/service"""
        user = await self.get_user_by_username(username)
        if not user:
            raise RBACError(f"User '{username}' not found")
        
        # Check if access already exists
        result = await self.db.execute(
            select(RBACUserAccess).where(
                and_(
                    RBACUserAccess.user_id == user.id,
                    RBACUserAccess.access_name == access_name,
                    RBACUserAccess.is_active == True
                )
            )
        )
        existing_access = result.scalar_one_or_none()
        
        if existing_access:
            raise RBACError(f"User '{username}' already has access to '{access_name}'")
        
        granter_id = None
        if granted_by:
            granter = await self.get_user_by_username(granted_by)
            if granter:
                granter_id = granter.id
        
        user_access = RBACUserAccess(
            user_id=user.id,
            access_name=access_name,
            access_type=access_type,
            description=description,
            granted_by=granter_id,
            expires_at=expires_at,
            is_active=True
        )
        
        self.db.add(user_access)
        await self.db.commit()
        await self.db.refresh(user_access)
        
        logger.info(f"Granted individual access '{access_name}' to user '{username}'")
        return user_access

    async def revoke_individual_access(self, username: str, access_name: str) -> bool:
        """Revoke individual access from a user"""
        user = await self.get_user_by_username(username)
        if not user:
            raise RBACError(f"User '{username}' not found")
        
        result = await self.db.execute(
            select(RBACUserAccess).where(
                and_(
                    RBACUserAccess.user_id == user.id,
                    RBACUserAccess.access_name == access_name,
                    RBACUserAccess.is_active == True
                )
            )
        )
        user_access = result.scalar_one_or_none()
        
        if not user_access:
            logger.warning(f"User '{username}' does not have access to '{access_name}'")
            return True
        
        user_access.is_active = False
        await self.db.commit()
        
        logger.info(f"Revoked individual access '{access_name}' from user '{username}'")
        return True

    async def get_user_individual_access(self, username: str) -> List[Dict[str, str]]:
        """Get all individual access for a user"""
        user = await self.get_user_by_username(username)
        if not user:
            return []
        
        result = await self.db.execute(
            select(RBACUserAccess).where(
                and_(
                    RBACUserAccess.user_id == user.id,
                    RBACUserAccess.is_active == True
                )
            )
        )
        access_list = result.scalars().all()
        
        return [
            {
                "access_name": access.access_name,
                "access_type": access.access_type,
                "description": access.description,
                "expires_at": access.expires_at.isoformat() if access.expires_at else None
            }
            for access in access_list
        ]

    async def check_user_individual_access(self, username: str, access_name: str) -> bool:
        """Check if a user has specific individual access"""
        user = await self.get_user_by_username(username)
        if not user:
            return False
        
        result = await self.db.execute(
            select(RBACUserAccess).where(
                and_(
                    RBACUserAccess.user_id == user.id,
                    RBACUserAccess.access_name == access_name,
                    RBACUserAccess.is_active == True
                )
            )
        )
        user_access = result.scalar_one_or_none()
        
        if not user_access:
            return False
        
        # Check if access has expired (use timezone-aware datetime)
        if user_access.expires_at and user_access.expires_at < datetime.now(timezone.utc):
            return False
        
        return True

    # get_user_complete_access and check_user_complete_access removed - role-based functions

    # Statistics and reporting
    async def get_rbac_stats(self) -> Dict[str, Any]:
        """Aggregate high-level statistics about RBAC entities (users and agents only).
        
        Users are counted from rbac_user_pivot table, which is the source of truth
        for users in the AD group-based access control system.
        Active users are determined by joining with the main User table.
        """
        # Count total users from rbac_user_pivot (source of truth for AD group-based access)
        total_users_result = await self.db.execute(
            select(func.count()).select_from(RBACUserPivot)
        )
        
        # Count active users by joining rbac_user_pivot with User table
        # user_name in pivot table matches email or username in User table
        active_users_result = await self.db.execute(
            select(func.count())
            .select_from(RBACUserPivot)
            .join(User, or_(
                User.email == RBACUserPivot.email,
                User.username == RBACUserPivot.email
            ))
            .where(User.is_active == True)
        )
        
        # Count agents from agents table (not rbac_agents)
        total_services_result = await self.db.execute(select(func.count()).select_from(Agent))
        active_services_result = await self.db.execute(
            select(func.count()).select_from(Agent).where(Agent.is_enabled == True)
        )

        return {
            "total_users": total_users_result.scalar() or 0,
            "total_agents": total_services_result.scalar() or 0,
            "active_users": active_users_result.scalar() or 0,
            "active_agents": active_services_result.scalar() or 0,
        }

    # Initialize default roles - REMOVED (roles no longer used)
    # Initialize default services - REMOVED (agents are created via /api/v1/admin/agent)

    # Missing methods that API endpoints need
    # Duplicate methods removed - see lines 120-157 for delete_role() and create_user()

    async def get_user_by_username(self, username: str) -> Optional[RBACUser]:
        """Get user by username"""
        result = await self.db.execute(
            select(RBACUser).where(RBACUser.username == username)
        )
        return result.scalar_one_or_none()

    # get_user_roles, assign_role_to_user, remove_role_from_user - REMOVED (role-based functions)

    # Duplicate create_service removed - see lines 83-107 for the main implementation

    async def get_all_services(self, active_status: str = "active", name: Optional[str] = None) -> List[Agent]:
        """Get all agents from the agents table, filtered by enabled status and name.
        
        This method queries the agents table directly (not rbac_agents).
        The RBAC system now uses the agents table as the source of truth.
        
        Args:
            active_status: Filter by status - "active" (is_enabled=True), "inactive" (is_enabled=False), or "all" (no filter)
            name: Optional name filter (case-insensitive partial match)
            
        Returns:
            List of Agent objects from the agents table
        """
        query = select(Agent)
        
        # Filter by is_enabled column based on active_status parameter
        if active_status == "active":
            query = query.where(Agent.is_enabled == True)
        elif active_status == "inactive":
            query = query.where(Agent.is_enabled == False)
        # else: active_status == "all", no filter applied
        
        if name:
            query = query.where(Agent.name.ilike(f"%{name}%"))
        
        result = await self.db.execute(query.order_by(Agent.name))
        return result.scalars().all()

    # get_service_types method removed - service_type field no longer exists
    # assign_services_to_role - REMOVED (role-based function)
    # Azure AD Group Role Mapping Methods - REMOVED (all role mapping functions removed)
    # Role Inheritance Methods - REMOVED (all role inheritance functions removed)
    # get_user_effective_services - REMOVED (role-based function)
    # All role-related function bodies removed - using AD group-based access control instead