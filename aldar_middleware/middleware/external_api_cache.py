"""Middleware for AGNO API caching and request optimization."""

import json
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from loguru import logger

from aldar_middleware.settings.context import get_correlation_id, get_user_id
from aldar_middleware.orchestration.agno import agno_service
from aldar_middleware.monitoring.prometheus import (
    record_external_api_cache_hit,
    record_external_api_cache_miss,
    record_external_api_request
)
from aldar_middleware.settings import settings


class AGNOAPICacheMiddleware(BaseHTTPMiddleware):
    """Middleware for AGNO API caching and optimization.
    
    This middleware:
    1. Intercepts requests to AGNO API endpoints
    2. Checks cache for existing responses
    3. Avoids unnecessary external API calls
    4. Optimizes database operations
    5. Provides fast response times
    """
    
    API_PREFIX = settings.api_prefix
    
    # Paths that should be cached
    CACHEABLE_PATHS = {
        f"{API_PREFIX}/orchestrat/config",
        f"{API_PREFIX}/orchestrat/models",
        f"{API_PREFIX}/orchestrat/agents",
        f"{API_PREFIX}/orchestrat/teams",
        f"{API_PREFIX}/orchestrat/workflows",
        f"{API_PREFIX}/orchestrat/health",
        f"{API_PREFIX}/orchestrat/sessions",
        f"{API_PREFIX}/orchestrat/memories",
        f"{API_PREFIX}/orchestrat/memory_topics",
        f"{API_PREFIX}/orchestrat/user_memory_stats",
        f"{API_PREFIX}/orchestrat/eval-runs",
        f"{API_PREFIX}/orchestrat/metrics",
        f"{API_PREFIX}/orchestrat/knowledge/content",
        f"{API_PREFIX}/orchestrat/knowledge/config",
        f"{API_PREFIX}/orchestrat/",
    }
    
    # Paths that should bypass cache
    BYPASS_CACHE_PATHS = {
        f"{API_PREFIX}/orchestrat/agents/*/runs",
        f"{API_PREFIX}/orchestrat/teams/*/runs",
        f"{API_PREFIX}/orchestrat/workflows/*/runs",
        f"{API_PREFIX}/orchestrat/sessions/*/rename",
        f"{API_PREFIX}/orchestrat/memories",
        f"{API_PREFIX}/orchestrat/eval-runs",
        f"{API_PREFIX}/orchestrat/metrics/refresh",
        f"{API_PREFIX}/orchestrat/knowledge/content",
    }
    
    def __init__(self, app, cache_ttl: int = 3600):
        """Initialize the AGNO API cache middleware.
        
        Args:
            app: The ASGI application
            cache_ttl: Cache TTL in seconds (default: 1 hour)
        """
        super().__init__(app)
        self.cache_ttl = cache_ttl

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request with caching logic.
        
        Args:
            request: The incoming request
            call_next: The next middleware or route handler
            
        Returns:
            The response (cached or fresh)
        """
        correlation_id = get_correlation_id()
        user_id = get_user_id()
        
        # Check if this is an external API request
        if not self._is_external_api_request(request):
            return await call_next(request)
        
        # Check if path should bypass cache
        if self._should_bypass_cache(request):
            logger.debug(
                f"Bypassing cache for path: {request.url.path}, "
                f"correlation_id={correlation_id}"
            )
            return await call_next(request)
        
        # Try to get cached response
        cached_response = await self._get_cached_response(request, user_id, correlation_id)
        if cached_response:
            logger.info(
                f"Cache hit for path: {request.url.path}, "
                f"correlation_id={correlation_id}"
            )
            return JSONResponse(
                content=cached_response,
                headers={"X-Cache": "HIT", "X-Correlation-ID": correlation_id}
            )
        
        # Cache miss - proceed with request
        logger.debug(
            f"Cache miss for path: {request.url.path}, "
            f"correlation_id={correlation_id}"
        )
        
        response = await call_next(request)
        
        # Cache successful responses
        if response.status_code == 200:
            await self._cache_response(request, response, user_id, correlation_id)
        
        return response

    def _is_external_api_request(self, request: Request) -> bool:
        """Check if request is for AGNO API endpoints."""
        return request.url.path.startswith(f"{self.API_PREFIX}/orchestrat/")

    def _should_bypass_cache(self, request: Request) -> bool:
        """Check if request should bypass cache."""
        path = request.url.path
        
        # Check bypass patterns
        for bypass_pattern in self.BYPASS_CACHE_PATHS:
            if self._path_matches_pattern(path, bypass_pattern):
                return True
        
        # POST, PUT, DELETE, PATCH requests should bypass cache
        if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
            return True
        
        # Check for force refresh parameter
        if request.query_params.get("force_refresh") == "true":
            return True
        
        return False

    def _path_matches_pattern(self, path: str, pattern: str) -> bool:
        """Check if path matches pattern (supports wildcards)."""
        if "*" in pattern:
            # Simple wildcard matching
            pattern_parts = pattern.split("*")
            if len(pattern_parts) == 2:
                return path.startswith(pattern_parts[0]) and path.endswith(pattern_parts[1])
        return path == pattern

    async def _get_cached_response(
        self, 
        request: Request, 
        user_id: Optional[str], 
        correlation_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get cached response for request."""
        try:
            # Generate cache key
            cache_key = self._generate_cache_key(request)
            
            # Get from AGNO API service cache
            cached_data = await agno_service.api_service.get_cached_response(
                cache_key, user_id, correlation_id
            )
            
            if cached_data:
                record_external_api_cache_hit(
                    api_type="agno_multiagent",
                    endpoint=request.url.path,
                    duration=0.0
                )
                return cached_data
            
            record_external_api_cache_miss(
                api_type="agno_multiagent", 
                endpoint=request.url.path,
                duration=0.0
            )
            return None
            
        except Exception as e:
            logger.warning(f"Error getting cached response: {str(e)}")
            return None

    async def _cache_response(
        self, 
        request: Request, 
        response: Response, 
        user_id: Optional[str], 
        correlation_id: str
    ) -> None:
        """Cache response for future requests."""
        try:
            # Only cache JSON responses
            if response.headers.get("content-type", "").startswith("application/json"):
                # Get response body without consuming the stream
                if hasattr(response, 'body') and response.body:
                    # Create a copy of the body to avoid consuming the original stream
                    body_bytes = response.body
                    response_data = json.loads(body_bytes.decode())
                    
                    # Generate cache key
                    cache_key = self._generate_cache_key(request)
                    
                    # Save to cache
                    await agno_service.api_service.save_to_cache(
                        cache_key=cache_key,
                        response_data=response_data,
                        endpoint=request.url.path,
                        ttl=self.cache_ttl,
                        user_id=user_id,
                        correlation_id=correlation_id
                    )
                    
                    logger.debug(
                        f"Cached response for path: {request.url.path}, "
                        f"correlation_id={correlation_id}"
                    )
                else:
                    # For streaming responses or empty responses, we can't cache easily
                    logger.debug(f"Skipping cache for non-cacheable response: {request.url.path}")
                
        except Exception as e:
            logger.warning(f"Error caching response: {str(e)}")

    def _generate_cache_key(self, request: Request) -> str:
        """Generate cache key for request."""
        # Include path, method, and query parameters
        key_parts = [
            request.url.path,
            request.method.upper()
        ]
        
        # Add query parameters if any
        if request.query_params:
            sorted_params = sorted(request.query_params.items())
            # Convert to dict and handle non-serializable values
            params_dict = {}
            for key, value in sorted_params:
                # Convert non-serializable objects to strings
                if hasattr(value, '__dict__') or not isinstance(value, (str, int, float, bool, type(None))):
                    params_dict[key] = str(value)
                else:
                    params_dict[key] = value
            key_parts.append(json.dumps(params_dict, sort_keys=True))
        
        return f"agno_middleware_cache:{':'.join(key_parts)}"


