"""OBO (On-Behalf-Of) token exchange utilities with caching and auto-refresh."""

import json
import base64
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from threading import Lock

import httpx
from loguru import logger
from fastapi import HTTPException
import msal
import jwt

from aldar_middleware.settings import settings


class OBOTokenCache:
    """Redis-based cache for OBO tokens with expiration management and in-memory fallback."""

    def __init__(self, redis_client: Optional[Any] = None):
        """Initialize token cache with Redis (fallback to in-memory if Redis unavailable)."""
        self.redis = redis_client
        self.use_redis = redis_client is not None
        # In-memory fallback cache
        self._memory_cache: Dict[str, Dict[str, Any]] = {}
        self._lock = Lock()
        # Token expiration time: 12 hours (43200 seconds)
        self.token_expiration_hours = 12
        self.token_expiration_seconds = self.token_expiration_hours * 3600
        
        # Only log when Redis is available; initialization happens before Redis connection
        if self.use_redis:
            logger.info("✓ OBO token cache using Redis")


    def _get_cache_key(self, user_access_token: str) -> str:
        """Generate cache key from user access token."""
        # Use hash of token for better key distribution
        import hashlib
        token_hash = hashlib.sha256(user_access_token.encode()).hexdigest()[:32]
        return f"obo_token:{token_hash}"

    def _is_token_expired(self, token_data: Dict[str, Any]) -> bool:
        """Check if cached token is expired."""
        if "expires_at" not in token_data:
            return True

        expires_at = token_data["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)

        # Check if token expires within next 5 minutes (refresh buffer)
        buffer_time = timedelta(minutes=5)
        cache_expired = datetime.utcnow() + buffer_time >= expires_at
        
        # Also check actual OBO token expiration by decoding it
        obo_token = token_data.get("obo_token")
        if obo_token:
            try:
                decoded = decode_token_without_verification(obo_token)
                exp = decoded.get('exp')
                if exp:
                    current_time = time.time()
                    # Consider expired if within 5 minutes of expiration
                    actual_expired = current_time >= (exp - 300)
                    if actual_expired:
                        logger.info("⚠️  OBO token is actually expired (from token 'exp' claim)")
                        return True
            except Exception as e:
                logger.warning(f"Could not decode OBO token to check expiration: {e}")
                # If we can't decode, trust cache expiration
                return cache_expired
        
        return cache_expired

    async def get_token(self, user_access_token: str) -> Optional[str]:
        """Get cached OBO token if valid, otherwise return None."""
        cache_key = self._get_cache_key(user_access_token)
        
        # Try Redis first
        if self.use_redis:
            try:
                cached_data = await self.redis.get(cache_key)
                if cached_data:
                    token_data = json.loads(cached_data)
                    if not self._is_token_expired(token_data):
                        logger.info(f"✓ Using cached OBO token from Redis (expires at: {token_data.get('expires_at')})")
                        return token_data.get("obo_token")
                    else:
                        logger.info("⚠️  Cached OBO token expired, will refresh")
                        # Remove expired token from Redis
                        await self.redis.delete(cache_key)
            except Exception as e:
                logger.warning(f"Redis get error, falling back to memory: {e}")
        
        # Fallback to in-memory cache
        with self._lock:
            if cache_key in self._memory_cache:
                token_data = self._memory_cache[cache_key]
                if not self._is_token_expired(token_data):
                    logger.info(f"✓ Using cached OBO token from memory (expires at: {token_data.get('expires_at')})")
                    return token_data.get("obo_token")
                else:
                    logger.info("⚠️  Cached OBO token expired, will refresh")
                    del self._memory_cache[cache_key]
        
        return None

    async def set_token(self, user_access_token: str, obo_token: str, expires_in: Optional[int] = None):
        """Store OBO token in cache with expiration."""
        cache_key = self._get_cache_key(user_access_token)
        
        # Calculate expiration time
        if expires_in:
            expires_at = datetime.utcnow() + timedelta(seconds=min(expires_in, self.token_expiration_seconds))
            ttl_seconds = min(expires_in, self.token_expiration_seconds)
        else:
            # Default to 12 hours if not provided
            expires_at = datetime.utcnow() + timedelta(seconds=self.token_expiration_seconds)
            ttl_seconds = self.token_expiration_seconds

        token_data = {
            "obo_token": obo_token,
            "expires_at": expires_at.isoformat(),
            "cached_at": datetime.utcnow().isoformat()
        }
        
        # Store in Redis
        if self.use_redis:
            try:
                await self.redis.setex(
                    cache_key,
                    ttl_seconds,
                    json.dumps(token_data)
                )
                logger.info(f"✓ Cached OBO token in Redis (expires at: {expires_at.isoformat()}, TTL: {ttl_seconds}s)")
            except Exception as e:
                logger.warning(f"Redis set error, falling back to memory: {e}")
                # Fallback to memory
                with self._lock:
                    self._memory_cache[cache_key] = token_data
                    logger.info(f"✓ Cached OBO token in memory (expires at: {expires_at.isoformat()})")
        else:
            # Store in memory
            with self._lock:
                self._memory_cache[cache_key] = token_data
                logger.info(f"✓ Cached OBO token in memory (expires at: {expires_at.isoformat()})")

    async def clear_token(self, user_access_token: str):
        """Remove token from cache."""
        cache_key = self._get_cache_key(user_access_token)
        
        if self.use_redis:
            try:
                await self.redis.delete(cache_key)
                logger.info("✓ Cleared OBO token from Redis")
            except Exception as e:
                logger.warning(f"Redis delete error: {e}")
        
        with self._lock:
            if cache_key in self._memory_cache:
                del self._memory_cache[cache_key]
                logger.info("✓ Cleared OBO token from memory")

    async def clear_all(self):
        """Clear all cached tokens."""
        if self.use_redis:
            try:
                # Delete all keys matching pattern
                keys = await self.redis.keys("obo_token:*")
                if keys:
                    await self.redis.delete(*keys)
                    logger.info(f"✓ Cleared {len(keys)} OBO tokens from Redis")
            except Exception as e:
                logger.warning(f"Redis clear error: {e}")
        
        with self._lock:
            self._memory_cache.clear()
            logger.info("✓ Cleared all OBO tokens from memory")


# Global token cache instance (will be initialized with Redis if available)
_obo_token_cache: Optional[OBOTokenCache] = None


def get_obo_token_cache() -> OBOTokenCache:
    """Get global OBO token cache instance."""
    global _obo_token_cache
    if _obo_token_cache is None:
        _obo_token_cache = OBOTokenCache(redis_client=None)  # Will be initialized with Redis later
    return _obo_token_cache


def init_obo_token_cache(redis_client: Optional[Any] = None) -> OBOTokenCache:
    """Initialize global OBO token cache with Redis client."""
    global _obo_token_cache
    _obo_token_cache = OBOTokenCache(redis_client=redis_client)
    if redis_client:
        logger.info("OBO token cache initialized with Redis")
    else:
        logger.info("OBO token cache initialized with in-memory storage (Redis unavailable or disabled)")
    return _obo_token_cache


# For backward compatibility
obo_token_cache = get_obo_token_cache()


class OBOExchangeService:
    """Service for exchanging tokens via OBO flow."""
    
    def __init__(self):
        """Initialize OBO exchange service."""
        self.tenant_id = settings.azure_tenant_id
        self.client_id = settings.azure_client_id
        self.client_secret = settings.azure_client_secret
        self.authority = settings.azure_authority or f"https://login.microsoftonline.com/{self.tenant_id}"
        self.target_client_id = settings.azure_obo_target_client_id
        self.target_scopes = [f"api://{self.target_client_id}/All"] if self.target_client_id else []
        
        # Create MSAL app instance
        self.msal_app = msal.ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=self.authority
        ) if self.client_id and self.client_secret else None

    async def exchange_token_obo(self, user_access_token: str) -> Tuple[str, Optional[int]]:
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
            decoded = decode_token_without_verification(user_access_token)
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
                obo_decoded = decode_token_without_verification(result["access_token"])
                logger.info(f"   📋 OBO token details:")
                logger.info(f"      Audience: {obo_decoded.get('aud')}")
                logger.info(f"      Scope: {obo_decoded.get('scp', obo_decoded.get('roles', 'N/A'))}")
                
                # Extract expiration information
                expires_in = result.get("expires_in")  # Seconds until expiration
                exp = obo_decoded.get('exp')  # Unix timestamp expiration
                
                if expires_in:
                    logger.info(f"      Expires in: {expires_in} seconds ({expires_in/3600:.2f} hours)")
                elif exp:
                    import time
                    remaining = exp - time.time()
                    logger.info(f"      Expires in: {remaining:.0f} seconds ({remaining/3600:.2f} hours)")

                return result["access_token"], expires_in

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


