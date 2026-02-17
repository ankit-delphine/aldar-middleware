"""Admin routes for user and system management."""

import csv
import io
import logging
import asyncio
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select, and_, or_, asc, desc, nullslast, exists, cast, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aldar_middleware.admin.schemas import (
    UserResponse,
    UserUpdate,
    UserAgentCreate,
    UserAgentUpdate,
    UserAgentResponse,
    UserPermissionCreate,
    UserPermissionUpdate,
    UserPermissionResponse,
    LogQueryRequest,
    LogEntryResponse,
    LogsResponse,
    AdminLogEventResponse,
    AzureADSyncRequest,
    AzureADSyncResponse,
    AzureADSyncResult,
    AzureADGroupsResponse,
    UserAgentInfo,
    GroupResponse,
)
from aldar_middleware.schemas.feedback import PaginatedResponse
from aldar_middleware.auth.dependencies import get_current_admin_user
from aldar_middleware.database.base import get_db, async_session
from aldar_middleware.models import User, UserGroupMembership, UserAgent, UserPermission, UserGroup
from aldar_middleware.services.logs_service import LogsService
from aldar_middleware.services.postgres_logs_service import postgres_logs_service
from aldar_middleware.services.azure_ad_sync import AzureADSyncService
from aldar_middleware.settings import settings
from aldar_middleware.settings.context import get_correlation_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# User Management Endpoints
@router.get("/users", response_model=PaginatedResponse[UserResponse])
async def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    is_admin: Optional[bool] = Query(None, description="Filter by admin status"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    is_verified: Optional[bool] = Query(None, description="Filter by verified status"),
    user_id: Optional[UUID] = Query(None, description="Filter by specific user ID"),
    username: Optional[str] = Query(None, description="Filter by exact username"),
    email: Optional[str] = Query(None, description="Filter by exact email"),
    azure_ad_id: Optional[str] = Query(None, description="Filter by Azure AD ID"),
    search: Optional[str] = Query(None, description="Search in email, username, first_name, last_name"),
    date_from: Optional[datetime] = Query(None, description="Filter users by last login from this date (ISO format)"),
    date_to: Optional[datetime] = Query(None, description="Filter users by last login to this date (ISO format)"),
    sort_by: Optional[str] = Query(None, description="Sort by field: username, department, full_name, email, date, role, last_active"),
    sort_order: Optional[str] = Query("ASC", description="Sort order: ASC or DESC"),
    include_groups: bool = Query(False, description="Include user groups (slower, use /users/{user_id}/groups for specific user)"),
    include_agents: bool = Query(True, description="Include user agents (default: True)"),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[UserResponse]:
    """List all users (admin only). 
    
    By default, agents are included but groups are excluded for better performance.
    Use include_groups=True to include groups, or use /users/{user_id}/groups endpoint 
    for specific user groups.
    """
    from sqlalchemy import func
    
    # Only load group memberships if groups are needed
    if include_groups:
        query = select(User).options(
            selectinload(User.group_memberships).selectinload(UserGroupMembership.group),
        )
    else:
        query = select(User)
    
    # Apply filters
    if is_admin is not None:
        query = query.where(User.is_admin == is_admin)
    
    if is_active is not None:
        query = query.where(User.is_active == is_active)
    
    if is_verified is not None:
        query = query.where(User.is_verified == is_verified)
    
    if user_id is not None:
        query = query.where(User.id == user_id)
    
    if username is not None:
        query = query.where(User.username == username)
    
    if email is not None:
        query = query.where(User.email == email)
    
    if azure_ad_id is not None:
        query = query.where(User.azure_ad_id == azure_ad_id)
    
    if search:
        from aldar_middleware.models.user_agent_access import UserAgentAccess
        from aldar_middleware.models.menu import Agent
        
        search_term = f"%{search}%"
        
        # Search in user fields
        user_search_conditions = [
            User.email.ilike(search_term),
            User.username.ilike(search_term),
            User.first_name.ilike(search_term),
            User.last_name.ilike(search_term),
            User.full_name.ilike(search_term),
            User.azure_department.ilike(search_term)
        ]
        
        # Collect all user IDs that have matching agents
        matching_user_ids = set()
        
        # 1. Search in agent names via UserAgentAccess table (direct assignments)
        # Find agents matching the search term that are active and not deleted
        user_agent_access_query = select(UserAgentAccess.user_id.distinct()).join(
            Agent, UserAgentAccess.agent_id == Agent.id
        ).where(
            and_(
                Agent.name.ilike(search_term),
                UserAgentAccess.is_active == True,
                Agent.is_deleted == False,
                Agent.status.notin_(["draft", "inactive"])
            )
        )
        user_agent_result = await db.execute(user_agent_access_query)
        user_ids_from_agent_access = [row[0] for row in user_agent_result.all()]
        matching_user_ids.update(user_ids_from_agent_access)
        
        logger.debug(f"Found {len(user_ids_from_agent_access)} users with direct agent assignments matching '{search}'")
        
        # 2. Search in agent names via RBACAgentPivot and RBACUserPivot (RBAC-based access)
        # Find agents matching the search term, get their Azure AD groups,
        # then find users whose Azure AD groups intersect with agent's groups
        from aldar_middleware.models.rbac import RBACAgentPivot, RBACUserPivot
        
        # Find agents matching the search term in RBAC pivot
        agent_pivot_query = select(RBACAgentPivot.agent_name, RBACAgentPivot.azure_ad_groups).where(
            RBACAgentPivot.agent_name.ilike(search_term)
        )
        agent_pivot_result = await db.execute(agent_pivot_query)
        matching_agent_pivots = agent_pivot_result.all()
        
        if matching_agent_pivots:
            # Filter out agents that are deleted or inactive
            # First, get the agent names from pivot
            pivot_agent_names = [name for name, _ in matching_agent_pivots]
            
            # Check which of these agents are actually active and not deleted
            active_agents_query = select(Agent.name).where(
                and_(
                    Agent.name.in_(pivot_agent_names),
                    Agent.is_deleted == False,
                    Agent.status.notin_(["draft", "inactive"])
                )
            )
            active_agents_result = await db.execute(active_agents_query)
            active_agent_names = {row[0] for row in active_agents_result.all()}
            
            # Only process pivots for agents that are active
            active_matching_pivots = [
                (name, groups) for name, groups in matching_agent_pivots 
                if name in active_agent_names
            ]
            
            if active_matching_pivots:
                # Collect all Azure AD group IDs from matching agents
                agent_ad_groups = set()
                for agent_name, ad_groups in active_matching_pivots:
                    if ad_groups and isinstance(ad_groups, list):
                        agent_ad_groups.update(ad_groups)
                
                logger.debug(
                    f"Found {len(active_matching_pivots)} active agents matching '{search}' "
                    f"with {len(agent_ad_groups)} unique AD groups"
                )
                
                if agent_ad_groups:
                    # Find users whose Azure AD groups intersect with these groups
                    # Use simple approach: check if any agent AD group appears in user's AD groups JSON text
                    user_pivot_conditions = []
                    for ad_group_id in agent_ad_groups:
                        # Convert JSON to text and search for the group ID
                        user_pivot_conditions.append(
                            cast(RBACUserPivot.azure_ad_groups, String).like(f'%{ad_group_id}%')
                        )
                    
                    if user_pivot_conditions:
                        user_pivot_query = select(RBACUserPivot.email).where(
                            or_(*user_pivot_conditions)
                        )
                        user_pivot_result = await db.execute(user_pivot_query)
                        matching_emails = [row[0] for row in user_pivot_result.all()]
                        
                        # Convert emails to user IDs
                        if matching_emails:
                            user_ids_query = select(User.id).where(User.email.in_(matching_emails))
                            user_ids_result = await db.execute(user_ids_query)
                            user_ids_from_rbac = [row[0] for row in user_ids_result.all()]
                            matching_user_ids.update(user_ids_from_rbac)
                            
                            logger.debug(f"Found {len(user_ids_from_rbac)} users with RBAC-based access to matching agents")
        
        # Add condition to match users who have matching agents
        if matching_user_ids:
            user_search_conditions.append(User.id.in_(matching_user_ids))
        
        query = query.where(or_(*user_search_conditions))
    
    # Apply date filters (filter by last_login, not created_at)
    if date_from or date_to:
        # Exclude users with NULL last_login when date filters are applied
        query = query.where(User.last_login.isnot(None))
    
    if date_from:
        # Frontend sends UTC datetime already converted from local time
        # Simple UTC comparison: WHERE last_login >= date_from
        if date_from.tzinfo is not None:
            date_from = date_from.astimezone(timezone.utc).replace(tzinfo=None)
        query = query.where(User.last_login >= date_from)
    
    if date_to:
        # Frontend sends UTC datetime already converted from local time
        # Simple UTC comparison: WHERE last_login <= date_to
        if date_to.tzinfo is not None:
            date_to = date_to.astimezone(timezone.utc).replace(tzinfo=None)
        query = query.where(User.last_login <= date_to)
    
    # Get total count before pagination
    count_query = select(func.count()).select_from(query.subquery())
    count_result = await db.execute(count_query)
    total = count_result.scalar_one()
    
    # Apply sorting
    order_by_clause = None
    if sort_by:
        sort_by_lower = sort_by.lower()
        sort_order_upper = (sort_order or "ASC").upper()
        
        # Validate sort_order
        if sort_order_upper not in ["ASC", "DESC"]:
            sort_order_upper = "ASC"
        
        # Map sort_by to User model fields
        if sort_by_lower == "role":
            # Sort by is_admin first (admin users come first in DESC, last in ASC)
            # Then by group role if available
            if sort_order_upper == "ASC":
                # ASC: non-admins first, then admins
                order_by_clause = asc(User.is_admin)
            else:
                # DESC: admins first, then non-admins
                order_by_clause = desc(User.is_admin)
        elif sort_by_lower == "last_active":
            # Sort by last_login (NULLs last)
            field = User.last_login
            if sort_order_upper == "ASC":
                order_by_clause = nullslast(asc(field))
            else:
                order_by_clause = nullslast(desc(field))
        elif sort_by_lower in ["username", "department", "full_name", "email", "date"]:
            field_mapping = {
                "username": User.username,
                "department": User.azure_department,
                "full_name": User.full_name,
                "email": User.email,
                "date": User.created_at,
            }
            field = field_mapping[sort_by_lower]
            if sort_order_upper == "ASC":
                # For ASC, put NULLs last
                order_by_clause = nullslast(asc(field))
            else:
                # For DESC, put NULLs last as well for consistency
                order_by_clause = nullslast(desc(field))
        else:
            # Invalid sort_by, default to name
            order_by_clause = nullslast(asc(User.full_name))
    else:
        # Default sorting by name (full_name, then username as fallback)
        # Use COALESCE to fallback to username if full_name is NULL
        from sqlalchemy import func
        name_expr = func.coalesce(User.full_name, User.username, User.email)
        order_by_clause = nullslast(asc(name_expr))
    
    # Apply pagination and sorting
    result = await db.execute(
        query
        .order_by(order_by_clause)
        .offset(skip)
        .limit(limit)
    )
    users = result.scalars().all()
    
    # Convert skip to page (1-based)
    page = (skip // limit) + 1 if limit > 0 else 1
    total_pages = (total + limit - 1) // limit if total > 0 else 0
    
    # Fast path: return users without groups if not needed (agents are included by default)
    if not include_groups and not include_agents:
        user_responses = []
        for user in users:
            # Get profile photo URL - check preferences first, then fallback to proxy endpoint
            from aldar_middleware.utils.user_utils import get_profile_photo_url
            profile_photo = get_profile_photo_url(user)
            if not profile_photo and user.azure_ad_id:
                profile_photo = f"{settings.api_prefix}/auth/users/{user.id}/profile-photo"
            
            user_dict = {
                "id": user.id,
                "email": user.email,
                "username": user.username or "",
                "first_name": user.first_name,
                "last_name": user.last_name,
                "full_name": user.full_name,
                "is_active": user.is_active,
                "is_verified": user.is_verified,
                "is_admin": user.is_admin,
                "azure_ad_id": user.azure_ad_id,
                "azure_display_name": user.azure_display_name,
                "azure_upn": user.azure_upn,
                "department": user.azure_department,
                "job_title": user.azure_job_title,
                "profile_photo": profile_photo,
                "external_id": user.external_id,
                "company": user.company,
                "is_onboarded": user.is_onboarded,
                "is_custom_query_enabled": user.is_custom_query_enabled,
                "created_at": user.created_at,
                "updated_at": user.updated_at,
                "last_login": user.last_login,
                "first_logged_in_at": user.first_logged_in_at,
                "groups": [],
                "agents": []
            }
            user_responses.append(UserResponse(**user_dict))
        
        return PaginatedResponse(
            items=user_responses,
            total=total,
            page=page,
            limit=limit,
            total_pages=total_pages,
        )
    
    # Build user responses with agents information (only if include_groups or include_agents is True)
    from aldar_middleware.models.user_agent_access import UserAgentAccess
    from aldar_middleware.models.menu import Agent
    from aldar_middleware.models.rbac import RBACUserPivot, RBACAgentPivot
    from aldar_middleware.models.attachment import Attachment
    from aldar_middleware.services.rbac_pivot_service import RBACPivotService
    
    # Helper function to resolve agent icon/thumbnail
    async def _resolve_agent_thumbnail(agent_icon: Optional[str]) -> Optional[str]:
        """Resolve agent icon to thumbnail URL.
        
        If agent_icon is a UUID (attachment ID), resolve it to blob URL.
        Otherwise, use it as-is (already a URL).
        """
        if not agent_icon:
            return None
        
        # Check if it's a UUID (attachment ID)
        try:
            icon_uuid = UUID(agent_icon)
            # It's a UUID, try to resolve to blob URL
            result = await db.execute(
                select(Attachment).where(
                    Attachment.id == icon_uuid,
                    Attachment.is_active == True
                )
            )
            attachment = result.scalar_one_or_none()
            if attachment and attachment.blob_url:
                return attachment.blob_url
            else:
                # UUID but no attachment found, return None
                return None
        except (ValueError, TypeError):
            # Not a UUID, assume it's already a URL
            return agent_icon
    
    # Initialize RBAC pivot service
    pivot_service = RBACPivotService(db)
    
    # Get all agent pivots once (for efficiency) - only if include_agents is True
    all_agent_pivots = []
    agents_by_name = {}
    if include_agents:
        all_agent_pivots = await pivot_service.list_all_agent_pivots()
        
        # Get all agents from Agent table for lookup - filter out deleted, draft and inactive agents
        all_agents_query = await db.execute(
            select(Agent).where(
                and_(
                    Agent.is_deleted == False,
                    Agent.status.notin_(["draft", "inactive"])
                )
            )
        )
        all_agents = all_agents_query.scalars().all()
        agents_by_name = {agent.name: agent for agent in all_agents}
    # Collect all unique Azure AD group IDs that need names (only if include_groups is True)
    all_ad_group_ids = set()
    if include_groups:
        for user in users:
            user_ad_groups = await pivot_service.get_user_ad_groups(user.email)
            if user_ad_groups:
                all_ad_group_ids.update(user_ad_groups)
    
    # Fetch group names from Azure AD API for groups that don't have UserGroup entries
    # First, get all UserGroup entries to see which groups we already have names for
    if include_groups and all_ad_group_ids:
        existing_user_groups_result = await db.execute(
            select(UserGroup).where(
                UserGroup.azure_ad_group_id.in_(list(all_ad_group_ids)),
                UserGroup.is_active == True
            )
        )
        existing_user_groups = existing_user_groups_result.scalars().all()
        existing_ad_group_ids_with_names = {g.azure_ad_group_id for g in existing_user_groups if g.azure_ad_group_id}
        
        # Get group IDs that need names fetched from Azure AD
        group_ids_needing_names = all_ad_group_ids - existing_ad_group_ids_with_names
        
        # Fetch group names from Azure AD API in parallel
        ad_group_names_map = {}
        if group_ids_needing_names:
            try:
                from aldar_middleware.services.azure_ad_sync import AzureADSyncService
                sync_service = AzureADSyncService()
                
                # Fetch group names in parallel (with rate limiting)
                fetch_tasks = []
                for group_id in group_ids_needing_names:
                    fetch_tasks.append(sync_service.validate_group_by_id(group_id))
                
                # Execute all fetches with concurrency limit to avoid rate limiting
                group_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
                
                # Build mapping of group ID to display name
                failed_count = 0
                not_found_count = 0
                error_count = 0
                
                for i, group_id in enumerate(group_ids_needing_names):
                    result = group_results[i]
                    if isinstance(result, dict) and result:
                        display_name = result.get("displayName") or result.get("mail") or f"Azure AD Group ({group_id[:8]}...)"
                        ad_group_names_map[group_id] = display_name
                    else:
                        # Handle different failure scenarios
                        failed_count += 1
                        if result is None:
                            # Group doesn't exist in Azure AD (404) - this is expected for deleted groups
                            not_found_count += 1
                            logger.debug(f"Azure AD group {group_id[:8]}... not found (may have been deleted)")
                            ad_group_names_map[group_id] = f"Azure AD Group ({group_id[:8]}...)"
                        elif isinstance(result, Exception):
                            # Other errors (auth, network, etc.)
                            error_count += 1
                            logger.debug(f"Error fetching name for Azure AD group {group_id[:8]}...: {type(result).__name__}: {result}")
                            ad_group_names_map[group_id] = f"Azure AD Group ({group_id[:8]}...)"
                        else:
                            # Unknown failure type
                            error_count += 1
                            ad_group_names_map[group_id] = f"Azure AD Group ({group_id[:8]}...)"
                
                if failed_count > 0:
                    if error_count > 0:
                        logger.warning(
                            f"Failed to fetch names for {failed_count}/{len(group_ids_needing_names)} Azure AD groups "
                            f"({not_found_count} not found, {error_count} errors). Using placeholder names. "
                            f"Errors may be due to authentication issues or rate limiting."
                        )
                    elif not_found_count > 0:
                        logger.info(
                            f"{not_found_count} Azure AD groups not found (may have been deleted). Using placeholder names."
                        )
            except Exception as e:
                logger.warning(
                    f"Error fetching Azure AD group names (likely authentication/configuration issue): {type(e).__name__}: {e}. "
                    f"Using placeholder names for {len(group_ids_needing_names)} groups."
                )
                # Fallback: use placeholder names for all groups
                for group_id in group_ids_needing_names:
                    ad_group_names_map[group_id] = f"Azure AD Group ({group_id[:8]}...)"
    else:
        ad_group_names_map = {}
    
    # OPTIMIZATION: Batch load all UserAgentAccess records for all users at once
    user_agents_map = {}  # Map user_id -> list of UserAgentAccess
    if include_agents and users:
        user_ids = [user.id for user in users]
        all_agent_accesses_result = await db.execute(
            select(UserAgentAccess)
            .join(Agent, UserAgentAccess.agent_id == Agent.id)
            .where(
                and_(
                    UserAgentAccess.user_id.in_(user_ids),
                    UserAgentAccess.is_active == True,
                    Agent.is_deleted == False,
                    Agent.status.notin_(["draft", "inactive"])
                )
            )
            .options(selectinload(UserAgentAccess.agent))
        )
        all_agent_accesses = all_agent_accesses_result.scalars().all()
        
        # Group by user_id
        for access in all_agent_accesses:
            user_id = access.user_id
            if user_id not in user_agents_map:
                user_agents_map[user_id] = []
            user_agents_map[user_id].append(access)
    
    # OPTIMIZATION: Batch load all user AD groups at once from RBACUserPivot table
    user_ad_groups_map = {}  # Map user_email -> list of AD group UUIDs
    if (include_agents or include_groups) and users:
        user_emails = [user.email for user in users if user.email]
        # Batch fetch AD groups for all users from database
        if user_emails:
            from aldar_middleware.models.rbac import RBACUserPivot
            # Single query to get all user pivots
            user_pivots_result = await db.execute(
                select(RBACUserPivot).where(RBACUserPivot.email.in_(user_emails))
            )
            user_pivots = user_pivots_result.scalars().all()
            
            # Build map from email to AD groups
            for pivot in user_pivots:
                user_ad_groups_map[pivot.email] = pivot.azure_ad_groups or []
            
            # Initialize empty lists for users not in pivot table
            for email in user_emails:
                if email not in user_ad_groups_map:
                    user_ad_groups_map[email] = []
    
    # OPTIMIZATION: Collect all unique agent icons that need thumbnail resolution
    agent_icons_to_resolve = set()
    if include_agents:
        # Collect from UserAgentAccess
        for access_list in user_agents_map.values():
            for access in access_list:
                if access.agent and access.agent.icon:
                    agent_icons_to_resolve.add(access.agent.icon)
        
        # Collect from agents_by_name (for RBAC-based access)
        for agent in agents_by_name.values():
            if agent.icon:
                agent_icons_to_resolve.add(agent.icon)
    
    # OPTIMIZATION: Batch resolve all agent thumbnails in parallel
    agent_thumbnail_cache = {}  # Map agent_icon -> thumbnail_url
    if agent_icons_to_resolve:
        import asyncio
        thumbnail_tasks = [
            _resolve_agent_thumbnail(icon) 
            for icon in agent_icons_to_resolve
        ]
        thumbnail_results = await asyncio.gather(*thumbnail_tasks, return_exceptions=True)
        
        for icon, thumbnail in zip(agent_icons_to_resolve, thumbnail_results):
            if isinstance(thumbnail, str):
                agent_thumbnail_cache[icon] = thumbnail
            else:
                agent_thumbnail_cache[icon] = None
    
    user_responses = []
    for user in users:
        # Get agents for this user (only if include_agents is True)
        agents_info = []
        if include_agents:
            agent_ids_seen = set()  # Track agents we've already added to avoid duplicates
            
            # Get agents from UserAgentAccess (batch loaded)
            agent_accesses = user_agents_map.get(user.id, [])
            for access in agent_accesses:
                if access.agent:
                    agent_id = str(access.agent.public_id)
                    # Get thumbnail from cache
                    agent_thumbnail = agent_thumbnail_cache.get(access.agent.icon) if access.agent.icon else None
                    agents_info.append(UserAgentInfo(
                        agent_id=agent_id,
                        agent_name=access.agent.name,
                        access_level=access.access_level,
                        is_active=access.is_active,
                        thumbnail=agent_thumbnail
                    ))
                    agent_ids_seen.add(agent_id)
            
            # Also get agents from AD groups via RBAC (using batch loaded AD groups)
            user_ad_groups = user_ad_groups_map.get(user.email, [])
            
            if user_ad_groups:
                user_groups_set = set(user_ad_groups)
                
                # Check each agent pivot for access
                for agent_pivot in all_agent_pivots:
                    agent_name = agent_pivot.agent_name
                    agent_groups = agent_pivot.azure_ad_groups or []
                    agent_groups_set = set(agent_groups)
                    
                    # Check if there's any intersection between user's AD groups and agent's AD groups
                    if user_groups_set & agent_groups_set:
                        # User has access to this agent via AD groups
                        agent_from_table = agents_by_name.get(agent_name)
                        if agent_from_table:
                            agent_id = str(agent_from_table.public_id)
                            # Only add if not already in the list (avoid duplicates)
                            if agent_id not in agent_ids_seen:
                                # Get thumbnail from cache
                                agent_thumbnail = agent_thumbnail_cache.get(agent_from_table.icon) if agent_from_table.icon else None
                                agents_info.append(UserAgentInfo(
                                    agent_id=agent_id,
                                    agent_name=agent_from_table.name,
                                    access_level="read",  # Default access level for AD group-based access
                                    is_active=True,
                                    thumbnail=agent_thumbnail
                                ))
                                agent_ids_seen.add(agent_id)
        
        # Get profile photo URL - check preferences first, then fallback to proxy endpoint
        from aldar_middleware.utils.user_utils import get_profile_photo_url
        profile_photo = get_profile_photo_url(user)
        if not profile_photo and user.azure_ad_id:
            # Fallback to proxy endpoint
            profile_photo = f"{settings.api_prefix}/auth/users/{user.id}/profile-photo"
        
        # Get groups from both traditional UserGroup memberships and Azure AD groups (only if include_groups is True)
        groups_list = []
        if include_groups:
            existing_group_ids = set()
            existing_ad_group_ids = set()  # Track Azure AD group IDs we've already added
            
            # 1. Get groups from traditional UserGroup memberships
            for membership in user.group_memberships:
                if membership.is_active and membership.group.is_active:
                    groups_list.append(GroupResponse.model_validate(membership.group))
                    existing_group_ids.add(membership.group.id)
                    # Track Azure AD group ID if it exists
                    if membership.group.azure_ad_group_id:
                        existing_ad_group_ids.add(membership.group.azure_ad_group_id)
            
            # 2. Get groups from Azure AD groups (RBAC pivot table) - using batch loaded AD groups
            user_ad_group_uuids = user_ad_groups_map.get(user.email, [])
            
            if user_ad_group_uuids:
                # Look up UserGroup entries that match these Azure AD group IDs
                # This allows us to get group names and other metadata if they exist
                ad_groups_result = await db.execute(
                    select(UserGroup).where(
                        UserGroup.azure_ad_group_id.in_(user_ad_group_uuids),
                        UserGroup.is_active == True
                    )
                )
                ad_groups = ad_groups_result.scalars().all()
                
                # Add groups found in UserGroup table (avoid duplicates)
                for ad_group in ad_groups:
                    if ad_group.id not in existing_group_ids:
                        groups_list.append(GroupResponse.model_validate(ad_group))
                        existing_group_ids.add(ad_group.id)
                        existing_ad_group_ids.add(ad_group.azure_ad_group_id)
                
                # For Azure AD groups that don't have a UserGroup entry, create minimal GroupResponse
                # This ensures all Azure AD groups from rbac_user_pivot are shown
                for ad_group_uuid in user_ad_group_uuids:
                    if ad_group_uuid not in existing_ad_group_ids:
                        # Get the actual group name from Azure AD API (or use placeholder if not available)
                        group_name = ad_group_names_map.get(ad_group_uuid, f"Azure AD Group ({ad_group_uuid[:8]}...)")
                        
                        # Create a minimal group response for Azure AD groups without UserGroup entries
                        from uuid import uuid4
                        groups_list.append(GroupResponse(
                            id=uuid4(),  # Generate a temporary UUID for the id field
                            name=group_name,  # Use actual Azure AD group name
                            description=None,
                            azure_ad_group_id=ad_group_uuid,  # Store the actual Azure AD group ID
                            is_active=True,
                            created_at=datetime.utcnow(),
                            updated_at=datetime.utcnow()
                        ))
                        existing_ad_group_ids.add(ad_group_uuid)
        
        # Build user response with all user information
        user_dict = {
            "id": user.id,
            "email": user.email,
            "username": user.username or "",
            "first_name": user.first_name,
            "last_name": user.last_name,
            "full_name": user.full_name,
            "is_active": user.is_active,
            "is_verified": user.is_verified,
            "is_admin": user.is_admin,
            "azure_ad_id": user.azure_ad_id,
            "azure_display_name": user.azure_display_name,
            "azure_upn": user.azure_upn,
            "department": user.azure_department,
            "job_title": user.azure_job_title,
            "profile_photo": profile_photo,
            "external_id": user.external_id,
            "company": user.company,
            "is_onboarded": user.is_onboarded,
            "is_custom_query_enabled": user.is_custom_query_enabled,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "last_login": user.last_login,
            "first_logged_in_at": user.first_logged_in_at,
            "groups": groups_list,
            "agents": agents_info
        }
        user_responses.append(UserResponse(**user_dict))
    
    return PaginatedResponse(
        items=user_responses,
        total=total,
        page=page,
        limit=limit,
        total_pages=total_pages,
    )


@router.get("/users/export/csv")
async def export_users_csv(
    is_admin: Optional[bool] = Query(None, description="Filter by admin status"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    is_verified: Optional[bool] = Query(None, description="Filter by verified status"),
    user_id: Optional[UUID] = Query(None, description="Filter by specific user ID"),
    username: Optional[str] = Query(None, description="Filter by exact username"),
    email: Optional[str] = Query(None, description="Filter by exact email"),
    azure_ad_id: Optional[str] = Query(None, description="Filter by Azure AD ID"),
    search: Optional[str] = Query(None, description="Search in email, username, first_name, last_name"),
    date_from: Optional[datetime] = Query(None, description="Filter users by last login from this date (ISO format)"),
    date_to: Optional[datetime] = Query(None, description="Filter users by last login to this date (ISO format)"),
    sort_by: Optional[str] = Query(None, description="Sort by field: username, department, full_name, email, date, role, last_active"),
    sort_order: Optional[str] = Query("ASC", description="Sort order: ASC or DESC"),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Export users as CSV (admin only)."""
    try:
        from sqlalchemy import func
        
        query = select(User)
        
        # Apply filters (same as list_users)
        if is_admin is not None:
            query = query.where(User.is_admin == is_admin)
        
        if is_active is not None:
            query = query.where(User.is_active == is_active)
        
        if is_verified is not None:
            query = query.where(User.is_verified == is_verified)
        
        if user_id is not None:
            query = query.where(User.id == user_id)
        
        if username is not None:
            query = query.where(User.username == username)
        
        if email is not None:
            query = query.where(User.email == email)
        
        if azure_ad_id is not None:
            query = query.where(User.azure_ad_id == azure_ad_id)
        
        if search:
            from aldar_middleware.models.user_agent_access import UserAgentAccess
            from aldar_middleware.models.menu import Agent
            
            search_term = f"%{search}%"
            
            user_search_conditions = [
                User.email.ilike(search_term),
                User.username.ilike(search_term),
                User.first_name.ilike(search_term),
                User.last_name.ilike(search_term),
                User.full_name.ilike(search_term),
                User.azure_department.ilike(search_term)
            ]
            
            from aldar_middleware.models.rbac import RBACAgentPivot, RBACUserPivot
            
            agent_pivot_query = select(RBACAgentPivot.agent_name, RBACAgentPivot.azure_ad_groups).where(
                RBACAgentPivot.agent_name.ilike(search_term)
            )
            agent_pivot_result = await db.execute(agent_pivot_query)
            matching_agent_pivots = agent_pivot_result.all()
            
            if matching_agent_pivots:
                agent_ad_groups = set()
                for agent_name, ad_groups in matching_agent_pivots:
                    if ad_groups and isinstance(ad_groups, list):
                        agent_ad_groups.update(ad_groups)
                
                if agent_ad_groups:
                    user_pivot_conditions = []
                    for ad_group_id in agent_ad_groups:
                        user_pivot_conditions.append(
                            cast(RBACUserPivot.azure_ad_groups, String).like(f'%{ad_group_id}%')
                        )
                    
                    if user_pivot_conditions:
                        user_pivot_query = select(RBACUserPivot.email).where(
                            or_(*user_pivot_conditions)
                        )
                        user_pivot_result = await db.execute(user_pivot_query)
                        matching_emails = [row[0] for row in user_pivot_result.all()]
                        
                        if matching_emails:
                            agent_search_condition = User.email.in_(matching_emails)
                            user_search_conditions.append(agent_search_condition)
            
            query = query.where(or_(*user_search_conditions))
        
        if date_from or date_to:
            query = query.where(User.last_login.isnot(None))
        
        if date_from:
            if date_from.tzinfo is not None:
                date_from = date_from.astimezone(timezone.utc).replace(tzinfo=None)
            query = query.where(User.last_login >= date_from)
        
        if date_to:
            if date_to.tzinfo is not None:
                date_to = date_to.astimezone(timezone.utc).replace(tzinfo=None)
            query = query.where(User.last_login <= date_to)
        
        # Apply sorting
        order_by_clause = None
        if sort_by:
            sort_by_lower = sort_by.lower()
            sort_order_upper = (sort_order or "ASC").upper()
            
            if sort_order_upper not in ["ASC", "DESC"]:
                sort_order_upper = "ASC"
            
            if sort_by_lower == "role":
                if sort_order_upper == "ASC":
                    order_by_clause = asc(User.is_admin)
                else:
                    order_by_clause = desc(User.is_admin)
            elif sort_by_lower == "last_active":
                field = User.last_login
                if sort_order_upper == "ASC":
                    order_by_clause = nullslast(asc(field))
                else:
                    order_by_clause = nullslast(desc(field))
            elif sort_by_lower in ["username", "department", "full_name", "email", "date"]:
                field_mapping = {
                    "username": User.username,
                    "department": User.azure_department,
                    "full_name": User.full_name,
                    "email": User.email,
                    "date": User.created_at,
                }
                field = field_mapping[sort_by_lower]
                if sort_order_upper == "ASC":
                    order_by_clause = nullslast(asc(field))
                else:
                    order_by_clause = nullslast(desc(field))
            else:
                name_expr = func.coalesce(User.full_name, User.username, User.email)
                order_by_clause = nullslast(asc(name_expr))
        else:
            name_expr = func.coalesce(User.full_name, User.username, User.email)
            order_by_clause = nullslast(asc(name_expr))
        
        # Get all users (no pagination for export)
        result = await db.execute(query.order_by(order_by_clause))
        users = result.scalars().all()
        
        # Create CSV
        csv_buffer = io.StringIO()
        csv_writer = csv.DictWriter(
            csv_buffer,
            fieldnames=[
                "id",
                "email",
                "username",
                "first_name",
                "last_name",
                "full_name",
                "is_active",
                "is_verified",
                "is_admin",
                "azure_ad_id",
                "azure_display_name",
                "azure_upn",
                "department",
                "job_title",
                "external_id",
                "company",
                "is_onboarded",
                "is_custom_query_enabled",
                "created_at",
                "updated_at",
                "last_login",
                "first_logged_in_at",
            ],
        )
        
        csv_writer.writeheader()
        for user in users:
            csv_writer.writerow({
                "id": str(user.id),
                "email": user.email or "",
                "username": user.username or "",
                "first_name": user.first_name or "",
                "last_name": user.last_name or "",
                "full_name": user.full_name or "",
                "is_active": user.is_active,
                "is_verified": user.is_verified,
                "is_admin": user.is_admin,
                "azure_ad_id": user.azure_ad_id or "",
                "azure_display_name": user.azure_display_name or "",
                "azure_upn": user.azure_upn or "",
                "department": user.azure_department or "",
                "job_title": user.azure_job_title or "",
                "external_id": user.external_id or "",
                "company": user.company or "",
                "is_onboarded": user.is_onboarded,
                "is_custom_query_enabled": user.is_custom_query_enabled,
                "created_at": user.created_at.isoformat() if user.created_at else "",
                "updated_at": user.updated_at.isoformat() if user.updated_at else "",
                "last_login": user.last_login.isoformat() if user.last_login else "",
                "first_logged_in_at": user.first_logged_in_at.isoformat() if user.first_logged_in_at else "",
            })
        
        csv_data = csv_buffer.getvalue()
        
        logger.info(
            "Users exported as CSV",
            extra={
                "user_count": len(users),
                "exported_by": str(current_user.id),
            },
        )
        
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=users.csv"},
        )
    
    except Exception as e:
        logger.error(
            f"Failed to export users: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to export users",
        )


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Get a specific user with their groups (admin only)."""
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.group_memberships).selectinload(UserGroupMembership.group),
        )
        .where(User.id == user_id),
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Get agents for this user from UserAgentAccess table
    from aldar_middleware.models.user_agent_access import UserAgentAccess
    from aldar_middleware.models.menu import Agent
    from aldar_middleware.models.attachment import Attachment
    from aldar_middleware.services.rbac_pivot_service import RBACPivotService
    
    # Helper function to resolve agent icon/thumbnail
    async def _resolve_agent_thumbnail(agent_icon: Optional[str]) -> Optional[str]:
        """Resolve agent icon to thumbnail URL.
        
        If agent_icon is a UUID (attachment ID), resolve it to blob URL.
        Otherwise, use it as-is (already a URL).
        """
        if not agent_icon:
            return None
        
        # Check if it's a UUID (attachment ID)
        try:
            icon_uuid = UUID(agent_icon)
            # It's a UUID, try to resolve to blob URL
            result = await db.execute(
                select(Attachment).where(
                    Attachment.id == icon_uuid,
                    Attachment.is_active == True
                )
            )
            attachment = result.scalar_one_or_none()
            if attachment and attachment.blob_url:
                return attachment.blob_url
            else:
                # UUID but no attachment found, return None
                return None
        except (ValueError, TypeError):
            # Not a UUID, assume it's already a URL
            return agent_icon
    
    agent_access_result = await db.execute(
        select(UserAgentAccess)
        .join(Agent, UserAgentAccess.agent_id == Agent.id)
        .where(
            and_(
                UserAgentAccess.user_id == user.id,
                UserAgentAccess.is_active == True,
                Agent.is_deleted == False,
                Agent.status.notin_(["draft", "inactive"])
            )
        )
        .options(selectinload(UserAgentAccess.agent))
    )
    agent_accesses = agent_access_result.scalars().all()
    
    # Build agent info list from UserAgentAccess
    agents_info = []
    agent_ids_seen = set()  # Track agents we've already added to avoid duplicates
    
    for access in agent_accesses:
        if access.agent:
            agent_id = str(access.agent.public_id)
            # Resolve agent thumbnail
            agent_thumbnail = await _resolve_agent_thumbnail(access.agent.icon)
            agents_info.append(UserAgentInfo(
                agent_id=agent_id,
                agent_name=access.agent.name,
                access_level=access.access_level,
                is_active=access.is_active,
                thumbnail=agent_thumbnail
            ))
            agent_ids_seen.add(agent_id)
    
    # Also get agents from AD groups via RBAC
    pivot_service = RBACPivotService(db)
    user_ad_groups = await pivot_service.get_user_ad_groups(user.email)
    
    if user_ad_groups:
        # Get all agent pivots
        all_agent_pivots = await pivot_service.list_all_agent_pivots()
        
        # Get all agents from Agent table for lookup - filter out deleted, draft and inactive agents
        all_agents_query = await db.execute(
            select(Agent).where(
                and_(
                    Agent.is_deleted == False,
                    Agent.status.notin_(["draft", "inactive"])
                )
            )
        )
        all_agents = all_agents_query.scalars().all()
        agents_by_name = {agent.name: agent for agent in all_agents}
        
        user_groups_set = set(user_ad_groups)
        
        # Check each agent pivot for access
        for agent_pivot in all_agent_pivots:
            agent_name = agent_pivot.agent_name
            agent_groups = agent_pivot.azure_ad_groups or []
            agent_groups_set = set(agent_groups)
            
            # Check if there's any intersection between user's AD groups and agent's AD groups
            if user_groups_set & agent_groups_set:
                # User has access to this agent via AD groups
                agent_from_table = agents_by_name.get(agent_name)
                if agent_from_table:
                    agent_id = str(agent_from_table.public_id)
                    # Only add if not already in the list (avoid duplicates)
                    if agent_id not in agent_ids_seen:
                        # Resolve agent thumbnail
                        agent_thumbnail = await _resolve_agent_thumbnail(agent_from_table.icon)
                        agents_info.append(UserAgentInfo(
                            agent_id=agent_id,
                            agent_name=agent_from_table.name,
                            access_level="read",  # Default access level for AD group-based access
                            is_active=True,
                            thumbnail=agent_thumbnail
                        ))
                        agent_ids_seen.add(agent_id)
    
    # Get profile photo URL - always use internal user ID (UUID) for consistency
    profile_photo = None
    if user.azure_ad_id:
        # Always use our proxy endpoint with internal user ID (UUID) instead of Azure AD ID
        # This ensures consistency even if preferences has old Azure AD ID format
        profile_photo = f"{settings.api_prefix}/auth/users/{user.id}/profile-photo"
    
    # Get groups from both traditional UserGroup memberships and Azure AD groups
    groups_list = []
    existing_group_ids = set()
    existing_ad_group_ids = set()  # Track Azure AD group IDs we've already added
    
    # 1. Get groups from traditional UserGroup memberships
    for membership in user.group_memberships:
        if membership.is_active and membership.group.is_active:
            groups_list.append(GroupResponse.model_validate(membership.group))
            existing_group_ids.add(membership.group.id)
            # Track Azure AD group ID if it exists
            if membership.group.azure_ad_group_id:
                existing_ad_group_ids.add(membership.group.azure_ad_group_id)
    
    # 2. Get groups from Azure AD groups (RBAC pivot table)
    # Get user's Azure AD group UUIDs from RBAC pivot table
    user_ad_group_uuids = await pivot_service.get_user_ad_groups(user.email)
    
    if user_ad_group_uuids:
        # Look up UserGroup entries that match these Azure AD group IDs
        # This allows us to get group names and other metadata if they exist
        ad_groups_result = await db.execute(
            select(UserGroup).where(
                UserGroup.azure_ad_group_id.in_(user_ad_group_uuids),
                UserGroup.is_active == True
            )
        )
        ad_groups = ad_groups_result.scalars().all()
        
        # Add groups found in UserGroup table (avoid duplicates)
        for ad_group in ad_groups:
            if ad_group.id not in existing_group_ids:
                groups_list.append(GroupResponse.model_validate(ad_group))
                existing_group_ids.add(ad_group.id)
                existing_ad_group_ids.add(ad_group.azure_ad_group_id)
        
        # For Azure AD groups that don't have a UserGroup entry, fetch names from Azure AD API
        group_ids_needing_names = [gid for gid in user_ad_group_uuids if gid not in existing_ad_group_ids]
        ad_group_names_map = {}
        
        if group_ids_needing_names:
            try:
                from aldar_middleware.services.azure_ad_sync import AzureADSyncService
                sync_service = AzureADSyncService()
                
                # Fetch group names in parallel
                fetch_tasks = [sync_service.validate_group_by_id(group_id) for group_id in group_ids_needing_names]
                group_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
                
                # Build mapping of group ID to display name
                failed_count = 0
                not_found_count = 0
                error_count = 0
                
                for i, group_id in enumerate(group_ids_needing_names):
                    result = group_results[i]
                    if isinstance(result, dict) and result:
                        display_name = result.get("displayName") or result.get("mail") or f"Azure AD Group ({group_id[:8]}...)"
                        ad_group_names_map[group_id] = display_name
                    else:
                        # Handle different failure scenarios
                        failed_count += 1
                        if result is None:
                            # Group doesn't exist in Azure AD (404) - this is expected for deleted groups
                            not_found_count += 1
                            logger.debug(f"Azure AD group {group_id[:8]}... not found (may have been deleted)")
                            ad_group_names_map[group_id] = f"Azure AD Group ({group_id[:8]}...)"
                        elif isinstance(result, Exception):
                            # Other errors (auth, network, etc.)
                            error_count += 1
                            logger.debug(f"Error fetching name for Azure AD group {group_id[:8]}...: {type(result).__name__}: {result}")
                            ad_group_names_map[group_id] = f"Azure AD Group ({group_id[:8]}...)"
                        else:
                            # Unknown failure type
                            error_count += 1
                            ad_group_names_map[group_id] = f"Azure AD Group ({group_id[:8]}...)"
                
                if failed_count > 0:
                    if error_count > 0:
                        logger.warning(
                            f"Failed to fetch names for {failed_count}/{len(group_ids_needing_names)} Azure AD groups "
                            f"({not_found_count} not found, {error_count} errors). Using placeholder names. "
                            f"Errors may be due to authentication issues or rate limiting."
                        )
                    elif not_found_count > 0:
                        logger.info(
                            f"{not_found_count} Azure AD groups not found (may have been deleted). Using placeholder names."
                        )
            except Exception as e:
                logger.warning(
                    f"Error fetching Azure AD group names (likely authentication/configuration issue): {type(e).__name__}: {e}. "
                    f"Using placeholder names for {len(group_ids_needing_names)} groups."
                )
                for group_id in group_ids_needing_names:
                    ad_group_names_map[group_id] = f"Azure AD Group ({group_id[:8]}...)"
        
        # Create minimal GroupResponse for Azure AD groups without UserGroup entries
        for ad_group_uuid in user_ad_group_uuids:
            if ad_group_uuid not in existing_ad_group_ids:
                group_name = ad_group_names_map.get(ad_group_uuid, f"Azure AD Group ({ad_group_uuid[:8]}...)")
                from uuid import uuid4
                groups_list.append(GroupResponse(
                    id=uuid4(),  # Generate a temporary UUID for the id field
                    name=group_name,  # Use actual Azure AD group name
                    description=None,
                    azure_ad_group_id=ad_group_uuid,  # Store the actual Azure AD group ID
                    is_active=True,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                ))
                existing_ad_group_ids.add(ad_group_uuid)
    
    # Build user response with all fields including profile photo, agents, and groups
    # SECURITY: UserResponse schema excludes sensitive fields (password_hash, azure_ad_refresh_token)
    # model_validate will only include fields defined in UserResponse schema
    user_dict = UserResponse.model_validate(user).model_dump(exclude_none=True)
    user_dict["profile_photo"] = profile_photo
    user_dict["agents"] = agents_info
    user_dict["groups"] = groups_list
    
    # SECURITY: Ensure no sensitive fields are accidentally included
    user_dict.pop("password_hash", None)
    user_dict.pop("azure_ad_refresh_token", None)
    
    return UserResponse(**user_dict)


@router.get("/users/{user_id}/groups", response_model=PaginatedResponse[GroupResponse])
async def get_user_groups(
    user_id: UUID,
    skip: Optional[int] = Query(
        None, ge=0, description="Alias for offset; number of groups to skip"
    ),
    offset: Optional[int] = Query(
        None, ge=0, description="Number of groups to skip"
    ),
    limit: int = Query(20, ge=1, le=100, description="Maximum number of groups to return"),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[GroupResponse]:
    """Get groups for a specific user (admin only) with pagination.
    
    This endpoint is optimized to fetch only groups for a single user,
    making it faster than including groups in the main users list endpoint.
    
    Note: Due to the hybrid nature of group sources (database + Azure AD API),
    pagination is applied in-memory after fetching all groups. This is acceptable
    for typical use cases where users have <100 groups.

    Pagination parameters:
    - `offset`: Number of groups to skip (preferred)
    - `skip`: Alias for `offset` for backward compatibility
    """
    from aldar_middleware.services.rbac_pivot_service import RBACPivotService
    
    # Get user
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.group_memberships).selectinload(UserGroupMembership.group),
        )
        .where(User.id == user_id),
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    
    # Initialize RBAC pivot service
    pivot_service = RBACPivotService(db)
    
    # Get groups from both traditional UserGroup memberships and Azure AD groups
    groups_list = []
    existing_group_ids = set()
    existing_ad_group_ids = set()  # Track Azure AD group IDs we've already added
    
    # 1. Get groups from traditional UserGroup memberships
    for membership in user.group_memberships:
        if membership.is_active and membership.group.is_active:
            groups_list.append(GroupResponse.model_validate(membership.group))
            existing_group_ids.add(membership.group.id)
            # Track Azure AD group ID if it exists
            if membership.group.azure_ad_group_id:
                existing_ad_group_ids.add(membership.group.azure_ad_group_id)
    
    # 2. Get groups from Azure AD groups (RBAC pivot table)
    # Get user's Azure AD group UUIDs from RBAC pivot table
    user_ad_group_uuids = await pivot_service.get_user_ad_groups(user.email)
    
    if user_ad_group_uuids:
        # Look up UserGroup entries that match these Azure AD group IDs
        # This allows us to get group names and other metadata if they exist
        ad_groups_result = await db.execute(
            select(UserGroup).where(
                UserGroup.azure_ad_group_id.in_(user_ad_group_uuids),
                UserGroup.is_active == True
            )
        )
        ad_groups = ad_groups_result.scalars().all()
        
        # Add groups found in UserGroup table (avoid duplicates)
        for ad_group in ad_groups:
            if ad_group.id not in existing_group_ids:
                groups_list.append(GroupResponse.model_validate(ad_group))
                existing_group_ids.add(ad_group.id)
                existing_ad_group_ids.add(ad_group.azure_ad_group_id)
        
        # For Azure AD groups that don't have a UserGroup entry, fetch names from Azure AD API
        group_ids_needing_names = [gid for gid in user_ad_group_uuids if gid not in existing_ad_group_ids]
        ad_group_names_map = {}
        
        if group_ids_needing_names:
            try:
                from aldar_middleware.services.azure_ad_sync import AzureADSyncService
                sync_service = AzureADSyncService()
                
                # Fetch group names in parallel
                fetch_tasks = [sync_service.validate_group_by_id(group_id) for group_id in group_ids_needing_names]
                group_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
                
                # Build mapping of group ID to display name
                failed_count = 0
                not_found_count = 0
                error_count = 0
                
                for i, group_id in enumerate(group_ids_needing_names):
                    result = group_results[i]
                    if isinstance(result, dict) and result:
                        display_name = result.get("displayName") or result.get("mail") or f"Azure AD Group ({group_id[:8]}...)"
                        ad_group_names_map[group_id] = display_name
                    else:
                        # Handle different failure scenarios
                        failed_count += 1
                        if result is None:
                            # Group doesn't exist in Azure AD (404) - this is expected for deleted groups
                            not_found_count += 1
                            logger.debug(f"Azure AD group {group_id[:8]}... not found (may have been deleted)")
                            ad_group_names_map[group_id] = f"Azure AD Group ({group_id[:8]}...)"
                        elif isinstance(result, Exception):
                            # Other errors (auth, network, etc.)
                            error_count += 1
                            logger.debug(f"Error fetching name for Azure AD group {group_id[:8]}...: {type(result).__name__}: {result}")
                            ad_group_names_map[group_id] = f"Azure AD Group ({group_id[:8]}...)"
                        else:
                            # Unknown failure type
                            error_count += 1
                            ad_group_names_map[group_id] = f"Azure AD Group ({group_id[:8]}...)"
                
                if failed_count > 0:
                    if error_count > 0:
                        logger.warning(
                            f"Failed to fetch names for {failed_count}/{len(group_ids_needing_names)} Azure AD groups "
                            f"({not_found_count} not found, {error_count} errors). Using placeholder names. "
                            f"Errors may be due to authentication issues or rate limiting."
                        )
                    elif not_found_count > 0:
                        logger.info(
                            f"{not_found_count} Azure AD groups not found (may have been deleted). Using placeholder names."
                        )
            except Exception as e:
                logger.warning(
                    f"Error fetching Azure AD group names (likely authentication/configuration issue): {type(e).__name__}: {e}. "
                    f"Using placeholder names for {len(group_ids_needing_names)} groups."
                )
                for group_id in group_ids_needing_names:
                    ad_group_names_map[group_id] = f"Azure AD Group ({group_id[:8]}...)"
        
        # Create minimal GroupResponse for Azure AD groups without UserGroup entries
        for ad_group_uuid in user_ad_group_uuids:
            if ad_group_uuid not in existing_ad_group_ids:
                group_name = ad_group_names_map.get(ad_group_uuid, f"Azure AD Group ({ad_group_uuid[:8]}...)")
                from uuid import uuid4
                groups_list.append(GroupResponse(
                    id=uuid4(),  # Generate a temporary UUID for the id field
                    name=group_name,  # Use actual Azure AD group name
                    description=None,
                    azure_ad_group_id=ad_group_uuid,  # Store the actual Azure AD group ID
                    is_active=True,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                ))
                existing_ad_group_ids.add(ad_group_uuid)
    
    # Calculate total count and apply pagination
    total_count = len(groups_list)
    
    # Log warning for users with large group counts
    if total_count > 100:
        logger.warning(
            f"User {user_id} has {total_count} groups - in-memory pagination may be slow",
            extra={"user_id": str(user_id), "group_count": total_count}
        )
    
    # Determine effective offset (prefer `offset`, fallback to `skip`, default 0)
    effective_skip = offset if offset is not None else (skip if skip is not None else 0)

    # Apply pagination to groups list
    paginated_groups = groups_list[effective_skip : effective_skip + limit]
    
    # Calculate pagination metadata with edge case handling
    if total_count == 0:
        current_page = 0
        total_pages = 0
    else:
        total_pages = (total_count + limit - 1) // limit
        current_page = (effective_skip // limit) + 1
    
    # Return the paginated response using standard PaginatedResponse schema
    return PaginatedResponse(
        items=paginated_groups,
        total=total_count,
        page=current_page,
        limit=limit,
        total_pages=total_pages
    )


# Logs Management Endpoints
@router.get("/logs", response_model=PaginatedResponse[AdminLogEventResponse])
async def query_logs(
    email: Optional[str] = Query(None, description="Filter by user email"),
    level: Optional[str] = Query(None, description="Filter by log level"),
    action_type: Optional[str] = Query(None, description="Filter by action type (e.g., USERS_LOGS_EXPORTED, KNOWLEDGE_AGENT_UPDATED)"),
    module: Optional[str] = Query(None, description="Filter by module"),
    function: Optional[str] = Query(None, description="Filter by function"),
    start_time: Optional[datetime] = Query(None, description="Start time filter"),
    end_time: Optional[datetime] = Query(None, description="End time filter"),
    date_from: Optional[datetime] = Query(None, description="Filter logs from this date (ISO format)"),
    date_to: Optional[datetime] = Query(None, description="Filter logs to this date (ISO format)"),
    search: Optional[str] = Query(None, description="Search across name, email, username, user_id, message, and log data fields"),
    limit: int = Query(100, ge=1, le=1000, description="Number of logs to return"),
    offset: int = Query(0, ge=0, description="Number of logs to skip"),
    sort_by: Optional[str] = Query(None, description="Sort by field: timestamp, level, action_type, eventType, email, username, name, module"),
    sort_order: Optional[str] = Query("DESC", description="Sort order: ASC or DESC"),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[LogEntryResponse]:
    """Query application logs (admin only) from PostgreSQL admin_logs table."""
    try:
        # Use date_from/date_to if provided, otherwise fall back to start_time/end_time
        effective_start_time = date_from or start_time
        effective_end_time = date_to or end_time
        
        # Query from PostgreSQL admin_logs table
        result = await postgres_logs_service.query_admin_logs(
            db=db,
            limit=limit,
            offset=offset,
            date_from=effective_start_time,
            date_to=effective_end_time,
            level=level,
            action_type=action_type,
            module=module,
            function=function,
            email=email,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        
        items = result.get("items", [])
        total = result.get("total", 0)
        
        # Transform to AdminLogEventResponse format (matching user logs structure)
        admin_logs_list = []
        user_ids_to_fetch = set()
        agent_ids_to_enrich = set()  # Collect agent IDs for thumbnail enrichment
        
        for item in items:
            # Extract log_data JSONB
            log_data = item.get("log_data", {})
            
            # Extract action_type and body from log_data
            action_type = item.get("action_type") or log_data.get("action_type", "")
            event_payload = log_data.get("body", {})
            
            # Collect agent IDs for enrichment
            # Check for agent ID in nested agent object (for some event types)
            agent = event_payload.get("agent", {})
            if isinstance(agent, dict):
                agent_id = agent.get("agentId")
                if agent_id:
                    agent_ids_to_enrich.add(agent_id)
            
            # Check for agent ID directly in eventPayload (for ADMIN_KNOWLEDGE_AGENT_UPDATED events)
            if action_type == "ADMIN_KNOWLEDGE_AGENT_UPDATED":
                agent_id = event_payload.get("id")
                if agent_id:
                    # Normalize to string for consistent matching
                    agent_ids_to_enrich.add(str(agent_id))
            
            # Extract timestamp
            timestamp_str = item.get("timestamp")
            if isinstance(timestamp_str, str):
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            else:
                timestamp = timestamp_str if timestamp_str else datetime.now(timezone.utc)
            
            # Extract user info
            user_id = item.get("user_id")
            email = item.get("email")
            
            # Collect user_ids for enrichment
            if user_id:
                try:
                    from uuid import UUID as UUIDType
                    UUIDType(user_id)
                    user_ids_to_fetch.add(user_id)
                except (ValueError, TypeError):
                    pass
            
            # Build AdminLogEventResponse (matching UserLogEventResponse structure)
            admin_log_dict = {
                "id": item.get("id", ""),
                "role": None,  # Will be enriched from user data
                "department": None,  # Will be enriched from user data
                "name": None,  # Will be enriched from user data (full_name)
                "email": email,
                "userEntraId": None,  # Will be enriched from user data (azure_ad_id)
                "profile_photo": None,  # Will be enriched from user data
                "eventType": action_type,
                "eventPayload": event_payload,
                "createdAt": timestamp,
            }
            admin_logs_list.append(admin_log_dict)
        
        # Helper function to resolve agent thumbnail
        async def _resolve_agent_thumbnail(agent_icon: Optional[str]) -> Optional[str]:
            """Resolve agent icon to thumbnail URL.
            
            If agent_icon is a UUID (attachment ID), resolve it to blob URL.
            Otherwise, use it as-is (already a URL).
            """
            if not agent_icon:
                return None
            
            # Check if it's a UUID (attachment ID)
            try:
                from uuid import UUID as UUIDType
                icon_uuid = UUIDType(agent_icon)
                # It's a UUID, try to resolve to blob URL
                from aldar_middleware.models.attachment import Attachment
                result = await db.execute(
                    select(Attachment).where(
                        Attachment.id == icon_uuid,
                        Attachment.is_active == True
                    )
                )
                attachment = result.scalar_one_or_none()
                if attachment and attachment.blob_url:
                    return attachment.blob_url
                else:
                    # UUID but no attachment found, return None
                    return None
            except (ValueError, TypeError):
                # Not a UUID, assume it's already a URL
                return agent_icon
        
        # Fetch agent records for thumbnail enrichment
        agent_enrichment_map = {}
        if agent_ids_to_enrich:
            try:
                from uuid import UUID as UUIDType
                from aldar_middleware.models.menu import Agent
                agent_uuid_ids = []
                for agent_id_str in agent_ids_to_enrich:
                    try:
                        agent_uuid_ids.append(UUIDType(agent_id_str))
                    except (ValueError, TypeError):
                        pass
                
                if agent_uuid_ids:
                    agent_query = select(Agent).where(Agent.public_id.in_(agent_uuid_ids))
                    agent_result = await db.execute(agent_query)
                    agents = agent_result.scalars().all()
                    
                    for agent in agents:
                        agent_id_str = str(agent.public_id)
                        agent_enrichment_map[agent_id_str] = agent
            except Exception as e:
                logger.warning(f"Error fetching agent records for thumbnail enrichment: {str(e)}")
        
        # Enrich logs with user information from PostgreSQL
        from uuid import UUID as UUIDType
        
        # Batch fetch users from PostgreSQL
        users_map = {}
        if user_ids_to_fetch:
            try:
                user_uuid_ids = []
                for user_id_str in user_ids_to_fetch:
                    try:
                        user_uuid_ids.append(UUIDType(user_id_str))
                    except (ValueError, TypeError):
                        pass
                
                if user_uuid_ids:
                    user_query = select(User).where(User.id.in_(user_uuid_ids))
                    user_result = await db.execute(user_query)
                    users = user_result.scalars().all()
                    
                    for user in users:
                        # Get full_name, fallback to azure_display_name or construct from first_name + last_name
                        full_name = user.full_name
                        if not full_name:
                            if user.azure_display_name:
                                full_name = user.azure_display_name
                            elif user.first_name or user.last_name:
                                full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
                        
                        # Get profile photo URL - check preferences first, then fallback to proxy endpoint
                        from aldar_middleware.utils.user_utils import get_profile_photo_url
                        profile_photo = get_profile_photo_url(user)
                        if not profile_photo and user.azure_ad_id:
                            profile_photo = f"{settings.api_prefix}/auth/users/{user.id}/profile-photo"
                        
                        users_map[str(user.id)] = {
                            "full_name": full_name,
                            "profile_photo": profile_photo,
                            "department": user.azure_department,
                            "role": "ADMIN" if user.is_admin else "NORMAL",
                            "azure_ad_id": user.azure_ad_id,
                        }
            except Exception as e:
                logger.warning(f"Error fetching user information for logs: {str(e)}")
        
        # Enrich admin logs with user information and agent thumbnails
        enriched_logs = []
        for idx, log_dict in enumerate(admin_logs_list):
            # Get user_id from original item
            original_item = items[idx]
            user_id = original_item.get("user_id")
            
            if user_id and user_id in users_map:
                user_info = users_map[user_id]
                log_dict["name"] = user_info.get("full_name")
                log_dict["profile_photo"] = user_info.get("profile_photo")
                log_dict["department"] = user_info.get("department")
                log_dict["role"] = user_info.get("role")
                log_dict["userEntraId"] = user_info.get("azure_ad_id")
            
            # Enrich agent thumbnail in eventPayload
            event_payload = log_dict.get("eventPayload", {})
            if isinstance(event_payload, dict):
                event_type = log_dict.get("eventType", "")
                
                # Handle nested agent object (for some event types)
                agent = event_payload.get("agent", {})
                if isinstance(agent, dict):
                    agent_id = agent.get("agentId")
                    agent_thumbnail = agent.get("agentThumbnail")
                    
                    # Enrich agent thumbnail if missing
                    if agent_id and not agent_thumbnail:
                        agent_record = agent_enrichment_map.get(agent_id)
                        if agent_record and agent_record.icon:
                            try:
                                resolved_thumbnail = await _resolve_agent_thumbnail(agent_record.icon)
                                if resolved_thumbnail:
                                    agent = agent.copy()
                                    agent["agentThumbnail"] = resolved_thumbnail
                                    event_payload["agent"] = agent
                                    log_dict["eventPayload"] = event_payload
                            except Exception as e:
                                logger.warning(f"Error resolving agent thumbnail for {agent_id}: {str(e)}")
                
                # Handle agent data directly in eventPayload (for ADMIN_KNOWLEDGE_AGENT_UPDATED events)
                if event_type == "ADMIN_KNOWLEDGE_AGENT_UPDATED":
                    agent_id = event_payload.get("id")
                    
                    # Always enrich agent icon from database (ensures we have the latest icon)
                    if agent_id:
                        # Normalize agent_id to string for map lookup
                        agent_id_str = str(agent_id)
                        agent_record = agent_enrichment_map.get(agent_id_str)
                        if agent_record and agent_record.icon:
                            try:
                                resolved_icon = await _resolve_agent_thumbnail(agent_record.icon)
                                if resolved_icon:
                                    event_payload = event_payload.copy()
                                    event_payload["icon"] = resolved_icon
                                    log_dict["eventPayload"] = event_payload
                            except Exception as e:
                                logger.warning(f"Error resolving agent icon for {agent_id_str}: {str(e)}")
            
            enriched_logs.append(AdminLogEventResponse(**log_dict))
        
        # Convert offset to page (1-based)
        page = (offset // limit) + 1 if limit > 0 else 1
        
        # Calculate total_pages
        total_pages = (total + limit - 1) // limit if total > 0 and limit > 0 else 1
        
        # Return paginated response
        return PaginatedResponse(
            items=enriched_logs,
            total=total,
            page=page,
            limit=limit,
            total_pages=total_pages,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid query parameters: {e!s}",
        ) from e
    except Exception as e:
        logger.error(f"Failed to query logs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to query logs. Please try again later.",
        ) from e


@router.get("/logs/export/csv")
async def export_admin_logs_csv(
    email: Optional[str] = Query(None, description="Filter by user email"),
    level: Optional[str] = Query(None, description="Filter by log level"),
    action_type: Optional[str] = Query(None, description="Filter by action type"),
    module: Optional[str] = Query(None, description="Filter by module"),
    function: Optional[str] = Query(None, description="Filter by function"),
    date_from: Optional[datetime] = Query(None, description="Filter logs from this date (ISO format)"),
    date_to: Optional[datetime] = Query(None, description="Filter logs to this date (ISO format)"),
    search: Optional[str] = Query(None, description="Search across name, email, username, user_id, message, and log data fields"),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Export admin logs as CSV (admin only)."""
    try:
        # Query all matching logs (no pagination for export)
        result = await postgres_logs_service.query_admin_logs(
            db=db,
            limit=100000,  # Large limit for export
            offset=0,
            date_from=date_from,
            date_to=date_to,
            level=level,
            action_type=action_type,
            module=module,
            function=function,
            email=email,
            search=search,
            sort_by="timestamp",
            sort_order="DESC",
        )
        
        items = result.get("items", [])
        
        # Fetch user information for enrichment
        from uuid import UUID as UUIDType
        user_ids_to_fetch = set()
        for item in items:
            user_id = item.get("user_id")
            if user_id:
                try:
                    UUIDType(user_id)
                    user_ids_to_fetch.add(user_id)
                except (ValueError, TypeError):
                    pass
        
        users_map = {}
        if user_ids_to_fetch:
            try:
                user_uuid_ids = []
                for user_id_str in user_ids_to_fetch:
                    try:
                        user_uuid_ids.append(UUIDType(user_id_str))
                    except (ValueError, TypeError):
                        pass
                
                if user_uuid_ids:
                    user_query = select(User).where(User.id.in_(user_uuid_ids))
                    user_result = await db.execute(user_query)
                    users = user_result.scalars().all()
                    
                    for user in users:
                        full_name = user.full_name
                        if not full_name:
                            if user.azure_display_name:
                                full_name = user.azure_display_name
                            elif user.first_name or user.last_name:
                                full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
                        
                        users_map[str(user.id)] = {
                            "full_name": full_name,
                            "department": user.azure_department,
                            "role": "ADMIN" if user.is_admin else "NORMAL",
                            "azure_ad_id": user.azure_ad_id,
                        }
            except Exception as e:
                logger.warning(f"Error fetching user information for logs: {str(e)}")
        
        # Create CSV with specific columns as requested
        csv_buffer = io.StringIO()
        csv_writer = csv.DictWriter(
            csv_buffer,
            fieldnames=[
                "ID",
                "Timestamp",
                "Level",
                "ActionType",
                "Module",
                "Function",
                "User ID",
                "Name",
                "Email",
                "Role",
                "User department",
                "message",
                "correlation_id",
                "log_data",
            ],
        )
        
        csv_writer.writeheader()
        for item in items:
            log_data = item.get("log_data", {})
            item_id = item.get("id") or log_data.get("id") or ""
            # Timestamp
            created_at = item.get("timestamp") or item.get("createdAt") or log_data.get("timestamp") or ""
            # Level and action
            level = item.get("level") or log_data.get("level") or ""
            action_type_val = item.get("action_type") or log_data.get("action_type") or ""
            module_val = item.get("module") or log_data.get("module") or ""
            function_val = item.get("function") or log_data.get("function") or ""

            user_id = item.get("user_id") or log_data.get("user_id") or ""

            # Get user info if available
            user_info = users_map.get(user_id, {}) if user_id else {}

            # Format log_data as JSON string
            import json
            try:
                log_data_str = json.dumps(log_data) if log_data else ""
            except Exception:
                log_data_str = str(log_data)

            # Get user fields
            name = user_info.get("full_name", "") or item.get("name", "")
            email = item.get("email", "") or user_info.get("email", "")
            role = user_info.get("role", "") or item.get("role", "")
            department = user_info.get("department", "") or item.get("department", "")

            # Clean up message - remove newlines and extra spaces
            message = (item.get("message") or "").replace("\n", " ").replace("\r", "").strip()

            csv_writer.writerow({
                "ID": item_id or "",
                "Timestamp": created_at if isinstance(created_at, str) else (created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at)),
                "Level": level or "",
                "ActionType": action_type_val or "",
                "Module": module_val or "",
                "Function": function_val or "",
                "User ID": user_id or "",
                "Name": name or "",
                "Email": email or "",
                "Role": role or "",
                "User department": department or "",
                "message": message or "",
                "correlation_id": item.get("correlation_id", "") or "",
                "log_data": log_data_str,
            })

        csv_data = csv_buffer.getvalue()
        
        logger.info(
            "Admin logs exported as CSV",
            extra={
                "user_id": str(current_user.id),
                "row_count": len(items),
            },
        )
        
        # Log admin action: ADMIN_ADMIN_LOGS_EXPORTED
        try:
            request_correlation_id = get_correlation_id() or str(uuid.uuid4())
            admin_log_data = {
                "id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": "INFO",
                "action_type": "ADMIN_ADMIN_LOGS_EXPORTED",
                "user_id": str(current_user.id),
                "email": current_user.email,
                "username": current_user.username or current_user.email,
                "correlation_id": request_correlation_id,
                "module": "admin",
                "function": "export_admin_logs_csv",
                "message": f"Admin logs exported as CSV: {len(items)} rows",
                "log_data": {
                    "action_type": "ADMIN_ADMIN_LOGS_EXPORTED",
                    "row_count": len(items),
                    "filters": {
                        "date_from": date_from.isoformat() if date_from else None,
                        "date_to": date_to.isoformat() if date_to else None,
                        "email": email,
                        "level": level,
                        "action_type": action_type,
                        "module": module,
                        "function": function,
                        "search": search,
                    }
                }
            }
            
            # Write to PostgreSQL synchronously using the same db session
            await postgres_logs_service.write_admin_log(db, admin_log_data)
        except Exception as e:
            logger.error(f"Failed to write admin log for admin logs export: {e}", exc_info=True)
        
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=admin_logs_export.csv"},
        )
    
    except Exception as e:
        logger.error(
            f"Failed to export admin logs: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to export admin logs",
        )


# Note: Agent management endpoints have been moved to /api/admin/agent/
# These endpoints manage the Agent model (from agents table) used for agent configurations.
# UserAgent endpoints below are for user-specific agent assignments (different model).


# Permission Management Endpoints
@router.post("/permissions", response_model=UserPermissionResponse)
async def create_permission(
    permission_data: UserPermissionCreate,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> UserPermissionResponse:
    """Create a new user permission (admin only)."""
    # Check if user exists
    result = await db.execute(select(User).where(User.id == permission_data.user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    
    # Check if agent exists (if provided)
    if permission_data.agent_id:
        result = await db.execute(select(UserAgent).where(UserAgent.id == permission_data.agent_id))
        agent = result.scalar_one_or_none()
        
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found",
            )
    
    permission = UserPermission(
        user_id=permission_data.user_id,
        agent_id=permission_data.agent_id,
        permission_type=permission_data.permission_type,
        resource=permission_data.resource,
        is_granted=permission_data.is_granted,
    )
    
    db.add(permission)
    await db.commit()
    await db.refresh(permission)
    
    return UserPermissionResponse.model_validate(permission)


@router.get("/permissions", response_model=List[UserPermissionResponse])
async def list_permissions(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    user_id: Optional[UUID] = Query(None, description="Filter by user ID"),
    agent_id: Optional[UUID] = Query(None, description="Filter by agent ID"),
    permission_type: Optional[str] = Query(None, description="Filter by permission type"),
    is_granted: Optional[bool] = Query(None, description="Filter by granted status"),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> List[UserPermissionResponse]:
    """List all user permissions (admin only)."""
    query = select(UserPermission)
    
    # Apply filters
    if user_id is not None:
        query = query.where(UserPermission.user_id == user_id)
    
    if agent_id is not None:
        query = query.where(UserPermission.agent_id == agent_id)
    
    if permission_type is not None:
        query = query.where(UserPermission.permission_type == permission_type)
    
    if is_granted is not None:
        query = query.where(UserPermission.is_granted == is_granted)
    
    result = await db.execute(
        query
        .offset(skip)
        .limit(limit)
        .order_by(UserPermission.created_at.desc()),
    )
    permissions = result.scalars().all()
    
    return [UserPermissionResponse.model_validate(permission) for permission in permissions]


@router.put("/permissions/{permission_id}", response_model=UserPermissionResponse)
async def update_permission(
    permission_id: UUID,
    permission_data: UserPermissionUpdate,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> UserPermissionResponse:
    """Update a user permission (admin only)."""
    result = await db.execute(select(UserPermission).where(UserPermission.id == permission_id))
    permission = result.scalar_one_or_none()
    
    if not permission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Permission not found",
        )
    
    # Update fields
    for field, value in permission_data.dict(exclude_unset=True).items():
        setattr(permission, field, value)
    
    await db.commit()
    await db.refresh(permission)
    
    return UserPermissionResponse.model_validate(permission)


@router.delete("/permissions/{permission_id}")
async def delete_permission(
    permission_id: UUID,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, str]:
    """Delete a user permission (admin only)."""
    result = await db.execute(select(UserPermission).where(UserPermission.id == permission_id))
    permission = result.scalar_one_or_none()
    
    if not permission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Permission not found",
        )
    
    await db.delete(permission)
    await db.commit()
    
    return {"message": "Permission deleted successfully"}


# Azure AD Sync Endpoints
@router.post("/azure-ad/sync-users", response_model=AzureADSyncResponse, status_code=status.HTTP_200_OK)
async def sync_users_from_azure_ad(
    request: AzureADSyncRequest,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AzureADSyncResponse:
    """
    Sync users from Azure AD to local database (admin only).
    
    This endpoint:
    - Fetches all users from Azure AD (can be filtered by domain)
    - Creates new users in local database
    - Updates existing users if requested
    - Supports multiple sub-organizations/domains
    
    Parameters:
    - domain_filter: Filter by domain (e.g., 'adq.ae' for users with @adq.ae email)
    - sync_groups: Also sync Azure AD groups
    - max_users: Maximum number of users to sync
    - overwrite_existing: Update existing users with new data
    """
    try:
        # If async_trigger is requested, fire-and-forget in background and return quickly
        if getattr(request, "async_trigger", False):
            async def background_sync(payload: AzureADSyncRequest):
                async with async_session() as session:
                    try:
                        # Reuse existing logic by calling this endpoint's core body synchronously
                        sync_service = AzureADSyncService()
                        azure_users = await sync_service.get_all_users(
                            domain_filter=payload.domain_filter,
                            max_users=payload.max_users
                        )
                        logger.info(f"[BG] Fetched {len(azure_users)} users from Azure AD")
                        results = []
                        successful = 0
                        failed = 0
                        synced_groups = 0
                        azure_ad_user_ids = set()
                        for azure_user in azure_users:
                            try:
                                email = azure_user.get("mail") or azure_user.get("userPrincipalName", "")
                                azure_ad_id = azure_user.get("id")
                                if not email:
                                    failed += 1
                                    continue
                                azure_ad_user_ids.add(azure_ad_id)
                                result = await session.execute(select(User).where(User.azure_ad_id == azure_ad_id))
                                existing_user = result.scalar_one_or_none()
                                first_name = azure_user.get("givenName") or azure_user.get("displayName", "").split()[0] if azure_user.get("displayName") else None
                                last_name = azure_user.get("surname")
                                if not last_name and azure_user.get("displayName"):
                                    name_parts = azure_user.get("displayName", "").split()
                                    if len(name_parts) > 1:
                                        last_name = " ".join(name_parts[1:])
                                username = email.split("@")[0] if "@" in email else email
                                is_active = azure_user.get("accountEnabled", True)
                                if existing_user:
                                    if payload.overwrite_existing:
                                        existing_user.email = email
                                        existing_user.username = username
                                        existing_user.first_name = first_name or existing_user.first_name
                                        existing_user.last_name = last_name or existing_user.last_name
                                        existing_user.is_active = is_active
                                else:
                                    new_user = User(
                                        email=email,
                                        username=username,
                                        first_name=first_name,
                                        last_name=last_name,
                                        azure_ad_id=azure_ad_id,
                                        is_active=is_active,
                                        is_verified=True
                                    )
                                    session.add(new_user)
                                successful += 1
                            except Exception:
                                failed += 1
                        if payload.overwrite_existing:
                            result = await session.execute(
                                select(User).where(
                                    User.azure_ad_id.isnot(None),
                                    User.azure_ad_id.notin_(azure_ad_user_ids)
                                )
                            )
                            users_to_deactivate = result.scalars().all()
                            for user in users_to_deactivate:
                                if user.is_active:
                                    user.is_active = False
                        if payload.sync_groups:
                            try:
                                groups = await sync_service.get_all_groups()
                                for group in groups[:100]:
                                    group_id = group.get("id")
                                    group_name = group.get("displayName")
                                    if not group_name:
                                        continue
                                    # Check by azure_ad_group_id OR name to avoid unique conflicts
                                    result = await session.execute(
                                        select(UserGroup).where(
                                            (UserGroup.azure_ad_group_id == group_id) | (UserGroup.name == group_name)
                                        )
                                    )
                                    existing_group = result.scalar_one_or_none()
                                    if existing_group:
                                        continue
                                    try:
                                        new_group = UserGroup(
                                            name=group_name,
                                            description=group.get("description"),
                                            azure_ad_group_id=group_id,
                                            is_active=True
                                        )
                                        session.add(new_group)
                                    except Exception:
                                        # Ignore duplicates that slip through due to race conditions
                                        await session.rollback()
                            except Exception:
                                pass
                        await session.commit()
                    except Exception as e:
                        logger.error(f"Background sync failed: {e}")
            asyncio.create_task(background_sync(request))
            return AzureADSyncResponse(
                total_fetched=0,
                total_synced=0,
                successful=0,
                failed=0,
                results=[],
                synced_groups=0,
            )
        sync_service = AzureADSyncService()
        
        # Fetch users from Azure AD
        azure_users = await sync_service.get_all_users(
            domain_filter=request.domain_filter,
            max_users=request.max_users
        )
        
        logger.info(f"Fetched {len(azure_users)} users from Azure AD")
        
        results = []
        successful = 0
        failed = 0
        synced_groups = 0
        
        # Track Azure AD user IDs for overwrite functionality
        azure_ad_user_ids = set()
        
        for azure_user in azure_users:
            try:
                # Extract user information
                email = azure_user.get("mail") or azure_user.get("userPrincipalName", "")
                azure_ad_id = azure_user.get("id")
                
                if not email:
                    logger.warning(f"Skipping user {azure_ad_id}: no email found")
                    failed += 1
                    continue
                
                # Track this Azure AD user ID for overwrite functionality
                azure_ad_user_ids.add(azure_ad_id)
                
                # Check if user exists
                result = await db.execute(
                    select(User).where(User.azure_ad_id == azure_ad_id)
                )
                existing_user = result.scalar_one_or_none()
                
                # Parse user details
                first_name = azure_user.get("givenName") or azure_user.get("displayName", "").split()[0] if azure_user.get("displayName") else None
                last_name = azure_user.get("surname")
                if not last_name and azure_user.get("displayName"):
                    name_parts = azure_user.get("displayName", "").split()
                    if len(name_parts) > 1:
                        last_name = " ".join(name_parts[1:])
                
                # Extract username from email (everything before @)
                username = email.split("@")[0] if "@" in email else email
                
                is_active = azure_user.get("accountEnabled", True)
                
                if existing_user:
                    # Update existing user
                    if request.overwrite_existing:
                        existing_user.email = email
                        existing_user.username = username
                        existing_user.first_name = first_name or existing_user.first_name
                        existing_user.last_name = last_name or existing_user.last_name
                        existing_user.is_active = is_active
                        
                        results.append(AzureADSyncResult(
                            email=email,
                            azure_ad_id=azure_ad_id,
                            success=True,
                            user_id=existing_user.id,
                            action="updated"
                        ))
                    else:
                        results.append(AzureADSyncResult(
                            email=email,
                            azure_ad_id=azure_ad_id,
                            success=True,
                            user_id=existing_user.id,
                            action="existing"
                        ))
                else:
                    # Create new user
                    new_user = User(
                        email=email,
                        username=username,
                        first_name=first_name,
                        last_name=last_name,
                        azure_ad_id=azure_ad_id,
                        is_active=is_active,
                        is_verified=True  # Users from AD are considered verified
                    )
                    
                    db.add(new_user)
                    await db.flush()
                    await db.refresh(new_user)
                    
                    results.append(AzureADSyncResult(
                        email=email,
                        azure_ad_id=azure_ad_id,
                        success=True,
                        user_id=new_user.id,
                        action="created"
                    ))
                
                successful += 1
                
            except Exception as e:
                logger.error(f"Error syncing user from Azure AD: {str(e)}")
                results.append(AzureADSyncResult(
                    email=azure_user.get("mail", "unknown"),
                    azure_ad_id=azure_user.get("id", "unknown"),
                    success=False,
                    error=str(e)
                ))
                failed += 1
        
        # Handle overwrite functionality: deactivate users not in Azure AD
        if request.overwrite_existing:
            try:
                # Get all users with Azure AD IDs that are not in the current sync
                result = await db.execute(
                    select(User).where(
                        User.azure_ad_id.isnot(None),
                        User.azure_ad_id.notin_(azure_ad_user_ids)
                    )
                )
                users_to_deactivate = result.scalars().all()
                
                for user in users_to_deactivate:
                    if user.is_active:  # Only deactivate if currently active
                        user.is_active = False
                        
                        results.append(AzureADSyncResult(
                            email=user.email or user.username or "",
                            azure_ad_id=user.azure_ad_id,
                            success=True,
                            user_id=user.id,
                            action="deactivated"
                        ))
                        
                        logger.info(f"Deactivated user {user.email} - not found in Azure AD")
                        
            except Exception as e:
                logger.error(f"Error deactivating users not in Azure AD: {str(e)}")
        
        # Sync groups if requested
        if request.sync_groups:
            try:
                groups = await sync_service.get_all_groups()
                for group in groups[:100]:  # Limit to first 100 groups
                    group_id = group.get("id")
                    group_name = group.get("displayName")
                    if not group_name:
                        continue
                    # Check by azure_ad_group_id OR name to avoid unique conflicts
                    result = await db.execute(
                        select(UserGroup).where(
                            (UserGroup.azure_ad_group_id == group_id) | (UserGroup.name == group_name)
                        )
                    )
                    existing_group = result.scalar_one_or_none()
                    if existing_group:
                        continue
                    try:
                        new_group = UserGroup(
                            name=group_name,
                            description=group.get("description"),
                            azure_ad_group_id=group_id,
                            is_active=True
                        )
                        db.add(new_group)
                        synced_groups += 1
                    except Exception as ie:
                        # Ignore duplicates/races; keep syncing
                        await db.rollback()
                        logger.debug(f"Skipped duplicate group {group_name}: {ie}")
                # commit deferred; a single commit will be executed at the end
            except Exception as e:
                logger.error(f"Error syncing groups: {str(e)}")
        
        # Single batched commit at the end for performance
        await db.commit()

        return AzureADSyncResponse(
            total_fetched=len(azure_users),
            total_synced=len(azure_users),
            successful=successful,
            failed=failed,
            results=results,
            synced_groups=synced_groups
        )
        
    except Exception as e:
        logger.error(f"Error syncing from Azure AD: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to sync from Azure AD: {str(e)}"
        )


@router.get("/azure-ad/groups", response_model=AzureADGroupsResponse, status_code=status.HTTP_200_OK)
async def list_azure_ad_groups(
    limit: int = Query(100, ge=1, le=10000, description="Maximum number of groups to return"),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AzureADGroupsResponse:
    """
    List all groups from Azure AD (admin only).
    
    Returns all security groups and distribution groups from Azure AD.
    This does not sync groups to the database, only retrieves the list.
    """
    try:
        sync_service = AzureADSyncService()
        groups = await sync_service.get_all_groups(max_groups=limit)
        
        # Convert to response format
        group_list = []
        for group in groups:
            group_list.append({
                "id": group.get("id"),
                "displayName": group.get("displayName"),
                "description": group.get("description"),
                "mail": group.get("mail"),
                "securityEnabled": group.get("securityEnabled", False),
                "mailEnabled": group.get("mailEnabled", False)
            })
        
        return AzureADGroupsResponse(
            total_groups=len(group_list),
            groups=group_list
        )
        
    except Exception as e:
        logger.error(f"Error fetching Azure AD groups: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch Azure AD groups: {str(e)}"
        )


@router.get("/azure-ad/groups/search", response_model=AzureADGroupsResponse, status_code=status.HTTP_200_OK)
async def search_azure_ad_groups(
    keyword: str = Query(..., description="Search keyword to filter groups (searches in displayName and description)"),
    max_results: int = Query(100, ge=1, le=10000, description="Maximum number of groups to return"),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AzureADGroupsResponse:
    """
    Search Azure AD groups by keyword (admin only).
    
    Searches groups where the keyword appears in displayName or description
    (case-insensitive partial matching). For example, searching for "finance"
    will return groups like "Finance Team", "Financial Services", etc.
    
    This does not sync groups to the database, only retrieves the list.
    """
    try:
        sync_service = AzureADSyncService()
        groups = await sync_service.search_groups(keyword=keyword, max_results=max_results)
        
        # Convert to response format
        group_list = []
        for group in groups:
            group_list.append({
                "id": group.get("id"),
                "displayName": group.get("displayName"),
                "description": group.get("description"),
                "mail": group.get("mail"),
                "securityEnabled": group.get("securityEnabled", False),
                "mailEnabled": group.get("mailEnabled", False)
            })
        
        return AzureADGroupsResponse(
            total_groups=len(group_list),
            groups=group_list
        )
        
    except Exception as e:
        logger.error(f"Error searching Azure AD groups: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to search Azure AD groups: {str(e)}"
        )


# =============================================================================
# RBAC Admin Endpoints - Role Groups and Individual Access Management
# =============================================================================
# Note: RBAC endpoints are now handled by the dedicated RBAC router
# which is included below. This section is kept for reference but
# the actual endpoints are in the RBAC router.


# User-to-RBAC Sync Endpoint
@router.post("/sync-users-to-rbac")
async def sync_users_to_rbac(
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Sync all users from main User model to RBAC system (admin only).
    
    This creates/updates RBAC users for all active users in the system,
    enabling them to be assigned roles and permissions.
    """
    from aldar_middleware.services.rbac_user_sync import RBACUserSyncService
    
    try:
        sync_service = RBACUserSyncService(db)
        stats = await sync_service.sync_all_users()
        
        return {
            "success": True,
            "message": f"Synced {stats['synced']}/{stats['total']} users to RBAC system",
            "stats": stats
        }
    except Exception as e:
        logger.error(f"Failed to sync users to RBAC: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to sync users: {str(e)}"
        )


# RBAC router is now included directly in the main app to avoid tag duplication
# See: aldar_middleware/web/application.py
