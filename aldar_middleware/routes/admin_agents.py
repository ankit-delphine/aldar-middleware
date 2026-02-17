"""Admin-only agent management API routes."""

import csv
import io
import json
import logging
import uuid
from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime, timezone
import httpx

from fastapi import APIRouter, Depends, HTTPException, Query, status, Body, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, delete, asc, desc, nullslast, text

from aldar_middleware.database.base import get_db
from aldar_middleware.models.user import User
from aldar_middleware.models.menu import Agent
from aldar_middleware.models.agent_tags import AgentTag
from aldar_middleware.models.agent_configuration import AgentConfiguration
from aldar_middleware.models.attachment import Attachment
from aldar_middleware.models.token_usage import TokenUsage
from aldar_middleware.models.sessions import Session
from aldar_middleware.auth.dependencies import get_current_admin_user
from aldar_middleware.schemas.admin_agents import (
    AgentCreateUpdateRequest,
    AgentResponse,
    AgentListResponse,
    AgentListItemResponse,
    AgentHealthResponse,
    AgentCategoriesResponse,
    AgentCategoryInfo,
    AgentDeleteResponse,
    CustomFeatureToggleResponse,
    CustomFeatureToggleFieldResponse,
    CustomFeatureDropdownResponse,
    CustomFeatureDropdownFieldResponse,
    CustomFeatureDropdownOptionResponse,
    CustomHeaderToggle,
    MCPValidationRequest,
    MCPValidationResponse,
    AgentAnalyticsResponse,
    AgentAnalyticsItem,
    AgentMonitoringResponse,
    AgentMonitoringItem,
)
from aldar_middleware.schemas.feedback import PaginatedResponse
from aldar_middleware.settings import settings
from aldar_middleware.services.postgres_logs_service import postgres_logs_service
from aldar_middleware.settings.context import get_correlation_id
from aldar_middleware.utils.agent_utils import set_agent_type
from aldar_middleware.services.agent_available_cache import get_agent_available_cache

logger = logging.getLogger(__name__)

router = APIRouter()


def get_cache_dependency():
    """Dependency function to get agent available cache instance."""
    return get_agent_available_cache()


def _is_uuid(value: str) -> bool:
    """Check if a string is a valid UUID."""
    if not value or not isinstance(value, str):
        return False
    try:
        UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


async def _resolve_attachment_id_to_url(db: AsyncSession, attachment_id: str) -> Optional[str]:
    """Resolve an attachment ID to a blob URL."""
    try:
        result = await db.execute(
            select(Attachment).where(
                Attachment.id == UUID(attachment_id),
                Attachment.is_active == True
            )
        )
        attachment = result.scalar_one_or_none()
        if attachment and attachment.blob_url:
            return attachment.blob_url
        else:
            logger.warning(f"Attachment not found or has no blob_url: {attachment_id}")
            return None
    except ValueError:
        logger.warning(f"Invalid UUID format: {attachment_id}")
        return None
    except Exception as e:
        logger.error(f"Failed to resolve attachment ID {attachment_id}: {str(e)}")
        return None


async def _get_agent_categories(db: AsyncSession, agent_id: int) -> list[str]:
    """Get categories for an agent."""
    try:
        # Try to get from agent_tags table
        result = await db.execute(
            select(AgentTag.tag)
            .where(
                and_(
                    AgentTag.agent_id == agent_id,
                    AgentTag.tag_type == "category",
                    AgentTag.is_active == True
                )
            )
        )
        tags = [row[0] for row in result.all()]
        if tags:
            return tags
    except Exception:
        # If agent_tags table doesn't exist, rollback and fall back to legacy fields
        await db.rollback()
    
    # Fallback to legacy category field or legacy_tags
    result = await db.execute(
        select(Agent.category, Agent.legacy_tags)
        .where(Agent.id == agent_id)
    )
    row = result.first()
    if row:
        # Check legacy_tags first (JSON array)
        if row.legacy_tags and isinstance(row.legacy_tags, list):
            return row.legacy_tags
        # Then check category field (single string)
        if row.category:
            return [row.category]
    return []


async def _set_agent_categories(db: AsyncSession, agent_id: int, categories: list[str]):
    """Set categories for an agent."""
    if not categories:
        return
    
    try:
        # Delete existing category tags
        await db.execute(
            delete(AgentTag).where(
                and_(
                    AgentTag.agent_id == agent_id,
                    AgentTag.tag_type == "category"
                )
            )
        )
        
        # Flush to ensure deletions are committed before insertions
        await db.flush()
        
        # Create new category tags
        for category in categories:
            tag = AgentTag(
                agent_id=agent_id,
                tag=category,
                tag_type="category",
                is_active=True
            )
            db.add(tag)
        
        # Flush to detect any integrity errors
        await db.flush()
        
    except Exception as e:
        # Check if it's a unique constraint violation
        if "unique constraint" in str(e).lower() or "duplicate key" in str(e).lower():
            logger.warning(f"Duplicate tags detected for agent {agent_id}, ignoring: {str(e)}")
            # Rollback the failed transaction
            await db.rollback()
            # Don't re-raise - treat duplicates as non-fatal
            return
        
        # If agent_tags table doesn't exist, rollback and fall back to legacy fields
        await db.rollback()
        logger.warning(f"agent_tags table may not exist, using legacy fields: {str(e)}")
        
        # Fallback: store in legacy_tags (JSON array)
        result = await db.execute(
            select(Agent).where(Agent.id == agent_id)
        )
        agent = result.scalar_one_or_none()
        if agent:
            agent.legacy_tags = categories


async def _set_agent_tools(db: AsyncSession, agent_id: int, tool_names: Optional[list[str]]):
    """Set tools for an agent."""
    from aldar_middleware.models.agent_tools import AgentTool
    
    try:
        # Delete existing tools
        await db.execute(
            delete(AgentTool).where(AgentTool.agent_id == agent_id)
        )
        
        # Flush to ensure deletions are committed before insertions
        await db.flush()
        
        # Create new tools from tool names
        if tool_names:
            for idx, tool_name in enumerate(tool_names):
                if tool_name and tool_name.strip():  # Skip empty strings
                    tool = AgentTool(
                        agent_id=agent_id,
                        tool_name=tool_name.strip(),
                        tool_description=None,  # Can be set later if needed
                        tool_url=None,
                        tool_icon=None,
                        tool_color=None,
                        tool_order=idx + 1,  # Start from 1
                        tool_is_active=True
                    )
                    db.add(tool)
            
            # Flush to detect any integrity errors
            await db.flush()
            
    except Exception as e:
        # Check if it's a unique constraint violation
        if "unique constraint" in str(e).lower() or "duplicate key" in str(e).lower():
            logger.warning(f"Duplicate tools detected for agent {agent_id}, ignoring: {str(e)}")
            await db.rollback()
            return
        
        # If agent_tools table doesn't exist, log warning but don't fail
        await db.rollback()
        logger.warning(f"agent_tools table may not exist, skipping tools update: {str(e)}")


async def _get_custom_features(db: AsyncSession, agent_id: int) -> dict:
    """Get custom feature configurations for an agent."""
    toggle_config = None
    dropdown_config = None
    text_config = None
    
    try:
        result = await db.execute(
            select(AgentConfiguration)
            .where(AgentConfiguration.agent_id == agent_id)
        )
        configs = result.scalars().all()
        
        for config in configs:
            if config.configuration_name == "custom_feature_toggle":
                toggle_config = config.values
            elif config.configuration_name == "custom_feature_dropdown":
                dropdown_config = config.values
            elif config.configuration_name == "custom_feature_text":
                text_config = config.values
    except Exception:
        # If agent_configuration table doesn't exist, return empty features
        await db.rollback()
    
    return {
        "toggle": toggle_config,
        "dropdown": dropdown_config,
        "text": text_config
    }


async def _set_custom_features(
    db: AsyncSession,
    agent_id: int,
    toggle_data: Optional[dict],
    dropdown_data: Optional[dict],
    text_data: Optional[dict] = None
):
    """Set custom feature configurations for an agent.
    
    Note: instruction is now stored directly in agents table column,
    not in agent_configuration table. Old instruction records in
    agent_configuration will be cleaned up automatically.
    """
    try:
        # Delete all existing configurations for this agent
        # This includes old instruction entries which are no longer needed
        # since instruction is now stored in agents.instruction column
        await db.execute(
            delete(AgentConfiguration).where(AgentConfiguration.agent_id == agent_id)
        )
        
        # Store custom feature toggle
        if toggle_data:
            toggle_config = AgentConfiguration(
                agent_id=agent_id,
                configuration_name="custom_feature_toggle",
                type="object",
                values=toggle_data
            )
            db.add(toggle_config)
        
        # Store custom feature dropdown
        if dropdown_data:
            dropdown_config = AgentConfiguration(
                agent_id=agent_id,
                configuration_name="custom_feature_dropdown",
                type="object",
                values=dropdown_data
            )
            db.add(dropdown_config)
        
        # Store custom feature text
        if text_data:
            text_config = AgentConfiguration(
                agent_id=agent_id,
                configuration_name="custom_feature_text",
                type="object",
                values=text_data
            )
            db.add(text_config)
    except Exception as e:
        # If agent_configuration table doesn't exist, rollback and log warning
        await db.rollback()
        logger.warning(f"agent_configuration table may not exist, skipping instruction/custom_features: {str(e)}")


