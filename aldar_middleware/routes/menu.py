"""Menu and navigation API endpoints."""

from typing import List, Optional, Dict, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.database.base import get_db
from aldar_middleware.models import User, Menu, LaunchpadApp, Agent, UserLaunchpadPin, UserAgentPin
from aldar_middleware.models.sessions import Session
from aldar_middleware.models.attachment import Attachment
from aldar_middleware.models.agent_configuration import AgentConfiguration
from aldar_middleware.models.user_agent_access import UserAgentAccess
from aldar_middleware.schemas.admin_agents import (
    CustomFeatureToggleResponse,
    CustomFeatureToggleFieldResponse,
    CustomFeatureDropdownResponse,
    CustomFeatureDropdownFieldResponse,
    CustomFeatureDropdownOptionResponse,
    CustomFeatureTextResponse,
    CustomFeatureTextFieldResponse,
)
from aldar_middleware.schemas.menu import (
    MenuResponse,
    LaunchpadAppResponse,
    AgentResponse,
    UserPinRequest,
    MenuListResponse,
    LaunchpadAppsResponse,
    AgentsResponse,
)
from aldar_middleware.services.rbac_pivot_service import RBACPivotService
from aldar_middleware.services.agent_available_cache import get_agent_available_cache
from aldar_middleware.utils.helpers import is_uuid, resolve_attachment_data

router = APIRouter(prefix="/menu", tags=["menu"])


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


async def _build_features_response(features: dict, db: AsyncSession) -> tuple:
    """Build feature responses with attachment data."""
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
    
    return toggle_response, dropdown_response, text_response


@router.get("/", response_model=MenuListResponse)
async def get_menus(
    db: AsyncSession = Depends(get_db)
):
    """Get all menu items."""
    result = await db.execute(
        select(Menu)
        .where(Menu.is_active == True)
        .order_by(Menu.order)
    )
    menus = result.scalars().all()
    
    return MenuListResponse(menus=[MenuResponse.from_orm(menu) for menu in menus])


