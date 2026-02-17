"""
RBAC Pydantic Schemas
Data validation and serialization for RBAC system
"""

from typing import List, Optional, Dict, Any
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from enum import Enum


class RoleLevel(int, Enum):
    """Role level enumeration"""
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


class ServiceType(str, Enum):
    """Service type enumeration"""
    API = "api"
    DATABASE = "database"
    MESSAGE_QUEUE = "message_queue"
    MONITORING = "monitoring"
    FILE_STORAGE = "file_storage"
    NOTIFICATION = "notification"
    ANALYTICS = "analytics"
    REPORTING = "reporting"
    AGENT = "agent"


class PermissionAction(str, Enum):
    """Permission action enumeration"""
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    ADMIN = "admin"
    EXECUTE = "execute"
    MANAGE = "manage"


# Base schemas
class RBACBase(BaseModel):
    """Base schema for RBAC entities"""
    is_active: bool = True


# Role schemas
class RoleBase(RBACBase):
    """Base role schema"""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None


class RoleCreate(RoleBase):
    """Schema for creating a role with optional parent roles and services"""
    parent_role_ids: Optional[List[UUID]] = Field(default=None, description="UUIDs of parent roles to inherit from")
    service_names: Optional[List[str]] = Field(default=None, description="Names of services to assign to this role")


class RoleUpdate(BaseModel):
    """Schema for updating a role"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    is_active: Optional[bool] = None
    parent_role_ids: Optional[List[UUID]] = Field(None, description="UUIDs of parent roles. Pass empty list [] to clear all parent roles. If provided, replaces all existing parents.")
    service_names: Optional[List[str]] = Field(None, description="Names of services. Pass empty list [] to clear all services. If provided, replaces all existing services.")


class RoleResponse(RoleBase):
    """Schema for role response"""
    id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None
    services: List[str] = []
    parent_roles: List[str] = []  # List of parent role names

    @classmethod
    def model_validate(cls, obj, **kwargs):
        """Custom validation to convert service objects and parent roles to names"""
        # Handle both dict and ORM object inputs
        if isinstance(obj, dict):
            return super().model_validate(obj, **kwargs)
        
        # It's an ORM object - convert to dict with service names
        service_names = []
        if hasattr(obj, 'services') and obj.services is not None:
            for service in obj.services:
                if isinstance(service, str):
                    service_names.append(service)
                elif hasattr(service, 'name'):
                    # It's a RBACService object, extract the name
                    service_names.append(service.name)
        
        # Extract parent role names
        parent_role_names = []
        if hasattr(obj, 'parent_roles') and obj.parent_roles is not None:
            for parent_role in obj.parent_roles:
                if isinstance(parent_role, str):
                    parent_role_names.append(parent_role)
                elif hasattr(parent_role, 'name'):
                    # It's a RBACRole object, extract the name
                    parent_role_names.append(parent_role.name)
        
        # Create a dict representation with converted services and parent roles
        obj_dict = {
            'id': obj.id,
            'name': obj.name,
            'description': obj.description,
            'is_active': obj.is_active,
            'created_at': obj.created_at,
            'updated_at': obj.updated_at,
            'services': service_names,
            'parent_roles': parent_role_names
        }
        return super().model_validate(obj_dict, **kwargs)

    class Config:
        from_attributes = True


# User schemas
class UserBase(RBACBase):
    """Base user schema"""
    username: str = Field(..., min_length=3, max_length=100)
    email: EmailStr
    full_name: Optional[str] = Field(None, max_length=200)


class UserCreate(UserBase):
    """Schema for creating a user"""
    pass


class UserUpdate(BaseModel):
    """Schema for updating a user"""
    username: Optional[str] = Field(None, min_length=3, max_length=100)
    email: Optional[EmailStr] = None
    full_name: Optional[str] = Field(None, max_length=200)
    is_active: Optional[bool] = None


class UserResponse(UserBase):
    """Schema for user response"""
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Agent schemas (formerly Service)
class ServiceBase(RBACBase):
    """Base agent schema"""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None


class ServiceCreate(ServiceBase):
    """Schema for creating an agent"""
    pass


class ServiceUpdate(BaseModel):
    """Schema for updating an agent (name, description, and is_active)"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class ServiceResponse(ServiceBase):
    """Schema for service response.
    
    Works with both RBACAgent (rbac_agents table) and Agent (agents table).
    For Agent model, uses public_id as id and is_enabled as is_active.
    """
    id: UUID
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def model_validate(cls, obj, **kwargs):
        """Custom validation to handle both Agent and RBACAgent models."""
        # Handle dict input
        if isinstance(obj, dict):
            return super().model_validate(obj, **kwargs)
        
        # Handle Agent model from agents table
        if hasattr(obj, 'public_id') and hasattr(obj, 'is_enabled'):
            # It's an Agent model
            obj_dict = {
                'id': obj.public_id,  # Use public_id as UUID
                'name': obj.name,
                'description': obj.description,
                'is_active': obj.is_enabled,  # Map is_enabled to is_active
                'created_at': None,  # Agent model doesn't have created_at
                'updated_at': None,  # Agent model doesn't have updated_at
            }
            return super().model_validate(obj_dict, **kwargs)
        
        # Handle RBACAgent model (for backward compatibility)
        if hasattr(obj, 'id') and hasattr(obj, 'is_active'):
            obj_dict = {
                'id': obj.id,
                'name': obj.name,
                'description': obj.description,
                'is_active': obj.is_active,
                'created_at': obj.created_at if hasattr(obj, 'created_at') else None,
                'updated_at': obj.updated_at if hasattr(obj, 'updated_at') else None,
            }
            return super().model_validate(obj_dict, **kwargs)
        
        # Fallback to default validation
        return super().model_validate(obj, **kwargs)

    class Config:
        from_attributes = True