async def _build_agent_response(db: AsyncSession, agent: Agent) -> AgentResponse:
    """Build agent response from database."""
    agent_id = str(agent.public_id)
    
    # Get categories
    categories = await _get_agent_categories(db, agent.id)
    
    # Get custom features
    features = await _get_custom_features(db, agent.id)
    
    # Instruction is now stored directly in agents table, not in agent_configuration
    instruction = agent.instruction
    
    # Helper function to resolve attachment data
    async def _resolve_attachment_data(attachment_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Resolve attachment ID to full attachment data."""
        if not attachment_id or not _is_uuid(attachment_id):
            return None
        try:
            result = await db.execute(
                select(Attachment).where(
                    Attachment.id == UUID(attachment_id),
                    Attachment.is_active == True
                )
            )
            attachment = result.scalar_one_or_none()
            if attachment:
                return {
                    "attachment_id": str(attachment.id),
                    "file_name": attachment.file_name,
                    "file_size": attachment.file_size,
                    "content_type": attachment.content_type,
                    "blob_url": attachment.blob_url,
                    "blob_name": attachment.blob_name,
                    "entity_type": attachment.entity_type,
                    "entity_id": attachment.entity_id,
                    "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
                }
        except Exception as e:
            logger.warning(f"Failed to fetch attachment {attachment_id}: {str(e)}")
        return None
    
    # Build toggle response
    toggle_response = None
    if features.get("toggle"):
        toggle_data = features["toggle"]
        toggle_fields = []
        for field in toggle_data.get("fields", []):
            field_icon = field.get("field_icon")
            field_icon_attachment = await _resolve_attachment_data(field_icon) if field_icon else None
            toggle_fields.append(
                CustomFeatureToggleFieldResponse(
                    field_id=field.get("field_id"),
                    field_name=field.get("field_name", ""),
                    is_default=field.get("is_default", False),
                    field_icon=field_icon,
                    field_icon_attachment=field_icon_attachment
                )
            )
        toggle_response = CustomFeatureToggleResponse(
            enabled=toggle_data.get("enabled", False),
            fields=toggle_fields
        )
    
    # Build dropdown response
    dropdown_response = None
    if features.get("dropdown"):
        dropdown_data = features["dropdown"]
        dropdown_fields = []
        for field in dropdown_data.get("fields", []):
            field_icon = field.get("field_icon")
            field_icon_attachment = await _resolve_attachment_data(field_icon) if field_icon else None
            
            # Process options
            options = []
            for opt in field.get("options", []):
                option_icon = opt.get("option_icon")
                option_icon_attachment = await _resolve_attachment_data(option_icon) if option_icon else None
                options.append(
                    CustomFeatureDropdownOptionResponse(
                        option_id=opt.get("option_id"),
                        title_name=opt.get("title_name", ""),
                        value=opt.get("value", ""),
                        is_default=opt.get("is_default", False),
                        option_icon=option_icon,
                        option_icon_attachment=option_icon_attachment
                    )
                )
            
            dropdown_fields.append(
                CustomFeatureDropdownFieldResponse(
                    field_id=field.get("field_id"),
                    field_name=field.get("field_name", ""),
                    field_icon=field_icon,
                    field_icon_attachment=field_icon_attachment,
                    options=options
                )
            )
        dropdown_response = CustomFeatureDropdownResponse(
            enabled=dropdown_data.get("enabled", False),
            fields=dropdown_fields
        )
    
    # Build text response
    from aldar_middleware.schemas.admin_agents import CustomFeatureTextResponse, CustomFeatureTextFieldResponse
    text_response = None
    if features.get("text"):
        text_data = features["text"]
        text_response = CustomFeatureTextResponse(
            enabled=text_data.get("enabled", False),
            fields=[
                CustomFeatureTextFieldResponse(
                    field_id=field.get("field_id"),
                    field_name=field.get("field_name", ""),
                    field_value=field.get("field_value", "")
                )
                for field in text_data.get("fields", [])
            ]
        )
    
    # Get tools for this agent - include both active and inactive tools
    from aldar_middleware.models.agent_tools import AgentTool
    from aldar_middleware.schemas.admin_agents import AgentToolResponse
    
    tools_result = await db.execute(
        select(AgentTool)
        .where(AgentTool.agent_id == agent.id)
        .order_by(AgentTool.tool_order)
    )
    tools = tools_result.scalars().all()
    
    tools_response = [
        AgentToolResponse(
            tool_id=str(tool.id),
            tool_name=tool.tool_name,
            tool_description=tool.tool_description,
            tool_url=tool.tool_url,
            tool_icon=tool.tool_icon,
            tool_color=tool.tool_color,
            tool_order=tool.tool_order,
            tool_is_active=tool.tool_is_active
        )
        for tool in tools
    ]
    
    # Resolve agent icon attachment if it's a UUID
    agent_icon_attachment = None
    agent_icon_value = agent.icon
    if agent_icon_value and _is_uuid(agent_icon_value):
        try:
            result = await db.execute(
                select(Attachment).where(
                    Attachment.id == UUID(agent_icon_value),
                    Attachment.is_active == True
                )
            )
            attachment = result.scalar_one_or_none()
            if attachment:
                agent_icon_attachment = {
                    "attachment_id": str(attachment.id),
                    "file_name": attachment.file_name,
                    "file_size": attachment.file_size,
                    "content_type": attachment.content_type,
                    "blob_url": attachment.blob_url,
                    "blob_name": attachment.blob_name,
                    "entity_type": attachment.entity_type,
                    "entity_id": attachment.entity_id,
                    "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
                }
        except Exception as e:
            logger.warning(f"Failed to fetch agent icon attachment {agent_icon_value}: {str(e)}")
    
    # Build custom_header_toggle from agent_header
    custom_header_toggle_response = None
    if agent.agent_header:
        # Convert dict to JSON string if needed
        header_value = agent.agent_header
        if isinstance(header_value, dict):
            header_value = json.dumps(header_value)
        custom_header_toggle_response = CustomHeaderToggle(
            enabled=True,
            value=header_value
        )
    else:
        custom_header_toggle_response = CustomHeaderToggle(
            enabled=False,
            value=None
        )
    
    # Convert agent_header to JSON string if it's a dict
    agent_header_str = agent.agent_header
    if isinstance(agent_header_str, dict):
        agent_header_str = json.dumps(agent_header_str)
    
    return AgentResponse(
        success=True,
        agent_id=agent_id,
        agent_name=agent.name,
        agent_intro=agent.intro,
        agent_icon=agent_icon_value,
        agent_icon_attachment=agent_icon_attachment,
        mcp_server_link=agent.mcp_url,
        agent_health_url=agent.health_url,
        categories=categories,
        agent_enabled=agent.is_enabled,
        description=agent.description,
        instruction=instruction,
        custom_feature_toggle=toggle_response,
        custom_feature_dropdown=dropdown_response,
        custom_feature_text=text_response,
        custom_header_toggle=custom_header_toggle_response,
        tools=tools_response,
        include_in_teams=agent.include_in_teams,
        agent_header=agent_header_str,
        agent_capabilities=agent.agent_capabilities,
        add_history_to_context=agent.add_history_to_context,
        agent_metadata=agent.agent_metadata,
        created_at=agent.created_at,
        updated_at=agent.updated_at
    )


def _parse_agent_header_for_storage(agent_header: Optional[str]) -> Optional[dict]:
    """Parse agent_header JSON string to dict for storage in JSON column.
    
    Args:
        agent_header: JSON string or None
        
    Returns:
        Parsed dict or None
    """
    if not agent_header:
        return None
    
    # If it's already a dict, return as-is
    if isinstance(agent_header, dict):
        return agent_header
    
    # Parse JSON string to dict
    try:
        return json.loads(agent_header)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"Failed to parse agent_header as JSON: {e}. Storing as empty dict.")
        return None


async def _validate_agent_name(db: AsyncSession, agent_name: str, exclude_agent_id: Optional[int] = None):
    """Validate agent name is unique."""
    query = select(Agent).where(Agent.name == agent_name)
    if exclude_agent_id:
        query = query.where(Agent.id != exclude_agent_id)
    
    result = await db.execute(query)
    existing_agent = result.scalar_one_or_none()
    
    if existing_agent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Entered agent name already exists."
        )


async def _create_agent_from_data(
    db: AsyncSession,
    agent_name: str,
    agent_intro: Optional[str],
    agent_icon_attachment_id: Optional[str],
    mcp_server_link: Optional[str],
    agent_health_url: Optional[str],
    categories_list: list[str],
    agent_enabled: bool,
    description: Optional[str],
    instruction: Optional[str],
    toggle_data: Optional[dict],
    dropdown_data: Optional[dict],
    text_data: Optional[dict],
    toggle_field_icon_urls: list[str],
    dropdown_field_icon_urls: list[str],
    dropdown_option_icon_urls: list[str],
    agent_icon_url: Optional[str],
    agent_type: Optional[str] = None,
    include_in_teams: bool = False,
    agent_header: Optional[str] = None,
    agent_capabilities: Optional[str] = None,
    add_history_to_context: bool = False,
    agent_metadata: Optional[dict] = None,
    cache = None
) -> AgentResponse:
    """Create agent from parsed data."""
    # Validate agent name
    await _validate_agent_name(db, agent_name)
    
    # Use icon value directly (can be UUID or URL)
    icon_value = agent_icon_url  # This parameter name is misleading but kept for compatibility
    logger.info(f"Creating agent '{agent_name}' with icon: {icon_value}")
    
    # Create agent
    logger.info(f"🔧 Creating Agent with - include_in_teams: {include_in_teams}, agent_header: {agent_header[:50] if agent_header else 'None'}...")
    
    # Parse agent_header JSON string to dict for storage
    agent_header_dict = _parse_agent_header_for_storage(agent_header)
    
    agent = Agent(
        name=agent_name,
        intro=agent_intro,
        icon=icon_value,  # Store UUID or URL as-is
        mcp_url=mcp_server_link,
        health_url=agent_health_url,
        description=description,
        is_enabled=agent_enabled,
        status="active" if agent_enabled else "inactive",  # Sync status with is_enabled
        category="enterprise_agents",  # Set category for admin-created agents
        include_in_teams=include_in_teams,
        agent_header=agent_header_dict,
        instruction=instruction,
        agent_capabilities=agent_capabilities,
        add_history_to_context=add_history_to_context,
        agent_metadata=agent_metadata
    )
    db.add(agent)
    await db.flush()  # Get agent.id
    
    # Verify the values were set
    logger.info(f"✅ Agent created - ID: {agent.id}, include_in_teams: {agent.include_in_teams}, agent_header: {str(agent.agent_header)[:50] if agent.agent_header else 'None'}...")
    
    agent_id = agent.id
    
    # Set categories
    await _set_agent_categories(db, agent_id, categories_list)
    
    # Set agent type if provided
    if agent_type:
        await set_agent_type(db, agent_id, agent_type)
    
    # Process custom feature toggle icons
    # If URLs are already in the data (from direct attachment ID resolution), use those
    # Otherwise, fall back to array-based mapping (backward compatibility)
    if toggle_data:
        fields = toggle_data.get("fields", [])
        for idx, field in enumerate(fields):
            # Only apply array URL if field doesn't already have a resolved URL
            # If field_icon is empty or still a UUID (not resolved), use array
            field_icon = field.get("field_icon", "")
            if not field_icon or _is_uuid(field_icon):
                if idx < len(toggle_field_icon_urls):
                    field["field_icon"] = toggle_field_icon_urls[idx]
    
    # Process custom feature dropdown icons
    # If URLs are already in the data (from direct attachment ID resolution), use those
    # Otherwise, fall back to array-based mapping (backward compatibility)
    if dropdown_data:
        fields = dropdown_data.get("fields", [])
        option_idx = 0
        
        # Process field icons
        for idx, field in enumerate(fields):
            # Only apply array URL if field doesn't already have a resolved URL
            field_icon = field.get("field_icon", "")
            if not field_icon or _is_uuid(field_icon):
                if idx < len(dropdown_field_icon_urls):
                    field["field_icon"] = dropdown_field_icon_urls[idx]
            
            # Process option icons
            options = field.get("options", [])
            for opt in options:
                # Only apply array URL if option doesn't already have a resolved URL
                option_icon = opt.get("option_icon", "")
                if not option_icon or _is_uuid(option_icon):
                    if option_idx < len(dropdown_option_icon_urls):
                        opt["option_icon"] = dropdown_option_icon_urls[option_idx]
                        option_idx += 1
    
    # Set custom features (instruction is now stored directly in agents table)
    await _set_custom_features(
        db=db,
        agent_id=agent_id,
        toggle_data=toggle_data,
        dropdown_data=dropdown_data,
        text_data=text_data
    )
    
    # Commit all changes
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to commit agent creation: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create agent: {str(e)}"
        )

    # Invalidate agent_available cache
    if cache:
        new_version = await cache.invalidate_all()
        logger.info(f"Invalidated agent_available cache after agent creation (version: {new_version})")

    # Re-query agent to ensure it's fully loaded
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id)
    )
    agent = result.scalar_one_or_none()
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent was created but could not be retrieved. Agent ID: {agent_id}"
        )
    
    logger.info(f"Agent '{agent.name}' created successfully with icon: {agent.icon}")
    
    return await _build_agent_response(db, agent)


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    http_request: Request,
    request: AgentCreateUpdateRequest = Body(...),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
    cache = Depends(get_cache_dependency)
) -> AgentResponse:
    # Log incoming request for debugging
    try:
        request_dict = request.model_dump()
        logger.info(f"📥 Incoming request fields: {list(request_dict.keys())}")
        if 'custom_header_toggle' in request_dict:
            logger.info(f"📥 custom_header_toggle: {request_dict.get('custom_header_toggle')}")
        if 'include_in_teams' in request_dict:
            logger.info(f"📥 include_in_teams: {request_dict.get('include_in_teams')}")
        if 'agent_header' in request_dict:
            logger.info(f"📥 agent_header: {request_dict.get('agent_header')[:50] if request_dict.get('agent_header') else 'None'}...")
    except Exception as e:
        logger.warning(f"⚠️  Could not log request fields: {e}")
    
    """
    Create/Update Agent Configuration (Admin Only).
    
    Creates a new agent configuration or updates if agent with same name exists.
    
    **File Upload Flow (Attachment-based):**
    1. First, upload files to `/api/attachments/upload` → get attachment IDs
    2. Then create agent with attachment IDs (not files directly)
    3. Agent API automatically resolves attachment IDs to blob URLs
    
    **Request Body (JSON) - Recommended Format:**
    ```json
    {
      "agent_name": "Legal Lens",
      "agent_intro": "For legal queries",
      "agent_icon": "uuid-from-upload",
      "mcp_server_link": "https://example.com/api",
      "agent_health_url": "https://example.com/health",
      "categories": ["Knowledge Agent", "Legal"],
      "agent_enabled": true,
      "description": "Agent description",
      "instruction": "You are a legal expert AI...",
      "include_in_teams": true,
      "custom_feature_toggle": {
        "enabled": true,
        "fields": [
          {
            "field_name": "Web Search",
            "is_default": true,
            "field_icon": "uuid-from-upload"
          },
          {
            "field_name": "Extended Thinking",
            "is_default": false,
            "field_icon": "uuid-from-upload"
          }
        ]
      },
      "custom_feature_dropdown": {
        "enabled": true,
        "fields": [
          {
            "field_name": "Mode",
            "field_icon": "uuid-from-upload",
            "options": [
              {
                "title_name": "Creative",
                "value": "Creative",
                "is_default": true,
                "option_icon": "uuid-from-upload"
              },
              {
                "title_name": "Precise",
                "value": "Precise",
                "is_default": false,
                "option_icon": "uuid-from-upload"
              }
            ]
          }
        ]
      },
      "custom_feature_text": {
        "enabled": true,
        "fields": [
          {
            "field_name": "Web Search",
            "field_value": "Enable web search for this agent"
          },
          {
            "field_name": "API Key",
            "field_value": "Enter your API key here"
          }
        ]
      }
    }
    ```
    
    **Notes:**
    - `agent_icon`, `field_icon`, and `option_icon` can be:
      - Attachment IDs (UUIDs) - will be automatically resolved to blob URLs
      - Blob URLs - used directly
      - Empty strings - no icon
    
    **Legacy Format (Still Supported):**
    The old format using separate arrays is still supported for backward compatibility:
    - `agent_icon_attachment_id`: Attachment ID for agent icon
    - `toggle_field_icon_ids`: Array of attachment IDs matching field order
    - `dropdown_field_icon_ids`: Array of attachment IDs matching field order
    - `dropdown_option_icon_ids`: Array of attachment IDs for all options in order
    """
    try:
        # Extract data from JSON request
        agent_name = request.agent_name
        agent_intro = request.agent_intro
        mcp_server_link = request.mcp_server_link
        agent_health_url = request.agent_health_url
        categories_list = request.categories or []
        agent_enabled = request.agent_enabled
        description = request.description
        instruction = request.instruction
        include_in_teams = request.include_in_teams
        agent_header = getattr(request, 'agent_header', None)
        
        # Extract new fields
        agent_capabilities = request.agent_capabilities
        add_history_to_context = request.add_history_to_context
        agent_metadata = request.agent_metadata
        
        # Handle custom_header_toggle from frontend (map to agent_header)
        # Frontend sends custom_header_toggle with enabled and value, we need to extract it
        if hasattr(request, 'custom_header_toggle') and request.custom_header_toggle:
            if request.custom_header_toggle.enabled and request.custom_header_toggle.value:
                agent_header = request.custom_header_toggle.value
                # Clean up the value if it has Python f-string syntax
                if agent_header and ('f"' in agent_header or "f'" in agent_header):
                    # Remove f-string prefix if present
                    agent_header = agent_header.replace('f"', '"').replace("f'", "'")
                logger.info(f"✓ Extracted agent_header from custom_header_toggle.value: {agent_header[:50] if agent_header else 'None'}...")
        
        logger.info(f"✓ Final values - include_in_teams: {include_in_teams}, agent_header: {agent_header[:50] if agent_header else 'None'}...")
        
        # Custom features are already parsed as objects (not strings)
        toggle_data = request.custom_feature_toggle.dict() if request.custom_feature_toggle else None
        dropdown_data = request.custom_feature_dropdown.dict() if request.custom_feature_dropdown else None
        text_data = request.custom_feature_text.dict() if request.custom_feature_text else None
        
        # Process agent_icon - preserve UUID if it's a UUID, otherwise use URL
        agent_icon_value = None
        
        # New way: agent_icon can be an attachment ID (UUID) or URL
        if request.agent_icon:
            if _is_uuid(request.agent_icon):
                # It's an attachment ID (UUID), validate it exists but store as-is
                try:
                    result = await db.execute(
                        select(Attachment).where(
                            Attachment.id == UUID(request.agent_icon),
                            Attachment.is_active == True
                        )
                    )
                    attachment = result.scalar_one_or_none()
                    if not attachment:
                        raise HTTPException(
                            status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Attachment with ID {request.agent_icon} not found or is inactive. Please upload the file first using /api/attachments/upload"
                        )
                    # Store the UUID as-is (don't convert to blob_url)
                    agent_icon_value = request.agent_icon
                except ValueError:
                    # Invalid UUID format, treat as URL
                    agent_icon_value = request.agent_icon
            else:
                # It's a URL, use it directly
                agent_icon_value = request.agent_icon
        
        # Backward compatibility: also check agent_icon_attachment_id
        if not agent_icon_value and request.agent_icon_attachment_id:
            try:
                result = await db.execute(
                    select(Attachment).where(
                        Attachment.id == UUID(request.agent_icon_attachment_id),
                        Attachment.is_active == True
                    )
                )
                attachment = result.scalar_one_or_none()
                if attachment:
                    # Store the UUID as-is
                    agent_icon_value = request.agent_icon_attachment_id
                else:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Attachment with ID {request.agent_icon_attachment_id} not found or is inactive. Please upload the file first using /api/attachments/upload"
                    )
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid attachment ID: {str(e)}"
                )
        
        # Process toggle fields - validate attachment IDs in field_icon (preserve UUIDs)
        if toggle_data:
            fields = toggle_data.get("fields", [])
            for field in fields:
                field_icon = field.get("field_icon")
                if field_icon and _is_uuid(field_icon):
                    # Validate that the attachment exists, but preserve UUID
                    try:
                        result = await db.execute(
                            select(Attachment).where(
                                Attachment.id == UUID(field_icon),
                                Attachment.is_active == True
                            )
                        )
                        attachment = result.scalar_one_or_none()
                        if not attachment:
                            logger.warning(f"Toggle field icon attachment not found: {field_icon}")
                            field["field_icon"] = ""  # Clear invalid ID
                        # Otherwise, keep the UUID as-is
                    except Exception as e:
                        logger.error(f"Failed to validate toggle field icon attachment {field_icon}: {str(e)}")
                        field["field_icon"] = ""  # Clear invalid ID
        
        # Process dropdown fields and options - validate attachment IDs (preserve UUIDs)
        if dropdown_data:
            fields = dropdown_data.get("fields", [])
            for field in fields:
                # Validate field icon
                field_icon = field.get("field_icon")
                if field_icon and _is_uuid(field_icon):
                    try:
                        result = await db.execute(
                            select(Attachment).where(
                                Attachment.id == UUID(field_icon),
                                Attachment.is_active == True
                            )
                        )
                        attachment = result.scalar_one_or_none()
                        if not attachment:
                            logger.warning(f"Dropdown field icon attachment not found: {field_icon}")
                            field["field_icon"] = ""  # Clear invalid ID
                        # Otherwise, keep the UUID as-is
                    except Exception as e:
                        logger.error(f"Failed to validate dropdown field icon attachment {field_icon}: {str(e)}")
                        field["field_icon"] = ""  # Clear invalid ID
                
                # Validate option icons
                options = field.get("options", [])
                for option in options:
                    option_icon = option.get("option_icon")
                    if option_icon and _is_uuid(option_icon):
                        try:
                            result = await db.execute(
                                select(Attachment).where(
                                    Attachment.id == UUID(option_icon),
                                    Attachment.is_active == True
                                )
                            )
                            attachment = result.scalar_one_or_none()
                            if not attachment:
                                logger.warning(f"Dropdown option icon attachment not found: {option_icon}")
                                option["option_icon"] = ""  # Clear invalid ID
                            # Otherwise, keep the UUID as-is
                        except Exception as e:
                            logger.error(f"Failed to validate dropdown option icon attachment {option_icon}: {str(e)}")
                            option["option_icon"] = ""  # Clear invalid ID
        
        # Backward compatibility: Process old-style arrays
        toggle_field_icon_ids = request.toggle_field_icon_ids or []
        dropdown_field_icon_ids = request.dropdown_field_icon_ids or []
        dropdown_option_icon_ids = request.dropdown_option_icon_ids or []
        
        # Fetch blob URLs for custom feature icons (backward compatibility)
        toggle_field_icon_urls = []
        for attachment_id in toggle_field_icon_ids:
            # Skip invalid UUIDs
            if not attachment_id or not _is_uuid(attachment_id):
                logger.debug(f"Skipping invalid toggle field icon attachment ID: {attachment_id}")
                continue
            try:
                result = await db.execute(
                    select(Attachment).where(
                        Attachment.id == UUID(attachment_id),
                        Attachment.is_active == True
                    )
                )
                attachment = result.scalar_one_or_none()
                if attachment:
                    toggle_field_icon_urls.append(attachment.blob_url)
                else:
                    logger.warning(f"Toggle field icon attachment not found: {attachment_id}")
            except Exception as e:
                logger.error(f"Failed to fetch toggle field icon attachment {attachment_id}: {str(e)}")
        
        dropdown_field_icon_urls = []
        for attachment_id in dropdown_field_icon_ids:
            # Skip invalid UUIDs
            if not attachment_id or not _is_uuid(attachment_id):
                logger.debug(f"Skipping invalid dropdown field icon attachment ID: {attachment_id}")
                continue
            try:
                result = await db.execute(
                    select(Attachment).where(
                        Attachment.id == UUID(attachment_id),
                        Attachment.is_active == True
                    )
                )
                attachment = result.scalar_one_or_none()
                if attachment:
                    dropdown_field_icon_urls.append(attachment.blob_url)
                else:
                    logger.warning(f"Dropdown field icon attachment not found: {attachment_id}")
            except Exception as e:
                logger.error(f"Failed to fetch dropdown field icon attachment {attachment_id}: {str(e)}")
        
        dropdown_option_icon_urls = []
        for attachment_id in dropdown_option_icon_ids:
            # Skip invalid UUIDs
            if not attachment_id or not _is_uuid(attachment_id):
                logger.debug(f"Skipping invalid dropdown option icon attachment ID: {attachment_id}")
                continue
            try:
                result = await db.execute(
                    select(Attachment).where(
                        Attachment.id == UUID(attachment_id),
                        Attachment.is_active == True
                    )
                )
                attachment = result.scalar_one_or_none()
                if attachment:
                    dropdown_option_icon_urls.append(attachment.blob_url)
                else:
                    logger.warning(f"Dropdown option icon attachment not found: {attachment_id}")
            except Exception as e:
                logger.error(f"Failed to fetch dropdown option icon attachment {attachment_id}: {str(e)}")
        
        # Log icon value for debugging
        if agent_icon_value:
            logger.info(f"Agent icon value: {agent_icon_value}")
        
        # Use helper function to create agent
        agent_response = await _create_agent_from_data(
            db=db,
            agent_name=agent_name,
            agent_intro=agent_intro,
            agent_icon_attachment_id=None,  # Not used anymore, pass icon value directly
            mcp_server_link=mcp_server_link,
            agent_health_url=agent_health_url,
            categories_list=categories_list,
            agent_enabled=agent_enabled,
            description=description,
            instruction=instruction,
            toggle_data=toggle_data,
            dropdown_data=dropdown_data,
            text_data=text_data,
            toggle_field_icon_urls=toggle_field_icon_urls,
            dropdown_field_icon_urls=dropdown_field_icon_urls,
            dropdown_option_icon_urls=dropdown_option_icon_urls,
            agent_icon_url=agent_icon_value,  # Pass the icon value (UUID or URL)
            agent_type=request.agent_type,
            include_in_teams=include_in_teams,
            agent_header=agent_header,
            agent_capabilities=agent_capabilities,
            add_history_to_context=add_history_to_context,
            agent_metadata=agent_metadata,
            cache=cache
        )
        
        # Set tools after agent is created (we need agent.id from the response)
        # Get agent_id from the response
        agent_id_result = await db.execute(
            select(Agent).where(Agent.public_id == UUID(agent_response.agent_id))
        )
        agent = agent_id_result.scalar_one_or_none()
        if agent:
            await _set_agent_tools(db, agent.id, request.tools)
            await db.commit()
            
            # Rebuild response to include tools
            agent_response = await _build_agent_response(db, agent)
        
        # Call internal MCP add-agent endpoint after successful agent creation
        mcp_response_data = None
        try:
            # Get base URL from settings
            base_url = settings.agno_base_url
            if base_url and http_request:
                # Extract authorization header from incoming request
                authorization_header = None
                auth_header = http_request.headers.get("Authorization")
                if auth_header:
                    authorization_header = auth_header
                
                # Build add-mcp-agent endpoint URL
                base_url_clean = base_url.rstrip("/")
                add_mcp_agent_url = f"{base_url_clean}/admin/mcp/add-mcp-agent"
                
                # Prepare request payload
                payload = {
                    "agent_name": agent_name
                }
                
                # Prepare headers
                headers = {
                    "Content-Type": "application/json",
                    "accept": "application/json"
                }
                
                # Add authorization header if available
                if authorization_header:
                    headers["Authorization"] = authorization_header
                
                logger.info(f"🔗 INTERNAL API CALL: POST {add_mcp_agent_url} with payload: {payload}")
                
                # Make request to add-mcp-agent API using configured timeout from settings
                timeout_seconds = settings.agno_api_timeout
                connect_timeout = min(10.0, timeout_seconds)
                timeout = httpx.Timeout(timeout_seconds, connect=connect_timeout)
                
                async with httpx.AsyncClient(timeout=timeout) as client:
                    try:
                        response = await client.post(
                            add_mcp_agent_url,
                            json=payload,
                            headers=headers
                        )
                        
                        # Log response - handle both success and error cases
                        if response.status_code >= 200 and response.status_code < 300:
                            # Success response - try to parse as JSON
                            try:
                                response_data = response.json() if response.content else {}
                                mcp_response_data = response_data  # Store for response
                                logger.info(
                                    f"✅ INTERNAL API RESPONSE: POST {add_mcp_agent_url}, "
                                    f"status={response.status_code}, "
                                    f"response={response_data}"
                                )
                            except Exception:
                                # If JSON parsing fails, log the raw text
                                response_text = response.text[:500] if response.text else "Empty response"
                                mcp_response_data = {"error": "Failed to parse JSON", "response_text": response_text}
                                logger.info(
                                    f"✅ INTERNAL API RESPONSE: POST {add_mcp_agent_url}, "
                                    f"status={response.status_code}, "
                                    f"response_text={response_text}"
                                )
                        else:
                            # Error response - log status and response text
                            try:
                                response_data = response.json() if response.content else {}
                                mcp_response_data = response_data  # Store for response
                                logger.warning(
                                    f"⚠️ INTERNAL API ERROR RESPONSE: POST {add_mcp_agent_url}, "
                                    f"status={response.status_code}, "
                                    f"response={response_data}"
                                )
                            except Exception:
                                # If JSON parsing fails, log the raw text
                                response_text = response.text[:500] if response.text else "Empty response"
                                mcp_response_data = {"error": "Failed to parse JSON", "status_code": response.status_code, "response_text": response_text}
                                logger.warning(
                                    f"⚠️ INTERNAL API ERROR RESPONSE: POST {add_mcp_agent_url}, "
                                    f"status={response.status_code}, "
                                    f"response_text={response_text}"
                                )
                        
                    except httpx.TimeoutException as e:
                        mcp_response_data = {"error": "Timeout", "message": str(e), "timeout_seconds": timeout_seconds}
                        logger.warning(
                            f"⏱️ Timeout calling internal MCP add-agent endpoint: {str(e)} "
                            f"(timeout: {timeout_seconds}s)"
                        )
                    except httpx.HTTPStatusError as e:
                        error_detail = getattr(e.response, 'text', 'No error details available')
                        mcp_response_data = {"error": "HTTP error", "status_code": e.response.status_code, "detail": error_detail[:500]}
                        logger.warning(
                            f"⚠️ HTTP error calling internal MCP add-agent endpoint: "
                            f"{e.response.status_code} - {error_detail[:500]}"
                        )
                    except httpx.RequestError as e:
                        error_msg = str(e) or f"Connection error: {type(e).__name__}"
                        mcp_response_data = {"error": "Request error", "message": error_msg}
                        logger.warning(
                            f"⚠️ Request error calling internal MCP add-agent endpoint: {error_msg}"
                        )
                    except Exception as e:
                        mcp_response_data = {"error": "Unexpected error", "message": str(e)}
                        logger.warning(
                            f"⚠️ Unexpected error calling internal MCP add-agent endpoint: {str(e)}"
                        )
            elif not base_url:
                logger.debug("Skipping internal MCP add-agent call: agno_base_url not configured")
            elif not http_request:
                logger.debug("Skipping internal MCP add-agent call: http_request not available")
        except Exception as e:
            # Don't fail agent creation if MCP call fails - just log the error
            mcp_response_data = {"error": "Exception", "message": str(e)}
            logger.warning(
                f"⚠️ Failed to call internal MCP add-agent endpoint (non-blocking): {str(e)}"
            )
        
        # Add MCP response to agent response
        if mcp_response_data:
            # Convert agent_response to dict, add mcp_response, and convert back
            agent_response_dict = agent_response.model_dump()
            agent_response_dict["mcp_add_agent_response"] = mcp_response_data
            agent_response = AgentResponse(**agent_response_dict)
        
        # Log admin action: ADMIN_KNOWLEDGE_AGENT_CREATED
        try:
            correlation_id = get_correlation_id() or str(uuid.uuid4())
            
            # Build body with agent properties (using correct field names from AgentResponse)
            body = {
                "id": agent_response.agent_id,
                "name": agent_response.agent_name,
                "description": agent_response.description,
                "intro": agent_response.agent_intro,
                "instruction": agent_response.instruction,
                "systemPrompt": None,  # Not available in AgentResponse
                "selectedAgentType": None,  # Not available in AgentResponse
                "isWebSearchEnabled": None,  # Not available in AgentResponse
                "isSystemPromptEnabled": None,  # Not available in AgentResponse
            }
            # Add groups/categories if available (categories is List[str] in AgentResponse)
            if agent_response.categories:
                body["groups"] = agent_response.categories
            
            admin_log_data = {
                "id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": "INFO",
                "action_type": "ADMIN_KNOWLEDGE_AGENT_CREATED",
                "user_id": str(current_user.id),
                "email": current_user.email,
                "username": current_user.username or current_user.email,
                "correlation_id": correlation_id,
                "module": "admin_agents",
                "function": "create_agent",
                "message": f"Knowledge agent created: {agent_response.agent_name}",
                "log_data": {
                    "action_type": "ADMIN_KNOWLEDGE_AGENT_CREATED",
                    "id": agent_response.agent_id,
                    "body": body,
                }
            }
            
            # Write to PostgreSQL synchronously using the same db session
            await postgres_logs_service.write_admin_log(db, admin_log_data)
        except Exception as e:
            logger.error(f"Failed to write admin log for agent creation: {e}", exc_info=True)
        
        return agent_response
        
    except HTTPException:
        # Re-raise HTTP exceptions (like validation errors) with their original status codes
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to create/update agent: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create/update agent: {str(e)}"
        )


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    request: AgentCreateUpdateRequest = Body(...),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
    cache = Depends(get_cache_dependency)
) -> AgentResponse:
    """Update Agent Configuration (Admin Only)."""
    try:
        # Find agent by public_id
        result = await db.execute(
            select(Agent).where(Agent.public_id == UUID(agent_id))
        )
        agent = result.scalar_one_or_none()
        
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found"
            )
        
        # Validate agent name (excluding current agent)
        await _validate_agent_name(db, request.agent_name, exclude_agent_id=agent.id)
        
        # Extract agent_header - check custom_header_toggle first, then fall back to direct agent_header
        agent_header = request.agent_header
        if hasattr(request, 'custom_header_toggle') and request.custom_header_toggle:
            if request.custom_header_toggle.enabled and request.custom_header_toggle.value:
                agent_header = request.custom_header_toggle.value
                # Clean up the value if it has Python f-string syntax
                if agent_header and ('f"' in agent_header or "f'" in agent_header):
                    agent_header = agent_header.replace('f"', '"').replace("f'", "'")
                logger.info(f"✓ Extracted agent_header from custom_header_toggle.value in PUT: {agent_header[:50] if agent_header else 'None'}...")
        
        # Parse agent_header JSON string to dict for storage
        agent_header_dict = _parse_agent_header_for_storage(agent_header)
        
        # Update agent fields
        agent.name = request.agent_name
        agent.intro = request.agent_intro
        agent.mcp_url = request.mcp_server_link
        agent.health_url = request.agent_health_url
        agent.description = request.description
        agent.is_enabled = request.agent_enabled
        agent.status = "active" if request.agent_enabled else "inactive"  # Sync status with is_enabled
        agent.include_in_teams = request.include_in_teams
        agent.agent_header = agent_header_dict  # Use parsed dict from custom_header_toggle or direct
        agent.instruction = request.instruction
        agent.agent_capabilities = request.agent_capabilities
        agent.add_history_to_context = request.add_history_to_context
        agent.agent_metadata = request.agent_metadata
        
        # Update icon - preserve UUID if it's a UUID, otherwise use URL
        if request.agent_icon:
            # If it's a UUID, store it as-is (don't convert to blob_url)
            # This allows us to return full attachment data in GET responses
            if _is_uuid(request.agent_icon):
                # Validate that the attachment exists
                try:
                    result = await db.execute(
                        select(Attachment).where(
                            Attachment.id == UUID(request.agent_icon),
                            Attachment.is_active == True
                        )
                    )
                    attachment = result.scalar_one_or_none()
                    if not attachment:
                        raise HTTPException(
                            status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Attachment with ID {request.agent_icon} not found or is inactive. Please upload the file first using /api/attachments/upload"
                        )
                    # Store the UUID as-is
                    agent.icon = request.agent_icon
                except ValueError:
                    # Invalid UUID format, treat as URL
                    agent.icon = request.agent_icon
                except Exception as e:
                    logger.error(f"Failed to validate agent icon attachment: {str(e)}")
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Invalid attachment ID: {str(e)}"
                    )
            else:
                # It's a URL, use it directly
                agent.icon = request.agent_icon
        elif request.agent_icon_attachment_id:
            # Backward compatibility: validate and store UUID
            try:
                result = await db.execute(
                    select(Attachment).where(
                        Attachment.id == UUID(request.agent_icon_attachment_id),
                        Attachment.is_active == True
                    )
                )
                attachment = result.scalar_one_or_none()
                if attachment:
                    # Store the UUID as-is
                    agent.icon = request.agent_icon_attachment_id
                else:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Attachment with ID {request.agent_icon_attachment_id} not found or is inactive"
                    )
            except Exception as e:
                logger.error(f"Failed to fetch agent icon attachment: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid attachment ID: {str(e)}"
                )
        
        # Store agent_id to avoid lazy loading issues after potential rollbacks
        agent_internal_id = agent.id
        agent_public_id = agent.public_id
        
        # Set categories
        await _set_agent_categories(db, agent_internal_id, request.categories or [])
        
        # Set agent type if provided
        if request.agent_type is not None:
            await set_agent_type(db, agent_internal_id, request.agent_type)
        
        # Process custom feature icons
        toggle_data = request.custom_feature_toggle.dict() if request.custom_feature_toggle else None
        dropdown_data = request.custom_feature_dropdown.dict() if request.custom_feature_dropdown else None
        text_data = request.custom_feature_text.dict() if request.custom_feature_text else None
        
        # Process toggle field icons from fields array (preserve UUIDs, validate they exist)
        if toggle_data:
            fields = toggle_data.get("fields", [])
            for field in fields:
                field_icon = field.get("field_icon")
                if field_icon and _is_uuid(field_icon):
                    # Validate that the attachment exists, but preserve UUID
                    try:
                        result = await db.execute(
                            select(Attachment).where(
                                Attachment.id == UUID(field_icon),
                                Attachment.is_active == True
                            )
                        )
                        attachment = result.scalar_one_or_none()
                        if not attachment:
                            logger.warning(f"Toggle field icon attachment not found: {field_icon}")
                            field["field_icon"] = ""  # Clear invalid ID
                    except Exception as e:
                        logger.error(f"Failed to validate toggle field icon attachment {field_icon}: {str(e)}")
                        field["field_icon"] = ""  # Clear invalid ID
        
        # Process dropdown field and option icons from fields array (preserve UUIDs, validate they exist)
        if dropdown_data:
            fields = dropdown_data.get("fields", [])
            for field in fields:
                # Validate field icon
                field_icon = field.get("field_icon")
                if field_icon and _is_uuid(field_icon):
                    try:
                        result = await db.execute(
                            select(Attachment).where(
                                Attachment.id == UUID(field_icon),
                                Attachment.is_active == True
                            )
                        )
                        attachment = result.scalar_one_or_none()
                        if not attachment:
                            logger.warning(f"Dropdown field icon attachment not found: {field_icon}")
                            field["field_icon"] = ""  # Clear invalid ID
                    except Exception as e:
                        logger.error(f"Failed to validate dropdown field icon attachment {field_icon}: {str(e)}")
                        field["field_icon"] = ""  # Clear invalid ID
                
                # Validate option icons
                options = field.get("options", [])
                for option in options:
                    option_icon = option.get("option_icon")
                    if option_icon and _is_uuid(option_icon):
                        try:
                            result = await db.execute(
                                select(Attachment).where(
                                    Attachment.id == UUID(option_icon),
                                    Attachment.is_active == True
                                )
                            )
                            attachment = result.scalar_one_or_none()
                            if not attachment:
                                logger.warning(f"Dropdown option icon attachment not found: {option_icon}")
                                option["option_icon"] = ""  # Clear invalid ID
                        except Exception as e:
                            logger.error(f"Failed to validate dropdown option icon attachment {option_icon}: {str(e)}")
                            option["option_icon"] = ""  # Clear invalid ID
        
        # Backward compatibility: Process toggle field icons from deprecated array
        if toggle_data and request.toggle_field_icon_ids:
            fields = toggle_data.get("fields", [])
            for idx, field in enumerate(fields):
                if idx < len(request.toggle_field_icon_ids):
                    attachment_id = request.toggle_field_icon_ids[idx]
                    # Skip invalid UUIDs
                    if not attachment_id or not _is_uuid(attachment_id):
                        logger.debug(f"Skipping invalid toggle field icon attachment ID: {attachment_id}")
                        continue
                    try:
                        result = await db.execute(
                            select(Attachment).where(
                                Attachment.id == UUID(attachment_id),
                                Attachment.is_active == True
                            )
                        )
                        attachment = result.scalar_one_or_none()
                        if attachment:
                            field["field_icon"] = attachment.blob_url
                    except Exception as e:
                        logger.error(f"Failed to fetch toggle field icon attachment {attachment_id}: {str(e)}")
        
        # Process dropdown field and option icons
        if dropdown_data:
            fields = dropdown_data.get("fields", [])
            option_idx = 0
            
            # Process field icons
            if request.dropdown_field_icon_ids:
                for idx, field in enumerate(fields):
                    if idx < len(request.dropdown_field_icon_ids):
                        attachment_id = request.dropdown_field_icon_ids[idx]
                        # Skip invalid UUIDs
                        if not attachment_id or not _is_uuid(attachment_id):
                            logger.debug(f"Skipping invalid dropdown field icon attachment ID: {attachment_id}")
                            continue
                        try:
                            result = await db.execute(
                                select(Attachment).where(
                                    Attachment.id == UUID(attachment_id),
                                    Attachment.is_active == True
                                )
                            )
                            attachment = result.scalar_one_or_none()
                            if attachment:
                                field["field_icon"] = attachment.blob_url
                        except Exception as e:
                            logger.error(f"Failed to fetch dropdown field icon attachment {attachment_id}: {str(e)}")
            
            # Process option icons
            if request.dropdown_option_icon_ids:
                for field in fields:
                    options = field.get("options", [])
                    for opt in options:
                        if option_idx < len(request.dropdown_option_icon_ids):
                            attachment_id = request.dropdown_option_icon_ids[option_idx]
                            # Skip invalid UUIDs
                            if not attachment_id or not _is_uuid(attachment_id):
                                logger.debug(f"Skipping invalid dropdown option icon attachment ID: {attachment_id}")
                                option_idx += 1
                                continue
                            try:
                                result = await db.execute(
                                    select(Attachment).where(
                                        Attachment.id == UUID(attachment_id),
                                        Attachment.is_active == True
                                    )
                                )
                                attachment = result.scalar_one_or_none()
                                if attachment:
                                    opt["option_icon"] = attachment.blob_url
                                option_idx += 1
                            except Exception as e:
                                logger.error(f"Failed to fetch dropdown option icon attachment {attachment_id}: {str(e)}")
                                option_idx += 1
        
        # Set tools (use stored agent_internal_id to avoid lazy loading)
        await _set_agent_tools(db, agent_internal_id, request.tools)

        # Set custom features (instruction is now stored directly in agents table)
        await _set_custom_features(
            db=db,
            agent_id=agent_internal_id,
            toggle_data=toggle_data,
            dropdown_data=dropdown_data,
            text_data=text_data
        )
        
        # Commit changes
        await db.commit()

        # Invalidate agent_available cache
        if cache:
            new_version = await cache.invalidate_all()
            logger.info(f"Invalidated agent_available cache after agent update (version: {new_version})")

        # Re-query agent to ensure it's fully loaded (use stored agent_internal_id)
        result = await db.execute(
            select(Agent).where(Agent.id == agent_internal_id)
        )
        agent = result.scalar_one_or_none()
        
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Agent was updated but could not be retrieved. Agent ID: {agent_public_id}"
            )
        
        agent_response = await _build_agent_response(db, agent)
        
        # Log admin action: ADMIN_KNOWLEDGE_AGENT_UPDATED
        try:
            correlation_id = get_correlation_id() or str(uuid.uuid4())
            
            # Build body with agent properties (using correct field names from AgentResponse)
            body = {
                "id": agent_response.agent_id,
                "name": agent_response.agent_name,
                "description": agent_response.description,
                "intro": agent_response.agent_intro,
                "instruction": agent_response.instruction,
                "systemPrompt": None,  # Not available in AgentResponse
                "selectedAgentType": None,  # Not available in AgentResponse
                "isWebSearchEnabled": None,  # Not available in AgentResponse
                "isSystemPromptEnabled": None,  # Not available in AgentResponse
            }
            # Add groups/categories if available (categories is List[str] in AgentResponse)
            if agent_response.categories:
                body["groups"] = agent_response.categories
            
            admin_log_data = {
                "id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": "INFO",
                "action_type": "ADMIN_KNOWLEDGE_AGENT_UPDATED",
                "user_id": str(current_user.id),
                "email": current_user.email,
                "username": current_user.username or current_user.email,
                "correlation_id": correlation_id,
                "module": "admin_agents",
                "function": "update_agent",
                "message": f"Knowledge agent updated: {agent_response.agent_name}",
                "log_data": {
                    "action_type": "ADMIN_KNOWLEDGE_AGENT_UPDATED",
                    "id": agent_response.agent_id,
                    "body": body,
                }
            }
            
            # Write to PostgreSQL synchronously using the same db session
            await postgres_logs_service.write_admin_log(db, admin_log_data)
        except Exception as e:
            logger.error(f"Failed to write admin log for agent update: {e}", exc_info=True)
        
        return agent_response
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to update agent: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update agent: {str(e)}"
        )


@router.get("/analytics", response_model=AgentAnalyticsResponse)
async def get_agent_analytics(
    date_from: Optional[datetime] = Query(None, description="Filter analytics from this date (ISO format)"),
    date_to: Optional[datetime] = Query(None, description="Filter analytics to this date (ISO format)"),
    agent_name: Optional[str] = Query(None, description="Filter by agent name (partial match)"),
    type: Optional[str] = Query(None, description="Filter by agent type: 'user' or 'enterprise'"),
    sort_by: Optional[str] = Query(None, description="Sort by field: agent_name, usage, status, type"),
    sort_order: Optional[str] = Query("DESC", description="Sort order: ASC or DESC"),
    hide_super_agent: bool = Query(True, description="Hide Super Agent from results (default: true)"),
    page: int = Query(1, ge=1, description="Page number (1-indexed, default: 1)"),
    limit: int = Query(100, ge=1, le=1000, description="Number of records per page (default: 100, max: 1000)"),
    current_user: User = Depends(get_current_admin_user),  # Admin-only access enforced here
    db: AsyncSession = Depends(get_db)
) -> AgentAnalyticsResponse:
    """
    Get agent analytics with utilisation status heatmap (Admin Only).
    
    **Security:** This endpoint requires admin privileges. Non-admin users will receive a 403 Forbidden error.
    
    Returns a paginated list of agents with their status (enabled/disabled), type (user/enterprise), and usage statistics.
    Usage is calculated by counting how many times each agent_id appears in the agent_usage_metrics table.
    
    **Usage Calculation:**
    - Counts total occurrences of each agent_id in agent_usage_metrics table
    - Each row in agent_usage_metrics represents one usage/invocation
    - Example: If agent_id=1 appears 4 times in the table, usage count = 4
    
    **Historical Data Migration:**
    - To migrate historical data from messages table, run:
      `python -m scripts.migrate_historical_usage_to_metrics`
    
    **Query Parameters:**
    - `date_from`: Optional start date filter (ISO format)
    - `date_to`: Optional end date filter (ISO format)
    - `agent_name`: Optional filter by agent name (partial match)
    - `type`: Optional filter by agent type ('user' or 'enterprise')
    - `sort_by`: Optional sort field (agent_name, usage, status, type)
    - `sort_order`: Optional sort order (ASC or DESC, default: DESC)
    - `hide_super_agent`: Hide Super Agent from results (default: true)
    - `page`: Page number (1-indexed, default: 1)
    - `limit`: Number of records per page (default: 100, max: 1000)
    
    **Response:**
    Returns agent analytics including:
    - Agent ID, public ID, and name
    - Status (enabled/disabled)
    - Type (user/enterprise)
    - Usage count and formatted usage (e.g., "751K")
    - Total agents (before pagination) and total usage
    - Pagination info (total, page, limit, total_pages)
    """
    # Admin access is enforced by get_current_admin_user dependency above
    # This ensures only users with is_admin=True can access this endpoint
    try:
        correlation_id = get_correlation_id()
        logger.info(f"[{correlation_id}] Fetching agent analytics with date_from={date_from}, date_to={date_to}")
        
        # Preserve original dates for response
        response_date_from = date_from
        response_date_to = date_to
        
        # Process date_from
        if date_from:
            # Frontend sends UTC datetime already converted from local time
            # Simple UTC comparison: WHERE created_at >= date_from
            if date_from.tzinfo is not None:
                date_from = date_from.astimezone(timezone.utc).replace(tzinfo=None)
        
        # Process date_to
        if date_to:
            # Frontend sends UTC datetime already converted from local time
            # Simple UTC comparison: WHERE created_at <= date_to
            if date_to.tzinfo is not None:
                date_to = date_to.astimezone(timezone.utc).replace(tzinfo=None)
        
        # Build query for agents (excluding soft-deleted ones)
        agents_query = select(Agent).where(Agent.is_deleted == False)
        
        # Apply agent_name filter
        if agent_name:
            search_term = f"%{agent_name}%"
            agents_query = agents_query.where(Agent.name.ilike(search_term))
        
        # Apply type filter
        if type:
            type_lower = type.lower()
            if type_lower == "user" or type_lower == "user-agent":
                # User agents: category='user_agents' OR legacy_tags contains 'user_agents'
                # Support both 'user' and 'user-agent' for backward compatibility
                agents_query = agents_query.where(
                    or_(
                        Agent.category == "user_agents",
                        text("legacy_tags::jsonb @> '[\"user_agents\"]'::jsonb")
                    )
                )
            elif type_lower == "enterprise":
                # Enterprise agents: category='enterprise_agents' OR NOT (category='user_agents' OR legacy_tags contains 'user_agents')
                agents_query = agents_query.where(
                    or_(
                        Agent.category == "enterprise_agents",
                        and_(
                            or_(Agent.category.is_(None), Agent.category != "user_agents"),
                            or_(
                                Agent.legacy_tags.is_(None),
                                text("NOT (legacy_tags::jsonb @> '[\"user_agents\"]'::jsonb)")
                            )
                        )
                    )
                )
        
        agents_result = await db.execute(agents_query)
        agents = agents_result.scalars().all()
        
        # Filter out Super Agent if hide_super_agent is True
        if hide_super_agent:
            agents = [agent for agent in agents if agent.name != "Super Agent"]
        
        # Get usage counts from agent_usage_metrics table
        # Count how many times each agent_id appears in the table
        # Note: Historical data should be migrated using the migration script:
        # scripts/migrate_historical_usage_to_metrics.py
        usage_filters = []
        if date_from:
            usage_filters.append(TokenUsage.created_at >= date_from)
        if date_to:
            usage_filters.append(TokenUsage.created_at <= date_to)
            
        # Query to count occurrences of each agent_id and get last used timestamp
        usage_query = select(
            TokenUsage.agent_id,
            func.count(TokenUsage.id).label("usage_count"),
            func.max(TokenUsage.created_at).label("last_used")
        ).group_by(TokenUsage.agent_id)
        
        if usage_filters:
            usage_query = usage_query.where(and_(*usage_filters))
        
        usage_result = await db.execute(usage_query)
        usage_data = {row.agent_id: {"count": row.usage_count or 0, "last_used": row.last_used} for row in usage_result.all()}
        
        # Calculate total usage across ALL agents (ignoring agent_name and type filters)
        # This ensures total_usage always reflects the complete database usage
        total_usage_all_agents = sum(v['count'] for v in usage_data.values())
        
        logger.info(f"[{correlation_id}] Agent usage counts from agent_usage_metrics: {len(usage_data)} agents, total: {total_usage_all_agents}")
        
        # Format usage number to "K" format (e.g., 751000 -> "751K", 32 -> "32", 500 -> "0.5K")
        def format_usage(usage: int) -> str:
            if usage >= 1000:
                # Round to nearest thousand for large numbers
                k_value = round(usage / 1000)
                return f"{k_value}K"
            elif usage >= 100:
                # For numbers 100-999, show with one decimal place (e.g., 500 -> "0.5K")
                k_value = usage / 1000.0
                # Format to one decimal place, removing trailing zero if whole decimal
                formatted = f"{k_value:.1f}"
                if formatted.endswith('.0'):
                    return f"{int(k_value)}K"
                return f"{formatted}K"
            elif usage > 0:
                # For numbers less than 100, show as-is (e.g., 32 -> "32")
                return str(usage)
            else:
                # For zero, show as "0"
                return "0"
        
        # Build analytics items
        analytics_items = []
        filtered_total_usage = 0  # Track usage of filtered agents only (for debugging/logging)
        usage_counts = []  # Track all usage counts for max/min calculation
        
        # Check if date filters are applied
        has_date_filter = date_from is not None or date_to is not None
        
        for agent in agents:
            agent_usage_info = usage_data.get(agent.id, {"count": 0, "last_used": None})
            agent_usage = agent_usage_info["count"]
            agent_last_used = agent_usage_info["last_used"]
            
            # If date filter is applied, skip agents with zero usage
            # (they weren't used during the filtered period)
            if has_date_filter and agent_usage == 0:
                continue
            
            filtered_total_usage += agent_usage  # This is just for the filtered results
            usage_counts.append(agent_usage)  # Track usage for max/min
            
            # Resolve icon if it's a UUID to get the blob URL
            agent_icon = agent.icon
            if agent_icon and _is_uuid(agent_icon):
                resolved_icon = await _resolve_attachment_id_to_url(db, agent_icon)
                if resolved_icon:
                    agent_icon = resolved_icon
            
            # Determine agent type
            is_user_agent = False
            if agent.category == "user_agents":
                is_user_agent = True
            elif agent.legacy_tags:
                if isinstance(agent.legacy_tags, list) and "user_agents" in agent.legacy_tags:
                    is_user_agent = True
            
            agent_type = "user" if is_user_agent else "enterprise"
            
            analytics_items.append(
                AgentAnalyticsItem(
                    agent_id=str(agent.id),
                    agent_public_id=str(agent.public_id),
                    agent_name=agent.name,
                    agent_icon=agent_icon,
                    status="enabled" if agent.is_enabled else "disabled",
                    type=agent_type,
                    usage=agent_usage,
                    usage_formatted=format_usage(agent_usage),
                    last_used=agent_last_used
                )
            )
        
        # Apply sorting
        if sort_by:
            sort_by_lower = sort_by.lower()
            sort_order_upper = (sort_order or "DESC").upper()
            reverse = (sort_order_upper == "DESC")
            
            if sort_by_lower == "agent_name":
                analytics_items.sort(key=lambda x: x.agent_name.lower(), reverse=reverse)
            elif sort_by_lower == "usage":
                analytics_items.sort(key=lambda x: x.usage, reverse=reverse)
            elif sort_by_lower == "status":
                analytics_items.sort(key=lambda x: x.status, reverse=reverse)
            elif sort_by_lower == "type":
                analytics_items.sort(key=lambda x: x.type, reverse=reverse)
            else:
                # Default: sort by usage descending
                analytics_items.sort(key=lambda x: x.usage, reverse=True)
        else:
            # Default: sort by usage descending (highest usage first)
            analytics_items.sort(key=lambda x: x.usage, reverse=True)
        
        # Store total count before pagination
        total_agents_count = len(analytics_items)
        
        # Calculate max and min usage
        max_usage = max(usage_counts) if usage_counts else None
        min_usage = min(usage_counts) if usage_counts else None
        max_usage_formatted = format_usage(max_usage) if max_usage is not None else None
        min_usage_formatted = format_usage(min_usage) if min_usage is not None else None
        
        # Calculate pagination
        total_pages = (total_agents_count + limit - 1) // limit if total_agents_count > 0 and limit > 0 else 1
        offset = (page - 1) * limit
        
        # Apply pagination
        paginated_items = analytics_items[offset:offset + limit]
        
        logger.info(f"[{correlation_id}] Agent analytics fetched: {total_agents_count} total agents, returning {len(paginated_items)} (page {page}/{total_pages}, limit={limit})")
        logger.info(f"[{correlation_id}] Total usage (all agents): {total_usage_all_agents}, Filtered usage: {filtered_total_usage}")
        
        return AgentAnalyticsResponse(
            success=True,
            agents=paginated_items,
            total=total_agents_count,
            page=page,
            limit=limit,
            total_pages=total_pages,
            total_usage=total_usage_all_agents,  # Always show total usage across all agents
            max_usage=max_usage,
            min_usage=min_usage,
            max_usage_formatted=max_usage_formatted,
            min_usage_formatted=min_usage_formatted,
            date_from=response_date_from,
            date_to=response_date_to
        )
    
    except Exception as e:
        correlation_id = get_correlation_id()
        logger.error(f"[{correlation_id}] Error fetching agent analytics: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch agent analytics: {str(e)}"
        )


@router.get("/monitoring", response_model=AgentMonitoringResponse)
async def get_agent_monitoring(
    current_user: User = Depends(get_current_admin_user),  # Admin-only access enforced here
    db: AsyncSession = Depends(get_db)
) -> AgentMonitoringResponse:
    """
    Get agent monitoring status (Admin Only).
    
    **Security:** This endpoint requires admin privileges. Non-admin users will receive a 403 Forbidden error.
    
    Returns a list of active and enabled agents with their health status (healthy/unhealthy) and activity status (active).
    Only agents with status='active' and is_enabled=True are shown. Draft, inactive, and disabled agents are excluded.
    
    **Health Status:**
    - Determined by Celery periodic task that checks mcp_server_link and agent_health_url
    - Status codes 200 or 401 = healthy
    - Updated every 30 minutes (or configured interval) by Celery Beat
    - Stored in agents.health_status field
    
    **Activity Status:**
    - All agents in this endpoint will have activity_status='active' since only enabled agents are returned
    - Determined by agents.is_enabled field from the database (is_enabled=True)
    - Disabled agents (is_enabled=False) are excluded from monitoring results
    """
    try:
        correlation_id = get_correlation_id()
        logger.info(f"[{correlation_id}] Fetching agent monitoring data")
        
        # Get only active agents (excluding draft, inactive, disabled, and soft-deleted)
        # Only show agents with status='active' and is_enabled=True in monitoring
        agents_result = await db.execute(
            select(Agent).where(
                and_(
                    Agent.is_deleted == False,
                    Agent.status == "active",
                    Agent.is_enabled == True
                )
            ).order_by(Agent.name.asc())
        )
        agents = agents_result.scalars().all()
        
        logger.info(f"[{correlation_id}] Found {len(agents)} active agents for monitoring (excluding draft/inactive/disabled agents)")
        
        monitoring_items = []
        healthy_count = 0
        unhealthy_count = 0
        active_count = 0
        inactive_count = 0
        
        for agent in agents:
            # Resolve icon if it's a UUID to get the blob URL
            agent_icon = agent.icon
            if agent_icon and _is_uuid(agent_icon):
                resolved_icon = await _resolve_attachment_id_to_url(db, agent_icon)
                if resolved_icon:
                    agent_icon = resolved_icon
            
            # Special handling for Super Agent: always healthy and active if enabled and no MCP URL
            is_super_agent = agent.name == "Super Agent" and (not agent.mcp_url or agent.mcp_url.strip() == "")
            
            if is_super_agent:
                # Super Agent is always healthy and active if it exists and is enabled
                if agent.is_enabled:
                    health_status = "healthy"
                    activity_status = "active"
                    healthy_count += 1
                    active_count += 1
                else:
                    health_status = "unhealthy"
                    activity_status = "inactive"
                    unhealthy_count += 1
                    inactive_count += 1
                last_active = agent.last_used
            else:
                # Determine health status for regular agents
                # Use health_status from database (updated by Celery task)
                # If not set, default to "unknown"
                health_status = agent.health_status or "unknown"
                if health_status in ["healthy"]:
                    healthy_count += 1
                elif health_status in ["unhealthy", "unreachable"]:
                    unhealthy_count += 1
                
                # Determine activity status from agents table (is_enabled field)
                # Activity status comes from database, not calculated from usage
                # Active = agent is enabled in the database
                # Inactive = agent is disabled in the database
                if agent.is_enabled:
                    activity_status = "active"
                    active_count += 1
                else:
                    activity_status = "inactive"
                    inactive_count += 1
                
                last_active = agent.last_used
            
            monitoring_items.append(
                AgentMonitoringItem(
                    agent_id=str(agent.id),
                    agent_name=agent.name,
                    agent_icon=agent_icon,
                    health_status=health_status,
                    activity_status=activity_status,
                    last_active=last_active,
                    last_health_check=agent.last_health_check
                )
            )
        
        logger.info(f"[{correlation_id}] Agent monitoring data fetched: {len(monitoring_items)} agents")
        
        return AgentMonitoringResponse(
            success=True,
            agents=monitoring_items,
            total_agents=len(monitoring_items),
            healthy_count=healthy_count,
            unhealthy_count=unhealthy_count,
            active_count=active_count,
            inactive_count=inactive_count
        )
    
    except Exception as e:
        correlation_id = get_correlation_id()
        logger.error(f"[{correlation_id}] Error fetching agent monitoring: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch agent monitoring: {str(e)}"
        )


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
) -> AgentResponse:
    """Get Agent Configuration (Admin Only)."""
    try:
        result = await db.execute(
            select(Agent).where(
                and_(
                    Agent.public_id == UUID(agent_id),
                    Agent.is_deleted == False
                )
            )
        )
        agent = result.scalar_one_or_none()
        
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found"
            )
        
        return await _build_agent_response(db, agent)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get agent: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get agent: {str(e)}"
        )


@router.get("", response_model=PaginatedResponse[AgentListItemResponse])
async def list_agents(
    enabled: Optional[bool] = Query(None, description="Filter by enabled status"),
    category: Optional[str] = Query(None, description="Filter by category"),
    agent_type: Optional[str] = Query("enterprise", description="Filter by agent type: all, user-agent, enterprise (default: enterprise)"),
    search: Optional[str] = Query(None, description="Search in agent name, intro, description"),
    date_from: Optional[datetime] = Query(None, description="Filter agents created from this date (ISO format)"),
    date_to: Optional[datetime] = Query(None, description="Filter agents created to this date (ISO format)"),
    limit: int = Query(20, ge=1, le=100, description="Number of agents to return"),
    offset: int = Query(0, ge=0, description="Number of agents to skip"),
    sort_by: Optional[str] = Query(None, description="Sort by field: agent_name, status, last_used"),
    sort_order: Optional[str] = Query("DESC", description="Sort order: ASC or DESC"),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
) -> PaginatedResponse[AgentListItemResponse]:
    """List All Agents (Admin Only).
    
    Args:
        agent_type: Filter agents by type:
            - "all": Show all agents (user-agent and enterprise)
            - "user-agent": Show only user agents (category='user_agents' or legacy_tags contains 'user_agents')
            - "enterprise": Show only enterprise agents (default - excludes user agents)
    
    Note: This endpoint filters out soft-deleted agents (is_deleted=True).
    """
    try:
        query = select(Agent)
        
        # Filter out soft-deleted agents
        query = query.where(Agent.is_deleted == False)
        
        # Apply agent_type filter (default to enterprise)
        agent_type_filter = (agent_type or "enterprise").lower()
        if agent_type_filter == "user-agent":
            # User agents: category='user_agents' OR legacy_tags contains 'user_agents'
            # Use PostgreSQL @> operator for JSONB array containment
            query = query.where(
                or_(
                    Agent.category == "user_agents",
                    text("legacy_tags::jsonb @> '[\"user_agents\"]'::jsonb")
                )
            )
        elif agent_type_filter == "enterprise":
            # Enterprise agents: category='enterprise_agents' OR NOT (category='user_agents' OR legacy_tags contains 'user_agents')
            # Use PostgreSQL NOT @> operator to exclude user_agents
            query = query.where(
                or_(
                    Agent.category == "enterprise_agents",
                    and_(
                        or_(Agent.category.is_(None), Agent.category != "user_agents"),
                        or_(
                            Agent.legacy_tags.is_(None),
                            text("NOT (legacy_tags::jsonb @> '[\"user_agents\"]'::jsonb)")
                        )
                    )
                )
            )
        # If "all", no filter applied
        
        # Apply filters
        if enabled is not None:
            query = query.where(Agent.is_enabled == enabled)
        
        if category:
            try:
                query = query.join(AgentTag).where(
                    and_(
                        AgentTag.tag == category,
                        AgentTag.tag_type == "category",
                        AgentTag.is_active == True
                    )
                )
            except Exception:
                query = query.where(Agent.category == category)
        
        # Apply search filter
        if search:
            search_term = f"%{search}%"
            query = query.where(
                (Agent.name.ilike(search_term))
            )
        
        # Apply date filters - filter by last_used field instead of created_at
        if date_from or date_to:
            # Exclude NULL last_used values when date filter is applied
            query = query.where(Agent.last_used.isnot(None))
            
            if date_from:
                # Frontend sends UTC datetime already converted from local time
                # Simple UTC comparison: WHERE last_used >= date_from
                if date_from.tzinfo is not None:
                    date_from = date_from.astimezone(timezone.utc).replace(tzinfo=None)
                query = query.where(Agent.last_used >= date_from)
            
            if date_to:
                # Frontend sends UTC datetime already converted from local time
                # Simple UTC comparison: WHERE last_used <= date_to
                if date_to.tzinfo is not None:
                    date_to = date_to.astimezone(timezone.utc).replace(tzinfo=None)
                query = query.where(Agent.last_used <= date_to)
        
        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar()
        
        # Apply sorting
        order_by_clause = None
        if sort_by:
            sort_by_lower = sort_by.lower()
            sort_order_upper = (sort_order or "DESC").upper()
            
            # Validate sort_order
            if sort_order_upper not in ["ASC", "DESC"]:
                sort_order_upper = "DESC"
            
            # Map sort_by to model fields
            if sort_by_lower == "agent_name":
                field = Agent.name
                if sort_order_upper == "ASC":
                    order_by_clause = asc(field)
                else:
                    order_by_clause = desc(field)
            elif sort_by_lower == "status":
                # Sort by is_enabled field (status)
                field = Agent.is_enabled
                if sort_order_upper == "ASC":
                    order_by_clause = asc(field)
                else:
                    order_by_clause = desc(field)
            elif sort_by_lower == "last_used":
                # Sort by last_used field with nulls last
                field = Agent.last_used
                if sort_order_upper == "ASC":
                    order_by_clause = nullslast(asc(field))
                else:
                    order_by_clause = nullslast(desc(field))
            else:
                # Default to created_at if invalid sort_by value
                order_by_clause = desc(Agent.created_at)
        else:
            # Default sort by created_at desc
            order_by_clause = desc(Agent.created_at)
        
        # Apply pagination with sorting
        query = query.order_by(order_by_clause).offset(offset).limit(limit)
        
        result = await db.execute(query)
        agents = result.scalars().all()
        
        # Build responses with tools
        from aldar_middleware.models.agent_tools import AgentTool
        from aldar_middleware.schemas.admin_agents import AgentToolResponse, CustomFeatureTextResponse, CustomFeatureTextFieldResponse
        
        agent_responses = []
        for agent in agents:
            categories = await _get_agent_categories(db, agent.id)
            
            # Instruction is now stored directly in agents table
            instruction = agent.instruction
            
            # Get custom features
            features = await _get_custom_features(db, agent.id)
            
            # Helper function to resolve attachment data
            async def _resolve_attachment_data_list(attachment_id: Optional[str]) -> Optional[Dict[str, Any]]:
                """Resolve attachment ID to full attachment data."""
                if not attachment_id or not _is_uuid(attachment_id):
                    return None
                try:
                    result = await db.execute(
                        select(Attachment).where(
                            Attachment.id == UUID(attachment_id),
                            Attachment.is_active == True
                        )
                    )
                    attachment = result.scalar_one_or_none()
                    if attachment:
                        return {
                            "attachment_id": str(attachment.id),
                            "file_name": attachment.file_name,
                            "file_size": attachment.file_size,
                            "content_type": attachment.content_type,
                            "blob_url": attachment.blob_url,
                            "blob_name": attachment.blob_name,
                            "entity_type": attachment.entity_type,
                            "entity_id": attachment.entity_id,
                            "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
                        }
                except Exception as e:
                    logger.warning(f"Failed to fetch attachment {attachment_id}: {str(e)}")
                return None
            
            # Build toggle response
            from aldar_middleware.schemas.admin_agents import CustomFeatureToggleResponse, CustomFeatureToggleFieldResponse, CustomFeatureDropdownResponse, CustomFeatureDropdownFieldResponse, CustomFeatureDropdownOptionResponse
            toggle_response = None
            if features.get("toggle"):
                toggle_data = features["toggle"]
                toggle_fields = []
                for field in toggle_data.get("fields", []):
                    field_icon = field.get("field_icon")
                    field_icon_attachment = await _resolve_attachment_data_list(field_icon) if field_icon else None
                    toggle_fields.append(
                        CustomFeatureToggleFieldResponse(
                            field_id=field.get("field_id"),
                            field_name=field.get("field_name", ""),
                            is_default=field.get("is_default", False),
                            field_icon=field_icon,
                            field_icon_attachment=field_icon_attachment
                        )
                    )
                toggle_response = CustomFeatureToggleResponse(
                    enabled=toggle_data.get("enabled", False),
                    fields=toggle_fields
                )
            
            # Build dropdown response
            dropdown_response = None
            if features.get("dropdown"):
                dropdown_data = features["dropdown"]
                dropdown_fields = []
                for field in dropdown_data.get("fields", []):
                    field_icon = field.get("field_icon")
                    field_icon_attachment = await _resolve_attachment_data_list(field_icon) if field_icon else None
                    
                    # Process options
                    options = []
                    for opt in field.get("options", []):
                        option_icon = opt.get("option_icon")
                        option_icon_attachment = await _resolve_attachment_data_list(option_icon) if option_icon else None
                        options.append(
                            CustomFeatureDropdownOptionResponse(
                                option_id=opt.get("option_id"),
                                title_name=opt.get("title_name", ""),
                                value=opt.get("value", ""),
                                is_default=opt.get("is_default", False),
                                option_icon=option_icon,
                                option_icon_attachment=option_icon_attachment
                            )
                        )
                    
                    dropdown_fields.append(
                        CustomFeatureDropdownFieldResponse(
                            field_id=field.get("field_id"),
                            field_name=field.get("field_name", ""),
                            field_icon=field_icon,
                            field_icon_attachment=field_icon_attachment,
                            options=options
                        )
                    )
                dropdown_response = CustomFeatureDropdownResponse(
                    enabled=dropdown_data.get("enabled", False),
                    fields=dropdown_fields
                )
            
            # Build text response
            text_response = None
            if features.get("text"):
                text_data = features["text"]
                text_response = CustomFeatureTextResponse(
                    enabled=text_data.get("enabled", False),
                    fields=[
                        CustomFeatureTextFieldResponse(
                            field_id=field.get("field_id"),
                            field_name=field.get("field_name", ""),
                            field_value=field.get("field_value", "")
                        )
                        for field in text_data.get("fields", [])
                    ]
                )
            
            # Get tools for this agent - include both active and inactive tools
            # Filter by agent_id only, don't filter by tool_is_active to show all tools
            tools_result = await db.execute(
                select(AgentTool)
                .where(AgentTool.agent_id == agent.id)
                .order_by(AgentTool.tool_order)
            )
            tools = tools_result.scalars().all()
            
            tools_response = [
                AgentToolResponse(
                    tool_id=str(tool.id),
                    tool_name=tool.tool_name,
                    tool_description=tool.tool_description,
                    tool_url=tool.tool_url,
                    tool_icon=tool.tool_icon,
                    tool_color=tool.tool_color,
                    tool_order=tool.tool_order,
                    tool_is_active=tool.tool_is_active
                )
                for tool in tools
            ]
            
            # Resolve agent icon attachment if it's a UUID
            agent_icon_attachment = None
            agent_icon_value = agent.icon
            if agent_icon_value and _is_uuid(agent_icon_value):
                try:
                    result = await db.execute(
                        select(Attachment).where(
                            Attachment.id == UUID(agent_icon_value),
                            Attachment.is_active == True
                        )
                    )
                    attachment = result.scalar_one_or_none()
                    if attachment:
                        agent_icon_attachment = {
                            "attachment_id": str(attachment.id),
                            "file_name": attachment.file_name,
                            "file_size": attachment.file_size,
                            "content_type": attachment.content_type,
                            "blob_url": attachment.blob_url,
                            "blob_name": attachment.blob_name,
                            "entity_type": attachment.entity_type,
                            "entity_id": attachment.entity_id,
                            "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
                        }
                except Exception as e:
                    logger.warning(f"Failed to fetch agent icon attachment {agent_icon_value}: {str(e)}")
            
            # Build custom_header_toggle from agent_header for list response
            custom_header_toggle_response = None
            if agent.agent_header:
                # Convert dict to JSON string if needed
                header_value = agent.agent_header
                if isinstance(header_value, dict):
                    header_value = json.dumps(header_value)
                custom_header_toggle_response = CustomHeaderToggle(
                    enabled=True,
                    value=header_value
                )
            else:
                custom_header_toggle_response = CustomHeaderToggle(
                    enabled=False,
                    value=None
                )
            
            # Convert agent_header to JSON string if it's a dict
            agent_header_str = agent.agent_header
            if isinstance(agent_header_str, dict):
                agent_header_str = json.dumps(agent_header_str)
            
            agent_responses.append(AgentListItemResponse(
                agent_id=str(agent.public_id),
                agent_name=agent.name,
                agent_intro=agent.intro,
                agent_icon=agent_icon_value,
                agent_icon_attachment=agent_icon_attachment,
                mcp_server_link=agent.mcp_url,
                agent_health_url=agent.health_url,
                categories=categories,
                agent_enabled=agent.is_enabled,
                status="active" if agent.is_enabled else "inactive",
                description=agent.description,
                instruction=instruction,
                custom_feature_toggle=toggle_response,
                custom_feature_dropdown=dropdown_response,
                custom_feature_text=text_response,
                custom_header_toggle=custom_header_toggle_response,
                tools=tools_response,
                include_in_teams=agent.include_in_teams,
                agent_header=agent_header_str,
                agent_capabilities=agent.agent_capabilities,
                add_history_to_context=agent.add_history_to_context,
                agent_metadata=agent.agent_metadata,
                created_at=agent.created_at,
                updated_at=agent.updated_at,
                last_used=agent.last_used
            ))
        
        # Convert offset to page (1-based)
        page = (offset // limit) + 1 if limit > 0 else 1
        total_pages = (total + limit - 1) // limit if total > 0 else 0
        
        return PaginatedResponse(
            items=agent_responses,
            total=total,
            page=page,
            limit=limit,
            total_pages=total_pages,
        )
        
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to list agents: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list agents: {str(e)}"
        )


@router.get("/export/csv")
async def export_agents_csv(
    enabled: Optional[bool] = Query(None, description="Filter by enabled status"),
    category: Optional[str] = Query(None, description="Filter by category"),
    search: Optional[str] = Query(None, description="Search in agent name, intro, description"),
    date_from: Optional[datetime] = Query(None, description="Filter agents created from this date (ISO format)"),
    date_to: Optional[datetime] = Query(None, description="Filter agents created to this date (ISO format)"),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Export agents data as CSV (admin only).
    
    This endpoint exports ALL agents including soft-deleted ones.
    The CSV will include an 'is_deleted' column to indicate deleted agents.
    """
    try:
        query = select(Agent)
        
        # NOTE: Do NOT filter by is_deleted - export should include all agents
        # The is_deleted column will be included in the CSV output
        
        # Apply filters (same as list_agents endpoint)
        if enabled is not None:
            query = query.where(Agent.is_enabled == enabled)
        
        if category:
            try:
                query = query.join(AgentTag).where(
                    and_(
                        AgentTag.tag == category,
                        AgentTag.tag_type == "category",
                        AgentTag.is_active == True
                    )
                )
            except Exception:
                query = query.where(Agent.category == category)
        
        if search:
            search_term = f"%{search}%"
            query = query.where(
                (Agent.name.ilike(search_term)) |
                (Agent.intro.ilike(search_term)) |
                (Agent.description.ilike(search_term))
            )
        
        if date_from or date_to:
            query = query.where(Agent.last_used.isnot(None))
            if date_from:
                # Frontend sends UTC datetime already converted from local time
                # Simple UTC comparison: WHERE last_used >= date_from
                if date_from.tzinfo is not None:
                    date_from = date_from.astimezone(timezone.utc).replace(tzinfo=None)
                query = query.where(Agent.last_used >= date_from)
            if date_to:
                # Frontend sends UTC datetime already converted from local time
                # Simple UTC comparison: WHERE last_used <= date_to
                if date_to.tzinfo is not None:
                    date_to = date_to.astimezone(timezone.utc).replace(tzinfo=None)
                query = query.where(Agent.last_used <= date_to)
        
        # Get all agents (no pagination for export)
        result = await db.execute(query.order_by(desc(Agent.created_at)))
        agents = result.scalars().all()
        
        # Create CSV
        csv_buffer = io.StringIO()
        csv_writer = csv.DictWriter(
            csv_buffer,
            fieldnames=[
                "agent_id",
                "public_id",
                "agent_name",
                "agent_intro",
                "description",
                "agent_icon",
                "mcp_server_link",
                "agent_health_url",
                "model_name",
                "model_provider",
                "categories",
                "agent_enabled",
                "is_deleted",
                "is_healthy",
                "health_status",
                "last_health_check",
                "instruction",
                "include_in_teams",
                "custom_feature_toggle",
                "custom_feature_dropdown",
                "custom_feature_text",
                "tools",
                "knowledge_sources",
                "created_at",
                "updated_at",
                "last_used",
            ],
        )
        
        csv_writer.writeheader()
        for agent in agents:
            # Get categories
            categories = await _get_agent_categories(db, agent.id)
            categories_str = ", ".join(categories) if categories else ""
            
            # Instruction is now stored directly in agents table
            instruction = agent.instruction
            
            # Get custom features
            features = await _get_custom_features(db, agent.id)
            toggle_str = json.dumps(features.get("toggle", {})) if features.get("toggle") else ""
            dropdown_str = json.dumps(features.get("dropdown", {})) if features.get("dropdown") else ""
            text_str = json.dumps(features.get("text", {})) if features.get("text") else ""
            
            # Get tools
            from aldar_middleware.models.agent_tools import AgentTool
            tools_result = await db.execute(
                select(AgentTool)
                .where(AgentTool.agent_id == agent.id)
                .order_by(AgentTool.tool_order)
            )
            tools = tools_result.scalars().all()
            tools_list = [
                {
                    "tool_name": tool.tool_name,
                    "tool_description": tool.tool_description,
                    "tool_url": tool.tool_url,
                    "tool_is_active": tool.tool_is_active,
                }
                for tool in tools
            ]
            tools_str = json.dumps(tools_list) if tools_list else ""
            
            # Format knowledge sources
            knowledge_sources_str = json.dumps(agent.knowledge_sources) if agent.knowledge_sources else ""
            
            csv_writer.writerow({
                "agent_id": str(agent.id),
                "public_id": str(agent.public_id),
                "agent_name": agent.name or "",
                "agent_intro": (agent.intro or "").replace("\n", " ").replace("\r", ""),
                "description": (agent.description or "").replace("\n", " ").replace("\r", ""),
                "agent_icon": agent.icon or "",
                "mcp_server_link": agent.mcp_url or "",
                "agent_health_url": agent.health_url or "",
                "model_name": agent.model_name or "",
                "model_provider": agent.model_provider or "",
                "categories": categories_str,
                "agent_enabled": agent.is_enabled,
                "is_deleted": agent.is_deleted,
                "is_healthy": agent.is_healthy,
                "health_status": agent.health_status or "",
                "last_health_check": agent.last_health_check.isoformat() if agent.last_health_check else "",
                "instruction": (instruction or "").replace("\n", " ").replace("\r", ""),
                "include_in_teams": agent.include_in_teams,
                "custom_feature_toggle": toggle_str,
                "custom_feature_dropdown": dropdown_str,
                "custom_feature_text": text_str,
                "tools": tools_str,
                "knowledge_sources": knowledge_sources_str,
                "created_at": agent.created_at.isoformat() if agent.created_at else "",
                "updated_at": agent.updated_at.isoformat() if agent.updated_at else "",
                "last_used": agent.last_used.isoformat() if agent.last_used else "",
            })
        
        csv_data = csv_buffer.getvalue()
        
        logger.info(
            "Agents exported as CSV",
            extra={
                "user_id": str(current_user.id),
                "row_count": len(agents),
            },
        )
        
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=agents_export.csv"},
        )
    
    except Exception as e:
        logger.error(
            f"Failed to export agents: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to export agents",
        )


