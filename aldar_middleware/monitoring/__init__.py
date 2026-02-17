"""Monitoring package for AIQ Backend."""

from aldar_middleware.monitoring.prometheus import (
    # Middleware
    PrometheusMiddleware,
    
    # Metrics endpoint
    get_metrics,
    
    # HTTP Request metrics
    REQUEST_COUNT,
    REQUEST_DURATION,
    REQUESTS_WITH_CORRELATION_ID,
    
    # Agent call metrics
    AGENT_CALLS_TOTAL,
    AGENT_CALL_DURATION,
    AGENT_ERRORS_TOTAL,
    record_agent_call,
    record_agent_error,
    
    # OpenAI metrics
    OPENAI_TOKENS_USED,
    OPENAI_TOKENS_HISTOGRAM,
    OPENAI_API_CALLS,
    OPENAI_COST_ESTIMATED,
    OPENAI_RESPONSE_TIME,
    record_openai_call,
    estimate_openai_cost,
    
    # MCP metrics
    MCP_REQUESTS_TOTAL,
    MCP_REQUEST_DURATION,
    MCP_CONNECTIONS_ACTIVE,
    MCP_CONNECTION_ERRORS,
    record_mcp_request,
    update_mcp_connections,
    record_mcp_error,
    
    # Multi-agent orchestration metrics
    AGENT_CHAIN_LENGTH,
    MULTI_AGENT_REQUESTS,
    AGENT_ORCHESTRATION_TIME,
    record_agent_chain,
    
    # Database metrics
    DATABASE_OPERATIONS,
    DATABASE_QUERY_DURATION,
    record_database_operation,
    
    # Legacy metrics
    AI_RESPONSE_TIME,
    record_ai_response_time,
)

from aldar_middleware.monitoring.health import (
    health_check,
    readiness_check,
)

from aldar_middleware.monitoring.azure_monitor import (
    initialize_azure_monitoring,
    get_azure_config,
    get_azure_prometheus_client,
    get_azure_grafana_client,
    get_app_insights_client,
    AzureMonitoringConfig,
    AzurePrometheusClient,
    AzureGrafanaClient,
    ApplicationInsightsClient,
)

from aldar_middleware.monitoring.cosmos_logger import (
    initialize_cosmos_logging,
    shutdown_cosmos_logging,
    get_cosmos_handler,
    log_request_response,
    CosmosLoggingConfig,
    CosmosLoggingHandler,
)

from aldar_middleware.monitoring.chat_cosmos_logger import (
    log_chat_session_created,
    log_chat_message,
    log_chat_session_updated,
    log_chat_session_deleted,
    log_chat_favorite_toggled,
    log_chat_analytics_event,
    log_conversation_renamed,
    log_starting_prompt_chosen,
    log_my_agent_knowledge_sources_updated,
    log_message_regenerated,
    log_user_created,
    log_conversation_download,
    log_conversation_share,
)

# Advanced Observability
from aldar_middleware.monitoring.pii_masking import (
    get_pii_masking_service,
    mask_string,
    mask_dict,
    mask_headers,
    PIIMaskingService,
    PIIMaskingConfig,
)

from aldar_middleware.monitoring.distributed_tracing import (
    get_distributed_tracing_service,
    initialize_distributed_tracing,
    DistributedTracingService,
    TraceSamplingConfig,
)

from aldar_middleware.monitoring.database_tracing import (
    get_database_tracer,
    install_database_tracing,
    DatabaseTracer,
)

__all__ = [
    # Middleware
    "PrometheusMiddleware",
    
    # Metrics endpoint
    "get_metrics",
    
    # HTTP Request metrics
    "REQUEST_COUNT",
    "REQUEST_DURATION",
    "REQUESTS_WITH_CORRELATION_ID",
    
    # Agent call metrics
    "AGENT_CALLS_TOTAL",
    "AGENT_CALL_DURATION",
    "AGENT_ERRORS_TOTAL",
    "record_agent_call",
    "record_agent_error",
    
    # OpenAI metrics
    "OPENAI_TOKENS_USED",
    "OPENAI_TOKENS_HISTOGRAM",
    "OPENAI_API_CALLS",
    "OPENAI_COST_ESTIMATED",
    "OPENAI_RESPONSE_TIME",
    "record_openai_call",
    "estimate_openai_cost",
    
    # MCP metrics
    "MCP_REQUESTS_TOTAL",
    "MCP_REQUEST_DURATION",
    "MCP_CONNECTIONS_ACTIVE",
    "MCP_CONNECTION_ERRORS",
    "record_mcp_request",
    "update_mcp_connections",
    "record_mcp_error",
    
    # Multi-agent orchestration metrics
    "AGENT_CHAIN_LENGTH",
    "MULTI_AGENT_REQUESTS",
    "AGENT_ORCHESTRATION_TIME",
    "record_agent_chain",
    
    # Database metrics
    "DATABASE_OPERATIONS",
    "DATABASE_QUERY_DURATION",
    "record_database_operation",
    
    # Legacy metrics
    "AI_RESPONSE_TIME",
    "record_ai_response_time",
    
    # Health checks
    "health_check",
    "readiness_check",
    
    # Azure monitoring
    "initialize_azure_monitoring",
    "get_azure_config",
    "get_azure_prometheus_client",
    "get_azure_grafana_client",
    "get_app_insights_client",
    "AzureMonitoringConfig",
    "AzurePrometheusClient",
    "AzureGrafanaClient",
    "ApplicationInsightsClient",
    
    # Cosmos DB logging
    "initialize_cosmos_logging",
    "shutdown_cosmos_logging",
    "get_cosmos_handler",
    "log_request_response",
    "CosmosLoggingConfig",
    "CosmosLoggingHandler",
    
    # Chat Cosmos DB logging
    "log_chat_session_created",
    "log_chat_message",
    "log_chat_session_updated",
    "log_chat_session_deleted",
    "log_chat_favorite_toggled",
    "log_chat_analytics_event",
    "log_conversation_renamed",
    "log_starting_prompt_chosen",
    "log_my_agent_knowledge_sources_updated",
    "log_message_regenerated",
    "log_user_created",
    "log_conversation_download",
    "log_conversation_share",
    
    # Advanced Observability
    # PII Masking
    "get_pii_masking_service",
    "mask_string",
    "mask_dict",
    "mask_headers",
    "PIIMaskingService",
    "PIIMaskingConfig",
    
    # Distributed Tracing
    "get_distributed_tracing_service",
    "initialize_distributed_tracing",
    "DistributedTracingService",
    "TraceSamplingConfig",
    
    # Database Tracing
    "get_database_tracer",
    "install_database_tracing",
    "DatabaseTracer",
]
