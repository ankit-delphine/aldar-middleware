"""Authentication API routes."""

import os
from typing import Dict, Any, Optional
from datetime import datetime
from urllib.parse import urlparse
from collections import deque
import asyncio
from asyncio import Lock
from fastapi import APIRouter, Depends, HTTPException, status, Body, Request, Query
from fastapi.responses import Response
from fastapi.security import OAuth2PasswordRequestForm, HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from aldar_middleware.auth.azure_ad import azure_ad_auth
from aldar_middleware.auth.azure_ad_obo import azure_ad_obo_auth
from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.settings import settings
from aldar_middleware.utils.user_utils import get_profile_photo_blob_path, set_profile_photo_blob_path, get_profile_photo_url
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from aldar_middleware.database.base import get_db
from aldar_middleware.models.user import User
from loguru import logger

router = APIRouter()
security = HTTPBearer()

# Simple in-memory store for code verifiers (in production, use Redis or database)
code_verifier_store = {}

# Rate limiting for authentication endpoints
# Limit: 5 attempts per minute, 20 per hour per IP/username
AUTH_RATE_LIMIT_PER_MINUTE = 5
AUTH_RATE_LIMIT_PER_HOUR = 20
AUTH_RATE_LIMIT_WINDOW_MINUTE = 60  # seconds
AUTH_RATE_LIMIT_WINDOW_HOUR = 3600  # seconds

_auth_rate_limit_store: Dict[str, deque] = {}
_auth_rate_limit_lock = Lock()


async def _enforce_auth_rate_limit(request: Request, identifier: Optional[str] = None) -> None:
    """Enforce rate limiting on authentication endpoints.
    
    Args:
        request: FastAPI request object
        identifier: Optional identifier (e.g., username) for user-based limiting
    """
    from datetime import datetime, timedelta
    
    # Use IP address and optional identifier for rate limiting
    client_ip = request.client.host if request.client else "unknown"
    rate_limit_key = f"{client_ip}:{identifier}" if identifier else client_ip
    
    now = datetime.utcnow()
    
    async with _auth_rate_limit_lock:
        # Get or create rate limit bucket
        bucket = _auth_rate_limit_store.setdefault(rate_limit_key, deque())
        
        # Clean up old entries (older than 1 hour)
        while bucket and (now - bucket[0]).total_seconds() > AUTH_RATE_LIMIT_WINDOW_HOUR:
            bucket.popleft()
        
        # Count requests in last minute
        minute_ago = now - timedelta(seconds=AUTH_RATE_LIMIT_WINDOW_MINUTE)
        requests_last_minute = sum(1 for ts in bucket if ts > minute_ago)
        
        # Count requests in last hour
        hour_ago = now - timedelta(seconds=AUTH_RATE_LIMIT_WINDOW_HOUR)
        requests_last_hour = len(bucket)
        
        # Check rate limits
        if requests_last_minute >= AUTH_RATE_LIMIT_PER_MINUTE:
            logger.warning(
                f"Auth rate limit exceeded (per minute): {rate_limit_key}, "
                f"attempts={requests_last_minute}/{AUTH_RATE_LIMIT_PER_MINUTE}"
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many authentication attempts. Please wait before trying again.",
                headers={"Retry-After": str(AUTH_RATE_LIMIT_WINDOW_MINUTE)}
            )
        
        if requests_last_hour >= AUTH_RATE_LIMIT_PER_HOUR:
            logger.warning(
                f"Auth rate limit exceeded (per hour): {rate_limit_key}, "
                f"attempts={requests_last_hour}/{AUTH_RATE_LIMIT_PER_HOUR}"
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many authentication attempts. Please wait before trying again.",
                headers={"Retry-After": str(AUTH_RATE_LIMIT_WINDOW_HOUR)}
            )
        
        # Record this request
        bucket.append(now)
        
        # Clean up empty buckets periodically
        if not bucket:
            _auth_rate_limit_store.pop(rate_limit_key, None)


# Pydantic model for preference updates
class UserPreferencesUpdate(BaseModel):
    """Schema for updating user preferences."""
    preferred_formatting: Optional[str] = None
    topics_of_interest: Optional[str] = None
    about_user: Optional[str] = None
    enable_for_new_messages: Optional[bool] = None
    thinking_panel: Optional[str] = None


def generate_about_user_text(full_name: Optional[str], azure_job_title: Optional[str], azure_department: Optional[str]) -> str:
    """
    Generate the 'About User' text in the format:
    "The person asking the questions is <full_name> working as a <azure_job_title> in the department <azure_department>."
    
    Args:
        full_name: User's full name
        azure_job_title: User's job title from Azure AD
        azure_department: User's department from Azure AD
        
    Returns:
        Formatted about user text
    """
    name = full_name if full_name else "Unknown User"
    job_title = azure_job_title if azure_job_title else "Unknown"
    department = azure_department if azure_department else "Unknown"
    
    return f"The person asking the questions is {name} working as a {job_title} in the department {department}."


def initialize_user_preferences(
    user: User,
    profile_photo_url: Optional[str] = None,
    force_regenerate_about: bool = False
) -> dict:
    """
    Initialize or update user preferences with default values.
    
    Args:
        user: User object
        profile_photo_url: Optional profile photo URL to set
        force_regenerate_about: If True, regenerate about_user even if it exists
        
    Returns:
        Updated preferences dictionary
    """
    # Ensure preferences is a dictionary
    if user.preferences is None:
        user.preferences = {}
    elif not isinstance(user.preferences, dict):
        user.preferences = {}
    
    # Set profile photo if provided
    if profile_photo_url:
        user.preferences["profile_photo"] = profile_photo_url
    
    # Initialize preferred_formatting if not set
    if "preferred_formatting" not in user.preferences or user.preferences.get("preferred_formatting") is None:
        user.preferences["preferred_formatting"] = ""
    
    # Initialize topics_of_interest if not set
    if "topics_of_interest" not in user.preferences or user.preferences.get("topics_of_interest") is None:
        user.preferences["topics_of_interest"] = ""
    
    # Initialize or regenerate about_user
    should_generate_about = (
        force_regenerate_about or
        "about_user" not in user.preferences or 
        user.preferences.get("about_user") is None or
        user.preferences.get("about_user", "").strip() == ""
    )
    
    if should_generate_about:
        user.preferences["about_user"] = generate_about_user_text(
            user.full_name,
            user.azure_job_title,
            user.azure_department
        )
    
    # Initialize enable_for_new_messages if not set (default to True)
    if "enable_for_new_messages" not in user.preferences or user.preferences.get("enable_for_new_messages") is None:
        user.preferences["enable_for_new_messages"] = True
    
    return user.preferences