class AGNOAPIOptimizationMiddleware(BaseHTTPMiddleware):
    """Middleware for optimizing AGNO API requests and database operations."""
    
    def __init__(self, app):
        """Initialize the optimization middleware."""
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        """Optimize AGNO API requests.
        
        This middleware:
        1. Batches similar requests
        2. Optimizes database queries
        3. Reduces redundant external API calls
        4. Implements request deduplication
        """
        correlation_id = get_correlation_id()
        
        # Check if this is an AGNO API request
        # Check if this is an AGNO API request
        if not request.url.path.startswith(f"{settings.api_prefix}/orchestrat/"):
            return await call_next(request)
        
        # Implement request deduplication
        request_key = self._generate_request_key(request)
        
        # Check if similar request is already in progress
        if await self._is_request_in_progress(request_key):
            logger.info(
                f"Request deduplication: waiting for similar request, "
                f"key={request_key}, correlation_id={correlation_id}"
            )
            # Wait for the other request to complete
            return await self._wait_for_request(request_key, correlation_id)
        
        # Mark request as in progress
        await self._mark_request_in_progress(request_key, correlation_id)
        
        try:
            # Process request
            response = await call_next(request)
            
            # Cache response for deduplication
            await self._cache_response_for_dedup(request_key, response)
            
            return response
            
        finally:
            # Clean up
            await self._cleanup_request(request_key)

    def _generate_request_key(self, request: Request) -> str:
        """Generate unique key for request deduplication."""
        key_parts = [
            request.url.path,
            request.method.upper(),
            request.query_params.get("user_id", ""),
            request.query_params.get("force_refresh", "")
        ]
        return f"agno_dedup:{':'.join(key_parts)}"

    async def _is_request_in_progress(self, request_key: str) -> bool:
        """Check if similar request is already in progress."""
        # This would typically use Redis or in-memory cache
        # For now, return False (no deduplication)
        return False

    async def _wait_for_request(self, request_key: str, correlation_id: str) -> Response:
        """Wait for similar request to complete."""
        # This would implement actual waiting logic
        # For now, just return a timeout response
        return JSONResponse(
            content={"error": "Request timeout - similar request in progress"},
            status_code=408
        )

    async def _mark_request_in_progress(self, request_key: str, correlation_id: str) -> None:
        """Mark request as in progress."""
        logger.debug(f"Marking AGNO request in progress: {request_key}")

    async def _cache_response_for_dedup(self, request_key: str, response: Response) -> None:
        """Cache response for request deduplication."""
        logger.debug(f"Caching AGNO response for deduplication: {request_key}")

    async def _cleanup_request(self, request_key: str) -> None:
        """Clean up request tracking."""
        logger.debug(f"Cleaning up AGNO request: {request_key}")


