"""
RBAC API Endpoints
RESTful API for agent-based access control management

Note: This API is designed for Azure AD SSO integration where users are synced
from Azure AD using the /admin/azure-ad/sync-users endpoint rather than created 
through this API. The RBAC API focuses on:
- Agent management
- AD group-based access control
- User-agent access checking

For user creation/sync, use: POST /admin/azure-ad/sync-users
"""

from typing import List, Optional, Any, Dict
from uuid import UUID
import uuid
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, Body
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from aldar_middleware.database.base import get_db
from aldar_middleware.models.user import User
from aldar_middleware.models.rbac import RBACAgentPivot
from aldar_middleware.models.menu import Agent
from aldar_middleware.auth.dependencies import get_current_user, get_current_admin_user
from aldar_middleware.services.rbac_service import RBACServiceLayer
from aldar_middleware.services.rbac_pivot_service import RBACPivotService
from aldar_middleware.services.azure_ad_sync import AzureADSyncService
from aldar_middleware.auth.azure_ad import azure_ad_auth
from aldar_middleware.schemas.rbac import (
    ServiceResponse,
    UserServicesResponse,
    RBACStatsResponse, ServiceFilter,
    RBACResponse, UserPivotResponse, UserPivotListResponse
)
from aldar_middleware.exceptions import RBACError, PermissionDeniedError
from aldar_middleware.settings.context import get_correlation_id, get_user_context
from aldar_middleware.monitoring import log_request_response
from aldar_middleware.settings import settings
import logging

# Configuration constants

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/access",
    # Tags are set when router is included in admin routes
)


def audit_log(action: str, resource: str, details: dict, success: bool = True):
    """Log security-sensitive RBAC operations for audit trail"""
    correlation_id = get_correlation_id()
    user_ctx = get_user_context()
    
    log_data = {
        "action": action,
        "resource": resource,
        "details": details,
        "success": success,
        "correlation_id": correlation_id,
        "actor_user_id": user_ctx.user_id if user_ctx else None,
        "actor_username": user_ctx.username if user_ctx else None,
    }
    
    if success:
        logger.info(f"RBAC_AUDIT: {action} on {resource}", extra=log_data)
    else:
        logger.warning(f"RBAC_AUDIT_FAILED: {action} on {resource}", extra=log_data)


def _to_serializable(payload: Any) -> Any:
    """Convert payload to a JSON-serializable structure."""
    if payload is None:
        return None
    if isinstance(payload, BaseModel):
        return payload.model_dump()
    if isinstance(payload, list):
        return [_to_serializable(item) for item in payload]
    if isinstance(payload, dict):
        return {key: _to_serializable(value) for key, value in payload.items()}
    return payload


def _build_rbac_response(
    data: Any = None,
    message: Optional[str] = None,
    success: bool = True,
) -> RBACResponse:
    correlation_id = get_correlation_id()
    return RBACResponse(
        success=success,
        message=message,
        data=data,
        correlation_id=correlation_id,
    )


def _log_to_cosmos(
    request: Request,
    status_code: int,
    request_body: Optional[Dict[str, Any]] = None,
    response_body: Optional[Any] = None,
):
    if not settings.cosmos_logging_enabled:
        return

    correlation_id = get_correlation_id()
    user_ctx = get_user_context()

    '''
    log_request_response(
        correlation_id=correlation_id,
        method=request.method,
        path=request.url.path,
        status_code=status_code,
        request_body=_to_serializable(request_body),
        response_body=_to_serializable(response_body),
        user_id=user_ctx.user_id if user_ctx else None,
    )
    '''


# User Management Endpoints
# Note: Users are synced from Azure AD SSO, not created via API


# AD Group-based Access Control Endpoints
# Note: Agent CRUD operations (create, update, delete, list) are handled by
# /api/v1/admin/agent endpoints. This router only handles RBAC-specific
# operations: AD group assignment and access checking.