@router.delete("/{agent_id}", response_model=AgentDeleteResponse)
async def delete_agent(
    agent_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
    cache = Depends(get_cache_dependency)
) -> AgentDeleteResponse:
    """Delete Agent Configuration (Admin Only) - Soft Delete.
    
    This endpoint performs a soft delete by setting is_deleted=True.
    The agent will not be shown in GET APIs but will remain in the database
    and can be exported in CSV with the is_deleted flag.
    """
    try:
        result = await db.execute(
            select(Agent).where(Agent.public_id == UUID(agent_id))
        )
        agent = result.scalar_one_or_none()
        
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found"
            )
        
        if agent.is_deleted:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Agent is already deleted"
            )
        
        # Soft delete: Set is_deleted flag to True
        agent.is_deleted = True
        agent.updated_at = datetime.utcnow()
        
        await db.commit()

        # Invalidate agent_available cache
        if cache:
            new_version = await cache.invalidate_all()
            logger.info(f"Invalidated agent_available cache after agent soft deletion (version: {new_version})")

        logger.info(
            f"Agent soft deleted successfully",
            extra={
                "agent_id": agent_id,
                "agent_name": agent.name,
                "deleted_by": str(current_user.id)
            }
        )

        return AgentDeleteResponse(
            success=True,
            message="Agent deleted successfully (soft delete)"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to delete agent: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete agent: {str(e)}"
        )


