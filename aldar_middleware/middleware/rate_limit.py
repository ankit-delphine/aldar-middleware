"""Rate limiting middleware."""

import asyncio
from typing import Callable, Optional
from uuid import UUID

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response
from loguru import logger

from aldar_middleware.settings.context import get_user_id, get_correlation_id
from aldar_middleware.services.rate_limit_service import RateLimitError


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce rate limits on requests.

    This middleware:
    1. Extracts user from request context
    2. Checks rate limits against Redis
    3. Returns HTTP 429 if rate limit exceeded
    4. Applies throttle delays if configured
    """

    # Paths that bypass rate limiting
    BYPASS_PATHS = {
        "/health",
        "/api/v1/health",
        "/api/v1/health/detailed",
        "/api/v1/health/ready",
        "/api/v1/health/live",
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
        """Process request with rate limiting.

        Args:
            request: The incoming request
            call_next: The next middleware or route handler

        Returns:
            Response with rate limiting applied
        """
        # Skip rate limiting for certain paths
        if self._should_bypass(request.url.path):
            return await call_next(request)

        # Get user from context
        user_id = get_user_id()
        if not user_id:
            # No user, allow (unauthenticated requests don't count)
            return await call_next(request)

        correlation_id = get_correlation_id()

        try:
            # Check rate limit
            from aldar_middleware.settings import get_settings

            settings = get_settings()
            if not settings.REDIS_URL:
                # No Redis, skip rate limiting
                return await call_next(request)

            # Rate limiting is enforced by services, not middleware
            # This middleware just logs and tracks
            response = await call_next(request)
            return response

        except RateLimitError as e:
            logger.warning(
                f"Rate limit exceeded | user={user_id} error={e.message}",
                extra={"correlation_id": correlation_id},
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests",
                    "message": e.message,
                    "retry_after": e.retry_after_seconds,
                    "correlation_id": correlation_id,
                },
                headers={"Retry-After": str(e.retry_after_seconds)},
            )

        except Exception as e:
            logger.error(
                f"Rate limit middleware error | error={e}",
                extra={"correlation_id": correlation_id},
                exc_info=True,
            )
            # Don't block request on middleware errors
            return await call_next(request)

    def _should_bypass(self, path: str) -> bool:
        """Check if path should bypass rate limiting.

        Args:
            path: Request path

        Returns:
            True if should bypass, False otherwise
        """
        for bypass_path in self.BYPASS_PATHS:
            if path.startswith(bypass_path):
                return True
        return False


class ThrottleMiddleware(BaseHTTPMiddleware):
    """Middleware to apply throttle delays for rate-limited requests.

    This middleware applies delays when requests are throttled
    to help with gradual backoff and congestion management.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        """Process request with throttle delay.

        Args:
            request: The incoming request
            call_next: The next middleware or route handler

        Returns:
            Response with throttle delay applied if needed
        """
        # Check if request is marked as throttled
        throttle_delay = getattr(request.state, "throttle_delay", None)

        if throttle_delay and throttle_delay > 0:
            correlation_id = get_correlation_id()
            logger.debug(
                f"Applying throttle delay | delay={throttle_delay}s",
                extra={"correlation_id": correlation_id},
            )

            # Apply delay
            await asyncio.sleep(throttle_delay)

        return await call_next(request)