@router.get("/current-user/agents", response_model=RBACResponse)
async def get_current_user_agents(
    request: Request, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get all agents the authenticated user has access to based on AD group intersection.
    
    This endpoint:
    1. Gets the authenticated user's AD groups from the pivot table
    2. Gets all agents and their AD groups
    3. Returns agents where there's at least one common AD group UUID
    
    Authentication: Uses JWT token to identify the user (no username parameter needed)
    """
    try:
        rbac_service = RBACServiceLayer(db)
        pivot_service = RBACPivotService(db)
        
        # Get authenticated user's email (used as username in RBAC)
        username = current_user.email
        
        # Get user's AD groups
        user_ad_groups = await pivot_service.get_user_ad_groups(username)
        
        if not user_ad_groups:
            # User has no AD groups, return empty list
            response_payload = UserServicesResponse(
                username=username,
                services=[],
                total_services=0
            )
            response = _build_rbac_response(
                data=response_payload,
                message=f"User '{username}' has no AD groups assigned"
            )
            _log_to_cosmos(
                request,
                status.HTTP_200_OK,
                request_body={"username": username},
                response_body=response
            )
            return response
        
        # Get all agents from pivot table (agents that have AD groups assigned)
        # This is the source of truth for which agents have AD groups
        all_agent_pivots = await pivot_service.list_all_agent_pivots()
        
        # Get all agents from Agent table for full details
        all_agents_from_table = await rbac_service.get_all_services(active_status="all")
        agents_by_name = {agent.name: agent for agent in all_agents_from_table}
        
        # Find agents the user has access to based on AD group intersection
        accessible_agents = []
        user_groups_set = set(user_ad_groups)
        
        # Check access for each agent in pivot table
        for agent_pivot in all_agent_pivots:
            agent_name = agent_pivot.agent_name
            agent_groups = agent_pivot.azure_ad_groups or []
            agent_groups_set = set(agent_groups)
            
            # Check if there's any intersection
            if user_groups_set & agent_groups_set:
                # Try to get the agent from Agent table if it exists
                agent_from_table = agents_by_name.get(agent_name)
                if agent_from_table:
                    accessible_agents.append(agent_from_table)
                else:
                    # Agent exists in pivot but not in Agent table
                    # This shouldn't normally happen, but we'll log a warning
                    logger.warning(
                        f"Agent '{agent_name}' has AD groups in pivot table but doesn't exist in Agent table. "
                        f"Please create the agent in the Agent table first."
                    )
                    # Skip agents that don't exist in Agent table
                    # They should be created in Agent table first before assigning AD groups
                    continue
        
        # Convert agents to ServiceResponse format
        agent_responses = [ServiceResponse.model_validate(agent) for agent in accessible_agents]
        
        response_payload = UserServicesResponse(
            username=username,
            services=agent_responses,  # Use 'services' field as per schema
            total_services=len(agent_responses)
        )
        response = _build_rbac_response(
            data=response_payload,
            message=f"Retrieved {len(accessible_agents)} accessible agents for user '{username}'"
        )
        _log_to_cosmos(
            request,
            status.HTTP_200_OK,
            request_body={"username": username},
            response_body=response
        )
        return response
    except RBACError as e:
        _log_to_cosmos(
            request,
            status.HTTP_404_NOT_FOUND,
            request_body={"username": username},
            response_body={"success": False, "message": str(e)}
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting user agents for '{username}': {str(e)}", exc_info=True)
        response = _build_rbac_response(message=str(e), success=False)
        _log_to_cosmos(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            request_body={"username": username},
            response_body=response
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get user agents: {str(e)}"
        )


# Statistics and Reporting
@router.get("/stats", response_model=RBACResponse)
async def get_rbac_stats(request: Request, db: AsyncSession = Depends(get_db)):
    """Get RBAC system statistics"""
    try:
        rbac_service = RBACServiceLayer(db)
        stats = await rbac_service.get_rbac_stats()
        stats_model = RBACStatsResponse(**stats)
        response = _build_rbac_response(data=stats_model)
        _log_to_cosmos(
            request,
            status.HTTP_200_OK,
            request_body={},
            response_body=response
        )
        return response
    except Exception as e:
        _log_to_cosmos(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            request_body={},
            response_body={"success": False, "message": str(e)}
        )
        raise


@router.get("/users", response_model=RBACResponse)
async def list_rbac_users(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get list of all RBAC users from the user pivot table.
    
    Returns all users with their Azure AD groups and metadata.
    This endpoint lists users from the rbac_user_pivot table, which is
    the source of truth for AD group-based access control.
    """
    try:
        pivot_service = RBACPivotService(db)
        user_pivots = await pivot_service.list_all_user_pivots()
        
        # Convert to response format
        user_responses = []
        for pivot in user_pivots:
            user_responses.append(UserPivotResponse(
                id=pivot.id,
                email=pivot.email,
                azure_ad_groups=pivot.azure_ad_groups or [],
                created_at=pivot.created_at.isoformat() if pivot.created_at else None,
                updated_at=pivot.updated_at.isoformat() if pivot.updated_at else None
            ))
        
        response_payload = UserPivotListResponse(
            users=user_responses,
            total_users=len(user_responses)
        )
        
        response = _build_rbac_response(
            data=response_payload,
            message=f"Retrieved {len(user_responses)} users from pivot table"
        )
        
        _log_to_cosmos(
            request,
            status.HTTP_200_OK,
            request_body={},
            response_body=response
        )
        return response
    except Exception as e:
        logger.error(f"Error listing RBAC users: {str(e)}", exc_info=True)
        response = _build_rbac_response(message=str(e), success=False)
        _log_to_cosmos(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            request_body={},
            response_body=response
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve users: {str(e)}"
        )


# Health Check
@router.get("/health", response_model=RBACResponse)
async def rbac_health_check(request: Request, db: AsyncSession = Depends(get_db)):
    """Health check for RBAC system"""
    try:
        rbac_service = RBACServiceLayer(db)
        # Simple health check - try to get agents
        agents = await rbac_service.get_all_services(active_status="active")
        response = _build_rbac_response(
            data={
            "status": "healthy",
                "total_agents": len(agents)
            },
            message="RBAC system is operational"
        )
        _log_to_cosmos(
            request,
            status.HTTP_200_OK,
            request_body={},
            response_body=response
        )
        return response
    except Exception as e:
        response = _build_rbac_response(
            data={
            "status": "unhealthy",
                "error": str(e)
            },
            message="RBAC system has issues",
            success=False
        )
        _log_to_cosmos(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            request_body={},
            response_body=response
        )
        return response


# ============================================================================
# AD Group-based Access Control Endpoints
# ============================================================================

@router.post("/agents/{agent_name}/ad-groups", response_model=RBACResponse)
async def assign_agent_ad_groups(
    agent_name: str,
    ad_groups: List[str] = Body(..., description="List of Azure AD group UUIDs (as strings) to assign to the agent"),
    agent_ad_groups_metadata: List[Dict[str, str]] = Body(..., description="List of AD group metadata objects with id and name: [{\"id\": \"uuid1\", \"name\": \"group1\"}, ...]"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None
) -> RBACResponse:
    """Assign Azure AD group UUIDs and metadata to an agent.
    
    This endpoint updates the rbac_agent_pivot table with the provided
    list of Azure AD group UUIDs and metadata for the specified agent. The old entry is
    replaced with the new one.
    
    Access control is based on AD group intersection:
    - User's AD groups ∩ Agent's AD groups = non-empty → User has access
    
    Multiple agents can share the same AD group UUIDs. If a user has at least
    one matching AD group UUID, they have access to all agents with that group.
    
    Args:
        agent_name: Name of the agent to assign groups to
        ad_groups: List of Azure AD group UUIDs (as strings) - REQUIRED
        agent_ad_groups_metadata: List of metadata objects with id and name - REQUIRED:
            [{"id": "uuid1", "name": "group1"}, {"id": "uuid2", "name": "group2"}, ...]
        
    Returns:
        Updated agent pivot entry
        
    Raises:
        HTTPException: If agent doesn't exist in agents table
    """
    try:
        # Validate that the agent exists in the agents table
        agent_result = await db.execute(
            select(Agent).where(Agent.name == agent_name)
        )
        agent = agent_result.scalar_one_or_none()
        
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent '{agent_name}' not found in agents table. Please create the agent first before assigning AD groups."
            )
        
        pivot_service = RBACPivotService(db)
        agent_pivot = await pivot_service.assign_agent_ad_groups(
            agent_name, 
            ad_groups, 
            ad_groups_metadata=agent_ad_groups_metadata
        )
        
        # Build response data safely
        response_data = {
            "agent_name": agent_pivot.agent_name,
            "azure_ad_groups": agent_pivot.azure_ad_groups,
            "agent_ad_groups_metadata": agent_pivot.agent_ad_groups_metadata,
        }
        
        # Safely handle datetime fields
        try:
            if agent_pivot.created_at:
                response_data["created_at"] = agent_pivot.created_at.isoformat()
            if agent_pivot.updated_at:
                response_data["updated_at"] = agent_pivot.updated_at.isoformat()
        except Exception as dt_error:
            logger.warning(f"Error serializing datetime fields: {dt_error}")
        
        try:
            audit_log(
                action="assign_agent_ad_groups",
                resource=f"agent:{agent_name}",
                details={"ad_groups": ad_groups},
                success=True
            )
        except Exception as audit_error:
            logger.warning(f"Error in audit_log: {audit_error}")
        
        # Build response with error handling
        try:
            response = _build_rbac_response(
                message=f"Successfully assigned {len(ad_groups)} AD groups to agent '{agent_name}'",
                data=response_data,
                success=True
            )
        except Exception as response_error:
            logger.error(f"Error building RBAC response: {response_error}", exc_info=True)
            # Fallback to simple response
            correlation_id = get_correlation_id() or "unknown"
            response = RBACResponse(
                success=True,
                message=f"Successfully assigned {len(ad_groups)} AD groups to agent '{agent_name}'",
                data=response_data,
                correlation_id=correlation_id
            )
        
        try:
            _log_to_cosmos(
                request,
                status.HTTP_200_OK,
                response_body=response
            )
        except Exception as log_error:
            # Don't fail the request if logging fails
            logger.warning(f"Error logging to Cosmos: {log_error}")
        
        return response
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error assigning AD groups to agent '{agent_name}': {error_msg}", exc_info=True)
        
        try:
            audit_log(
                action="assign_agent_ad_groups",
                resource=f"agent:{agent_name}",
                details={"error": error_msg},
                success=False
            )
        except Exception as audit_error:
            logger.warning(f"Error in audit_log during exception: {audit_error}")
        
        try:
            response = _build_rbac_response(message=error_msg, success=False)
            _log_to_cosmos(
                request,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                response_body=response
            )
        except Exception as response_error:
            logger.error(f"Error building error response: {response_error}")
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to assign AD groups to agent: {error_msg}"
        )


