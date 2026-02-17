"""Azure AD OBO (On-Behalf-Of) flow API routes for accessing User Agents in AIQ 2.5."""

import asyncio
import base64
import json
import logging
import secrets
from datetime import datetime
from typing import Optional, Dict, Any, List
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Body, Depends, status
from fastapi.responses import RedirectResponse, JSONResponse, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import select, func, delete, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from aldar_middleware.auth.azure_ad_obo import azure_ad_obo_auth
from aldar_middleware.auth.obo_utils import exchange_token_obo, verify_mcp_token, create_mcp_token
from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.settings import settings
from aldar_middleware.database.base import get_db
from aldar_middleware.models.menu import Agent
from aldar_middleware.models.agent_tags import AgentTag
from aldar_middleware.models.agent_configuration import AgentConfiguration
from aldar_middleware.models.user import User
from aldar_middleware.models.user_agent_access import UserAgentAccess
from aldar_middleware.models.attachment import Attachment
from aldar_middleware.models.admin_config import AdminConfig
from aldar_middleware.utils.helpers import is_uuid
from aldar_middleware.services.postgres_logs_service import PostgresLogsService
from aldar_middleware.services.agent_available_cache import get_agent_available_cache
from aldar_middleware.services.user_memory_cache import get_user_memory_cache

logger = logging.getLogger(__name__)

router = APIRouter()

# Security scheme for Authorization header
security = HTTPBearer(auto_error=False)


@router.options("/api/knowledge-sources", include_in_schema=False)
@router.options("/api/knowledge-sources/direct", include_in_schema=False)
@router.options("/api/knowledge-sources/auth-url", include_in_schema=False)
async def options_handler():
    """
    Handle CORS preflight requests.
    
    This endpoint is hidden from Swagger documentation as it's automatically
    handled by CORS middleware. It's only here for explicit CORS header control.
    """
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, Accept",
            "Access-Control-Max-Age": "3600",
        }
    )

# In-memory storage for pending requests (use Redis in production)
pending_requests: Dict[str, Dict[str, Any]] = {}


async def invalidate_agent_cache_if_active(status: str, agent_name: str = "agent") -> None:
    """
    Invalidate agent cache if status is 'active' (case-insensitive).
    
    Handles status values like 'ACTIVE', 'active', 'Active', etc.
    
    Args:
        status: The agent status (e.g., 'active', 'ACTIVE', 'draft', 'DRAFT')
        agent_name: Name of the agent for logging purposes
    """
    if status and status.lower() == "active":
        try:
            cache = get_agent_available_cache()
            if cache:
                await cache.invalidate_all()
                logger.info(f"✅ Invalidated agent_available cache after activating agent: {agent_name}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to invalidate cache for agent {agent_name}: {e}")


async def get_user_access_token_auto(
    current_user: User,
    db: AsyncSession,
    provided_token: Optional[str] = None
) -> Optional[str]:
    """
    Automatically extract user_access_token from refresh token or use provided token.
    
    Priority:
    1. Use provided_token if given
    2. Auto-refresh from current_user.azure_ad_refresh_token
    3. Return None if neither available
    """
    # If token is provided, use it
    if provided_token:
        logger.info("✓ Using provided user_access_token")
        return provided_token
    
    # Auto-extract from refresh token
    if current_user.azure_ad_refresh_token:
        logger.info("🔄 Auto-refreshing Azure AD token from stored refresh token for OBO exchange...")
        try:
            from aldar_middleware.auth.azure_ad import azure_ad_auth
            import httpx
            
            # Refresh token with OBO scope
            refresh_data = {
                "client_id": settings.azure_client_id,
                "client_secret": settings.azure_client_secret,
                "refresh_token": current_user.azure_ad_refresh_token,
                "grant_type": "refresh_token",
                "scope": f"openid profile offline_access {settings.azure_client_id}/.default"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{azure_ad_auth.authority}/oauth2/v2.0/token",
                    data=refresh_data
                )
                
                if response.status_code == 200:
                    token_response = response.json()
                    user_access_token = token_response.get("access_token")
                    if user_access_token:
                        logger.info("✓ Azure AD user access token auto-obtained from refresh token for OBO exchange")
                        # Update refresh token if new one provided
                        new_refresh_token = token_response.get("refresh_token")
                        if new_refresh_token:
                            current_user.azure_ad_refresh_token = new_refresh_token
                            await db.commit()
                        return user_access_token
                    else:
                        logger.warning("⚠️  Refresh token response did not contain access_token")
                else:
                    logger.warning(f"⚠️  Failed to auto-refresh Azure AD token: {response.status_code} - {response.text}")
        except Exception as e:
            logger.warning(f"⚠️  Failed to auto-refresh Azure AD token from refresh token: {e}")
    else:
        logger.warning("⚠️  No Azure AD refresh token stored for user - OBO token exchange will be skipped")
    
    return None


class DirectOBORequest(BaseModel):
    """Request model for direct OBO API call."""

    user_access_token: str = Field(
        ...,
        description="User's Azure AD access token with audience = your app's client ID",
        example="eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsIng1dCI6Ik1uQ19WWmNBVGZNNXp..."
    )
    page: int = Field(1, ge=1, description="Page number (starts from 1)")
    size: int = Field(20, ge=1, le=100, description="Number of items per page")
    search: str = Field("", description="Search query string")


class CreateUserAgentRequest(BaseModel):
    """Request model for creating user agent via OBO."""

    user_access_token: Optional[str] = Field(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)",
        example="eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsIng1dCI6Ik1uQ19WWmNBVGZNNXp..."
    )
    name: str = Field(..., description="Agent name")
    prompt: Optional[str] = Field(None, description="Agent prompt/instructions")
    description: Optional[str] = Field(None, description="Agent description")
    isWebSearchEnabled: bool = Field(False, description="Enable web search for agent")
    selectedAgentType: str = Field("PRECISE", description="Agent type: PRECISE or CREATIVE")
    icon_attachment_id: Optional[str] = Field(
        None,
        description="Attachment ID (UUID) from /api/attachments/upload. The icon will be resolved to a blob URL and returned in the response."
    )


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


async def _get_user_agents_config(db: AsyncSession) -> Optional[Dict[str, Any]]:
    """
    Get predefined configuration for user agents from admin_config table.
    
    Fetches the config with key='USER_AGENTS' and returns the value JSON.
    Returns None if not found.
    """
    try:
        result = await db.execute(
            select(AdminConfig).where(AdminConfig.key == "USER_AGENTS")
        )
        config = result.scalar_one_or_none()
        if config and config.value:
            logger.info("✓ Loaded USER_AGENTS config from admin_config table")
            return config.value
        else:
            logger.debug("No USER_AGENTS config found in admin_config table")
            return None
    except Exception as e:
        logger.warning(f"Failed to load USER_AGENTS config: {str(e)}")
        return None


async def _update_custom_feature_toggle(
    db: AsyncSession, 
    agent_main_id: int, 
    is_web_search_enabled: bool,
    selected_agent_type: str = "PRECISE",
    agent_uuid: str = None
) -> None:
    """
    Update agent_configuration table with custom feature configurations.
    
    Deletes existing configurations if exist, then re-adds with new values.
    Creates three configurations:
    - custom_feature_toggle: Web Search on/off
    - custom_feature_dropdown: Mode (PRECISE/CREATIVE)
    - custom_feature_text: documentMyAgentId
    
    Args:
        db: Database session
        agent_main_id: The primary key (id) of the agent in agents table
        is_web_search_enabled: Value for Web Search field's is_default
        selected_agent_type: PRECISE or CREATIVE
        agent_uuid: The agent's UUID (agent_id field)
    """
    try:
        # Delete existing configurations if exist
        await db.execute(
            delete(AgentConfiguration).where(
                AgentConfiguration.agent_id == agent_main_id,
                AgentConfiguration.configuration_name.in_([
                    "custom_feature_toggle",
                    "custom_feature_dropdown", 
                    "custom_feature_text"
                ])
            )
        )
        
        # 1. Create custom_feature_toggle configuration (Web Search)
        feature_toggle_config = AgentConfiguration(
            agent_id=agent_main_id,
            configuration_name="custom_feature_toggle",
            type="object",
            values={
                "enabled": True,
                "fields": [
                    {
                        "field_name": "Web Search",
                        "is_default": is_web_search_enabled,
                        "field_icon": None
                    }
                ]
            }
        )
        db.add(feature_toggle_config)
        
        # 2. Create custom_feature_dropdown configuration (Mode: PRECISE/CREATIVE)
        feature_dropdown_config = AgentConfiguration(
            agent_id=agent_main_id,
            configuration_name="custom_feature_dropdown",
            type="object",
            values={
                "enabled": True,
                "fields": [
                    {
                        "field_name": "Mode",
                        "field_icon": None,
                        "options": [
                            {
                                "title_name": "Precise",
                                "value": "PRECISE",
                                "is_default": selected_agent_type == "PRECISE",
                                "option_icon": None
                            },
                            {
                                "title_name": "Creative",
                                "value": "CREATIVE",
                                "is_default": selected_agent_type == "CREATIVE",
                                "option_icon": None
                            }
                        ]
                    }
                ]
            }
        )
        db.add(feature_dropdown_config)
        
        # 3. Create custom_feature_text configuration (documentMyAgentId)
        feature_text_config = AgentConfiguration(
            agent_id=agent_main_id,
            configuration_name="custom_feature_text",
            type="object",
            values={
                "enabled": True,
                "fields": [
                    {
                        "field_name": "documentMyAgentId",
                        "field_value": agent_uuid or ""
                    }
                ]
            }
        )
        db.add(feature_text_config)
        
        logger.info(f"✓ Updated agent configurations for agent {agent_main_id}:")
        logger.info(f"   → custom_feature_toggle: Web Search = {is_web_search_enabled}")
        logger.info(f"   → custom_feature_dropdown: Mode = {selected_agent_type}")
        logger.info(f"   → custom_feature_text: documentMyAgentId = {agent_uuid}")
        
    except Exception as e:
        logger.warning(f"Failed to update agent configurations: {str(e)}")
        raise


class VerifyMCPTokenRequest(BaseModel):
    """Request model for verifying MCP token."""

    user_access_token: str = Field(
        ...,
        description="User's Azure AD access token with audience = your app's client ID",
        example="eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsIng1dCI6Ik1uQ19WWmNBVGZNNXp..."
    )


class UserProfileUpdateRequest(BaseModel):
    """Request model for updating user profile (memory toggle)."""

    isMemoryEnabled: Optional[bool] = Field(
        None,
        description="Enable or disable memory for new messages"
    )


class MemoryExtractRequest(BaseModel):
    """Request model for extracting memories from a query."""

    query: str = Field(
        ...,
        description="The user query to extract memories from"
    )
    existingMemories: Optional[List[str]] = Field(
        None,
        description="List of existing memories for context"
    )
    conversationHistory: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Conversation history for context"
    )


class MemoryStatusUpdateRequest(BaseModel):
    """Request model for updating memory status (accept/reject)."""

    status: str = Field(
        ...,
        description="Memory status: 'accepted' or 'rejected'"
    )


