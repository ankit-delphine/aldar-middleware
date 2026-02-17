"""Menu and navigation related schemas."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from aldar_middleware.schemas.admin_agents import (
    CustomFeatureDropdownResponse,
    CustomFeatureTextResponse,
    CustomFeatureToggleResponse,
)


class MenuResponse(BaseModel):
    """Menu response schema."""
    
    id: UUID
    name: str
    display_name: str
    icon: Optional[str] = None
    route: Optional[str] = None
    order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LaunchpadAppResponse(BaseModel):
    """Launchpad app response schema."""
    
    id: str = Field(..., description="App ID for frontend")
    title: str
    subtitle: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    logoSrc: Optional[str] = None
    category: str
    url: Optional[str] = None
    isPinned: bool = False

    class Config:
        from_attributes = True


class AgentResponse(BaseModel):
    """Agent response schema."""
    
    id: str = Field(..., description="Agent ID for frontend (public_id)")
    agent_id: Optional[str] = Field(None, description="Legacy agent_id field for user agents")
    title: str
    subtitle: Optional[str] = None
    description: Optional[str] = None
    agent_intro: Optional[str] = Field(None, description="Agent introduction")
    tags: Optional[List[str]] = None
    logoSrc: Optional[str] = None
    logoAttachment: Optional[Dict[str, Any]] = Field(
        None, description="Full attachment data when logoSrc is a UUID (includes attachment_id, file_name, blob_url, etc.)"
    )
    category: str
    status: str = "active"
    methods: Optional[List[str]] = None
    lastUsed: Optional[datetime] = None
    usageCount: int = Field(0, description="Number of times the user has used this agent (session count)")
    isPinned: bool = False
    hasPermission: bool = Field(
        False, description="Whether the user has permission to access this agent"
    )
    custom_feature_toggle: Optional[CustomFeatureToggleResponse] = Field(
        None, description="Custom feature toggle configuration",
    )
    custom_feature_dropdown: Optional[CustomFeatureDropdownResponse] = Field(
        None, description="Custom feature dropdown configuration",
    )
    custom_feature_text: Optional[CustomFeatureTextResponse] = Field(
        None, description="Custom feature text configuration",
    )

    class Config:
        from_attributes = True


class UserPinRequest(BaseModel):
    """User pin request schema."""
    
    is_pinned: bool = Field(..., description="Whether to pin or unpin the item")


class UserPinResponse(BaseModel):
    """User pin response schema."""
    
    id: UUID
    user_id: UUID
    item_id: UUID
    is_pinned: bool
    order: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MenuListResponse(BaseModel):
    """Menu list response schema."""
    
    menus: List[MenuResponse]


class LaunchpadAppsResponse(BaseModel):
    """Launchpad apps response schema."""
    
    apps: List[LaunchpadAppResponse]
    total: int
    category: str


class AgentsResponse(BaseModel):
    """Agents response schema."""
    
    agents: List[AgentResponse]
    total: int
    category: str