@router.get("/agents/{agent_name}/ad-groups", response_model=RBACResponse)
async def get_agent_ad_groups(
    agent_name: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None
) -> RBACResponse:
    """Get Azure AD group UUIDs and metadata assigned to an agent.
    
    Args:
        agent_name: Name of the agent to lookup
        
    Returns:
        Azure AD group UUIDs and metadata assigned to the agent
    """
    try:
        pivot_service = RBACPivotService(db)
        ad_groups = await pivot_service.get_agent_ad_groups(agent_name)
        
        # Get the full pivot entry to include metadata
        result = await db.execute(
            select(RBACAgentPivot).where(RBACAgentPivot.agent_name == agent_name)
        )
        agent_pivot = result.scalar_one_or_none()
        
        response_data = {
            "agent_name": agent_name,
            "azure_ad_groups": ad_groups,
        }
        
        if agent_pivot and agent_pivot.agent_ad_groups_metadata:
            response_data["agent_ad_groups_metadata"] = agent_pivot.agent_ad_groups_metadata
        
        response = _build_rbac_response(
            message=f"Retrieved AD groups for agent '{agent_name}'",
            data=response_data,
            success=True
        )
        
        _log_to_cosmos(
            request,
            status.HTTP_200_OK,
            response_body=response
        )
        return response
        
    except Exception as e:
        logger.error(f"Error retrieving AD groups for agent '{agent_name}': {str(e)}", exc_info=True)
        response = _build_rbac_response(message=str(e), success=False)
        _log_to_cosmos(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            response_body=response
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve AD groups for agent: {str(e)}"
        )