@router.get("/api/knowledge-sources", tags=["Azure AD OBO Authentication"])
async def get_knowledge_sources(
    request: Request,
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    search: str = Query("", description="Search query string"),
    return_url: bool = Query(False, description="Return JSON with auth_url instead of redirect (for Swagger/API clients)")
):
    """
    Get knowledge sources - initiates OAuth flow.

    **How to test in Swagger:**
    1. Click "Try it out"
    2. Set `return_url=true` parameter (important for Swagger!)
    3. Set page, size, search parameters (optional)
    4. Click "Execute"
    5. Copy the `auth_url` from response
    6. Open that URL in browser to complete Microsoft login
    7. After login, callback will automatically process and return data

    **Note:** 
    - By default, this endpoint redirects to Microsoft login (causes CORS issues in Swagger)
    - Use `return_url=true` parameter to get JSON response with auth_url (Swagger-friendly)
    - For direct testing without OAuth, use `/api/knowledge-sources/direct` endpoint
    """
    logger.info(f"🔐 Starting auth flow for API call: page={page}, size={size}, search='{search}'")

    # Generate unique state for this request
    request_state = secrets.token_urlsafe(16)

    # Store request parameters
    pending_requests[request_state] = {
        'page': page,
        'size': size,
        'search': search
    }

    # Get redirect URI from settings or construct from request
    redirect_uri = settings.azure_obo_redirect_uri
    if not redirect_uri:
        # Construct redirect URI from request - ensure proper scheme
        base_url = str(request.base_url).rstrip('/')
        # Ensure base_url has proper scheme (http or https)
        if not base_url.startswith(('http://', 'https://')):
            # If no scheme, use https for production, http for local
            scheme = 'https' if settings.environment.value != 'development' else 'http'
            host = request.headers.get('host', 'localhost:8000')
            base_url = f"{scheme}://{host}"
        redirect_uri = f"{base_url}/api/v1/auth/azure-ad-obo/callback"

    # Get authorization URL with TARGET API SCOPE
    auth_url = azure_ad_obo_auth.get_authorization_url(redirect_uri, request_state)

    logger.info(f"✓ Auth flow initiated with state: {request_state}")
    logger.info(f"   Redirect URI: {redirect_uri}")
    logger.info(f"   Requesting scope: api://{settings.azure_client_id}/.default")
    logger.info(f"   Auth URL: {auth_url}")

    # Check if request is from Swagger/API client (via return_url param or Accept header)
    accept_header = request.headers.get("accept", "")
    is_api_client = (
        return_url or
        "application/json" in accept_header or
        "swagger" in request.headers.get("user-agent", "").lower() or
        "swagger" in request.headers.get("referer", "").lower()
    )

    if is_api_client:
        # Return JSON response for Swagger/API clients (avoids CORS issues)
        logger.info("   Returning JSON response (API client detected)")
        return JSONResponse(
            content={
                "status": "oauth_required",
                "auth_url": auth_url,
                "state": request_state,
                "redirect_uri": redirect_uri,
                "message": "Open auth_url in browser to complete Microsoft login",
                "parameters": {
                    "page": page,
                    "size": size,
                    "search": search
                }
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    # Redirect to Microsoft login for browser requests
    logger.info("   Redirecting to Microsoft login")
    return RedirectResponse(
        url=auth_url,
        status_code=302,  # Use 302 instead of 307 to avoid CORS issues
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        }
    )


@router.get("/auth/azure-ad-obo/callback", tags=["Azure AD OBO Authentication"])
async def azure_ad_obo_callback(
    request: Request,
    state: str = Query(None, description="OAuth state parameter"),
    code: str = Query(None, description="Authorization code from Azure AD"),
    error: str = Query(None, description="Error code from Azure AD"),
    error_description: str = Query(None, description="Error description from Azure AD")
):
    """
    Azure AD OAuth callback endpoint for OBO flow.

    **This endpoint is called automatically by Azure AD after user login.**
    
    **Do not call this directly from Swagger** - it requires valid state and code
    from the OAuth flow initiated by `/api/knowledge-sources`.

    Handles the redirect from Microsoft login and performs OBO token exchange.
    """
    # Check for errors from Azure AD
    if error:
        error_msg = f"{error}: {error_description or 'No description provided'}"
        logger.error(f"Azure AD returned error: {error_msg}")
        raise HTTPException(
            status_code=400,
            detail=error_msg
        )

    # Check for required parameters
    if not code or not state:
        raise HTTPException(
            status_code=400,
            detail="Missing authorization code or state parameter"
        )

    try:
        # Get stored request data
        if state not in pending_requests:
            raise HTTPException(
                status_code=400,
                detail="Invalid or expired authentication session"
            )

        request_data = pending_requests.pop(state)
        page = request_data['page']
        size = request_data['size']
        search = request_data['search']

        logger.info(f"✓ Retrieved request: page={page}, size={size}, search='{search}'")
        logger.info(f"✓ State validated: {state}")

        # Get redirect URI from settings or construct from request
        redirect_uri = settings.azure_obo_redirect_uri
        if not redirect_uri:
            base_url = str(request.base_url).rstrip('/')
            redirect_uri = f"{base_url}/api/v1/auth/azure-ad-obo/callback"

        # Exchange code for user access token WITH YOUR APP SCOPE
        logger.info("⏳ Exchanging authorization code for access token...")
        logger.info(f"   Requesting scope: api://{settings.azure_client_id}/.default")

        token_response = await azure_ad_obo_auth.get_access_token(code, redirect_uri)
        user_access_token = token_response.get('access_token')

        if not user_access_token:
            raise HTTPException(status_code=500, detail="No access token received")

        # Decode and display token info
        decoded = azure_ad_obo_auth._decode_token_without_verification(user_access_token)
        logger.info(f"✓ Token received with audience: {decoded.get('aud')}")
        logger.info(f"✓ Token scope: {decoded.get('scp', decoded.get('roles', 'N/A'))}")

        # Get user info from ID token
        user_email = "Unknown"
        if 'id_token' in token_response:
            user_info = azure_ad_obo_auth.decode_id_token(token_response['id_token'])
            user_email = user_info.get('preferred_username', user_info.get('email', 'Unknown'))
            logger.info(f"✓ User authenticated: {user_email}")

        logger.debug(f"✓ User access token obtained (length: {len(user_access_token)} chars)")

        # Immediately exchange user token for OBO token and store it
        logger.info("🔄 Exchanging user token for OBO token immediately after login...")
        try:
            obo_token = await exchange_token_obo(user_access_token)
            logger.debug(f"✓ OBO token obtained and cached (length: {len(obo_token)} chars)")
        except Exception as e:
            logger.warning(f"⚠️  Failed to exchange OBO token during login: {e}")
            logger.warning("   Will attempt exchange when making API calls")
            obo_token = None

        # Call API using OBO flow
        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/agents/my/knowledge-sources"
        params = {
            "page": page,
            "size": size,
            "search": search
        }

        logger.info(f"⏳ Calling API with OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   OBO Scope: api://{settings.azure_obo_target_client_id}/All")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=user_access_token,
            api_url=api_url,
            method="GET",
            params=params
        )

        logger.info("✓ API call successful!")

        # SECURITY: Do NOT return tokens in regular API responses
        # Tokens should only be returned by dedicated token endpoints
        # Return success response with API data (without token)
        return JSONResponse(
            content={
                "status": "success",
                "user": user_email,
                # SECURITY: Token removed from response - use /auth/token-callback endpoint to get tokens
                "token_available": True,  # Indicate token was obtained but not included
                "data": result,
                "pagination": {
                    "page": page,
                    "size": size,
                    "search": search
                }
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error(f"Error in OBO callback: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"OBO flow error: {str(e)}"
        )


@router.post("/api/knowledge-sources/direct", tags=["Azure AD OBO Authentication"])
async def get_knowledge_sources_direct(
    request: DirectOBORequest = Body(
        ...,
        description="Request body with user access token and pagination parameters",
        example={
            "user_access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsIng1dCI6Ik1uQ19WWmNBVGZNNXp...",
            "page": 1,
            "size": 20,
            "search": ""
        }
    )
):
    """
    **Direct API endpoint to get knowledge sources using OBO flow - BEST FOR SWAGGER TESTING**

    **How to test in Swagger:**
    1. Click "Try it out"
    2. In the request body, provide:
       - `user_access_token`: Your Azure AD access token (must have audience = your app's client ID)
       - `page`: Page number (default: 1)
       - `size`: Items per page (default: 20, max: 100)
       - `search`: Optional search query
    3. Click "Execute"
    4. Response will contain knowledge sources data

    **How to get user_access_token:**
    - Option 1: Use `/api/knowledge-sources` endpoint first, complete OAuth flow, 
      then extract token from logs or response
    - Option 2: Get token from your frontend application after user login
    - Option 3: Use Azure AD token endpoint directly with your app credentials

    **Token Requirements:**
    - Token audience must be your app's client ID (not Microsoft Graph)
    - Token must have scope: `api://{your-client-id}/.default`
    - Token will be exchanged via OBO for target API scope: `api://{target-client-id}/All`

    **Example Request:**
    ```json
    {
        "user_access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsIng1dCI6Ik1uQ19WWmNBVGZNNXp...",
        "page": 1,
        "size": 20,
        "search": "legal"
    }
    ```
    """
    try:
        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/agents/my/knowledge-sources"
        params = {
            "page": request.page,
            "size": request.size,
            "search": request.search
        }

        logger.info(f"⏳ Calling API with OBO flow (direct)...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   OBO Scope: api://{settings.azure_obo_target_client_id}/All")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=request.user_access_token,
            api_url=api_url,
            method="GET",
            params=params
        )

        logger.info("✓ API call successful!")

        return JSONResponse(
            content={
                "status": "success",
                "data": result,
                "pagination": {
                    "page": request.page,
                    "size": request.size,
                    "search": request.search
                }
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in direct OBO call: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"OBO flow error: {str(e)}"
        )


@router.get("/api/auth/token", tags=["Azure AD OBO Authentication"])
async def get_user_access_token(
    request: Request,
    return_url: bool = Query(False, description="Return JSON with auth_url instead of redirect (for Swagger/API clients)")
):
    """
    **Get user_access_token via OAuth flow - USE THIS TO GET TOKEN FOR OTHER ENDPOINTS**

    **How to get user_access_token:**
    1. Click "Try it out"
    2. Set `return_url=true` parameter (important for Swagger!)
    3. Click "Execute"
    4. Copy the `auth_url` from response
    5. Open that URL in browser to complete Microsoft login
    6. After login, callback will return JSON with `user_access_token`
    7. Copy the `user_access_token` from response
    8. Use this token in other endpoints (create_user_agent, get_user_agent, etc.)

    **Response will contain:**
    - `user_access_token`: Use this token in other API calls
    - `user`: User email
    - `expires_in`: Token expiration time (seconds)

    **Example Usage:**
    After getting token, use it in other endpoints:
    ```
    POST /api/v1/api/user/agents
    {
        "user_access_token": "eyJ0eXAi...",
        "name": "My Agent",
        ...
    }
    ```
    """
    logger.info("🔐 Starting OAuth flow to get user_access_token...")

    # Generate unique state for this request
    request_state = secrets.token_urlsafe(16)

    # Store request parameters (empty for token-only request)
    pending_requests[request_state] = {
        "action": "get_token_only"
    }

    # Get redirect URI from settings or construct from request
    redirect_uri = settings.azure_obo_redirect_uri
    if not redirect_uri:
        base_url = str(request.base_url).rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            scheme = "https" if settings.environment.value != "development" else "http"
            host = request.headers.get("host", "localhost:8000")
            base_url = f"{scheme}://{host}"
        redirect_uri = f"{base_url}/api/v1/auth/azure-ad-obo/token-callback"

    # Get authorization URL
    auth_url = azure_ad_obo_auth.get_authorization_url(redirect_uri, request_state)

    logger.info(f"✓ Auth flow initiated with state: {request_state}")
    logger.info(f"   Redirect URI: {redirect_uri}")
    logger.info(f"   Auth URL: {auth_url}")

    # Check if request is from Swagger/API client
    accept_header = request.headers.get("accept", "")
    is_api_client = (
        return_url or
        "application/json" in accept_header or
        "swagger" in request.headers.get("user-agent", "").lower() or
        "swagger" in request.headers.get("referer", "").lower()
    )

    if is_api_client:
        logger.info("   Returning JSON response (API client detected)")
        return JSONResponse(
            content={
                "status": "oauth_required",
                "auth_url": auth_url,
                "state": request_state,
                "redirect_uri": redirect_uri,
                "message": "Open auth_url in browser to complete Microsoft login and get user_access_token"
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    # Redirect to Microsoft login for browser requests
    logger.info("   Redirecting to Microsoft login")
    return RedirectResponse(
        url=auth_url,
        status_code=302,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        }
    )


@router.get("/auth/azure-ad-obo/token-callback", tags=["Azure AD OBO Authentication"])
async def azure_ad_obo_token_callback(
    request: Request,
    state: str = Query(None, description="OAuth state parameter"),
    code: str = Query(None, description="Authorization code from Azure AD"),
    error: str = Query(None, description="Error code from Azure AD"),
    error_description: str = Query(None, description="Error description from Azure AD")
):
    """
    **Azure AD OAuth callback endpoint that returns user_access_token**

    This endpoint is called automatically by Azure AD after user login.
    It returns the user_access_token that can be used in other API calls.
    """
    # Check for errors from Azure AD
    if error:
        error_msg = f"{error}: {error_description or 'No description provided'}"
        logger.error(f"Azure AD returned error: {error_msg}")
        raise HTTPException(
            status_code=400,
            detail=error_msg
        )

    # Check for required parameters
    if not code or not state:
        raise HTTPException(
            status_code=400,
            detail="Missing authorization code or state parameter"
        )

    try:
        # Get stored request data
        if state not in pending_requests:
            raise HTTPException(
                status_code=400,
                detail="Invalid or expired authentication session"
            )

        request_data = pending_requests.pop(state)

        # Get redirect URI from settings or construct from request
        redirect_uri = settings.azure_obo_redirect_uri
        if not redirect_uri:
            base_url = str(request.base_url).rstrip("/")
            redirect_uri = f"{base_url}/api/v1/auth/azure-ad-obo/token-callback"

        # Exchange code for user access token
        logger.info("⏳ Exchanging authorization code for access token...")
        logger.info(f"   Requesting scope: api://{settings.azure_client_id}/.default")

        token_response = await azure_ad_obo_auth.get_access_token(code, redirect_uri)
        user_access_token = token_response.get("access_token")

        if not user_access_token:
            raise HTTPException(status_code=500, detail="No access token received")

        # Decode and display token info
        decoded = azure_ad_obo_auth._decode_token_without_verification(user_access_token)
        logger.info(f"✓ Token received with audience: {decoded.get('aud')}")
        logger.info(f"✓ Token scope: {decoded.get('scp', decoded.get('roles', 'N/A'))}")

        # Get user info from ID token
        user_email = "Unknown"
        if "id_token" in token_response:
            user_info = azure_ad_obo_auth.decode_id_token(token_response["id_token"])
            user_email = user_info.get("preferred_username", user_info.get("email", "Unknown"))
            logger.info(f"✓ User authenticated: {user_email}")

        logger.debug(f"✓ User access token obtained (length: {len(user_access_token)} chars)")

        # Immediately exchange user token for OBO token and store it
        logger.info("🔄 Exchanging user token for OBO token immediately after login...")
        try:
            obo_token = await exchange_token_obo(user_access_token)
            logger.debug(f"✓ OBO token obtained and cached (length: {len(obo_token)} chars)")
        except Exception as e:
            logger.warning(f"⚠️  Failed to exchange OBO token during login: {e}")
            logger.warning("   Will attempt exchange when making API calls")
            obo_token = None

        # Return token in response
        return JSONResponse(
            content={
                "status": "success",
                "user": user_email,
                "user_access_token": user_access_token,
                "token_type": "Bearer",
                "expires_in": token_response.get("expires_in", 3600),
                "scope": decoded.get("scp", decoded.get("roles", "N/A")),
                "audience": decoded.get("aud"),
                "message": "Copy user_access_token to use in other API endpoints"
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error(f"Error in token callback: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Token callback error: {str(e)}"
        )


@router.get("/api/knowledge-sources/auth-url", tags=["Azure AD OBO Authentication"])
async def get_auth_url(
    request: Request,
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    search: str = Query("", description="Search query string")
):
    """
    **Get Azure AD authorization URL for manual testing**

    **How to test in Swagger:**
    1. Click "Try it out"
    2. Set optional parameters (page, size, search)
    3. Click "Execute"
    4. Copy the `auth_url` from response
    5. Open that URL in browser
    6. Complete Microsoft login
    7. After login, you'll be redirected to callback which processes the request

    **Use this endpoint to:**
    - Get the authorization URL for testing
    - See what URL will be used for OAuth flow
    - Test OAuth flow manually in browser

    **Returns:**
    - `auth_url`: Full Azure AD authorization URL
    - `state`: Unique state token for this request
    - `redirect_uri`: Callback URL that Azure AD will redirect to
    """
    # Generate unique state for this request
    request_state = secrets.token_urlsafe(16)

    # Store request parameters
    pending_requests[request_state] = {
        'page': page,
        'size': size,
        'search': search
    }

    # Get redirect URI from settings or construct from request
    redirect_uri = settings.azure_obo_redirect_uri
    if not redirect_uri:
        base_url = str(request.base_url).rstrip('/')
        redirect_uri = f"{base_url}/api/v1/auth/azure-ad-obo/callback"

    # Get authorization URL
    auth_url = azure_ad_obo_auth.get_authorization_url(redirect_uri, request_state)

    return JSONResponse(
        content={
            "auth_url": auth_url,
            "state": request_state,
            "redirect_uri": redirect_uri,
            "message": "Open auth_url in browser to start OAuth flow",
            "parameters": {
                "page": page,
                "size": size,
                "search": search
            }
        },
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        }
    )


@router.post("/api/user/agents", tags=["User Agents Management"])
async def create_user_agent(
    request: CreateUserAgentRequest = Body(
        ...,
        description="Request to create a new user agent",
        example={
            "name": "My Custom Agent",
            "prompt": "You are a helpful assistant",
            "description": "Agent description",
            "isWebSearchEnabled": True,
            "selectedAgentType": "PRECISE",
            "icon_attachment_id": "550e8400-e29b-41d4-a716-446655440000"
        }
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Create a new user agent using OBO flow (Automatic Token Extraction)**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    **External API:** `POST https://aiq-sit.adq.ae/mapi/v1/agents/my`
    
    **Request Body (base64 encoded):**
    ```json
    {"data": "eyJuYW1lIjoiRGVscGhpIEFnZW50IDUiLCJwcm9tcHQiOiJJbnN0cnVjdGlvbnMgNSIsImRlc2NyaXB0aW9uIjoiRGVzY3JpcHRpb24gNSIsImlzV2ViU2VhcmNoRW5hYmxlZCI6dHJ1ZSwic2VsZWN0ZWRBZ2VudFR5cGUiOiJQUkVDSVNFIn0="}
    ```
    
    **Decoded payload:**
    ```json
    {
        "name": "Delphi Agent 5",
        "prompt": "Instructions 5",
        "description": "Description 5",
        "isWebSearchEnabled": true,
        "selectedAgentType": "PRECISE"
    }
    ```
    
    **External API Response:**
    ```json
    {
        "id": "76b87f72-bcee-4e24-953a-a76beb2d4ea6",
        "name": "Delphi Agent 5",
        "prompt": "Instructions 5",
        "description": "Description 5",
        "isWebSearchEnabled": true,
        "selectedAgentType": "PRECISE",
        "createdAt": "2025-01-15T10:30:00Z"
    }
    ```
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. In the request body, provide:
       - `name`: Agent name (required)
       - `prompt`: Agent instructions (optional)
       - `description`: Agent description (optional)
       - `isWebSearchEnabled`: Enable web search (default: false)
       - `selectedAgentType`: Agent type - "PRECISE" or "CREATIVE" (default: "PRECISE")
       - `icon_attachment_id`: Optional - Attachment ID (UUID) from /api/attachments/upload
       - `user_access_token`: Optional - if not provided, will be auto-extracted from refresh token
    4. Click "Execute"
    5. Response will contain the created agent data and icon_blob_url if icon_attachment_id was provided

    **Example Request:**
    ```json
    {
        "name": "Delphi Agent 5",
        "prompt": "Instructions 5",
        "description": "Description 5",
        "isWebSearchEnabled": true,
        "selectedAgentType": "PRECISE",
        "icon_attachment_id": "550e8400-e29b-41d4-a716-446655440000"
    }
    ```
    
    **Icon Upload Flow:**
    1. First, upload icon file to `/api/attachments/upload` → get `attachment_id`
    2. Then create agent with `icon_attachment_id` in request body
    3. Response will include `icon_blob_url` with the resolved blob URL
    """
    try:
        import base64
        import json

        # Auto-extract user_access_token
        user_access_token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=request.user_access_token
        )
        
        if not user_access_token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in request."
            )

        # Resolve icon attachment ID to blob URL early (before external API call)
        # NOTE: Icon is NOT sent to external API - it's only resolved for our response to frontend
        # This ensures icon_blob_url is available even if external API call fails
        icon_blob_url = None
        if request.icon_attachment_id:
            logger.info(f"🖼️  Resolving icon attachment ID: {request.icon_attachment_id}")
            icon_blob_url = await _resolve_attachment_id_to_url(db, request.icon_attachment_id)
            if icon_blob_url:
                logger.info(f"✓ Resolved icon attachment ID {request.icon_attachment_id} to blob URL: {icon_blob_url[:100]}...")
            else:
                logger.warning(f"⚠ Could not resolve icon attachment ID: {request.icon_attachment_id}")
        else:
            logger.debug("No icon_attachment_id provided in request")

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/agents/my"

        # Prepare agent data for external API
        # NOTE: icon_attachment_id and icon_blob_url are NOT included - they're only for our frontend response
        agent_data = {
            "name": request.name,
            "prompt": request.prompt or "",
            "description": request.description or "",
            "isWebSearchEnabled": request.isWebSearchEnabled,
            "selectedAgentType": request.selectedAgentType
        }

        # Encode data as base64 (as per API requirement)
        agent_json = json.dumps(agent_data)
        encoded_data = base64.b64encode(agent_json.encode("utf-8")).decode("utf-8")

        # Request body format: {"data": "base64_encoded_json"}
        request_body = {"data": encoded_data}

        logger.info(f"⏳ Creating user agent via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Agent name: {request.name}")
        logger.info(f"   OBO Scope: api://{settings.azure_obo_target_client_id}/All")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=user_access_token,
            api_url=api_url,
            method="POST",
            data=request_body
        )

        logger.info("✓ User agent created successfully in AIQ 2.5!")

        # Save agent to our database
        try:
            # Use current_user directly (already authenticated)
            user = current_user
            user_email = current_user.email

            if user:
                    # Extract AIQ agent ID from response
                    aiq_agent_id = result.get("id") if isinstance(result, dict) else None

                    if aiq_agent_id:
                        # Check if agent already exists by AIQ agent ID
                        existing_agent_result = await db.execute(
                            select(Agent).where(Agent.agent_id == str(aiq_agent_id))
                        )
                        existing_agent = existing_agent_result.scalar_one_or_none()

                        # Prepare agent metadata
                        agent_metadata = {
                            "agent_source": "USER",  # Identifier to distinguish from admin agents
                            "aiq_agent_id": aiq_agent_id,
                            "aiq_api_data": result if isinstance(result, dict) else {},
                            "created_by_user_email": user_email,
                            "created_at_aiq": result.get("createdAt") if isinstance(result, dict) else None
                        }

                        # Store metadata in knowledge_sources JSON field (or use methods field)
                        # We'll use knowledge_sources to store metadata
                        agent_metadata_json = agent_metadata.copy()

                        if existing_agent:
                            # Update existing agent
                            existing_agent.name = request.name
                            existing_agent.description = request.description or ""
                            existing_agent.intro = request.prompt or ""
                            existing_agent.knowledge_sources = agent_metadata_json
                            # Normalize status to lowercase for consistency
                            status_value = result.get("status", "draft") if isinstance(result, dict) else "draft"
                            existing_agent.status = status_value.lower() if isinstance(status_value, str) else "draft"
                            existing_agent.updated_at = datetime.utcnow()
                            existing_agent.icon = request.icon_attachment_id
                            existing_agent.logo_src = request.icon_attachment_id
                            
                            # Apply predefined values from admin_config (USER_AGENTS)
                            user_agents_config = await _get_user_agents_config(db)
                            if user_agents_config:
                                logger.info("📋 Applying predefined config from USER_AGENTS admin_config")
                                if "mcp_url" in user_agents_config:
                                    existing_agent.mcp_url = user_agents_config["mcp_url"]
                                if "agent_header" in user_agents_config:
                                    existing_agent.agent_header = user_agents_config["agent_header"]
                                if "agent_capabilities" in user_agents_config:
                                    existing_agent.agent_capabilities = user_agents_config["agent_capabilities"]
                                if "add_history_to_context" in user_agents_config:
                                    existing_agent.add_history_to_context = user_agents_config["add_history_to_context"]
                                if "instruction" in user_agents_config:
                                    existing_agent.instruction = user_agents_config["instruction"]
                            
                            # Update agent configurations in agent_configuration table
                            await _update_custom_feature_toggle(
                                db=db,
                                agent_main_id=existing_agent.id,
                                is_web_search_enabled=request.isWebSearchEnabled,
                                selected_agent_type=request.selectedAgentType,
                                agent_uuid=str(aiq_agent_id)
                            )
                            
                            logger.info(f"✓ Updated existing user agent in database: {aiq_agent_id}")
                            agent_id = existing_agent.id
                        else:
                            # Get predefined config for new agent
                            user_agents_config = await _get_user_agents_config(db)
                            
                            # Normalize status to lowercase for consistency
                            status_value = result.get("status", "draft") if isinstance(result, dict) else "draft"
                            normalized_status = status_value.lower() if isinstance(status_value, str) else "draft"
                            
                            # Create new agent record
                            new_agent = Agent(
                                name=request.name,
                                description=request.description or "",
                                intro=request.prompt or "",
                                agent_id=str(aiq_agent_id),  # Store AIQ agent ID in legacy agent_id field
                                knowledge_sources=agent_metadata_json,  # Store metadata here
                                status=normalized_status,
                                is_enabled=True,
                                category="user_agents",
                                icon = request.icon_attachment_id,
                                logo_src = request.icon_attachment_id 
                            )
                            
                            # Apply predefined values from admin_config (USER_AGENTS)
                            if user_agents_config:
                                logger.info("📋 Applying predefined config from USER_AGENTS admin_config for new agent")
                                if "mcp_url" in user_agents_config:
                                    new_agent.mcp_url = user_agents_config["mcp_url"]
                                if "agent_header" in user_agents_config:
                                    new_agent.agent_header = user_agents_config["agent_header"]
                                if "agent_capabilities" in user_agents_config:
                                    new_agent.agent_capabilities = user_agents_config["agent_capabilities"]
                                if "add_history_to_context" in user_agents_config:
                                    new_agent.add_history_to_context = user_agents_config["add_history_to_context"]
                                if "instruction" in user_agents_config:
                                    new_agent.instruction = user_agents_config["instruction"]
                            
                            db.add(new_agent)
                            await db.flush()  # Get agent.id
                            agent_id = new_agent.id
                            
                            # Update agent configurations in agent_configuration table for new agent
                            await _update_custom_feature_toggle(
                                db=db,
                                agent_main_id=new_agent.id,
                                is_web_search_enabled=request.isWebSearchEnabled,
                                selected_agent_type=request.selectedAgentType,
                                agent_uuid=str(aiq_agent_id)
                            )
                            
                            logger.info(f"✓ Saved user agent to database: {aiq_agent_id}")

                        # Create UserAgentAccess entry to link user to agent
                        # Check if access already exists
                        existing_access_result = await db.execute(
                            select(UserAgentAccess).where(
                                UserAgentAccess.user_id == user.id,
                                UserAgentAccess.agent_id == agent_id
                            )
                        )
                        existing_access = existing_access_result.scalar_one_or_none()

                        if not existing_access:
                            user_agent_access = UserAgentAccess(
                                user_id=user.id,
                                agent_id=agent_id,
                                access_level="admin",  # User has admin access to their own agent
                                is_active=True
                            )
                            db.add(user_agent_access)
                            logger.info(f"✓ Created user agent access for user: {user_email}")

                        await db.commit()
                        logger.info(f"✓ Database save successful for user: {user_email}")
                        
                        # Invalidate cache if agent was activated
                        final_status = existing_agent.status if existing_agent else (new_agent.status if 'new_agent' in locals() else None)
                        if final_status:
                            await invalidate_agent_cache_if_active(final_status, request.name)

                        # Prepare common user details for logging
                        logs_service = PostgresLogsService()
                        # Log only in user logs; reflect actual actor role for analytics
                        role = "ADMIN" if getattr(current_user, "is_admin", False) else "USER"
                        department = getattr(current_user, "azure_department", None)
                        username = (
                            f"{getattr(current_user, 'first_name', '')} {getattr(current_user, 'last_name', '')}"
                        ).strip()

                        # Write a user log event (for user activity analytics)
                        try:
                            created_at = (
                                result.get("createdAt") if isinstance(result, dict) and result.get("createdAt")
                                else datetime.utcnow().isoformat() + "Z"
                            )
                            user_log_payload = {
                                "eventType": "USER_MY_AGENT_CREATED",
                                "createdAt": created_at,
                                "userId": str(user.id) if getattr(user, "id", None) else None,
                                "email": user_email,
                                # Optional correlation ID if available upstream
                                "correlationId": None,
                                # Store required fields inside eventPayload for user logs API
                                "eventPayload": {
                                    "name": request.name,
                                    "prompt": request.prompt or "",
                                    "description": request.description or "",
                                    "selectedAgentType": request.selectedAgentType,
                                    "isWebSearchEnabled": request.isWebSearchEnabled,
                                    "myAgentID": str(aiq_agent_id) if aiq_agent_id else None,
                                    "status": result.get("status", "DRAFT") if isinstance(result, dict) else "DRAFT",
                                    # Optional context fields (role, department) for analytics
                                    "role": role,
                                    "department": department,
                                },
                            }
                            await logs_service.write_user_log(db=db, log_data=user_log_payload)
                            logger.info("✓ User log written: USER_MY_AGENT_CREATED")
                        except Exception as user_log_err:
                            logger.warning(f"⚠ Failed to write user log USER_MY_AGENT_CREATED: {user_log_err}")

                        # Additionally, log activation at creation time if status is ACTIVE
                        try:
                            new_status = result.get("status") if isinstance(result, dict) else None
                            if str(new_status).upper() == "ACTIVE":
                                activated_at = (
                                    result.get("updatedAt") if isinstance(result, dict) and result.get("updatedAt")
                                    else datetime.utcnow().isoformat() + "Z"
                                )
                                agent_name = result.get("name") if isinstance(result, dict) else request.name
                                user_log_payload_activated = {
                                    "eventType": "USER_MY_AGENT_ACTIVATED",
                                    "createdAt": activated_at,
                                    "userId": str(user.id) if getattr(user, "id", None) else None,
                                    "email": user_email,
                                    "correlationId": None,
                                    "eventPayload": {
                                        "name": agent_name,
                                        "myAgentID": str(aiq_agent_id) if aiq_agent_id else None,
                                        "role": role,
                                        "department": department,
                                    },
                                }
                                await logs_service.write_user_log(db=db, log_data=user_log_payload_activated)
                                logger.info("✓ User log written: USER_MY_AGENT_ACTIVATED (creation)")
                        except Exception as user_log_err:
                            logger.warning(f"⚠ Failed to write user log USER_MY_AGENT_ACTIVATED at creation: {user_log_err}")
                    else:
                        logger.warning("No agent ID in AIQ response, skipping database save")
            else:
                logger.warning(f"User not found in database: {user_email}, skipping database save")
        except Exception as db_error:
            await db.rollback()
            logger.error(f"Error saving agent to database: {str(db_error)}", exc_info=True)
            # Don't fail the request if DB save fails - AIQ agent was created successfully

        # Prepare response data
        response_data = {
            "status": "success",
            "data": result
        }
        
        # Add icon blob URL to response if available
        if icon_blob_url:
            response_data["icon_blob_url"] = icon_blob_url

        return JSONResponse(
            content=response_data,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException as http_exc:
        # Include icon_blob_url in error response if available
        # FastAPI HTTPException can have detail as string or dict
        if icon_blob_url:
            if isinstance(http_exc.detail, dict):
                http_exc.detail["icon_blob_url"] = icon_blob_url
            else:
                # Convert string detail to dict format
                http_exc.detail = {
                    "detail": str(http_exc.detail),
                    "icon_blob_url": icon_blob_url
                }
        raise http_exc
    except Exception as e:
        logger.error(f"Error creating user agent: {str(e)}")
        error_detail = {
            "detail": f"Failed to create user agent: {str(e)}"
        }
        if icon_blob_url:
            error_detail["icon_blob_url"] = icon_blob_url
        raise HTTPException(
            status_code=500,
            detail=error_detail
        )


@router.get("/api/user/agents", tags=["User Agents Management"])
async def get_all_user_agents(
    user_access_token: Optional[str] = Query(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    ),
    page: Optional[int] = Query(None, ge=1, description="Page number (starts from 1)"),
    size: Optional[int] = Query(None, ge=1, le=100, description="Number of items per page"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Get all user agents using OBO flow (Automatic Token Extraction)**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    **External API:** `GET https://aiq-sit.adq.ae/mapi/v1/agents/my`
    
    **Query Parameters:**
    - `page`: Page number (optional, default: 1)
    - `size`: Items per page (optional, default: 20, max: 100)
    
    **External API Response:**
    ```json
    {
        "content": [
            {
                "id": "76b87f72-bcee-4e24-953a-a76beb2d4ea6",
                "name": "Delphi Agent 5",
                "prompt": "Instructions 5",
                "description": "Description 5",
                "isWebSearchEnabled": true,
                "selectedAgentType": "PRECISE"
            }
        ],
        "page": 1,
        "size": 20,
        "totalElements": 1,
        "totalPages": 1
    }
    ```
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `page`: Optional page number (default: 1)
       - `size`: Optional items per page (default: 20, max: 100)
       - `user_access_token`: Optional query parameter (will be auto-extracted if not provided)
    4. Click "Execute"
    5. Response will contain all user agents

    **Example:**
    ```
    GET /api/user/agents
    GET /api/user/agents?page=1&size=20
    ```
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in query parameter."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/agents/my"

        # Prepare query parameters if provided
        params = {}
        if page is not None:
            params["page"] = page
        if size is not None:
            params["size"] = size

        logger.info(f"⏳ Getting all user agents via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Params: {params if params else 'None'}")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="GET",
            params=params if params else None
        )

        logger.info("✓ All user agents retrieved successfully!")

        return JSONResponse(
            content={
                "status": "success",
                "data": result
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting all user agents: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get all user agents: {str(e)}"
        )


@router.get("/api/user/agents/{agent_id}", tags=["User Agents Management"])
async def get_user_agent(
    agent_id: str,
    user_access_token: Optional[str] = Query(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Get a specific user agent by ID using OBO flow (Automatic Token Extraction)**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    **External API:** `GET https://aiq-sit.adq.ae/mapi/v1/agents/my/{agent_id}`
    
    **Path Parameters:**
    - `agent_id`: UUID of the agent (e.g., `76b87f72-bcee-4e24-953a-a76beb2d4ea6`)
    
    **External API Response:**
    ```json
    {
        "id": "76b87f72-bcee-4e24-953a-a76beb2d4ea6",
        "name": "Delphi Agent 5",
        "prompt": "Instructions 5",
        "description": "Description 5",
        "isWebSearchEnabled": true,
        "selectedAgentType": "PRECISE",
        "createdAt": "2025-01-15T10:30:00Z",
        "knowledgeSources": [...],
        "starterPrompts": [...]
    }
    ```
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `agent_id`: The UUID of the agent to retrieve (path parameter)
       - `user_access_token`: Optional query parameter (will be auto-extracted if not provided)
    4. Click "Execute"
    5. Response will contain the agent data

    **Example:**
    ```
    GET /api/user/agents/76b87f72-bcee-4e24-953a-a76beb2d4ea6
    ```
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in query parameter."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/agents/my/{agent_id}"

        logger.info(f"⏳ Getting user agent via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Agent ID: {agent_id}")
        logger.info(f"   OBO Scope: api://{settings.azure_obo_target_client_id}/All")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="GET"
        )

        # Enrich response with local database data (icon)
        try:
            # Look up the agent in our local database by public_id (UUID) or agent_id (legacy string)
            logger.info(f"Looking up agent in local DB with agent_id: {agent_id}")
            
            local_agent = None
            
            # Try to parse as UUID first and look up by public_id (modern UUID field)
            if is_uuid(agent_id):
                agent_uuid = UUID(agent_id)
                local_agent_result = await db.execute(
                    select(Agent).where(Agent.public_id == agent_uuid)
                )
                local_agent = local_agent_result.scalar_one_or_none()
                logger.info(f"Agent lookup by public_id: found={local_agent is not None}")
            
            # If not found by public_id, try legacy agent_id field
            if not local_agent:
                logger.info(f"Agent not found by public_id, trying legacy agent_id field")
                local_agent_result = await db.execute(
                    select(Agent).where(Agent.agent_id == str(agent_id))
                )
                local_agent = local_agent_result.scalar_one_or_none()
                logger.info(f"Agent lookup by agent_id: found={local_agent is not None}")
            
            # Resolve icon from agents.icon (UUID) to attachments table
            if local_agent:
                agent_icon_value = local_agent.icon
                logger.info(f"Agent found: id={local_agent.id}, icon={agent_icon_value}")
                
                if agent_icon_value and is_uuid(agent_icon_value):
                    # agents.icon contains a UUID, look it up in attachments table
                    try:
                        logger.info(f"Looking up attachment with id: {agent_icon_value}")
                        attachment_result = await db.execute(
                            select(Attachment).where(
                                Attachment.id == UUID(agent_icon_value),
                                Attachment.is_active == True
                            )
                        )
                        attachment = attachment_result.scalar_one_or_none()
                        logger.info(f"Attachment lookup: found={attachment is not None}")
                        
                        if attachment:
                            result["icon"] = str(agent_icon_value)
                            result["icon_attachment"] = {
                                "attachment_id": str(attachment.id),
                                "file_name": attachment.file_name,
                                "file_size": attachment.file_size,
                                "content_type": attachment.content_type,
                                "blob_url": attachment.blob_url,
                                "blob_name": attachment.blob_name,
                                "entity_type": attachment.entity_type,
                                "entity_id": str(attachment.entity_id) if attachment.entity_id else None,
                                "created_at": attachment.created_at.isoformat() if attachment.created_at else None
                            }
                            logger.info(f"✓ Icon attachment resolved: {attachment.file_name}")
                        else:
                            logger.warning(f"Attachment not found for icon UUID: {agent_icon_value}")
                            result["icon"] = str(agent_icon_value)
                            result["icon_attachment"] = None
                    except Exception as attach_error:
                        logger.warning(f"Error looking up attachment {agent_icon_value}: {str(attach_error)}", exc_info=True)
                        result["icon"] = str(agent_icon_value)
                        result["icon_attachment"] = None
                elif agent_icon_value:
                    # Icon is not a UUID, assume it's already a URL
                    logger.info(f"Icon is not a UUID, treating as URL: {agent_icon_value}")
                    result["icon"] = agent_icon_value
                    result["icon_attachment"] = None
                else:
                    # No icon value
                    logger.info("Agent has no icon value")
                    result["icon"] = None
                    result["icon_attachment"] = None
            else:
                logger.warning(f"Agent not found in local DB for agent_id: {agent_id}")
                result["icon"] = None
                result["icon_attachment"] = None
                
        except Exception as enrich_error:
            logger.error(f"Could not enrich agent with local data: {str(enrich_error)}", exc_info=True)
            # Don't fail the request if enrichment fails
            result["icon"] = None
            result["icon_attachment"] = None

        logger.info("✓ User agent retrieved successfully!")

        icon_blob_url = None
        icon_blob_id = None
        try:
            existing_agent_result = await db.execute(
                select(Agent).where(Agent.agent_id == str(agent_id))
            )
            existing_agent = existing_agent_result.scalar_one_or_none()

            if existing_agent:
                if existing_agent.icon:
                    icon_blob_id = existing_agent.icon
                    logger.info(f"🖼️  Resolving icon attachment ID: {existing_agent.icon}")
                    icon_blob_url = await _resolve_attachment_id_to_url(db, existing_agent.icon)
                    if icon_blob_url:
                        logger.info(f"✓ Resolved icon attachment ID {existing_agent.icon} to blob URL: {icon_blob_url[:100]}...")
                    else:
                        logger.warning(f"⚠ Could not resolve icon attachment ID: {existing_agent.icon}")
                else:
                    logger.debug("No icon_attachment_id provided in request")
                
        except Exception as db_error:
            await db.rollback()
            logger.warning(f"⚠️  Failed to get local database: {str(db_error)}")
            # Don't fail the request - external API update was successful

        response_data = {
            "status": "success",
            "data": result
        }
        if icon_blob_url:
            response_data["icon_blob_url"] = icon_blob_url
            response_data["icon_blob_id"] = icon_blob_id

        return JSONResponse(
            content=response_data,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user agent: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get user agent: {str(e)}"
        )


@router.get("/api/user/agents/{agent_id}/complete", tags=["User Agents Management"])
async def get_user_agent_complete(
    agent_id: str,
    user_access_token: Optional[str] = Query(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    ),
    page: int = Query(1, ge=1, description="Page number for documents (starts from 1)"),
    size: int = Query(20, ge=1, le=100, description="Number of documents per page"),
    search: str = Query("", description="Search query for documents"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Get complete agent details (agent + knowledge sources + documents + starter prompts) in a single call**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    This endpoint combines 4 external API calls into one for better performance:
    
    **External APIs Called (in parallel):**
    1. `GET https://aiq-sit.adq.ae/mapi/v1/agents/my/{agent_id}` - Agent details
    2. `GET https://aiq-sit.adq.ae/mapi/v1/agents/my/{agent_id}/knowledge-sources` - Knowledge sources
    3. `GET https://aiq-sit.adq.ae/mapi/v1/agents/my/{agent_id}/documents?page={page}&size={size}&search={search}` - Documents
    4. `GET https://aiq-sit.adq.ae/mapi/v1/agents/my/{agent_id}/starter-prompts` - Starter prompts
    
    **Response Structure:**
    ```json
    {
        "status": "success",
        "data": {
            "agent": {
                "id": "d32c3598-dbd8-4b8e-a1b3-887f0fee40b2",
                "name": "My Agent",
                "prompt": "Instructions",
                "description": "Description",
                "isWebSearchEnabled": true,
                "selectedAgentType": "PRECISE"
            },
            "knowledgeSources": {
                "data": [
                    {
                        "id": "4165a4bf-a027-41a8-a1fa-4242d09fe213",
                        "name": "Test Demo",
                        "status": "ACTIVE",
                        "documentCount": 15,
                        "createdAt": "2025-10-22T06:43:53.233082"
                    }
                ]
            },
            "documents": {
                "items": [...],
                "total": 18,
                "page": 1,
                "size": 20,
                "totalPages": 1
            },
            "starterPrompts": [
                {
                    "id": "05395ddc-514e-4d05-a576-3367b33eeda7",
                    "order": 1,
                    "prompt": "Starter Prompt Description 1",
                    "title": "Starter Prompt Title 1",
                    "isHighlighted": false,
                    "myAgentId": "d32c3598-dbd8-4b8e-a1b3-887f0fee40b2"
                }
            ]
        }
    }
    ```
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `agent_id`: The UUID of the agent (path parameter)
       - `page`: Page number for documents (optional, default: 1)
       - `size`: Documents per page (optional, default: 20)
       - `search`: Search query for documents (optional)
    4. Click "Execute"
    5. Response will contain all agent data combined in a single response
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in query parameter."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        
        # Define all API URLs
        agent_url = f"{api_base_url}/mapi/v1/agents/my/{agent_id}"
        knowledge_sources_url = f"{api_base_url}/mapi/v1/agents/my/{agent_id}/knowledge-sources"
        documents_url = f"{api_base_url}/mapi/v1/agents/my/{agent_id}/documents"
        starter_prompts_url = f"{api_base_url}/mapi/v1/agents/my/{agent_id}/starter-prompts"

        logger.info(f"⏳ Getting complete agent details via OBO flow (4 parallel calls)...")
        logger.info(f"   Agent ID: {agent_id}")

        # Define async tasks for parallel execution
        async def fetch_agent():
            try:
                return await azure_ad_obo_auth.call_api_with_obo(
                    user_access_token=token,
                    api_url=agent_url,
                    method="GET"
                )
            except Exception as e:
                logger.error(f"Error fetching agent: {str(e)}")
                return {"error": str(e)}

        async def fetch_knowledge_sources():
            try:
                return await azure_ad_obo_auth.call_api_with_obo(
                    user_access_token=token,
                    api_url=knowledge_sources_url,
                    method="GET"
                )
            except Exception as e:
                logger.error(f"Error fetching knowledge sources: {str(e)}")
                return {"error": str(e)}

        async def fetch_documents():
            try:
                params = {"page": page, "size": size, "search": search}
                return await azure_ad_obo_auth.call_api_with_obo(
                    user_access_token=token,
                    api_url=documents_url,
                    method="GET",
                    params=params
                )
            except Exception as e:
                logger.error(f"Error fetching documents: {str(e)}")
                return {"error": str(e)}

        async def fetch_starter_prompts():
            try:
                return await azure_ad_obo_auth.call_api_with_obo(
                    user_access_token=token,
                    api_url=starter_prompts_url,
                    method="GET"
                )
            except Exception as e:
                logger.error(f"Error fetching starter prompts: {str(e)}")
                return {"error": str(e)}

        # Execute all 4 API calls in parallel
        agent_result, knowledge_sources_result, documents_result, starter_prompts_result = await asyncio.gather(
            fetch_agent(),
            fetch_knowledge_sources(),
            fetch_documents(),
            fetch_starter_prompts()
        )

        logger.info("✓ Complete agent details retrieved successfully!")

        # Enrich agent result with local database data (icon)
        try:
            from aldar_middleware.models.agent import Agent
            from aldar_middleware.models.attachment import Attachment
            
            # Look up the agent in our local database by agent_id
            local_agent_result = await db.execute(
                select(Agent).where(Agent.agent_id == str(agent_id))
            )
            local_agent = local_agent_result.scalar_one_or_none()
            
            if local_agent and local_agent.icon:
                # Get the attachment details if icon exists
                attachment_result = await db.execute(
                    select(Attachment).where(Attachment.id == local_agent.icon)
                )
                attachment = attachment_result.scalar_one_or_none()
                
                if attachment:
                    agent_result["icon"] = str(local_agent.icon)
                    agent_result["icon_attachment"] = {
                        "attachment_id": str(attachment.id),
                        "file_name": attachment.file_name,
                        "file_size": attachment.file_size,
                        "content_type": attachment.content_type,
                        "blob_url": attachment.blob_url,
                        "blob_name": attachment.blob_name,
                        "entity_type": attachment.entity_type,
                        "entity_id": str(attachment.entity_id) if attachment.entity_id else None,
                        "created_at": attachment.created_at.isoformat() if attachment.created_at else None
                    }
                else:
                    agent_result["icon"] = str(local_agent.icon)
                    agent_result["icon_attachment"] = None
            else:
                agent_result["icon"] = None
                agent_result["icon_attachment"] = None
                
        except Exception as enrich_error:
            logger.warning(f"Could not enrich agent with local data: {str(enrich_error)}")
            # Don't fail the request if enrichment fails
            agent_result["icon"] = None
            agent_result["icon_attachment"] = None

        # Combine all results
        combined_result = {
            "agent": agent_result,
            "knowledgeSources": knowledge_sources_result,
            "documents": documents_result,
            "starterPrompts": starter_prompts_result
        }

        return JSONResponse(
            content={
                "status": "success",
                "data": combined_result
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting complete agent details: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get complete agent details: {str(e)}"
        )


@router.get("/api/user/agents/{agent_id}/knowledge-sources", tags=["User Agents Management"])
async def get_agent_knowledge_sources(
    agent_id: str,
    user_access_token: Optional[str] = Query(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Get knowledge sources associated with a specific agent using OBO flow (Automatic Token Extraction)**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    **External API:** `GET https://aiq-sit.adq.ae/mapi/v1/agents/my/{agent_id}/knowledge-sources`
    
    **Path Parameters:**
    - `agent_id`: UUID of the agent (e.g., `76b87f72-bcee-4e24-953a-a76beb2d4ea6`)
    
    **External API Response:**
    ```json
    [
        {
            "id": "ks-uuid-1",
            "name": "Knowledge Source 1",
            "type": "DOCUMENT",
            "documentsCount": 5
        },
        {
            "id": "ks-uuid-2",
            "name": "Knowledge Source 2",
            "type": "WEB",
            "documentsCount": 10
        }
    ]
    ```
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `agent_id`: The UUID of the agent (path parameter)
       - `user_access_token`: Optional query parameter (will be auto-extracted if not provided)
    4. Click "Execute"
    5. Response will contain the knowledge sources data
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in query parameter."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/agents/my/{agent_id}/knowledge-sources"

        logger.info(f"⏳ Getting agent knowledge sources via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Agent ID: {agent_id}")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="GET"
        )

        logger.info("✓ Agent knowledge sources retrieved successfully!")

        return JSONResponse(
            content={
                "status": "success",
                "data": result
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting agent knowledge sources: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get agent knowledge sources: {str(e)}"
        )


@router.get("/api/user/knowledge-sources", tags=["User Agents Management"])
async def get_all_knowledge_sources(
    user_access_token: Optional[str] = Query(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    ),
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    search: str = Query("", description="Search query string"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Get all knowledge sources (without requiring agent ID) using OBO flow (Automatic Token Extraction)**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    **External API:** `GET https://aiq-sit.adq.ae/mapi/v1/agents/my/knowledge-sources?page={page}&size={size}&search={search}`
    
    **Query Parameters:**
    - `page`: Page number (default: 1)
    - `size`: Items per page (default: 20, max: 100)
    - `search`: Search query (optional)
    
    **External API Response:**
    ```json
    {
        "items": [
            {
                "id": "4165a4bf-a027-41a8-a1fa-4242d09fe213",
                "name": "Test Demo",
                "status": "ACTIVE",
                "numberOfDocuments": 15,
                "createdBy": "Guido Falcucci",
                "createdAt": "2025-10-22T06:43:53.233082",
                "updatedAt": "2025-10-22T06:45:35.977927"
            }
        ],
        "total": 1,
        "page": 1,
        "size": 20,
        "totalPages": 1
    }
    ```
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `page`: Page number (optional, default: 1)
       - `size`: Items per page (optional, default: 20)
       - `search`: Search query (optional)
       - `user_access_token`: Optional query parameter (will be auto-extracted if not provided)
    4. Click "Execute"
    5. Response will contain all available knowledge sources
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in query parameter."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/agents/my/knowledge-sources"

        params = {
            "page": page,
            "size": size,
            "search": search
        }

        logger.info(f"⏳ Getting all knowledge sources via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Page: {page}, Size: {size}, Search: {search}")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="GET",
            params=params
        )

        logger.info("✓ All knowledge sources retrieved successfully!")

        return JSONResponse(
            content={
                "status": "success",
                "data": result
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting all knowledge sources: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get all knowledge sources: {str(e)}"
        )


class AttachDocumentsRequest(BaseModel):
    """Request model for attaching documents to an agent."""

    user_access_token: Optional[str] = Field(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    )
    documentExternalIds: List[str] = Field(
        ...,
        description="List of document external IDs to attach to the agent"
    )


@router.put("/api/user/agents/{agent_id}/documents", tags=["User Agents Management"])
async def attach_documents_to_agent(
    agent_id: str,
    request: AttachDocumentsRequest = Body(
        ...,
        description="Request to attach documents to an agent",
        example={
            "documentExternalIds": [
                "b5955024a37b1f00796193619fa0d9c91ad124e64a2092669b4c843f6f8851f8",
                "c6809bd6a03a99a25ec902aff0c921648dec07f322d0e0d35f168e3a511b9e34"
            ]
        }
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Attach documents to a user agent using OBO flow (Automatic Token Extraction)**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    **External API:** `PUT https://aiq-sit.adq.ae/mapi/v1/agents/my/{agent_id}/documents`
    
    **Request Body (base64 encoded):**
    ```json
    {"data": "eyJkb2N1bWVudEV4dGVybmFsSWRzIjpbImI1OTU1MDI0YTM3YjFmMDA3OTYxOTM2MTlmYTBkOWM5MWFkMTI0ZTY0YTIwOTI2NjliNGM4NDNmNmY4ODUxZjgiLCJjNjgwOWJkNmEwM2E5OWEyNWVjOTAyYWZmMGM5MjE2NDhkZWMwN2YzMjJkMGUwZDM1ZjE2OGUzYTUxMWI5ZTM0Il19"}
    ```
    
    **Decoded payload:**
    ```json
    {
        "documentExternalIds": [
            "b5955024a37b1f00796193619fa0d9c91ad124e64a2092669b4c843f6f8851f8",
            "c6809bd6a03a99a25ec902aff0c921648dec07f322d0e0d35f168e3a511b9e34"
        ]
    }
    ```
    
    **External API Response:**
    Returns the updated list of documents attached to the agent.
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `agent_id`: The UUID of the agent (path parameter)
       - Request body with `documentExternalIds` array
    4. Click "Execute"
    5. Response will contain the updated documents list
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=request.user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in request body."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/agents/my/{agent_id}/documents"

        # Prepare documents data
        documents_data = {
            "documentExternalIds": request.documentExternalIds
        }

        # Encode data as base64
        documents_json = json.dumps(documents_data)
        encoded_data = base64.b64encode(documents_json.encode("utf-8")).decode("utf-8")

        # Request body format: {"data": "base64_encoded_json"}
        request_body = {"data": encoded_data}

        logger.info(f"⏳ Attaching documents to agent via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Agent ID: {agent_id}")
        logger.info(f"   Documents count: {len(request.documentExternalIds)}")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="PUT",
            data=request_body
        )

        logger.info("✓ Documents attached to agent successfully!")

        # Write user log: USER_MY_AGENT_DOCUMENTS_UPDATED
        try:
            logs_service = PostgresLogsService()
            role = "ADMIN" if getattr(current_user, "is_admin", False) else "USER"
            department = getattr(current_user, "azure_department", None)
            created_at = datetime.utcnow().isoformat() + "Z"
            # Try to fetch agent name from local DB
            agent_name = None
            try:
                existing_agent_result = await db.execute(
                    select(Agent).where(Agent.agent_id == str(agent_id))
                )
                existing_agent = existing_agent_result.scalar_one_or_none()
                if existing_agent:
                    agent_name = getattr(existing_agent, "name", None)
            except Exception:
                pass
            user_log_payload = {
                "eventType": "USER_MY_AGENT_DOCUMENTS_UPDATED",
                "createdAt": created_at,
                "userId": str(current_user.id) if getattr(current_user, "id", None) else None,
                "email": current_user.email,
                "correlationId": None,
                "eventPayload": {
                    "name": agent_name,
                    "myAgentID": str(agent_id),
                    "documentExternalIds": request.documentExternalIds,
                    "role": role,
                    "department": department,
                },
            }
            await logs_service.write_user_log(db=db, log_data=user_log_payload)
            logger.info("✓ User log written: USER_MY_AGENT_DOCUMENTS_UPDATED")
        except Exception as user_log_err:
            logger.warning(f"⚠ Failed to write user log USER_MY_AGENT_DOCUMENTS_UPDATED: {user_log_err}")

        return JSONResponse(
            content={
                "status": "success",
                "data": result
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, PUT, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error attaching documents to agent: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to attach documents to agent: {str(e)}"
        )


class DocumentSearchRequest(BaseModel):
    """Request model for document search."""

    user_access_token: Optional[str] = Field(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    )
    page: int = Field(1, ge=1, description="Page number (starts from 1)")
    size: int = Field(20, ge=1, le=100, description="Number of items per page")
    search: str = Field("", description="Search query string")
    filters: Optional[List[Dict[str, Any]]] = Field(
        default_factory=list, 
        description="Array of filter objects. Each filter has 'field', 'operator', and 'value'. Example: [{'field': 'type', 'operator': 'eq', 'value': 'NEWS'}]"
    )


@router.post("/api/documents/search", tags=["User Agents Management"])
async def search_documents(
    request: DocumentSearchRequest = Body(
        ...,
        description="Request to search documents with optional filters",
        example={
            "page": 1,
            "size": 20,
            "search": "",
            "filters": [
                {"field": "type", "operator": "eq", "value": "NEWS"},
                {"field": "knowledgeSourceId", "operator": "eq", "value": "4165a4bf-a027-41a8-a1fa-4242d09fe213"}
            ]
        }
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Search documents using OBO flow (Automatic Token Extraction)**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    **External API:** `POST https://aiq-sit.adq.ae/mapi/v1/documents/search?search={search}`
    
    **Request Body (base64 encoded):**
    ```json
    {"data": "eyJmaWx0ZXJzIjpbXX0="}
    ```
    
    **Decoded payload (empty filters):**
    ```json
    {
        "filters": []
    }
    ```
    
    **Decoded payload (with filters):**
    ```json
    {
        "filters": [
            {"field": "type", "operator": "eq", "value": "NEWS"},
            {"field": "knowledgeSourceId", "operator": "eq", "value": "4165a4bf-a027-41a8-a1fa-4242d09fe213"}
        ]
    }
    ```
    
    **Available Filter Fields:**
    - `documentTitle` (or `title`): Filter by document title
    - `documentTag` (or `tags`, `tag`): Filter by document tags
    - `type`: Document type (e.g., "NEWS", "PDF", "WEB")
    - `knowledgeSourceId`: Filter by knowledge source UUID
    - `isIngested`: Filter by ingestion status (true/false)
    - `createdAt`: Filter by creation date
    - `updatedAt`: Filter by update date
    
    **Note:** Field aliases are automatically mapped:
    - `title` → `documentTitle`
    - `tags`, `tag` → `documentTag`
    
    **Filter Operators:**
    - `CONTAINS`: Contains substring (case-insensitive)
    - `eq`: Equals
    - `ne`: Not equals
    - `gt`: Greater than
    - `gte`: Greater than or equal
    - `lt`: Less than
    - `lte`: Less than or equal
    - `in`: Value in array
    
    **External API Response:**
    ```json
    {
        "items": [
            {
                "id": "cfe81a6d-cbe6-46e8-9c0f-325c2fb60532",
                "title": "Document Title",
                "fabricUrl": null,
                "sharepointUrl": "https://...",
                "type": "NEWS",
                "externalId": "0eb24c39ef6a2a37b44500e1c188974f5d6091fa99554f920af1547868edb0bc",
                "createdAt": "2025-12-10T06:00:21.150757",
                "updatedAt": "2025-12-10T06:00:21.150757",
                "isIngested": true,
                "tags": [
                    {"tagId": "...", "tagLabel": "tag_2022", "tagType": "Syntetic News Tag"}
                ]
            }
        ],
        "total": 100,
        "page": 1,
        "size": 20,
        "totalPages": 5
    }
    ```
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. In the request body, provide:
       - `page`: Page number (default: 1)
       - `size`: Items per page (default: 20)
       - `search`: Search query (optional)
       - `filters`: Array of filter objects (optional, see examples above)
       - `user_access_token`: Optional - will be auto-extracted if not provided
    4. Click "Execute"
    5. Response will contain the search results
    
    **Example Requests:**
    
    *Search all documents (no filters):*
    ```json
    {"page": 1, "size": 20, "search": "", "filters": []}
    ```
    
    *Search NEWS documents only:*
    ```json
    {"page": 1, "size": 20, "search": "", "filters": [{"field": "type", "operator": "eq", "value": "NEWS"}]}
    ```
    
    *Search documents from specific knowledge source:*
    ```json
    {"page": 1, "size": 20, "search": "ADQ", "filters": [{"field": "knowledgeSourceId", "operator": "eq", "value": "4165a4bf-a027-41a8-a1fa-4242d09fe213"}]}
    ```
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=request.user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in request body."
            )

        import base64
        import json
        import uuid

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/documents/search"

        # Field name mapping (frontend-friendly names -> external API names)
        field_name_mapping = {
            "title": "documentTitle",
            "document_title": "documentTitle",
            "tags": "documentTag",
            "tag": "documentTag",
            "document_tag": "documentTag",
        }

        # Transform filters to match external API format
        transformed_filters = []
        for filter_item in (request.filters or []):
            transformed_filter = dict(filter_item)  # Create a copy
            
            # Map field names
            if "field" in transformed_filter:
                field_name = transformed_filter["field"]
                transformed_filter["field"] = field_name_mapping.get(field_name, field_name)
            
            # Add id if not present (external API may require it)
            if "id" not in transformed_filter:
                transformed_filter["id"] = str(uuid.uuid4())
            
            transformed_filters.append(transformed_filter)

        # Prepare search data
        search_data = {
            "filters": transformed_filters
        }

        logger.info(f"   Transformed filters: {search_data}")

        # Encode data as base64
        search_json = json.dumps(search_data)
        encoded_data = base64.b64encode(search_json.encode("utf-8")).decode("utf-8")

        # Request body format: {"data": "base64_encoded_json"}
        request_body = {"data": encoded_data}

        # Query parameters
        params = {
            "page": request.page,
            "size": request.size,
            "search": request.search
        }

        logger.info(f"⏳ Searching documents via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Page: {request.page}, Size: {request.size}, Search: {request.search}")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="POST",
            data=request_body,
            params=params
        )

        logger.info("✓ Document search successful!")

        return JSONResponse(
            content={
                "status": "success",
                "data": result
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error searching documents: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to search documents: {str(e)}"
        )


@router.get("/api/user/agents/{agent_id}/documents/all", tags=["User Agents Management"])
async def get_agent_documents_all(
    agent_id: str,
    user_access_token: Optional[str] = Query(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Get all documents for a specific agent using OBO flow (Automatic Token Extraction)**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    **External API:** `GET https://aiq-sit.adq.ae/mapi/v1/agents/my/{agent_id}/documents/all`
    
    **Path Parameters:**
    - `agent_id`: UUID of the agent (e.g., `76b87f72-bcee-4e24-953a-a76beb2d4ea6`)
    
    **External API Response:**
    ```json
    [
        {
            "knowledgeSourceId": "ks-uuid-1",
            "knowledgeSourceName": "Knowledge Source 1",
            "documents": [
                {
                    "id": "doc-uuid-1",
                    "name": "Document 1.pdf",
                    "type": "PDF",
                    "size": 1024000
                }
            ]
        }
    ]
    ```
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `agent_id`: The UUID of the agent (path parameter)
       - `user_access_token`: Optional query parameter (will be auto-extracted if not provided)
    4. Click "Execute"
    5. Response will contain all documents organized by knowledge sources
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in query parameter."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/agents/my/{agent_id}/documents/all"

        logger.info(f"⏳ Getting all agent documents via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Agent ID: {agent_id}")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="GET"
        )

        logger.info("✓ All agent documents retrieved successfully!")

        return JSONResponse(
            content={
                "status": "success",
                "data": result
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting all agent documents: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get all agent documents: {str(e)}"
        )


@router.get("/api/user/agents/{agent_id}/documents", tags=["User Agents Management"])
async def get_agent_documents(
    agent_id: str,
    user_access_token: Optional[str] = Query(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    ),
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    search: str = Query("", description="Search query string"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Get paginated documents for a specific agent using OBO flow (Automatic Token Extraction)**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    **External API:** `GET https://aiq-sit.adq.ae/mapi/v1/agents/my/{agent_id}/documents?page={page}&size={size}&search={search}`
    
    **Path Parameters:**
    - `agent_id`: UUID of the agent (e.g., `76b87f72-bcee-4e24-953a-a76beb2d4ea6`)
    
    **Query Parameters:**
    - `page`: Page number (default: 1)
    - `size`: Items per page (default: 20, max: 100)
    - `search`: Search query (optional)
    
    **External API Response:**
    ```json
    {
        "content": [
            {
                "id": "doc-uuid-1",
                "name": "Document 1.pdf",
                "type": "PDF",
                "size": 1024000,
                "knowledgeSourceId": "ks-uuid-1",
                "createdAt": "2025-01-15T10:30:00Z"
            }
        ],
        "page": 1,
        "size": 20,
        "totalElements": 1,
        "totalPages": 1
    }
    ```
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `agent_id`: The UUID of the agent (path parameter)
       - `page`: Page number (optional, default: 1)
       - `size`: Items per page (optional, default: 20)
       - `search`: Search query (optional)
       - `user_access_token`: Optional query parameter (will be auto-extracted if not provided)
    4. Click "Execute"
    5. Response will contain paginated documents
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in query parameter."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/agents/my/{agent_id}/documents"

        params = {
            "page": page,
            "size": size,
            "search": search
        }

        logger.info(f"⏳ Getting agent documents via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Agent ID: {agent_id}, Page: {page}, Size: {size}")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="GET",
            params=params
        )

        logger.info("✓ Agent documents retrieved successfully!")

        return JSONResponse(
            content={
                "status": "success",
                "data": result
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting agent documents: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get agent documents: {str(e)}"
        )


@router.get("/api/user/agents/{agent_id}/starter-prompts", tags=["User Agents Management"])
async def get_agent_starter_prompts(
    agent_id: str,
    user_access_token: Optional[str] = Query(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Get starter prompts for a specific agent using OBO flow (Automatic Token Extraction)**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    **External API:** `GET https://aiq-sit.adq.ae/mapi/v1/agents/my/{agent_id}/starter-prompts`
    
    **Path Parameters:**
    - `agent_id`: UUID of the agent (e.g., `76b87f72-bcee-4e24-953a-a76beb2d4ea6`)
    
    **External API Response:**
    ```json
    [
        {
            "id": "prompt-uuid-1",
            "title": "Example Title",
            "prompt": "Example prompt text",
            "isHighlighted": false,
            "order": 1
        },
        {
            "id": "prompt-uuid-2",
            "title": "Another Title",
            "prompt": "Another prompt text",
            "isHighlighted": true,
            "order": 2
        }
    ]
    ```
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `agent_id`: The UUID of the agent (path parameter)
       - `user_access_token`: Optional query parameter (will be auto-extracted if not provided)
    4. Click "Execute"
    5. Response will contain the starter prompts array
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in query parameter."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/agents/my/{agent_id}/starter-prompts"

        logger.info(f"⏳ Getting agent starter prompts via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Agent ID: {agent_id}")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="GET"
        )

        logger.info("✓ Agent starter prompts retrieved successfully!")

        return JSONResponse(
            content={
                "status": "success",
                "data": result
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PUT, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting agent starter prompts: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get agent starter prompts: {str(e)}"
        )


class UpdateStarterPromptsRequest(BaseModel):
    """Request model for updating starter prompts."""

    user_access_token: Optional[str] = Field(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    )
    items: List[Dict[str, Any]] = Field(
        ...,
        description="Array of starter prompt items with title, prompt, isHighlighted, order"
    )


@router.put("/api/user/agents/{agent_id}/starter-prompts", tags=["User Agents Management"])
async def update_agent_starter_prompts(
    agent_id: str,
    request: UpdateStarterPromptsRequest = Body(
        ...,
        description="Request to update starter prompts",
        example={
            "items": [
                {
                    "title": "Example Title",
                    "prompt": "Example prompt",
                    "isHighlighted": False,
                    "order": 1
                }
            ]
        }
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Update starter prompts for a specific agent using OBO flow (Automatic Token Extraction)**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    **External API:** `PUT https://aiq-sit.adq.ae/mapi/v1/agents/my/{agent_id}/starter-prompts`
    
    **Request Body (base64 encoded):**
    ```json
    {"data": "eyJpdGVtcyI6W3sidGl0bGUiOiJFeGFtcGxlIFRpdGxlIiwicHJvbXB0IjoiRXhhbXBsZSBwcm9tcHQiLCJpc0hpZ2hsaWdodGVkIjpmYWxzZSwib3JkZXIiOjF9XX0="}
    ```
    
    **Decoded payload:**
    ```json
    {
        "items": [
            {
                "title": "Example Title",
                "prompt": "Example prompt",
                "isHighlighted": false,
                "order": 1
            }
        ]
    }
    ```
    
    **External API Response:**
    ```json
    [
        {
            "id": "prompt-uuid-1",
            "title": "Example Title",
            "prompt": "Example prompt",
            "isHighlighted": false,
            "order": 1
        }
    ]
    ```
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `agent_id`: The UUID of the agent (path parameter)
       - Request body with `items` array (user_access_token is optional - will be auto-extracted)
    4. Click "Execute"
    5. Response will contain the updated starter prompts
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=request.user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in request body."
            )

        import base64
        import json

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/agents/my/{agent_id}/starter-prompts"

        # Prepare prompts data - pad to 6 items if fewer provided (external API requires exactly 6)
        items = list(request.items)
        while len(items) < 6:
            items.append({
                "title": "",
                "prompt": "",
                "isHighlighted": False,
                "order": len(items) + 1
            })
        
        prompts_data = {
            "items": items
        }

        # Encode data as base64
        prompts_json = json.dumps(prompts_data)
        encoded_data = base64.b64encode(prompts_json.encode("utf-8")).decode("utf-8")

        # Request body format: {"data": "base64_encoded_json"}
        request_body = {"data": encoded_data}

        logger.info(f"⏳ Updating agent starter prompts via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Agent ID: {agent_id}")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="PUT",
            data=request_body
        )

        logger.info("✓ Agent starter prompts updated successfully!")

        return JSONResponse(
            content={
                "status": "success",
                "data": result
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PUT, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating agent starter prompts: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update agent starter prompts: {str(e)}"
        )


class UpdateUserAgentRequest(BaseModel):
    """Request model for updating user agent details (full update)."""

    user_access_token: Optional[str] = Field(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    )
    name: str = Field(..., description="Agent name")
    prompt: Optional[str] = Field(None, description="Agent prompt/instructions")
    description: Optional[str] = Field(None, description="Agent description")
    isWebSearchEnabled: bool = Field(False, description="Enable web search for agent")
    selectedAgentType: str = Field("PRECISE", description="Agent type: PRECISE or CREATIVE")
    icon_attachment_id: Optional[str] = Field(
        None,
        description="Attachment ID (UUID) from /api/attachments/upload. The icon will be resolved to a blob URL and returned in the response."
    )


@router.put("/api/user/agents/{agent_id}/details", tags=["User Agents Management"])
async def update_user_agent(
    agent_id: str,
    request: UpdateUserAgentRequest = Body(
        ...,
        description="Request to update user agent details",
        example={
            "name": "Delphi Agent 7 (edit)",
            "prompt": "Instructions",
            "description": "Description",
            "isWebSearchEnabled": True,
            "selectedAgentType": "PRECISE",
            "icon_attachment_id": "550e8400-e29b-41d4-a716-446655440000"
        }
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Update user agent details (full update) using OBO flow (Automatic Token Extraction)**

    **External API:** `PATCH https://aiq-sit.adq.ae/mapi/v1/agents/my/{agent_id}`
    
    **Request Body (base64 encoded):**
    ```json
    {"data": "eyJuYW1lIjoiRGVscGhpIEFnZW50IDcgKGVkaXQpIiwicHJvbXB0IjoiSW5zdHJ1Y3Rpb25zIiwiZGVzY3JpcHRpb24iOiJEZXNjcmlwdGlvbiIsImlzV2ViU2VhcmNoRW5hYmxlZCI6dHJ1ZSwic2VsZWN0ZWRBZ2VudFR5cGUiOiJQUkVDSVNFIn0="}
    ```
    
    **Decoded payload:**
    ```json
    {
        "name": "Delphi Agent 7 (edit)",
        "prompt": "Instructions",
        "description": "Description",
        "isWebSearchEnabled": true,
        "selectedAgentType": "PRECISE"
    }
    ```
    
    **External API Response:**
    ```json
    {
        "id": "724f78b6-56a8-4318-9af9-31d1b8aa7e6b",
        "name": "Delphi Agent 7 (edit)",
        "prompt": "Instructions",
        "description": "Description",
        "selectedAgentType": "PRECISE",
        "shouldSendDirectlyToOpenAi": false,
        "isWebSearchEnabled": true,
        "status": "ACTIVE",
        "createdAt": "2025-12-16T06:52:19.334Z",
        "updatedAt": "2025-12-17T11:37:44.603Z"
    }
    ```

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `agent_id`: The UUID of the agent (path parameter)
       - Request body with agent details (user_access_token is optional - will be auto-extracted)
    4. Click "Execute"
    5. Response will contain the updated agent data
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=request.user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in request body."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/agents/my/{agent_id}"

        # Prepare agent data for external API
        agent_data = {
            "name": request.name,
            "prompt": request.prompt or "",
            "description": request.description or "",
            "isWebSearchEnabled": request.isWebSearchEnabled,
            "selectedAgentType": request.selectedAgentType
        }

        # Encode data as base64 (as per API requirement)
        agent_json = json.dumps(agent_data)
        encoded_data = base64.b64encode(agent_json.encode("utf-8")).decode("utf-8")

        # Request body format: {"data": "base64_encoded_json"}
        request_body = {"data": encoded_data}

        logger.info(f"⏳ Updating user agent via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Agent ID: {agent_id}")
        logger.info(f"   Agent name: {request.name}")
        logger.info(f"   OBO Scope: api://{settings.azure_obo_target_client_id}/All")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="PATCH",
            data=request_body
        )

        logger.info("✓ User agent updated successfully!")

        # Resolve icon attachment ID to blob URL early (before external API call)
        # NOTE: Icon is NOT sent to external API - it's only resolved for our response to frontend
        # This ensures icon_blob_url is available even if external API call fails
        icon_blob_url = None
        if request.icon_attachment_id:
            logger.info(f"🖼️  Resolving icon attachment ID: {request.icon_attachment_id}")
            icon_blob_url = await _resolve_attachment_id_to_url(db, request.icon_attachment_id)
            if icon_blob_url:
                logger.info(f"✓ Resolved icon attachment ID {request.icon_attachment_id} to blob URL: {icon_blob_url[:100]}...")
            else:
                logger.warning(f"⚠ Could not resolve icon attachment ID: {request.icon_attachment_id}")
        else:
            logger.debug("No icon_attachment_id provided in request")

        # Update local database if agent exists
        try:
            existing_agent_result = await db.execute(
                select(Agent).where(Agent.agent_id == str(agent_id))
            )
            existing_agent = existing_agent_result.scalar_one_or_none()

            if existing_agent:
                existing_agent.name = request.name
                existing_agent.description = request.description or ""
                existing_agent.intro = request.prompt or ""
                existing_agent.updated_at = datetime.utcnow()
                existing_agent.icon = request.icon_attachment_id
                existing_agent.logo_src = request.icon_attachment_id
                # Update status if returned from API (normalize to lowercase)
                if isinstance(result, dict) and result.get("status"):
                    status_value = result.get("status")
                    existing_agent.status = status_value.lower() if isinstance(status_value, str) else status_value
                
                # Apply predefined values from admin_config (USER_AGENTS)
                user_agents_config = await _get_user_agents_config(db)
                if user_agents_config:
                    logger.info("📋 Applying predefined config from USER_AGENTS admin_config")
                    
                    # Apply predefined values
                    if "mcp_url" in user_agents_config:
                        existing_agent.mcp_url = user_agents_config["mcp_url"]
                        logger.info(f"   → mcp_url: {user_agents_config['mcp_url']}")
                    
                    if "agent_header" in user_agents_config:
                        existing_agent.agent_header = user_agents_config["agent_header"]
                        logger.info(f"   → agent_header: set")
                    
                    if "agent_capabilities" in user_agents_config:
                        existing_agent.agent_capabilities = user_agents_config["agent_capabilities"]
                        logger.info(f"   → agent_capabilities: set")
                    
                    if "add_history_to_context" in user_agents_config:
                        existing_agent.add_history_to_context = user_agents_config["add_history_to_context"]
                        logger.info(f"   → add_history_to_context: {user_agents_config['add_history_to_context']}")
                    
                    if "instruction" in user_agents_config:
                        existing_agent.instruction = user_agents_config["instruction"]
                        logger.info(f"   → instruction: set (length: {len(user_agents_config['instruction'])} chars)")
                
                # Update agent configurations in agent_configuration table
                await _update_custom_feature_toggle(
                    db=db,
                    agent_main_id=existing_agent.id,
                    is_web_search_enabled=request.isWebSearchEnabled,
                    selected_agent_type=request.selectedAgentType,
                    agent_uuid=str(agent_id)
                )
                
                await db.commit()
                logger.info(f"✓ Local database updated for agent: {agent_id}")
                
                # Invalidate cache if agent was activated
                if existing_agent and existing_agent.status:
                    await invalidate_agent_cache_if_active(existing_agent.status, existing_agent.name)
        except Exception as db_error:
            await db.rollback()
            logger.warning(f"⚠️  Failed to update local database: {str(db_error)}")
            # Don't fail the request - external API update was successful

        response_data = {
            "status": "success",
            "data": result
        }
        if icon_blob_url:
            response_data["icon_blob_url"] = icon_blob_url

        # Write user log: USER_MY_AGENT_UPDATED on successful edit
        try:
            logs_service = PostgresLogsService()
            role = "ADMIN" if getattr(current_user, "is_admin", False) else "USER"
            department = getattr(current_user, "azure_department", None)
            updated_at = (
                result.get("updatedAt") if isinstance(result, dict) and result.get("updatedAt")
                else datetime.utcnow().isoformat() + "Z"
            )
            agent_name = result.get("name") if isinstance(result, dict) else request.name
            payload = result if isinstance(result, dict) else {}
            user_log_payload = {
                "eventType": "USER_MY_AGENT_UPDATED",
                "createdAt": updated_at,
                "userId": str(current_user.id) if getattr(current_user, "id", None) else None,
                "email": current_user.email,
                "correlationId": None,
                "eventPayload": {
                    "name": agent_name,
                    "prompt": payload.get("prompt", request.prompt or None),
                    "description": payload.get("description", request.description or None),
                    "selectedAgentType": payload.get("selectedAgentType", request.selectedAgentType),
                    "isWebSearchEnabled": payload.get("isWebSearchEnabled", request.isWebSearchEnabled),
                    "status": payload.get("status"),
                    "myAgentID": str(agent_id),
                    "role": role,
                    "department": department,
                },
            }
            await logs_service.write_user_log(db=db, log_data=user_log_payload)
            logger.info("✓ User log written: USER_MY_AGENT_UPDATED")
        except Exception as user_log_err:
            logger.warning(f"⚠ Failed to write user log USER_MY_AGENT_UPDATED: {user_log_err}")

        return JSONResponse(
            content=response_data,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user agent: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update user agent: {str(e)}"
        )


class AttachKnowledgeSourcesRequest(BaseModel):
    """Request model for attaching knowledge sources to an agent."""

    user_access_token: Optional[str] = Field(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    )
    knowledgeSourceIds: List[str] = Field(
        ...,
        description="Array of knowledge source UUIDs to attach to the agent"
    )


@router.put("/api/user/agents/{agent_id}/knowledge-sources", tags=["User Agents Management"])
async def attach_knowledge_sources(
    agent_id: str,
    request: AttachKnowledgeSourcesRequest = Body(
        ...,
        description="Request to attach knowledge sources to an agent",
        example={
            "knowledgeSourceIds": ["4165a4bf-a027-41a8-a1fa-4242d09fe213"]
        }
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Attach knowledge sources to a user agent using OBO flow (Automatic Token Extraction)**

    **External API:** `PUT https://aiq-sit.adq.ae/mapi/v1/agents/my/{agent_id}/knowledge-sources`
    
    **Request Body (base64 encoded):**
    ```json
    {"data": "eyJrbm93bGVkZ2VTb3VyY2VJZHMiOlsiNDE2NWE0YmYtYTAyNy00MWE4LWExZmEtNDI0MmQwOWZlMjEzIl19"}
    ```
    
    **Decoded payload:**
    ```json
    {
        "knowledgeSourceIds": ["4165a4bf-a027-41a8-a1fa-4242d09fe213"]
    }
    ```
    
    **External API Response:** `204 No Content` (success, no body)

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `agent_id`: The UUID of the agent (path parameter)
       - Request body with `knowledgeSourceIds` array (user_access_token is optional - will be auto-extracted)
    4. Click "Execute"
    5. Response will confirm knowledge sources were attached (204 No Content from external API)

    **Note:** This endpoint attaches the specified knowledge sources to the agent. 
    To get available knowledge sources, use `GET /api/knowledge-sources`.
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=request.user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in request body."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/agents/my/{agent_id}/knowledge-sources"

        # Prepare knowledge sources data
        ks_data = {
            "knowledgeSourceIds": request.knowledgeSourceIds
        }

        # Encode data as base64 (as per API requirement)
        ks_json = json.dumps(ks_data)
        encoded_data = base64.b64encode(ks_json.encode("utf-8")).decode("utf-8")

        # Request body format: {"data": "base64_encoded_json"}
        request_body = {"data": encoded_data}

        logger.info(f"⏳ Attaching knowledge sources to agent via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Agent ID: {agent_id}")
        logger.info(f"   Knowledge Source IDs: {request.knowledgeSourceIds}")
        logger.info(f"   OBO Scope: api://{settings.azure_obo_target_client_id}/All")

        # Call external API - may return 204 No Content
        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="PUT",
            data=request_body
        )

        logger.info("✓ Knowledge sources attached successfully!")

        # Write user log: USER_MY_AGENT_KNOWLEDGE_SOURCES_UPDATED
        try:
            logs_service = PostgresLogsService()
            role = "ADMIN" if getattr(current_user, "is_admin", False) else "USER"
            department = getattr(current_user, "azure_department", None)
            created_at = datetime.utcnow().isoformat() + "Z"
            user_log_payload = {
                "eventType": "USER_MY_AGENT_KNOWLEDGE_SOURCES_UPDATED",
                "createdAt": created_at,
                "userId": str(current_user.id) if getattr(current_user, "id", None) else None,
                "email": current_user.email,
                "correlationId": None,
                "eventPayload": {
                    "myAgentID": str(agent_id),
                    "knowledgeSourceIDs": request.knowledgeSourceIds,
                    "role": role,
                    "department": department,
                },
            }
            await logs_service.write_user_log(db=db, log_data=user_log_payload)
            logger.info("✓ User log written: USER_MY_AGENT_KNOWLEDGE_SOURCES_UPDATED")
        except Exception as user_log_err:
            logger.warning(f"⚠ Failed to write user log USER_MY_AGENT_KNOWLEDGE_SOURCES_UPDATED: {user_log_err}")

        return JSONResponse(
            content={
                "status": "success",
                "message": "Knowledge sources attached successfully",
                "agent_id": agent_id,
                "knowledge_source_ids": request.knowledgeSourceIds,
                "data": result if result else None
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PUT, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error attaching knowledge sources: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to attach knowledge sources: {str(e)}"
        )


class UpdateAgentStatusRequest(BaseModel):
    """Request model for updating agent status."""

    user_access_token: Optional[str] = Field(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    )
    status: str = Field(..., description="Agent status: DRAFT, ACTIVE, etc.")


class RemoveAgentRequest(BaseModel):
    """Request model for updating agent status."""

    user_access_token: Optional[str] = Field(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    )


@router.patch("/api/user/agents/{agent_id}", tags=["User Agents Management"])
async def update_agent_status(
    agent_id: str,
    request: UpdateAgentStatusRequest = Body(
        ...,
        description="Request to update agent status",
        example={
            "status": "ACTIVE"
        }
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Update agent status only using OBO flow (Automatic Token Extraction)**
    
    **Note:** This endpoint only updates the agent status. For full agent update (name, prompt, description, etc.), 
    use `PUT /api/user/agents/{agent_id}/details`.

    **External API:** `PATCH https://aiq-sit.adq.ae/mapi/v1/agents/my/{agent_id}`
    
    **Request Body (base64 encoded):**
    ```json
    {"data": "eyJzdGF0dXMiOiJBQ1RJVkUifQ=="}
    ```
    
    **Decoded payload:**
    ```json
    {"status": "ACTIVE"}
    ```
    
    **External API Response:**
    ```json
    {
        "id": "724f78b6-56a8-4318-9af9-31d1b8aa7e6b",
        "name": "Delphi Agent 7",
        "prompt": "Instructions",
        "description": "Description",
        "selectedAgentType": "PRECISE",
        "shouldSendDirectlyToOpenAi": false,
        "isWebSearchEnabled": true,
        "status": "ACTIVE",
        "createdAt": "2025-12-16T06:52:19.334Z",
        "updatedAt": "2025-12-17T11:37:44.603Z"
    }
    ```

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `agent_id`: The UUID of the agent (path parameter)
       - Request body with `status` (user_access_token is optional - will be auto-extracted)
    4. Click "Execute"
    5. Response will contain the updated agent data
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=request.user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in request body."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/agents/my/{agent_id}"

        # Prepare status data
        status_data = {
            "status": request.status
        }

        # Encode data as base64
        status_json = json.dumps(status_data)
        encoded_data = base64.b64encode(status_json.encode("utf-8")).decode("utf-8")

        # Request body format: {"data": "base64_encoded_json"}
        request_body = {"data": encoded_data}

        logger.info(f"⏳ Updating agent status via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Agent ID: {agent_id}, Status: {request.status}")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="PATCH",
            data=request_body
        )

        # Save agent to our database
        try:
            # Use current_user directly (already authenticated)
            user = current_user
            user_email = current_user.email

            if user:
                    # Extract AIQ agent ID from response
                    aiq_agent_id = agent_id

                    if aiq_agent_id:
                        # Check if agent already exists by AIQ agent ID
                        existing_agent_result = await db.execute(
                            select(Agent).where(Agent.agent_id == str(aiq_agent_id))
                        )
                        existing_agent = existing_agent_result.scalar_one_or_none()

                        if existing_agent:
                            # Update existing agent
                            existing_agent.status = "active"
                            existing_agent.updated_at = datetime.utcnow()
                            
                            # Apply predefined values from admin_config (USER_AGENTS)
                            user_agents_config = await _get_user_agents_config(db)
                            if user_agents_config:
                                logger.info("📋 Applying predefined config from USER_AGENTS admin_config")
                                if "mcp_url" in user_agents_config:
                                    existing_agent.mcp_url = user_agents_config["mcp_url"]
                                if "agent_header" in user_agents_config:
                                    existing_agent.agent_header = user_agents_config["agent_header"]
                                if "agent_capabilities" in user_agents_config:
                                    existing_agent.agent_capabilities = user_agents_config["agent_capabilities"]
                                if "add_history_to_context" in user_agents_config:
                                    existing_agent.add_history_to_context = user_agents_config["add_history_to_context"]
                                if "instruction" in user_agents_config:
                                    existing_agent.instruction = user_agents_config["instruction"]
                            
                            logger.info(f"✓ Updated existing user agent in database: {aiq_agent_id}")
                        
                        await db.commit()
                        await db.refresh(existing_agent)
                        logger.info(f"✓ Database save successful for user: {user_email}")
                        
                        # Invalidate cache since agent was activated
                        if existing_agent and existing_agent.status:
                            await invalidate_agent_cache_if_active(existing_agent.status, existing_agent.name)
                    else:
                        logger.warning("No agent ID in AIQ response, skipping database save")
            else:
                logger.warning(f"User not found in database: {user_email}, skipping database save")
        except Exception as db_error:
            await db.rollback()
            logger.error(f"Error saving agent to database: {str(db_error)}", exc_info=True)
            # Don't fail the request if DB save fails - AIQ agent was created successfully

 
        # Write user log: activation vs generic update
        try:
            logs_service = PostgresLogsService()
            role = "ADMIN" if getattr(current_user, "is_admin", False) else "USER"
            department = getattr(current_user, "azure_department", None)
            # Prefer updatedAt from external response if available
            updated_at = (
                result.get("updatedAt") if isinstance(result, dict) and result.get("updatedAt")
                else datetime.utcnow().isoformat() + "Z"
            )
            # Determine new status to choose event type
            new_status = (
                result.get("status") if isinstance(result, dict) and result.get("status")
                else request.status
            )
            event_type = "USER_MY_AGENT_ACTIVATED" if str(new_status).upper() == "ACTIVE" else "USER_MY_AGENT_UPDATED"
            agent_name = result.get("name") if isinstance(result, dict) else None
            user_log_payload = {
                "eventType": event_type,
                "createdAt": updated_at,
                "userId": str(current_user.id) if getattr(current_user, "id", None) else None,
                "email": current_user.email,
                "correlationId": None,
                "eventPayload": {
                    "name": agent_name,
                    "myAgentID": str(agent_id),
                    "role": role,
                    "department": department,
                },
            }
            await logs_service.write_user_log(db=db, log_data=user_log_payload)
            logger.info(f"✓ User log written: {event_type}")
        except Exception as user_log_err:
            logger.warning(f"⚠ Failed to write user log {event_type}: {user_log_err}")

        logger.info("✓ Agent status updated successfully!")

        return JSONResponse(
            content={
                "status": "success",
                "data": result
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating agent status: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update agent status: {str(e)}"
        )



@router.delete("/api/user/agents/{agent_id}", tags=["User Agents Management"])
async def delete_user_agent(
    agent_id: str,
    request: RemoveAgentRequest = Body(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
   
    try:
        # First, check if agent exists locally and determine its category
        # Try to find by agent_id first, then by public_id
        existing_agent = None
        existing_agent_result = await db.execute(
            select(Agent).where(Agent.agent_id == str(agent_id))
        )
        existing_agent = existing_agent_result.scalar_one_or_none()
        
        if not existing_agent:
            # Try finding by public_id
            try:
                from uuid import UUID as UUID_type
                agent_uuid = UUID_type(agent_id)
                existing_agent_result = await db.execute(
                    select(Agent).where(Agent.public_id == agent_uuid)
                )
                existing_agent = existing_agent_result.scalar_one_or_none()
            except (ValueError, TypeError):
                pass  # Not a valid UUID, skip
        
        if not existing_agent:
            raise HTTPException(
                status_code=404,
                detail=f"Agent with ID {agent_id} not found"
            )
        
        # Check if this is a user agent (category = "user_agents") - only call external API for user agents
        is_user_agent = existing_agent.category == "user_agents"
        
        if is_user_agent:
            # Auto-extract user_access_token only for user agents
            token = await get_user_access_token_auto(
                current_user=current_user,
                db=db,
                provided_token=request.user_access_token
            )
            
            if not token:
                raise HTTPException(
                    status_code=401,
                    detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in request body."
                )

            api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
            api_url = f"{api_base_url}/mapi/v1/agents/my/{agent_id}"

            result = await azure_ad_obo_auth.call_api_with_obo(
                user_access_token=token,
                api_url=api_url,
                method="DELETE"
            )
            logger.info(f"✓ External API delete successful for user agent: {agent_id}")
        else:
            logger.info(f"Agent {agent_id} is not a user agent (category: {existing_agent.category}), skipping external API call")

        # Soft delete agent from our database (set is_deleted=True)
        try:
            # Use current_user directly (already authenticated)
            user = current_user
            user_email = current_user.email

            if user:
                    # Extract AIQ agent ID from response
                    aiq_agent_id = agent_id

                    if aiq_agent_id and existing_agent:
                            existing_agent_name = existing_agent.name if hasattr(existing_agent, "name") else None
                            agent_main_id = existing_agent.id
                            
                            # Check if already deleted
                            if existing_agent.is_deleted:
                                raise HTTPException(
                                    status_code=status.HTTP_400_BAD_REQUEST,
                                    detail="Agent is already deleted"
                                )
                            
                            # Soft delete: Set is_deleted flag to True
                            existing_agent.is_deleted = True
                            existing_agent.updated_at = datetime.utcnow()
                            
                            await db.commit()

                            # Invalidate cached /api/v1/agent/available responses
                            # so soft-deleted agents disappear immediately
                            try:
                                cache = get_agent_available_cache()
                                if cache:
                                    await cache.invalidate_all()
                                    logger.info(
                                        f" Invalidated agent_available cache after deleting agent: {existing_agent_name}"
                                    )
                            except Exception as cache_error:
                                logger.warning(
                                    f" Failed to invalidate agent_available cache after deleting agent {existing_agent_name}: {cache_error}"
                                )
                        
                            logger.info(f" Database soft delete successful for agent: {existing_agent_name} (id: {agent_main_id})")
                            # Write user log for deletion
                            try:
                                logs_service = PostgresLogsService()
                                role = "ADMIN" if getattr(current_user, "is_admin", False) else "USER"
                                department = getattr(current_user, "azure_department", None)
                                created_at = datetime.utcnow().isoformat() + "Z"
                                user_log_payload = {
                                    "eventType": "USER_MY_AGENT_DELETED",
                                    "createdAt": created_at,
                                    "userId": str(user.id) if getattr(user, "id", None) else None,
                                    "email": user_email,
                                    "correlationId": None,
                                    "eventPayload": {
                                        "myAgentID": str(aiq_agent_id) if aiq_agent_id else None,
                                        "role": role,
                                        "department": department,
                                        **({"name": existing_agent_name} if existing_agent_name else {})
                                    },
                                }
                                await logs_service.write_user_log(db=db, log_data=user_log_payload)
                                logger.info("✓ User log written: USER_MY_AGENT_DELETED")
                            except Exception as user_log_err:
                                logger.warning(f"⚠ Failed to write user log USER_MY_AGENT_DELETED: {user_log_err}")
                    else:
                        logger.warning("No agent ID or existing agent, skipping database delete")
            else:
                logger.warning(f"User not found, skipping database delete")
        except Exception as db_error:
            await db.rollback()
            logger.error(f"Error deleting agent from database: {str(db_error)}", exc_info=True)
            # Don't fail the request if DB delete fails


        logger.info("✓ Agent status updated successfully!")

        return JSONResponse(
            content={
                "status": "success"
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS, DELETE",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting agent: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete agent: {str(e)}"
        )


@router.get("/api/user/profile", tags=["User Agents Management"])
async def get_user_profile(
    user_access_token: Optional[str] = Query(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Get user profile using OBO flow (Automatic Token Extraction)**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    **External API:** `GET https://aiq-sit.adq.ae/mapi/v1/user/profile`
    
    **External API Response:**
    ```json
    {
        "id": "user-uuid-1",
        "email": "user@example.com",
        "displayName": "John Doe",
        "firstName": "John",
        "lastName": "Doe",
        "department": "Engineering",
        "jobTitle": "Software Engineer",
        "createdAt": "2025-01-15T10:30:00Z"
    }
    ```
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `user_access_token`: Optional query parameter (will be auto-extracted if not provided)
    4. Click "Execute"
    5. Response will contain the user profile data
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in query parameter."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/user/profile"

        logger.info(f"⏳ Getting user profile via OBO flow...")
        logger.info(f"   Target: {api_url}")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="GET"
        )

        logger.info("✓ User profile retrieved successfully!")

        return JSONResponse(
            content={
                "status": "success",
                "data": result
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user profile: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get user profile: {str(e)}"
        )


# ============================================================================
# USER MEMORY MANAGEMENT ENDPOINTS
# ============================================================================

@router.get("/api/memory/", tags=["User Memory Management"])
async def get_all_user_memories(
    user_access_token: Optional[str] = Query(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Get all user memories using OBO flow (Automatic Token Extraction)**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    **External API:** `GET https://aiq-sit.adq.ae/mapi/v1/memory/`
    
    **External API Response:**
    ```json
    [
        {
            "id": "bdf7df21-331d-4998-b4a7-4b131cc1b1fa",
            "createdAt": "2025-12-18T14:34:17.120+04:00",
            "extractedMemory": "Lives in Dubai",
            "status": "accepted"
        },
        {
            "id": "4dfa692e-276a-4d21-bf3c-bcc93b70080e",
            "createdAt": "2025-12-03T12:12:41.728+04:00",
            "extractedMemory": "Prefers Korean food",
            "status": "accepted"
        }
    ]
    ```
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `user_access_token`: Optional query parameter (will be auto-extracted if not provided)
    4. Click "Execute"
    5. Response will contain the list of user memories
    """
    try:
        user_id = str(current_user.id)
        
        # Try to get from cache first
        cache = get_user_memory_cache()
        if cache:
            cached_memories = await cache.get_cached_memories(user_id)
            if cached_memories is not None:
                logger.info(f"✓ User memories retrieved from cache for user {user_id}")
                return JSONResponse(
                    content={
                        "status": "success",
                        "data": cached_memories,
                        "cached": True
                    },
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET, OPTIONS",
                        "Access-Control-Allow-Headers": "Content-Type, Authorization",
                    }
                )
        
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in query parameter."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/memory/"

        logger.info(f"⏳ Getting user memories via OBO flow...")
        logger.info(f"   Target: {api_url}")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="GET"
        )

        logger.info("✓ User memories retrieved successfully!")
        
        # Cache the result
        if cache and isinstance(result, list):
            await cache.set_cached_memories(user_id, result)
            logger.info(f"✓ User memories cached for user {user_id}")

        return JSONResponse(
            content={
                "status": "success",
                "data": result,
                "cached": False
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user memories: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get user memories: {str(e)}"
        )


@router.patch("/api/user/profile", tags=["User Memory Management"])
async def update_user_profile(
    request_body: UserProfileUpdateRequest = Body(
        ...,
        description="User profile update request",
        example={"isMemoryEnabled": False}
    ),
    user_access_token: Optional[str] = Query(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Update user profile (toggle memory) using OBO flow (Automatic Token Extraction)**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    **External API:** `PATCH https://aiq-sit.adq.ae/mapi/v1/user/profile`
    
    **Request Body:**
    ```json
    {
        "isMemoryEnabled": false
    }
    ```
    
    Note: The request body is automatically base64-encoded before being sent to the external API.
    
    **External API Response:**
    ```json
    {
        "id": "b5f5a11e-204d-450d-bb23-e4b705fcf80b",
        "email": "spandey@adq.ae",
        "role": "ADMIN",
        "firstName": "Sangam",
        "lastName": "Pandey",
        "initials": "SP",
        "customQueryAboutUser": "The person asking the questions is Sangam Pandey...",
        "customQueryPreferredFormatting": "",
        "customQueryTopicsOfInterest": "",
        "isCustomQueryEnabled": true,
        "isMemoryEnabled": false,
        "isOnboarded": true
    }
    ```
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `isMemoryEnabled`: Boolean to enable/disable memory
       - `user_access_token`: Optional query parameter (will be auto-extracted if not provided)
    4. Click "Execute"
    5. Response will contain the updated user profile
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in query parameter."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/user/profile"

        # Build payload and base64 encode it
        payload = {}
        if request_body.isMemoryEnabled is not None:
            payload["isMemoryEnabled"] = request_body.isMemoryEnabled
        
        # Base64 encode the payload
        payload_json = json.dumps(payload)
        payload_base64 = base64.b64encode(payload_json.encode()).decode()
        encoded_body = {"data": payload_base64}

        logger.info(f"⏳ Updating user profile via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Payload: {payload}")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="PATCH",
            data=encoded_body
        )

        logger.info("✓ User profile updated successfully!")

        # Sync to local user preferences
        try:
            from sqlalchemy.orm.attributes import flag_modified
            
            if current_user.preferences is None:
                current_user.preferences = {}
            elif not isinstance(current_user.preferences, dict):
                current_user.preferences = {}
            
            # Sync isMemoryEnabled from response to local preferences
            if isinstance(result, dict) and "isMemoryEnabled" in result:
                current_user.preferences["enable_for_new_messages"] = result.get("isMemoryEnabled", True)
                flag_modified(current_user, "preferences")
                await db.commit()
                await db.refresh(current_user)
                logger.info(f"✓ Local preferences synced: enable_for_new_messages = {result.get('isMemoryEnabled')}")
        except Exception as sync_error:
            logger.warning(f"⚠️ Failed to sync local preferences: {str(sync_error)}")
            # Don't fail the request if local sync fails

        return JSONResponse(
            content={
                "status": "success",
                "data": result
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "PATCH, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user profile: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update user profile: {str(e)}"
        )


@router.delete("/api/memory/{memory_id}", tags=["User Memory Management"])
async def delete_user_memory(
    memory_id: str,
    user_access_token: Optional[str] = Query(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Delete a specific user memory using OBO flow (Automatic Token Extraction)**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    **External API:** `DELETE https://aiq-sit.adq.ae/mapi/v1/memory/{memory_id}`
    
    **Response:** `204 No Content` on success
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `memory_id`: UUID of the memory to delete
       - `user_access_token`: Optional query parameter (will be auto-extracted if not provided)
    4. Click "Execute"
    5. Response will be 204 No Content on success
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in query parameter."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/memory/{memory_id}"

        logger.info(f"⏳ Deleting user memory via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Memory ID: {memory_id}")

        await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="DELETE"
        )

        logger.info(f"✓ User memory {memory_id} deleted successfully!")
        
        # Invalidate user's memory cache
        user_id = str(current_user.id)
        cache = get_user_memory_cache()
        if cache:
            await cache.invalidate_user_cache(user_id)
            logger.info(f"✓ Invalidated memory cache for user {user_id}")

        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting user memory: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete user memory: {str(e)}"
        )


@router.post("/api/memory/extract", tags=["User Memory Management"])
async def extract_memories(
    request_body: MemoryExtractRequest = Body(
        ...,
        description="Memory extraction request",
        example={
            "query": "Whats is the current weather I stay in Dubai and I prefer Vegan good and I travel to Abu Dhabi for work",
            "existingMemories": ["Full name: Sangam Pandey", "Department: Data & AI"],
            "conversationHistory": []
        }
    ),
    user_access_token: Optional[str] = Query(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Extract memories from a query using OBO flow (Automatic Token Extraction)**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    **External API:** `POST https://aiq-dev2.adq.ae/mapi/v1/memory/extract`
    
    **Request Body:**
    ```json
    {
        "query": "Whats is the current weather I stay in Dubai and I prefer Vegan good and I travel to Abu Dhabi for work",
        "existingMemories": [
            "Full name: Sangam Pandey",
            "Department: Data & AI"
        ],
        "conversationHistory": []
    }
    ```
    
    **External API Response:**
    ```json
    {
        "memories": [
            {
                "text": "Prefers vegan food",
                "confidence": "low"
            },
            {
                "text": "Travels to Abu Dhabi for work",
                "confidence": "high"
            }
        ]
    }
    ```
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `query`: The user query to extract memories from
       - `existingMemories`: Optional list of existing memories
       - `conversationHistory`: Optional conversation history
       - `user_access_token`: Optional query parameter (will be auto-extracted if not provided)
    4. Click "Execute"
    5. Response will contain extracted memories with confidence levels
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in query parameter."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/memory/extract"

        # Build payload
        payload = {
            "query": request_body.query,
            "existingMemories": request_body.existingMemories or [],
            "conversationHistory": request_body.conversationHistory or []
        }

        logger.info(f"⏳ Extracting memories via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Query: {request_body.query[:100]}...")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="POST",
            data=payload
        )

        logger.info("✓ Memories extracted successfully!")
        
        # Invalidate user's memory cache since new memories may have been created
        user_id = str(current_user.id)
        cache = get_user_memory_cache()
        if cache:
            await cache.invalidate_user_cache(user_id)
            logger.info(f"✓ Invalidated memory cache for user {user_id}")

        return JSONResponse(
            content={
                "status": "success",
                "data": result
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error extracting memories: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to extract memories: {str(e)}"
        )


@router.patch("/api/memory/{memory_id}", tags=["User Memory Management"])
async def update_memory_status(
    memory_id: str,
    request_body: MemoryStatusUpdateRequest = Body(
        ...,
        description="Memory status update request",
        example={"status": "accepted"}
    ),
    user_access_token: Optional[str] = Query(
        None,
        description="User's Azure AD access token (optional - will be auto-extracted from refresh token if not provided)"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    **Update memory status (accept/reject) using OBO flow (Automatic Token Extraction)**

    **Authentication:** Uses JWT token from Authorization header. Azure AD access token is automatically extracted from user's refresh token.

    ---
    
    **External API:** `PATCH https://aiq-dev2.adq.ae/mapi/v1/memory/{memory_id}`
    
    **Request Body:**
    ```json
    {
        "status": "accepted"
    }
    ```
    
    Note: The request body is automatically base64-encoded before being sent to the external API.
    
    **External API Response:**
    ```json
    {
        "id": "a8dfc61f-11e7-4b47-a0d4-2bdf8dbd5abe",
        "createdAt": "2026-01-14T19:38:15.681+04:00",
        "extractedMemory": "Prefers vegan food",
        "status": "accepted"
    }
    ```
    
    ---

    **How to test in Swagger:**
    1. Click "Authorize" button and enter your JWT token
    2. Click "Try it out"
    3. Provide:
       - `memory_id`: UUID of the memory to update
       - `status`: Either "accepted" or "rejected"
       - `user_access_token`: Optional query parameter (will be auto-extracted if not provided)
    4. Click "Execute"
    5. Response will contain the updated memory
    """
    try:
        # Auto-extract user_access_token
        token = await get_user_access_token_auto(
            current_user=current_user,
            db=db,
            provided_token=user_access_token
        )
        
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Azure AD access token is required. Please ensure you have a valid refresh token stored or provide user_access_token in query parameter."
            )

        api_base_url = settings.azure_obo_api_base_url or "https://aiq-dev2.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/memory/{memory_id}"

        # Build payload and base64 encode it
        payload = {"status": request_body.status}
        
        # Base64 encode the payload
        payload_json = json.dumps(payload)
        payload_base64 = base64.b64encode(payload_json.encode()).decode()
        encoded_body = {"data": payload_base64}

        logger.info(f"⏳ Updating memory status via OBO flow...")
        logger.info(f"   Target: {api_url}")
        logger.info(f"   Memory ID: {memory_id}")
        logger.info(f"   Status: {request_body.status}")

        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=token,
            api_url=api_url,
            method="PATCH",
            data=encoded_body
        )

        logger.info(f"✓ Memory {memory_id} status updated to '{request_body.status}' successfully!")
        
        # Invalidate user's memory cache
        user_id = str(current_user.id)
        cache = get_user_memory_cache()
        if cache:
            await cache.invalidate_user_cache(user_id)
            logger.info(f"✓ Invalidated memory cache for user {user_id}")

        return JSONResponse(
            content={
                "status": "success",
                "data": result
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "PATCH, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating memory status: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update memory status: {str(e)}"
        )


@router.post("/api/debug/verify-mcp-token", tags=["Debug & Testing"], include_in_schema=settings.debug)
async def verify_mcp_token_endpoint(
    request: VerifyMCPTokenRequest = Body(
        ...,
        description="Request to verify MCP token creation and OBO token embedding",
        example={
            "user_access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsIng1dCI6Ik1uQ19WWmNBVGZNNXp..."
        }
    )
):
    """
    **DEBUG ENDPOINT: Verify MCP token contains OBO token**
    
    This endpoint helps you verify that:
    1. OBO token exchange is working
    2. MCP token is created correctly
    3. mcp_token field contains the OBO token
    
    **How to test:**
    1. Get a user_access_token from `/api/auth/token` endpoint
    2. Use that token in this endpoint
    3. Response will show:
       - OBO token (exchanged from user token)
       - MCP token (created with OBO embedded)
       - Decoded MCP token payload (to verify mcp_token field)
    
    **Response includes:**
    - `obo_token`: The OBO token that was exchanged
    - `mcp_token`: The custom JWT token with OBO embedded
    - `decoded_mcp_token`: Full decoded payload showing mcp_token field
    - `verification`: Confirmation that mcp_token field exists
    """
    try:
        # Step 1: Exchange for OBO token
        logger.info("🔄 Step 1: Exchanging user token for OBO token...")
        obo_token = await exchange_token_obo(request.user_access_token)
        logger.debug(f"✓ OBO token obtained (length: {len(obo_token)} chars)")
        
        # Step 2: Create MCP token
        logger.info("🔄 Step 2: Creating MCP token with OBO embedded...")
        mcp_token = create_mcp_token(request.user_access_token, obo_token)
        logger.debug(f"✓ MCP token created (length: {len(mcp_token)} chars)")
        
        # Step 3: Verify MCP token
        logger.info("🔄 Step 3: Verifying MCP token contains OBO token...")
        decoded_mcp = verify_mcp_token(mcp_token)
        
        # Extract mcp_token field
        embedded_obo = decoded_mcp.get("mcp_token", "")
        verification_status = "✅ SUCCESS" if embedded_obo else "❌ FAILED"
        
        logger.info(f"{verification_status}: MCP token verification complete")
        
        # SECURITY: Do NOT return full tokens in API responses
        # Only return metadata (length, exists, etc.) for security
        return JSONResponse(
            content={
                "status": "success",
                "verification": verification_status,
                "obo_token": {
                    # SECURITY: Token value removed - only metadata returned
                    "length": len(obo_token),
                    "available": True
                },
                "mcp_token": {
                    # SECURITY: Token value removed - only metadata returned
                    "length": len(mcp_token),
                    "available": True
                },
                "decoded_mcp_token": {
                    "sub": decoded_mcp.get("sub"),
                    "email": decoded_mcp.get("email"),
                    "iss": decoded_mcp.get("iss"),
                    "exp": decoded_mcp.get("exp"),
                    "iat": decoded_mcp.get("iat"),
                    "mcp_token": {
                        "exists": "mcp_token" in decoded_mcp,
                        "length": len(embedded_obo) if embedded_obo else 0,
                        # SECURITY: Token preview removed
                        "matches_obo": embedded_obo == obo_token if embedded_obo else False
                    }
                },
                "message": "✅ MCP token successfully contains OBO token in mcp_token field" if embedded_obo else "❌ ERROR: mcp_token field not found!"
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error verifying MCP token: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Failed to verify MCP token: {str(e)}"
        )


@router.get("/api/debug/decode-mcp-token", tags=["Debug & Testing"], include_in_schema=settings.debug)
async def decode_mcp_token_endpoint(
    mcp_token: str = Query(..., description="MCP token to decode and verify")
):
    """
    **DEBUG ENDPOINT: Decode and verify MCP token**
    
    **How to use:**
    1. Get MCP token from logs (look for "✓ MCP token created: ...")
    2. Call this endpoint: `/api/debug/decode-mcp-token?mcp_token=YOUR_TOKEN_HERE`
    3. Response will show decoded payload with mcp_token field
    
    **Example:**
    ```
    GET /api/debug/decode-mcp-token?mcp_token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
    ```
    """
    try:
        from aldar_middleware.auth.obo_utils import verify_mcp_token
        
        logger.debug(f"🔄 Decoding MCP token (length: {len(mcp_token)} chars)")
        decoded = verify_mcp_token(mcp_token)
        
        # SECURITY: Do NOT return full tokens in API responses
        mcp_token_value = decoded.get("mcp_token", "")
        return JSONResponse(
            content={
                "status": "success",
                # SECURITY: Full token removed from response
                "mcp_token_received_length": len(mcp_token),
                "decoded_payload": {
                    # Only return non-sensitive fields from decoded payload
                    "sub": decoded.get("sub"),
                    "email": decoded.get("email"),
                    "iss": decoded.get("iss"),
                    "exp": decoded.get("exp"),
                    "iat": decoded.get("iat"),
                },
                "mcp_token_field": {
                    "exists": "mcp_token" in decoded,
                    # SECURITY: Token value removed - only metadata
                    "length": len(mcp_token_value) if mcp_token_value else 0,
                    "available": bool(mcp_token_value)
                },
                "all_fields": list(decoded.keys())
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )
    except Exception as e:
        logger.error(f"Error decoding MCP token: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Failed to decode MCP token: {str(e)}"
        )


@router.get("/api/debug/check-token-from-header", tags=["Debug & Testing"])
async def check_token_from_header(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
):
    """
    **DEBUG ENDPOINT: Check token from Swagger Authorization header**
    
    **How to test in Swagger:**
    1. Click the "Authorize" button (🔒) at the top right of Swagger UI
    2. Enter your JWT token: `Bearer your_jwt_token_here` (or just `your_jwt_token_here`)
    3. Click "Authorize" and "Close"
    4. Call this endpoint: `/api/debug/check-token-from-header`
    5. Response will show:
       - Your JWT token details (decoded)
       - OBO token (if exchange successful)
       - MCP token (with OBO embedded)
       - Full verification that mcp_token field contains OBO token
    
    **This endpoint helps you verify:**
    - ✅ Token is received from Authorization header
    - ✅ OBO token exchange works
    - ✅ MCP token is created correctly
    - ✅ mcp_token field contains OBO token
    
    **Example Response:**
    ```json
    {
      "status": "success",
      "jwt_token": {
        "received": true,
        "decoded": {...},
        "user_id": "...",
        "email": "..."
      },
      "obo_token": {
        "exchanged": true,
        "token": "...",
        "length": 1234
      },
      "mcp_token": {
        "created": true,
        "token": "...",
        "decoded": {
          "sub": "...",
          "email": "...",
          "mcp_token": "OBO_TOKEN_HERE"
        }
      },
      "verification": "✅ SUCCESS: mcp_token field contains OBO token"
    }
    ```
    """
    try:
        # Step 1: Check if token was received from Authorization header
        if not credentials:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "❌ No token received from Authorization header",
                    "instructions": "Please click 'Authorize' button in Swagger and add your JWT token"
                },
                status_code=401
            )
        
        jwt_token = credentials.credentials
        logger.debug(f"✓ Received JWT token from Authorization header (length: {len(jwt_token)} chars)")
        
        # Step 2: Decode JWT token to get user info
        from aldar_middleware.auth.azure_ad import azure_ad_auth
        try:
            decoded_jwt = azure_ad_auth.verify_jwt_token(jwt_token)
            user_id = decoded_jwt.get("sub")
            user_email = decoded_jwt.get("email", "unknown@example.com")
            logger.info(f"✓ JWT token decoded: user_id={user_id}, email={user_email}")
        except Exception as e:
            logger.error(f"❌ Failed to decode JWT token: {e}")
            return JSONResponse(
                content={
                    "status": "error",
                    "message": f"❌ Invalid JWT token: {str(e)}",
                    "jwt_token": {
                        "received": True,
                        "valid": False,
                        "error": str(e)
                    }
                },
                status_code=401
            )
        
        # Step 3: Try to get Azure AD user access token
        # First, check if user has refresh token stored
        from aldar_middleware.database.base import get_db
        from aldar_middleware.models.user import User
        from sqlalchemy import select
        
        user_access_token = None
        async for db in get_db():
            result = await db.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            
            if user and user.azure_ad_refresh_token:
                try:
                    # Try to refresh Azure AD token
                    from aldar_middleware.auth.azure_ad import azure_ad_auth
                    token_response = await azure_ad_auth.refresh_access_token(user.azure_ad_refresh_token)
                    user_access_token = token_response.get("access_token")
                    logger.info(f"✓ Azure AD token refreshed from refresh token")
                except Exception as e:
                    logger.warning(f"⚠️  Could not refresh Azure AD token: {e}")
            break
        
        if not user_access_token:
            return JSONResponse(
                content={
                    "status": "partial_success",
                    "message": "⚠️  JWT token received but Azure AD token not available",
                    "jwt_token": {
                        "received": True,
                        "valid": True,
                        "decoded": decoded_jwt,
                        "user_id": user_id,
                        "email": user_email
                    },
                    "obo_token": {
                        "exchanged": False,
                        "reason": "Azure AD user access token not available. User may need to login via Azure AD first."
                    },
                    "mcp_token": {
                        "created": False,
                        "reason": "Cannot create MCP token without OBO token"
                    }
                }
            )
        
        # Step 4: Exchange for OBO token
        logger.info("🔄 Exchanging Azure AD token for OBO token...")
        try:
            obo_token = await exchange_token_obo(user_access_token)
            logger.debug(f"✓ OBO token obtained (length: {len(obo_token)} chars)")
        except Exception as e:
            logger.error(f"❌ Failed to exchange OBO token: {e}")
            return JSONResponse(
                content={
                    "status": "partial_success",
                    "message": "⚠️  OBO token exchange failed",
                    "jwt_token": {
                        "received": True,
                        "valid": True,
                        "decoded": decoded_jwt,
                        "user_id": user_id,
                        "email": user_email
                    },
                    "user_access_token": {
                        "available": True,
                        # SECURITY: Token preview removed
                        "length": len(user_access_token)
                    },
                    "obo_token": {
                        "exchanged": False,
                        "error": str(e)
                    },
                    "mcp_token": {
                        "created": False,
                        "reason": "Cannot create MCP token without OBO token"
                    }
                }
            )
        
        # Step 5: Create MCP token
        logger.info("🔄 Creating MCP token with OBO embedded...")
        try:
            mcp_token = create_mcp_token(user_access_token, obo_token)
            logger.debug(f"✓ MCP token created (length: {len(mcp_token)} chars)")
        except Exception as e:
            logger.error(f"❌ Failed to create MCP token: {e}")
            return JSONResponse(
                content={
                    "status": "partial_success",
                    "message": "⚠️  MCP token creation failed",
                    "jwt_token": {
                        "received": True,
                        "valid": True,
                        "decoded": decoded_jwt
                    },
                    "obo_token": {
                        "exchanged": True,
                        # SECURITY: Token value removed
                        "length": len(obo_token),
                        "available": True
                    },
                    "mcp_token": {
                        "created": False,
                        "error": str(e)
                    }
                }
            )
        
        # Step 6: Verify MCP token
        logger.info("🔄 Verifying MCP token...")
        decoded_mcp = verify_mcp_token(mcp_token)
        embedded_obo = decoded_mcp.get("mcp_token", "")
        verification_status = "✅ SUCCESS" if embedded_obo and embedded_obo == obo_token else "❌ FAILED"
        # Step 7: Add mcp_token into the original JWT and re-encode it
        try:
            from aldar_middleware.auth.obo_utils import add_mcp_token_to_jwt, decode_token_without_verification

            logger.info("🔄 Adding mcp_token into the original JWT and re-encoding...")
            jwt_with_mcp = add_mcp_token_to_jwt(jwt_token, obo_token)
            decoded_new_jwt = decode_token_without_verification(jwt_with_mcp)
        except Exception as e:
            logger.warning(f"⚠️  Could not add mcp_token to original JWT: {e}")
            jwt_with_mcp = None
            decoded_new_jwt = {}
        
        # SECURITY: Do NOT return full tokens in API responses
        # Only return metadata and non-sensitive decoded fields
        return JSONResponse(
            content={
                "status": "success",
                "message": "✅ All tokens verified successfully!",
                "jwt_token": {
                    "received": True,
                    "valid": True,
                    "decoded": {
                        # Only return non-sensitive fields
                        "sub": decoded_jwt.get("sub"),
                        "email": decoded_jwt.get("email"),
                        "iss": decoded_jwt.get("iss"),
                        "exp": decoded_jwt.get("exp"),
                        "iat": decoded_jwt.get("iat"),
                    },
                    "user_id": user_id,
                    "email": user_email,
                    # SECURITY: Token preview removed
                    "length": len(jwt_token)
                },
                "user_access_token": {
                    "available": True,
                    # SECURITY: Token preview removed
                    "length": len(user_access_token)
                },
                "obo_token": {
                    "exchanged": True,
                    # SECURITY: Token value removed
                    "length": len(obo_token),
                    "available": True
                },
                "mcp_token": {
                    "created": True,
                    # SECURITY: Token value removed
                    "length": len(mcp_token),
                    "available": True,
                    "decoded": {
                        "sub": decoded_mcp.get("sub"),
                        "email": decoded_mcp.get("email"),
                        "iss": decoded_mcp.get("iss"),
                        "exp": decoded_mcp.get("exp"),
                        "iat": decoded_mcp.get("iat"),
                        "mcp_token": {
                            "exists": "mcp_token" in decoded_mcp,
                            "length": len(embedded_obo) if embedded_obo else 0,
                            # SECURITY: Token preview removed
                            "matches_obo": embedded_obo == obo_token if embedded_obo else False
                        }
                    }
                },
                "jwt_token_with_mcp": {
                    "created": True if jwt_with_mcp else False,
                    # SECURITY: Token value removed
                    "length": len(jwt_with_mcp) if jwt_with_mcp else 0,
                    "available": bool(jwt_with_mcp),
                    "decoded": {
                        # Only return non-sensitive fields
                        "sub": decoded_new_jwt.get("sub"),
                        "email": decoded_new_jwt.get("email"),
                        "iss": decoded_new_jwt.get("iss"),
                        "exp": decoded_new_jwt.get("exp"),
                        "iat": decoded_new_jwt.get("iat"),
                        "mcp_token_exists": "mcp_token" in decoded_new_jwt
                    }
                },
                "verification": verification_status,
                "verification_details": {
                    "mcp_token_field_exists": "mcp_token" in decoded_mcp,
                    "mcp_token_matches_obo": embedded_obo == obo_token if embedded_obo else False,
                    "mcp_token_length": len(embedded_obo) if embedded_obo else 0,
                    "obo_token_length": len(obo_token)
                }
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking token from header: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Failed to check token: {str(e)}"
        )

