"""AGNO Multiagent API service for comprehensive integration with all endpoints."""

import json
import time
import uuid
from typing import Dict, Any, Optional, List, Union, AsyncIterator
from datetime import datetime, timedelta
from enum import Enum

import httpx
import jwt
from loguru import logger
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import selectinload

from aiq_backend.settings import settings
from aiq_backend.database.base import get_db
from aiq_backend.settings.context import get_correlation_id, track_agent_call
from aiq_backend.auth.obo_utils import exchange_token_obo, create_mcp_token
from aiq_backend.monitoring.prometheus import (
    record_external_api_request,
    record_external_api_error,
    record_external_api_cache_hit,
    record_external_api_cache_miss
)


class AGNOAPIType(str, Enum):
    """AGNO API types."""
    AGNO_MULTIAGENT = "agno_multiagent"


# Global HTTP client with connection pooling
_http_client: Optional[httpx.AsyncClient] = None

# Simple in-memory cache
_cache: Dict[str, Dict[str, Any]] = {}


async def get_http_client() -> httpx.AsyncClient:
    """Get or create the global HTTP client with connection pooling.
    
    SECURITY: SSL certificate verification is ENABLED by default.
    This prevents man-in-the-middle attacks.
    """
    global _http_client
    if _http_client is None:
        # Set shorter timeouts: 5s connect, configured timeout for read/write
        # This prevents long waits when server is unreachable
        timeout_config = httpx.Timeout(
            connect=5.0,  # 5 seconds to establish connection
            read=settings.agno_api_timeout,  # Read timeout from config
            write=10.0,  # 10 seconds to write request
            pool=5.0  # 5 seconds to get connection from pool
        )
        _http_client = httpx.AsyncClient(
            timeout=timeout_config,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
            http2=True,
            verify=True  # SECURITY: Enable SSL certificate verification (default, but explicit)
        )
    return _http_client