@router.get("/launchpad", response_model=LaunchpadAppsResponse)
async def get_launchpad_apps(
    category: str = Query("all", description="Category filter: all, finance, trending"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get launchpad apps with user pinning information."""
    # Check if user has any pinned apps - if not, set default pins
    existing_pinned_result = await db.execute(
        select(UserLaunchpadPin).where(
            and_(
                UserLaunchpadPin.user_id == user.id,
                UserLaunchpadPin.is_pinned == True
            )
        )
    )
    existing_pinned = existing_pinned_result.scalars().all()
    
    # If user has no pinned apps, create default pins for: Workspaces, Data Camp, Oracle
    if not existing_pinned:
        default_app_ids = ["workspaces", "data-camp", "oracle"]
        default_apps_result = await db.execute(
            select(LaunchpadApp).where(
                and_(
                    LaunchpadApp.app_id.in_(default_app_ids),
                    LaunchpadApp.is_active == True
                )
            )
        )
        default_apps = default_apps_result.scalars().all()
        
        # Create pins for default apps (check for existing pins first to avoid duplicates)
        for order, app in enumerate(default_apps, start=1):
            # Check if pin already exists for this app
            existing_pin_result = await db.execute(
                select(UserLaunchpadPin).where(
                    and_(
                        UserLaunchpadPin.user_id == user.id,
                        UserLaunchpadPin.app_id == app.id
                    )
                )
            )
            existing_pin = existing_pin_result.scalar_one_or_none()
            
            if not existing_pin:
                user_pin = UserLaunchpadPin(
                    user_id=user.id,
                    app_id=app.id,
                    is_pinned=True,
                    order=order
                )
                db.add(user_pin)
            elif not existing_pin.is_pinned:
                # Update existing unpinned pin to pinned
                existing_pin.is_pinned = True
                existing_pin.order = order
        
        await db.commit()
    
    # Build query
    query = select(LaunchpadApp).where(LaunchpadApp.is_active == True)
    
    if category != "all":
        query = query.where(LaunchpadApp.category == category)
    
    query = query.order_by(LaunchpadApp.order)
    
    # Get apps
    result = await db.execute(query)
    apps = result.scalars().all()
    
    # Get user pinning information
    if apps:
        app_ids = [app.id for app in apps]
        pin_result = await db.execute(
            select(UserLaunchpadPin)
            .where(
                and_(
                    UserLaunchpadPin.user_id == user.id,
                    UserLaunchpadPin.app_id.in_(app_ids)
                )
            )
        )
        user_pins = {pin.app_id: pin for pin in pin_result.scalars().all()}
        
        # Build response with pinning info
        app_responses = []
        for app in apps:
            user_pin = user_pins.get(app.id)
            app_responses.append(LaunchpadAppResponse(
                id=app.app_id,
                title=app.title,
                subtitle=app.subtitle,
                description=app.description,
                tags=app.tags,
                logoSrc=app.logo_src,
                category=app.category,
                url=app.url,
                isPinned=user_pin.is_pinned if user_pin else False
            ))
    else:
        app_responses = []
    
    return LaunchpadAppsResponse(
        apps=app_responses,
        total=len(app_responses),
        category=category
    )


@router.get("/agents", response_model=AgentsResponse)
async def get_agents(
    category: str = Query("all", description="Category filter: all, procurement, risk-analysis"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get agents with user pinning information."""
    # Build query (include all enabled agents - active, draft, etc.)
    # Exclude soft-deleted agents
    query = select(Agent).where(
        and_(
            Agent.is_enabled == True,
            Agent.is_deleted == False
        )
    )
    
    if category != "all":
        query = query.where(Agent.category == category)
    
    query = query.order_by(Agent.order)
    
    # Get agents
    result = await db.execute(query)
    agents = result.scalars().all()

    # Get user agent access IDs for current user (for user_agents category)
    user_agent_access_ids = set()
    if agents:
        agent_ids = [agent.id for agent in agents]
        access_result = await db.execute(
            select(UserAgentAccess.agent_id)
            .where(
                and_(
                    UserAgentAccess.user_id == user.id,
                    UserAgentAccess.agent_id.in_(agent_ids),
                    UserAgentAccess.is_active == True
                )
            )
        )
        user_agent_access_ids = {row.agent_id for row in access_result.all()}

    # Get user pinning information
    if agents:
        agent_ids = [agent.id for agent in agents]
        pin_result = await db.execute(
            select(UserAgentPin)
            .where(
                and_(
                    UserAgentPin.user_id == user.id,
                    UserAgentPin.agent_id.in_(agent_ids)
                )
            )
        )
        user_pins = {pin.agent_id: pin for pin in pin_result.scalars().all()}
        
        # Get user-specific last_used and usage count from Session table
        # Query the most recent session and count for each agent-user combination
        session_result = await db.execute(
            select(
                Session.agent_id,
                func.max(
                    func.coalesce(
                        Session.last_message_interaction_at,
                        Session.updated_at,
                        Session.created_at
                    )
                ).label('last_used'),
                func.count(Session.id).label('usage_count')
            )
            .where(
                and_(
                    Session.user_id == user.id,
                    Session.agent_id.in_(agent_ids),
                    Session.deleted_at.is_(None)  # Exclude soft-deleted sessions
                )
            )
            .group_by(Session.agent_id)
        )
        session_stats = {row.agent_id: {'last_used': row.last_used, 'usage_count': row.usage_count} for row in session_result.all()}
        
        # Initialize RBAC service for permission checks
        rbac_service = RBACPivotService(db)
        
        # Build response with pinning info and permissions
        agent_responses = []
        for agent in agents:
            # Skip agents without required fields for menu display
            # Use public_id (UUID) as primary ID for consistency, fallback to legacy agent_id
            response_id = str(agent.public_id) if agent.public_id else agent.agent_id
            agent_title = agent.title or agent.name or "Untitled Agent"
            agent_category = agent.category or "all"
            
            # Skip if we still don't have a valid ID
            if not response_id:
                continue
            
            # Determine if this is a user agent category
            is_user_agent_category = agent_category == "user_agents"
            if not is_user_agent_category:
                # Also check legacy_tags for user_agents
                legacy_tags = agent.legacy_tags
                if legacy_tags and isinstance(legacy_tags, list) and "user_agents" in legacy_tags:
                    is_user_agent_category = True
            
            # Resolve logo attachment if icon/logo_src is a UUID
            # Prioritize icon (newer field) over logo_src (legacy field)
            logo_attachment = None
            logo_src = agent.icon or agent.logo_src
            if logo_src and is_uuid(logo_src):
                logo_attachment = await resolve_attachment_data(logo_src, db)
            
            user_pin = user_pins.get(agent.id)
            
            # Get user-specific stats (last_used and usage_count), fallback to defaults if user never used this agent
            agent_stats = session_stats.get(agent.id, {'last_used': None, 'usage_count': 0})
            user_specific_last_used = agent_stats['last_used']
            user_usage_count = agent_stats['usage_count']
            
            # Get custom features for this agent
            features = await _get_custom_features(db, agent.id)
            toggle_response, dropdown_response, text_response = await _build_features_response(features, db)

            # Check user permissions for this agent (dual access control)
            # is_user_agent_category is already computed above when determining agent_id
            has_permission = False
            if is_user_agent_category:
                # For user agents, check UserAgentAccess table
                has_permission = agent.id in user_agent_access_ids
            else:
                # For enterprise agents, use RBAC (Azure AD groups)
                if agent.name:
                    try:
                        has_permission = await rbac_service.check_user_has_access_to_agent(
                            user_name=user.email,
                            agent_name=agent.name
                        )
                    except Exception:
                        # If permission check fails, default to False
                        has_permission = False

            # Skip agents without permission
            if not has_permission:
                continue

            agent_responses.append(AgentResponse(
                id=response_id,
                agent_id=agent.agent_id,  # Legacy agent_id field
                title=agent_title,
                subtitle=agent.subtitle,
                description=agent.description,
                agent_intro=agent.intro,
                tags=agent.legacy_tags,
                logoSrc=logo_src,
                logoAttachment=logo_attachment,
                category=agent_category,
                status=agent.status or "active",
                methods=agent.methods,
                lastUsed=user_specific_last_used,  # Use user-specific last_used instead of overall
                usageCount=user_usage_count,  # Number of sessions the user has with this agent
                isPinned=user_pin.is_pinned if user_pin else False,
                hasPermission=has_permission,
                custom_feature_toggle=toggle_response,
                custom_feature_dropdown=dropdown_response,
                custom_feature_text=text_response
            ))
    else:
        agent_responses = []
    
    return AgentsResponse(
        agents=agent_responses,
        total=len(agent_responses),
        category=category
    )


@router.post("/launchpad/{app_id}/pin", response_model=dict)
async def toggle_launchpad_pin(
    app_id: str,
    pin_request: UserPinRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Toggle pin status for a launchpad app."""
    # Get the app
    app_result = await db.execute(
        select(LaunchpadApp).where(LaunchpadApp.app_id == app_id)
    )
    app = app_result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    
    # Check if pin already exists - handle duplicates
    pin_result = await db.execute(
        select(UserLaunchpadPin).where(
            and_(
                UserLaunchpadPin.user_id == user.id,
                UserLaunchpadPin.app_id == app.id
            )
        )
    )
    all_pins = pin_result.scalars().all()
    
    # Handle duplicate pins - keep the first one (prefer pinned if exists), delete others
    user_pin = None
    if all_pins:
        # Prefer a pinned pin if any exist, otherwise use the first one
        pinned_pin = next((p for p in all_pins if p.is_pinned), None)
        user_pin = pinned_pin if pinned_pin else all_pins[0]
        
        # Delete duplicate pins
        for pin in all_pins:
            if pin.id != user_pin.id:
                db.delete(pin)
        await db.flush()
    
    # If trying to pin, check if user has reached the maximum limit of 10 pinned apps
    if pin_request.is_pinned:
        # Check if this is a new pin or updating an existing unpinned pin
        is_new_pin = user_pin is None or not user_pin.is_pinned
        
        if is_new_pin:
            # Count currently pinned apps for this user (excluding the current app if it exists)
            count_query = select(func.count(UserLaunchpadPin.id)).where(
                and_(
                    UserLaunchpadPin.user_id == user.id,
                    UserLaunchpadPin.is_pinned == True
                )
            )
            # Exclude current app from count if it exists
            if user_pin:
                count_query = count_query.where(UserLaunchpadPin.app_id != app.id)
            
            count_result = await db.execute(count_query)
            pinned_count = count_result.scalar() or 0
            
            # If user already has 10 pinned apps and trying to pin a new one, raise error
            if pinned_count >= 10:
                raise HTTPException(
                    status_code=400,
                    detail="Maximum 10 apps can be pinned. Unpin an app to add a new one."
                )
    
    if user_pin:
        # Update existing pin
        user_pin.is_pinned = pin_request.is_pinned
        if pin_request.is_pinned:
            # Set order for pinned items
            max_order_result = await db.execute(
                select(UserLaunchpadPin.order)
                .where(UserLaunchpadPin.user_id == user.id)
                .order_by(UserLaunchpadPin.order.desc())
                .limit(1)
            )
            max_order = max_order_result.scalar_one_or_none() or 0
            user_pin.order = max_order + 1
    else:
        # Create new pin
        user_pin = UserLaunchpadPin(
            user_id=user.id,
            app_id=app.id,
            is_pinned=pin_request.is_pinned,
            order=1 if pin_request.is_pinned else 0
        )
        db.add(user_pin)
    
    await db.commit()
    await db.refresh(user_pin)
    
    return {"message": f"App {'pinned' if pin_request.is_pinned else 'unpinned'} successfully"}


@router.post("/agents/{agent_id}/pin", response_model=dict)
async def toggle_agent_pin(
    agent_id: str,
    pin_request: UserPinRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Toggle pin status for an agent."""
    # Get the agent - try both agent_id (legacy) and public_id (new)
    # The agent_id parameter could be either the legacy agent_id or the public_id UUID
    try:
        # Try to parse as UUID first (for public_id)
        agent_uuid = UUID(agent_id)
        agent_result = await db.execute(
            select(Agent).where(
                and_(
                    or_(
                        Agent.agent_id == agent_id,
                        Agent.public_id == agent_uuid
                    ),
                    Agent.is_deleted == False
                )
            )
        )
    except ValueError:
        # Not a valid UUID, try legacy agent_id only
        agent_result = await db.execute(
            select(Agent).where(
                and_(
                    Agent.agent_id == agent_id,
                    Agent.is_deleted == False
                )
            )
        )
    
    agent = agent_result.scalar_one_or_none()
    
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Check if pin already exists
    pin_result = await db.execute(
        select(UserAgentPin).where(
            and_(
                UserAgentPin.user_id == user.id,
                UserAgentPin.agent_id == agent.id
            )
        )
    )
    user_pin = pin_result.scalar_one_or_none()
    
    if user_pin:
        # Update existing pin
        user_pin.is_pinned = pin_request.is_pinned
        if pin_request.is_pinned:
            # Set order for pinned items
            max_order_result = await db.execute(
                select(UserAgentPin.order)
                .where(UserAgentPin.user_id == user.id)
                .order_by(UserAgentPin.order.desc())
                .limit(1)
            )
            max_order = max_order_result.scalar_one_or_none() or 0
            user_pin.order = max_order + 1
    else:
        # Create new pin
        user_pin = UserAgentPin(
            user_id=user.id,
            agent_id=agent.id,
            is_pinned=pin_request.is_pinned,
            order=1 if pin_request.is_pinned else 0
        )
        db.add(user_pin)
    
    await db.commit()
    await db.refresh(user_pin)

    # Invalidate cached /api/v1/agent/available responses so pin state is fresh
    cache = get_agent_available_cache()
    if cache:
        await cache.invalidate_all()
    
    return {"message": f"Agent {'pinned' if pin_request.is_pinned else 'unpinned'} successfully"}


@router.get("/launchpad/pinned", response_model=LaunchpadAppsResponse)
async def get_pinned_launchpad_apps(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get user's pinned launchpad apps."""
    result = await db.execute(
        select(LaunchpadApp, UserLaunchpadPin)
        .join(UserLaunchpadPin, LaunchpadApp.id == UserLaunchpadPin.app_id)
        .where(
            and_(
                UserLaunchpadPin.user_id == user.id,
                UserLaunchpadPin.is_pinned == True,
                LaunchpadApp.is_active == True
            )
        )
        .order_by(UserLaunchpadPin.order)
    )
    
    apps_with_pins = result.all()
    app_responses = []
    
    for app, user_pin in apps_with_pins:
        app_responses.append(LaunchpadAppResponse(
            id=app.app_id,
            title=app.title,
            subtitle=app.subtitle,
            description=app.description,
            tags=app.tags,
            logoSrc=app.logo_src,
            category=app.category,
            url=app.url,
            isPinned=True
        ))
    
    return LaunchpadAppsResponse(
        apps=app_responses,
        total=len(app_responses),
        category="pinned"
    )


@router.get("/agents/pinned", response_model=AgentsResponse)
async def get_pinned_agents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get user's pinned agents."""
    result = await db.execute(
        select(Agent, UserAgentPin)
        .join(UserAgentPin, Agent.id == UserAgentPin.agent_id)
        .where(
            and_(
                UserAgentPin.user_id == user.id,
                UserAgentPin.is_pinned == True,
                Agent.is_enabled == True
            )
        )
        .order_by(UserAgentPin.order)
    )
    
    agents_with_pins = result.all()
    agent_responses = []

    # Get user agent access IDs for current user (for user_agents category)
    pinned_agent_ids = [agent.id for agent, _ in agents_with_pins]
    user_agent_access_ids = set()
    if pinned_agent_ids:
        access_result = await db.execute(
            select(UserAgentAccess.agent_id)
            .where(
                and_(
                    UserAgentAccess.user_id == user.id,
                    UserAgentAccess.agent_id.in_(pinned_agent_ids),
                    UserAgentAccess.is_active == True
                )
            )
        )
        user_agent_access_ids = {row.agent_id for row in access_result.all()}

    # Get user-specific last_used from Session table for pinned agents
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
                Session.user_id == user.id,
                Session.agent_id.in_(pinned_agent_ids),
                Session.deleted_at.is_(None)  # Exclude soft-deleted sessions
            )
        )
        .group_by(Session.agent_id)
    )
    user_last_used = {row.agent_id: row.last_used for row in session_result.all()}
    
    # Initialize RBAC service for permission checks
    rbac_service = RBACPivotService(db)
    
    for agent, user_pin in agents_with_pins:
        # Use public_id (UUID) as primary ID for consistency, fallback to legacy agent_id
        agent_id = str(agent.public_id) if agent.public_id else agent.agent_id
        agent_title = agent.title or agent.name or "Untitled Agent"
        agent_category = agent.category or "all"
        
        # Skip if we still don't have a valid ID
        if not agent_id:
            continue
        
        # Resolve logo attachment if icon/logo_src is a UUID
        # Prioritize icon (newer field) over logo_src (legacy field)
        logo_attachment = None
        logo_src = agent.icon or agent.logo_src
        if logo_src and is_uuid(logo_src):
            logo_attachment = await resolve_attachment_data(logo_src, db)
        
        # Get user-specific last_used, fallback to None if user never used this agent
        user_specific_last_used = user_last_used.get(agent.id)
        
        # Get custom features for this agent
        features = await _get_custom_features(db, agent.id)
        toggle_response, dropdown_response, text_response = await _build_features_response(features, db)

        # Check user permissions for this agent (dual access control)
        # Check if this is a user agent - check both category field and legacy_tags
        is_user_agent = False
        if agent_category == "user_agents":
            is_user_agent = True
        legacy_tags = agent.legacy_tags
        if legacy_tags and isinstance(legacy_tags, list) and "user_agents" in legacy_tags:
            is_user_agent = True

        has_permission = False
        if is_user_agent:
            # For user agents, check UserAgentAccess table
            has_permission = agent.id in user_agent_access_ids
        else:
            # For enterprise agents, use RBAC (Azure AD groups)
            if agent.name:
                try:
                    has_permission = await rbac_service.check_user_has_access_to_agent(
                        user_name=user.email,
                        agent_name=agent.name
                    )
                except Exception:
                    # If permission check fails, default to False
                    has_permission = False

        # Skip agents without permission
        if not has_permission:
            continue

        agent_responses.append(AgentResponse(
            id=agent_id,
            title=agent_title,
            subtitle=agent.subtitle,
            description=agent.description,
            tags=agent.legacy_tags,
            logoSrc=logo_src,
            logoAttachment=logo_attachment,
            category=agent_category,
            status=agent.status or "active",
            methods=agent.methods,
            lastUsed=user_specific_last_used,  # Use user-specific last_used instead of overall
            isPinned=True,
            hasPermission=has_permission,
            custom_feature_toggle=toggle_response,
            custom_feature_dropdown=dropdown_response,
            custom_feature_text=text_response
        ))
    
    return AgentsResponse(
        agents=agent_responses,
        total=len(agent_responses),
        category="pinned"
    )
