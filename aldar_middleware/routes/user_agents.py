"""User-facing agent API routes."""

import logging
from datetime import datetime
from typing import Optional, Dict, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, cast
from sqlalchemy.dialects.postgresql import JSONB

from aldar_middleware.database.base import get_db
from aldar_middleware.models.user import User
from aldar_middleware.models.menu import Agent, UserAgentPin
from aldar_middleware.models.sessions import Session
from aldar_middleware.models.agent_tags import AgentTag
from aldar_middleware.models.agent_configuration import AgentConfiguration
from aldar_middleware.models.attachment import Attachment
from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.services.rbac_pivot_service import RBACPivotService
from aldar_middleware.utils.helpers import is_uuid, resolve_attachment_data
from aldar_middleware.services.agent_available_cache import get_agent_available_cache
from aldar_middleware.schemas.admin_agents import (
    UserAvailableAgentsResponse,
    UserAvailableAgentResponse,
    CustomFeatureToggleResponse,
    CustomFeatureToggleFieldResponse,
    CustomFeatureDropdownResponse,
    CustomFeatureDropdownFieldResponse,
    CustomFeatureDropdownOptionResponse,
    CustomFeatureTextResponse,
    CustomFeatureTextFieldResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


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


async def _build_user_agent_response(db: AsyncSession, agent: Agent, agent_data: dict, is_pinned: bool = False, last_used: Optional[datetime] = None) -> UserAvailableAgentResponse:
    """Build user-facing agent response."""
    # Use pre-extracted agent data to avoid SQLAlchemy lazy loading issues
    # Use public_id as the primary agent_id for the response
    agent_id = agent_data["public_id"]
    agent_name = agent_data["name"]
    agent_intro = agent_data["intro"]
    agent_icon = agent_data["icon"]
    
    # Resolve agent icon attachment if it's a UUID
    agent_icon_attachment = None
    if agent_icon and is_uuid(agent_icon):
        agent_icon_attachment = await resolve_attachment_data(agent_icon, db)
    
    # Get categories from pre-extracted agent data
    categories = []
    if agent_data.get("legacy_tags") and isinstance(agent_data["legacy_tags"], list):
        categories = agent_data["legacy_tags"]
    elif agent_data.get("category"):
        categories = [agent_data["category"]]
    else:
        # Try agent_tags table if available (non-blocking)
        try:
            categories = await _get_agent_categories(db, agent_data["id"])
        except Exception:
            # If agent_tags table doesn't exist, that's fine - we already tried legacy fields
            pass
    
    features = await _get_custom_features(db, agent_data["id"])
    
    # Build toggle response with attachment data
    toggle_response = None
    if features.get("toggle"):
        toggle_data = features["toggle"]
        toggle_fields = []
        for field in toggle_data.get("fields", []):
            field_icon = field.get("field_icon")
            field_icon_attachment = await resolve_attachment_data(field_icon, db) if field_icon else None
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
    
    # Build dropdown response with attachment data
    dropdown_response = None
    if features.get("dropdown"):
        dropdown_data = features["dropdown"]
        dropdown_fields = []
        for field in dropdown_data.get("fields", []):
            field_icon = field.get("field_icon")
            field_icon_attachment = await resolve_attachment_data(field_icon, db) if field_icon else None
            
            # Process options with attachment data
            options = []
            for opt in field.get("options", []):
                option_icon = opt.get("option_icon")
                option_icon_attachment = await resolve_attachment_data(option_icon, db) if option_icon else None
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
    
    return UserAvailableAgentResponse(
        agent_id=agent_id,
        legacy_agent_id=agent_data.get("agent_id"),  # Legacy agent_id field
        agent_name=agent_name,
        agent_intro=agent_intro,
        agent_icon=agent_icon,
        agent_icon_attachment=agent_icon_attachment,
        categories=categories,
        custom_feature_toggle=toggle_response,
        custom_feature_dropdown=dropdown_response,
        custom_feature_text=text_response,
        is_pinned=is_pinned,
        lastUsed=last_used
    )


@router.get("/available", response_model=UserAvailableAgentsResponse)
async def get_available_agents(
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(20, ge=1, le=1000, description="Number of agents to return"),
    offset: int = Query(0, ge=0, description="Number of agents to skip"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cache = Depends(get_agent_available_cache)
) -> UserAvailableAgentsResponse:
    """
    Get Available Agents (User).
    
    Returns only enabled enterprise agents that the authenticated user has permission to access.
    - Only returns agents where agent_enabled: true and status: active
    - Excludes user agents (category='user_agents') - user agents are not shown here
    - Filters agents based on user's assigned permissions/roles via Azure AD RBAC
    - Does not expose sensitive admin fields
    """
    try:
        # Try cache first
        if cache:
            cached_response = await cache.get_cached_response(
                user_id=str(current_user.id),
                category=category,
                limit=limit,
                offset=offset
            )
            if cached_response:
                # Defensive stale-cache check: ensure cached agents are not soft-deleted
                # This handles cases where cache was populated before invalidation logic.
                try:
                    cached_agents = cached_response.get("agents", [])
                    cached_public_ids = []
                    for cached_agent in cached_agents:
                        cached_agent_id = cached_agent.get("agent_id")
                        if cached_agent_id and is_uuid(cached_agent_id):
                            cached_public_ids.append(UUID(cached_agent_id))

                    if cached_public_ids:
                        deleted_count_result = await db.execute(
                            select(func.count()).select_from(Agent).where(
                                and_(
                                    Agent.public_id.in_(cached_public_ids),
                                    Agent.is_deleted == True,
                                )
                            )
                        )
                        deleted_count = deleted_count_result.scalar() or 0
                        if deleted_count > 0:
                            logger.info(
                                f"Cache STALE: agent_available for user {current_user.id} contains {deleted_count} deleted agents; rebuilding"
                            )
                            cached_response = None
                except Exception as e:
                    logger.warning(f"Failed cache stale-check for user {current_user.id}: {e}")

            if cached_response:
                logger.info(f"Cache HIT: agent_available for user {current_user.id}")
                return UserAvailableAgentsResponse(**cached_response)
            logger.info(f"Cache MISS: agent_available for user {current_user.id}")

        # Start with query for enabled agents with ACTIVE status
        # Includes both enterprise agents and ACTIVE user agents
        # Excludes DRAFT agents and any non-ACTIVE status agents
        # Excludes soft-deleted agents
        query = select(Agent).where(
            and_(
                Agent.is_enabled == True,
                Agent.is_deleted == False,
                or_(
                    Agent.status.ilike('active'),  # Case-insensitive match for 'active'
                    Agent.status.is_(None)  # Include NULL status for backward compatibility
                )
            )
        )
        
        # Apply category filter if provided
        if category:
            try:
                # Try to filter by agent_tags table
                from aldar_middleware.models.agent_tags import AgentTag
                query = query.join(AgentTag).where(
                    and_(
                        AgentTag.tag == category,
                        AgentTag.tag_type == "category",
                        AgentTag.is_active == True
                    )
                )
            except Exception:
                # Fallback to legacy category field
                query = query.where(Agent.category == category)
        
        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total_count = total_result.scalar()
        
        # Apply pagination
        query = query.order_by(Agent.created_at.desc()).offset(offset).limit(limit + 1)
        
        result = await db.execute(query)
        agents = result.scalars().all()
        
        has_more = len(agents) > limit
        if has_more:
            agents = agents[:limit]
        
        # Extract all agent data immediately to avoid SQLAlchemy lazy loading issues
        # after potential rollbacks
        agent_data_list = []
        for agent in agents:
            # Extract all fields we need immediately while agent object is still valid
            agent_data = {
                "id": agent.id,
                "public_id": str(agent.public_id),
                "agent_id": agent.agent_id,  # Legacy UUID string field for user agents
                "name": agent.name,
                "intro": agent.intro,
                "icon": agent.icon,
                "legacy_tags": agent.legacy_tags,
                "category": agent.category,
            }
            agent_data_list.append((agent, agent_data))
        
        # Get user pinning information
        user_pins = {}
        user_last_used = {}
        if agent_data_list:
            agent_ids = [agent_data["id"] for _, agent_data in agent_data_list]
            pin_result = await db.execute(
                select(UserAgentPin)
                .where(
                    and_(
                        UserAgentPin.user_id == current_user.id,
                        UserAgentPin.agent_id.in_(agent_ids)
                    )
                )
            )
            user_pins = {pin.agent_id: pin for pin in pin_result.scalars().all()}
            
            # Get user-specific last_used from Session table
            # Query the most recent session for each agent-user combination
            session_result = await db.execute(
                select(
                    Session.agent_id,
                    func.max(
                        func.coalesce(
                            Session.last_message_interaction_at,
                            Session.updated_at,
                            Session.created_at
                        )
                    ).label('last_used')
                )
                .where(
                    and_(
                        Session.user_id == current_user.id,
                        Session.agent_id.in_(agent_ids),
                        Session.deleted_at.is_(None)  # Exclude soft-deleted sessions
                    )
                )
                .group_by(Session.agent_id)
            )
            user_last_used = {row.agent_id: row.last_used for row in session_result.all()}
        
        # Build responses with RBAC access checking (enterprise agents only)
        # Note: User agents are already filtered out at the query level
        agent_responses = []
        pivot_service = RBACPivotService(db)
        
        # Get user's email for RBAC check (use email as primary identifier)
        user_email = current_user.email or current_user.username
        
        # Get user agent access IDs (for user_agents category)
        from aldar_middleware.models.user_agent_access import UserAgentAccess
        user_agent_ids = [agent_data["id"] for _, agent_data in agent_data_list]
        user_agent_access_result = await db.execute(
            select(UserAgentAccess.agent_id)
            .where(
                and_(
                    UserAgentAccess.user_id == current_user.id,
                    UserAgentAccess.agent_id.in_(user_agent_ids),
                    UserAgentAccess.is_active == True
                )
            )
        )
        user_agent_access_ids = {row.agent_id for row in user_agent_access_result.all()}
        
        for agent, agent_data in agent_data_list:
            has_access = False
            
            # Check if this is a user agent
            is_user_agent = agent_data["category"] == "user_agents"
            if not is_user_agent and agent_data["legacy_tags"]:
                is_user_agent = "user_agents" in agent_data["legacy_tags"]
            
            if is_user_agent:
                # For user agents, check UserAgentAccess table
                has_access = agent_data["id"] in user_agent_access_ids
            else:
                # For enterprise agents, use RBAC (Azure AD groups)
                if user_email:
                    try:
                        has_access = await pivot_service.check_user_has_access_to_agent(
                            user_email, 
                            agent.name
                        )
                    except Exception as e:
                        logger.warning(
                            f"Error checking RBAC access for user '{user_email}' to agent '{agent.name}': {e}"
                        )
                        # If RBAC check fails, deny access by default
                        has_access = False
            
            # Only include agents the user has access to
            if has_access:
                # Get pin status for this agent
                user_pin = user_pins.get(agent_data["id"])
                is_pinned = user_pin.is_pinned if user_pin else False
                
                # Get user-specific last_used for this agent
                user_specific_last_used = user_last_used.get(agent_data["id"])
                
                agent_response = await _build_user_agent_response(db, agent, agent_data, is_pinned, user_specific_last_used)
                agent_responses.append(agent_response)
        
        # Get total enabled agents count for user permissions (exclude drafted agents)
        total_enabled_query = select(func.count()).select_from(Agent).where(
            and_(
                Agent.is_enabled == True,
                or_(
                    Agent.status.ilike('active'),
                    Agent.status.is_(None)
                )
            )
        )
        total_enabled_result = await db.execute(total_enabled_query)
        total_enabled_agents = total_enabled_result.scalar()
        
        if not agent_responses:
            response = UserAvailableAgentsResponse(
                success=False,
                agents=[],
                total_count=0,
                has_more=False,
                user_permissions={
                    "accessible_agents_count": 0,
                    "total_enabled_agents": total_enabled_agents
                }
            )
            # Cache the empty response
            if cache:
                response_dict = response.model_dump(mode='json')
                await cache.set_cached_response(
                    user_id=str(current_user.id),
                    category=category,
                    limit=limit,
                    offset=offset,
                    response_data=response_dict,
                    ttl=900  # 15 minutes
                )
                logger.info(f"Cache SET: agent_available for user {current_user.id} (empty response)")
            return response
        
        response = UserAvailableAgentsResponse(
            success=True,
            agents=agent_responses,
            total_count=total_count,
            has_more=has_more,
            user_permissions={
                "accessible_agents_count": len(agent_responses),
                "total_enabled_agents": total_enabled_agents
            }
        )

        # Cache the response
        if cache:
            response_dict = response.model_dump(mode='json')
            await cache.set_cached_response(
                user_id=str(current_user.id),
                category=category,
                limit=limit,
                offset=offset,
                response_data=response_dict,
                ttl=900  # 15 minutes
            )
            logger.info(f"Cache SET: agent_available for user {current_user.id}")

        return response
        
    except Exception as e:
        logger.error(f"Failed to get available agents: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get available agents: {str(e)}"
        )