@router.post("/current-user/ad-groups", response_model=RBACResponse)
async def sync_current_user_ad_groups(
    ad_groups: List[str] = Body(..., description="List of Azure AD group UUIDs (as strings) to assign to the user"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None
) -> RBACResponse:
    """Sync authenticated user's Azure AD group UUIDs (for testing/simulation).
    
    This endpoint simulates the login flow where a user's AD groups are fetched
    and synced to the pivot table. In production, this happens automatically
    during Azure AD login via the auth callback.
    
    This endpoint is useful for:
    - Testing AD group-based access control
    - Simulating login flows
    - Manual AD group assignment for testing
    
    Authentication: Uses JWT token to identify the user (no username parameter needed)
    
    Args:
        ad_groups: List of Azure AD group UUIDs (as strings) to assign
        
    Returns:
        Updated user pivot entry with synced AD groups
    """
    try:
        # Get authenticated user's email (used as username in RBAC)
        user_email = current_user.email
        
        pivot_service = RBACPivotService(db)
        user_pivot = await pivot_service.sync_user_ad_groups_direct(user_email, ad_groups)
        
        audit_log(
            action="sync_user_ad_groups",
            resource="user_pivot",
            details={
                "user_email": user_email,
                "ad_group_count": len(ad_groups)
            }
        )
        
        response = _build_rbac_response(
            message=f"Successfully synced {len(ad_groups)} AD groups for user '{user_email}'",
            data={
                "email": user_pivot.email,
                "azure_ad_groups": user_pivot.azure_ad_groups,
                "created_at": user_pivot.created_at.isoformat() if user_pivot.created_at else None,
                "updated_at": user_pivot.updated_at.isoformat() if user_pivot.updated_at else None,
            },
            success=True
        )
        
        _log_to_cosmos(
            request,
            status.HTTP_200_OK,
            request_body={"user_email": user_email, "ad_groups": ad_groups},
            response_body=response
        )
        return response
        
    except Exception as e:
        logger.error(f"Error syncing AD groups for user '{current_user.email}': {str(e)}", exc_info=True)
        audit_log(
            action="sync_user_ad_groups",
            resource="user_pivot",
            details={
                "user_email": current_user.email,
                "error": str(e)
            },
            success=False
        )
        response = _build_rbac_response(message=str(e), success=False)
        _log_to_cosmos(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            request_body={"user_email": current_user.email, "ad_groups": ad_groups},
            response_body=response
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to sync AD groups for user: {str(e)}"
        )


@router.get("/current-user/ad-groups", response_model=RBACResponse)
async def get_current_user_ad_groups(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None
) -> RBACResponse:
    """Get Azure AD group UUIDs for the authenticated user.
    
    Authentication: Uses JWT token to identify the user (no username parameter needed)
    
    Returns:
        List of Azure AD group UUIDs (as strings) for the authenticated user
    """
    try:
        # Get authenticated user's email (used as username in RBAC)
        user_email = current_user.email
        
        pivot_service = RBACPivotService(db)
        ad_groups = await pivot_service.get_user_ad_groups(user_email)
        
        response = _build_rbac_response(
            message=f"Retrieved AD groups for user '{user_email}'",
            data={
                "email": user_email,
                "azure_ad_groups": ad_groups,
            },
            success=True
        )
        
        _log_to_cosmos(
            request,
            status.HTTP_200_OK,
            response_body=response
        )
        return response
        
    except Exception as e:
        logger.error(f"Error retrieving AD groups for user '{user_email}': {str(e)}", exc_info=True)
        response = _build_rbac_response(message=str(e), success=False)
        _log_to_cosmos(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            response_body=response
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve AD groups for user: {str(e)}"
        )


@router.get("/current-user/agents/{agent_name}/access", response_model=RBACResponse)
async def check_current_user_agent_access(
    agent_name: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None
) -> RBACResponse:
    """Check if the authenticated user has access to an agent based on AD group UUID intersection.
    
    Access control is based solely on Azure AD group intersection:
    - User's AD groups ∩ Agent's AD groups = non-empty → User has access
    - If there's any overlap in AD group UUIDs, access is granted
    
    No roles are involved - only AD group intersection determines access.
    
    Authentication: Uses JWT token to identify the user (no username parameter needed)
    
    Args:
        agent_name: Agent name to check access for
        
    Returns:
        Access check result with details including user and agent AD groups
    """
    try:
        # Get authenticated user's email (used as username in RBAC)
        user_email = current_user.email
        
        pivot_service = RBACPivotService(db)
        has_access = await pivot_service.check_user_has_access_to_agent(user_email, agent_name)
        user_groups = await pivot_service.get_user_ad_groups(user_email)
        agent_groups = await pivot_service.get_agent_ad_groups(agent_name)
        
        response = _build_rbac_response(
            message=f"Access check completed for user '{user_email}' to agent '{agent_name}'",
            data={
                "email": user_email,
                "agent_name": agent_name,
                "has_access": has_access,
                "user_ad_groups": user_groups,
                "agent_ad_groups": agent_groups,
            },
            success=True
        )
        
        _log_to_cosmos(
            request,
            status.HTTP_200_OK,
            response_body=response
        )
        return response
        
    except Exception as e:
        logger.error(f"Error checking access for user '{current_user.email}' to agent '{agent_name}': {str(e)}", exc_info=True)
        response = _build_rbac_response(message=str(e), success=False)
        _log_to_cosmos(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            response_body=response
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to check access: {str(e)}"
        )


# ============================================================================
# Test Endpoint: Get Current User's AD Groups
# ============================================================================

@router.get("/test/my-ad-groups", response_model=RBACResponse)
async def get_my_ad_groups(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None
) -> RBACResponse:
    """Get Azure AD groups for the currently logged-in user.
    
    This endpoint:
    1. Uses the user's stored refresh token to get a new Azure AD access token
    2. Calls Microsoft Graph API to fetch the user's AD groups
    3. Returns the groups
    
    This is a test endpoint to verify AD group retrieval works correctly.
    """
    try:
        # Check if user has a refresh token stored
        if not current_user.azure_ad_refresh_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User does not have an Azure AD refresh token. Please login via Azure AD first."
            )
        
        # Get a new Azure AD access token using the refresh token
        logger.info(f"Refreshing Azure AD access token for user: {current_user.email}")
        token_response = await azure_ad_auth.refresh_access_token(current_user.azure_ad_refresh_token)
        azure_access_token = token_response.get("access_token")
        
        if not azure_access_token:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to get Azure AD access token from refresh token"
            )
        
        logger.info(f"Successfully obtained Azure AD access token for user: {current_user.email}")
        
        # Get user's AD groups using the Azure AD access token
        logger.info(f"Fetching AD groups for user: {current_user.email}")
        ad_groups = await azure_ad_auth.get_user_groups(azure_access_token)
        
        logger.info(f"Retrieved {len(ad_groups)} AD groups for user: {current_user.email}")
        
        response = _build_rbac_response(
            message=f"Successfully retrieved {len(ad_groups)} Azure AD groups",
            data={
                "email": current_user.email,
                "azure_ad_groups": ad_groups,
                "total_groups": len(ad_groups)
            },
            success=True
        )
        
        _log_to_cosmos(
            request,
            status.HTTP_200_OK,
            response_body=response
        )
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting AD groups for user '{current_user.email}': {str(e)}", exc_info=True)
        response = _build_rbac_response(
            message=f"Failed to get AD groups: {str(e)}",
            success=False
        )
        _log_to_cosmos(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            response_body=response
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get AD groups: {str(e)}"
        )


