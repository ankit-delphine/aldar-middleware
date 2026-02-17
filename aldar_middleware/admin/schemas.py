"""Admin schemas for request/response models."""

from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, Field, field_validator


class GroupResponse(BaseModel):
    """Group response schema."""
    id: UUID
    name: str
    description: Optional[str] = None
    azure_ad_group_id: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserAgentInfo(BaseModel):
    """User agent information schema."""
    agent_id: str
    agent_name: str
    access_level: str
    is_active: bool
    thumbnail: Optional[str] = None  # Agent thumbnail/icon URL

    class Config:
        from_attributes = True


class UserResponse(BaseModel):
    """User response schema."""
    id: UUID
    email: str
    username: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: Optional[str] = None
    is_active: bool
    is_verified: bool
    is_admin: bool
    azure_ad_id: Optional[str] = None
    azure_display_name: Optional[str] = None
    azure_upn: Optional[str] = None
    department: Optional[str] = None  # azure_department
    job_title: Optional[str] = None  # azure_job_title
    profile_photo: Optional[str] = None  # User profile photo URL
    external_id: Optional[str] = None
    company: Optional[str] = None
    is_onboarded: bool = False
    is_custom_query_enabled: bool = False
    # Custom query preferences stored in preferences JSON:
    # - customQueryAboutUser: Optional[str]
    # - customQueryPreferredFormatting: Optional[str]
    # - customQueryTopicsOfInterest: Optional[List[str]]
    created_at: datetime
    updated_at: datetime
    last_login: Optional[datetime] = None
    first_logged_in_at: Optional[datetime] = None
    groups: List[GroupResponse] = []
    agents: List[UserAgentInfo] = []  # Agents the user has access to

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    """User update schema."""
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None
    is_admin: Optional[bool] = None
    external_id: Optional[str] = None
    company: Optional[str] = None
    is_onboarded: Optional[bool] = None
    is_custom_query_enabled: Optional[bool] = None

    class Config:
        from_attributes = True



# RBAC Admin Schemas
class RoleGroupCreate(BaseModel):
    """Role group creation schema."""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None


class RoleGroupResponse(BaseModel):
    """Role group response schema."""
    id: int
    name: str
    description: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class AssignUserToRoleGroupRequest(BaseModel):
    """Assign user to role group request."""
    username: str
    role_group_name: str
    granted_by: Optional[str] = None


class RemoveUserFromRoleGroupRequest(BaseModel):
    """Remove user from role group request."""
    username: str
    role_group_name: str


class IndividualAccessGrantRequest(BaseModel):
    """Grant individual access request."""
    username: str
    access_name: str
    access_type: str  # e.g., "app", "feature", "service", "tool"
    description: Optional[str] = None
    granted_by: Optional[str] = None
    expires_at: Optional[datetime] = None


class IndividualAccessRevokeRequest(BaseModel):
    """Revoke individual access request."""
    username: str
    access_name: str


class AddRoleToGroupRequest(BaseModel):
    """Add role to group request."""
    group_name: str
    role_name: str


class RemoveRoleFromGroupRequest(BaseModel):
    """Remove role from group request."""
    group_name: str
    role_name: str


class UserAccessResponse(BaseModel):
    """User access response schema."""
    role_groups: List[str]
    individual_access: List[Dict[str, Any]]
    all_permissions: List[Dict[str, str]]


class UserAgentCreate(BaseModel):
    """User agent creation schema."""
    user_id: UUID
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    agent_type: str = Field(..., min_length=1, max_length=50)
    agent_config: Optional[Dict[str, Any]] = None