@router.get("/{agent_id}/health", response_model=AgentHealthResponse)
async def get_agent_health(
    agent_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
) -> AgentHealthResponse:
    """Check Agent Health (Admin Only)."""
    try:
        result = await db.execute(
            select(Agent).where(Agent.public_id == UUID(agent_id))
        )
        agent = result.scalar_one_or_none()
        
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found"
            )
        
        health_status = "unreachable"
        mcp_server_status = None
        response_time_ms = None
        details = {}
        
        if agent.health_url:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    import time
                    start_time = time.time()
                    response = await client.get(agent.health_url)
                    response_time_ms = int((time.time() - start_time) * 1000)
                    
                    if response.status_code == 200:
                        health_status = "healthy"
                        mcp_server_status = "online"
                        details = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                    elif response.status_code == 503:
                        health_status = "degraded"
                        mcp_server_status = "degraded"
                    else:
                        health_status = "unhealthy"
                        mcp_server_status = "offline"
            except Exception as e:
                health_status = "unreachable"
                mcp_server_status = "offline"
                details = {"error": str(e)}
        else:
            health_status = "unknown"
        
        return AgentHealthResponse(
            success=True,
            agent_id=str(agent.public_id),
            health_status=health_status,
            mcp_server_status=mcp_server_status,
            response_time_ms=response_time_ms,
            last_checked=None,  # TODO: Store last_checked in database
            details=details
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to check agent health: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to check agent health: {str(e)}"
        )


