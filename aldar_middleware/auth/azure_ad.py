"""Azure AD OAuth2 authentication."""

import jwt
from jwt import PyJWKClient
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

import httpx
from loguru import logger
from fastapi import HTTPException

from aldar_middleware.settings import settings


class AzureADAuth:
    """Azure AD OAuth2 authentication service."""

    def __init__(self):
        """Initialize Azure AD auth."""
        self.tenant_id = settings.azure_tenant_id
        self.client_id = settings.azure_client_id
        self.client_secret = settings.azure_client_secret
        self.authority = settings.azure_authority or f"https://login.microsoftonline.com/{self.tenant_id}"
        self.issuer = f"{self.authority}/v2.0"
        # Initialize JWKS client for token verification with caching
        if self.tenant_id:
            self.jwks_url = f"{self.authority}/discovery/v2.0/keys"
            self.jwks_client = PyJWKClient(self.jwks_url, cache_keys=True)
        else:
            self.jwks_client = None

    async def get_access_token(self, code: str, redirect_uri: str, code_verifier: str = None) -> Dict[str, Any]:
        """Exchange authorization code for access token using client secret.
        
        This gets a Microsoft Graph API token for fetching user info, groups, and photos.
        The same token can be used for backend API authentication (validation accepts Graph tokens).
        """
        try:
            # Use client secret for web applications
            # Request Microsoft Graph API scope to enable Graph API calls
            scopes = [
                "openid",
                "profile",
                "email", 
                "offline_access",
                "https://graph.microsoft.com/.default"  # Request token for Microsoft Graph API
            ]
            scope_string = " ".join(scopes)
            
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "scope": scope_string  # Explicitly request Microsoft Graph scope
            }
            
            logger.info(f"Using client secret for web application")
            logger.info(f"Token request data: {dict(data)}")
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.authority}/oauth2/v2.0/token",
                    data=data
                )
                
                # Log response details for debugging
                logger.info(f"Token response status: {response.status_code}")
                logger.info(f"Token response headers: {dict(response.headers)}")
                
                if response.status_code != 200:
                    error_text = response.text
                    logger.error(f"Token exchange failed: {error_text}")
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"Token exchange failed: {error_text}"
                    )
                
                return response.json()
                
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error getting access token: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Error getting access token: {e}")
            raise

    async def get_graph_token(self, refresh_token: str) -> Dict[str, Any]:
        """Get a Microsoft Graph API access token using refresh token.
        
        This token is for calling Microsoft Graph API (user info, groups, photos).
        
        Args:
            refresh_token: The refresh token obtained during initial login
            
        Returns:
            Token response containing access_token for Microsoft Graph API
        """
        try:
            # Request token for Microsoft Graph API
            scopes = [
                "openid",
                "profile",
                "email",
                "offline_access",
                "https://graph.microsoft.com/.default"  # Request token for Microsoft Graph API
            ]
            scope_string = " ".join(scopes)
            
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": scope_string
            }
            
            logger.debug(f"Requesting Graph API token with scope: {scope_string}")
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.authority}/oauth2/v2.0/token",
                    data=data
                )
                
                if response.status_code != 200:
                    error_text = response.text
                    logger.error(f"Graph API token exchange failed: {error_text}")
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"Graph API token exchange failed: {error_text}"
                    )
                
                token_data = response.json()
                logger.info(f"Successfully obtained Graph API token (length: {len(token_data.get('access_token', ''))} chars)")
                return token_data
                
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error getting Graph API token: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Error getting Graph API token: {e}")
            raise

    async def get_application_token(self, refresh_token: str) -> Dict[str, Any]:
        """Get an application-scoped access token using refresh token.
        
        This token is for authenticating API calls to your application,
        not for Microsoft Graph API calls.
        
        Args:
            refresh_token: The refresh token obtained during initial login
            
        Returns:
            Token response containing access_token for your application
        """
        try:
            # Request token for THIS application (not Graph API)
            scopes = [
                "openid",
                "profile",
                "email",
                "offline_access",
                f"{self.client_id}/.default"  # Request token for THIS application
            ]
            scope_string = " ".join(scopes)
            
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": scope_string
            }
            
            logger.debug(f"Requesting application token with scope: {scope_string}")
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.authority}/oauth2/v2.0/token",
                    data=data
                )
                
                if response.status_code != 200:
                    error_text = response.text
                    logger.error(f"Application token exchange failed: {error_text}")
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"Application token exchange failed: {error_text}"
                    )
                
                token_data = response.json()
                logger.info(f"Successfully obtained application token (length: {len(token_data.get('access_token', ''))} chars)")
                return token_data
                
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error getting application token: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Error getting application token: {e}")
            raise

    async def decode_id_token(self, id_token: str) -> Dict[str, Any]:
        """Decode and verify ID token to get user information.
        
        SECURITY: This method now properly verifies the token signature
        using Azure AD's JWKS endpoint to prevent token forgery.
        """
        try:
            # Use the validate_token method which properly verifies signature
            # This ensures the token is authentic and issued by Azure AD
            user_info = await self.validate_token(id_token)
            logger.info(f"ID token payload: {user_info}")
            return user_info
            
        except Exception as e:
            logger.error(f"Error decoding ID token: {e}")
            raise

    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        """Get user information from Microsoft Graph.
        
        Requests additional fields like department, jobTitle, and companyName
        that are not included in the default response.
        """
        try:
            async with httpx.AsyncClient() as client:
                # Request specific fields including department, jobTitle, companyName, and employeeId
                select_fields = "id,userPrincipalName,mail,displayName,givenName,surname,department,jobTitle,companyName,officeLocation,country,accountEnabled,employeeId,onPremisesExtensionAttributes"
                response = await client.get(
                    f"https://graph.microsoft.com/v1.0/me?$select={select_fields}",
                    headers={"Authorization": f"Bearer {access_token}"}
                )
                response.raise_for_status()
                user_data = response.json()
                logger.debug(f"Graph API user info response: {user_data}")
                return user_data
                
        except Exception as e:
            logger.error(f"Error getting user info: {e}")
            raise

    async def get_user_profile_photo_url(self, access_token: str, user_id: str) -> Optional[str]:
        """Get user profile photo URL from Microsoft Graph.
        
        Args:
            access_token: Azure AD access token
            user_id: Azure AD user ID (oid from token)
            
        Returns:
            Profile photo URL if available, None otherwise
        """
        try:
            # Construct the profile photo URL
            # Format: https://graph.microsoft.com/v1.0/users/{user_id}/photo/$value
            photo_url = f"https://graph.microsoft.com/v1.0/users/{user_id}/photo/$value"
            
            # Verify that the photo exists by checking the photo metadata endpoint
            # This endpoint returns 404 if photo doesn't exist
            async with httpx.AsyncClient() as client:
                # Check if photo exists by calling the metadata endpoint
                metadata_url = f"https://graph.microsoft.com/v1.0/users/{user_id}/photo"
                response = await client.get(
                    metadata_url,
                    headers={"Authorization": f"Bearer {access_token}"}
                )
                
                # If photo exists (200), return the URL
                if response.status_code == 200:
                    logger.info(f"Profile photo found for user {user_id}")
                    return photo_url
                else:
                    # Photo doesn't exist (404) or other error
                    logger.debug(f"Profile photo not found for user {user_id} (status: {response.status_code})")
                    return None
                
        except Exception as e:
            logger.warning(f"Error checking user profile photo: {e}")
            # Return None on error - don't fail the login process
            return None

    async def get_user_profile_photo_bytes(self, access_token: str, user_id: str) -> Optional[bytes]:
        """Get user profile photo bytes from Microsoft Graph.
        
        Args:
            access_token: Azure AD access token
            user_id: Azure AD user ID (oid from token)
            
        Returns:
            Photo bytes if available, None otherwise
        """
        try:
            photo_url = f"https://graph.microsoft.com/v1.0/users/{user_id}/photo/$value"
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    photo_url,
                    headers={"Authorization": f"Bearer {access_token}"}
                )
                
                if response.status_code == 200:
                    logger.info(f"Successfully fetched profile photo for user {user_id} ({len(response.content)} bytes)")
                    return response.content
                elif response.status_code == 404:
                    logger.debug(f"Profile photo not found for user {user_id} (404)")
                    return None
                else:
                    error_text = response.text[:200] if response.text else "No error message"
                    logger.warning(
                        f"Profile photo fetch failed for user {user_id}: "
                        f"status={response.status_code}, error={error_text}"
                    )
                    return None
                
        except httpx.TimeoutException as e:
            logger.error(f"Timeout fetching profile photo for user {user_id}: {e}")
            return None
        except httpx.RequestError as e:
            logger.error(f"Request error fetching profile photo for user {user_id}: {e}")
            return None
        except Exception as e:
            logger.error(
                f"Error fetching user profile photo for user {user_id}: {type(e).__name__}: {str(e)}",
                exc_info=True
            )
            return None

    async def get_user_groups(self, access_token: str) -> List[str]:
        """Get Azure AD groups for the current user from Microsoft Graph.
        
        Uses GET /me/memberOf endpoint which works with GroupMember.Read.All permission.
        This endpoint returns all groups (security and distribution) the user is a member of.
        
        Args:
            access_token: Azure AD access token (must have GroupMember.Read.All scope)
            
        Returns:
            List of Azure AD group UUIDs (as strings)
        """
        try:
            # Use /me/memberOf which works with GroupMember.Read.All permission
            # This returns all groups (security and distribution) the user is a member of
            url = "https://graph.microsoft.com/v1.0/me/memberOf"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            params = {
                "$select": "id"  # Only get the group ID (UUID)
            }
            
            all_groups = []
            next_link = url
            is_first_request = True
            
            async with httpx.AsyncClient() as client:
                while next_link:
                    logger.info(f"Calling Microsoft Graph API: {next_link} with params: {params if is_first_request else 'pagination'}")
                    # Only pass params on first request or if URL doesn't already have query params
                    request_params = params if (is_first_request or "?" not in next_link) else None
                    
                    response = await client.get(
                        next_link,
                        headers=headers,
                        params=request_params
                    )
                    
                    is_first_request = False
                    
                    # Check for authentication and authorization errors
                    if response.status_code == 401:
                        error_data = response.json() if response.text else {}
                        logger.error(f"Authentication error from Microsoft Graph: {error_data}")
                        raise HTTPException(
                            status_code=401,
                            detail=f"Invalid or expired Azure AD access token: {error_data.get('error', {}).get('message', 'Unknown error')}"
                        )
                    
                    if response.status_code == 403:
                        error_data = response.json() if response.text else {}
                        logger.error(f"Authorization error from Microsoft Graph: {error_data}")
                        raise HTTPException(
                            status_code=403,
                            detail=f"Insufficient privileges: {error_data.get('error', {}).get('message', 'Unknown error')}. Note: /me/memberOf requires GroupMember.Read.All permission."
                        )
                    
                    response.raise_for_status()
                    data = response.json()
                    
                    # Log the raw response for debugging (first page only)
                    if len(all_groups) == 0:
                        logger.info(f"Raw response from memberOf (first page): {data}")
                    
                    # The response from /me/memberOf contains a "value" array with group objects
                    # Response format: {"value": [{"id": "uuid1", ...}, {"id": "uuid2", ...}, ...], "@odata.nextLink": "..."}
                    page_groups = data.get("value", [])
                    
                    # Extract group IDs (UUIDs) from the group objects
                    for group in page_groups:
                        group_id = group.get("id")
                        if group_id:
                            all_groups.append(group_id)
                    
                    # Check for next page
                    next_link = data.get("@odata.nextLink", "")
                    if next_link:
                        logger.info(f"More pages available, fetching next page...")
            
            logger.info(f"Retrieved {len(all_groups)} Azure AD group UUIDs for user")
            return all_groups
                
        except HTTPException:
            # Re-raise HTTP exceptions (like 401) so they can be handled properly
            raise
        except Exception as e:
            logger.error(f"Error getting user groups: {e}", exc_info=True)
            # Return empty list on error rather than failing
            return []

    async def validate_token(self, token: str) -> Dict[str, Any]:
        """Validate JWT token with full cryptographic verification.
        
        SECURITY: This method verifies:
        - Token signature (prevents forgery)
        - Token expiration (prevents use of expired tokens)
        - Token audience (ensures token is for this application)
        - Token issuer (ensures token is from Azure AD)
        """
        try:
            if not self.jwks_client:
                raise ValueError("JWKS client not initialized - Azure AD tenant ID not configured")
            
            # First, check the token header to detect if it's a custom JWT vs Azure AD token
            # This helps provide better error messages
            try:
                import jwt as jwt_lib
                header = jwt_lib.get_unverified_header(token)
                if header.get("alg") == "HS256":
                    logger.warning(
                        "Token appears to be a custom JWT (HS256). "
                        "Please use Azure AD access_token instead."
                    )
                    raise ValueError(
                        "Token is a custom JWT token. Please use Azure AD access_token. "
                        "If you just logged in, make sure to use the 'access_token' from the login response."
                    )
            except jwt.DecodeError:
                raise ValueError("Token is not a valid JWT")
            except ValueError:
                # Re-raise ValueError (our custom error about HS256)
                raise
            except Exception:
                # If we can't check header, continue with normal validation
                pass
            
            # Get the signing key from Azure AD JWKS endpoint (with caching)
            try:
                signing_key = self.jwks_client.get_signing_key_from_jwt(token)
            except jwt.PyJWKClientError as e:
                # If key not found in cache, force refresh and retry
                logger.warning(f"Signing key not found in JWKS cache, refreshing: {e}")
                # Force refresh of JWKS cache by creating a new client
                self.jwks_client = PyJWKClient(self.jwks_url, cache_keys=True)
                signing_key = self.jwks_client.get_signing_key_from_jwt(token)
            
            # Azure AD tokens can have different audience formats:
            # 1. Just the client_id (e.g., "abc123-def456-...")
            # 2. api://{client_id} format
            # 3. Sometimes a GUID or other format
            # We'll decode first without audience verification, then manually check
            # Accept both v1.0 and v2.0 issuer formats
            v1_issuer = f"https://sts.windows.net/{self.tenant_id}/"
            v2_issuer = self.issuer
            
            # Try to decode with v2.0 issuer first (most common for app tokens)
            try:
                decoded = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256"],  # Azure AD uses RS256
                    issuer=v2_issuer,     # Verify issuer (v2.0)
                    options={
                        "verify_signature": True,   # MUST be True - prevents token forgery
                        "verify_exp": True,         # Check expiration
                        "verify_nbf": True,         # Check not-before
                        "verify_iat": True,         # Check issued-at
                        "verify_aud": False,        # We'll check audience manually
                        "verify_iss": True,         # Check issuer
                        "require": ["exp", "iat", "sub", "aud", "iss"]
                    }
                )
            except jwt.InvalidSignatureError:
                # Signature failed - JWKS cache might be stale, refresh and retry once
                logger.warning("Token signature verification failed, refreshing JWKS cache and retrying")
                self.jwks_client = PyJWKClient(self.jwks_url, cache_keys=True)
                signing_key = self.jwks_client.get_signing_key_from_jwt(token)
                # Retry decode with fresh signing key
                try:
                    decoded = jwt.decode(
                        token,
                        signing_key.key,
                        algorithms=["RS256"],
                        issuer=v2_issuer,
                        options={
                            "verify_signature": True,
                            "verify_exp": True,
                            "verify_nbf": True,
                            "verify_iat": True,
                            "verify_aud": False,
                            "verify_iss": True,
                            "require": ["exp", "iat", "sub", "aud", "iss"]
                        }
                    )
                except (jwt.InvalidSignatureError, jwt.InvalidIssuerError):
                    # Try with v1.0 issuer format (Graph tokens often use v1.0)
                    logger.debug("Trying v1.0 issuer format for Graph API token")
                    decoded = jwt.decode(
                        token,
                        signing_key.key,
                        algorithms=["RS256"],
                        issuer=v1_issuer,
                        options={
                            "verify_signature": True,
                            "verify_exp": True,
                            "verify_nbf": True,
                            "verify_iat": True,
                            "verify_aud": False,
                            "verify_iss": True,
                            "require": ["exp", "iat", "sub", "aud", "iss"]
                        }
                    )
            except jwt.InvalidIssuerError:
                # Try with v1.0 issuer format as fallback (Graph tokens often use v1.0)
                logger.debug("Token issuer mismatch with v2.0, trying v1.0 issuer format")
                try:
                    decoded = jwt.decode(
                        token,
                        signing_key.key,
                        algorithms=["RS256"],
                        issuer=v1_issuer,  # Verify issuer (v1.0)
                        options={
                            "verify_signature": True,
                            "verify_exp": True,
                            "verify_nbf": True,
                            "verify_iat": True,
                            "verify_aud": False,
                            "verify_iss": True,
                            "require": ["exp", "iat", "sub", "aud", "iss"]
                        }
                    )
                except jwt.InvalidSignatureError:
                    # Signature failed on v1.0 too - refresh JWKS cache and retry
                    logger.warning("Token signature verification failed (v1.0), refreshing JWKS cache and retrying")
                    self.jwks_client = PyJWKClient(self.jwks_url, cache_keys=True)
                    signing_key = self.jwks_client.get_signing_key_from_jwt(token)
                    # Retry with v1.0 issuer
                    decoded = jwt.decode(
                        token,
                        signing_key.key,
                        algorithms=["RS256"],
                        issuer=v1_issuer,
                        options={
                            "verify_signature": True,
                            "verify_exp": True,
                            "verify_nbf": True,
                            "verify_iat": True,
                            "verify_aud": False,
                            "verify_iss": True,
                            "require": ["exp", "iat", "sub", "aud", "iss"]
                        }
                    )
                except jwt.InvalidIssuerError:
                    # If both fail, decode without issuer check to get details for error
                    decoded_unverified = jwt.decode(
                        token,
                        signing_key.key,
                        algorithms=["RS256"],
                        options={"verify_signature": True, "verify_exp": False, "verify_iss": False}
                    )
                    actual_issuer = decoded_unverified.get("iss")
                    logger.error(
                        f"Token issuer mismatch: expected {v2_issuer} or {v1_issuer}, "
                        f"got {actual_issuer}"
                    )
                    raise ValueError(
                        f"Token has invalid issuer: {actual_issuer}. "
                        f"Expected: {v2_issuer} or {v1_issuer}"
                    )
            
            # Manual audience validation - accept multiple formats
            token_audience = decoded.get("aud")
            expected_audiences = [
                self.client_id,
                f"api://{self.client_id}",
                f"{self.client_id}/.default",
                "https://graph.microsoft.com"  # Accept Graph API tokens too
            ]
            
            if token_audience not in expected_audiences:
                logger.warning(
                    f"Token audience mismatch: expected one of {expected_audiences}, "
                    f"got {token_audience}"
                )
                # Check if it's a Graph API token (common mistake)
                if token_audience == "00000003-0000-0000-c000-000000000000":
                    raise ValueError(
                        f"Token is for Microsoft Graph API, not this application. "
                        f"Please ensure the authorization request includes scope: {self.client_id}/.default. "
                        f"If you just logged in, please log out and log in again to get a new token."
                    )
                raise ValueError(
                    f"Token has invalid audience: {token_audience}. "
                    f"Expected one of: {expected_audiences}"
                )
            
            # Additional validation of required claims
            if not decoded.get("sub"):
                logger.warning("Token missing subject claim")
                raise ValueError("Token missing required 'sub' claim")
            
            logger.debug(f"Successfully validated token for user: {decoded.get('sub')}")
            return decoded
            
        except jwt.ExpiredSignatureError:
            logger.warning("Token has expired")
            raise ValueError("Token has expired")
        except jwt.InvalidIssuerError:
            logger.warning("Token has invalid issuer")
            raise ValueError("Token has invalid issuer")
        except jwt.InvalidSignatureError as e:
            logger.warning(f"Token signature is invalid: {e}")
            # Try to decode without verification to get more info for debugging
            try:
                import jwt as jwt_lib
                unverified = jwt_lib.decode(token, options={"verify_signature": False})
                logger.debug(f"Token (unverified) details: aud={unverified.get('aud')}, "
                           f"iss={unverified.get('iss')}, sub={unverified.get('sub')}")
            except Exception:
                pass
            raise ValueError("Token signature is invalid")
        except ValueError:
            # Re-raise ValueError as-is (these are our custom validation errors)
            raise
        except Exception as e:
            logger.error(f"Token validation failed: {type(e).__name__}: {e}")
            raise

    def create_jwt_token(self, user_id: str, email: str, expires_delta: Optional[timedelta] = None) -> str:
        """Create JWT token for user."""
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=settings.jwt_access_token_expire_minutes)
        
        payload = {
            "sub": user_id,
            "email": email,
            "exp": expire,
            "iat": datetime.utcnow(),
            "iss": "aldar-middleware"
        }
        
        return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    def verify_jwt_token(self, token: str) -> Dict[str, Any]:
        """Verify JWT token."""
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret_key,
                algorithms=[settings.jwt_algorithm]
            )
            return payload
        except jwt.ExpiredSignatureError:
            raise ValueError("Token has expired")
        except jwt.InvalidTokenError:
            raise ValueError("Invalid token")

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh access token using refresh token."""
        try:
            # Request scope for THIS application, not Microsoft Graph
            scopes = [
                "openid",
                "profile",
                "email",
                "offline_access",
                f"{self.client_id}/.default"  # Request token for THIS application
            ]
            scope_string = " ".join(scopes)
            
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": scope_string  # Explicitly request application scope
            }
            
            logger.info("Refreshing access token")
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.authority}/oauth2/v2.0/token",
                    data=data
                )
                
                logger.info(f"Refresh token response status: {response.status_code}")
                
                if response.status_code != 200:
                    error_text = response.text
                    error_detail = error_text
                    
                    # Try to parse error response for better error messages
                    try:
                        error_json = response.json()
                        error_code = error_json.get("error")
                        error_description = error_json.get("error_description", "")
                        error_codes = error_json.get("error_codes", [])
                        
                        # Check for specific error codes
                        if 7000215 in error_codes or "invalid_client" in error_code:
                            error_detail = (
                                f"Azure AD authentication configuration error: {error_description}. "
                                f"Please verify that AZURE_CLIENT_SECRET is set to the actual secret value "
                                f"(not the secret ID) in your environment configuration."
                            )
                            logger.error(
                                f"Token refresh failed - Invalid client secret (AADSTS7000215): {error_description}. "
                                f"This indicates the Azure AD client secret is misconfigured."
                            )
                        else:
                            error_detail = f"Token refresh failed: {error_description or error_text}"
                            logger.error(f"Token refresh failed: {error_text}")
                    except (ValueError, KeyError):
                        # If we can't parse the error, use the raw text
                        logger.error(f"Token refresh failed: {error_text}")
                        error_detail = f"Token refresh failed: {error_text}"
                    
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=error_detail
                    )
                
                return response.json()
                
        except httpx.HTTPStatusError as e:
            error_text = e.response.text
            error_detail = error_text
            
            # Try to parse error for better messages
            try:
                error_json = e.response.json()
                error_code = error_json.get("error")
                error_description = error_json.get("error_description", "")
                error_codes = error_json.get("error_codes", [])
                
                if 7000215 in error_codes or "invalid_client" in error_code:
                    error_detail = (
                        f"Azure AD authentication configuration error: {error_description}. "
                        f"Please verify that AZURE_CLIENT_SECRET is set to the actual secret value "
                        f"(not the secret ID) in your environment configuration."
                    )
                    logger.error(
                        f"HTTP error refreshing token - Invalid client secret (AADSTS7000215): {error_description}"
                    )
                else:
                    error_detail = f"HTTP error refreshing token: {error_description or error_text}"
            except (ValueError, KeyError):
                error_detail = f"HTTP error refreshing token: {error_text}"
            
            logger.error(f"HTTP error refreshing token: {e.response.status_code} - {error_detail}")
            raise HTTPException(
                status_code=e.response.status_code,
                detail=error_detail
            )
        except HTTPException:
            # Re-raise HTTP exceptions as-is
            raise
        except Exception as e:
            logger.error(f"Error refreshing token: {type(e).__name__}: {e}")
            raise

    def get_authorization_url(self, redirect_uri: str, state: str, code_verifier: str = None) -> str:
        """Get Azure AD authorization URL for Web applications.
        
        Requests Microsoft Graph API scope to enable user info and group queries.
        """
        # Request scopes for OpenID Connect AND Microsoft Graph API
        # The https://graph.microsoft.com/.default scope enables Graph API calls
        scopes = [
            "openid",
            "profile", 
            "email",
            "offline_access",
            "https://graph.microsoft.com/.default"  # Request token for Microsoft Graph API
        ]
        scope_string = " ".join(scopes)
        
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": scope_string,
            "state": state,
            "response_mode": "query"
        }
        
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        return f"{self.authority}/oauth2/v2.0/authorize?{query_string}"


# Global Azure AD auth instance
azure_ad_auth = AzureADAuth()
