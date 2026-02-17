"""Application settings and configuration."""

import enum
import json
from pathlib import Path
from tempfile import gettempdir
from typing import Any, List, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from yarl import URL

TEMP_DIR = Path(gettempdir())


def _strip_inline_comment(value: Any) -> Any:
    """Strip inline # comment from env value (e.g. Azure App Settings from .env)."""
    if isinstance(value, str) and "#" in value:
        return value.split("#")[0].strip()
    return value


class Environment(str, enum.Enum):
    """Environment types."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TESTING = "testing"


class LogLevel(str, enum.Enum):
    """Log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ALDAR_",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # Environment
    environment: Environment = Field(default=Environment.DEVELOPMENT)
    debug: bool = Field(default=False)
    log_level: LogLevel = Field(default=LogLevel.INFO)

    # Application
    app_name: str = Field(default="Aldar Middleware")
    app_version: str = Field(default="0.1.0")
    api_prefix: str = Field(default="/api/v1")
    docs_url: Optional[str] = Field(default="/docs")
    redoc_url: Optional[str] = Field(default="/redoc")
    base_url: Optional[str] = Field(default=None, description="Base URL for the application (e.g., https://api.example.com)")

    # Server
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    workers_count: int = Field(default=1)
    reload: bool = Field(default=False)

    # Database
    db_host: str = Field(default="localhost")
    db_port: int = Field(default=5432)
    db_user: str = Field(default="aldar")
    db_pass: str = Field(default="aldar")
    db_base: str = Field(default="aldar")
    db_echo: bool = Field(default=False)
    db_url: Optional[str] = Field(default=None)

    # Redis
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6379)
    redis_password: Optional[str] = Field(default=None)
    redis_db: int = Field(default=0)
    redis_url: Optional[str] = Field(default=None)
    # Enable or disable Redis connection attempts. Set to false for local
    # development when Redis is a Private Link resource and unreachable.
    redis_enabled: bool = Field(default=True)

    # Azure AD OAuth2
    azure_tenant_id: Optional[str] = Field(default=None)
    azure_client_id: Optional[str] = Field(default=None)
    azure_client_secret: Optional[str] = Field(default=None)
    azure_authority: Optional[str] = Field(default=None)
    jwt_secret_key: str = Field(
        default="",  # No default - MUST be set via environment variable
        description="JWT secret key for signing tokens. MUST be at least 32 characters. "
                    "Generate with: python -c 'import secrets; print(secrets.token_hex(32))'"
    )
    jwt_algorithm: str = Field(default="HS256")
    jwt_access_token_expire_minutes: int = Field(default=30)
    
    # Azure AD Admin Groups
    admin_group_ids: Optional[str] = Field(
        default=None,
        description="Comma-separated list or JSON array of Azure AD group IDs that grant admin privileges. "
                    "Users belonging to any of these groups will have is_admin=True. "
                    "Format: 'group-id-1,group-id-2' or '[\"group-id-1\",\"group-id-2\"]'"
    )

    # Azure AD OBO (On-Behalf-Of) Flow Configuration
    azure_obo_target_client_id: Optional[str] = Field(
        default=None,
        description="Target API client ID for OBO token exchange",
    )
    azure_obo_api_base_url: Optional[str] = Field(
        default=None,
        description="Base URL of the target API for OBO flow",
    )
    azure_obo_redirect_uri: Optional[str] = Field(
        default=None,
        description="Redirect URI for OBO OAuth flow",
    )
    
    # ARIA Token Configuration (separate OBO target for ARIA)
    azure_aria_target_client_id: Optional[str] = Field(
        default=None,
        description="ARIA API client ID for OBO token exchange",
    )

    # Service Bus
    service_bus_connection_string: Optional[str] = Field(default=None)
    service_bus_queue_name: str = Field(default="aiq-queue")

    # Azure Web PubSub
    web_pubsub_connection_string: Optional[str] = Field(default=None)

    # Event Grid
    eventgrid_topic_name: Optional[str] = Field(default=None)
    eventgrid_topic_resource_id: Optional[str] = Field(default=None)
    eventgrid_topic_endpoint: Optional[str] = Field(default=None)
    eventgrid_topic_key: Optional[str] = Field(default=None)
    eventgrid_publish_timeout: float = Field(default=5.0)

    # Celery
    celery_broker_url: Optional[str] = Field(default=None)
    celery_result_backend: Optional[str] = Field(default=None)
    
    # Agent Health Check Configuration
    agent_health_check_interval_minutes: int = Field(
        default=30,
        description="Interval in minutes for periodic agent health checks. Set to 1 for testing."
    )

    # MCP (Model Context Protocol)
    mcp_server_url: Optional[str] = Field(default=None)
    mcp_api_key: Optional[str] = Field(default=None)

    # Monitoring
    prometheus_enabled: bool = Field(default=True)
    prometheus_port: int = Field(default=9090)
    grafana_enabled: bool = Field(default=True)
    grafana_port: int = Field(default=3000)
    
    # Azure Managed Prometheus
    azure_prometheus_enabled: bool = Field(default=False)
    azure_prometheus_endpoint: Optional[str] = Field(default=None)
    azure_prometheus_workspace_id: Optional[str] = Field(default=None)
    
    # Azure Managed Grafana
    azure_grafana_enabled: bool = Field(default=False)
    azure_grafana_endpoint: Optional[str] = Field(default=None)
    azure_grafana_api_key: Optional[str] = Field(default=None)
    
    # Application Insights
    app_insights_connection_string: Optional[str] = Field(default=None)
    app_insights_enabled: bool = Field(default=False)

    # Azure Metrics Ingestion (DCR - Data Collection Rules)
    azure_metrics_ingestion_enabled: bool = Field(default=False)
    azure_metrics_ingestion_endpoint: Optional[str] = Field(default=None)
    azure_metrics_dcr_rule_id: Optional[str] = Field(default=None)
    azure_metrics_dcr_stream_name: str = Field(default="Custom-Metrics")
    azure_metrics_push_interval: int = Field(default=60)  # seconds between metric pushes
    
    # Sentry
    sentry_dsn: Optional[str] = Field(default=None)
    sentry_sample_rate: float = Field(default=1.0)

    # CORS
    cors_origins: Optional[str] = Field(default="*")
    cors_allow_credentials: bool = Field(default=True)

    # OpenAI
    openai_api_key: Optional[str] = Field(default=None)
    openai_model: str = Field(default="gpt-4")

    # Azure Storage
    azure_storage_connection_string: Optional[str] = Field(default=None)
    azure_storage_account_name: Optional[str] = Field(default=None)
    azure_storage_account_key: Optional[str] = Field(default=None)
    azure_storage_container_name: str = Field(default="aiq-storage")

    # Feedback System
    feedback_blob_container_name: str = Field(default="feedback")
    feedback_max_file_size_mb: int = Field(default=5)
    feedback_allowed_extensions: List[str] = Field(
        default_factory=lambda: [
            "png",
            "PNG",
            "jpg",
            "JPEG",
            "jpeg",
            "JPEG",
            "pdf",
            "PDF",
            "pptx",
            "PPTX",
            "docx",
            "DOCX",
        ]
    )
    feedback_sas_token_expiry_hours: int = Field(default=24)
    feedback_analytics_cache_ttl_seconds: int = Field(default=300)  # 5 minutes
    feedback_soft_delete_retention_days: int = Field(default=30)

    # Rate Limiting
    rate_limit_requests: int = Field(default=100)
    rate_limit_window: int = Field(default=60)  # seconds

    # Chat Configuration
    max_chat_history: int = Field(default=100)
    chat_timeout: int = Field(default=300)  # seconds
    max_query_length: int = Field(default=4000)
    max_attachments_per_session: int = Field(default=5)
    max_attachment_size_bytes: int = Field(default=50 * 1024 * 1024)  # 50 MB
    session_ttl_hours: int = Field(default=24)
    chat_attachment_sas_token_expiry_hours: float = Field(default=0.5)  # 30 minutes
    # 1 hour for shared PDFs (configurable for testing)
    shared_pdf_sas_token_expiry_hours: float = Field(default=1.0)

    # Cosmos DB Configuration
    cosmos_endpoint: Optional[str] = Field(default=None)
    cosmos_key: Optional[str] = Field(default=None)
    cosmos_database_name: str = Field(default="aldar_memory")
    cosmos_container_name: str = Field(default="conversations")
    cosmos_consistency_level: str = Field(default="Session")
    
    # Cosmos DB Logging Configuration
    cosmos_logging_enabled: bool = Field(default=False)
    cosmos_logging_database_name: str = Field(default="aiq-database")
    cosmos_logging_container_name: str = Field(default="aiq-log")  # For admin/application logs
    cosmos_logging_user_logs_container_name: str = Field(default="aiq-user-logs")  # For user activity logs (faster queries)
    cosmos_logging_throughput: int = Field(default=400)
    cosmos_logging_batch_size: int = Field(default=50)
    cosmos_logging_flush_interval: int = Field(default=5)  # seconds
    cosmos_logging_save_request_response: bool = Field(default=True)  # Save HTTP request/response bodies
    cosmos_log_verbose: bool = Field(default=False)  # Show verbose Cosmos DB logs in terminal

    # Advanced Observability
    # Distributed Tracing Configuration
    distributed_tracing_enabled: bool = Field(default=True)  # Enable OpenTelemetry + Application Insights
    trace_sample_rate: str = Field(default="full")  # "full" (100%), "partial" (10%), "minimal" (1%)
    
    # PII Masking Configuration
    pii_masking_enabled: bool = Field(default=True)  # Comprehensive PII masking in logs/traces
    pii_mask_emails: bool = Field(default=True)
    pii_mask_phone_numbers: bool = Field(default=True)
    pii_mask_credit_cards: bool = Field(default=True)
    pii_mask_tokens: bool = Field(default=True)
    pii_mask_api_keys: bool = Field(default=True)
    
    # Request/Response Audit Configuration
    audit_log_enabled: bool = Field(default=True)
    audit_log_include_request_body: bool = Field(default=True)
    audit_log_include_response_body: bool = Field(default=True)
    audit_log_max_body_size_kb: int = Field(default=10)  # Max request/response body size to log (KB)
    
    # Database Query Tracing
    trace_database_queries: bool = Field(default=True)
    trace_query_slow_threshold_ms: int = Field(default=100)  # Log queries slower than this

    # Query Processing Configuration
    query_cache_ttl: int = Field(default=3600)  # seconds
    session_cache_ttl: int = Field(default=86400)  # seconds (24 hours)
    max_context_messages: int = Field(default=50)
    enable_long_term_memory: bool = Field(default=True)

    # AGNO API Configuration (Data Team API)
    agno_api_enabled: bool = Field(default=True)
    agno_base_url: str = Field(default="")
    agno_api_timeout: int = Field(default=10)
    # Optional settings below - defaults work fine, no need to set in .env
    agno_api_cache_ttl: int = Field(default=3600)
    agno_api_key: Optional[str] = Field(default=None)
    agno_api_max_retries: int = Field(default=3)
    agno_api_retry_delay: float = Field(default=1.0)
    agno_api_rate_limit_per_minute: int = Field(default=60)
    agno_api_enable_caching: bool = Field(default=True)
    agno_api_enable_metrics: bool = Field(default=True)

    # Azure Key Vault Configuration
    azure_key_vault_enabled: bool = Field(default=False, description="Enable Azure Key Vault integration")
    azure_key_vault_url: Optional[str] = Field(default=None, description="Azure Key Vault URL (e.g., https://your-vault.vault.azure.net/)")
    azure_key_vault_use_managed_identity: bool = Field(default=True, description="Use Managed Identity for authentication (True for AKS, False for local dev)")
    azure_key_vault_secret_mapping: Optional[str] = Field(
        default=None,
        description="Comma-separated mapping of env vars to Key Vault secrets. Format: ENV_VAR1=SECRET_NAME1,ENV_VAR2=SECRET_NAME2"
    )

    # Kubernetes Deployment Version Configuration
    k8s_namespace_frontend: str = Field(default="middleware-ui", description="Kubernetes namespace for frontend deployment")
    k8s_namespace_backend: str = Field(default="middleware-main", description="Kubernetes namespace for backend deployment")
    k8s_namespace_data: str = Field(default="middleware-ai", description="Kubernetes namespace for data/AI deployment")
    k8s_deployment_frontend: str = Field(default="aiq-frontend", description="Kubernetes deployment name for frontend")
    k8s_deployment_backend: str = Field(default="aldar-middleware-main", description="Kubernetes deployment name for backend")
    k8s_deployment_data: str = Field(default="aiq-genai-orchestration", description="Kubernetes deployment name for data/AI")

    @property
    def db_url_property(self) -> URL:
        """Build database URL from components."""
        if self.db_url:
            return URL(self.db_url)
        
        # Determine database name based on environment
        # In testing environment, use aldar_test if db_base is still 'aldar' (default)
        db_name = self.db_base
        if self.environment == Environment.TESTING and self.db_base == "aldar":
            db_name = "aldar_test"
        
        # Check if it's an Azure PostgreSQL database
        if "postgres.database.azure.com" in self.db_host:
            # Azure PostgreSQL requires SSL - use asyncpg with SSL parameters
            return URL.build(
                scheme="postgresql+asyncpg",
                host=self.db_host,
                port=self.db_port,
                user=self.db_user,
                password=self.db_pass,
                path=f"/{db_name}",
                query={"ssl": "require"},
            )
        
        # Local PostgreSQL - use asyncpg for better performance
        return URL.build(
            scheme="postgresql+asyncpg",
            host=self.db_host,
            port=self.db_port,
            user=self.db_user,
            password=self.db_pass,
            path=f"/{db_name}",
        )

    @property
    def redis_url_property(self) -> URL:
        """Build Redis URL from components."""
        if self.redis_url:
            return URL(self.redis_url)
        
        # Check if it's an Azure Redis Cache
        if "redis.cache.windows.net" in self.redis_host:
            # Azure Redis Cache requires SSL - use rediss scheme
            return URL.build(
                scheme="rediss",  # Use rediss for SSL
                host=self.redis_host,
                port=self.redis_port,
                user=None,
                password=self.redis_password,
                path=f"/{self.redis_db}" if self.redis_db else "",
            )
        
        # Local Redis - use standard redis scheme
        return URL.build(
            scheme="redis",
            host=self.redis_host,
            port=self.redis_port,
            user=None,
            password=self.redis_password,
            path=f"/{self.redis_db}" if self.redis_db else "",
        )

    @property
    def celery_broker_url_property(self) -> str:
        """Build Celery broker URL."""
        # Only use explicit broker URL if it's a Redis URL, otherwise fall back to Redis
        if self.celery_broker_url and self.celery_broker_url.startswith(('redis://', 'rediss://')):
            return self.celery_broker_url
        # Ignore Azure Service Bus URLs and use Redis instead
        return str(self.redis_url_property)

    @property
    def celery_result_backend_property(self) -> str:
        """Build Celery result backend URL."""
        # Only use explicit backend URL if it's a Redis URL, otherwise fall back to Redis
        if self.celery_result_backend and self.celery_result_backend.startswith(('redis://', 'rediss://')):
            return self.celery_result_backend
        # Ignore Azure Service Bus URLs and use Redis instead
        return str(self.redis_url_property)

    @model_validator(mode="before")
    @classmethod
    def strip_inline_comments_from_env(cls, data: Any) -> Any:
        """Strip inline # comments from all string values (Azure App Settings from .env)."""
        if isinstance(data, dict):
            return {k: _strip_inline_comment(v) if isinstance(v, str) else v for k, v in data.items()}
        return data

    @field_validator('jwt_secret_key')
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        """Validate JWT secret key strength."""
        # Known weak secrets that should be rejected
        weak_secrets = [
            "your-secret-key",
            "your-secret-key-change-in-production",
            "secret",
            "changeme",
            "password",
            "jwt-secret",
            "",
        ]
        
        if v in weak_secrets:
            raise ValueError(
                "JWT_SECRET_KEY cannot be a known weak value. "
                "Generate a secure secret with: "
                "python -c 'import secrets; print(secrets.token_hex(32))'"
            )
        
        if len(v) < 32:
            raise ValueError(
                f"JWT_SECRET_KEY must be at least 32 characters long (got {len(v)}). "
                "Generate a secure secret with: "
                "python -c 'import secrets; print(secrets.token_hex(32))'"
            )
        
        return v

    @property
    def admin_group_ids_list(self) -> List[str]:
        """Parse admin_group_ids setting into a list of group IDs.

        Supports both JSON array format and comma-separated format.
        Returns empty list if not configured.
        """
        if not self.admin_group_ids:
            return []

        admin_group_ids_str = self.admin_group_ids.strip()
        if not admin_group_ids_str:
            return []

        # Try to parse as JSON array first
        try:
            parsed = json.loads(admin_group_ids_str)
            if isinstance(parsed, list):
                # Filter out empty strings and return as list of strings
                return [
                    str(gid).strip() for gid in parsed if str(gid).strip()
                ]
        except (json.JSONDecodeError, ValueError):
            pass

        # If not JSON, try comma-separated format
        return [
            gid.strip()
            for gid in admin_group_ids_str.split(",")
            if gid.strip()
        ]


# Global settings instance
settings = Settings()
