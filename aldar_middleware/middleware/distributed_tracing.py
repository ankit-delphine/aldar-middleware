"""Middleware for distributed tracing and request/response audit logging."""

import time
import json
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse
from loguru import logger

from aldar_middleware.settings import settings
from aldar_middleware.settings.context import set_correlation_id, get_correlation_id, get_agent_context
from aldar_middleware.monitoring.distributed_tracing import get_distributed_tracing_service, DistributedTracingService
from aldar_middleware.monitoring.pii_masking import get_pii_masking_service


class DistributedTracingMiddleware(BaseHTTPMiddleware):
    """Middleware for distributed tracing and audit logging.
    
    Wraps all requests with:
    - Correlation ID generation/propagation
    - Distributed trace initialization
    - Request/response audit logging
    - Performance monitoring
    """
    
    # Paths to skip tracing
    SKIP_PATHS = {
        "/health",
        "/api/v1/health",
        "/api/v1/health/detailed",
        "/api/v1/health/ready",
        "/api/v1/health/live",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
    
    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request with distributed tracing.
        
        Args:
            request: HTTP request
            call_next: Next middleware/handler
            
        Returns:
            HTTP response
        """
        # Extract or generate correlation ID
        correlation_id = request.headers.get(
            "x-correlation-id",
            request.headers.get("x-request-id", None)
        )
        if not correlation_id:
            import uuid
            correlation_id = str(uuid.uuid4())
        
        # Set correlation ID in context
        set_correlation_id(correlation_id)
        
        # Check if we should trace this path
        should_trace = request.url.path not in self.SKIP_PATHS
        
        # Initialize distributed tracing
        tracing_service: Optional[DistributedTracingService] = None
        if should_trace and settings.distributed_tracing_enabled:
            try:
                tracing_service = await get_distributed_tracing_service()
                
                # Extract user ID if available (from JWT or other auth mechanism)
                user_id = self._extract_user_id(request)
                
                # Start trace
                await tracing_service.start_trace(
                    request_method=request.method,
                    request_path=request.url.path,
                    request_endpoint=request.scope.get("path"),
                    user_id=user_id,
                    correlation_id=correlation_id,
                )
            except Exception as e:
                logger.error(f"Failed to initialize distributed tracing: {e}")
                tracing_service = None
        
        # Capture request body
        request_body = None
        if settings.audit_log_enabled and settings.audit_log_include_request_body:
            try:
                request_body = await self._get_request_body(request)
            except Exception as e:
                logger.error(f"Failed to capture request body: {e}")
        
        # Record request start time
        start_time = time.time()
        
        try:
            # Process request
            response = await call_next(request)
        except Exception as e:
            # Handle exception - record trace
            if tracing_service:
                await tracing_service.end_trace(
                    status="error",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
            raise
        
        # Calculate response time
        response_time_ms = int((time.time() - start_time) * 1000)
        
        # Capture response body if small enough
        response_body = None
        if settings.audit_log_enabled and settings.audit_log_include_response_body:
            try:
                response_body = await self._get_response_body(response)
            except Exception as e:
                logger.error(f"Failed to capture response body: {e}")
        
        # Add trace context headers to response
        if tracing_service and tracing_service.current_trace:
            trace_context = tracing_service.get_current_trace_context()
            for header, value in trace_context.items():
                response.headers[f"X-{header}"] = value
            
            # End trace
            await tracing_service.end_trace(
                status="success",
                http_status_code=response.status_code,
            )
        
        # Add correlation ID to response headers
        response.headers["X-Correlation-ID"] = correlation_id
        
        # Log request/response audit if enabled
        if settings.audit_log_enabled:
            self._log_audit(
                correlation_id=correlation_id,
                request=request,
                response=response,
                request_body=request_body,
                response_body=response_body,
                response_time_ms=response_time_ms,
            )
        
        return response
    
    def _extract_user_id(self, request: Request) -> Optional[str]:
        """Extract user ID from request.
        
        Args:
            request: HTTP request
            
        Returns:
            User ID if available, None otherwise
        """
        try:
            # Try to get from scope (set by auth middleware)
            if "user" in request.scope:
                user = request.scope.get("user")
                if user:
                    return str(user.get("id") or user.get("sub"))
            
            # Try to get from token claims (if available)
            if hasattr(request.state, "user"):
                return str(request.state.user.id)
        except Exception:
            pass
        
        return None
    
    async def _get_request_body(self, request: Request) -> Optional[str]:
        """Capture request body.
        
        Args:
            request: HTTP request
            
        Returns:
            Request body as string, truncated if necessary
        """
        try:
            body = await request.body()
            
            # Check size limit
            max_size_bytes = settings.audit_log_max_body_size_kb * 1024
            if len(body) > max_size_bytes:
                return None  # Too large, skip
            
            # Try to decode and mask PII
            try:
                body_str = body.decode("utf-8")
                masking_service = get_pii_masking_service()
                return masking_service.mask_string(body_str)
            except UnicodeDecodeError:
                # Binary data, return truncated hex
                return body[:100].hex()
        except Exception as e:
            logger.error(f"Error capturing request body: {e}")
        
        return None
    
    async def _get_response_body(self, response: Response) -> Optional[str]:
        """Capture response body.
        
        Args:
            response: HTTP response
            
        Returns:
            Response body as string, truncated if necessary
        """
        try:
            # For streaming responses, we can't capture the body
            if isinstance(response, StreamingResponse):
                return None
            
            # Get body from response
            if hasattr(response, "body"):
                body = response.body
            else:
                return None
            
            # Check size limit
            max_size_bytes = settings.audit_log_max_body_size_kb * 1024
            if len(body) > max_size_bytes:
                return None  # Too large, skip
            
            # Try to decode and mask PII
            try:
                body_str = body.decode("utf-8")
                masking_service = get_pii_masking_service()
                return masking_service.mask_string(body_str)
            except UnicodeDecodeError:
                # Binary data, return truncated hex
                return body[:100].hex()
        except Exception as e:
            logger.error(f"Error capturing response body: {e}")
        
        return None
    
    def _log_audit(
        self,
        correlation_id: str,
        request: Request,
        response: Response,
        request_body: Optional[str],
        response_body: Optional[str],
        response_time_ms: int,
    ) -> None:
        """Log request/response audit.
        
        Args:
            correlation_id: Correlation ID
            request: HTTP request
            response: HTTP response
            request_body: Captured request body
            response_body: Captured response body
            response_time_ms: Response time in milliseconds
        """
        try:
            # Extract user ID
            user_id = self._extract_user_id(request)
            
            # Get agent context for stats
            agent_context = get_agent_context()
            agent_stats = agent_context.get_agent_statistics() if agent_context else None
            
            # Build audit log
            audit_log = {
                "type": "audit",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "correlation_id": correlation_id,
                "request": {
                    "method": request.method,
                    "path": request.url.path,
                    "query": str(request.url.query) if request.url.query else None,
                    "body": request_body,
                    "client_ip": self._get_client_ip(request),
                },
                "response": {
                    "status_code": response.status_code,
                    "body": response_body,
                },
                "performance": {
                    "response_time_ms": response_time_ms,
                    "agent_stats": agent_stats,
                },
                "user_id": user_id,
            }
            
            # Mask headers if needed
            masking_service = get_pii_masking_service()
            request_headers = dict(request.headers) if request.headers else {}
            audit_log["request"]["headers"] = masking_service.mask_headers(request_headers)
            
            # Log at appropriate level based on status code
            if response.status_code >= 500:
                logger.error(f"Request audit: {audit_log}")
            elif response.status_code >= 400:
                logger.warning(f"Request audit: {audit_log}")
            else:
                logger.info(f"Request audit: {audit_log}")
        
        except Exception as e:
            logger.error(f"Error logging audit: {e}")
    
    def _get_client_ip(self, request: Request) -> Optional[str]:
        """Extract client IP address from request.
        
        Args:
            request: HTTP request
            
        Returns:
            Client IP address if available
        """
        # Check for X-Forwarded-For header (proxy)
        if "x-forwarded-for" in request.headers:
            return request.headers["x-forwarded-for"].split(",")[0].strip()
        
        # Check for X-Real-IP header
        if "x-real-ip" in request.headers:
            return request.headers["x-real-ip"]
        
        # Fall back to client from scope
        if request.client:
            return request.client.host
        
        return None