"""Admin agent management schemas."""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class CustomFeatureToggleField(BaseModel):
    """Custom feature toggle field schema."""

    field_name: str
    is_default: bool = False
    field_icon: Optional[str] = None


class CustomFeatureDropdownOption(BaseModel):
    """Custom feature dropdown option schema."""

    title_name: str
    value: str
    is_default: bool = False
    option_icon: Optional[str] = None


class CustomFeatureDropdownField(BaseModel):
    """Custom feature dropdown field schema."""

    field_name: str
    field_icon: Optional[str] = None
    options: List[CustomFeatureDropdownOption]


class CustomFeatureToggle(BaseModel):
    """Custom feature toggle configuration."""

    enabled: bool = False
    fields: List[CustomFeatureToggleField] = []


class CustomFeatureDropdown(BaseModel):
    """Custom feature dropdown configuration."""

    enabled: bool = False
    fields: List[CustomFeatureDropdownField] = []


class CustomFeatureTextField(BaseModel):
    """Custom feature text field schema."""

    field_name: str
    field_value: str


class CustomFeatureText(BaseModel):
    """Custom feature text configuration."""

    enabled: bool = False
    fields: List[CustomFeatureTextField] = []


class CustomHeaderToggle(BaseModel):
    """Custom header toggle configuration."""

    enabled: bool = False
    value: Optional[str] = None  # JSON string for headers


