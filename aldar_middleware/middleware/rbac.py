"""
RBAC Middleware
Middleware for role-based access control and permission checking
"""

from typing import List, Optional, Dict, Any
from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from aldar_middleware.database.base import get_db
from aldar_middleware.services.rbac_service import RBACServiceLayer
from aldar_middleware.exceptions import RBACError, PermissionDeniedError
import logging
import jwt
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Security scheme
security = HTTPBearer()


class RBACMiddleware:
    """RBAC middleware for permission checking"""
    
    def __init__(self, secret_key: str, algorithm: str = "HS256"):
        self.secret_key = secret_key
        self.algorithm = algorithm
    
    def get_current_user(self, credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
        """Extract username from JWT token"""
        try:
            token = credentials.credentials
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            username = payload.get("sub")
            if not username:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token: missing username"
                )
            return username
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired"
            )
        except jwt.InvalidTokenError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )
    
    def check_permission(self, resource: str, action: str, required_level: int = 0):
        """Decorator for permission checking"""
        def decorator(func):
            async def wrapper(*args, **kwargs):
                # Extract username and db from function arguments
                username = None
                db = None
                
                for arg in args:
                    if isinstance(arg, str) and len(arg) > 0:
                        username = arg
                    elif isinstance(arg, AsyncSession):  # AsyncSession
                        db = arg
                
                for key, value in kwargs.items():
                    if key == 'username' and isinstance(value, str):
                        username = value
                    elif key == 'db' and isinstance(value, AsyncSession):
                        db = value
                
                if not username or not db:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Could not extract username or database session"
                    )
                
                # Check permission
                rbac_service = RBACServiceLayer(db)
                has_permission = await rbac_service.check_user_permission(username, resource, action)
                
                if not has_permission:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Permission denied: {action} on {resource}"
                    )
                
                return await func(*args, **kwargs)
            return wrapper
        return decorator


class PermissionChecker:
    """Permission checker utility class"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.rbac_service = RBACServiceLayer(db)
    
    async def check_user_permission(self, username: str, resource: str, action: str) -> bool:
        """Check if user has permission"""
        return await self.rbac_service.check_user_permission(username, resource, action)
    
    async def get_user_roles(self, username: str) -> List[str]:
        """Get user's effective roles"""
        try:
            user_roles = await self.rbac_service.get_user_roles(username)
            # Extract role names from the response
            return [role.name for role in user_roles.effective_roles] if hasattr(user_roles, 'effective_roles') else []
        except RBACError:
            return []
    
    async def get_user_level(self, username: str) -> int:
        """Get user's highest role level (legacy - not used in non-hierarchical system)"""
        try:
            user_roles = await self.rbac_service.get_user_roles(username)
            return user_roles.highest_level if hasattr(user_roles, 'highest_level') else 0
        except RBACError:
            return 0
    
    async def require_level(self, username: str, required_level: int) -> bool:
        """Check if user has required role level (legacy - not used in non-hierarchical system)"""
        user_level = await self.get_user_level(username)
        return user_level >= required_level
    
    async def require_roles(self, username: str, required_roles: List[str]) -> bool:
        """Check if user has any of the required roles"""
        user_roles = await self.get_user_roles(username)
        return any(role in user_roles for role in required_roles)
    
    async def require_services(self, username: str, required_services: List[str]) -> bool:
        """Check if user has access to required services"""
        user_services = await self.rbac_service.get_user_services(username)
        return any(service in user_services for service in required_services)


async def get_permission_checker(db: AsyncSession = Depends(get_db)) -> PermissionChecker:
    """Dependency to get permission checker"""
    return PermissionChecker(db)


# ==============================================================================
# SECURITY NOTE: Permission Decorators Removed
# ==============================================================================
# The previous placeholder decorators (require_permission, require_role_level, 
# require_roles) have been REMOVED because they were non-functional and created
# a FALSE SENSE OF SECURITY.
#
# RECOMMENDED APPROACH (Already Implemented):
# Use FastAPI's dependency injection pattern with proper authorization checks:
#
# @router.post("/admin/endpoint")
# async def my_endpoint(
#     current_user: User = Depends(get_current_user),
#     db: AsyncSession = Depends(get_db)
# ):
#     # Check admin access
#     if not current_user.is_admin:
#         raise HTTPException(status_code=403, detail="Admin access required")
#     
#     # For fine-grained permissions, use PermissionChecker:
#     checker = PermissionChecker(db)
#     if not await checker.check_user_permission(current_user.username, "users", "read"):
#         raise HTTPException(status_code=403, detail="Permission denied")
#     
#     # Your endpoint logic here
#     pass
#
# This approach is:
# - More explicit and visible
# - Easier to test
# - Follows FastAPI best practices
# - Provides better error handling
# - Allows for audit logging at the check point
# ==============================================================================


# ==============================================================================
# Constants for RBAC System
# ==============================================================================

# Role level constants (for reference - not used in non-hierarchical system)
# These levels are optional numeric categorizations, NOT used for permission checks
class RoleLevels:
    """Legacy role levels - kept for backward compatibility but NOT used for permissions"""
    USER = 0
    BASIC_USER = 10
    STANDARD_USER = 20
    ADVANCED_USER = 30
    POWER_USER = 40
    MODERATOR = 50
    SUPERVISOR = 60
    MANAGER = 70
    ADMIN = 80
    SUPER_ADMIN = 90
    SUPERADMIN = 100


# Permission action constants
class Permissions:
    """Standard permission actions for RBAC"""
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    ADMIN = "admin"
    EXECUTE = "execute"
    MANAGE = "manage"


# Resource type constants
class Resources:
    """Standard resource types for RBAC"""
    USERS = "users"
    ROLES = "roles"
    SERVICES = "services"
    PERMISSIONS = "permissions"
    REPORTS = "reports"
    ANALYTICS = "analytics"
    SYSTEM = "system"
    CONFIGURATION = "configuration"