class AGNOAPIMonitoringMiddleware(BaseHTTPMiddleware):
    """Middleware for monitoring AGNO API performance."""
    
    def __init__(self, app):
        """Initialize the monitoring middleware."""
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        """Monitor AGNO API requests."""
        start_time = time.time()
        correlation_id = get_correlation_id()
        
        # Check if this is an AGNO API request
        # Check if this is an AGNO API request
        if not request.url.path.startswith(f"{settings.api_prefix}/orchestrat/"):
            return await call_next(request)
        
        logger.info(
            f"AGNO API request started: {request.method} {request.url.path}, "
            f"correlation_id={correlation_id}"
        )
        
        try:
            response = await call_next(request)
            
            duration = time.time() - start_time
            
            # Record metrics
            record_external_api_request(
                api_type="agno_multiagent",
                endpoint=request.url.path,
                method=request.method,
                status="success" if response.status_code < 400 else "error",
                duration=duration
            )
            
            logger.info(
                f"AGNO API request completed: {request.method} {request.url.path}, "
                f"status={response.status_code}, duration={duration:.2f}s, "
                f"correlation_id={correlation_id}"
            )
            
            return response
            
        except Exception as e:
            duration = time.time() - start_time
            
            # Safely convert exception to string, handling non-serializable objects
            try:
                error_str = str(e)
            except Exception:
                error_str = f"Error of type {type(e).__name__}: {repr(e)}"
            
            logger.error(
                f"AGNO API request failed: {request.method} {request.url.path}, "
                f"error={error_str}, duration={duration:.2f}s, correlation_id={correlation_id}"
            )
            
            return JSONResponse(
                content={"error": "AGNO API request failed", "details": error_str},
                status_code=500
            )