@router.post("/monitoring/check-health", status_code=status.HTTP_202_ACCEPTED)
async def trigger_agent_health_check(
    current_user: User = Depends(get_current_admin_user),  # Admin-only access enforced here
) -> Dict[str, Any]:
    """
    Manually trigger agent health check (Admin Only).
    
    **Security:** This endpoint requires admin privileges. Non-admin users will receive a 403 Forbidden error.
    
    This endpoint triggers the Celery task to check health of all enabled agents immediately.
    The task runs asynchronously and updates agent health status in the database.
    """
    try:
        from aldar_middleware.queue.tasks import check_agent_health_periodic
        from celery.result import AsyncResult
        
        correlation_id = get_correlation_id()
        logger.info(f"[{correlation_id}] Manually triggering agent health check")
        
        # Trigger the Celery task asynchronously
        task = check_agent_health_periodic.delay()
        
        logger.info(f"[{correlation_id}] Task queued with ID: {task.id}")
        
        return {
            "success": True,
            "message": "Agent health check task has been triggered",
            "task_id": task.id,
            "status": "accepted",
            "note": "Check worker logs to see task execution. Task will check all enabled agents."
        }
    
    except Exception as e:
        correlation_id = get_correlation_id()
        logger.error(f"[{correlation_id}] Error triggering agent health check: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger agent health check: {str(e)}"
        )