# Permission schemas
class PermissionBase(RBACBase):
    """Base permission schema"""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    resource: str = Field(..., min_length=1, max_length=100)
    action: PermissionAction


class PermissionCreate(PermissionBase):
    """Schema for creating a permission"""
    pass


class PermissionResponse(PermissionBase):
    """Schema for permission response"""
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Complex response schemas
class UserRoleResponse(BaseModel):
    """Schema for user role information"""
    username: str
    specific_roles: List[str] = []  # Roles directly assigned to the user
    inherited_roles: List[str] = []  # Roles inherited through role groups
    all_roles: List[str] = []  # All roles (specific + inherited)
    services: List[str] = []


class RoleServiceAssignment(BaseModel):
    """Schema for role-service assignment"""
    role_name: str
    service_names: List[str]


class UserPermissionCheck(BaseModel):
    """Schema for permission check request"""
    username: str
    resource: str
    action: str


class PermissionCheckResponse(BaseModel):
    """Schema for permission check response"""
    username: str
    resource: str
    action: str
    has_permission: bool
    granted_by_roles: List[str] = []


class RoleHierarchyResponse(BaseModel):
    """Schema for role hierarchy information"""
    name: str
    description: str
    inherits_from: List[str] = []
    permissions: List[str] = []


class ServiceAssignmentResponse(BaseModel):
    """Schema for service assignment information"""
    service_name: str
    service_type: str
    assigned_roles: List[str] = []
    required_level: int


# API request/response schemas
class AssignRoleRequest(BaseModel):
    """Schema for assigning role to user"""
    username: str
    role_name: str


class RemoveRoleRequest(BaseModel):
    """Schema for removing role from user"""
    username: str
    role_name: str


class AssignServiceRequest(BaseModel):
    """Schema for assigning services to role"""
    role_name: str
    service_names: List[str]


class UserServicesResponse(BaseModel):
    """Schema for user services response"""
    username: str
    services: List[ServiceResponse] = []
    total_services: int = 0


class RoleServicesResponse(BaseModel):
    """Schema for role services response"""
    role_name: str
    services: List[ServiceResponse] = []
    total_services: int = 0


# Bulk operation schemas
class BulkRoleAssignment(BaseModel):
    """Schema for bulk role assignment"""
    username: str
    role_names: List[str]


class BulkServiceAssignment(BaseModel):
    """Schema for bulk service assignment"""
    role_name: str
    service_names: List[str]