def get_redirect_uri(request: Optional[Request] = None, provided_uri: Optional[str] = None) -> str:
    """
    Get the redirect URI dynamically based on configuration or request.

    Priority order:
    1. provided_uri parameter (if explicitly provided)
    2. ALDAR_URL environment variable (constructs callback URL)
    3. ALDAR_BASE_URL environment variable (constructs callback URL)
    4. settings.base_url (constructs callback URL)
    5. Request URL (if request object provided)
    6. Construct from ALDAR_HOST + PORT environment variables
    7. Construct from settings.host + settings.port (default)

    Args:
        request: Optional FastAPI Request object to extract URL from
        provided_uri: Optional redirect URI provided by user

    Returns:
        Full redirect URI including callback path
    """
    callback_path = f"{settings.api_prefix}/auth/azure-ad/callback"

    # 1. Use provided URI if explicitly given
    if provided_uri:
        return provided_uri

    # 2. Check ALDAR_URL environment variable
    aiq_url = os.getenv("ALDAR_URL")
    if aiq_url:
        # Parse and construct callback URL
        parsed = urlparse(aiq_url)
        protocol = parsed.scheme or "http"
        host = parsed.hostname or (parsed.netloc.split(":")[0] if ":" in parsed.netloc else parsed.netloc) or "0.0.0.0"
        if parsed.port:
            port = parsed.port
        elif protocol == "https":
            port = 443
        elif protocol == "http":
            port = 80
        else:
            port = 8000

        if (protocol == "http" and port == 80) or (protocol == "https" and port == 443):
            return f"{protocol}://{host}{callback_path}"
        else:
            return f"{protocol}://{host}:{port}{callback_path}"

    # 3. Check ALDAR_BASE_URL environment variable
    base_url = os.getenv("ALDAR_BASE_URL")
    if base_url:
        # Ensure base_url doesn't end with /
        base_url = base_url.rstrip("/")
        return f"{base_url}{callback_path}"

    # 4. Check settings.base_url
    if settings.base_url:
        base_url = settings.base_url.rstrip("/")
        return f"{base_url}{callback_path}"

    # 5. Try to get from request URL
    if request:
        try:
            url = str(request.url)
            # Replace the path with callback path
            parsed = urlparse(url)
            if parsed.port and parsed.port not in [80, 443]:
                base = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
            else:
                base = f"{parsed.scheme}://{parsed.hostname}"
            return f"{base}{callback_path}"
        except Exception:
            pass

    # 6. Construct from ALDAR_HOST + PORT environment variables
    host = os.getenv("ALDAR_HOST")
    port = os.getenv("PORT")
    if host and port:
        try:
            port_int = int(port)
            if port_int == 80:
                return f"http://{host}{callback_path}"
            elif port_int == 443:
                return f"https://{host}{callback_path}"
            else:
                return f"http://{host}:{port_int}{callback_path}"
        except ValueError:
            pass

    # 7. Fall back to settings defaults
    host = settings.host
    port = settings.port

    # Use http://localhost for localhost/127.0.0.1, otherwise use provided host
    if host in ["0.0.0.0", "127.0.0.1"]:
        host = "localhost"

    if port == 80:
        return f"http://{host}{callback_path}"
    elif port == 443:
        return f"https://{host}{callback_path}"
    else:
        return f"http://{host}:{port}{callback_path}"


@router.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()) -> Dict[str, Any]:
    """Login endpoint - redirects to Azure AD OAuth2 flow."""
    # For Azure AD integration, we need to redirect to Azure AD
    # This endpoint should not be used directly for Azure AD
    return {
        "message": "Please use Azure AD OAuth2 flow",
        "azure_ad_url": f"{settings.api_prefix}/auth/azure-ad/login",
        "instructions": "Use the Azure AD login endpoint for authentication"
    }


@router.get("/azure-ad/login")
async def azure_ad_login(
    request: Request,
    redirect_uri: Optional[str] = Query(
        None,
        description="Redirect URI for Azure AD callback. Format: http://your-domain:port/api/v1/auth/azure-ad/callback. If not provided, will be automatically constructed from server configuration.",
        example="http://localhost:8080/api/v1/auth/azure-ad/callback"
    )
) -> Dict[str, Any]:
    """Initiate Azure AD OAuth2 login flow for Web applications."""
    import secrets

    # Enforce rate limiting on login attempts
    await _enforce_auth_rate_limit(request)

    # Get redirect URI dynamically if not provided
    final_redirect_uri = get_redirect_uri(request=request, provided_uri=redirect_uri)

    state = secrets.token_urlsafe(32)
    auth_url = azure_ad_auth.get_authorization_url(final_redirect_uri, state)

    return {
        "authorization_url": auth_url,
        "state": state,
        "redirect_uri": final_redirect_uri,
        "message": "Redirect user to authorization_url to complete login."
    }