@router.get("/monitoring/check-health/{task_id}", status_code=status.HTTP_200_OK)
async def get_health_check_status(
    task_id: str,
    current_user: User = Depends(get_current_admin_user),  # Admin-only access enforced here
) -> Dict[str, Any]:
    """
    Get status of agent health check task (Admin Only).
    
    **Security:** This endpoint requires admin privileges.
    
    Returns the current status and result of the health check task.
    """
    try:
        from celery.result import AsyncResult
        
        correlation_id = get_correlation_id()
        logger.info(f"[{correlation_id}] Checking task status for: {task_id}")
        
        # Get task result
        task_result = AsyncResult(task_id)
        
        response = {
            "task_id": task_id,
            "status": task_result.state,
            "ready": task_result.ready(),
        }
        
        if task_result.ready():
            if task_result.successful():
                response["result"] = task_result.result
                response["message"] = "Task completed successfully"
            else:
                response["error"] = str(task_result.info) if task_result.info else "Task failed"
                response["message"] = "Task failed"
        else:
            response["message"] = "Task is still processing"
            if task_result.info:
                response["info"] = task_result.info
        
        return response
    
    except Exception as e:
        correlation_id = get_correlation_id()
        logger.error(f"[{correlation_id}] Error checking task status: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to check task status: {str(e)}"
        )


