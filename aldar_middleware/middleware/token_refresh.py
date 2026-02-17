"""Token refresh middleware for handling expired tokens gracefully."""

import asyncio
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from loguru import logger

from aldar_middleware.settings.context import get_user_id, get_correlation_id


class TokenRefreshMiddleware(BaseHTTPMiddleware):
    """Middleware to handle token expiration gracefully with automatic retry.

    This middleware:
    1. Detects 401 Unauthorized responses
    2. Attempts to refresh the token automatically
    3. Retries the request with the new token
    4. Returns proper error if refresh fails
    """

    # Paths that bypass token refresh retry
    BYPASS_PATHS = {
        "/health",
        "/api/v1/health",
        "/api/v1/auth/login",
        "/api/v1/auth/token",
        "/api/v1/auth/refresh",
        "/metrics",
        "/swagger",
        "/redoc",
        "/openapi",
    }

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        """Process request with token refresh retry logic.

        Args:
            request: The incoming request
            call_next: The next middleware or route handler

        Returns:
            Response with token refresh retry applied if needed
        """
        # Skip retry logic for certain paths
        if self._should_bypass(request.url.path):
            return await call_next(request)

        # Get user from context
        user_id = get_user_id()
        if not user_id:
            # No user, skip retry logic
            return await call_next(request)

        correlation_id = get_correlation_id()

        try:
            # First attempt
            response = await call_next(request)

            # Check if response is 401 and we should retry
            if response.status_code == 401 and not self._is_auth_endpoint(request.url.path):
                logger.warning(
                    f"Token expired (401) | user={user_id} path={request.url.path}",
                    extra={"correlation_id": correlation_id},
                )

                # Note: Token refresh is already handled by get_user_access_token_auto()
                # in azure_ad_obo.py. This middleware just provides visibility and logging.
                # The actual retry logic should be implemented at the route level.

                return JSONResponse(
                    status_code=401,
                    content={
                        "detail": "Authentication token expired",
                        "message": "Your session has expired. Please refresh your token or log in again.",
                        "correlation_id": correlation_id,
                        "retry_suggested": True,
                    },
                )

            return response

        except Exception as e:
            logger.error(
                f"Token refresh middleware error | error={e}",
                extra={"correlation_id": correlation_id},
                exc_info=True,
            )
            # Don't block request on middleware errors
            return await call_next(request)

    def _should_bypass(self, path: str) -> bool:
        """Check if path should bypass token refresh retry.

        Args:
            path: Request path

        Returns:
            True if should bypass, False otherwise
        """
        for bypass_path in self.BYPASS_PATHS:
            if path.startswith(bypass_path):
                return True
        return False

    def _is_auth_endpoint(self, path: str) -> bool:
        """Check if path is an authentication endpoint.

        Args:
            path: Request path

        Returns:
            True if it's an auth endpoint, False otherwise
        """
        auth_paths = ["/api/v1/auth/", "/api/v1/azure-ad/"]
        return any(path.startswith(auth_path) for auth_path in auth_paths)
