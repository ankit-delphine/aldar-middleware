"""Correlation ID middleware for request tracking.

This middleware ensures every request has a correlation ID for tracking
the entire request lifecycle across services and agents.
"""

import uuid
import re
from typing import Callable, Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse
from loguru import logger

from aldar_middleware.settings.context import set_correlation_id, get_correlation_id, clear_correlation_id, set_user_context, clear_user_context


# Supported correlation ID header names (in order of precedence)
CORRELATION_ID_HEADERS = [
    "X-Correlation-ID",
    "X-Correlation-Id",
    "X-Request-ID",
    "X-Request-Id",
    "Request-ID",
    "Request-Id",
]

# Response header name
RESPONSE_CORRELATION_ID_HEADER = "X-Correlation-ID"

# UUID validation pattern
UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE
)


def generate_correlation_id() -> str:
    """Generate a new correlation ID using UUID v4.
    
    Returns:
        A new UUID v4 correlation ID as a string
    """
    return str(uuid.uuid4())


def extract_correlation_id(request: Request) -> Optional[str]:
    """Extract correlation ID from request headers.
    
    Tries multiple header names in order of precedence.
    Validates that the extracted ID is a valid UUID.
    
    Args:
        request: The incoming request
        
    Returns:
        The correlation ID if found and valid, None otherwise
    """
    for header_name in CORRELATION_ID_HEADERS:
        correlation_id = request.headers.get(header_name)
        if correlation_id:
            # Validate it's a proper UUID
            if is_valid_correlation_id(correlation_id):
                logger.debug(f"Extracted correlation ID from header {header_name}: {correlation_id}")
                return correlation_id
            else:
                logger.warning(
                    f"Invalid correlation ID format in header {header_name}: {correlation_id}. "
                    "Expected UUID format. Generating new ID."
                )
    return None


def is_valid_correlation_id(correlation_id: str) -> bool:
    """Validate that a correlation ID is a valid UUID.
    
    Args:
        correlation_id: The correlation ID to validate
        
    Returns:
        True if valid UUID format, False otherwise
    """
    if not correlation_id or not isinstance(correlation_id, str):
        return False
    
    # Trim whitespace
    correlation_id = correlation_id.strip()
    
    # Check length (UUID is 36 characters with hyphens)
    if len(correlation_id) != 36:
        return False
    
    # Validate UUID pattern
    return UUID_PATTERN.match(correlation_id) is not None