@router.get("/azure-ad/callback")
async def azure_ad_callback(
    request: Request,
    code: str,
    state: str,
    redirect_uri: Optional[str] = Query(
        None,
        description="Redirect URI used during the authorization request. Format: http://your-domain:port/api/v1/auth/azure-ad/callback. If not provided, will be automatically constructed from server configuration.",
        example="http://localhost:8080/api/v1/auth/azure-ad/callback"
    )
) -> Dict[str, Any]:
    """Handle Azure AD OAuth2 callback for Web applications."""
    try:
        # Enforce rate limiting on callback attempts
        await _enforce_auth_rate_limit(request)
        
        # Get redirect URI dynamically if not provided
        final_redirect_uri = get_redirect_uri(request=request, provided_uri=redirect_uri)

        # Exchange code for Microsoft Graph API access token
        # This single token will be used for BOTH Graph API calls AND your API authentication
        token_response = await azure_ad_auth.get_access_token(code, final_redirect_uri)
        access_token = token_response["access_token"]
        logger.debug(f"Azure AD token response keys: {list(token_response.keys())}")
        id_token = token_response.get("id_token")
        refresh_token = token_response.get("refresh_token")  # Extract refresh token from Azure AD response
        
        # SECURITY: Do NOT log access tokens in production
        # Only log token metadata (length, expiry) for debugging
        if settings.debug:
            logger.debug(f"Azure AD token obtained: length={len(access_token)} chars")
            logger.debug(f"Token Response Keys: {list(token_response.keys())}")
            if "expires_in" in token_response:
                logger.debug(f"Token Expires In: {token_response.get('expires_in')} seconds")
        else:
            # In production, only log that token was obtained (no token value)
            logger.info("Azure AD access token obtained successfully")

        # Get user info from ID token (preferred) and Graph API (for additional metadata)
        user_info = {}
        graph_user_info = {}

        if id_token:
            # Decode ID token for basic user info (with signature verification)
            user_info = await azure_ad_auth.decode_id_token(id_token)

        # Also get info from Graph API for additional metadata (department, jobTitle, etc.)
        try:
            graph_user_info = await azure_ad_auth.get_user_info(access_token)
        except Exception as e:
            logger.warning(f"Could not fetch Graph API user info: {e}")
            # Continue with ID token info only

        # Merge both sources - ID token takes precedence for basic fields
        merged_user_info = {**graph_user_info, **user_info}

        # Create or update user in database
        async for db in get_db():
            from sqlalchemy import select, func

            # Extract user information with fallback field names
            user_id = merged_user_info.get("oid") or merged_user_info.get("sub") or merged_user_info.get("id")
            email_raw = merged_user_info.get("email") or merged_user_info.get("upn") or merged_user_info.get("userPrincipalName") or merged_user_info.get("mail")

            # Normalize email (lowercase and strip whitespace)
            email = email_raw.lower().strip() if email_raw else None

            # Extract Azure username - prefer preferred_username from ID token, fallback to upn or email
            azure_username = (
                merged_user_info.get("preferred_username") or
                merged_user_info.get("upn") or
                merged_user_info.get("userPrincipalName") or
                merged_user_info.get("mail") or
                email
            )
            if azure_username:
                azure_username = azure_username.lower().strip()

            # Handle name extraction - try individual fields first, then split full name
            first_name = merged_user_info.get("given_name") or merged_user_info.get("givenName")
            last_name = merged_user_info.get("family_name") or merged_user_info.get("surname")

            # If individual names not available, try to split the full name
            if not first_name and not last_name:
                full_name = merged_user_info.get("name") or merged_user_info.get("displayName")
                if full_name:
                    name_parts = full_name.strip().split(" ", 1)
                    first_name = name_parts[0] if len(name_parts) > 0 else None
                    last_name = name_parts[1] if len(name_parts) > 1 else None

            # Combine first_name and last_name into full_name
            full_name_value = None
            if first_name and last_name:
                full_name_value = f"{first_name} {last_name}".strip()
            elif first_name:
                full_name_value = first_name
            elif last_name:
                full_name_value = last_name
            # Fallback to displayName if available
            if not full_name_value:
                full_name_value = merged_user_info.get("name") or merged_user_info.get("displayName")

            # Extract Azure AD metadata
            azure_upn = merged_user_info.get("upn") or merged_user_info.get("userPrincipalName")
            azure_display_name = merged_user_info.get("name") or merged_user_info.get("displayName")
            azure_tenant_id = merged_user_info.get("tid")
            azure_department = merged_user_info.get("department") or None  # Explicitly convert empty string to None
            azure_job_title = merged_user_info.get("jobTitle") or None  # Explicitly convert empty string to None
            
            # Extract additional fields
            # Try employeeId first (most common), then check extension attributes for custom fields
            external_id = merged_user_info.get("employeeId") or merged_user_info.get("externalId") or merged_user_info.get("external_id")
            
            # Check extension attributes for custom external ID fields (common in enterprise setups)
            # Extension attributes are often used for employee IDs in hybrid AD environments
            extension_attrs = merged_user_info.get("onPremisesExtensionAttributes")
            if not external_id and extension_attrs and isinstance(extension_attrs, dict):
                # Check common extension attributes (typically extensionAttribute1-15)
                for i in range(1, 16):
                    attr_value = extension_attrs.get(f"extensionAttribute{i}")
                    if attr_value:
                        external_id = attr_value
                        break
            
            external_id = external_id or None  # Convert empty string to None
            
            company = merged_user_info.get("companyName") or merged_user_info.get("company") or merged_user_info.get("organization") or None  # Explicitly convert empty string to None
            
            # Log extracted values for debugging
            logger.info(f"Extracted Azure AD fields - department: {azure_department}, jobTitle: {azure_job_title}, company: {company}, external_id: {external_id}")
            # isOnboarded is typically a custom field, not from Azure AD - default to False
            is_onboarded = merged_user_info.get("isOnboarded") or merged_user_info.get("is_onboarded") or False

            # Fetch user profile photo from Microsoft Graph API
            # We'll store it in blob storage after user is created/updated
            profile_photo_bytes = None
            profile_photo_url = None
            if user_id:
                try:
                    # Download photo bytes if available
                    photo_bytes = await azure_ad_auth.get_user_profile_photo_bytes(access_token, user_id)
                    if photo_bytes:
                        profile_photo_bytes = photo_bytes
                        logger.info(f"Profile photo found for user {user_id} (size: {len(photo_bytes)} bytes)")
                    else:
                        logger.debug(f"No profile photo found for user {user_id}")
                except Exception as e:
                    logger.warning(f"Error fetching profile photo for user {user_id}: {e}")
                    # Continue without profile photo - don't fail login

            if not user_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Unable to extract user ID from token"
                )

            if not email:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Unable to extract email from token"
                )

            # First, check if user exists by azure_ad_id
            result = await db.execute(
                select(User).where(User.azure_ad_id == user_id)
            )
            user = result.scalar_one_or_none()
            logger.debug(f"User lookup by azure_ad_id '{user_id}': {'found' if user else 'not found'}")

            # If not found by azure_ad_id, check by email (case-insensitive) to handle existing users
            if not user and email:
                # Try case-insensitive match using func.lower
                result = await db.execute(
                    select(User).where(func.lower(User.email) == email)
                )
                user = result.scalar_one_or_none()

                # Also try exact match in case the email in DB is already lowercase
                if not user:
                    result = await db.execute(
                        select(User).where(User.email == email)
                    )
                    user = result.scalar_one_or_none()

                # Also try matching the raw email before normalization
                if not user and email_raw:
                    result = await db.execute(
                        select(User).where(func.lower(User.email) == email_raw.lower().strip())
                    )
                    user = result.scalar_one_or_none()

                logger.debug(f"User lookup by email '{email}' (normalized from '{email_raw}'): {'found' if user else 'not found'}")

                # Log all users with similar emails for debugging
                if not user:
                    # Query to see what emails exist in DB for debugging
                    all_users_result = await db.execute(
                        select(User.email).where(func.lower(User.email).like(f"%{email.split('@')[0]}%"))
                    )
                    similar_emails = [row[0] for row in all_users_result.fetchall()]
                    if similar_emails:
                        logger.warning(f"Found similar emails in DB: {similar_emails} (searching for: {email})")

            if not user:
                # Create new user
                logger.info(f"Creating new user with email: {email}, azure_ad_id: {user_id}")
                # Initialize preferences (profile photo will be added after user creation)
                preferences = {}
                
                # Use Azure username (preferred_username) instead of email
                user = User(
                    email=email,
                    username=azure_username,  # Use Azure preferred_username
                    first_name=first_name,
                    last_name=last_name,
                    full_name=full_name_value,  # Combine first_name and last_name
                    azure_ad_id=user_id,
                    azure_upn=azure_upn,
                    azure_display_name=azure_display_name,
                    azure_tenant_id=azure_tenant_id,
                    azure_department=azure_department if azure_department else None,  # Convert empty string to None
                    azure_job_title=azure_job_title if azure_job_title else None,  # Convert empty string to None
                    azure_ad_refresh_token=refresh_token,  # Store refresh token
                    preferences=preferences if preferences else None,
                    external_id=external_id,
                    company=company if company else None,  # Convert empty string to None
                    is_onboarded=is_onboarded,
                    first_logged_in_at=datetime.utcnow(),  # Set first login time
                    last_login=datetime.utcnow(),  # Set last login time
                    is_verified=True
                )
                db.add(user)
                try:
                    await db.commit()
                    await db.refresh(user)
                    logger.info(f"Successfully created user: {user.email}")
                    
                    # Upload profile photo to blob storage if available
                    if profile_photo_bytes:
                        try:
                            from aldar_middleware.orchestration.blob_storage import BlobStorageService
                            # Use main storage container for profile photos
                            blob_service = BlobStorageService(container_name=settings.azure_storage_container_name)
                            photo_url, blob_path, _ = await blob_service.upload_profile_photo(
                                file_content=profile_photo_bytes,
                                user_id=str(user.id),
                                overwrite=True
                            )
                            set_profile_photo_blob_path(user, blob_path)
                            logger.info(f"Profile photo uploaded to blob storage for user {user.email}: {blob_path}")
                            flag_modified(user, "preferences")
                            await db.commit()
                            await db.refresh(user)
                        except Exception as photo_error:
                            logger.warning(f"Error uploading profile photo to blob storage: {photo_error}")
                            # Continue without blob storage - fallback to proxy endpoint
                            profile_photo_url = f"{settings.api_prefix}/auth/users/{user.id}/profile-photo"
                    
                    # Initialize user preferences with all required fields
                    profile_photo_url_with_user_id = get_profile_photo_url(user)
                    if not profile_photo_url_with_user_id and profile_photo_url:
                        profile_photo_url_with_user_id = f"{settings.api_prefix}/auth/users/{user.id}/profile-photo"
                    
                    # Initialize preferences with new fields
                    initialize_user_preferences(user, profile_photo_url_with_user_id, force_regenerate_about=True)
                    flag_modified(user, "preferences")
                    await db.commit()
                    logger.info(f"User preferences initialized for new user: {user.email}")
                except IntegrityError as commit_error:
                    await db.rollback()
                    logger.error(f"Integrity error creating user: {commit_error}")
                    # Check if it's a unique constraint error (email or azure_ad_id)
                    error_str = str(commit_error).lower()
                    if "unique constraint" in error_str or "duplicate key" in error_str:
                        logger.warning(f"Duplicate key error, attempting to find existing user by email: {email}")
                        # Try one more time to find the user - might be a race condition
                        # First try by email (case-insensitive)
                        result = await db.execute(
                            select(User).where(func.lower(User.email) == email)
                        )
                        user = result.scalar_one_or_none()

                        # If not found by email, try by azure_ad_id
                        if not user:
                            result = await db.execute(
                                select(User).where(User.azure_ad_id == user_id)
                            )
                            user = result.scalar_one_or_none()

                        if user:
                            logger.info(f"Found existing user, updating instead: {user.email} (azure_ad_id: {user.azure_ad_id})")
                            # Update existing user instead
                            user.first_name = first_name
                            user.last_name = last_name
                            user.full_name = full_name_value
                            user.username = azure_username
                            user.azure_upn = azure_upn
                            user.azure_display_name = azure_display_name
                            if not user.azure_ad_id:
                                user.azure_ad_id = user_id
                            if azure_tenant_id:
                                user.azure_tenant_id = azure_tenant_id
                            # Update Azure department - set to None if empty string, otherwise update
                            if azure_department is not None:
                                user.azure_department = azure_department if azure_department else None
                            # Update Azure job title - set to None if empty string, otherwise update
                            if azure_job_title is not None:
                                user.azure_job_title = azure_job_title if azure_job_title else None
                            if external_id:
                                user.external_id = external_id
                            # Update company - set to None if empty string, otherwise update
                            if company is not None:
                                user.company = company if company else None
                            # Update is_onboarded if provided (but don't overwrite if already True)
                            if is_onboarded is not False:
                                user.is_onboarded = is_onboarded
                            user.azure_ad_refresh_token = refresh_token
                            user.is_verified = True
                            user.last_login = datetime.utcnow()
                            # Set first_logged_in_at if not already set
                            if not user.first_logged_in_at:
                                user.first_logged_in_at = datetime.utcnow()
                            # Upload profile photo to blob storage if available
                            if profile_photo_bytes:
                                try:
                                    from aldar_middleware.orchestration.blob_storage import BlobStorageService
                                    # Use main storage container for profile photos
                                    blob_service = BlobStorageService(container_name=settings.azure_storage_container_name)
                                    # Delete old photo if exists
                                    old_blob_path = get_profile_photo_blob_path(user)
                                    if old_blob_path:
                                        try:
                                            await blob_service.delete_blob(old_blob_path)
                                            logger.info(f"Deleted old profile photo for user {user.email}")
                                        except Exception as delete_error:
                                            logger.warning(f"Error deleting old profile photo: {delete_error}")
                                    # Upload new photo
                                    photo_url, blob_path, _ = await blob_service.upload_profile_photo(
                                        file_content=profile_photo_bytes,
                                        user_id=str(user.id),
                                        overwrite=True
                                    )
                                    set_profile_photo_blob_path(user, blob_path)
                                    flag_modified(user, "preferences")
                                    logger.info(f"Profile photo uploaded to blob storage for user {user.email}: {blob_path}")
                                except Exception as photo_error:
                                    logger.warning(f"Error uploading profile photo to blob storage: {photo_error}")
                                    # Continue without blob storage - fallback to proxy endpoint
                                    profile_photo_url = f"{settings.api_prefix}/auth/users/{user.id}/profile-photo"
                            
                            # Initialize/update user preferences with all required fields
                            profile_photo_url_for_prefs = get_profile_photo_url(user)
                            if not profile_photo_url_for_prefs and profile_photo_url:
                                profile_photo_url_for_prefs = f"{settings.api_prefix}/auth/users/{user.id}/profile-photo"
                            
                            initialize_user_preferences(user, profile_photo_url_for_prefs)
                            flag_modified(user, "preferences")
                            logger.info(f"User preferences updated for existing user: {user.email}")
                            try:
                                await db.commit()
                                await db.refresh(user)
                                logger.info(f"Successfully updated existing user: {user.email}")
                            except Exception as update_error:
                                await db.rollback()
                                logger.error(f"Error updating user after duplicate key error: {update_error}")
                                raise HTTPException(
                                    status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"Authentication failed: Unable to update existing user - {str(update_error)}"
                                )
                        else:
                            logger.error(f"Integrity error but could not find existing user by email {email} or azure_ad_id {user_id}")
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Authentication failed: User already exists but could not be found - {str(commit_error)}"
                            )
                    else:
                        # Not a unique constraint error, re-raise
                        raise
                except Exception as commit_error:
                    await db.rollback()
                    logger.error(f"Unexpected error creating user: {commit_error}", exc_info=True)
                    raise
            else:
                # Update existing user with latest information and refresh token
                logger.info(f"Updating existing user: {user.email}")
                user.first_name = first_name
                user.last_name = last_name
                user.full_name = full_name_value  # Update full_name from first_name and last_name
                user.username = azure_username  # Update username from Azure AD
                user.azure_upn = azure_upn  # Update Azure UPN
                user.azure_display_name = azure_display_name  # Update Azure display name
                # Link Azure AD ID if not already set (important for users created before Azure AD linking)
                if not user.azure_ad_id:
                    user.azure_ad_id = user_id
                elif user.azure_ad_id != user_id:
                    # User already linked to a different Azure AD account - don't change it
                    logger.warning(
                        f"User {user.email} already has azure_ad_id {user.azure_ad_id}, "
                        f"but token has {user_id}. Keeping existing azure_ad_id."
                    )
                    # Don't update azure_ad_id if it's already set to a different value
                if azure_tenant_id:
                    user.azure_tenant_id = azure_tenant_id
                # Update Azure department - set to None if empty string, otherwise update
                if azure_department is not None:
                    user.azure_department = azure_department if azure_department else None
                # Update Azure job title - set to None if empty string, otherwise update
                if azure_job_title is not None:
                    user.azure_job_title = azure_job_title if azure_job_title else None
                if external_id:
                    user.external_id = external_id
                # Update company - set to None if empty string, otherwise update
                if company is not None:
                    user.company = company if company else None
                # Update is_onboarded if provided (but don't overwrite if already True)
                if is_onboarded is not False:
                    user.is_onboarded = is_onboarded
                user.azure_ad_refresh_token = refresh_token  # Update refresh token
                user.is_verified = True  # Ensure user is marked as verified after Azure AD auth
                user.last_login = datetime.utcnow()
                # Set first_logged_in_at if not already set
                if not user.first_logged_in_at:
                    user.first_logged_in_at = datetime.utcnow()
                # Upload profile photo to blob storage if available
                if profile_photo_bytes:
                    try:
                        from aldar_middleware.orchestration.blob_storage import BlobStorageService
                        # Use main storage container for profile photos
                        blob_service = BlobStorageService(container_name=settings.azure_storage_container_name)
                        # Delete old photo if exists
                        old_blob_path = get_profile_photo_blob_path(user)
                        if old_blob_path:
                            try:
                                await blob_service.delete_blob(old_blob_path)
                                logger.info(f"Deleted old profile photo for user {user.email}")
                            except Exception as delete_error:
                                logger.warning(f"Error deleting old profile photo: {delete_error}")
                        # Upload new photo
                        photo_url, blob_path, _ = await blob_service.upload_profile_photo(
                            file_content=profile_photo_bytes,
                            user_id=str(user.id),
                            overwrite=True
                        )
                        set_profile_photo_blob_path(user, blob_path)
                        flag_modified(user, "preferences")
                        logger.info(f"Profile photo uploaded to blob storage for user {user.email}: {blob_path}")
                    except Exception as photo_error:
                        logger.warning(f"Error uploading profile photo to blob storage: {photo_error}")
                        # Continue without blob storage - fallback to proxy endpoint
                        profile_photo_url = f"{settings.api_prefix}/auth/users/{user.id}/profile-photo"
                
                # Initialize/update user preferences with all required fields
                profile_photo_url_with_user_id = get_profile_photo_url(user)
                if not profile_photo_url_with_user_id and profile_photo_url:
                    # Reconstruct profile photo URL using internal user ID (UUID) instead of Azure AD ID
                    profile_photo_url_with_user_id = f"{settings.api_prefix}/auth/users/{user.id}/profile-photo"
                
                initialize_user_preferences(user, profile_photo_url_with_user_id)
                flag_modified(user, "preferences")
                logger.info(f"User preferences updated for existing user: {user.email}")
                try:
                    await db.commit()
                    logger.info(f"User profile updated successfully in database for: {user.email}")
                except Exception as commit_error:
                    await db.rollback()
                    logger.error(f"Error updating user: {commit_error}")
                    raise

        # Sync user's Azure AD groups to pivot table
        # This flow:
        # 1. Gets user's email
        # 2. Calls MS Graph API internally to fetch all AD groups for the user
        # 3. Replaces old AD groups in user pivot table with new ones (or creates new entry if doesn't exist)
        user_pivot = None
        try:
            from aldar_middleware.services.rbac_pivot_service import RBACPivotService
            pivot_service = RBACPivotService(db)
            # Use email as primary identifier (field renamed from user_name to email)
            # Fallback to username only if email is not available
            user_email = user.email or user.username
            if user_email:
                # This internally calls MS Graph API to get AD groups, then updates/replaces pivot table entry
                user_pivot = await pivot_service.sync_user_ad_groups(user_email, access_token)
                logger.info(f"Successfully synced AD groups for user: {user_email}")
        except Exception as e:
            # Log error but don't fail login if AD group sync fails
            logger.error(f"Failed to sync AD groups for user {user.email}: {e}", exc_info=True)

        # Check if user should be admin based on Azure AD groups
        # If user belongs to any admin group IDs from settings, set is_admin=True
        # Note: This will override any manual admin assignments on login
        try:
            admin_group_ids = settings.admin_group_ids_list
            
            # Only proceed if admin groups are configured
            if not admin_group_ids:
                logger.debug(
                    f"No admin group IDs configured in settings - "
                    f"skipping admin check for user {user.email}"
                )
            elif user_pivot:
                # User has AD groups synced - check for admin group membership
                user_ad_groups = user_pivot.azure_ad_groups or []
                # Check if user has any of the admin group IDs
                user_groups_set = set(str(gid) for gid in user_ad_groups)
                admin_groups_set = set(str(gid).strip() for gid in admin_group_ids)
                matched_groups = user_groups_set & admin_groups_set
                is_admin = bool(matched_groups)
                
                # Log the comparison for debugging
                logger.info(
                    f"Admin group check for user {user.email}: "
                    f"user_groups={sorted(user_groups_set)}, "
                    f"admin_groups={sorted(admin_groups_set)}, "
                    f"matched_groups={sorted(matched_groups)}, "
                    f"is_admin={is_admin}"
                )
                
                # Reload user from database to ensure it's attached to the session
                # This is necessary because the user object might be detached after AD group sync
                result = await db.execute(
                    select(User).where(User.id == user.id)
                )
                user_in_session = result.scalar_one_or_none()
                
                if not user_in_session:
                    logger.warning(
                        f"Could not reload user {user.email} from database for admin check"
                    )
                else:
                    # Always update admin status based on group membership
                    # This ensures that if user loses admin groups, is_admin is set to False
                    if user_in_session.is_admin != is_admin:
                        user_in_session.is_admin = is_admin
                        try:
                            await db.commit()
                            await db.refresh(user_in_session)
                            # Update the original user object reference
                            user.is_admin = user_in_session.is_admin
                            logger.info(
                                f"Updated admin status for user {user.email}: "
                                f"is_admin={is_admin} "
                                f"(matched groups: {sorted(matched_groups) if matched_groups else 'none'})"
                            )
                        except Exception as commit_error:
                            await db.rollback()
                            logger.error(
                                f"Failed to commit admin status change for user {user.email}: "
                                f"{commit_error}",
                                exc_info=True
                            )
                    else:
                        logger.debug(
                            f"Admin status unchanged for user {user.email}: "
                            f"is_admin={user_in_session.is_admin} "
                            f"(matched groups: {sorted(matched_groups) if matched_groups else 'none'})"
                        )
            else:
                # Admin groups configured but user has no AD groups synced
                # This could mean:
                # 1. AD group sync failed (already logged above)
                # 2. User doesn't belong to any AD groups
                # Set to False if currently True (user lost admin groups or they were removed from env)
                
                # Reload user from database to ensure it's attached to the session
                result = await db.execute(
                    select(User).where(User.id == user.id)
                )
                user_in_session = result.scalar_one_or_none()
                
                if not user_in_session:
                    logger.warning(
                        f"Could not reload user {user.email} from database for admin check"
                    )
                elif user_in_session.is_admin:
                    user_in_session.is_admin = False
                    try:
                        await db.commit()
                        await db.refresh(user_in_session)
                        # Update the original user object reference
                        user.is_admin = user_in_session.is_admin
                        logger.info(
                            f"Removed admin status for user {user.email} "
                            f"(no AD groups synced or user not in admin groups)"
                        )
                    except Exception as commit_error:
                        await db.rollback()
                        logger.error(
                            f"Failed to remove admin status for user {user.email}: "
                            f"{commit_error}",
                            exc_info=True
                        )
        except Exception as e:
            # Log error but don't fail login if admin check fails
            # This ensures login still works even if admin check has issues
            logger.error(
                f"Failed to check admin status for user {user.email}: {e}",
                exc_info=True
            )

        # Refresh user object one final time to ensure we have the latest data
        # (especially is_admin status which might have been updated above)
        try:
            await db.refresh(user)
        except Exception:
            # If refresh fails, continue with current user object
            pass

        # CRITICAL FIX: Get application-scoped token for backend API authentication
        # The Graph API token (access_token) has aud=https://graph.microsoft.com
        # which fails validation when used for backend API calls.
        # We need a token with aud=client_id for backend API authentication.
        api_access_token = access_token  # Default to Graph token for backward compatibility
        
        if refresh_token:
            try:
                # Exchange refresh token for application-scoped token
                logger.info("Exchanging refresh token for application-scoped token...")
                app_token_response = await azure_ad_auth.get_application_token(refresh_token)
                api_access_token = app_token_response.get("access_token")
                if api_access_token:
                    logger.info("Successfully obtained application-scoped token for API authentication")
                else:
                    logger.warning("Application token exchange succeeded but no access_token in response, using Graph token")
            except Exception as e:
                logger.warning(f"Failed to get application-scoped token, using Graph token: {e}")
                # Continue with Graph token - refresh endpoint will handle it later
                api_access_token = access_token

        # Build response with application-scoped token for API authentication
        response = {
            "access_token": api_access_token,  # Application-scoped token for backend API
            "token_type": "bearer",
            "user": {
                "id": str(user.id),
                "email": user.email,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "full_name": user.full_name,  # Include full_name in response
                "is_admin": user.is_admin,  # Include is_admin status
            }
        }

        # Add refresh token if available
        if refresh_token:
            response["refresh_token"] = refresh_token

        return response

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Authentication failed: {str(e)}"
        )