# Global OBO exchange service instance
obo_exchange_service = OBOExchangeService()


class ARIAOBOExchangeService:
    """Service for exchanging tokens via OBO flow specifically for ARIA."""
    
    def __init__(self):
        """Initialize ARIA OBO exchange service."""
        self.tenant_id = settings.azure_tenant_id
        self.client_id = settings.azure_client_id
        self.client_secret = settings.azure_client_secret
        self.authority = settings.azure_authority or f"https://login.microsoftonline.com/{self.tenant_id}"
        self.aria_client_id = settings.azure_aria_target_client_id
        self.aria_scopes = [f"api://{self.aria_client_id}/api"] if self.aria_client_id else []
        
        # Create MSAL app instance
        self.msal_app = msal.ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=self.authority
        ) if self.client_id and self.client_secret else None

    async def exchange_token_aria(self, user_access_token: str) -> Tuple[str, Optional[int]]:
        """
        Exchange user token for ARIA OBO token to call ARIA API.

        Args:
            user_access_token: The user's Azure AD access token
            
        Returns:
            Tuple of (aria_token, expires_in)
        """
        if not self.msal_app:
            raise HTTPException(
                status_code=500,
                detail="MSAL app not initialized. Check Azure AD configuration."
            )

        if not self.aria_client_id:
            logger.warning("⚠️  ARIA client ID not configured, skipping ARIA token exchange")
            return None, None

        try:
            logger.info("🔄 Starting ARIA OBO token exchange...")
            logger.info(f"   Source app: {self.client_id}")
            logger.info(f"   ARIA app: {self.aria_client_id}")
            logger.info(f"   ARIA scope: {self.aria_scopes}")

            # Verify incoming token audience
            decoded = decode_token_without_verification(user_access_token)
            logger.info(f"   📋 Incoming token details:")
            logger.info(f"      Audience (aud): {decoded.get('aud')}")
            logger.info(f"      Token use: {decoded.get('token_use', 'N/A')}")

            # Perform OBO token exchange to ARIA API
            logger.info(f" Calling MSAL acquire_token_on_behalf_of for ARIA...")
            result = self.msal_app.acquire_token_on_behalf_of(
                user_assertion=user_access_token,
                scopes=self.aria_scopes  # Exchange for ARIA API token
            )

            if "access_token" in result:
                logger.info("✓ ARIA OBO token exchange successful")

                # Verify ARIA token
                aria_decoded = decode_token_without_verification(result["access_token"])
                logger.info(f" ARIA token details:")
                logger.info(f"  Audience: {aria_decoded.get('aud')}")
                logger.info(f"  Scope: {aria_decoded.get('scp', aria_decoded.get('roles', 'N/A'))}")
                
                expires_in = result.get("expires_in")
                if expires_in:
                    logger.info(f"  Expires in: {expires_in} seconds ({expires_in/3600:.2f} hours)")

                return result["access_token"], expires_in

            error_desc = result.get("error_description", "Unknown error")
            error_code = result.get("error", "Unknown")

            logger.error(f" ARIA OBO exchange failed:")
            logger.error(f" Error code: {error_code}")
            logger.error(f" Description: {error_desc}")

            raise Exception(f"Failed ARIA OBO: {error_desc}")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f" Error in ARIA OBO token exchange: {e}")
            # Don't raise exception - return None to allow continuing without ARIA token
            return None, None