@router.get("/categories", response_model=AgentCategoriesResponse)
async def get_agent_categories(
    enabled_only: bool = Query(False, description="Only include categories from enabled agents"),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
) -> AgentCategoriesResponse:
    """Get Agent Categories (Admin Only)."""
    try:
        # Query categories from agent_tags table
        try:
            query = select(AgentTag.tag, func.count(Agent.id).label("agent_count"))
            query = query.join(Agent, AgentTag.agent_id == Agent.id)
            query = query.where(
                and_(
                    AgentTag.tag_type == "category",
                    AgentTag.is_active == True
                )
            )
            
            if enabled_only:
                query = query.where(Agent.is_enabled == True)
            
            query = query.group_by(AgentTag.tag)
            
            result = await db.execute(query)
            rows = result.all()
            
            categories_dict = {}
            for row in rows:
                tag = row[0]
                agent_count = row[1]
                
                # Get enabled count
                enabled_query = select(func.count(Agent.id))
                enabled_query = enabled_query.join(AgentTag, AgentTag.agent_id == Agent.id)
                enabled_query = enabled_query.where(
                    and_(
                        AgentTag.tag == tag,
                        AgentTag.tag_type == "category",
                        AgentTag.is_active == True,
                        Agent.is_enabled == True
                    )
                )
                enabled_result = await db.execute(enabled_query)
                enabled_count = enabled_result.scalar() or 0
                
                # Get agents
                agents_query = select(Agent)
                agents_query = agents_query.join(AgentTag, AgentTag.agent_id == Agent.id)
                agents_query = agents_query.where(
                    and_(
                        AgentTag.tag == tag,
                        AgentTag.tag_type == "category",
                        AgentTag.is_active == True
                    )
                )
                if enabled_only:
                    agents_query = agents_query.where(Agent.is_enabled == True)
                
                agents_result = await db.execute(agents_query)
                agents = agents_result.scalars().all()
                
                agents_list = [
                    {
                        "agent_id": str(agent.public_id),
                        "agent_name": agent.name,
                        "agent_enabled": agent.is_enabled
                    }
                    for agent in agents
                ]
                
                categories_dict[tag] = {
                    "name": tag,
                    "agent_count": agent_count,
                    "enabled_count": enabled_count,
                    "agents": agents_list
                }
            
            categories_list = list(categories_dict.values())
            
        except Exception as e:
            await db.rollback()
            logger.warning(f"agent_tags table may not exist, using legacy fields: {str(e)}")
            
            # Fallback: use legacy category field
            query = select(Agent.category, func.count(Agent.id).label("agent_count"))
            query = query.where(Agent.category.isnot(None))
            
            if enabled_only:
                query = query.where(Agent.is_enabled == True)
            
            query = query.group_by(Agent.category)
            
            result = await db.execute(query)
            rows = result.all()
            
            categories_list = []
            for row in rows:
                category = row[0]
                agent_count = row[1]
                
                # Get enabled count
                enabled_query = select(func.count(Agent.id))
                enabled_query = enabled_query.where(
                    and_(
                        Agent.category == category,
                        Agent.is_enabled == True
                    )
                )
                enabled_result = await db.execute(enabled_query)
                enabled_count = enabled_result.scalar() or 0
                
                # Get agents
                agents_query = select(Agent).where(Agent.category == category)
                if enabled_only:
                    agents_query = agents_query.where(Agent.is_enabled == True)
                
                agents_result = await db.execute(agents_query)
                agents = agents_result.scalars().all()
                
                agents_list = [
                    {
                        "agent_id": str(agent.public_id),
                        "agent_name": agent.name,
                        "agent_enabled": agent.is_enabled
                    }
                    for agent in agents
                ]
                
                categories_list.append({
                    "name": category,
                    "agent_count": agent_count,
                    "enabled_count": enabled_count,
                    "agents": agents_list
                })
        
        # Get total agents count
        total_agents_query = select(func.count(Agent.id))
        if enabled_only:
            total_agents_query = total_agents_query.where(Agent.is_enabled == True)
        total_agents_result = await db.execute(total_agents_query)
        total_agents = total_agents_result.scalar() or 0
        
        return AgentCategoriesResponse(
            success=True,
            categories=[AgentCategoryInfo(**cat) for cat in categories_list],
            total_categories=len(categories_list),
            total_agents=total_agents
        )
        
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to get agent categories: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get agent categories: {str(e)}"
        )


