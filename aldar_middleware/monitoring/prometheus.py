"""Prometheus monitoring configuration."""

from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST, CollectorRegistry
from fastapi import Response
from fastapi.routing import APIRoute
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse
import time
from typing import Callable, Optional, Dict, Any

from aldar_middleware.settings.context import get_correlation_id

# Dynamic metrics registry for generic metric recording
_DYNAMIC_METRICS: Dict[str, Gauge] = {}

# ========================================
# HTTP Request Metrics
# ========================================
REQUEST_COUNT = Counter(
    "aiq_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"]
)

REQUEST_DURATION = Histogram(
    "aiq_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0)
)

REQUESTS_WITH_CORRELATION_ID = Counter(
    "aiq_requests_with_correlation_id_total",
    "Total requests tracked with correlation ID",
    ["has_correlation_id"]
)

# ========================================
# External API Metrics
# ========================================
EXTERNAL_API_REQUESTS = Counter(
    "aiq_external_api_requests_total",
    "Total external API requests",
    ["api_type", "endpoint", "method", "status"]
)

EXTERNAL_API_DURATION = Histogram(
    "aiq_external_api_request_duration_seconds",
    "External API request duration in seconds",
    ["api_type", "endpoint", "method"],
    buckets=(0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0)
)

EXTERNAL_API_CACHE_HITS = Counter(
    "aiq_external_api_cache_hits_total",
    "Total external API cache hits",
    ["api_type", "endpoint"]
)

EXTERNAL_API_CACHE_MISSES = Counter(
    "aiq_external_api_cache_misses_total",
    "Total external API cache misses",
    ["api_type", "endpoint"]
)

EXTERNAL_API_ERRORS = Counter(
    "aiq_external_api_errors_total",
    "Total external API errors",
    ["api_type", "endpoint", "error_type"]
)

# ========================================
# Agent Call Metrics (Core)
# ========================================
AGENT_CALLS_TOTAL = Counter(
    "aiq_agent_calls_total",
    "Total agent calls by type, name, method, and status",
    ["agent_type", "agent_name", "method", "status"]
)

AGENT_CALL_DURATION = Histogram(
    "aiq_agent_call_duration_seconds",
    "Agent call duration in seconds",
    ["agent_type", "agent_name", "method"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0)
)

AGENT_ERRORS_TOTAL = Counter(
    "aiq_agent_errors_total",
    "Total agent errors by type, name, and error type",
    ["agent_type", "agent_name", "error_type"]
)

# ========================================
# OpenAI-Specific Metrics
# ========================================
OPENAI_TOKENS_USED = Counter(
    "aiq_openai_tokens_used_total",
    "Total tokens used by OpenAI API",
    ["model", "token_type"]  # token_type: prompt, completion, total
)

OPENAI_TOKENS_HISTOGRAM = Histogram(
    "aiq_openai_tokens_per_request",
    "Token usage per OpenAI request",
    ["model", "token_type"],
    buckets=(10, 50, 100, 250, 500, 1000, 2000, 4000, 8000, 16000)
)

OPENAI_API_CALLS = Counter(
    "aiq_openai_api_calls_total",
    "Total OpenAI API calls",
    ["model", "method", "status"]
)

OPENAI_COST_ESTIMATED = Counter(
    "aiq_openai_cost_estimated_usd",
    "Estimated OpenAI API cost in USD",
    ["model"]
)

OPENAI_RESPONSE_TIME = Histogram(
    "aiq_openai_response_duration_seconds",
    "OpenAI API response time in seconds",
    ["model"],
    buckets=(0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 30.0, 60.0)
)

# ========================================
# MCP-Specific Metrics
# ========================================
MCP_REQUESTS_TOTAL = Counter(
    "aiq_mcp_requests_total",
    "Total MCP requests",
    ["connection_id", "method", "status"]
)