async def _check_user_in_aiq_25_background(current_user: User) -> None:
    """
    Check if user exists in AIQ 2.5 by calling /mapi/v1/user/profile via OBO flow.
    This runs in the background and only logs results - does not return any data.
    
    This function:
    1. Gets user's Azure AD refresh token from database
    2. Uses refresh token to get Azure AD access token
    3. Exchanges access token for OBO token
    4. Calls AIQ 2.5 API with OBO token
    5. Logs the results
    
    Note: This runs in background and errors are only logged - they do NOT affect the main /me endpoint flow.
    
    Args:
        current_user: Current authenticated user
    """
    try:
        logger.info(f"🔄 [Background] Starting AIQ 2.5 profile check for user: {current_user.email}")
        
        # Get user's refresh token from database
        if not current_user.azure_ad_refresh_token:
            logger.warning(f"[Background] No refresh token found for user {current_user.email}")
            return
        
        # Get Azure AD access token from refresh token
        logger.info(f"🔄 [Background] Getting Azure AD access token for user: {current_user.email}")
        token_result = await azure_ad_auth.get_application_token(current_user.azure_ad_refresh_token)
        
        if not token_result or "access_token" not in token_result:
            logger.warning(f"[Background] Failed to get Azure AD access token for user {current_user.email}")
            return
        
        user_access_token = token_result["access_token"]
        logger.info(f"✓ [Background] Got Azure AD access token for user: {current_user.email}")
        
        # Call AIQ 2.5 API with OBO flow
        api_base_url = settings.azure_obo_api_base_url or "https://aiq-sit.adq.ae"
        api_url = f"{api_base_url}/mapi/v1/user/profile"
        
        logger.info(f"🌐 [Background] Checking user in AIQ 2.5: {api_url}")
        
        # Use azure_ad_obo_auth to call the API with OBO token
        result = await azure_ad_obo_auth.call_api_with_obo(
            user_access_token=user_access_token,
            api_url=api_url,
            method="GET"
        )
        
        # Log results
        if result:
            user_id = result.get("id", "")
            user_name = result.get("displayName") or result.get("email", "")
            logger.info(f"✓ [Background] User exists in AIQ 2.5 - ID: {user_id}, Name: {user_name}")
        else:
            logger.warning(f"[Background] User not found in AIQ 2.5: {current_user.email}")
        
    except HTTPException as e:
        # HTTP errors from the API call
        logger.warning(f"[Background] HTTP error checking user in AIQ 2.5: {e.detail}")
    except Exception as e:
        # Any other errors
        logger.warning(f"[Background] Error checking user in AIQ 2.5: {str(e)}")


