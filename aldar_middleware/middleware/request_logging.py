"""Middleware for logging all HTTP requests and responses."""

import time
import json
from typing import Callable, Optional, Any
from io import BytesIO

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.datastructures import MutableHeaders
from loguru import logger

from aldar_middleware.settings.context import get_correlation_id, get_user_context
from aldar_middleware.settings import settings


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log HTTP requests and responses with full body data."""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and response logging.
        
        Args:
            request: The incoming request
            call_next: The next middleware/handler
            
        Returns:
            The response
        """
        correlation_id = get_correlation_id()
        
        # Capture request data
        request_start_time = time.time()
        
        try:
            # Read request body
            request_body = await self._read_request_body(request)
            
            # Create response
            response = await call_next(request)
            
            # Capture response timing
            duration_ms = (time.time() - request_start_time) * 1000
            
            # Read response body for logging (but pass through original)
            response_body = await self._read_response_body(response)
            
            # Log the request/response with appropriate detail based on config
            # Always log basic info with correlation_id, optionally include bodies
            save_bodies = settings.cosmos_logging_enabled and settings.cosmos_logging_save_request_response
            await self._log_request_response(
                correlation_id=correlation_id,
                method=request.method,
                path=request.url.path,
                query_params=dict(request.query_params) if save_bodies else {},
                request_body=request_body if save_bodies else None,
                response_status=response.status_code,
                response_body=response_body if save_bodies else None,
                duration_ms=duration_ms,
                headers=dict(request.headers) if save_bodies else {},
                save_bodies=save_bodies,
            )

            # Track in Azure Application Insights
            await self._track_in_azure(
                correlation_id=correlation_id,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
            
            return response
            
        except Exception as e:
            duration_ms = (time.time() - request_start_time) * 1000
            
            # Get user context for error logging
            user_ctx = get_user_context()
            user_bind_context = {}
            if user_ctx:
                user_bind_context = {
                    "user_id": user_ctx.user_id or "N/A",
                    "username": user_ctx.username or "N/A",
                    "user_type": user_ctx.user_type or "N/A",
                    "email": user_ctx.email or "N/A",
                    "is_authenticated": user_ctx.is_authenticated
                }
            else:
                user_bind_context = {
                    "user_id": "N/A",
                    "username": "N/A",
                    "user_type": "N/A",
                    "email": "N/A",
                    "is_authenticated": False
                }
            
            # Safely convert exception to string, handling non-serializable objects
            try:
                error_str = str(e)
            except Exception:
                error_str = f"Error of type {type(e).__name__}: {repr(e)}"
            
            # Use extra parameter to avoid format string interpretation issues
            # This prevents KeyError when error_str contains curly braces
            logger.bind(**user_bind_context).error(
                "Error in request logging middleware",
                extra={
                    "correlation_id": correlation_id,
                    "method": request.method,
                    "path": request.url.path,
                    "error_type": type(e).__name__,
                    "error_message": error_str,
                    "error_repr": repr(e) if error_str != repr(e) else None,
                }
            )
            raise
    
    async def _read_request_body(self, request: Request) -> Optional[dict]:
        """Read and parse request body.
        
        Args:
            request: The request object
            
        Returns:
            Parsed request body or None
        """
        try:
            # Do NOT read multipart bodies (e.g., file uploads) or form-data to avoid consuming the stream
            content_type = request.headers.get("content-type", "").lower()
            if content_type.startswith("multipart/form-data"):
                return None
            if content_type.startswith("application/x-www-form-urlencoded"):
                return None

            body = await request.body()
            
            # Re-wrap body for downstream handlers
            async def receive():
                return {"type": "http.request", "body": body}
            request._receive = receive
            
            if not body:
                return None
            
            # Try to parse as JSON
            try:
                return json.loads(body.decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Return as string if not JSON
                return {"raw": body.decode(errors="ignore")[:500]}  # Limit size
                
        except Exception as e:
            logger.debug(f"Could not read request body: {e}")
            return None
    
    async def _read_response_body(self, response: Response) -> Optional[dict]:
        """Read and parse response body while keeping it intact.
        
        Args:
            response: The response object
            
        Returns:
            Parsed response body or None
        """
        try:
            # Read response body
            body_parts = []
            
            async for chunk in response.body_iterator:
                body_parts.append(chunk)
            
            body = b"".join(body_parts)
            
            if not body:
                return None
            
            # Create new iterator for response
            async def new_iterator():
                for chunk in body_parts:
                    yield chunk
            
            response.body_iterator = new_iterator()
            
            # Try to parse as JSON
            try:
                data = json.loads(body.decode())
                # Limit response body size in logs
                if isinstance(data, dict):
                    # Truncate large values
                    return self._truncate_dict(data, max_size=1000)
                return data
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Return size info if not JSON
                return {"raw_size_bytes": len(body)}
                
        except Exception as e:
            logger.debug(f"Could not read response body: {e}")
            return None
    
    async def _log_request_response(
        self,
        correlation_id: str,
        method: str,
        path: str,
        query_params: dict,
        request_body: Optional[dict],
        response_status: int,
        response_body: Optional[dict],
        duration_ms: float,
        headers: dict,
        save_bodies: bool = True,
    ) -> None:
        """Log request and response data.
        
        Args:
            correlation_id: Request correlation ID
            method: HTTP method
            path: Request path
            query_params: Query parameters
            request_body: Request body
            response_status: Response status code
            response_body: Response body
            duration_ms: Request duration
            headers: Request headers
            save_bodies: Whether to include request/response bodies and headers in logs
        """
        # Determine log level based on status code
        if response_status >= 500:
            log_level = "error"
        elif response_status >= 400:
            log_level = "warning"
        else:
            log_level = "info"
        
        # Create log message
        log_message = (
            f"{method} {path} -> {response_status} ({duration_ms:.2f}ms)"
        )
        
        # Get user context for logging
        user_ctx = get_user_context()
        
        # Build user context for logger.bind()
        user_bind_context = {}
        if user_ctx:
            user_bind_context = {
                "user_id": user_ctx.user_id or "N/A",
                "username": user_ctx.username or "N/A",
                "user_type": user_ctx.user_type or "N/A",
                "email": user_ctx.email or "N/A",
                "is_authenticated": user_ctx.is_authenticated
            }
        else:
            user_bind_context = {
                "user_id": "N/A",
                "username": "N/A",
                "user_type": "N/A",
                "email": "N/A",
                "is_authenticated": False
            }
        
        # Log with user context using logger.bind()
        log_func = getattr(logger.bind(**user_bind_context), log_level)
        
        # Build extra dict with basic info (always included)
        extra_dict = {
            "correlation_id": correlation_id,
            "request_method": method,
            "request_path": path,
            "response_status": response_status,
            "duration_ms": duration_ms,
        }
        
        # Add optional body data if configured
        if save_bodies:
            extra_dict.update({
                "request_query": query_params,
                "request_body": request_body,
                "request_headers": self._sanitize_headers(headers),
                "response_body": response_body,
                "request_data": {
                    "method": method,
                    "path": path,
                    "query": query_params,
                    "body": request_body,
                },
                "response_data": {
                    "status_code": response_status,
                    "body": response_body,
                },
            })
        
        # Escape curly braces in log message to prevent format string errors
        safe_log_message = log_message.replace("{", "{{").replace("}", "}}")
        log_func(safe_log_message, extra=extra_dict)
    
    def _sanitize_headers(self, headers: dict) -> dict:
        """Remove sensitive headers from logging.
        
        Args:
            headers: Request headers
            
        Returns:
            Sanitized headers
        """
        sensitive_keys = {
            "authorization",
            "cookie",
            "x-api-key",
            "x-auth-token",
            "secret",
            "password",
            "token",
        }
        
        return {
            k: "[REDACTED]" if k.lower() in sensitive_keys else v
            for k, v in headers.items()
        }
    
    def _truncate_dict(self, data: dict, max_size: int = 1000) -> dict:
        """Truncate large values in a dictionary.
        
        Args:
            data: Dictionary to truncate
            max_size: Maximum value size in characters
            
        Returns:
            Truncated dictionary
        """
        result = {}
        for key, value in data.items():
            if isinstance(value, str) and len(value) > max_size:
                result[key] = value[:max_size] + f"... ({len(value) - max_size} more chars)"
            elif isinstance(value, dict):
                result[key] = self._truncate_dict(value, max_size)
            elif isinstance(value, list) and len(value) > 10:
                result[key] = value[:10] + [f"... and {len(value) - 10} more items"]
            else:
                result[key] = value
        return result
    
    async def _track_in_azure(
        self,
        correlation_id: str,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
    ) -> None:
        """Track HTTP request in Azure Application Insights.
        
        Args:
            correlation_id: Request correlation ID
            method: HTTP method
            path: Request path
            status_code: Response status code
            duration_ms: Request duration in milliseconds
        """
        try:
            from aldar_middleware.monitoring.azure_monitor import get_app_insights_client
            
            client = get_app_insights_client()
            if client:
                client.track_request(
                    method=method,
                    path=path,
                    status_code=status_code,
                    duration=duration_ms / 1000,  # Convert to seconds
                    correlation_id=correlation_id,
                )
        except Exception as e:
            logger.debug(f"Failed to track request in Azure: {e}")