@router.post("/mcp-validation", response_model=MCPValidationResponse, status_code=status.HTTP_200_OK)
async def validate_mcp_server(
    http_request: Request,
    request_data: MCPValidationRequest = Body(...),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
) -> MCPValidationResponse:
    """
    Validate MCP server configuration (Admin Only).
    
    This endpoint validates an MCP server by calling the validation API.
    It uses the Azure AD access token (obtained from the user's refresh token) 
    to authenticate with the validation service, NOT the OBO MCP token.
    
    **Request Body:**
    ```json
    {
      "name": "Weather MCP Agent",
      "intro": "you have access to weather details",
      "description": "Use this agent for all weather-related questions...",
      "instruction": "Use this agent for all weather-related questions...",
      "mcp_url": "https://adq-apim-v2.azure-api.net/weather-mcp-server/mcp",
      "transport": "streamable-http"
    }
    ```
    
    **Response:**
    Returns validation result including tool count and tool names.
    """
    try:
        # Get base URL from settings
        base_url = settings.agno_base_url
        if not base_url:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="ALDAR_AGNO_BASE_URL is not configured in environment variables"
            )
        
        # Get Azure AD user access token (not OBO token)
        # Auto-refresh from user's stored refresh token
        azure_ad_access_token = None
        if current_user.azure_ad_refresh_token:
            # AUTO-REFRESH: Get Azure AD access token from refresh token
            logger.info("🔄 Auto-refreshing Azure AD access token from stored refresh token...")
            try:
                from aldar_middleware.auth.azure_ad import azure_ad_auth
                
                # IMPORTANT: Include scope to get token with correct audience (app's client ID)
                # Without this, Azure AD defaults to Microsoft Graph (audience: 00000003-0000-0000-c000-000000000000)
                refresh_data = {
                    "client_id": azure_ad_auth.client_id,
                    "client_secret": azure_ad_auth.client_secret,
                    "refresh_token": current_user.azure_ad_refresh_token,
                    "grant_type": "refresh_token",
                    "scope": f"openid profile offline_access {azure_ad_auth.client_id}/.default"
                }
                
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{azure_ad_auth.authority}/oauth2/v2.0/token",
                        data=refresh_data
                    )
                    
                    if response.status_code == 200:
                        token_response = response.json()
                        azure_ad_access_token = token_response.get("access_token")
                        if azure_ad_access_token:
                            # SECURITY: Do not log token values, only metadata
                            logger.info("✓ Azure AD access token obtained from refresh token")
                            logger.debug(f"   Token length: {len(azure_ad_access_token)} characters")
                            # Update refresh token if new one provided
                            new_refresh_token = token_response.get("refresh_token")
                            if new_refresh_token:
                                current_user.azure_ad_refresh_token = new_refresh_token
                                await db.commit()
                        else:
                            logger.warning("⚠️  Refresh token response did not contain access_token")
                    else:
                        logger.warning(f"⚠️  Failed to auto-refresh Azure AD token: {response.status_code} - {response.text}")
            except Exception as e:
                logger.warning(f"⚠️  Failed to auto-refresh Azure AD token from refresh token: {e}")
        else:
            logger.warning("⚠️  No Azure AD refresh token stored for user")
        
        if not azure_ad_access_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Azure AD access token is required for MCP validation. Please ensure you have logged in via Azure AD."
            )
        
        # Exchange Azure AD token for OBO token (MCP token)
        mcp_obo_token = None
        try:
            from aldar_middleware.auth.obo_utils import exchange_token_obo
            logger.info("🔄 Exchanging Azure AD token for OBO token (MCP token)...")
            mcp_obo_token = await exchange_token_obo(azure_ad_access_token)
            logger.info("✓ OBO token (MCP token) obtained successfully")
            logger.debug(f"   OBO token length: {len(mcp_obo_token)} characters")
        except Exception as e:
            logger.warning(f"⚠️  Failed to exchange Azure AD token for OBO token: {e}")
            logger.warning("   Will proceed without OBO token, but mcp_token will not be available")
        
        # Extract JWT token from incoming request (this is now Azure AD token)
        auth_header = http_request.headers.get("Authorization")
        jwt_token_with_azure_ad = None
        
        if auth_header:
            try:
                from aldar_middleware.auth.obo_utils import decode_token_without_verification
                import jwt
                import time
                
                # Extract token from authorization header (could be Azure AD token or custom JWT)
                incoming_token = auth_header[7:] if auth_header.startswith("Bearer ") else auth_header
                
                # Decode to check what type of token it is
                try:
                    decoded_incoming = decode_token_without_verification(incoming_token)
                    # Get header to check algorithm
                    try:
                        import jwt as jwt_lib
                        header = jwt_lib.get_unverified_header(incoming_token)
                    except:
                        # Fallback: try to decode header manually
                        import base64
                        import json
                        parts = incoming_token.split('.')
                        if len(parts) >= 1:
                            header_data = base64.urlsafe_b64decode(parts[0] + '==')
                            header = json.loads(header_data)
                        else:
                            header = {"alg": "HS256"}  # Default assumption
                    
                    # Check if it's an Azure AD token (RS256) or custom JWT (HS256)
                    if header.get("alg") == "RS256":
                        # It's an Azure AD token - create custom JWT with both azure_ad_token and mcp_token
                        logger.info("🔄 Creating custom JWT from Azure AD token with azure_ad_token and mcp_token fields...")
                        
                        # Extract user info from Azure AD token
                        user_sub = decoded_incoming.get('sub') or decoded_incoming.get('oid')
                        user_email = (
                            decoded_incoming.get('email') or
                            decoded_incoming.get('preferred_username') or 
                            decoded_incoming.get('upn') or
                            'unknown@example.com'
                        )
                        exp = decoded_incoming.get('exp') or (int(time.time()) + 3600)
                        iat = decoded_incoming.get('iat') or int(time.time())
                        
                        # Create custom JWT payload with both azure_ad_token and mcp_token
                        custom_jwt_payload = {
                            "sub": user_sub,
                            "email": user_email,
                            "exp": exp,
                            "iat": iat,
                            "iss": "aldar-middleware",
                            "azure_ad_token": azure_ad_access_token,  # Azure AD token
                            "mcp_token": mcp_obo_token if mcp_obo_token else azure_ad_access_token  # OBO token or fallback to Azure AD
                        }
                        
                        # Create custom JWT (HS256)
                        jwt_token_with_azure_ad = jwt.encode(
                            custom_jwt_payload,
                            settings.jwt_secret_key,
                            algorithm=settings.jwt_algorithm
                        )
                        logger.info("✓ Created custom JWT with both azure_ad_token and mcp_token fields")
                        logger.info(f"   Has azure_ad_token: Yes")
                        logger.info(f"   Has mcp_token: {'Yes (OBO)' if mcp_obo_token else 'Yes (Azure AD fallback)'}")
                    else:
                        # It's a custom JWT - check if it has both fields, add if missing
                        logger.info("🔄 Incoming token is custom JWT, checking for azure_ad_token and mcp_token...")
                        
                        if "azure_ad_token" in decoded_incoming:
                            # JWT already has azure_ad_token
                            if "mcp_token" not in decoded_incoming or decoded_incoming.get("mcp_token") == decoded_incoming.get("azure_ad_token"):
                                # Update mcp_token to OBO token if available
                                decoded_incoming["mcp_token"] = mcp_obo_token if mcp_obo_token else decoded_incoming.get("azure_ad_token")
                                jwt_token_with_azure_ad = jwt.encode(
                                    decoded_incoming,
                                    settings.jwt_secret_key,
                                    algorithm=settings.jwt_algorithm
                                )
                                logger.info("✓ Updated mcp_token in existing JWT")
                            else:
                                # Both fields already exist
                                jwt_token_with_azure_ad = incoming_token
                                logger.info("✓ JWT already has both azure_ad_token and mcp_token")
                        else:
                            # JWT doesn't have azure_ad_token - add both fields
                            decoded_incoming["azure_ad_token"] = azure_ad_access_token
                            decoded_incoming["mcp_token"] = mcp_obo_token if mcp_obo_token else azure_ad_access_token
                            jwt_token_with_azure_ad = jwt.encode(
                                decoded_incoming,
                                settings.jwt_secret_key,
                                algorithm=settings.jwt_algorithm
                            )
                            logger.info("✓ Added both azure_ad_token and mcp_token to existing JWT")
                except Exception as e:
                    logger.warning(f"⚠️  Could not decode incoming token: {e}, creating new JWT with both fields")
                    # Create new JWT with both fields
                    user_sub = "unknown"
                    user_email = "unknown@example.com"
                    exp = int(time.time()) + 3600
                    iat = int(time.time())
                    
                    custom_jwt_payload = {
                        "sub": user_sub,
                        "email": user_email,
                        "exp": exp,
                        "iat": iat,
                        "iss": "aldar-middleware",
                        "azure_ad_token": azure_ad_access_token,
                        "mcp_token": mcp_obo_token if mcp_obo_token else azure_ad_access_token
                    }
                    
                    jwt_token_with_azure_ad = jwt.encode(
                        custom_jwt_payload,
                        settings.jwt_secret_key,
                        algorithm=settings.jwt_algorithm
                    )
                    logger.info("✓ Created new custom JWT with both azure_ad_token and mcp_token")
            except Exception as e:
                logger.warning(f"⚠️  Failed to create JWT with Azure AD token: {e}, will use Azure AD token directly")
                import traceback
                logger.warning(f"   Traceback: {traceback.format_exc()}")
                jwt_token_with_azure_ad = None
        
        # Build validation endpoint URL
        base_url_clean = base_url.rstrip("/")
        validation_url = f"{base_url_clean}/admin/mcp/validate"
        
        # Prepare request payload - exclude None values to avoid sending null to downstream API
        payload = request_data.model_dump(exclude_none=True)
        
        # Add Azure AD access token to request body (validation API needs it)
        if azure_ad_access_token:
            payload["azure_ad_token"] = azure_ad_access_token
            logger.info("✓ Added Azure AD access token to request body")
        
        # Add mcp_token (OBO token) to request body as well (validation API needs both)
        if mcp_obo_token:
            payload["mcp_token"] = mcp_obo_token
            logger.info("✓ Added mcp_token (OBO token) to request body")
        elif azure_ad_access_token:
            # Fallback: use Azure AD token as mcp_token if OBO exchange failed
            payload["mcp_token"] = azure_ad_access_token
            logger.warning("⚠️  Using Azure AD token as mcp_token (OBO exchange failed)")
        
        # CRITICAL FIX: Add MCP server headers to payload if not already provided
        # The external validation API needs the "headers" field in the request body
        # These headers will be used when connecting to the actual MCP server
        if "headers" not in payload or not payload.get("headers"):
            # Construct headers for MCP server connection
            mcp_server_headers = {
                "Content-Type": "application/json",
                "Accept": "application/json,text/event-stream"
            }
            
            # Add authorization header with mcp_token (OBO token)
            if mcp_obo_token:
                mcp_server_headers["Authorization"] = f"Bearer {{mcp_token}}"
                logger.info("✓ Added MCP server headers with Authorization Bearer {mcp_token} placeholder")
            elif azure_ad_access_token:
                mcp_server_headers["Authorization"] = f"Bearer {{mcp_token}}"
                logger.info("✓ Added MCP server headers with Authorization Bearer {mcp_token} placeholder (fallback)")
            
            payload["headers"] = mcp_server_headers
            logger.info(f"✓ Added MCP server headers to payload: {mcp_server_headers}")
        else:
            logger.info(f"✓ MCP server headers already provided in request: {payload['headers']}")
        
        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "accept": "application/json"
        }
        
        # Use JWT with azure_ad_token if available, otherwise use Azure AD token directly
        if jwt_token_with_azure_ad:
            headers["Authorization"] = f"Bearer {jwt_token_with_azure_ad}"
            logger.info("✓ Using JWT token with azure_ad_token embedded in Authorization header")
            logger.debug(f"   JWT token length: {len(jwt_token_with_azure_ad)} characters")
        elif settings.agno_api_key:
            headers["Authorization"] = f"Bearer {settings.agno_api_key}"
            logger.info("✓ Using AGNO API key in Authorization header")
        else:
            # Fallback: use Azure AD token directly
            headers["Authorization"] = f"Bearer {azure_ad_access_token}"
            logger.info("✓ Using Azure AD access token directly in Authorization header")
        
        logger.info(f"Validating MCP server: {request_data.mcp_url} via {validation_url}")
        logger.info(f"   Using JWT with azure_ad_token: {'Yes' if jwt_token_with_azure_ad else 'No'}")
        logger.info(f"   Azure AD access token length: {len(azure_ad_access_token)}")
        
        # TESTING: Log what's being sent in request body
        logger.info(f"🔑 [TESTING] Request body payload keys: {list(payload.keys())}")
        if "azure_ad_token" in payload:
            logger.info(f"🔑 [TESTING] azure_ad_token in payload: {payload['azure_ad_token'][:50]}...{payload['azure_ad_token'][-20:]}")
        if "mcp_token" in payload:
            logger.info(f"🔑 [TESTING] mcp_token in payload: {payload['mcp_token'][:50]}...{payload['mcp_token'][-20:]}")
        
        # TESTING: Log Authorization header token
        if "Authorization" in headers:
            auth_token = headers["Authorization"].replace("Bearer ", "")
            logger.info(f"🔑 [TESTING] Authorization header token: {auth_token[:50]}...{auth_token[-20:]}")
            # Decode and show payload
            try:
                from aldar_middleware.auth.obo_utils import decode_token_without_verification
                decoded = decode_token_without_verification(auth_token)
                logger.info(f"🔑 [TESTING] Authorization token payload: {decoded}")
                if "azure_ad_token" in decoded:
                    logger.info(f"🔑 [TESTING] JWT has azure_ad_token field: {decoded['azure_ad_token'][:50]}...")
                if "mcp_token" in decoded:
                    logger.info(f"🔑 [TESTING] JWT has mcp_token field: {decoded['mcp_token'][:50]}...")
            except Exception as e:
                logger.warning(f"🔑 [TESTING] Could not decode Authorization token: {e}")
        
        # Make request to validation API using configured timeout from settings
        # Use agno_api_timeout from settings (defaults to 10 seconds, can be configured via ALDAR_AGNO_API_TIMEOUT)
        timeout_seconds = settings.agno_api_timeout
        connect_timeout = min(10.0, timeout_seconds)  # Connection timeout should not exceed total timeout
        timeout = httpx.Timeout(timeout_seconds, connect=connect_timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.post(
                    validation_url,
                    json=payload,
                    headers=headers
                )
                
                # Check if request was successful
                if response.status_code != 200:
                    error_text = response.text
                    logger.error(f"MCP validation failed: {response.status_code} - {error_text}")
                    logger.error(f"Request URL: {validation_url}")
                    logger.error(f"Request headers: {headers}")
                    logger.error(f"Request payload keys: {list(payload.keys())}")
                    # Try to get more details from response
                    try:
                        error_json = response.json()
                        logger.error(f"Error response JSON: {error_json}")
                    except:
                        logger.error(f"Error response text (not JSON): {error_text[:500]}")
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"MCP validation failed: {error_text}"
                    )
                
                # Parse response
                response_data = response.json()
                
                # Validate response structure
                if not isinstance(response_data, dict):
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Invalid response format from validation API"
                    )
                
                # SECURITY: Do NOT include tokens in API responses
                # Tokens should only be returned by authentication endpoints
                # MCP validation should return validation status, not tokens
                if jwt_token_with_azure_ad or azure_ad_access_token:
                    logger.debug("Tokens available for MCP validation (not included in response for security)")
                    # Add a flag to indicate token is available/valid instead of returning the token
                    response_data["token_status"] = "valid"
                else:
                    response_data["token_status"] = "unavailable"
                
                # Return validation response
                return MCPValidationResponse(**response_data)
            except httpx.TimeoutException as e:
                logger.error(f"Timeout error validating MCP server: {str(e)} (timeout: {timeout_seconds}s)")
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail=f"Validation request timed out after {timeout_seconds} seconds. The validation service may be slow or unavailable."
                )
            
    except httpx.HTTPStatusError as e:
        error_detail = getattr(e.response, 'text', 'No error details available')
        logger.error(f"HTTP error validating MCP server: {e.response.status_code} - {error_detail}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Failed to validate MCP server: {error_detail}"
        )
    except httpx.RequestError as e:
        error_msg = str(e) or f"Connection error: {type(e).__name__}"
        logger.error(f"Request error validating MCP server: {error_msg}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to connect to validation service at {validation_url}. Please check if the service is available and {base_url} is correct."
        )
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Unexpected error validating MCP server: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to validate MCP server: {str(e)}"
        )


@router.post("/cache/invalidate")
async def invalidate_agent_cache(
    current_user: User = Depends(get_current_admin_user),
    cache = Depends(get_cache_dependency)
) -> Dict[str, Any]:
    """
    Invalidate Agent Available Cache (Admin Only).

    Manually invalidate the agent available cache by incrementing the global version.
    This forces all users to fetch fresh agent data on their next request.

    Useful for:
    - Testing cache behavior
    - Force refresh after bulk agent updates
    - Troubleshooting cache issues
    """
    try:
        if not cache:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Agent cache is not available (Redis might be down)"
            )

        # Invalidate the cache
        new_version = await cache.invalidate_all()

        logger.info(
            f"Admin {current_user.email} manually invalidated agent_available cache "
            f"(new version: {new_version})"
        )

        return {
            "success": True,
            "message": "Agent available cache invalidated successfully",
            "new_version": new_version,
            "invalidated_by": current_user.email,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to invalidate agent cache: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to invalidate cache: {str(e)}"
        )

