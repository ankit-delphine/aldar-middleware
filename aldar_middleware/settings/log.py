"""Logging configuration."""

import sys
import json
from typing import Dict, Any
from loguru import logger

from aldar_middleware.settings import settings


def correlation_id_filter(record: Dict[str, Any]) -> Dict[str, Any]:
    """Add correlation ID to log record.
    
    This filter injects the correlation ID from context into every log record.
    Checks extra fields first (set by middleware), then falls back to context.
    Also tries to extract correlation ID from the message if not found in context.
    
    Args:
        record: The log record dictionary
        
    Returns:
        Modified log record with correlation_id field
    """
    from aldar_middleware.settings.context import get_correlation_id, get_agent_context, get_user_context
    import re
    
    # First check if correlation_id is already in extra (set by middleware)
    correlation_id = record["extra"].get("correlation_id")
    
    # If not set or is "N/A", try to get from context
    if not correlation_id or correlation_id == "N/A":
        correlation_id = get_correlation_id()
    
    # If still not found, try to extract from message content
    if not correlation_id or correlation_id == "N/A":
        message = record.get("message", "")
        # Look for correlation ID in format [uuid] at the start of message
        uuid_pattern = r'^\[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]'
        match = re.match(uuid_pattern, message, re.IGNORECASE)
        if match:
            correlation_id = match.group(1)
    
    # Set the correlation ID in extra fields
    record["extra"]["correlation_id"] = correlation_id or "N/A"
    
    # Add user context information if available
    user_ctx = get_user_context()
    if user_ctx:
        record["extra"]["user_id"] = user_ctx.user_id or "N/A"
        record["extra"]["username"] = user_ctx.username or "N/A"
        record["extra"]["user_type"] = user_ctx.user_type or "N/A"
        record["extra"]["email"] = user_ctx.email or "N/A"
        record["extra"]["is_authenticated"] = user_ctx.is_authenticated
    else:
        record["extra"]["user_id"] = "N/A"
        record["extra"]["username"] = "N/A"
        record["extra"]["user_type"] = "N/A"
        record["extra"]["email"] = "N/A"
        record["extra"]["is_authenticated"] = False
    
    # Add agent context information if available
    agent_ctx = get_agent_context()
    if agent_ctx and agent_ctx.agent_calls:
        # Get the last agent call (current agent)
        current_agent = agent_ctx.agent_calls[-1]
        record["extra"]["agent_type"] = current_agent.agent_type
        record["extra"]["agent_name"] = current_agent.agent_name
        record["extra"]["agent_method"] = current_agent.method
        record["extra"]["agent_count"] = len(agent_ctx.agent_calls)
    else:
        record["extra"]["agent_type"] = "N/A"
        record["extra"]["agent_name"] = "N/A"
        record["extra"]["agent_method"] = "N/A"
        record["extra"]["agent_count"] = 0
    
    return record


def json_formatter(record: Dict[str, Any]) -> str:
    """Format log record as JSON for production.
    
    Args:
        record: The log record dictionary
        
    Returns:
        JSON formatted log string
    """
    log_entry = {
        "timestamp": record["time"].isoformat(),
        "level": record["level"].name,
        "correlation_id": record["extra"].get("correlation_id", "N/A"),
        "user_id": record["extra"].get("user_id", "N/A"),
        "username": record["extra"].get("username", "N/A"),
        "user_type": record["extra"].get("user_type", "N/A"),
        "email": record["extra"].get("email", "N/A"),
        "is_authenticated": record["extra"].get("is_authenticated", False),
        "agent_type": record["extra"].get("agent_type", "N/A"),
        "agent_name": record["extra"].get("agent_name", "N/A"),
        "agent_method": record["extra"].get("agent_method", "N/A"),
        "agent_count": record["extra"].get("agent_count", 0),
        "module": record["name"],
        "function": record["function"],
        "line": record["line"],
        "message": record["message"],
    }
    
    # Add exception info if present
    if record["exception"]:
        log_entry["exception"] = {
            "type": record["exception"].type.__name__,
            "value": str(record["exception"].value),
            "traceback": record["exception"].traceback,
        }
    
    return json.dumps(log_entry)


def configure_logging():
    """Configure application logging with correlation ID support."""
    # Remove default handler
    logger.remove()
    
    # Development/Staging: Human-readable logs with correlation ID
    if settings.environment.value in ["development", "staging", "testing"]:
        logger.add(
            sys.stdout,
            level=settings.log_level.value,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<yellow>[{extra[correlation_id]}]</yellow> | "
                "<magenta>[{extra[agent_type]}:{extra[agent_name]}]</magenta> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
                "<level>{message}</level>"
            ),
            colorize=True,
            filter=correlation_id_filter,
        )
    
    # Production: JSON structured logs
    if settings.environment.value == "production":
        # Console output as JSON
        logger.add(
            sys.stdout,
            level=settings.log_level.value,
            format=json_formatter,
            serialize=False,
            filter=correlation_id_filter,
        )
        
        # File output as JSON
        logger.add(
            "logs/aldar-middleware.log",
            level=settings.log_level.value,
            format=json_formatter,
            serialize=False,
            rotation="1 day",
            retention="30 days",
            compression="zip",
            filter=correlation_id_filter,
        )
        
        # Error log file
        logger.add(
            "logs/aldar-middleware-error.log",
            level="ERROR",
            format=json_formatter,
            serialize=False,
            rotation="1 day",
            retention="30 days",
            compression="zip",
            filter=correlation_id_filter,
        )
    
    # Configure log levels for external libraries
    logger.disable("httpx")
    logger.disable("httpcore")
    logger.disable("asyncio")
    
    # Suppress non-critical Azure Monitor exporter errors
    # These occur when connection closes before telemetry can be sent (non-critical)
    import logging
    azure_exporter_logger = logging.getLogger("azure.monitor.opentelemetry.exporter.export._base")
    azure_exporter_logger.setLevel(logging.CRITICAL)  # Only show critical errors
    
    # Suppress Azure SDK HTTP logging policy (too verbose)
    # This prevents INFO logs from azure.core.pipeline.policies.http_logging_policy
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
    
    return logger


def log_with_correlation(level: str, message: str, **kwargs):
    """Log a message with correlation ID context.
    
    Args:
        level: Log level (debug, info, warning, error, critical)
        message: Log message
        **kwargs: Additional context to log
    """
    from aldar_middleware.settings.context import get_correlation_id
    
    correlation_id = get_correlation_id()
    log_func = getattr(logger, level.lower())
    
    if correlation_id:
        log_func(f"[{correlation_id}] {message}", **kwargs)
    else:
        log_func(message, **kwargs)