class AgentCreateUpdateRequest(BaseModel):
    """Request schema for creating/updating agent configuration.
    
    Supports both JSON body (for update and create) and attachment-based icon uploads.
    For icon uploads, use attachment_id fields (upload files first via /api/attachments/upload).
    """

    agent_name: str = Field(..., description="Agent name")
    agent_intro: Optional[str] = Field(None, description="Agent introduction")
    agent_icon: Optional[str] = Field(
        None, description="Blob URL, existing icon URL, or attachment ID (UUID) from /api/attachments/upload. If UUID format, will be resolved to blob URL."
    )
    agent_icon_attachment_id: Optional[str] = Field(
        None, description="[DEPRECATED] Attachment ID from /api/attachments/upload. Use agent_icon field instead."
    )
    mcp_server_link: Optional[str] = Field(None, description="MCP server URL")
    agent_health_url: Optional[str] = Field(None, description="Health check URL")
    categories: Optional[List[str]] = Field(
        None, description="List of category names"
    )
    agent_type: Optional[str] = Field(
        None, description="Agent type (e.g., 'Enterprise Agent', 'Knowledge Agent', 'Creative Agent'). If not provided, defaults to 'Enterprise Agent'."
    )
    agent_enabled: bool = Field(True, description="Whether agent is enabled")
    description: Optional[str] = Field(None, description="Agent description")
    instruction: Optional[str] = Field(None, max_length=20000, description="Agent instruction prompt (max 20,000 characters)")
    custom_feature_toggle: Optional[CustomFeatureToggle] = None
    custom_feature_dropdown: Optional[CustomFeatureDropdown] = None
    custom_feature_text: Optional[CustomFeatureText] = None
    custom_header_toggle: Optional[CustomHeaderToggle] = Field(
        None, description="Custom header toggle configuration. If enabled, value will be mapped to agent_header field."
    )
    tools: Optional[List[str]] = Field(
        None, description="List of tool names (e.g., ['_forecastApi', '_historyApi']). Tools will be created/updated in agent_tools table."
    )
    include_in_teams: bool = Field(False, description="Include agent in teams")
    agent_header: Optional[str] = Field(
        None, 
        description="Agent HTTP headers as JSON string. Example: {\"Authorization\": \"Bearer {mcp_token}\", \"Content-Type\": \"application/json\", \"Accept\": \"application/json,text/event-stream\"}. Can also be provided via custom_header_toggle.value."
    )
    agent_capabilities: Optional[str] = Field(
        None, max_length=5000, description="Routing instruction (max 5,000 characters)"
    )
    add_history_to_context: bool = Field(
        False, description="Include history in context (Boolean)"
    )
    agent_metadata: Optional[Dict[str, Any]] = Field(
        None, 
        description="Agent metadata (JSON). WARNING: Do NOT store sensitive data (API keys, passwords, tokens, PII, connection strings). For structured configuration only. Max 100KB when serialized."
    )
    
    @field_validator('agent_metadata')
    @classmethod
    def validate_metadata_size(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Validate agent_metadata size does not exceed 100KB."""
        if v is not None:
            # Serialize to JSON to check actual size
            json_str = json.dumps(v)
            size_bytes = len(json_str.encode('utf-8'))
            max_size_bytes = 100 * 1024  # 100KB
            
            if size_bytes > max_size_bytes:
                size_kb = size_bytes / 1024
                raise ValueError(
                    f"agent_metadata exceeds maximum size limit. "
                    f"Current size: {size_kb:.2f}KB, Maximum allowed: 100KB. "
                    f"Please reduce the metadata size or store large data elsewhere."
                )
        return v
    
    @field_validator('instruction')
    @classmethod
    def validate_instruction_length(cls, v: Optional[str]) -> Optional[str]:
        """Validate instruction length does not exceed 20,000 characters."""
        if v is not None and len(v) > 20000:
            raise ValueError(
                f"instruction exceeds maximum length. "
                f"Current length: {len(v)} characters, Maximum allowed: 20,000 characters."
            )
        return v
    
    @field_validator('agent_capabilities')
    @classmethod
    def validate_capabilities_length(cls, v: Optional[str]) -> Optional[str]:
        """Validate agent_capabilities length does not exceed 5,000 characters."""
        if v is not None and len(v) > 5000:
            raise ValueError(
                f"agent_capabilities exceeds maximum length. "
                f"Current length: {len(v)} characters, Maximum allowed: 5,000 characters."
            )
        return v
    
    # [DEPRECATED] Attachment IDs for custom feature icons - use field_icon and option_icon fields directly instead
    toggle_field_icon_ids: Optional[List[str]] = Field(
        None, description="[DEPRECATED] List of attachment IDs for toggle field icons. Use field_icon in fields array instead."
    )
    dropdown_field_icon_ids: Optional[List[str]] = Field(
        None, description="[DEPRECATED] List of attachment IDs for dropdown field icons. Use field_icon in fields array instead."
    )
    dropdown_option_icon_ids: Optional[List[str]] = Field(
        None, description="[DEPRECATED] List of attachment IDs for dropdown option icons. Use option_icon in options array instead."
    )


class CustomFeatureToggleFieldResponse(CustomFeatureToggleField):
    """Custom feature toggle field response with ID."""

    field_id: Optional[UUID] = None
    field_icon_attachment: Optional[Dict[str, Any]] = Field(
        None, description="Full attachment data when field_icon is a UUID (includes attachment_id, file_name, blob_url, etc.)"
    )


class CustomFeatureDropdownOptionResponse(CustomFeatureDropdownOption):
    """Custom feature dropdown option response with ID."""

    option_id: Optional[UUID] = None
    option_icon_attachment: Optional[Dict[str, Any]] = Field(
        None, description="Full attachment data when option_icon is a UUID (includes attachment_id, file_name, blob_url, etc.)"
    )


class CustomFeatureDropdownFieldResponse(BaseModel):
    """Custom feature dropdown field response with ID."""

    field_id: Optional[UUID] = None
    field_name: str
    field_icon: Optional[str] = None
    field_icon_attachment: Optional[Dict[str, Any]] = Field(
        None, description="Full attachment data when field_icon is a UUID (includes attachment_id, file_name, blob_url, etc.)"
    )
    options: List[CustomFeatureDropdownOptionResponse]


class CustomFeatureToggleResponse(BaseModel):
    """Custom feature toggle configuration response."""

    enabled: bool = False
    fields: List[CustomFeatureToggleFieldResponse] = []


class CustomFeatureDropdownResponse(BaseModel):
    """Custom feature dropdown configuration response."""

    enabled: bool = False
    fields: List[CustomFeatureDropdownFieldResponse] = []


class CustomFeatureTextFieldResponse(CustomFeatureTextField):
    """Custom feature text field response with ID."""

    field_id: Optional[UUID] = None


class CustomFeatureTextResponse(BaseModel):
    """Custom feature text configuration response."""

    enabled: bool = False
    fields: List[CustomFeatureTextFieldResponse] = []


class AgentToolResponse(BaseModel):
    """Agent tool response schema."""
    tool_id: str
    tool_name: str
    tool_description: Optional[str] = None
    tool_url: Optional[str] = None
    tool_icon: Optional[str] = None
    tool_color: Optional[str] = None
    tool_order: int = 0
    tool_is_active: bool = True

    class Config:
        from_attributes = True


class AgentResponse(BaseModel):
    """Agent configuration response schema."""

    success: bool = True
    agent_id: str
    agent_name: str
    agent_intro: Optional[str] = None
    agent_icon: Optional[str] = None
    agent_icon_attachment: Optional[Dict[str, Any]] = Field(
        None, description="Full attachment data when agent_icon is a UUID (includes attachment_id, file_name, blob_url, etc.)"
    )
    mcp_server_link: Optional[str] = None
    agent_health_url: Optional[str] = None
    categories: List[str] = []
    agent_enabled: bool = True
    description: Optional[str] = None
    instruction: Optional[str] = None
    custom_feature_toggle: Optional[CustomFeatureToggleResponse] = None
    custom_feature_dropdown: Optional[CustomFeatureDropdownResponse] = None
    custom_feature_text: Optional[CustomFeatureTextResponse] = None
    custom_header_toggle: Optional[CustomHeaderToggle] = Field(
        None, description="Custom header toggle configuration reconstructed from agent_header"
    )
    tools: List[AgentToolResponse] = []  # Agent tools
    include_in_teams: bool = False
    agent_header: Optional[str] = None
    agent_capabilities: Optional[str] = None
    add_history_to_context: bool = False
    agent_metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    mcp_add_agent_response: Optional[Dict[str, Any]] = Field(
        None, description="Response from internal MCP add-agent API call"
    )


class AgentListItemResponse(BaseModel):
    """Agent list item response schema."""

    agent_id: str
    agent_name: str
    agent_intro: Optional[str] = None
    agent_icon: Optional[str] = None
    agent_icon_attachment: Optional[Dict[str, Any]] = Field(
        None, description="Full attachment data when agent_icon is a UUID (includes attachment_id, file_name, blob_url, etc.)"
    )
    mcp_server_link: Optional[str] = None
    agent_health_url: Optional[str] = None
    categories: List[str] = []
    agent_enabled: bool = True
    status: str = Field(default="active", description="Agent status: 'active' or 'inactive' based on agent_enabled")
    description: Optional[str] = None
    instruction: Optional[str] = None
    custom_feature_toggle: Optional[CustomFeatureToggleResponse] = None
    custom_feature_dropdown: Optional[CustomFeatureDropdownResponse] = None
    custom_feature_text: Optional[CustomFeatureTextResponse] = None
    custom_header_toggle: Optional[CustomHeaderToggle] = Field(
        None, description="Custom header toggle configuration reconstructed from agent_header"
    )
    tools: List[AgentToolResponse] = []  # Agent tools
    include_in_teams: bool = False
    agent_header: Optional[str] = None
    agent_capabilities: Optional[str] = None
    add_history_to_context: bool = False
    agent_metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_used: Optional[datetime] = None


class AgentListResponse(BaseModel):
    """Agent list response schema."""

    success: bool = True
    agents: List[AgentListItemResponse] = []
    total_count: int = 0
    has_more: bool = False


class AgentHealthResponse(BaseModel):
    """Agent health check response schema."""

    success: bool = True
    agent_id: str
    health_status: str = Field(
        ..., description="healthy, degraded, unhealthy, unreachable"
    )
    mcp_server_status: Optional[str] = None
    response_time_ms: Optional[int] = None
    last_checked: Optional[datetime] = None
    details: Optional[Dict[str, Any]] = None


class AgentCategoryInfo(BaseModel):
    """Agent category information."""

    name: str
    agent_count: int = 0
    enabled_count: int = 0
    agents: List[Dict[str, Any]] = []


class AgentCategoriesResponse(BaseModel):
    """Agent categories response schema."""

    success: bool = True
    categories: List[AgentCategoryInfo] = []
    total_categories: int = 0
    total_agents: int = 0


class AgentDeleteResponse(BaseModel):
    """Agent delete response schema."""

    success: bool = True
    message: str


class UserAvailableAgentResponse(BaseModel):
    """User-facing available agent response schema."""

    agent_id: str
    legacy_agent_id: Optional[str] = Field(None, description="Legacy agent_id field for user agents")
    agent_name: str
    agent_intro: Optional[str] = None
    agent_icon: Optional[str] = None
    agent_icon_attachment: Optional[Dict[str, Any]] = Field(
        None, description="Full attachment data when agent_icon is a UUID (includes attachment_id, file_name, blob_url, etc.)"
    )
    categories: List[str] = []
    custom_feature_toggle: Optional[CustomFeatureToggleResponse] = None
    custom_feature_dropdown: Optional[CustomFeatureDropdownResponse] = None
    custom_feature_text: Optional[CustomFeatureTextResponse] = None
    is_pinned: bool = False
    lastUsed: Optional[datetime] = Field(None, description="Last time the user used this agent")


class UserAvailableAgentsResponse(BaseModel):
    """User-facing available agents response schema."""

    success: bool = True
    agents: List[UserAvailableAgentResponse] = []
    total_count: int = 0
    has_more: bool = False
    user_permissions: Optional[Dict[str, Any]] = None


class MCPValidationRequest(BaseModel):
    """MCP validation request schema."""

    name: str = Field(..., description="Agent name")
    intro: Optional[str] = Field(None, description="Agent introduction")
    description: Optional[str] = Field(None, description="Agent description")
    instruction: Optional[str] = Field(None, description="Agent instruction")
    instructions: Optional[str] = Field(None, description="Agent instructions")
    mcp_url: str = Field(..., description="MCP server URL")
    transport: str = Field(..., description="Transport type (e.g., 'streamable-http')")
    headers: Optional[Dict[str, str]] = Field(None, description="Headers to pass to MCP server (e.g., Authorization)")


class MCPValidationResponse(BaseModel):
    """MCP validation response schema."""

    status: str
    message: str
    agent_name: str
    mcp_url: str
    transport: str
    valid: bool
    tool_count: int
    tool_names: List[str]
    access_token: Optional[str] = Field(None, description="Azure AD access token used for MCP validation")


class AgentAnalyticsItem(BaseModel):
    """Agent analytics item schema for utilisation status heatmap."""

    agent_id: str = Field(..., description="Agent ID")
    agent_public_id: str = Field(..., description="Agent public ID (UUID)")
    agent_name: str = Field(..., description="Agent name")
    agent_icon: Optional[str] = Field(None, description="Agent icon URL or UUID")
    status: str = Field(..., description="Agent status: 'enabled' or 'disabled'")
    type: str = Field(..., description="Agent type: 'user' or 'enterprise'")
    usage: int = Field(..., description="Total usage count")
    usage_formatted: str = Field(..., description="Usage formatted as string (e.g., '751K')")
    last_used: Optional[datetime] = Field(None, description="Last time the agent was used")

    class Config:
        from_attributes = True


class AgentAnalyticsResponse(BaseModel):
    """Agent analytics response schema."""

    success: bool = True
    agents: List[AgentAnalyticsItem] = Field(..., description="List of agents with analytics")
    total: int = Field(..., description="Total number of agents (before pagination)")
    page: int = Field(..., description="Current page number (1-indexed)")
    limit: int = Field(..., description="Number of records per page")
    total_pages: int = Field(..., description="Total number of pages")
    total_usage: int = Field(..., description="Total usage across all agents")
    max_usage: Optional[int] = Field(None, description="Maximum usage count among all agents")
    min_usage: Optional[int] = Field(None, description="Minimum usage count among all agents")
    max_usage_formatted: Optional[str] = Field(None, description="Maximum usage count formatted (e.g., '22K')")
    min_usage_formatted: Optional[str] = Field(None, description="Minimum usage count formatted (e.g., '3K')")
    date_from: Optional[datetime] = Field(None, description="Start date filter applied")
    date_to: Optional[datetime] = Field(None, description="End date filter applied")


class AgentMonitoringItem(BaseModel):
    """Agent monitoring item schema for status and health."""

    agent_id: str = Field(..., description="Agent ID")
    agent_name: str = Field(..., description="Agent name")
    agent_icon: Optional[str] = Field(None, description="Agent icon URL or UUID")
    health_status: str = Field(..., description="Agent health status: 'healthy' or 'unhealthy'")
    activity_status: str = Field(..., description="Agent activity status: 'active' or 'inactive'")
    last_active: Optional[datetime] = Field(None, description="Last active timestamp")
    last_health_check: Optional[datetime] = Field(None, description="Last health check timestamp")

    class Config:
        from_attributes = True


class AgentMonitoringResponse(BaseModel):
    """Agent monitoring response schema."""

    success: bool = True
    agents: List[AgentMonitoringItem] = Field(..., description="List of agents with monitoring data")
    total_agents: int = Field(..., description="Total number of agents")
    healthy_count: int = Field(..., description="Number of healthy agents")
    unhealthy_count: int = Field(..., description="Number of unhealthy agents")
    active_count: int = Field(..., description="Number of active agents")
    inactive_count: int = Field(..., description="Number of inactive agents")