MCP_REQUEST_DURATION = Histogram(
    "aiq_mcp_request_duration_seconds",
    "MCP request duration in seconds",
    ["connection_id", "method"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
)

MCP_CONNECTIONS_ACTIVE = Gauge(
    "aiq_mcp_connections_active",
    "Number of active MCP connections"
)

MCP_CONNECTION_ERRORS = Counter(
    "aiq_mcp_connection_errors_total",
    "Total MCP connection errors",
    ["connection_id", "error_type"]
)

# ========================================
# Multi-Agent Orchestration Metrics
# ========================================
AGENT_CHAIN_LENGTH = Histogram(
    "aiq_agent_chain_length",
    "Number of agents called per request",
    buckets=(1, 2, 3, 4, 5, 7, 10, 15, 20)
)

MULTI_AGENT_REQUESTS = Counter(
    "aiq_multi_agent_requests_total",
    "Requests involving multiple agents",
    ["agent_count_range"]  # 1, 2-3, 4-5, 6+
)

AGENT_ORCHESTRATION_TIME = Histogram(
    "aiq_agent_orchestration_duration_seconds",
    "Total time spent orchestrating multiple agents",
    buckets=(1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0)
)

# ========================================
# Database Metrics
# ========================================
DATABASE_OPERATIONS = Counter(
    "aiq_database_operations_total",
    "Total database operations",
    ["operation", "table"]
)

DATABASE_QUERY_DURATION = Histogram(
    "aiq_database_query_duration_seconds",
    "Database query duration in seconds",
    ["operation", "table"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0)
)

# ========================================
# Feedback System Metrics
# ========================================
FEEDBACK_CREATED = Counter(
    "aiq_feedback_created_total",
    "Total feedback entries created",
    ["entity_type", "rating"]
)

FEEDBACK_FAILED = Counter(
    "aiq_feedback_failed_total",
    "Total feedback creation failures",
    ["reason"]
)

FEEDBACK_FILES_UPLOADED = Counter(
    "aiq_feedback_files_uploaded_total",
    "Total files uploaded with feedback",
    ["status"]
)

FEEDBACK_FILE_SIZE = Histogram(
    "aiq_feedback_file_size_bytes",
    "Feedback file size in bytes",
    buckets=(1024, 10240, 102400, 1048576, 5242880, 10485760)  # 1KB to 10MB
)

FEEDBACK_API_DURATION = Histogram(
    "aiq_feedback_api_duration_seconds",
    "Feedback API endpoint duration in seconds",
    ["endpoint", "method"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
)

FEEDBACK_ANALYTICS_QUERY_DURATION = Histogram(
    "aiq_feedback_analytics_query_duration_seconds",
    "Feedback analytics query duration in seconds",
    ["query_type"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
)

# ========================================
# Legacy Metrics (kept for compatibility)
# ========================================
AI_RESPONSE_TIME = Histogram(
    "aiq_ai_response_duration_seconds",
    "AI response generation time in seconds (legacy)",
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0)
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Middleware for Prometheus metrics collection."""

    async def dispatch(self, request: Request, call_next: Callable) -> StarletteResponse:
        """Process request and collect metrics."""
        start_time = time.time()
        
        # Get route information
        route = request.scope.get("route")
        if route and isinstance(route, APIRoute):
            endpoint = route.path
            method = request.method
        else:
            endpoint = request.url.path
            method = request.method
        
        # Track correlation ID presence
        correlation_id = get_correlation_id()
        REQUESTS_WITH_CORRELATION_ID.labels(
            has_correlation_id="true" if correlation_id else "false"
        ).inc()
        
        # Process request
        response = await call_next(request)
        
        # Calculate duration
        duration = time.time() - start_time
        
        # Record metrics
        REQUEST_COUNT.labels(
            method=method,
            endpoint=endpoint,
            status_code=response.status_code
        ).inc()
        
        REQUEST_DURATION.labels(
            method=method,
            endpoint=endpoint
        ).observe(duration)
        
        return response


# ========================================
# Metrics Endpoint
# ========================================
def get_metrics() -> Response:
    """Get Prometheus metrics."""
    return Response(
        generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


# ========================================
# Agent Call Metrics Helpers
# ========================================
def record_agent_call(
    agent_type: str,
    agent_name: str,
    method: str,
    duration: float,
    status: str = "success"
):
    """Record agent call metrics."""
    AGENT_CALLS_TOTAL.labels(
        agent_type=agent_type,
        agent_name=agent_name,
        method=method,
        status=status
    ).inc()
    
    AGENT_CALL_DURATION.labels(
        agent_type=agent_type,
        agent_name=agent_name,
        method=method
    ).observe(duration)


def record_agent_error(agent_type: str, agent_name: str, error_type: str):
    """Record agent error metric."""
    AGENT_ERRORS_TOTAL.labels(
        agent_type=agent_type,
        agent_name=agent_name,
        error_type=error_type
    ).inc()


# ========================================
# OpenAI Metrics Helpers
# ========================================
def record_openai_call(
    model: str,
    method: str,
    status: str,
    duration: float,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None
):
    """Record OpenAI API call metrics."""
    # Record API call
    OPENAI_API_CALLS.labels(
        model=model,
        method=method,
        status=status
    ).inc()
    
    # Record response time
    OPENAI_RESPONSE_TIME.labels(model=model).observe(duration)
    
    # Record token usage if available
    if prompt_tokens is not None:
        OPENAI_TOKENS_USED.labels(
            model=model,
            token_type="prompt"
        ).inc(prompt_tokens)
        
        OPENAI_TOKENS_HISTOGRAM.labels(
            model=model,
            token_type="prompt"
        ).observe(prompt_tokens)
    
    if completion_tokens is not None:
        OPENAI_TOKENS_USED.labels(
            model=model,
            token_type="completion"
        ).inc(completion_tokens)
        
        OPENAI_TOKENS_HISTOGRAM.labels(
            model=model,
            token_type="completion"
        ).observe(completion_tokens)
    
    if total_tokens is not None:
        OPENAI_TOKENS_USED.labels(
            model=model,
            token_type="total"
        ).inc(total_tokens)
        
        OPENAI_TOKENS_HISTOGRAM.labels(
            model=model,
            token_type="total"
        ).observe(total_tokens)
        
        # Estimate cost (approximate pricing as of 2024)
        cost = estimate_openai_cost(model, prompt_tokens or 0, completion_tokens or 0)
        if cost > 0:
            OPENAI_COST_ESTIMATED.labels(model=model).inc(cost)


def estimate_openai_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate OpenAI API cost in USD."""
    # Pricing per 1K tokens (approximate, update as needed)
    pricing = {
        "gpt-4": {"prompt": 0.03, "completion": 0.06},
        "gpt-4-turbo": {"prompt": 0.01, "completion": 0.03},
        "gpt-4o": {"prompt": 0.005, "completion": 0.015},
        "gpt-3.5-turbo": {"prompt": 0.0005, "completion": 0.0015},
    }
    
    # Match model to pricing (handle variants)
    model_lower = model.lower()
    for key, prices in pricing.items():
        if key in model_lower:
            prompt_cost = (prompt_tokens / 1000) * prices["prompt"]
            completion_cost = (completion_tokens / 1000) * prices["completion"]
            return prompt_cost + completion_cost
    
    # Default fallback pricing
    return ((prompt_tokens + completion_tokens) / 1000) * 0.002


# ========================================
# MCP Metrics Helpers
# ========================================
def record_mcp_request(connection_id: str, method: str, status: str, duration: Optional[float] = None):
    """Record MCP request metric."""
    MCP_REQUESTS_TOTAL.labels(
        connection_id=connection_id,
        method=method,
        status=status
    ).inc()
    
    if duration is not None:
        MCP_REQUEST_DURATION.labels(
            connection_id=connection_id,
            method=method
        ).observe(duration)


def update_mcp_connections(count: int):
    """Update active MCP connections metric."""
    MCP_CONNECTIONS_ACTIVE.set(count)


def record_mcp_error(connection_id: str, error_type: str):
    """Record MCP connection error metric."""
    MCP_CONNECTION_ERRORS.labels(
        connection_id=connection_id,
        error_type=error_type
    ).inc()


# ========================================
# External API Metrics Helpers
# ========================================
def record_external_api_request(api_type: str, endpoint: str, method: str, status: str, duration: Optional[float] = None):
    """Record external API request metric."""
    EXTERNAL_API_REQUESTS.labels(
        api_type=api_type,
        endpoint=endpoint,
        method=method,
        status=status
    ).inc()
    
    if duration is not None:
        EXTERNAL_API_DURATION.labels(
            api_type=api_type,
            endpoint=endpoint,
            method=method
        ).observe(duration)


def record_external_api_cache_hit(api_type: str, endpoint: str, duration: Optional[float] = None):
    """Record external API cache hit metric."""
    EXTERNAL_API_CACHE_HITS.labels(
        api_type=api_type,
        endpoint=endpoint
    ).inc()


def record_external_api_cache_miss(api_type: str, endpoint: str, duration: Optional[float] = None):
    """Record external API cache miss metric."""
    EXTERNAL_API_CACHE_MISSES.labels(
        api_type=api_type,
        endpoint=endpoint
    ).inc()


def record_external_api_error(api_type: str, endpoint: str, method: str, error: str, duration: Optional[float] = None):
    """Record external API error metric."""
    EXTERNAL_API_ERRORS.labels(
        api_type=api_type,
        endpoint=endpoint,
        error_type=type(error).__name__
    ).inc()
    
    # Also record as failed request
    record_external_api_request(api_type, endpoint, method, "error", duration)


# ========================================
# Multi-Agent Orchestration Metrics Helpers
# ========================================
def record_agent_chain(agent_count: int, total_duration: float):
    """Record multi-agent orchestration metrics."""
    AGENT_CHAIN_LENGTH.observe(agent_count)
    
    # Categorize agent count
    if agent_count == 1:
        count_range = "1"
    elif agent_count <= 3:
        count_range = "2-3"
    elif agent_count <= 5:
        count_range = "4-5"
    else:
        count_range = "6+"
    
    MULTI_AGENT_REQUESTS.labels(agent_count_range=count_range).inc()
    
    if total_duration > 0:
        AGENT_ORCHESTRATION_TIME.observe(total_duration)


# ========================================
# Database Metrics Helpers
# ========================================
def record_database_operation(operation: str, table: str, duration: Optional[float] = None):
    """Record database operation metric."""
    DATABASE_OPERATIONS.labels(
        operation=operation,
        table=table
    ).inc()
    
    if duration is not None:
        DATABASE_QUERY_DURATION.labels(
            operation=operation,
            table=table
        ).observe(duration)


# ========================================
# Generic Metrics Recording (for dynamic metrics)
# ========================================
def record_metric(metric_name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
    """
    Record a generic metric dynamically.
    
    Args:
        metric_name: Name of the metric (e.g., "agent_health_check_duration_ms")
        value: Numeric value to record
        labels: Optional dictionary of label keys and values
    """
    # Create a metric key that includes label keys (to handle different label sets)
    label_keys = tuple(sorted(labels.keys())) if labels else ()
    metric_key = (metric_name, label_keys)
    
    # Create the metric if it doesn't exist
    if metric_key not in _DYNAMIC_METRICS:
        label_names = list(label_keys) if label_keys else []
        _DYNAMIC_METRICS[metric_key] = Gauge(
            metric_name,
            f"Dynamic metric: {metric_name}",
            labelnames=label_names
        )
    
    # Record the metric value
    gauge = _DYNAMIC_METRICS[metric_key]
    if labels:
        gauge.labels(**labels).set(value)
    else:
        gauge.set(value)


# ========================================
# Legacy Helpers (kept for compatibility)
# ========================================
def record_ai_response_time(duration: float):
    """Record AI response time metric (legacy)."""
    AI_RESPONSE_TIME.observe(duration)
