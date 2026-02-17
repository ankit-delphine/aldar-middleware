"""FastAPI application factory."""

import logging
from importlib import metadata
from pathlib import Path

import sentry_sdk
from loguru import logger
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from fastapi.openapi.utils import get_openapi
from fastapi.responses import UJSONResponse, Response
from pydantic import ValidationError

try:
    import orjson
    ORJSON_AVAILABLE = True
except ImportError:
    ORJSON_AVAILABLE = False
    import json
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from aldar_middleware.settings import settings
from aldar_middleware.routes.router import api_router
from aldar_middleware.lifespan import lifespan_setup
from aldar_middleware.monitoring.prometheus import PrometheusMiddleware
from aldar_middleware.monitoring.health import router as health_router
from aldar_middleware.admin.routes import router as admin_router
from aldar_middleware.routes.rbac import router as rbac_router
from aldar_middleware.routes.admin_config import router as admin_config_router
from aldar_middleware.middleware import CorrelationIdMiddleware
from aldar_middleware.middleware.request_logging import RequestLoggingMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from aldar_middleware.middleware.external_api_cache import (
    AGNOAPICacheMiddleware,
    AGNOAPIOptimizationMiddleware,
    AGNOAPIMonitoringMiddleware
)
from aldar_middleware.settings.context import get_correlation_id


class ORJSONResponse(Response):
    """Custom JSON response using orjson that doesn't escape forward slashes in URLs."""

    media_type = "application/json"

    def render(self, content: any) -> bytes:  # noqa: ANN001
        if ORJSON_AVAILABLE:
            return orjson.dumps(
                content,
                option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_SERIALIZE_DATACLASS,
            )
        else:
            # Fallback to standard json (though this will still escape slashes)
            # This should not happen if orjson is properly installed
            return json.dumps(content, ensure_ascii=False).encode('utf-8')