# Global ARIA OBO exchange service instance
aria_obo_exchange_service = ARIAOBOExchangeService()


async def exchange_token_aria(user_access_token: str) -> Optional[str]:
    """
    Exchange user access token for ARIA OBO token with caching.
    
    Args:
        user_access_token: The user's Azure AD access token
        
    Returns:
        ARIA OBO token or None if exchange fails or not configured
    """
    if not user_access_token:
        logger.warning("⚠️  No user access token provided for ARIA token exchange")
        return None
    
    try:
        # Check cache first
        cache = get_obo_token_cache()
        cached_token = await cache.get_token(f"aria_{user_access_token}")
        
        if cached_token:
            logger.info("✓ Using cached ARIA token")
            return cached_token
        
        # Exchange for new ARIA token
        aria_token, expires_in = await aria_obo_exchange_service.exchange_token_aria(user_access_token)
        
        if aria_token:
            # Cache the token
            await cache.set_token(f"aria_{user_access_token}", aria_token, expires_in)
            logger.info("✓ ARIA token exchanged and cached")
            return aria_token
        else:
            logger.warning("⚠️  ARIA token exchange returned None")
            return None
            
    except Exception as e:
        logger.error(f"❌ Error exchanging ARIA token: {e}")
        return None


async def exchange_token_obo(user_access_token: str) -> str:
    """
    Exchange user access token for OBO token with caching and auto-refresh.

    This function:
    1. Checks Redis cache for valid token
    2. If expired or not cached, exchanges token via OBO flow
    3. Caches the new token in Redis with 12-hour expiration
    4. Returns the OBO token

    Args:
        user_access_token: User's Azure AD access token with audience = your app's client ID

    Returns:
        OBO token for calling downstream API

    Raises:
        HTTPException: If token exchange fails
    """
    try:
        cache = get_obo_token_cache()
        
        # Step 1: Check cache first (Redis or memory)
        cached_token = await cache.get_token(user_access_token)
        if cached_token:
            return cached_token

        # Step 2: Token not in cache or expired, exchange via OBO
        logger.info("🔄 Exchanging user token for OBO token (cache miss or expired)...")
        
        obo_token, expires_in = await obo_exchange_service.exchange_token_obo(user_access_token)
        
        # Step 3: Cache the token with actual expiration from Azure AD
        # Use actual expiration from Azure AD (usually 1 hour), but cap at 12 hours max
        if expires_in:
            # Use actual expiration from Azure AD response
            cache_expires_in = min(expires_in, 43200)  # Cap at 12 hours
            logger.info(f"✓ Caching OBO token with actual expiration: {expires_in}s (capped at {cache_expires_in}s)")
        else:
            # Fallback to 12 hours if expiration not provided
            cache_expires_in = 43200
            logger.info(f"✓ Caching OBO token with default expiration: {cache_expires_in}s (12 hours)")
        
        await cache.set_token(user_access_token, obo_token, expires_in=cache_expires_in)
        
        logger.info("✓ OBO token exchanged and cached successfully")
        return obo_token

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error exchanging OBO token: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"OBO token exchange error: {str(e)}"
        )