async def close_http_client():
    """Close the global HTTP client."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


def _cleanup_expired_cache():
    """Remove expired cache entries."""
    current_time = time.time()
    expired_keys = []
    
    for key, entry in _cache.items():
        if entry.get('expires_at', 0) < current_time:
            expired_keys.append(key)
    
    for key in expired_keys:
        del _cache[key]


class AGNOAPIService:
    """Service for managing AGNO Multiagent API integrations with caching."""

    def __init__(self):
        """Initialize AGNO API service."""
        self.base_url = settings.agno_base_url
        self.default_timeout = settings.agno_api_timeout
        self.cache_ttl = settings.agno_api_cache_ttl
        self.max_retries = settings.agno_api_max_retries
        self.retry_delay = settings.agno_api_retry_delay

    async def make_request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        cache_ttl: Optional[int] = None,
        force_refresh: bool = False,
        user_id: Optional[str] = None,
        content_type: str = "application/json",
        files: Optional[List] = None,
        authorization_header: Optional[str] = None,
        user_access_token: Optional[str] = None,
        obo_token: Optional[str] = None,
        return_mcp_token: bool = False
    ) -> Dict[str, Any]:
        """Make a request to AGNO API with caching support.
        
        Args:
            endpoint: API endpoint (e.g., "/agents", "/models")
            method: HTTP method (GET, POST, PUT, DELETE, PATCH)
            data: Request payload
            headers: Additional headers
            cache_ttl: Cache TTL in seconds (overrides default)
            force_refresh: Force refresh cache
            user_id: User ID for request tracking
            content_type: Content type for the request (application/json or multipart/form-data)
            files: Files to upload (for multipart requests)
            authorization_header: Authorization header (JWT token)
            user_access_token: User access token for OBO exchange (will be exchanged for OBO token)
            
        Returns:
            API response data
        """
        start_time = time.time()
        correlation_id = get_correlation_id() or "test-request"
        request_id = str(uuid.uuid4())
        
        # Build full URL - handle trailing/leading slashes properly
        base_url_clean = self.base_url.rstrip("/")
        endpoint_clean = endpoint.lstrip("/")
        # Ensure no double slashes
        if endpoint_clean.startswith("/"):
            endpoint_clean = endpoint_clean[1:]
        full_url = f"{base_url_clean}/{endpoint_clean}"
        
        # Step 1: Get OBO token - Priority: 1) Manually provided obo_token, 2) Exchange from user_access_token
        final_obo_token = obo_token  # Use manually provided OBO token if available
        if not final_obo_token and user_access_token:
            # Auto-generate OBO token from user_access_token
            try:
                logger.info(f"🔄 Exchanging user token for OBO token for endpoint: {endpoint}")
                logger.info(f"   User access token length: {len(user_access_token) if user_access_token else 0}")
                final_obo_token = await exchange_token_obo(user_access_token)
                logger.info(f"✓ OBO token obtained for endpoint: {endpoint}")
                logger.info(f"   OBO token length: {len(final_obo_token) if final_obo_token else 0}")
            except HTTPException as e:
                # Handle configuration errors gracefully - continue without OBO token
                error_detail = str(e.detail) if hasattr(e, 'detail') else str(e)
                if "Target client ID not configured" in error_detail or "not configured for OBO flow" in error_detail:
                    logger.warning(f"⚠️  OBO token exchange skipped: {error_detail}")
                    logger.warning(f"   Continuing without OBO token - data API may not receive Authorization-OBO header")
                else:
                    logger.error(f"⚠️  OBO token exchange failed: {error_detail}")
                    logger.error(f"   Continuing without OBO token")
                final_obo_token = None  # Explicitly set to None to continue without OBO token
            except Exception as e:
                logger.error(f"⚠️  Failed to exchange OBO token for endpoint {endpoint}: {e}")
                import traceback
                logger.error(f"   Traceback: {traceback.format_exc()}")
                final_obo_token = None  # Continue without OBO token if exchange fails
        elif final_obo_token:
            logger.info(f"✓ Using manually provided OBO token for endpoint: {endpoint}")
            logger.info(f"   OBO token length: {len(final_obo_token)}")
        else:
            logger.debug(f"No OBO token available for endpoint {endpoint} - OBO token exchange skipped")
        
        # Step 2: Extract JWT token from authorization_header (will be sent in header, not payload)
        jwt_token = None
        if authorization_header:
            # Extract token from "Bearer <token>" format
            if authorization_header.startswith("Bearer "):
                jwt_token = authorization_header[7:]
            else:
                jwt_token = authorization_header
        
        # Initialize data if None (but don't add JWT token to payload - it goes in header)
        if data is None:
            data = {}
        
        # JWT token and OBO token will be sent in headers (not in payload)
        # This is handled in _make_http_request method
        
        # Create cache key (after modifying data)
        cache_key = self._generate_cache_key(endpoint, method, data)
        
        # Log external API call details
        logger.info(
            f"🌐 EXTERNAL API CALL - AGNO API: {method} {full_url}, "
            f"endpoint={endpoint}, request_id={request_id}, correlation_id={correlation_id}, "
            f"user_id={user_id}, content_type={content_type}, "
            f"has_auth_header={'Yes' if authorization_header else 'No'}"
        )
        
        # Log request data (sanitized for sensitive info)
        if data and method.upper() in ["POST", "PUT", "PATCH"]:
            # Don't log full data if it's too large, just log keys
            if isinstance(data, dict):
                data_keys = list(data.keys())
                # Show actual values for non-sensitive fields, mask sensitive ones
                sensitive_fields = {"password", "token", "secret", "authorization", "auth", "api_key", "apikey"}
                data_preview = {}
                for k, v in data.items():
                    key_lower = str(k).lower()
                    if any(sensitive in key_lower for sensitive in sensitive_fields):
                        data_preview[k] = "<REDACTED>"
                    elif isinstance(v, str) and len(v) > 100:
                        data_preview[k] = f"{v[:100]}... (truncated)"
                    elif isinstance(v, dict):
                        # Special handling for user_context to show preferences
                        if k == "user_context" and isinstance(v, dict):
                            user_context_preview = {}
                            for uc_k, uc_v in v.items():
                                if uc_k == "preferences" and isinstance(uc_v, dict):
                                    # Show preferences in detail
                                    user_context_preview[uc_k] = uc_v
                                else:
                                    user_context_preview[uc_k] = uc_v
                            data_preview[k] = user_context_preview
                        elif len(str(v)) > 200:
                            data_preview[k] = f"<dict with {len(v)} keys>"
                        else:
                            data_preview[k] = v
                    elif isinstance(v, list) and len(v) > 10:
                        data_preview[k] = f"<list with {len(v)} items>"
                    else:
                        data_preview[k] = v
                logger.debug(
                    f"📤 EXTERNAL API REQUEST DATA: {data_preview}, "
                    f"keys={data_keys}, correlation_id={correlation_id}"
                )
        
        try:
            # Check cache first (unless force refresh)
            if not force_refresh and method == "GET":
                cached_response = await self.get_cached_response(
                    cache_key, user_id, correlation_id
                )
                if cached_response:
                    duration = time.time() - start_time
                    record_external_api_cache_hit(
                        api_type=AGNOAPIType.AGNO_MULTIAGENT.value,
                        endpoint=endpoint,
                        duration=duration
                    )
                    return cached_response
            
            # Make actual API request
            response_data, mcp_token_created = await self._make_http_request(
                full_url, method, data, headers, correlation_id, content_type, files, authorization_header, final_obo_token, jwt_token, user_access_token, return_mcp_token
            )
            
            # Include MCP token in response if requested
            if return_mcp_token and mcp_token_created:
                if isinstance(response_data, dict):
                    response_data["mcp_token"] = mcp_token_created
                else:
                    # If response is not a dict, wrap it
                    response_data = {
                        "data": response_data,
                        "mcp_token": mcp_token_created
                    }
            
            # Save to cache if successful
            if method == "GET" and response_data:
                await self.save_to_cache(
                    cache_key, response_data, endpoint, 
                    cache_ttl or self.cache_ttl, user_id, correlation_id
                )
            
            # Record metrics
            duration = time.time() - start_time
            record_external_api_request(
                api_type=AGNOAPIType.AGNO_MULTIAGENT.value,
                endpoint=endpoint,
                method=method,
                status="success",
                duration=duration
            )
            
            logger.info(
                f"✅ EXTERNAL API SUCCESS: {method} {full_url}, "
                f"endpoint={endpoint}, duration={duration:.3f}s, "
                f"request_id={request_id}, correlation_id={correlation_id}"
            )
            
            return response_data
            
        except Exception as e:
            duration = time.time() - start_time
            # Get detailed error message
            error_msg = str(e) if str(e) else f"{type(e).__name__}: {repr(e)}"
            if not error_msg:
                error_msg = "Unknown error occurred"
            
            record_external_api_error(
                api_type=AGNOAPIType.AGNO_MULTIAGENT.value,
                endpoint=endpoint,
                method=method,
                error=error_msg,
                duration=duration
            )
            
            logger.error(
                f"❌ EXTERNAL API FAILED: {method} {full_url}, "
                f"endpoint={endpoint}, error={error_msg}, error_type={type(e).__name__}, "
                f"request_id={request_id}, correlation_id={correlation_id}, duration={duration:.3f}s"
            )
            
            # Re-raise with better error message if original was empty
            if not str(e):
                raise ValueError(f"AGNO API request failed: {method} {full_url} - {error_msg}") from e
            raise

    async def _make_http_request(
        self,
        url: str,
        method: str,
        data: Optional[Dict[str, Any]],
        headers: Optional[Dict[str, str]],
        correlation_id: Optional[str],
        content_type: str = "application/json",
        files: Optional[List] = None,
        authorization_header: Optional[str] = None,
        obo_token: Optional[str] = None,
        jwt_token: Optional[str] = None,
        user_access_token: Optional[str] = None,
        return_mcp_token: bool = False
    ) -> tuple[Dict[str, Any], Optional[str]]:
        """Make HTTP request to AGNO API using pooled client."""
        default_headers = {
            "Content-Type": content_type,
            "User-Agent": f"AIQ-Backend/{settings.app_version}"
        }
        
        # Add correlation ID if available
        if correlation_id:
            default_headers["X-Correlation-ID"] = correlation_id
        
        # Step 1: Create MCP token if we have user_access_token
        # If obo_token is not provided but user_access_token is, auto-exchange for OBO token
        mcp_token_created = None
        effective_obo_token = obo_token  # Use provided OBO token or exchange for one
        effective_aria_token = None  # ARIA token for external API
        
        if user_access_token and not effective_obo_token:
            # Auto-exchange for OBO token if not provided
            try:
                logger.info(" Auto-exchanging Azure AD token for OBO token...")
                effective_obo_token = await exchange_token_obo(user_access_token)
                logger.info("✓ OBO token obtained via auto-exchange")
            except Exception as e:
                logger.warning(f"  Failed to auto-exchange for OBO token: {e}")
                effective_obo_token = None
        
        # Exchange for ARIA token if user_access_token is available
        if user_access_token:
            try:
                from aiq_backend.auth.obo_utils import exchange_token_aria
                logger.info(" Exchanging Azure AD token for ARIA token...")
                effective_aria_token = await exchange_token_aria(user_access_token)
                logger.info(f"✓ ARIA token obtained ({len(effective_aria_token) if effective_aria_token else 0} chars)")
            except Exception as e:
                logger.warning(f" Failed to exchange for ARIA token: {e}")
                effective_aria_token = None
        
        # Create MCP token if we have both OBO token and user_access_token
        # MCP token format: JWT with OBO token and optional ARIA token embedded
        if effective_obo_token and user_access_token:
            try:
                logger.info(" Creating MCP token with OBO token and ARIA token embedded...")
                mcp_token_created = create_mcp_token(user_access_token, effective_obo_token, effective_aria_token)
                logger.info("✓ MCP token created")
                logger.debug(f"   MCP token length: {len(mcp_token_created)} characters")
                
                # Verify MCP token contains mcp_token field (only in debug mode)
                if settings.debug:
                    try:
                        from aiq_backend.auth.obo_utils import verify_mcp_token
                        decoded_mcp = verify_mcp_token(mcp_token_created)
                        if "mcp_token" in decoded_mcp:
                            embedded_obo = decoded_mcp["mcp_token"]
                            logger.debug(" VERIFIED: MCP token decoded successfully")
                            logger.debug(f"   mcp_token field exists: True")
                            logger.debug(f"   mcp_token length: {len(embedded_obo)} characters")
                            logger.debug(f"   Decoded payload keys: {list(decoded_mcp.keys())}")
                            logger.debug(f"   Decoded payload metadata: sub={decoded_mcp.get('sub')}, email={decoded_mcp.get('email')}, iss={decoded_mcp.get('iss')}")
                        else:
                            logger.error(" ERROR: MCP token does NOT contain mcp_token field!")
                            logger.error(f"   Decoded payload keys: {list(decoded_mcp.keys())}")
                    except Exception as verify_error:
                        logger.warning(f"  Could not verify MCP token: {verify_error}")
                
                # Use MCP token in Authorization header instead of separate OBO header
                default_headers["Authorization"] = f"Bearer {mcp_token_created}"
                logger.info("✓ Using MCP token in Authorization header (contains OBO token in mcp_token field)")
                # TESTING: Log full token for debugging
                # logger.info(f" [TESTING] Full MCP Token being sent to data API: {mcp_token_created}")
            except Exception as e:
                logger.warning(f"  Failed to create MCP token: {e}")
                import traceback
                logger.warning(f"   Traceback: {traceback.format_exc()}")
                logger.warning("   Falling back to separate OBO token header")
                mcp_token_created = None
        
        # Step 2: If MCP token not created, try to create custom JWT from Azure AD token
        if not mcp_token_created:
            # If caller provided an Authorization header that contains an MCP JWT
            # (our own JWT with `mcp_token` embedded), extract the embedded OBO
            # token and send that as the Authorization header to AGNO. This
            # allows clients to forward `jwt_token_with_mcp` directly.
            if authorization_header:
                jwt_candidate = authorization_header[7:] if authorization_header.startswith("Bearer ") else authorization_header
                try:
                    from aiq_backend.auth.obo_utils import extract_obo_from_mcp_jwt

                    obo_from_mcp = extract_obo_from_mcp_jwt(jwt_candidate, verify_signature=False)
                    # Use the extracted OBO token as the Authorization header for AGNO
                    default_headers["Authorization"] = f"Bearer {obo_from_mcp}"
                    logger.info("✓ Extracted OBO from provided MCP JWT and using it in Authorization header")
                    logger.info(f"   Authorization (OBO) preview: {obo_from_mcp[:50]}...{obo_from_mcp[-20:]}")
                    # TESTING: Log full OBO token for debugging
                    logger.info(f"🔑 [TESTING] Full OBO Token being sent to data API: {obo_from_mcp}")
                except Exception:
                    # Not an MCP JWT (or extraction failed)
                    # If we have user_access_token, create a custom JWT (HS256) from it
                    # External APIs need HS256, not RS256 (Azure AD tokens)
                    if user_access_token:
                        try:
                            from aiq_backend.auth.obo_utils import decode_token_without_verification
                            
                            # Decode Azure AD token to get user info
                            decoded_azure = decode_token_without_verification(user_access_token)
                            
                            # Extract user info
                            user_sub = decoded_azure.get('sub') or decoded_azure.get('oid')
                            user_email = (
                                decoded_azure.get('email') or
                                decoded_azure.get('preferred_username') or 
                                decoded_azure.get('upn') or
                                'unknown@example.com'
                            )
                            
                            # Create custom JWT (HS256) - even without OBO token
                            # External API needs HS256 format
                            exp = decoded_azure.get('exp') or (int(time.time()) + 3600)
                            iat = decoded_azure.get('iat') or int(time.time())
                            
                            custom_jwt_payload = {
                                "sub": user_sub,
                                "email": user_email,
                                "exp": exp,
                                "iat": iat,
                                "iss": "aiq-backend"
                            }
                            
                            # Add mcp_token if we have OBO token
                            if effective_obo_token:
                                custom_jwt_payload["mcp_token"] = effective_obo_token
                                logger.info("✓ Created custom JWT with mcp_token (OBO token available)")
                            else:
                                # Create JWT without mcp_token - external API might still accept it
                                # Or we can use a placeholder - but better to have OBO configured
                                logger.warning("⚠️  Creating custom JWT without mcp_token (OBO exchange failed)")
                                logger.warning("   External API might reject this token. Please configure OBO flow.")
                            
                            # Create custom JWT with HS256 (what external API expects)
                            custom_jwt = jwt.encode(
                                custom_jwt_payload,
                                settings.jwt_secret_key,
                                algorithm=settings.jwt_algorithm  # HS256
                            )
                            
                            default_headers["Authorization"] = f"Bearer {custom_jwt}"
                            logger.info("✓ Created custom JWT (HS256) from Azure AD token for external API")
                            logger.info(f"   Custom JWT length: {len(custom_jwt)} characters")
                            logger.info(f"   Has mcp_token: {'Yes' if effective_obo_token else 'No'}")
                            # TESTING: Log full token for debugging
                            logger.info(f"🔑 [TESTING] Full Custom JWT Token being sent to data API: {custom_jwt}")
                        except Exception as jwt_error:
                            logger.warning(f"⚠️  Failed to create custom JWT from Azure AD token: {jwt_error}")
                            import traceback
                            logger.warning(f"   Traceback: {traceback.format_exc()}")
                            # Fall back to sending original Authorization header
                            default_headers["Authorization"] = authorization_header
                            logger.info("✓ Added original JWT token in Authorization header (fallback)")
                            jwt_token_for_log = authorization_header.replace("Bearer ", "") if authorization_header.startswith("Bearer ") else authorization_header
                            logger.info(f"   Authorization: Bearer {jwt_token_for_log[:50]}...{jwt_token_for_log[-20:]}")
                            # TESTING: Log full token for debugging
                            logger.info(f"🔑 [TESTING] Full Fallback Token being sent to data API: {jwt_token_for_log}")
                    else:
                        # No user_access_token - fall back to sending the original Authorization header
                        default_headers["Authorization"] = authorization_header
                        logger.info("✓ Added JWT token in Authorization header")
                        jwt_token_for_log = authorization_header.replace("Bearer ", "") if authorization_header.startswith("Bearer ") else authorization_header
                        logger.info(f"   Authorization: Bearer {jwt_token_for_log[:50]}...{jwt_token_for_log[-20:]}")
                        # TESTING: Log full token for debugging
                        logger.info(f"🔑 [TESTING] Full Original Token being sent to data API: {jwt_token_for_log}")

            # OBO token goes in "Authorization-OBO" header (legacy format)
            if effective_obo_token:
                default_headers["Authorization-OBO"] = f"Bearer {effective_obo_token}"
                logger.info("✓ Using OBO token in Authorization-OBO header (legacy format)")
                logger.debug(f"   OBO token length: {len(effective_obo_token)} characters")
            elif settings.agno_api_key:
                default_headers["Authorization"] = f"Bearer {settings.agno_api_key}"
                logger.info("✓ Using AGNO API key in Authorization header")
                # TESTING: Log API key (first/last chars only for security)
                api_key_preview = f"{settings.agno_api_key[:10]}...{settings.agno_api_key[-10:]}" if len(settings.agno_api_key) > 20 else "***"
                logger.info(f"🔑 [TESTING] Using AGNO API Key: {api_key_preview}")
        
        # Log summary of headers being sent to data API
        headers_summary = []
        if mcp_token_created:
            headers_summary.append("Authorization: ✓ (MCP token with OBO embedded)")
        else:
            if "Authorization-OBO" in default_headers:
                headers_summary.append("Authorization-OBO: ✓")
            if "Authorization" in default_headers:
                headers_summary.append("Authorization: ✓")
        if headers_summary:
            logger.info(f"📋 Headers being sent to data API: {', '.join(headers_summary)}")
        else:
            logger.warning("⚠️  No authorization headers being sent to data API")
        
        if headers:
            default_headers.update(headers)
        
        # Log request details before making the call
        logger.info(
            f"📡 Making HTTP request to external API: {method} {url}, "
            f"content_type={content_type}, has_files={'Yes' if files else 'No'}, "
            f"correlation_id={correlation_id}"
        )
        
        # TESTING: Log the final Authorization token being sent
        if "Authorization" in default_headers:
            auth_token = default_headers["Authorization"]
            # Remove "Bearer " prefix if present for logging
            token_only = auth_token.replace("Bearer ", "") if auth_token.startswith("Bearer ") else auth_token
            logger.info(f"🔑 [TESTING] Final Authorization Token being sent to data API: {token_only}")
            # Also decode and show payload for debugging
            try:
                from aiq_backend.auth.obo_utils import decode_token_without_verification
                decoded = decode_token_without_verification(token_only)
                # logger.info(f"🔑 [TESTING] Decoded Token Payload: {decoded}")
                if "mcp_token" in decoded:
                    logger.info(f"🔑 [TESTING] Token HAS mcp_token field: {decoded['mcp_token'][:50]}...")
                else:
                    logger.warning(f"🔑 [TESTING] Token DOES NOT have mcp_token field. Available keys: {list(decoded.keys())}")
            except Exception as decode_error:
                logger.warning(f"🔑 [TESTING] Could not decode token for testing: {decode_error}")
        
        # Use the pooled HTTP client
        client = await get_http_client()
        request_start_time = time.time()
        
        if method.upper() == "GET":
            response = await client.get(url, headers=default_headers)
        elif method.upper() == "POST":
            if content_type == "multipart/form-data":
                # Remove Content-Type header for multipart requests (httpx will set it automatically)
                multipart_headers = {k: v for k, v in default_headers.items() if k.lower() != "content-type"}
                response = await client.post(url, data=data, files=files, headers=multipart_headers)
            elif content_type == "application/x-www-form-urlencoded":
                response = await client.post(url, data=data, headers=default_headers)
            else:
                response = await client.post(url, json=data, headers=default_headers)
        elif method.upper() == "PUT":
            if content_type == "multipart/form-data":
                multipart_headers = {k: v for k, v in default_headers.items() if k.lower() != "content-type"}
                response = await client.put(url, data=data, files=files, headers=multipart_headers)
            else:
                response = await client.put(url, json=data, headers=default_headers)
        elif method.upper() == "DELETE":
            response = await client.delete(url, headers=default_headers)
        elif method.upper() == "PATCH":
            response = await client.patch(url, json=data, headers=default_headers)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")
        
        # Calculate request duration
        request_duration = time.time() - request_start_time
        
        # Log response received
        logger.info(
            f"📥 EXTERNAL API RESPONSE: {method} {url}, "
            f"status={response.status_code}, duration={request_duration:.3f}s, "
            f"correlation_id={correlation_id}"
        )
        
        # Check response status and provide better error messages
        if response.status_code >= 400:
            try:
                error_data = response.json()
                error_message = error_data.get("detail", error_data.get("message", error_data.get("error", "Unknown error")))
                # Log full error details for debugging
                logger.error(
                    f"❌ EXTERNAL API ERROR: {response.status_code} {url}, "
                    f"error_data={error_data}, duration={request_duration:.3f}s, "
                    f"correlation_id={correlation_id}"
                )
            except Exception:
                error_message = response.text or f"HTTP {response.status_code} error"
                logger.error(
                    f"❌ EXTERNAL API ERROR (non-JSON): {response.status_code} {url}, "
                    f"response_text={response.text[:500]}, duration={request_duration:.3f}s, "
                    f"correlation_id={correlation_id}"
                )
            
            # Raise a more descriptive exception
            raise ValueError(
                f"Server error '{response.status_code} {response.reason_phrase}' for url '{url}': {error_message}\n"
                f"For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/{response.status_code}"
            )
        
        # Handle streaming responses (Server-Sent Events)
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type or "text/plain" in content_type:
            # For streaming responses, read the stream and parse SSE events
            events = []
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event_type = line[7:].strip()
                elif line.startswith("data: "):
                    event_data = line[6:].strip()
                    try:
                        parsed_data = json.loads(event_data)
                        events.append({
                            "event": event_type,
                            "data": parsed_data
                        })
                    except json.JSONDecodeError:
                        events.append({
                            "event": event_type,
                            "data": event_data
                        })
            return {"events": events}
        
        # Try to parse as JSON, fallback to text if it fails
        try:
            response_data = response.json()
        except (ValueError, json.JSONDecodeError):
            response_data = {"response_text": response.text}
        
        # Return both response data and MCP token (if created)
        return response_data, mcp_token_created

    async def make_stream_request(
        self,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        user_id: Optional[str] = None,
        authorization_header: Optional[str] = None,
        user_access_token: Optional[str] = None,
        obo_token: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Stream a POST request to AGNO API and yield raw SSE lines as received.

        Yields raw text lines exactly as sent by the external API so the caller
        can forward them verbatim to the frontend (Server-Sent Events proxy).
        """
        base_url_clean = self.base_url.rstrip("/")
        endpoint_clean = endpoint.lstrip("/")
        full_url = f"{base_url_clean}/{endpoint_clean}"
        correlation_id = get_correlation_id() or "stream-request"

        # Build auth headers (same logic as _make_http_request)
        default_headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": f"AIQ-Backend/{settings.app_version}",
            "Accept": "text/event-stream",
        }
        if correlation_id:
            default_headers["X-Correlation-ID"] = correlation_id

        # Resolve OBO token
        final_obo_token = obo_token
        if not final_obo_token and user_access_token:
            try:
                final_obo_token = await exchange_token_obo(user_access_token)
            except Exception as exc:
                logger.warning(f"[stream] OBO exchange failed: {exc}")
                final_obo_token = None

        # Build authorization header — mirrors _make_http_request logic exactly
        if final_obo_token and user_access_token:
            # Best case: MCP token (OBO embedded)
            try:
                effective_aria_token = None
                try:
                    from aiq_backend.auth.obo_utils import exchange_token_aria
                    effective_aria_token = await exchange_token_aria(user_access_token)
                except Exception:
                    pass
                mcp_token = create_mcp_token(user_access_token, final_obo_token, effective_aria_token)
                default_headers["Authorization"] = f"Bearer {mcp_token}"
                logger.info("[stream] ✓ Using MCP token in Authorization header")
            except Exception as exc:
                logger.warning(f"[stream] MCP token creation failed: {exc}")
                if authorization_header:
                    default_headers["Authorization"] = authorization_header
        elif authorization_header:
            jwt_candidate = authorization_header[7:] if authorization_header.startswith("Bearer ") else authorization_header
            try:
                # Try to extract OBO token from MCP JWT (if caller already built one)
                from aiq_backend.auth.obo_utils import extract_obo_from_mcp_jwt
                obo_from_mcp = extract_obo_from_mcp_jwt(jwt_candidate, verify_signature=False)
                default_headers["Authorization"] = f"Bearer {obo_from_mcp}"
                logger.info("[stream] ✓ Extracted OBO from MCP JWT")
            except Exception:
                # Fallback: create custom HS256 JWT from Azure AD token — same as _make_http_request
                if user_access_token:
                    try:
                        from aiq_backend.auth.obo_utils import decode_token_without_verification
                        decoded_azure = decode_token_without_verification(user_access_token)
                        user_sub = decoded_azure.get("sub") or decoded_azure.get("oid")
                        user_email = (
                            decoded_azure.get("email")
                            or decoded_azure.get("preferred_username")
                            or decoded_azure.get("upn")
                            or "unknown@example.com"
                        )
                        custom_jwt_payload = {
                            "sub": user_sub,
                            "email": user_email,
                            "exp": decoded_azure.get("exp") or (int(time.time()) + 3600),
                            "iat": decoded_azure.get("iat") or int(time.time()),
                            "iss": "aiq-backend",
                        }
                        if final_obo_token:
                            custom_jwt_payload["mcp_token"] = final_obo_token
                        custom_jwt = jwt.encode(
                            custom_jwt_payload,
                            settings.jwt_secret_key,
                            algorithm=settings.jwt_algorithm,
                        )
                        default_headers["Authorization"] = f"Bearer {custom_jwt}"
                        logger.info("[stream] ✓ Created custom HS256 JWT from Azure AD token")
                    except Exception as jwt_exc:
                        logger.warning(f"[stream] Custom JWT creation failed: {jwt_exc}, using original header")
                        default_headers["Authorization"] = authorization_header
                else:
                    default_headers["Authorization"] = authorization_header
        elif settings.agno_api_key:
            default_headers["Authorization"] = f"Bearer {settings.agno_api_key}"

        if headers:
            default_headers.update(headers)

        if data is None:
            data = {}

        logger.info(f"[stream] Starting SSE proxy: POST {full_url}")

        # Use a fresh client with streaming timeout (no read timeout limit)
        stream_timeout = httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0)
        async with httpx.AsyncClient(timeout=stream_timeout, http2=True, verify=True) as client:
            async with client.stream("POST", full_url, json=data, headers=default_headers) as response:
                if response.status_code >= 400:
                    error_text = await response.aread()
                    raise ValueError(
                        f"External API error {response.status_code} for {full_url}: {error_text.decode()[:500]}"
                    )
                async for line in response.aiter_lines():
                    yield line

    async def get_cached_response(
        self, 
        cache_key: str, 
        user_id: Optional[str], 
        correlation_id: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Get cached response from in-memory cache."""
        # Clean up expired entries periodically
        _cleanup_expired_cache()
        
        if cache_key in _cache:
            entry = _cache[cache_key]
            current_time = time.time()
            
            # Check if entry is still valid
            if entry.get('expires_at', 0) > current_time:
                logger.debug(
                    f"Cache hit for key: {cache_key}, "
                    f"correlation_id={correlation_id}"
                )
                return entry.get('data')
            else:
                # Remove expired entry
                del _cache[cache_key]
        
        logger.debug(
            f"Cache miss for key: {cache_key}, "
            f"correlation_id={correlation_id}"
        )
        return None

    async def save_to_cache(
        self,
        cache_key: str,
        response_data: Dict[str, Any],
        endpoint: str,
        ttl: int,
        user_id: Optional[str],
        correlation_id: Optional[str]
    ) -> None:
        """Save response to in-memory cache."""
        current_time = time.time()
        expires_at = current_time + ttl
        
        _cache[cache_key] = {
            'data': response_data,
            'expires_at': expires_at,
            'created_at': current_time,
            'api_type': AGNOAPIType.AGNO_MULTIAGENT.value,
            'endpoint': endpoint,
            'user_id': user_id,
            'correlation_id': correlation_id
        }
        
        logger.debug(
            f"Cached response for key: {cache_key}, "
            f"ttl={ttl}s, expires_at={expires_at}, correlation_id={correlation_id}"
        )

    def _generate_cache_key(
        self,
        endpoint: str,
        method: str,
        data: Optional[Dict[str, Any]]
    ) -> str:
        """Generate cache key for request."""
        # Create deterministic cache key
        key_parts = [AGNOAPIType.AGNO_MULTIAGENT.value, endpoint, method.upper()]
        
        if data:
            # Sort data keys for consistent cache keys
            sorted_data = json.dumps(data, sort_keys=True)
            key_parts.append(sorted_data)
        
        return f"agno_api:{':'.join(key_parts)}"

    async def clear_cache(
        self,
        endpoint: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> int:
        """Clear cache entries from in-memory cache."""
        cleared_count = 0
        keys_to_remove = []
        
        for key, entry in _cache.items():
            should_remove = False
            
            if endpoint and entry.get('endpoint') == endpoint:
                should_remove = True
            elif user_id and entry.get('user_id') == user_id:
                should_remove = True
            elif not endpoint and not user_id:
                # Clear all if no filters specified
                should_remove = True
            
            if should_remove:
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del _cache[key]
            cleared_count += 1
        
        logger.info(
            f"Cleared {cleared_count} cache entries: endpoint={endpoint}, user_id={user_id}"
        )
        return cleared_count

    async def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics from in-memory cache."""
        # Clean up expired entries first
        _cleanup_expired_cache()
        
        current_time = time.time()
        valid_entries = 0
        expired_entries = 0
        
        for entry in _cache.values():
            if entry.get('expires_at', 0) > current_time:
                valid_entries += 1
            else:
                expired_entries += 1
        
        return {
            "valid_entries": valid_entries,
            "expired_entries": expired_entries,
            "total_entries": len(_cache),
            "cache_type": "in_memory",
            "max_size": "unlimited"  # Could be made configurable
        }


class AGNOService:
    """Comprehensive service for AGNO Multiagent API integration with all endpoints."""
    
    def __init__(self):
        self.api_service = AGNOAPIService()

    # Core endpoints
    async def get_config(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get OS Configuration."""
        return await self.api_service.make_request(
            endpoint="/config",
            method="GET",
            user_id=user_id
        )

    async def get_models(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get Available Models."""
        return await self.api_service.make_request(
            endpoint="/models",
            method="GET",
            user_id=user_id
        )

    # Agent endpoints
    async def create_agent_run(
        self,
        agent_id: str, 
        data: Dict[str, Any], 
        user_id: Optional[str] = None,
        use_multipart: bool = False,
        files: Optional[List] = None,
        authorization_header: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create Agent Run."""
        content_type = "multipart/form-data" if use_multipart else "application/json"
        return await self.api_service.make_request(
            endpoint=f"/agents/{agent_id}/runs",
            method="POST",
            data=data,
            user_id=user_id,
            content_type=content_type,
            files=files,
            authorization_header=authorization_header
        )

    async def continue_agent_run(
        self,
        agent_id: str,
        run_id: str,
        data: Dict[str, Any],
        user_id: Optional[str] = None,
        use_multipart: bool = False,
        files: Optional[List] = None
    ) -> Dict[str, Any]:
        """Continue Agent Run."""
        # Continue agent run uses application/x-www-form-urlencoded, not JSON or multipart
        content_type = "application/x-www-form-urlencoded"
        return await self.api_service.make_request(
            endpoint=f"/agents/{agent_id}/runs/{run_id}/continue",
            method="POST",
            data=data,
            user_id=user_id,
            content_type=content_type,
            files=files
        )

    async def get_agents(
        self, 
        user_id: Optional[str] = None,
        user_access_token: Optional[str] = None,
        authorization_header: Optional[str] = None
    ) -> Dict[str, Any]:
        """List All Agents."""
        return await self.api_service.make_request(
            endpoint="/agents",
            method="GET",
            user_id=user_id,
            user_access_token=user_access_token,
            authorization_header=authorization_header
        )

    async def get_agent_details(self, agent_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get Agent Details."""
        return await self.api_service.make_request(
            endpoint=f"/agents/{agent_id}",
            method="GET",
            user_id=user_id
        )

    # Team endpoints
    async def create_team_run(
        self, 
        team_id: str, 
        data: Dict[str, Any], 
        user_id: Optional[str] = None,
        use_multipart: bool = False,
        files: Optional[List] = None,
        authorization_header: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create Team Run."""
        content_type = "multipart/form-data" if use_multipart else "application/json"
        return await self.api_service.make_request(
            endpoint=f"/teams/{team_id}/runs",
            method="POST",
            data=data,
            user_id=user_id,
            content_type=content_type,
            files=files,
            authorization_header=authorization_header
        )

    async def cancel_team_run(
        self,
        team_id: str,
        run_id: str,
        user_id: Optional[str] = None,
        authorization_header: Optional[str] = None,
        user_access_token: Optional[str] = None,
        obo_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """Cancel Team Run (distributed cancellation).
        
        Calls POST /teams/runs/cancel with request body.
        
        Args:
            team_id: Team ID
            run_id: Run ID to cancel
            user_id: User ID for tracking
            authorization_header: Optional authorization header (JWT token) to forward
            user_access_token: Optional Azure AD user access token for OBO exchange
            obo_token: Optional OBO token (if not provided, will be exchanged from user_access_token)
            
        Returns:
            API response data
        """
        # Log the request details
        logger.info(
            f" AGNO API /teams/runs/cancel REQUEST:"
        )
        logger.info(f"   Endpoint: {self.api_service.base_url}/teams/runs/cancel")
        logger.info(f"   Method: POST")
        logger.info(f"   team_id: {team_id}")
        logger.info(f"   run_id: {run_id}")
        logger.info(f"   user_id: {user_id}")
        logger.info(f"   authorization_header: {'Present' if authorization_header else 'None'}")
        logger.info(f"   user_access_token: {'Present' if user_access_token else 'None'}")
        logger.info(f"   obo_token: {'Present' if obo_token else 'None'}")
        
        data = {
            "team_id": team_id,
            "run_id": run_id
        }
        return await self.api_service.make_request(
            endpoint="/teams/runs/cancel",
            method="POST",
            data=data,
            user_id=user_id,
            content_type="application/json",
            authorization_header=authorization_header,
            user_access_token=user_access_token,
            obo_token=obo_token,
            return_mcp_token=False  # Return MCP token in response
        )


    # Agent Run endpoints
    async def cancel_agent_run(
        self,
        agent_id: str,
        run_id: str,
        user_id: Optional[str] = None,
        authorization_header: Optional[str] = None,
        user_access_token: Optional[str] = None,
        obo_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """Cancel Agent Run (distributed cancellation).
        
        Calls POST /agents/runs/cancel with request body.
        
        Args:
            agent_id: Agent ID
            run_id: Run ID to cancel
            user_id: User ID for tracking
            authorization_header: Optional authorization header (JWT token) to forward
            user_access_token: Optional Azure AD user access token for OBO exchange
            obo_token: Optional OBO token (if not provided, will be exchanged from user_access_token)
            
        Returns:
            API response data
        """
        # Log the request details
        logger.info(
            f" AGNO API /agents/runs/cancel REQUEST:"
        )
        logger.info(f"   Endpoint: {self.api_service.base_url}/agents/runs/cancel")
        logger.info(f"   Method: POST")
        logger.info(f"   agent_id: {agent_id}")
        logger.info(f"   run_id: {run_id}")
        logger.info(f"   user_id: {user_id}")
        logger.info(f"   authorization_header: {'Present' if authorization_header else 'None'}")
        logger.info(f"   user_access_token: {'Present' if user_access_token else 'None'}")
        logger.info(f"   obo_token: {'Present' if obo_token else 'None'}")
        
        data = {
            "agent_id": agent_id,
            "run_id": run_id
        }
        return await self.api_service.make_request(
            endpoint="/agents/runs/cancel",
            method="POST",
            data=data,
            user_id=user_id,
            content_type="application/json",
            authorization_header=authorization_header,
            user_access_token=user_access_token,
            obo_token=obo_token,
            return_mcp_token=False  # Return MCP token in response
        )

    async def get_teams(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """List All Teams."""
        return await self.api_service.make_request(
            endpoint="/teams",
            method="GET",
            user_id=user_id
        )

    async def get_team_details(self, team_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get Team Details."""
        return await self.api_service.make_request(
            endpoint=f"/teams/{team_id}",
            method="GET",
            user_id=user_id
        )

    # Query-agent endpoint
    async def query_agent(
        self,
        agent_name: str,
        query: str,
        stream_id: str,
        session_id: str,
        agent_id: str,
        user_id: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        custom_fields: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
        stream_config: Optional[Dict[str, Any]] = None,
        authorization_header: Optional[str] = None,
        user_access_token: Optional[str] = None,
        obo_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """Query agent using the /query-agent endpoint.
        
        Args:
            agent_name: Name of the agent to query
            query: User query/message
            stream_id: Stream ID for tracking
            session_id: Session ID
            agent_id: Agent ID (required by external DAT API)
            user_id: User ID
            attachments: Optional list of attachment objects with attachment_uuid, download_url, filename, content_type
            custom_fields: Optional custom fields
            user_context: Optional user context
            stream_config: Optional stream configuration
            authorization_header: Optional authorization header (JWT token) to forward
            user_access_token: Optional Azure AD user access token for OBO exchange
            
        Returns:
            API response data
        """
        data = {
            "agent_name": agent_name,
            "agent_id": agent_id,
            "query": query,
            "stream_id": stream_id,
            "session_id": session_id,
        }
        
        if attachments:
            data["attachments"] = attachments
        if custom_fields:
            data["custom_fields"] = custom_fields
        if user_context:
            data["user_context"] = user_context
        if stream_config:
            data["stream_config"] = stream_config
        
        # Log the payload being sent to external AGNO API
        import json
        logger.info(f"📤 AGNO API /query-agent REQUEST PAYLOAD:")
        logger.info(f"   Endpoint: {self.api_service.base_url}/query-agent")
        logger.info(f"   Method: POST")
        logger.info(f"   Payload: {json.dumps(data, indent=2, default=str)}")
        logger.info(f"   user_id: {user_id}")
        logger.info(f"   authorization_header: {'Present' if authorization_header else 'None'}")
        logger.info(f"   user_access_token: {'Present' if user_access_token else 'None'}")
        logger.info(f"   obo_token: {'Present' if obo_token else 'None'}")
        
        return await self.api_service.make_request(
            endpoint="/query-agent",
            method="POST",
            data=data,
            user_id=user_id,
            content_type="application/json",
            authorization_header=authorization_header,
            user_access_token=user_access_token,
            obo_token=obo_token,
            return_mcp_token=True  # Return MCP token in response
        )

    async def query_team(
        self,
        message: str,
        stream_id: str,
        session_id: str,
        user_id: str,
        team_id: Optional[str] = None,
        db: Optional[AsyncSession] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        custom_fields: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
        stream_config: Optional[Dict[str, Any]] = None,
        authorization_header: Optional[str] = None,
        user_access_token: Optional[str] = None,
        obo_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """Query team using the /query-team endpoint.
        
        Args:
            message: User message/query
            stream_id: Stream ID for tracking
            session_id: Session ID
            user_id: User ID
            agent_id: Agent ID (if not provided, will use Super Agent's public_id)
            db: Database session (required to fetch Super Agent if agent_id not provided)
            attachments: Optional list of attachment objects with attachment_uuid, download_url, filename, content_type
            custom_fields: Optional custom fields
            user_context: Optional user context
            stream_config: Optional stream configuration
            authorization_header: Optional authorization header (JWT token) to forward
            user_access_token: Optional Azure AD user access token for OBO exchange
            
        Returns:
            API response data
        """
        # If agent_id (team_id) not provided, fetch Super Agent's public_id
        if not team_id:
            if not db:
                raise ValueError("Database session (db) is required when agent_id is not provided")
            
            from aiq_backend.models.menu import Agent
            
            # Fetch Super Agent
            result = await db.execute(select(Agent).where(Agent.name == "Super Agent"))
            super_agent = result.scalar_one_or_none()
            
            if not super_agent:
                logger.warning("Super Agent not found in database, creating default Super Agent")
                # Create default Super Agent if it doesn't exist
                super_agent = Agent(
                    name="Super Agent",
                    is_enabled=True
                )
                db.add(super_agent)
                await db.commit()
                await db.refresh(super_agent)
            
            team_id = str(super_agent.public_id)
            logger.info(f"✓ Using Super Agent's public_id for query-team: {team_id}")
        
        data = {
            "message": message,
            "team_id": team_id,
            "stream_id": stream_id,
            "session_id": session_id,
        }
        
        if attachments:
            data["attachments"] = attachments
        if custom_fields:
            data["custom_fields"] = custom_fields
        if user_context:
            data["user_context"] = user_context
        if stream_config:
            data["stream_config"] = stream_config
        
        # Log the payload being sent to external AGNO API
        import json
        logger.info(f"📤 AGNO API /query-team REQUEST PAYLOAD:")
        logger.info(f"   Endpoint: {self.api_service.base_url}/query-team")
        logger.info(f"   Method: POST")
        logger.info(f"   Payload: {json.dumps(data, indent=2, default=str)}")
        logger.info(f"   user_id: {user_id}")
        logger.info(f"   authorization_header: {'Present' if authorization_header else 'None'}")
        logger.info(f"   user_access_token: {'Present' if user_access_token else 'None'}")
        logger.info(f"   obo_token: {'Present' if obo_token else 'None'}")
        
        return await self.api_service.make_request(
            endpoint="/query-team",
            method="POST",
            data=data,
            user_id=user_id,
            content_type="application/json",
            authorization_header=authorization_header,
            user_access_token=user_access_token,
            obo_token=obo_token,
            return_mcp_token=True  # Return MCP token in response
        )

    async def query_agent_stream(
        self,
        agent_name: str,
        query: str,
        stream_id: str,
        session_id: str,
        agent_id: str,
        user_id: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        custom_fields: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
        authorization_header: Optional[str] = None,
        user_access_token: Optional[str] = None,
        obo_token: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Stream /query-agent response as raw SSE lines."""
        data: Dict[str, Any] = {
            "agent_name": agent_name,
            "agent_id": agent_id,
            "query": query,
            "stream_id": stream_id,
            "session_id": session_id,
            "stream_config": {"stream": True},
        }
        if attachments:
            data["attachments"] = attachments
        if custom_fields:
            data["custom_fields"] = custom_fields
        if user_context:
            data["user_context"] = user_context

        async for line in self.api_service.make_stream_request(
            endpoint="/query-agent",
            data=data,
            user_id=user_id,
            authorization_header=authorization_header,
            user_access_token=user_access_token,
            obo_token=obo_token,
        ):
            yield line

    async def query_team_stream(
        self,
        message: str,
        stream_id: str,
        session_id: str,
        user_id: str,
        team_id: Optional[str] = None,
        db: Optional[AsyncSession] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        custom_fields: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
        authorization_header: Optional[str] = None,
        user_access_token: Optional[str] = None,
        obo_token: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Stream /query-team response as raw SSE lines."""
        if not team_id:
            if not db:
                raise ValueError("Database session (db) is required when team_id is not provided")
            from aiq_backend.models.menu import Agent
            result = await db.execute(select(Agent).where(Agent.name == "Super Agent"))
            super_agent = result.scalar_one_or_none()
            if not super_agent:
                super_agent = Agent(name="Super Agent", is_enabled=True)
                db.add(super_agent)
                await db.commit()
                await db.refresh(super_agent)
            team_id = str(super_agent.public_id)

        data: Dict[str, Any] = {
            "message": message,
            "team_id": team_id,
            "stream_id": stream_id,
            "session_id": session_id,
            "stream_config": {"stream": True},
        }
        if attachments:
            data["attachments"] = attachments
        if custom_fields:
            data["custom_fields"] = custom_fields
        if user_context:
            data["user_context"] = user_context

        async for line in self.api_service.make_stream_request(
            endpoint="/query-team",
            data=data,
            user_id=user_id,
            authorization_header=authorization_header,
            user_access_token=user_access_token,
            obo_token=obo_token,
        ):
            yield line

    # Workflow endpoints
    async def get_workflows(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """List All Workflows."""
        return await self.api_service.make_request(
            endpoint="/workflows",
            method="GET",
            user_id=user_id
        )

    async def get_workflow_details(self, workflow_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get Workflow Details."""
        return await self.api_service.make_request(
            endpoint=f"/workflows/{workflow_id}",
            method="GET",
            user_id=user_id
        )

    async def execute_workflow(
        self, 
        workflow_id: str, 
        data: Dict[str, Any], 
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute Workflow."""
        return await self.api_service.make_request(
            endpoint=f"/workflows/{workflow_id}/runs",
            method="POST",
            data=data,
            user_id=user_id
        )

    async def cancel_workflow_run(
        self,
        workflow_id: str,
        run_id: str,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Cancel Workflow Run."""
        return await self.api_service.make_request(
            endpoint=f"/workflows/{workflow_id}/runs/{run_id}/cancel",
            method="POST",
            user_id=user_id
        )

    # My Agents and Teams endpoints
    async def get_my_agents(
        self,
        user_id: Optional[str] = None,
        user_access_token: Optional[str] = None,
        authorization_header: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get My Accessible Agents."""
        return await self.api_service.make_request(
            endpoint="/my-agents",
            method="GET",
            user_id=user_id,
            user_access_token=user_access_token,
            authorization_header=authorization_header
        )

    async def get_my_teams(
        self,
        user_id: Optional[str] = None,
        user_access_token: Optional[str] = None,
        authorization_header: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get My Teams."""
        return await self.api_service.make_request(
            endpoint="/my-teams",
            method="GET",
            user_id=user_id,
            user_access_token=user_access_token,
            authorization_header=authorization_header
        )

    # Admin MCP endpoints
    async def validate_mcp_server(
        self,
        data: Dict[str, Any],
        user_id: Optional[str] = None,
        user_access_token: Optional[str] = None,
        authorization_header: Optional[str] = None
    ) -> Dict[str, Any]:
        """Validate MCP Server."""
        return await self.api_service.make_request(
            endpoint="/admin/mcp/validate",
            method="POST",
            data=data,
            user_id=user_id,
            user_access_token=user_access_token,
            authorization_header=authorization_header
        )

    async def add_mcp_agent(
        self,
        data: Dict[str, Any],
        user_id: Optional[str] = None,
        user_access_token: Optional[str] = None,
        authorization_header: Optional[str] = None
    ) -> Dict[str, Any]:
        """Add MCP Agent To System."""
        return await self.api_service.make_request(
            endpoint="/admin/mcp/add-mcp-agent",
            method="POST",
            data=data,
            user_id=user_id,
            user_access_token=user_access_token,
            authorization_header=authorization_header
        )

    # Health endpoint
    async def get_health(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Health Check."""
        return await self.api_service.make_request(
            endpoint="/health",
            method="GET",
            user_id=user_id
        )

    # Home endpoint
    async def get_api_info(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """API Information."""
        return await self.api_service.make_request(
            endpoint="/",
            method="GET",
            user_id=user_id
        )

    # Session endpoints
    async def get_sessions(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """List Sessions."""
        return await self.api_service.make_request(
            endpoint="/sessions",
            method="GET",
            user_id=user_id
        )

    async def delete_sessions(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Delete Multiple Sessions."""
        return await self.api_service.make_request(
            endpoint="/sessions",
            method="DELETE",
            user_id=user_id
        )

    async def get_session_by_id(self, session_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get Session by ID."""
        return await self.api_service.make_request(
            endpoint=f"/sessions/{session_id}",
            method="GET",
            user_id=user_id
        )

    async def delete_session(self, session_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Delete Session."""
        return await self.api_service.make_request(
            endpoint=f"/sessions/{session_id}",
            method="DELETE",
            user_id=user_id
        )

    async def get_session_runs(
        self, 
        session_id: str, 
        user_id: Optional[str] = None,
        authorization_header: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get Session Runs."""
        return await self.api_service.make_request(
            endpoint=f"/sessions/{session_id}/runs",
            method="GET",
            user_id=user_id,
            authorization_header=authorization_header
        )

    async def rename_session(
        self, 
        session_id: str, 
        data: Dict[str, Any], 
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Rename Session."""
        return await self.api_service.make_request(
            endpoint=f"/sessions/{session_id}/rename",
            method="POST",
            data=data,
            user_id=user_id
        )

    # Memory endpoints
    async def create_memory(
        self, 
        data: Dict[str, Any], 
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create Memory."""
        return await self.api_service.make_request(
            endpoint="/memories",
            method="POST",
            data=data,
            user_id=user_id
        )

    async def delete_memories(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Delete Multiple Memories."""
        return await self.api_service.make_request(
            endpoint="/memories",
            method="DELETE",
            user_id=user_id
        )

    async def get_memories(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """List Memories."""
        return await self.api_service.make_request(
            endpoint="/memories",
            method="GET",
            user_id=user_id
        )

    async def delete_memory(self, memory_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Delete Memory."""
        return await self.api_service.make_request(
            endpoint=f"/memories/{memory_id}",
            method="DELETE",
            user_id=user_id
        )

    async def get_memory_by_id(self, memory_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get Memory by ID."""
        return await self.api_service.make_request(
            endpoint=f"/memories/{memory_id}",
            method="GET",
            user_id=user_id
        )

    async def update_memory(
        self, 
        memory_id: str, 
        data: Dict[str, Any], 
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update Memory."""
        return await self.api_service.make_request(
            endpoint=f"/memories/{memory_id}",
            method="PATCH",
            data=data,
            user_id=user_id
        )

    async def get_memory_topics(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get Memory Topics."""
        return await self.api_service.make_request(
            endpoint="/memory_topics",
            method="GET",
            user_id=user_id
        )

    async def get_user_memory_stats(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get User Memory Statistics."""
        return await self.api_service.make_request(
            endpoint="/user_memory_stats",
            method="GET",
            user_id=user_id
        )

    # Evaluation endpoints
    async def get_eval_runs(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """List Evaluation Runs."""
        return await self.api_service.make_request(
            endpoint="/eval-runs",
            method="GET",
            user_id=user_id
        )

    async def delete_eval_runs(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Delete Evaluation Runs."""
        return await self.api_service.make_request(
            endpoint="/eval-runs",
            method="DELETE",
            user_id=user_id
        )

    async def execute_evaluation(
        self, 
        data: Dict[str, Any], 
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute Evaluation."""
        return await self.api_service.make_request(
            endpoint="/eval-runs",
            method="POST",
            data=data,
            user_id=user_id
        )

    async def get_eval_run(self, eval_run_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get Evaluation Run."""
        return await self.api_service.make_request(
            endpoint=f"/eval-runs/{eval_run_id}",
            method="GET",
            user_id=user_id
        )

    async def update_eval_run(
        self, 
        eval_run_id: str, 
        data: Dict[str, Any], 
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update Evaluation Run."""
        return await self.api_service.make_request(
            endpoint=f"/eval-runs/{eval_run_id}",
            method="PATCH",
            data=data,
            user_id=user_id
        )

    # Metrics endpoints
    async def get_metrics(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get AgentOS Metrics."""
        return await self.api_service.make_request(
            endpoint="/metrics",
            method="GET",
            user_id=user_id
        )

    async def refresh_metrics(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Refresh Metrics."""
        return await self.api_service.make_request(
            endpoint="/metrics/refresh",
            method="POST",
            user_id=user_id
        )

    # Knowledge endpoints
    async def upload_content(
        self, 
        data: Dict[str, Any], 
        user_id: Optional[str] = None,
        use_multipart: bool = False,
        files: Optional[List] = None
    ) -> Dict[str, Any]:
        """Upload Content."""
        content_type = "multipart/form-data" if use_multipart else "application/json"
        return await self.api_service.make_request(
            endpoint="/knowledge/content",
            method="POST",
            data=data,
            user_id=user_id,
            content_type=content_type,
            files=files
        )

    async def list_content(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """List Content."""
        return await self.api_service.make_request(
            endpoint="/knowledge/content",
            method="GET",
            user_id=user_id
        )

    async def delete_all_content(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Delete All Content."""
        return await self.api_service.make_request(
            endpoint="/knowledge/content",
            method="DELETE",
            user_id=user_id
        )

    async def update_content(
        self, 
        content_id: str, 
        data: Dict[str, Any], 
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update Content."""
        return await self.api_service.make_request(
            endpoint=f"/knowledge/content/{content_id}",
            method="PATCH",
            data=data,
            user_id=user_id
        )

    async def get_content_by_id(self, content_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get Content by ID."""
        return await self.api_service.make_request(
            endpoint=f"/knowledge/content/{content_id}",
            method="GET",
            user_id=user_id
        )

    async def delete_content_by_id(self, content_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Delete Content by ID."""
        return await self.api_service.make_request(
            endpoint=f"/knowledge/content/{content_id}",
            method="DELETE",
            user_id=user_id
        )

    async def get_content_status(self, content_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get Content Status."""
        return await self.api_service.make_request(
            endpoint=f"/knowledge/content/{content_id}/status",
            method="GET",
            user_id=user_id
        )

    async def get_knowledge_config(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get Knowledge Configuration."""
        return await self.api_service.make_request(
            endpoint="/knowledge/config",
            method="GET",
            user_id=user_id
        )


# Global service instances
agno_api_service = AGNOAPIService()
agno_service = AGNOService()
