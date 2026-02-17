"""Azure AD OAuth2 authentication with On-Behalf-Of (OBO) flow support."""

import json
import base64
from typing import Optional, Dict, Any

import httpx
from loguru import logger
from fastapi import HTTPException
import msal

from aldar_middleware.settings import settings
from urllib.parse import urlencode
from aldar_middleware.auth.obo_utils import exchange_token_obo, create_mcp_token


class AzureADOBOAuth:
    """Azure AD OAuth2 authentication service with OBO support."""

    def __init__(self):
        """Initialize Azure AD OBO auth."""
        self.tenant_id = settings.azure_tenant_id
        self.client_id = settings.azure_client_id
        self.client_secret = settings.azure_client_secret
        self.authority = settings.azure_authority or f"https://login.microsoftonline.com/{self.tenant_id}"

        # OBO target configuration
        self.target_client_id = settings.azure_obo_target_client_id

        # ✅ CRITICAL FIX: Request scope for YOUR app explicitly
        # Always include OpenID Connect scopes plus your app's /.default scope
        self.initial_scopes = [
            "openid",
            "profile",
            "offline_access",
            f"{self.client_id}/.default"
        ]

        # Step 2: Then use OBO to get token for target API
        self.target_scopes = [f"api://{self.target_client_id}/All"] if self.target_client_id else []

        # Create MSAL app instance
        self.msal_app = msal.ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=self.authority
        ) if self.client_id and self.client_secret else None

    def _decode_token_without_verification(self, token: str) -> Dict[str, Any]:
        """
        Decode JWT token without signature verification.

        Used for inspecting token claims like audience.
        """
        try:
            parts = token.split('.')
            if len(parts) != 3:
                raise ValueError("Invalid JWT token format")

            # Decode the payload (second part)
            payload = parts[1]
            # Add padding if needed
            payload += '=' * (4 - len(payload) % 4)
            decoded_bytes = base64.urlsafe_b64decode(payload)

            return json.loads(decoded_bytes)
        except Exception as e:
            logger.error(f"Error decoding token: {e}")
            return {}

    def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        """
        Build Azure AD authorization URL manually so we can combine OIDC scopes
        with our application's /.default scope without MSAL restrictions.
        """
        logger.info("🔐 Generating authorization URL")
        scope_string = " ".join(self.initial_scopes)
        logger.info(f"   Requesting scope for YOUR app: {scope_string}")

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": scope_string,
            "state": state
        }

        auth_url = f"{self.authority}/oauth2/v2.0/authorize?{urlencode(params)}"
        logger.info("✓ Authorization URL generated")
        return auth_url

    async def get_access_token(self, code: str, redirect_uri: str, code_verifier: Optional[str] = None) -> Dict[str, Any]:
        """
        Exchange authorization code for access token using a direct HTTP call.

        Matches the working sample while enforcing correct scopes for OBO.
        """
        try:
            scope_string = " ".join(self.initial_scopes)
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "scope": scope_string
            }

            if code_verifier:
                data["code_verifier"] = code_verifier

            logger.info("🔄 Exchanging authorization code for access token via token endpoint")
            logger.info(f"   Authority: {self.authority}")
            logger.info(f"   Scopes: {scope_string}")

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.authority}/oauth2/v2.0/token",
                    data=data
                )

            logger.info(f"   Token response status: {response.status_code}")
            logger.info(f"   Token response headers: {dict(response.headers)}")

            if response.status_code != 200:
                error_text = response.text
                logger.error(f"❌ Token exchange failed: {error_text}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Token exchange failed: {error_text}"
                )

            result = response.json()

            if "access_token" not in result:
                error_desc = result.get('error_description', 'Unknown error')
                logger.error(f"❌ Token exchange failed: {error_desc}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Token exchange failed: {error_desc}"
                )

            access_token = result['access_token']
            decoded = self._decode_token_without_verification(access_token)

            logger.info("✓ Token received successfully")
            logger.info(f"   Token audience: {decoded.get('aud')}")
            logger.info(f"   Expected audience: {self.client_id}")
            logger.info(f"   Token scope: {decoded.get('scp', decoded.get('roles', 'N/A'))}")

            if decoded.get('aud') not in [self.client_id, f"api://{self.client_id}"]:
                logger.warning("⚠️  Token audience unexpected!")
                logger.warning(f"   Got: {decoded.get('aud')}")
                logger.warning(f"   Expected: {self.client_id} or api://{self.client_id}")
            else:
                logger.info("✓ Token has correct audience for OBO flow")

            return result

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"❌ Error getting access token: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Token exchange error: {str(e)}"
            )

    async def exchange_token_obo(self, user_access_token: str) -> str:
        """
        Exchange user token for OBO token to call downstream API.

        Token must have audience = YOUR client app ID for this to work.
        """
        if not self.msal_app:
            raise HTTPException(
                status_code=500,
                detail="MSAL app not initialized. Check Azure AD configuration."
            )

        if not self.target_client_id:
            raise HTTPException(
                status_code=500,
                detail="Target client ID not configured for OBO flow."
            )

        try:
            logger.info("🔄 Starting OBO token exchange...")
            logger.info(f"   Source app: {self.client_id}")
            logger.info(f"   Target app: {self.target_client_id}")
            logger.info(f"   Target scope: {self.target_scopes}")

            # Verify incoming token audience before OBO
            decoded = self._decode_token_without_verification(user_access_token)
            logger.info(f"   📋 Incoming token details:")
            logger.info(f"      Audience (aud): {decoded.get('aud')}")
            logger.info(f"      Issuer (iss): {decoded.get('iss')}")
            logger.info(f"      App ID (appid): {decoded.get('appid')}")
            logger.info(f"      Scope (scp): {decoded.get('scp')}")
            logger.info(f"      Version (ver): {decoded.get('ver')}")
            logger.info(f"      Token use (token_use): {decoded.get('token_use', 'N/A')}")

            # Token must have audience = YOUR app ID (not target API)
            expected_audiences = [self.client_id, f"api://{self.client_id}"]
            actual_aud = decoded.get('aud')

            if actual_aud not in expected_audiences:
                logger.error(f"   ❌ Token audience mismatch!")
                logger.error(f"      Expected: {expected_audiences}")
                logger.error(f"      Got: {actual_aud}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Token has wrong audience for OBO. Got: {actual_aud}, Expected: {self.client_id} or api://{self.client_id}"
                )

            logger.info(f"   ✓ Token audience is correct for OBO")

            # Check token version - v1 vs v2 tokens behave differently
            token_version = decoded.get('ver', 'unknown')
            if token_version == '1.0':
                logger.warning(f"   ⚠️  Token is v1.0 - this may cause OBO issues")
                logger.warning(f"      Consider requesting v2.0 tokens")
            else:
                logger.info(f"   ✓ Token version: {token_version}")

            # Perform OBO token exchange to TARGET API
            logger.info(f"   🔄 Calling MSAL acquire_token_on_behalf_of...")
            result = self.msal_app.acquire_token_on_behalf_of(
                user_assertion=user_access_token,
                scopes=self.target_scopes  # Exchange for TARGET API token
            )

            if "access_token" in result:
                logger.info("✓ OBO token exchange successful")

                # Verify OBO token has correct audience
                obo_decoded = self._decode_token_without_verification(result["access_token"])
                logger.info(f"   📋 OBO token details:")
                logger.info(f"      Audience: {obo_decoded.get('aud')}")
                logger.info(f"      Scope: {obo_decoded.get('scp', obo_decoded.get('roles', 'N/A'))}")

                return result["access_token"]

            error_desc = result.get("error_description", "Unknown error")
            error_code = result.get("error", "Unknown")

            logger.error(f"❌ OBO exchange failed:")
            logger.error(f"   Error code: {error_code}")
            logger.error(f"   Description: {error_desc}")

            # Additional diagnostics
            if "correlation_id" in result:
                logger.error(f"   Correlation ID: {result['correlation_id']}")

            raise Exception(f"Failed OBO: {error_desc}")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"❌ Error in OBO token exchange: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"OBO exchange error: {str(e)}"
            )

    def _get_user_friendly_error(
        self,
        status_code: int,
        response_text: str,
        method: str,
        api_url: str
    ) -> str:
        """
        Generate user-friendly error messages based on API response.
        
        Args:
            status_code: HTTP status code from the API
            response_text: Raw response text from the API
            method: HTTP method used (GET, POST, DELETE, etc.)
            api_url: The API URL that was called
            
        Returns:
            User-friendly error message string
        """
        import json
        
        # Try to parse the response as JSON to extract message
        original_message = response_text
        try:
            response_json = json.loads(response_text)
            if isinstance(response_json, dict):
                original_message = response_json.get("message", response_text)
        except (json.JSONDecodeError, TypeError):
            pass
        
        # Determine the resource type from the URL
        resource_type = "resource"
        if "/agents/" in api_url or "/agent/" in api_url:
            resource_type = "agent"
        elif "/user/" in api_url:
            resource_type = "user data"
        elif "/conversation" in api_url:
            resource_type = "conversation"
        
        # Determine the action from the method
        action_map = {
            "GET": "access",
            "POST": "create",
            "PUT": "update",
            "PATCH": "update",
            "DELETE": "delete"
        }
        action = action_map.get(method.upper(), "perform this action on")
        
        # Generate user-friendly messages based on status code
        if status_code == 403:
            # Forbidden - permission denied
            if "Forbidden" in original_message:
                return f"You don't have permission to {action} this {resource_type}. This {resource_type} may belong to another user or you may not have the required access level."
            return f"Access denied: You don't have permission to {action} this {resource_type}."
        
        elif status_code == 401:
            return "Your session has expired. Please log in again to continue."
        
        elif status_code == 404:
            return f"The {resource_type} you're trying to {action} was not found. It may have been deleted or never existed."
        
        elif status_code == 400:
            return f"Invalid request: {original_message}"
        
        elif status_code == 409:
            return f"Conflict: The {resource_type} cannot be modified because it conflicts with an existing resource."
        
        elif status_code == 422:
            return f"Validation error: {original_message}"
        
        elif status_code == 429:
            return "Too many requests. Please wait a moment and try again."
        
        elif status_code >= 500:
            return f"Server error: The service is temporarily unavailable. Please try again later."
        
        # Default fallback
        return f"Unable to {action} {resource_type}: {original_message}"

    async def call_api_with_obo(
        self,
        user_access_token: str,
        api_url: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Call downstream API using custom MCP token format with OBO token embedded.

        This method:
        1. Exchanges user token for OBO token (with caching and auto-refresh)
        2. Creates custom JWT token with format:
           {
               "sub": "user_id",
               "email": "user@example.com",
               "exp": 1765392896,
               "iat": 1765356896,
               "iss": "aldar-middleware",
               "mcp_token": "OBO_token"
           }
        3. Sends custom token in Authorization header

        Args:
            user_access_token: User's access token (with correct audience)
            api_url: Full URL of the API to call
            method: HTTP method (GET, POST, etc.)
            data: Request body data (for POST/PUT)
            params: Query parameters

        Returns:
            API response as dictionary
        """
        try:
            # Step 1: Exchange for OBO token (with caching and auto-refresh)
            obo_token = await exchange_token_obo(user_access_token)

            # Step 2: Send OBO token directly to external API
            # The external API expects the OBO token directly, not embedded in MCP token
            logger.info(f"🌐 Calling API: {method} {api_url}")
            logger.info(f"   Authorization: Bearer {obo_token[:50]}...")
            logger.info(f"   OBO token length: {len(obo_token)} chars")
            logger.info(f"   Params: {params}")

            headers = {
                "Authorization": f"Bearer {obo_token}",
                "Content-Type": "application/json",
                "x-content-encoded": "true"
            }

            async with httpx.AsyncClient() as client:
                if method.upper() == "GET":
                    response = await client.get(api_url, headers=headers, params=params)
                elif method.upper() == "POST":
                    response = await client.post(api_url, headers=headers, json=data, params=params)
                elif method.upper() == "PUT":
                    response = await client.put(api_url, headers=headers, json=data, params=params)
                elif method.upper() == "PATCH":
                    response = await client.patch(api_url, headers=headers, json=data, params=params)
                elif method.upper() == "DELETE":
                    response = await client.delete(api_url, headers=headers, params=params)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                logger.info(f"   Response status: {response.status_code}")

                # Accept any 2xx status code as success (200, 201, 204, etc.)
                if not (200 <= response.status_code < 300):
                    logger.error(f"   API call failed: {response.text}")
                    
                    # Generate user-friendly error messages
                    error_detail = self._get_user_friendly_error(
                        status_code=response.status_code,
                        response_text=response.text,
                        method=method,
                        api_url=api_url
                    )
                    
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=error_detail
                    )

                logger.info("✓ API call successful")
                
                # Handle 204 No Content or empty responses
                if response.status_code == 204 or not response.text:
                    return {}
                return response.json()

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error calling API with OBO: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"API call error: {str(e)}"
            )

    def decode_id_token(self, id_token: str) -> Dict[str, Any]:
        """Decode ID token to get user information."""
        try:
            parts = id_token.split('.')
            if len(parts) != 3:
                raise ValueError("Invalid JWT token format")

            payload = parts[1]
            payload += '=' * (4 - len(payload) % 4)
            decoded = base64.urlsafe_b64decode(payload)

            user_info = json.loads(decoded)
            logger.info(f"ID token payload: {user_info}")
            return user_info

        except Exception as e:
            logger.error(f"Error decoding ID token: {e}")
            raise


# Global Azure AD OBO auth instance
azure_ad_obo_auth = AzureADOBOAuth()