async def get_or_refresh_obo_token(user_access_token: str) -> str:
    """
    Get OBO token from cache or refresh if expired.

    This is a convenience wrapper around exchange_token_obo that handles
    automatic refresh when tokens expire.

    Args:
        user_access_token: User's Azure AD access token

    Returns:
        Valid OBO token (from cache or newly exchanged)
    """
    return await exchange_token_obo(user_access_token)


def decode_token_without_verification(token: str) -> Dict[str, Any]:
    """
    Decode JWT token without signature verification.

    Used for inspecting token claims like expiration.
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


def is_user_token_expired(user_access_token: str) -> bool:
    """
    Check if user access token is expired by decoding it.

    Args:
        user_access_token: User's Azure AD access token

    Returns:
        True if token is expired or invalid, False otherwise
    """
    try:
        decoded = decode_token_without_verification(user_access_token)
        
        # Check 'exp' claim (expiration time as Unix timestamp)
        exp = decoded.get('exp')
        if exp:
            import time
            current_time = time.time()
            # Consider expired if within 5 minutes of expiration
            return current_time >= (exp - 300)
        
        return False
    except Exception:
        return True


def add_mcp_token_to_jwt(jwt_token: str, obo_token: str, aria_token: Optional[str] = None) -> str:
    """
    Add mcp_token and aria_token fields to existing JWT token (Azure AD or custom JWT).
    
    This function:
    1. Decodes the existing JWT token (Azure AD or custom)
    2. Extracts user info from the token
    3. Creates a NEW custom JWT token (HS256) with user info, mcp_token, and aria_token embedded
    
    Args:
        jwt_token: Existing JWT token (Azure AD token or custom JWT from Authorization header)
        obo_token: OBO token to embed in mcp_token field
        aria_token: Optional ARIA token to embed in aria_token field
        
    Returns:
        New custom JWT token (HS256) with mcp_token and aria_token fields added
    """
    try:
        from aldar_middleware.auth.azure_ad import azure_ad_auth
        import jwt as jwt_lib
        
        # Try to decode as Azure AD token first (RS256), then fallback to custom JWT (HS256)
        decoded_payload = None
        try:
            # First, try to decode without verification to check token type
            unverified = decode_token_without_verification(jwt_token)
            header = jwt.get_unverified_header(jwt_token)
            
            # Check if it's an Azure AD token (RS256) or custom JWT (HS256)
            if header.get("alg") == "RS256":
                # It's an Azure AD token - decode without verification to extract user info
                decoded_payload = unverified
                logger.debug("✓ Detected Azure AD token (RS256), decoded without verification")
            else:
                # It's likely a custom JWT - try to verify it
                try:
                    decoded_payload = azure_ad_auth.verify_jwt_token(jwt_token)
                    logger.debug("✓ Decoded as custom JWT token (HS256)")
                except Exception:
                    # If verification fails, use unverified payload
                    decoded_payload = unverified
                    logger.debug("✓ Decoded custom JWT without verification")
        except Exception as e:
            logger.warning(f"⚠️  Could not decode token: {e}, trying decode without verification")
            # Last resort: decode without verification
            decoded_payload = decode_token_without_verification(jwt_token)
            logger.debug("✓ Decoded token without verification (fallback)")
        
        # Extract user info from decoded token
        user_sub = decoded_payload.get('sub') or decoded_payload.get('oid')
        user_email = (
            decoded_payload.get('email') or
            decoded_payload.get('preferred_username') or 
            decoded_payload.get('upn') or
            decoded_payload.get('unique_name', 'unknown@example.com')
        )
        
        # Get expiration - use token's exp or default to 1 hour
        user_exp = decoded_payload.get('exp')
        if user_exp:
            current_time = time.time()
            max_exp = current_time + (12 * 3600)  # Cap at 12 hours
            exp = min(user_exp, int(max_exp))
        else:
            exp = int(time.time()) + 3600  # 1 hour from now
        
        iat = decoded_payload.get('iat') or int(time.time())
        
        # Create NEW custom JWT payload with mcp_token and aria_token embedded
        # This will be a custom JWT (HS256) that external APIs can validate
        new_payload = {
            "sub": user_sub,
            "email": user_email,
            "exp": exp,
            "iat": iat,
            "iss": "aldar-middleware",
            "mcp_token": obo_token  # Embed OBO token in mcp_token field
        }
        
        # Add aria_token if provided
        if aria_token:
            new_payload["aria_token"] = aria_token
            logger.info("✓ Added aria_token to JWT payload")
        else:
            logger.debug("⊘ No aria_token provided, not adding to JWT payload")
        
        # Create new JWT token with HS256 algorithm (custom JWT for external APIs)
        new_token = jwt.encode(
            new_payload,
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm  # HS256
        )
        
        # Verify the new token contains mcp_token
        try:
            verified = jwt.decode(
                new_token,
                settings.jwt_secret_key,
                algorithms=[settings.jwt_algorithm]
            )
            if "mcp_token" in verified:
                logger.info(f"✅ VERIFIED: New JWT token contains mcp_token field")
                logger.info(f"   Original token sub: {user_sub}")
                logger.info(f"   New token sub: {verified.get('sub')}")
                logger.info(f"   mcp_token length: {len(verified.get('mcp_token', ''))} characters")
                if "aria_token" in verified:
                    logger.info(f"   ✅ aria_token length: {len(verified.get('aria_token', ''))} characters")
                else:
                    logger.warning(f"   ⚠️  aria_token NOT found in verified JWT")
            else:
                logger.error(f"❌ ERROR: New JWT token does NOT contain mcp_token field!")
            
            # Log all payload keys for debugging
            logger.info(f"   📋 Final JWT payload keys: {list(verified.keys())}")
        except Exception as verify_error:
            logger.warning(f"⚠️  Could not verify new JWT token: {verify_error}")
        
        logger.info(f"✓ Created custom JWT with mcp_token for user: {user_email}")
        
        return new_token
        
    except Exception as e:
        logger.error(f"❌ Error adding mcp_token to JWT token: {e}")
        import traceback
        logger.error(f"   Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to add mcp_token to JWT token: {str(e)}"
        )


def add_azure_ad_token_to_jwt(jwt_token: str, azure_ad_token: str) -> str:
    """
    Add azure_ad_token field to existing JWT token.
    
    This function:
    1. Decodes the existing JWT token
    2. Adds azure_ad_token field with Azure AD access token
    3. Also adds mcp_token field with same value (temporary for validation API compatibility)
    4. Creates a new JWT token with the updated payload
    
    Args:
        jwt_token: Existing JWT token (from Authorization header)
        azure_ad_token: Azure AD access token to embed in azure_ad_token and mcp_token fields
        
    Returns:
        New JWT token with both azure_ad_token and mcp_token fields added
    """
    try:
        from aldar_middleware.auth.azure_ad import azure_ad_auth
        
        # Decode existing JWT token
        decoded_payload = azure_ad_auth.verify_jwt_token(jwt_token)
        
        # Add azure_ad_token field to payload
        decoded_payload["azure_ad_token"] = azure_ad_token
        
        # TEMPORARY: Also add mcp_token with same value for backward compatibility
        # Validation API still expects mcp_token until it's updated to use azure_ad_token
        # TODO: Remove mcp_token once validation API is updated to use azure_ad_token
        decoded_payload["mcp_token"] = azure_ad_token
        logger.info("✓ Added both azure_ad_token and mcp_token (mcp_token is temporary for validation API compatibility)")
        
        # Create new JWT token with updated payload
        new_token = jwt.encode(
            decoded_payload,
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm
        )
        
        # Verify the new token contains azure_ad_token
        try:
            verified = jwt.decode(
                new_token,
                settings.jwt_secret_key,
                algorithms=[settings.jwt_algorithm]
            )
            if "azure_ad_token" in verified:
                logger.info(f"✅ VERIFIED: New JWT token contains azure_ad_token field")
                logger.info(f"   Original token sub: {decoded_payload.get('sub')}")
                logger.info(f"   New token sub: {verified.get('sub')}")
                logger.info(f"   azure_ad_token length: {len(verified.get('azure_ad_token', ''))} characters")
                if "mcp_token" in verified:
                    logger.info(f"✅ VERIFIED: New JWT token also contains mcp_token field (for validation API compatibility)")
                else:
                    logger.warning(f"⚠️  WARNING: New JWT token does NOT contain mcp_token field (validation API may fail)")
            else:
                logger.error(f"❌ ERROR: New JWT token does NOT contain azure_ad_token field!")
        except Exception as verify_error:
            logger.warning(f"⚠️  Could not verify new JWT token: {verify_error}")
        
        logger.info(f"✓ Added azure_ad_token to JWT token for user: {decoded_payload.get('email', 'unknown')}")
        
        return new_token
        
    except Exception as e:
        logger.error(f"❌ Error adding azure_ad_token to JWT token: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to add azure_ad_token to JWT token: {str(e)}"
        )


def create_mcp_token(user_access_token: str, obo_token: str, aria_token: Optional[str] = None) -> str:
    """
    Create a custom JWT token for data team API calls with OBO token and optional ARIA token embedded.
    
    This function creates a JWT token in the format required by the data team:
    {
        "sub": "user_id",
        "email": "user@example.com",
        "exp": 1765392896,
        "iat": 1765356896,
        "iss": "aldar-middleware",
        "mcp_token": "OBO_token",
        "aria_token": "ARIA_token"  // optional
    }
    
    Args:
        user_access_token: User's Azure AD access token (to extract user info)
        obo_token: OBO token to embed in mcp_token field
        aria_token: Optional ARIA token to embed in aria_token field
        
    Returns:
        JWT token string with the custom format
    """
    try:
        # Decode user token to extract user information
        decoded_user_token = decode_token_without_verification(user_access_token)
        
        # Extract user ID (sub) from user token
        user_sub = decoded_user_token.get('sub') or decoded_user_token.get('oid') or decoded_user_token.get('appid')
        
        # Extract email from user token
        user_email = (
            decoded_user_token.get('preferred_username') or 
            decoded_user_token.get('upn') or 
            decoded_user_token.get('email') or
            decoded_user_token.get('unique_name', 'unknown@example.com')
        )
        
        # Get expiration from user token, or use default (1 hour from now)
        user_exp = decoded_user_token.get('exp')
        if user_exp:
            # Use user token expiration, but ensure it's not too far in the future
            current_time = time.time()
            # Cap at 12 hours from now
            max_exp = current_time + (12 * 3600)
            exp = min(user_exp, int(max_exp))
        else:
            # Default to 1 hour from now
            exp = int(time.time()) + 3600
        
        # Get issued at time
        iat = decoded_user_token.get('iat') or int(time.time())
        
        # Create payload with required format
        payload = {
            "sub": user_sub,
            "email": user_email,
            "exp": exp,
            "iat": iat,
            "iss": "aldar-middleware",
            "mcp_token": obo_token
        }
        
        # Add aria_token if provided
        if aria_token:
            payload["aria_token"] = aria_token
            logger.info(f"✓ Added aria_token to MCP token payload ({len(aria_token)} chars)")
        
        # Sign the token using JWT secret key
        token = jwt.encode(
            payload,
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm
        )
        
        # Verify the token was created correctly by decoding it
        try:
            decoded_verification = jwt.decode(
                token,
                settings.jwt_secret_key,
                algorithms=[settings.jwt_algorithm]
            )
            mcp_token_in_decoded = decoded_verification.get("mcp_token")
            if mcp_token_in_decoded:
                logger.info(f"✅ VERIFIED: MCP token contains mcp_token field with {len(mcp_token_in_decoded)} characters")
                logger.info(f"   Decoded mcp_token preview: {mcp_token_in_decoded[:50]}...")
                logger.info(f"   Matches original OBO token: {mcp_token_in_decoded == obo_token}")
            else:
                logger.error(f"❌ ERROR: MCP token does NOT contain mcp_token field!")
                logger.error(f"   Decoded payload keys: {list(decoded_verification.keys())}")
        except Exception as verify_error:
            logger.warning(f"⚠️  Could not verify MCP token: {verify_error}")
        
        # Log token details for verification
        logger.info(f"✓ Created MCP token for user: {user_email}")
        logger.info(f"   Token expires at: {datetime.fromtimestamp(exp).isoformat()}")
        logger.info(f"   MCP token (OBO) embedded: {obo_token[:50]}...")
        logger.info(f"   Full payload keys: {list(payload.keys())}")
        logger.info(f"   Payload sub: {user_sub}, email: {user_email}, iss: aldar-middleware")
        logger.info(f"   ✅ mcp_token field in payload: {len(obo_token)} characters")
        logger.info(f"   MCP token string length: {len(token)} characters")
        
        return token
        
    except Exception as e:
        logger.error(f"❌ Error creating MCP token: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create MCP token: {str(e)}"
        )


def verify_mcp_token(mcp_token: str) -> Dict[str, Any]:
    """
    Decode and verify MCP token to check if OBO token is embedded.
    
    This function decodes the MCP token and returns its contents,
    allowing you to verify that mcp_token field contains the OBO token.
    
    Args:
        mcp_token: The MCP JWT token to verify
        
    Returns:
        Dictionary containing decoded token payload with mcp_token field
        
    Example:
        >>> token_data = verify_mcp_token(mcp_token_string)
        >>> print(token_data['mcp_token'])  # This should be the OBO token
    """
    try:
        # Decode token without verification first to inspect
        decoded = decode_token_without_verification(mcp_token)
        
        # Verify token signature
        try:
            verified = jwt.decode(
                mcp_token,
                settings.jwt_secret_key,
                algorithms=[settings.jwt_algorithm]
            )
            logger.info("✓ MCP token signature verified successfully")
        except jwt.ExpiredSignatureError:
            logger.warning("⚠️  MCP token has expired")
            verified = decoded  # Still return decoded data
        except jwt.InvalidTokenError as e:
            logger.warning(f"⚠️  MCP token signature invalid: {e}")
            verified = decoded  # Still return decoded data for inspection
        
        # Check if mcp_token field exists
        if "mcp_token" in verified:
            obo_token = verified["mcp_token"]
            logger.info(f"✅ mcp_token field found in token")
            logger.info(f"   OBO token length: {len(obo_token)} characters")
            logger.info(f"   OBO token preview: {obo_token[:50]}...")
        else:
            logger.error("❌ mcp_token field NOT found in token!")
        
        return verified
        
    except Exception as e:
        logger.error(f"❌ Error verifying MCP token: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to verify MCP token: {str(e)}"
        )


def extract_obo_from_mcp_jwt(mcp_jwt: str, verify_signature: bool = True) -> str:
    """
    Extract the embedded OBO token from a JWT that contains an `mcp_token` field.

    Args:
        mcp_jwt: The JWT string that was created by `create_mcp_token` or `add_mcp_token_to_jwt`.
        verify_signature: If True, verify the JWT signature using the configured JWT secret and algorithm.
                          If verification fails and this flag is True, the function will try to fall back
                          to decoding without verification to extract the `mcp_token` if present.

    Returns:
        The embedded OBO token string.

    Raises:
        HTTPException: If the token is invalid or does not contain an `mcp_token` field.
    """
    try:
        decoded: Dict[str, Any]

        if verify_signature:
            try:
                decoded = jwt.decode(
                    mcp_jwt,
                    settings.jwt_secret_key,
                    algorithms=[settings.jwt_algorithm]
                )
            except jwt.ExpiredSignatureError:
                logger.warning("⚠️  MCP JWT has expired when verifying signature")
                # Still attempt to decode without verification to extract the field
                decoded = decode_token_without_verification(mcp_jwt)
            except jwt.InvalidTokenError as e:
                logger.warning(f"⚠️  MCP JWT signature invalid: {e}")
                # Fall back to decode without verification to try to extract the field
                decoded = decode_token_without_verification(mcp_jwt)
        else:
            decoded = decode_token_without_verification(mcp_jwt)

        mcp_token = decoded.get("mcp_token")
        if not mcp_token:
            logger.error("❌ mcp_token field not found in provided JWT")
            raise HTTPException(status_code=400, detail="mcp_token field not found in provided JWT")

        logger.info(f"✓ Extracted embedded OBO token from MCP JWT (length={len(mcp_token)})")
        return mcp_token

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error extracting OBO from MCP JWT: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to extract OBO token: {str(e)}")


def get_bearer_from_mcp_jwt(mcp_jwt: str, verify_signature: bool = True) -> Dict[str, str]:
    """
    Helper to produce an Authorization header dict using the embedded OBO token.

    Args:
        mcp_jwt: JWT containing `mcp_token` field.
        verify_signature: Whether to verify JWT signature when extracting.

    Returns:
        A dict suitable for passing as headers, e.g. `{"Authorization": "Bearer <obo_token>"}`

    Raises:
        HTTPException: If extraction fails.
    """
    obo = extract_obo_from_mcp_jwt(mcp_jwt, verify_signature=verify_signature)
    return {"Authorization": f"Bearer {obo}"}