# Statistics and reporting schemas
class RBACStatsResponse(BaseModel):
    """Schema for RBAC statistics (users and agents only)"""
    total_users: int
    total_agents: int
    active_users: int
    active_agents: int


class UserPivotResponse(BaseModel):
    """Schema for user pivot table entry"""
    id: UUID
    email: str
    azure_ad_groups: List[str]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class UserPivotListResponse(BaseModel):
    """Schema for list of user pivot entries"""
    users: List[UserPivotResponse]
    total_users: int


class RBACResponse(BaseModel):
    """Standard RBAC API response wrapper"""

    success: bool = True
    message: Optional[str] = None
    data: Optional[Any] = None
    correlation_id: str


class UserActivityResponse(BaseModel):
    """Schema for user activity tracking"""
    username: str
    last_login: Optional[datetime] = None
    total_sessions: int = 0
    active_sessions: int = 0
    role_changes: int = 0
    last_role_change: Optional[datetime] = None


# Search and filter schemas
class RoleFilter(BaseModel):
    """Schema for role filtering"""
    is_active: Optional[bool] = None
    service_type: Optional[ServiceType] = None


class UserFilter(BaseModel):
    """Schema for user filtering"""
    is_active: Optional[bool] = None
    has_service: Optional[str] = None


class ServiceFilter(BaseModel):
    """Schema for service filtering"""
    service_type: Optional[ServiceType] = None
    is_active: Optional[bool] = None
    assigned_to_role: Optional[str] = None


# Azure AD Group Role Mapping Schemas
class AzureADGroupRoleMappingCreate(BaseModel):
    """Schema for creating Azure AD group to role mapping"""
    azure_ad_group_id: str
    azure_ad_group_name: Optional[str] = None
    role_id: UUID
    is_active: bool = True


class AzureADGroupRoleMappingUpdate(BaseModel):
    """Schema for updating Azure AD group to role mapping"""
    azure_ad_group_name: Optional[str] = None
    role_id: Optional[UUID] = None
    is_active: Optional[bool] = None


class AzureADGroupRoleMappingResponse(BaseModel):
    """Schema for Azure AD group to role mapping response"""
    id: int
    azure_ad_group_id: str
    azure_ad_group_name: Optional[str] = None
    role_id: UUID
    role_name: Optional[str] = None  # Populated from role relationship
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None
    
    class Config:
        from_attributes = True


class AutoAssignRolesRequest(BaseModel):
    """Schema for auto-assigning roles based on Azure AD groups"""
    username: str
    azure_ad_groups: List[str]  # List of Azure AD group IDs


class AutoAssignRolesResponse(BaseModel):
    """Schema for auto-assign roles response"""
    username: str
    assigned_roles: List[str]  # List of role names that were assigned
    skipped_roles: List[str]  # List of roles that were already assigned
    unmapped_groups: List[str]  # List of Azure AD groups with no role mapping


# Role Inheritance Schemas
class RoleInheritanceRequest(BaseModel):
    """Schema for assigning parent role to a child role"""
    child_role_id: UUID
    parent_role_id: UUID


class UserEffectiveServicesResponse(BaseModel):
    """Schema for user's effective services response"""
    username: str
    services: List[str]
    service_count: int
    role_breakdown: Dict[str, List[str]]
    individual_access: List[Dict[str, Any]]


class RoleWithServicesDetail(BaseModel):
    """Schema for role with all its services (direct + inherited)"""
    role_name: str
    direct_services: List[str]
    inherited_services: List[str]
    all_services: List[str]
    parent_roles: List[str]


class UserCompleteAccessDetail(BaseModel):
    """Schema for a single user's complete access information"""
    username: str
    email: str
    full_name: Optional[str]
    is_active: bool
    roles: List[RoleWithServicesDetail]
    individual_services: List[str]  # Services granted outside of roles
    total_unique_services: int
    created_at: datetime


class AllUsersAccessResponse(BaseModel):
    """Schema for all users' access information"""
    users: List[UserCompleteAccessDetail]
    total_users: int
    summary: Dict[str, Any]  # Summary statistics