class UserAgentUpdate(BaseModel):
    """User agent update schema."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    agent_type: Optional[str] = Field(None, min_length=1, max_length=50)
    agent_config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class UserAgentResponse(BaseModel):
    """User agent response schema."""
    id: UUID
    user_id: UUID
    name: str
    description: Optional[str] = None
    agent_type: str
    agent_config: Optional[Dict[str, Any]] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserPermissionCreate(BaseModel):
    """User permission creation schema."""
    user_id: UUID
    agent_id: Optional[UUID] = None
    permission_type: str = Field(..., min_length=1, max_length=50)
    resource: Optional[str] = Field(None, max_length=100)
    is_granted: bool = True


class UserPermissionUpdate(BaseModel):
    """User permission update schema."""
    permission_type: Optional[str] = Field(None, min_length=1, max_length=50)
    resource: Optional[str] = Field(None, max_length=100)
    is_granted: Optional[bool] = None


class UserPermissionResponse(BaseModel):
    """User permission response schema."""
    id: UUID
    user_id: UUID
    agent_id: Optional[UUID] = None
    permission_type: str
    resource: Optional[str] = None
    is_granted: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LogQueryRequest(BaseModel):
    """Log query request schema."""
    correlation_id: Optional[str] = Field(None, description="Filter by correlation ID", max_length=50)
    user_id: Optional[str] = Field(None, description="Filter by user ID", max_length=50)
    username: Optional[str] = Field(None, description="Filter by username", max_length=100)
    user_type: Optional[str] = Field(None, description="Filter by user type", max_length=20)
    email: Optional[str] = Field(None, description="Filter by email", max_length=100)
    level: Optional[str] = Field(None, description="Filter by log level (DEBUG, INFO, WARNING, ERROR)", max_length=10)
    module: Optional[str] = Field(None, description="Filter by module name", max_length=100)
    function: Optional[str] = Field(None, description="Filter by function name", max_length=100)
    start_time: Optional[datetime] = Field(None, description="Start time for time range filter")
    end_time: Optional[datetime] = Field(None, description="End time for time range filter")
    search: Optional[str] = Field(None, description="Search across user_id, username, email, message fields", max_length=200)
    limit: int = Field(100, ge=1, le=1000, description="Maximum number of logs to return")
    offset: int = Field(0, ge=0, description="Number of logs to skip")
    
    @field_validator('level')
    @classmethod
    def validate_level(cls, v):
        if v is not None and v.upper() not in ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']:
            raise ValueError('Level must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL')
        return v.upper() if v else v


class LogEntryResponse(BaseModel):
    """Log entry response schema."""
    id: str
    timestamp: datetime
    level: str
    correlation_id: Optional[str] = None
    user_id: Optional[str] = None
    username: Optional[str] = None
    user_type: Optional[str] = None
    email: Optional[str] = None
    user_full_name: Optional[str] = None
    user_profile_photo: Optional[str] = None
    user_department: Optional[str] = None
    user_job_title: Optional[str] = None
    user_company: Optional[str] = None
    user_external_id: Optional[str] = None
    user_azure_display_name: Optional[str] = None
    user_azure_upn: Optional[str] = None
    is_authenticated: bool
    agent_type: Optional[str] = None
    agent_name: Optional[str] = None
    agent_thumbnail: Optional[str] = None
    agent_method: Optional[str] = None
    agent_count: int
    module: str
    function: str
    line: int
    message: str
    process_id: str
    thread_id: int
    thread_name: str


class LogsResponse(BaseModel):
    """Logs response schema."""
    logs: List[LogEntryResponse]
    total_count: int
    has_more: bool
    query_params: LogQueryRequest


# User Logs 3.0 Schemas (matching Figma design)
class AgentInfo(BaseModel):
    """Agent information schema for user logs."""
    agentId: Optional[str] = None
    agentName: Optional[str] = None
    agentType: Optional[str] = None
    agentThumbnail: Optional[str] = None


class UserConversationCreatedPayload(BaseModel):
    """Event payload for USER_CONVERSATION_CREATED."""
    conversationId: Optional[str] = None
    selectedAgentType: Optional[str] = None
    customQueryAboutUser: Optional[str] = None
    customQueryTopicsOfInterest: Optional[str] = None
    customQueryPreferredFormatting: Optional[str] = None
    agent: Optional[AgentInfo] = None


class UserMessageCreatedPayload(BaseModel):
    """Event payload for USER_MESSAGE_CREATED."""
    messageId: Optional[str] = None
    conversationId: Optional[str] = None
    selectedAgentType: Optional[str] = None
    customQueryAboutUser: Optional[str] = None
    customQueryTopicsOfInterest: Optional[str] = None
    customQueryPreferredFormatting: Optional[str] = None
    agent: Optional[AgentInfo] = None
    metrics: Optional[Dict[str, Any]] = None


class UserLogEventResponse(BaseModel):
    """User log event response schema matching 3.0 structure."""
    id: str
    role: Optional[str] = None
    department: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    userEntraId: Optional[str] = None
    profile_photo: Optional[str] = None
    eventType: str
    eventPayload: Dict[str, Any]
    createdAt: datetime


class AdminLogEventResponse(BaseModel):
    """Admin log event response schema matching user logs structure."""
    id: str
    role: Optional[str] = None
    department: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    userEntraId: Optional[str] = None
    profile_photo: Optional[str] = None
    eventType: str  # action_type from admin_logs
    eventPayload: Dict[str, Any]  # body from log_data
    createdAt: datetime


# Azure AD Sync Schemas
class AzureADSyncRequest(BaseModel):
    """Request schema for Azure AD sync."""
    domain_filter: Optional[str] = Field(None, description="Domain filter (e.g., 'adq.ae')")
    sync_groups: bool = Field(False, description="Also sync Azure AD groups")
    max_users: int = Field(10000, ge=1, le=100000, description="Maximum users to sync")
    overwrite_existing: bool = Field(False, description="Update existing users")
    async_trigger: bool = Field(False, description="Run sync in background and return immediately")


class AzureADUser(BaseModel):
    """Azure AD user schema."""
    id: str = Field(..., description="Azure AD user ID")
    userPrincipalName: str = Field(..., description="User Principal Name (email)")
    mail: Optional[str] = Field(None, description="Mail address")
    displayName: Optional[str] = Field(None, description="Display name")
    givenName: Optional[str] = Field(None, description="First name")
    surname: Optional[str] = Field(None, description="Last name")
    accountEnabled: Optional[bool] = Field(None, description="Account enabled status")
    department: Optional[str] = None
    officeLocation: Optional[str] = None
    country: Optional[str] = None
    jobTitle: Optional[str] = None
    companyName: Optional[str] = None


class AzureADGroup(BaseModel):
    """Azure AD group schema."""
    id: str = Field(..., description="Azure AD group ID")
    displayName: str = Field(..., description="Group display name")
    description: Optional[str] = Field(None, description="Group description")
    mail: Optional[str] = Field(None, description="Group email")
    securityEnabled: bool = Field(..., description="Is security group")
    mailEnabled: bool = Field(..., description="Is mail enabled")


class AzureADSyncResult(BaseModel):
    """Result for a single user sync."""
    email: str
    azure_ad_id: str
    success: bool
    user_id: Optional[UUID] = None
    error: Optional[str] = None
    action: str = "created"  # "created", "updated", "existing", or "deactivated"


class AzureADSyncResponse(BaseModel):
    """Response for Azure AD sync."""
    total_fetched: int
    total_synced: int
    successful: int
    failed: int
    results: List[AzureADSyncResult]
    synced_groups: int = 0


class AzureADGroupsResponse(BaseModel):
    """Response for listing Azure AD groups."""
    total_groups: int
    groups: List[AzureADGroup]