@router.get("/me")
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Get current user information with all available fields.
    
    This endpoint also checks if the user exists in AIQ 2.5 by calling
    /mapi/v1/user/profile via OBO flow. The check is non-blocking and
    errors will not affect the main response.
    """
    # Get profile photo URL - check preferences first, then fallback to proxy endpoint
    from aldar_middleware.utils.user_utils import get_profile_photo_url
    profile_photo = get_profile_photo_url(current_user)
    if not profile_photo and current_user.azure_ad_id:
        # Fallback to proxy endpoint
        profile_photo = f"{settings.api_prefix}/auth/users/{current_user.id}/profile-photo"
    
    # Check if preferences need initialization
    preferences = current_user.preferences if isinstance(current_user.preferences, dict) else {}
    needs_initialization = (
        not preferences or
        "about_user" not in preferences or
        preferences.get("about_user", "").strip() == ""
    )
    
    # Auto-initialize preferences for existing users if needed
    if needs_initialization:
        logger.info(f"Auto-initializing preferences for user: {current_user.email}")
        initialize_user_preferences(current_user, profile_photo)
        flag_modified(current_user, "preferences")
        try:
            await db.commit()
            await db.refresh(current_user)
            logger.info(f"Preferences auto-initialized successfully for user: {current_user.email}")
            # Update preferences reference after commit
            preferences = current_user.preferences if isinstance(current_user.preferences, dict) else {}
        except Exception as e:
            await db.rollback()
            logger.error(f"Failed to auto-initialize preferences for user {current_user.email}: {e}")
            # Continue with empty preferences rather than failing the request
    
    # Trigger AIQ 2.5 profile check in background (fire and forget - does not block response)
    asyncio.create_task(_check_user_in_aiq_25_background(current_user))
    logger.info(f"🚀 Triggered background AIQ 2.5 profile check for user: {current_user.email}")
    
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "username": current_user.username,
        "first_name": current_user.first_name,
        "last_name": current_user.last_name,
        "full_name": current_user.full_name,
        "is_active": current_user.is_active,
        "is_verified": current_user.is_verified,
        "is_admin": current_user.is_admin,
        "azure_ad_id": current_user.azure_ad_id,
        "azure_display_name": current_user.azure_display_name,
        "azure_upn": current_user.azure_upn,
        "department": current_user.azure_department,  # Map azure_department to department
        "job_title": current_user.azure_job_title,  # Include job title
        "company": current_user.company,
        "external_id": current_user.external_id,
        "profile_photo": profile_photo,
        "is_onboarded": current_user.is_onboarded,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
        "updated_at": current_user.updated_at.isoformat() if current_user.updated_at else None,
        "last_login": current_user.last_login.isoformat() if current_user.last_login else None,
        "first_logged_in_at": current_user.first_logged_in_at.isoformat() if current_user.first_logged_in_at else None,
        # Add preference fields
        "preferences": {
            "preferred_formatting": preferences.get("preferred_formatting", ""),
            "topics_of_interest": preferences.get("topics_of_interest", ""),
            "about_user": preferences.get("about_user", ""),
            "enable_for_new_messages": preferences.get("enable_for_new_messages", True),
            "profile_photo": preferences.get("profile_photo", profile_photo)
        }
    }


@router.put("/me/preferences")
async def update_user_preferences(
    preferences_update: UserPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False))
) -> Dict[str, Any]:
    """Update current user's preferences.
    
    This endpoint allows users to update their personal preferences:
    - preferred_formatting: How they want responses formatted
    - topics_of_interest: Topics they're interested in
    - about_user: Information about the user (can be manually edited)
    - enable_for_new_messages: Whether to enable custom instructions for new messages
    """
    # Ensure preferences is a dictionary
    if current_user.preferences is None:
        current_user.preferences = {}
    elif not isinstance(current_user.preferences, dict):
        current_user.preferences = {}
    
    # Update only the fields that were provided (not None)
    if preferences_update.preferred_formatting is not None:
        current_user.preferences["preferred_formatting"] = preferences_update.preferred_formatting
    
    if preferences_update.topics_of_interest is not None:
        current_user.preferences["topics_of_interest"] = preferences_update.topics_of_interest
    
    if preferences_update.about_user is not None:
        current_user.preferences["about_user"] = preferences_update.about_user
    
    if preferences_update.enable_for_new_messages is not None:
        current_user.preferences["enable_for_new_messages"] = preferences_update.enable_for_new_messages
    
    if preferences_update.thinking_panel is not None:
        current_user.preferences["thinking_panel"] = preferences_update.thinking_panel
    
    # Flag the JSON column as modified so SQLAlchemy detects the change
    flag_modified(current_user, "preferences")
    
    try:
        await db.commit()
        await db.refresh(current_user)
        logger.info(f"Preferences updated successfully for user: {current_user.email}")
    except Exception as e:
        await db.rollback()
        logger.error(f"Error updating preferences for user {current_user.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update preferences"
        )

    # After local update, patch external user profile via OBO flow
    external_patch_status: str = "skipped"
    try:
        api_base_url = settings.azure_obo_api_base_url
        api_url = f"{api_base_url}/mapi/v1/user/profile"

        # Prepare payload mapping
        payload = {
            "customQueryAboutUser": current_user.preferences.get("about_user", ""),
            "customQueryPreferredFormatting": current_user.preferences.get("preferred_formatting", ""),
            "customQueryTopicsOfInterest": current_user.preferences.get("topics_of_interest", ""),
            "isCustomQueryEnabled": bool(current_user.preferences.get("enable_for_new_messages", True)),
        }

        if not credentials or not credentials.credentials:
            logger.warning("No Authorization token found on request; skipping OBO profile patch")
        else:
            user_access_token = credentials.credentials
            logger.info("Patching external profile via OBO API...")
            logger.info(f"   Target: {api_url}")
            logger.info(f"   Payload keys: {list(payload.keys())}")

            # Perform PATCH with OBO token exchange
            result = await azure_ad_obo_auth.call_api_with_obo(
                user_access_token=user_access_token,
                api_url=api_url,
                method="PATCH",
                data=payload,
            )

            external_patch_status = "success"
            logger.info("✓ External profile patched successfully via OBO")
            logger.debug(f"   Response keys: {list(result.keys())}")
    except HTTPException as e:
        external_patch_status = "failed"
        logger.error(f"OBO profile patch failed: {e.detail}")
    except Exception as e:
        external_patch_status = "failed"
        logger.error(f"Unexpected error during OBO profile patch: {e}")
    
    # Return updated preferences
    return {
        "message": "Preferences updated successfully",
        "preferences": {
            "preferred_formatting": current_user.preferences.get("preferred_formatting", ""),
            "topics_of_interest": current_user.preferences.get("topics_of_interest", ""),
            "about_user": current_user.preferences.get("about_user", ""),
            "enable_for_new_messages": current_user.preferences.get("enable_for_new_messages", True),
            "thinking_panel": current_user.preferences.get("thinking_panel", "closed")
        }
    }


@router.post("/refresh")
async def refresh_token(
    request: Request,
    refresh_token: Optional[str] = Body(default=None, embed=True),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False))
) -> Dict[str, Any]:
    """Refresh access token using Azure AD refresh token.

    Supports two modes:
    - Auth header present: uses the authenticated user's stored refresh token unless a body refresh_token is provided.
    - No auth header: requires a body refresh_token; tries to refresh with Azure AD, then finds user by stored token.
    """
    try:
        # Enforce rate limiting on token refresh attempts
        await _enforce_auth_rate_limit(request)
        # Determine the user and effective refresh token to use
        target_user: Optional[User] = None
        effective_refresh_token: Optional[str] = None

        # If Authorization header present, try to derive user; otherwise require refresh_token body
        if credentials and credentials.credentials:
            token = credentials.credentials
            try:
                # Validate Azure AD token to get user
                payload = await azure_ad_auth.validate_token(token)
                azure_ad_user_id = payload.get("oid") or payload.get("sub")
                if azure_ad_user_id:
                    async for db in get_db():
                        result = await db.execute(select(User).where(User.azure_ad_id == azure_ad_user_id))
                        target_user = result.scalar_one_or_none()
            except Exception:
                # If token invalid/expired, we'll fall back to body refresh_token
                target_user = None

        if target_user is not None:
            # Use provided refresh_token or fall back to stored token
            effective_refresh_token = refresh_token or target_user.azure_ad_refresh_token
        else:
            # No valid auth header; require body refresh_token
            if not refresh_token:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="refresh_token is required in request body"
                )

            # Try to refresh token first with Azure AD (this validates the token)
            # If successful, then find user by current or new refresh token
            try:
                token_response = await azure_ad_auth.refresh_access_token(refresh_token)
                effective_refresh_token = refresh_token
                new_refresh_token = token_response.get("refresh_token")

                # Find user by refresh token (check both old and new refresh token)
                async for db in get_db():
                    # First try to find by provided refresh token
                    result = await db.execute(
                        select(User).where(User.azure_ad_refresh_token == refresh_token)
                    )
                    target_user = result.scalar_one_or_none()

                    # If not found and we got a new refresh token, try that too
                    if not target_user and new_refresh_token:
                        result = await db.execute(
                            select(User).where(User.azure_ad_refresh_token == new_refresh_token)
                        )
                        target_user = result.scalar_one_or_none()

                    if not target_user:
                        # If still not found, try to find by all users and match Azure AD user ID
                        # This is a fallback in case refresh token changed but we can identify user
                        if token_response.get("id_token"):
                            try:
                                id_token_info = await azure_ad_auth.decode_id_token(token_response.get("id_token"))
                                user_id = id_token_info.get("oid") or id_token_info.get("sub")
                                if user_id:
                                    result = await db.execute(
                                        select(User).where(User.azure_ad_id == user_id)
                                    )
                                    target_user = result.scalar_one_or_none()
                            except Exception:
                                pass

                    if not target_user:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid refresh token or user not found"
                        )

                    # Update refresh token in database
                    if new_refresh_token:
                        target_user.azure_ad_refresh_token = new_refresh_token
                        await db.commit()

                # Get Azure AD access_token from token response
                azure_access_token = token_response.get("access_token")
                if not azure_access_token:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="No access token received from Azure AD"
                    )

                # Return Azure AD access_token directly
                response = {
                    "access_token": azure_access_token,  # Use Azure AD access_token directly
                    "token_type": "bearer",
                    "user": {
                        "id": str(target_user.id),
                        "email": target_user.email,
                        "username": target_user.username,
                        "first_name": target_user.first_name,
                        "last_name": target_user.last_name,
                        "full_name": target_user.full_name,
                    }
                }

                # Add refresh token if available
                if new_refresh_token:
                    response["refresh_token"] = new_refresh_token

                return response

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error refreshing token: {e}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Invalid refresh token: {str(e)}"
                )

        if not effective_refresh_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No refresh token available for this user"
            )

        # Refresh the token with Azure AD
        token_response = await azure_ad_auth.refresh_access_token(effective_refresh_token)

        # Extract Azure AD access_token and refresh_token
        azure_access_token = token_response.get("access_token")
        new_refresh_token = token_response.get("refresh_token")

        if not azure_access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No access token received from Azure AD"
            )

        # Update refresh token in database if a new one was provided
        if new_refresh_token and target_user is not None:
            async for db in get_db():
                # Fetch user in this session to avoid detached instance error
                result = await db.execute(select(User).where(User.id == target_user.id))
                user_in_session = result.scalar_one_or_none()
                if user_in_session:
                    user_in_session.azure_ad_refresh_token = new_refresh_token
                await db.commit()
                break  # Ensure we exit the loop after use

        # Return Azure AD access_token directly
        response = {
            "access_token": azure_access_token,  # Use Azure AD access_token directly
            "token_type": "bearer",
            "user": {
            "id": str(target_user.id),
            "email": target_user.email,
            "username": target_user.username,
            "first_name": target_user.first_name,
            "last_name": target_user.last_name,
            "full_name": target_user.full_name,  # Include full_name in response
            }
        }

        # Add refresh token if available
        if new_refresh_token:
            response["refresh_token"] = new_refresh_token

        return response

    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Token refresh failed: {str(e)}"
        )


@router.post("/logout")
async def logout(
    current_user: User = Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Dict[str, Any]:
    """Logout endpoint - invalidates the current Azure AD access token."""
    from aldar_middleware.auth.token_blacklist import token_blacklist
    import time
    import jwt

    token = credentials.credentials

    # Extract expiry time from the Azure AD token
    try:
        # Decode Azure AD token without verification to get expiry
        # Azure AD tokens use RS256, but we just need the expiry claim
        payload = jwt.decode(
            token,
            options={"verify_signature": False, "verify_exp": False}
        )

        expiry_time = payload.get("exp", time.time() + 3600)  # Default to 1 hour if not found

        # Add token to blacklist
        token_blacklist.blacklist_token(token, expiry_time=expiry_time)

        logger.info(f"User {current_user.email} logged out successfully, Azure AD token blacklisted")

        return {
            "message": "Logged out successfully",
            "token_invalidated": True
        }

    except Exception as e:
        logger.error(f"Error blacklisting Azure AD token: {e}")
        # Still return success even if blacklist fails
        return {"message": "Logged out successfully"}


@router.get("/users/{user_id}/profile-photo")
async def get_user_profile_photo(
    user_id: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Get user profile photo as image.
    
    This endpoint serves profile photos from blob storage if available,
    otherwise falls back to Microsoft Graph API.
    Authentication is NOT required - profile photos can be accessed without auth for frontend img tags.
    
    Args:
        user_id: Internal user UUID or Azure AD ID
        db: Database session
        
    Returns:
        Image response with profile photo bytes
    """
    from uuid import UUID
    
    try:
        user = None
        
        # Try to parse as UUID (internal user ID) first
        try:
            user_uuid = UUID(user_id)
            result = await db.execute(select(User).where(User.id == user_uuid))
            user = result.scalar_one_or_none()
        except ValueError:
            # Not a valid UUID format, will try as Azure AD ID below
            pass
        
        # If not found by internal ID, try as Azure AD ID
        if not user:
            result = await db.execute(select(User).where(User.azure_ad_id == user_id))
            user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # First, try to serve from blob storage
        blob_path = get_profile_photo_blob_path(user)
        if blob_path:
            try:
                from aldar_middleware.orchestration.blob_storage import BlobStorageService
                # Use main storage container for profile photos
                blob_service = BlobStorageService(container_name=settings.azure_storage_container_name)
                photo_bytes = await blob_service.download_blob(blob_path)
                
                logger.debug(f"Serving profile photo from blob storage for user {user.email}")
                return Response(
                    content=photo_bytes,
                    media_type="image/jpeg",
                    headers={
                        "Cache-Control": "public, max-age=86400",  # Cache for 24 hours
                    }
                )
            except Exception as blob_error:
                logger.warning(f"Error fetching profile photo from blob storage: {blob_error}, falling back to Graph API")
                # Fall through to Graph API fallback
        
        # Fallback to Microsoft Graph API if blob storage is not available
        if not user.azure_ad_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User does not have Azure AD profile and no photo in blob storage"
            )
        
        # Get access token using refresh token
        if not user.azure_ad_refresh_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User refresh token not available"
            )
        
        # Extract all needed user data before async operations to avoid database connection issues
        user_email = user.email
        user_azure_ad_id = user.azure_ad_id
        refresh_token = user.azure_ad_refresh_token
        
        # Refresh the access token
        try:
            token_response = await azure_ad_auth.refresh_access_token(refresh_token)
            access_token = token_response.get("access_token")
            
            if not access_token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Failed to get access token"
                )
        except Exception as e:
            logger.error(f"Error refreshing token for profile photo: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Failed to authenticate with Azure AD"
            )
        
        # Fetch photo bytes from Microsoft Graph
        try:
            photo_bytes = await azure_ad_auth.get_user_profile_photo_bytes(access_token, user_azure_ad_id)
            
            if not photo_bytes:
                logger.warning(f"Profile photo not available for user {user_email} (ID: {user_azure_ad_id})")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Profile photo not found or not available"
                )
            
            # Store in blob storage for future use (async, don't wait)
            try:
                from aldar_middleware.orchestration.blob_storage import BlobStorageService
                # Use main storage container for profile photos
                blob_service = BlobStorageService(container_name=settings.azure_storage_container_name)
                # Delete old photo if exists
                old_blob_path = get_profile_photo_blob_path(user)
                if old_blob_path:
                    try:
                        await blob_service.delete_blob(old_blob_path)
                    except Exception:
                        pass  # Ignore delete errors
                # Upload new photo
                photo_url, blob_path, _ = await blob_service.upload_profile_photo(
                    file_content=photo_bytes,
                    user_id=str(user.id),
                    overwrite=True
                )
                set_profile_photo_blob_path(user, blob_path)
                # Update user in database (async, don't block response)
                flag_modified(user, "preferences")
                await db.commit()
                logger.info(f"Profile photo stored in blob storage for user {user_email}")
            except Exception as store_error:
                logger.warning(f"Error storing profile photo in blob storage: {store_error}")
                # Continue - photo is still served from Graph API
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching profile photo for user {user_email}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch profile photo from Microsoft Graph"
            )
        
        # Return image with proper headers
        # Microsoft Graph returns JPEG images by default
        logger.debug(f"Serving profile photo from Graph API for user {user.email}")
        return Response(
            content=photo_bytes,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "public, max-age=3600",  # Cache for 1 hour
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching profile photo: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch profile photo"
        )