def sanitize_correlation_id(correlation_id: str) -> str:
    """Sanitize correlation ID by removing unwanted characters.
    
    Args:
        correlation_id: The correlation ID to sanitize
        
    Returns:
        Sanitized correlation ID
    """
    # Remove any whitespace
    correlation_id = correlation_id.strip()
    
    # Convert to lowercase for consistency
    correlation_id = correlation_id.lower()
    
    return correlation_id


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Middleware to handle correlation ID for request tracking.
    
    This middleware:
    1. Extracts correlation ID from request headers (if present)
    2. Generates a new correlation ID if not present or invalid
    3. Stores correlation ID in async-safe context
    4. Injects correlation ID into response headers
    5. Cleans up context after request completion
    
    The correlation ID can be accessed throughout the request lifecycle
    using the context management functions in aldar_middleware.context.
    """
    
    def __init__(self, app, header_name: str = RESPONSE_CORRELATION_ID_HEADER):
        """Initialize the correlation ID middleware.
        
        Args:
            app: The ASGI application
            header_name: The header name to use for response correlation ID
        """
        super().__init__(app)
        self.header_name = header_name
    
    async def dispatch(
        self, 
        request: Request, 
        call_next: Callable
    ) -> StarletteResponse:
        """Process request and inject correlation ID.
        
        Args:
            request: The incoming request
            call_next: The next middleware or route handler
            
        Returns:
            The response with correlation ID header
        """
        # Extract or generate correlation ID
        correlation_id = extract_correlation_id(request)
        
        if not correlation_id:
            correlation_id = generate_correlation_id()
            logger.debug(f"Generated new correlation ID: {correlation_id}")
        else:
            # Sanitize the extracted correlation ID
            correlation_id = sanitize_correlation_id(correlation_id)
            logger.debug(f"Using existing correlation ID: {correlation_id}")
        
        # Store correlation ID in context
        set_correlation_id(correlation_id)
        
        # Extract and store user information FIRST
        user_id, username, user_type, email, is_authenticated = extract_user_info(request)
        set_user_context(
            user_id=user_id,
            username=username,
            user_type=user_type,
            email=email,
            is_authenticated=is_authenticated
        )
        
        # Log user extraction success with proper user context
        if user_id and user_id != "N/A":
            logger.bind(
                user_id=user_id,
                username=username, 
                user_type=user_type,
                email=email,
                is_authenticated=is_authenticated
            ).info(f"User context established: user_id={user_id}, email={email}, user_type={user_type}")
        
        # Log request with correlation ID and user info
        user_info = f"User: {username or 'anonymous'}" if is_authenticated else "User: anonymous"
        
        # Bind user context to the logger for this request
        logger.bind(
            user_id=user_id or "N/A",
            username=username or "N/A", 
            user_type=user_type or "N/A",
            email=email or "N/A",
            is_authenticated=is_authenticated
        ).info(
            f"[{correlation_id}] {request.method} {request.url.path} - "
            f"Client: {request.client.host if request.client else 'unknown'} - {user_info}"
        )

        # Track in Azure Application Insights
        await self._track_correlation_id(correlation_id, request)
        
        try:
            # Process request
            response = await call_next(request)
            
            # Inject correlation ID into response headers
            response.headers[self.header_name] = correlation_id
            
            # Log response with correlation ID and user context
            logger.bind(
                user_id=user_id or "N/A",
                username=username or "N/A", 
                user_type=user_type or "N/A",
                email=email or "N/A",
                is_authenticated=is_authenticated
            ).info(
                f"[{correlation_id}] Response status: {response.status_code}"
            )
            
            return response
            
        except Exception as e:
            # Log error with correlation ID and user context
            logger.bind(
                user_id=user_id or "N/A",
                username=username or "N/A", 
                user_type=user_type or "N/A",
                email=email or "N/A",
                is_authenticated=is_authenticated
            ).error(
                f"[{correlation_id}] Error processing request: {str(e) if not hasattr(e, '__dict__') else f'Error of type {type(e).__name__}'}",
                exc_info=True
            )
            raise
            
        finally:
            # Clean up context
            clear_correlation_id()
            clear_user_context()

    async def _track_correlation_id(self, correlation_id: str, request: Request) -> None:
        """Track correlation ID in Azure Application Insights.
        
        Args:
            correlation_id: The request correlation ID
            request: The incoming request
        """
        try:
            from aldar_middleware.monitoring.azure_monitor import get_app_insights_client
            
            client = get_app_insights_client()
            if client:
                client.track_event(
                    "CorrelationIdGenerated",
                    properties={
                        "correlation_id": correlation_id,
                        "method": request.method,
                        "path": request.url.path,
                        "client": request.client.host if request.client else "unknown",
                    }
                )
        except Exception as e:
            logger.debug(f"Failed to track correlation ID in Azure: {e}")


# Removed WebSocketCorrelationIdMixin (legacy WebSocket support)


def get_or_generate_correlation_id() -> str:
    """Get correlation ID from context or generate a new one.
    
    This is useful for background tasks or situations where
    a correlation ID might not be set in the context.
    
    Returns:
        The correlation ID from context or a new one
    """
    correlation_id = get_correlation_id()
    if not correlation_id:
        correlation_id = generate_correlation_id()
        set_correlation_id(correlation_id)
    return correlation_id


def extract_user_info(request: Request) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str], bool]:
    """Extract user information from request.
    
    Args:
        request: The incoming request
        
    Returns:
        Tuple of (user_id, username, user_type, email, is_authenticated)
    """
    # Debug logging removed to prevent logs without user context
    try:
        # Try to get user from request state (set by auth middleware)
        if hasattr(request.state, "user") and request.state.user:
            user = request.state.user
            user_type = "admin" if getattr(user, "is_admin", False) else "user"
            return (
                str(user.id),
                getattr(user, "username", None),
                user_type,
                getattr(user, "email", None),
                True
            )
        
        # Try to get from request scope
        if "user" in request.scope:
            user_data = request.scope.get("user")
            if user_data:
                user_type = "admin" if user_data.get("is_admin", False) else "user"
                return (
                    str(user_data.get("id") or user_data.get("sub")),
                    user_data.get("username"),
                    user_type,
                    user_data.get("email"),
                    True
                )
        
        # Try to extract from JWT token if available
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]  # Remove "Bearer " prefix
            try:
                # Decode JWT payload for logging context only (NOT for authentication)
                # WARNING: This is for logging purposes only and does NOT verify token authenticity
                import base64
                import json
                
                try:
                    # JWT has 3 parts separated by dots: header.payload.signature
                    parts = token.split('.')
                    if len(parts) >= 2:
                        # Decode the payload (second part) - FOR LOGGING CONTEXT ONLY
                        payload_part = parts[1]
                        # Add padding if needed
                        payload_part += '=' * (4 - len(payload_part) % 4)
                        payload_bytes = base64.urlsafe_b64decode(payload_part)
                        payload = json.loads(payload_bytes.decode('utf-8'))
                        
                        # Extract user information for logging context
                        user_id = payload.get("sub")
                        email = payload.get("email")
                        username = email  # Use email as username if no separate username field
                        
                        # Determine user type based on token claims
                        user_type = "admin" if payload.get("is_admin", False) else "user"
                        
                        if user_id:
                            # Don't log here - log after user context is set
                            return (user_id, username, user_type, email, True)
                        else:
                            logger.warning("JWT token decoded but no user_id found in payload")
                            
                except Exception as decode_error:
                    logger.debug(f"Failed to decode JWT payload for logging context: {decode_error}")
                    # Token is present but we can't decode it - still consider it authenticated
                    return (None, None, "authenticated", None, True)
                    
            except Exception as jwt_error:
                logger.debug(f"Failed to decode JWT token: {jwt_error}")
                # Token is present but we can't decode it
                return (None, None, "authenticated", None, True)
            
    except Exception as e:
        logger.debug(f"Failed to extract user info: {e}")
    
    return (None, None, None, None, False)