def get_app() -> FastAPI:
    """
    Get FastAPI application.

    This is the main constructor of an application.

    :return: application.
    """
    # Configure logging
    logging.basicConfig(level=settings.log_level.value)
    
    # Configure Sentry
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            traces_sample_rate=settings.sentry_sample_rate,
            environment=settings.environment,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                LoggingIntegration(
                    level=logging.getLevelName(settings.log_level.value),
                    event_level=logging.ERROR,
                ),
                SqlalchemyIntegration(),
            ],
        )
    
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan_setup,
        docs_url=settings.docs_url,
        redoc_url=settings.redoc_url,
        openapi_url=f"{settings.api_prefix}/openapi.json",
        default_response_class=ORJSONResponse,
    )

    # Add Request logging middleware FIRST (for Cosmos DB logging)
    # This needs to run AFTER CorrelationIdMiddleware sets user context
    if settings.cosmos_logging_enabled:
        app.add_middleware(RequestLoggingMiddleware)
    
    # Add Correlation ID middleware (MUST be before Prometheus middleware)
    # This sets user context that RequestLoggingMiddleware needs
    app.add_middleware(CorrelationIdMiddleware)
    
    # Add Prometheus middleware
    if settings.prometheus_enabled:
        app.add_middleware(PrometheusMiddleware)
    
    # Add External API middleware
    app.add_middleware(AGNOAPICacheMiddleware, cache_ttl=3600)
    app.add_middleware(AGNOAPIOptimizationMiddleware)
    app.add_middleware(AGNOAPIMonitoringMiddleware)

    # Configure CORS middleware
    # SECURITY: Never use wildcard origins with credentials enabled
    cors_origins_raw = settings.cors_origins or "*"
    cors_origins = [origin.strip() for origin in cors_origins_raw.split(",")] if cors_origins_raw != "*" else ["*"]
    
    # Security check: warn if using wildcard with credentials
    if "*" in cors_origins and settings.cors_allow_credentials:
        logger.warning(
            "SECURITY WARNING: CORS is configured with allow_origins=['*'] and "
            "allow_credentials=True. This is insecure and allows any website to make "
            "authenticated requests. Consider explicitly whitelisting allowed origins."
        )
        # In production, this should be an error, but we'll allow it with a warning
        # to avoid breaking existing deployments. Remove wildcard in production.
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=[
            "Accept",
            "Accept-Language",
            "Content-Language",
            "Content-Type",
            "Authorization",
            "X-Requested-With",
            "X-CSRF-Token",
        ],
    )

    # Add security headers middleware
    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        """Middleware to add security headers to all responses."""
        
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            
            # Add security headers
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-XSS-Protection"] = "1; mode=block"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            
            # Add HSTS header for HTTPS connections
            if request.url.scheme == "https":
                response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            
            # Remove server version disclosure
            # MutableHeaders doesn't have pop(), use del with existence check
            if "server" in response.headers:
                del response.headers["server"]
            
            return response
    
    app.add_middleware(SecurityHeadersMiddleware)
    
    # SECURITY: Add request size limit middleware to prevent DoS attacks
    MAX_REQUEST_SIZE = getattr(settings, 'max_attachment_size_bytes', 10 * 1024 * 1024)  # Default 10MB
    
    class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
        """Middleware to limit request body size."""
        
        async def dispatch(self, request, call_next):
            # Check Content-Length header if present
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    size = int(content_length)
                    if size > MAX_REQUEST_SIZE:
                        return Response(
                            content='{"detail": "Request body too large"}',
                            status_code=413,
                            media_type="application/json"
                        )
                except ValueError:
                    pass  # Invalid content-length, let it proceed
            
            response = await call_next(request)
            return response
    
    app.add_middleware(RequestSizeLimitMiddleware)

    # Add HTTPBearer security scheme to OpenAPI
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        
        # Tags to hide from Swagger documentation
        hidden_tags = {
            "mcp",
            "monitoring-demo",
            "routing",
            "workflows",
            "quotas",
            "remediation",
            "Orchestration",
        }
        
        # Filter out paths with hidden tags
        paths_to_remove = []
        methods_to_remove = {}  # {path: [method_names]}
        
        for path, methods in openapi_schema["paths"].items():
            methods_to_remove_for_path = []
            for method_name, method_info in methods.items():
                if isinstance(method_info, dict) and "tags" in method_info:
                    # Check if any tag matches hidden tags (case-insensitive)
                    method_tags = method_info.get("tags", [])
                    if any(tag.lower() in {ht.lower() for ht in hidden_tags} for tag in method_tags):
                        methods_to_remove_for_path.append(method_name)
            
            if methods_to_remove_for_path:
                if len(methods_to_remove_for_path) == len(methods):
                    # All methods in this path should be removed, remove entire path
                    paths_to_remove.append(path)
                else:
                    # Store methods to remove for this path
                    methods_to_remove[path] = methods_to_remove_for_path
        
        # Remove methods from paths
        for path, method_names in methods_to_remove.items():
            for method_name in method_names:
                if path in openapi_schema["paths"] and method_name in openapi_schema["paths"][path]:
                    del openapi_schema["paths"][path][method_name]
        
        # Remove entire paths if all methods were removed
        for path in paths_to_remove:
            if path in openapi_schema["paths"]:
                del openapi_schema["paths"][path]
        
        # Add security scheme
        openapi_schema["components"]["securitySchemes"] = {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            },
        }
        
        # Apply security to all /api paths and /admin paths except public ones
        public_paths = [
            f"{settings.api_prefix}/auth/azure-ad/login",
            f"{settings.api_prefix}/auth/azure-ad/callback",
            f"{settings.api_prefix}/auth/users/{{user_id}}/profile-photo",  # Profile photos are public
        ]
        
        for path, methods in openapi_schema["paths"].items():
            if (path.startswith("/api") or path.startswith("/admin")) and path not in public_paths:
                for method in methods.values():
                    method["security"] = [{"BearerAuth": []}]
            elif path in public_paths:
                # Ensure public endpoints don't have security
                for method in methods.values():
                    if "security" in method:
                        del method["security"]
        
        # Set tag order - attachments first, and filter out hidden tags
        if "tags" in openapi_schema:
            # Extract all tags and sort with "attachments" first
            all_tags = openapi_schema["tags"] if openapi_schema.get("tags") else []
            tag_names = [tag["name"] if isinstance(tag, dict) else tag for tag in all_tags]
            
            # Create ordered tags list with "attachments" first, excluding hidden tags
            ordered_tags = []
            if "attachments" in tag_names:
                ordered_tags.append({"name": "attachments", "description": "File upload and attachment management"})
            for tag_name in sorted(tag_names):
                if tag_name != "attachments":
                    # Skip hidden tags (case-insensitive comparison)
                    if not any(tag_name.lower() == ht.lower() for ht in hidden_tags):
                        tag_info = next((t for t in all_tags if (t["name"] if isinstance(t, dict) else t) == tag_name), None)
                        if tag_info:
                            ordered_tags.append(tag_info if isinstance(tag_info, dict) else {"name": tag_name})
            
            openapi_schema["tags"] = ordered_tags
        
        app.openapi_schema = openapi_schema
        return app.openapi_schema
    app.openapi = custom_openapi

    # Include routers
    app.include_router(router=api_router, prefix=settings.api_prefix)
    app.include_router(router=health_router, prefix=settings.api_prefix)
    app.include_router(router=admin_router, prefix=settings.api_prefix)
    app.include_router(router=admin_config_router, prefix=settings.api_prefix)
    # Include RBAC router directly to avoid tag inheritance from admin router
    app.include_router(
        router=rbac_router,
        prefix=f"{settings.api_prefix}/admin",
        tags=["RBAC"],
    )

    # Register global exception handlers with correlation ID
    def _sanitize_for_json(obj):
        """Recursively convert bytes and non-serializable values to JSON-safe types."""
        try:
            if isinstance(obj, bytes):
                return obj.decode(errors="ignore")
            if isinstance(obj, dict):
                return {k: _sanitize_for_json(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple, set)):
                t = type(obj)
                return t(_sanitize_for_json(v) for v in obj)
            # Pydantic/Starlette error objects may carry non-serializable fields
            # Fallback: let ujson handle primitives; stringify anything else
            return obj
        except Exception:
            return str(obj)
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request, exc):
        correlation_id = get_correlation_id()
        content = _sanitize_for_json({
            "detail": exc.detail,
            "correlation_id": correlation_id
        })
        return ORJSONResponse(
            status_code=exc.status_code,
            content=content
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request, exc):
        correlation_id = get_correlation_id()
        # Sanitize errors to handle non-serializable values
        errors = exc.errors()
        sanitized_errors = []
        for error in errors:
            sanitized_error = {}
            for key, value in error.items():
                try:
                    # Try to serialize the value
                    import json
                    json.dumps(value)
                    sanitized_error[key] = value
                except (TypeError, ValueError):
                    # If not serializable, convert to string
                    sanitized_error[key] = str(value)
            sanitized_errors.append(sanitized_error)
        
        content = _sanitize_for_json({
            "detail": sanitized_errors,
            "correlation_id": correlation_id
        })
        return ORJSONResponse(
            status_code=422,
            content=content
        )

    @app.exception_handler(ValidationError)
    async def pydantic_validation_exception_handler(request, exc):
        correlation_id = get_correlation_id()
        content = _sanitize_for_json({
            "detail": str(exc),
            "correlation_id": correlation_id
        })
        return ORJSONResponse(
            status_code=422,
            content=content
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request, exc):
        correlation_id = get_correlation_id()
        logging.error(f"Unhandled exception: {str(exc)}", exc_info=True)
        content = _sanitize_for_json({
            "detail": "Internal server error",
            "correlation_id": correlation_id
        })
        return ORJSONResponse(
            status_code=500,
            content=content
        )

    return app


# Create app instance for direct uvicorn usage
app = get_app()