# ============================================================================
# Azure AD Groups Management Endpoints
# ============================================================================

@router.get("/azure-ad/groups", response_model=RBACResponse)
async def list_all_azure_ad_groups(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None
) -> RBACResponse:
    """List all Azure AD groups (no filters).
    
    This endpoint returns all groups from Azure AD without any filtering.
    Internally uses: GET https://graph.microsoft.com/v1.0/groups
    """
    try:
        sync_service = AzureADSyncService()
        groups = await sync_service.get_all_groups(max_groups=10000)
        
        response = _build_rbac_response(
            message=f"Retrieved {len(groups)} Azure AD groups",
            data={
                "total_groups": len(groups),
                "groups": groups
            },
            success=True
        )
        
        _log_to_cosmos(
            request,
            status.HTTP_200_OK,
            response_body=response
        )
        return response
        
    except Exception as e:
        logger.error(f"Error listing Azure AD groups: {str(e)}", exc_info=True)
        response = _build_rbac_response(message=str(e), success=False)
        _log_to_cosmos(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            response_body=response
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list Azure AD groups: {str(e)}"
        )


@router.get("/azure-ad/groups/paginated", response_model=RBACResponse)
async def list_azure_ad_groups_paginated(
    top: int = Query(999, ge=1, le=999, description="Maximum number of groups per page (max: 999)"),
    select: Optional[str] = Query(None, description="Comma-separated list of fields to select (e.g., 'id,displayName,description'). If not provided, all fields are returned"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None
) -> RBACResponse:
    """List Azure AD groups with pagination support.
    
    This endpoint supports pagination and field selection.
    Internally uses: GET https://graph.microsoft.com/v1.0/groups?$top={top}&$select={select}
    
    Args:
        top: Maximum number of groups per page (default: 999, max: 999)
        select: Comma-separated list of fields to select (e.g., 'id,displayName,description,mailEnabled,securityEnabled')
                If not provided, all fields are returned
    """
    try:
        sync_service = AzureADSyncService()
        result = await sync_service.get_groups_with_pagination(top=top, select_fields=select)
        
        response = _build_rbac_response(
            message=f"Retrieved {result['count']} Azure AD groups",
            data=result,
            success=True
        )
        
        _log_to_cosmos(
            request,
            status.HTTP_200_OK,
            response_body=response
        )
        return response
        
    except Exception as e:
        logger.error(f"Error listing Azure AD groups with pagination: {str(e)}", exc_info=True)
        response = _build_rbac_response(message=str(e), success=False)
        _log_to_cosmos(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            response_body=response
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list Azure AD groups: {str(e)}"
        )


@router.get("/azure-ad/groups/security-only", response_model=RBACResponse)
async def list_security_groups_only(
    select: Optional[str] = Query(None, description="Comma-separated list of fields to select (e.g., 'id,displayName,description'). Default: 'id,displayName,description'"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None
) -> RBACResponse:
    """List only security groups from Azure AD.
    
    This endpoint returns only security-enabled groups.
    Internally uses: GET https://graph.microsoft.com/v1.0/groups?$filter=securityEnabled eq true&$select={select}
    
    Args:
        select: Comma-separated list of fields to select (default: 'id,displayName,description')
    """
    try:
        sync_service = AzureADSyncService()
        groups = await sync_service.get_security_groups_only(select_fields=select)
        
        response = _build_rbac_response(
            message=f"Retrieved {len(groups)} security groups",
            data={
                "total_groups": len(groups),
                "groups": groups
            },
            success=True
        )
        
        _log_to_cosmos(
            request,
            status.HTTP_200_OK,
            response_body=response
        )
        return response
        
    except Exception as e:
        logger.error(f"Error listing security groups: {str(e)}", exc_info=True)
        response = _build_rbac_response(message=str(e), success=False)
        _log_to_cosmos(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            response_body=response
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list security groups: {str(e)}"
        )


@router.get("/azure-ad/groups/{group_id}/validate", response_model=RBACResponse)
async def validate_azure_ad_group(
    group_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None
) -> RBACResponse:
    """Validate an Azure AD group by its ID.
    
    This endpoint validates if a group exists and returns its details.
    Internally uses: GET https://graph.microsoft.com/v1.0/groups/{group_id}
    
    Args:
        group_id: Azure AD group ID (UUID)
    """
    try:
        sync_service = AzureADSyncService()
        group = await sync_service.validate_group_by_id(group_id)
        
        if not group:
            response = _build_rbac_response(
                message=f"Group with ID '{group_id}' not found",
                data={"group_id": group_id, "exists": False},
                success=False
            )
            _log_to_cosmos(
                request,
                status.HTTP_404_NOT_FOUND,
                response_body=response
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group with ID '{group_id}' not found"
            )
        
        response = _build_rbac_response(
            message=f"Group '{group_id}' validated successfully",
            data={"group_id": group_id, "exists": True, "group": group},
            success=True
        )
        
        _log_to_cosmos(
            request,
            status.HTTP_200_OK,
            response_body=response
        )
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error validating group '{group_id}': {str(e)}", exc_info=True)
        response = _build_rbac_response(message=str(e), success=False)
        _log_to_cosmos(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            response_body=response
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to validate group: {str(e)}"
        )


@router.get("/azure-ad/groups/search/basic", response_model=RBACResponse)
async def search_azure_ad_groups_basic(
    query: str = Query(..., description="Search query string (e.g., 'Finance')"),
    select: Optional[str] = Query(None, description="Comma-separated list of fields to select (e.g., 'id,displayName,description,mailEnabled,securityEnabled')"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None
) -> RBACResponse:
    """Basic search for Azure AD groups starting with query string.
    
    This endpoint searches groups where displayName starts with the query.
    Internally uses: GET https://graph.microsoft.com/v1.0/groups?$filter=startswith(displayName,'{query}')
    
    Args:
        query: Search query string (e.g., "Finance")
        select: Optional comma-separated list of fields to select
    """
    try:
        sync_service = AzureADSyncService()
        groups = await sync_service.search_groups_basic(query=query, select_fields=select)
        
        response = _build_rbac_response(
            message=f"Found {len(groups)} groups matching query '{query}'",
            data={
                "query": query,
                "total_groups": len(groups),
                "groups": groups
            },
            success=True
        )
        
        _log_to_cosmos(
            request,
            status.HTTP_200_OK,
            response_body=response
        )
        return response
        
    except Exception as e:
        logger.error(f"Error searching groups with query '{query}': {str(e)}", exc_info=True)
        response = _build_rbac_response(message=str(e), success=False)
        _log_to_cosmos(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            response_body=response
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to search groups: {str(e)}"
        )


@router.get("/azure-ad/groups/search/advanced", response_model=RBACResponse)
async def search_azure_ad_groups_advanced(
    query: str = Query(..., description="Search query string (e.g., 'Finance')"),
    security_enabled_only: bool = Query(False, description="If true, only return security groups"),
    select: Optional[str] = Query(None, description="Comma-separated list of fields to select (e.g., 'id,displayName,description,mailEnabled,securityEnabled')"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None
) -> RBACResponse:
    """Advanced search for Azure AD groups with multiple filters.
    
    This endpoint searches groups with optional security group filter.
    Internally uses: GET https://graph.microsoft.com/v1.0/groups?$filter=startswith(displayName,'{query}') and securityEnabled eq true
    
    Args:
        query: Search query string (e.g., "Finance")
        security_enabled_only: If true, only return security groups
        select: Optional comma-separated list of fields to select
    """
    try:
        sync_service = AzureADSyncService()
        groups = await sync_service.search_groups_advanced(
            query=query,
            security_enabled_only=security_enabled_only,
            select_fields=select
        )
        
        response = _build_rbac_response(
            message=f"Found {len(groups)} groups matching advanced query '{query}'",
            data={
                "query": query,
                "security_enabled_only": security_enabled_only,
                "total_groups": len(groups),
                "groups": groups
            },
            success=True
        )
        
        _log_to_cosmos(
            request,
            status.HTTP_200_OK,
            response_body=response
        )
        return response
        
    except Exception as e:
        logger.error(f"Error in advanced group search with query '{query}': {str(e)}", exc_info=True)
        response = _build_rbac_response(message=str(e), success=False)
        _log_to_cosmos(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            response_body=response
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to search groups: {str(e)}"
        )


# ============================================================================
# Admin-Only Endpoint: Get AD Group by UUID
# ============================================================================

@router.get("/admin/ad-groups/{group_uuid}", response_model=RBACResponse)
async def get_ad_group_by_uuid(
    group_uuid: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
    request: Request = None
) -> RBACResponse:
    """Get Azure AD group details by UUID (Admin only).
    
    This endpoint calls Microsoft Graph API to retrieve AD group information
    by its UUID. This is useful for resolving group UUIDs to their display names
    and other metadata.
    
    **Admin Only**: Requires is_admin=true
    
    Args:
        group_uuid: Azure AD group UUID (GUID format)
        
    Returns:
        AD group details including id, displayName, description, etc.
        
    Raises:
        HTTPException: 
            - 403 if user is not admin
            - 404 if group not found
            - 500 on Microsoft Graph API errors
    """
    try:
        sync_service = AzureADSyncService()
        group_info = await sync_service.validate_group_by_id(group_uuid)
        
        if not group_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"AD group with UUID '{group_uuid}' not found"
            )
        
        response = _build_rbac_response(
            message=f"Successfully retrieved AD group '{group_info.get('displayName', 'N/A')}'",
            data={
                "group_uuid": group_uuid,
                "group": group_info
            },
            success=True
        )
        
        _log_to_cosmos(
            request,
            status.HTTP_200_OK,
            request_body={"group_uuid": group_uuid},
            response_body=response
        )
        return response
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Error retrieving AD group '{group_uuid}': {str(e)}", exc_info=True)
        response = _build_rbac_response(message=str(e), success=False)
        _log_to_cosmos(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            request_body={"group_uuid": group_uuid},
            response_body=response
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve AD group: {str(e)}"
        )