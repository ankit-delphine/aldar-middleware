"""Application lifespan management."""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from loguru import logger
from sqlalchemy import text

from aldar_middleware.database.base import engine
from aldar_middleware.settings import settings
from aldar_middleware.monitoring.azure_monitor import initialize_azure_monitoring
from aldar_middleware.monitoring.azure_metrics_ingestion import (
    initialize_metrics_ingestion,
    shutdown_metrics_ingestion,
)
from aldar_middleware.monitoring.metrics_forwarder import (
    initialize_prometheus_forwarder,
    shutdown_prometheus_forwarder,
)
from aldar_middleware.monitoring.cosmos_logger import (
    initialize_cosmos_logging,
    shutdown_cosmos_logging,
)
from aldar_middleware.services.user_logs_service import user_logs_service
from aldar_middleware.orchestration.azure_key_vault import (
    get_key_vault_service,
    load_secrets_from_key_vault,
)


async def _initialize_caches(redis_client, redis_available: bool) -> None:
    """
    Initialize OBO token cache and agent available cache.
    
    Args:
        redis_client: Redis client instance (or None if Redis unavailable)
        redis_available: Whether Redis is available (for logging purposes)
    """
    # Initialize OBO token cache
    try:
        from aldar_middleware.auth.obo_utils import init_obo_token_cache
        init_obo_token_cache(redis_client=redis_client)
        if redis_available:
            logger.info("✓ OBO token cache initialized with Redis")
        else:
            logger.info("✓ OBO token cache initialized with in-memory storage (Redis unavailable)")
    except Exception as cache_error:
        logger.warning(f"Failed to initialize OBO token cache: {cache_error}")

    # Initialize agent available cache
    try:
        from aldar_middleware.services.agent_available_cache import init_agent_available_cache
        await init_agent_available_cache(redis_client=redis_client)
        if redis_available:
            logger.info("✓ Agent available cache initialized with Redis")
        else:
            logger.info("⚠ Agent available cache disabled (Redis not available)")
    except Exception as cache_error:
        logger.warning(f"Failed to initialize agent available cache: {cache_error}")

    # Initialize user memory cache
    try:
        from aldar_middleware.services.user_memory_cache import init_user_memory_cache
        init_user_memory_cache(redis_client=redis_client)
        if redis_available:
            logger.info("✓ User memory cache initialized with Redis")
        else:
            logger.info("⚠ User memory cache disabled (Redis not available)")
    except Exception as cache_error:
        logger.warning(f"Failed to initialize user memory cache: {cache_error}")


@asynccontextmanager
async def lifespan_setup(app) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager.

    Handles startup and shutdown events.
    """
    # Startup
    logger.info("Starting AIQ Backend application...")

    # Initialize Azure Key Vault and load secrets
    try:
        key_vault_loaded = load_secrets_from_key_vault()
        if key_vault_loaded:
            logger.info("Secrets loaded from Azure Key Vault")
        else:
            logger.debug("Azure Key Vault not configured or no secrets to load")
    except Exception as e:
        logger.warning(f"Failed to load secrets from Azure Key Vault: {e}")
        # Don't raise - Key Vault is optional

    # Initialize Key Vault service (for programmatic access)
    try:
        key_vault_service = get_key_vault_service()
        if key_vault_service:
            logger.info("Azure Key Vault service initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize Azure Key Vault service: {e}")
        # Don't raise - Key Vault is optional

    # Initialize Cosmos DB logging (admin logs)
    try:
        cosmos_initialized = await initialize_cosmos_logging()
        if cosmos_initialized:
            logger.info("Cosmos DB logging initialized")
        else:
            logger.debug("Cosmos DB logging not enabled")
    except Exception as e:
        logger.warning(f"Failed to initialize Cosmos DB logging: {e}")
        # Don't raise - Cosmos DB logging is optional
    
    # Initialize User Logs Service (user logs collection)
    try:
        user_logs_initialized = await user_logs_service.initialize()
        if user_logs_initialized:
            logger.info("User logs service initialized successfully")
        else:
            logger.debug("User logs service not enabled")
    except Exception as e:
        logger.warning(f"Failed to initialize user logs service: {e}")
        # Don't raise - User logs service is optional

    # Initialize Azure monitoring
    try:
        initialize_azure_monitoring()
        logger.info("Azure monitoring initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize Azure monitoring: {e}")
        # Don't raise - Azure monitoring is optional

    # Initialize Azure Metrics Ingestion
    try:
        initialize_metrics_ingestion()
        logger.info("Azure Metrics Ingestion initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize Azure Metrics Ingestion: {e}")
        # Don't raise - metrics ingestion is optional

    # Initialize Prometheus to Azure Metrics Forwarder
    try:
        initialize_prometheus_forwarder(collection_interval=settings.azure_metrics_push_interval)
        logger.info("Prometheus to Azure metrics forwarder initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize Prometheus forwarder: {e}")
        # Don't raise - forwarder is optional

    # Initialize database
    try:
        # Test database connection
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: sync_conn.execute(text("SELECT 1")))
        logger.info("Database connection established")
    except Exception as e:
        logger.warning(f"Failed to connect to database: {e}")
        logger.warning("Continuing without database - some features may be unavailable")
        # Don't raise - database connection is optional for basic functionality

    # Initialize Redis connection
    redis_client = None
    # If Redis connection attempts are disabled via settings, skip trying
    if not settings.redis_enabled:
        logger.info("Redis connection attempts disabled via settings (ALDAR_REDIS_ENABLED=false). Using in-memory cache.")
        try:
            from aldar_middleware.auth.obo_utils import init_obo_token_cache
            init_obo_token_cache(redis_client=None)
            logger.info("✓ OBO token cache initialized with in-memory storage (Redis disabled)")
        except Exception as cache_error:
            logger.warning(f"Failed to initialize OBO token cache: {cache_error}")
        
        # Initialize Redis client as None for dependency injection
        from aldar_middleware.database.redis_client import init_redis_client
        init_redis_client(None)
    else:
        try:
            import redis.asyncio as redis
            import ssl

            # Get Redis URL - prefer explicit redis_url over building from components
            if settings.redis_url:
                redis_url = settings.redis_url
                logger.info(f"Using explicit Redis URL from settings")
            else:
                redis_url = str(settings.redis_url_property)
                logger.info(f"Built Redis URL from components")
        
            # Mask password in logs for security
            redis_url_log = redis_url
            if '@' in redis_url:
                # Mask password: rediss://:****@host:port/db
                parts = redis_url.split('@')
                if ':' in parts[0]:
                    scheme_auth = parts[0].split('://')
                    if len(scheme_auth) > 1 and ':' in scheme_auth[1]:
                        auth_part = scheme_auth[1].split(':')
                        if len(auth_part) > 1:
                            redis_url_log = f"{scheme_auth[0]}://:{'*' * min(len(auth_part[1]), 8)}@{parts[1]}"

            logger.info(f"Attempting to connect to Redis: {redis_url_log}")

            # Check if it's Azure Redis Cache
            is_azure_redis = "redis.cache.windows.net" in redis_url or "rediss://" in redis_url.lower()

            if is_azure_redis:
                # Azure Redis Cache requires SSL parameters
                logger.info("Detected Azure Redis Cache - configuring SSL/TLS parameters")
                # Parse URL to extract components
                from urllib.parse import urlparse
                parsed = urlparse(redis_url)

                # Build SSL context for Azure Redis Cache
                # SECURITY: Enable SSL verification for production
                # Azure Redis Cache uses valid certificates, so we should verify them
                from aldar_middleware.settings.settings import Environment
                ssl_context = ssl.create_default_context()
                # Only disable verification in development if explicitly configured
                if settings.environment == Environment.DEVELOPMENT and settings.debug:
                    logger.warning("SSL verification disabled for Redis (development mode only)")
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE
                else:
                    # Production: Enable full SSL verification
                    ssl_context.check_hostname = True
                    ssl_context.verify_mode = ssl.CERT_REQUIRED

                # Build connection parameters
                connection_params = {
                    "host": parsed.hostname or settings.redis_host,
                    "port": parsed.port or settings.redis_port,
                    "db": int(parsed.path.lstrip('/')) if parsed.path else settings.redis_db,
                    "password": parsed.password or settings.redis_password,
                    "ssl": ssl_context,  # Use SSL context instead of boolean
                    "decode_responses": False,
                    "socket_connect_timeout": 15,  # 15 seconds connection timeout
                    "socket_timeout": 15,  # 15 seconds socket timeout
                    "retry_on_timeout": True,
                    "health_check_interval": 30
                }

                logger.info(f"Connecting to Azure Redis: {connection_params['host']}:{connection_params['port']}")
                redis_client = redis.Redis(**connection_params)
            else:
                # Standard Redis connection
                logger.info("Using standard Redis connection")
                redis_client = redis.from_url(
                    redis_url,
                    socket_connect_timeout=15,
                    socket_timeout=15,
                    retry_on_timeout=True,
                    health_check_interval=30
                )

            # Test connection with timeout
            logger.info("Testing Redis connection with ping...")
            try:
                ping_result = await asyncio.wait_for(redis_client.ping(), timeout=15.0)
                if ping_result:
                    logger.info("✓ Redis connection established successfully")
                else:
                    raise Exception("Redis ping returned False")
            except asyncio.TimeoutError:
                raise Exception("Redis ping timed out after 15 seconds")

            # Initialize caches with Redis
            await _initialize_caches(redis_client=redis_client, redis_available=True)
            
            # Initialize Redis client for dependency injection
            from aldar_middleware.database.redis_client import init_redis_client
            init_redis_client(redis_client)
        except redis.ConnectionError as e:
            logger.error(f"❌ Redis connection error: {e}")
            logger.warning("Continuing without Redis - some features may be unavailable")
            logger.info("💡 Check: Redis host, port, password, and network connectivity")
            await _initialize_caches(redis_client=None, redis_available=False)
            
            # Initialize Redis client as None for dependency injection
            from aldar_middleware.database.redis_client import init_redis_client
            init_redis_client(None)
        except redis.TimeoutError as e:
            logger.error(f"❌ Redis connection timeout: {e}")
            logger.warning("Continuing without Redis - some features may be unavailable")
            logger.info("💡 Check: Network connectivity, firewall rules, and Redis server status")
            await _initialize_caches(redis_client=None, redis_available=False)
            
            # Initialize Redis client as None for dependency injection
            from aldar_middleware.database.redis_client import init_redis_client
            init_redis_client(None)
        except Exception as e:
            logger.error(f"❌ Failed to connect to Redis: {type(e).__name__}: {e}")
            import traceback
            logger.debug(f"Redis connection error traceback: {traceback.format_exc()}")
            logger.warning("Continuing without Redis - some features may be unavailable")
            await _initialize_caches(redis_client=None, redis_available=False)
            
            # Initialize Redis client as None for dependency injection
            from aldar_middleware.database.redis_client import init_redis_client
            init_redis_client(None)

    # Initialize OpenAI client
    try:
        from aldar_middleware.services.ai_service import AIService
        ai_service = AIService()
        logger.info("OpenAI service initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize OpenAI service: {e}")
        logger.warning("Continuing without OpenAI - AI features may be unavailable")
        # Don't raise - OpenAI service is optional for basic functionality

    logger.info("AIQ Backend application started successfully")

    yield

    # Shutdown
    logger.info("Shutting down AIQ Backend application...")
    
    # Close Redis connection
    if redis_client:
        try:
            await redis_client.aclose()
            logger.info("Redis connection closed")
        except Exception as e:
            logger.warning(f"Error closing Redis connection: {e}")

    # Shutdown Prometheus to Azure Metrics Forwarder
    try:
        shutdown_prometheus_forwarder()
        logger.info("Prometheus metrics forwarder shut down")
    except Exception as e:
        logger.warning(f"Error shutting down Prometheus forwarder: {e}")

    # Shutdown Azure Metrics Ingestion
    try:
        shutdown_metrics_ingestion()
        logger.info("Azure Metrics Ingestion shut down")
    except Exception as e:
        logger.warning(f"Error shutting down Azure Metrics Ingestion: {e}")

    # Shutdown Cosmos DB logging
    try:
        shutdown_cosmos_logging()
        logger.info("Cosmos DB logging shut down")
    except Exception as e:
        logger.warning(f"Error shutting down Cosmos DB logging: {e}")

    # Close database connections
    try:
        await engine.dispose()
        logger.info("Database connections closed")
    except Exception as e:
        logger.error(f"Error closing database connections: {e}")

    logger.info("AIQ Backend application shutdown complete")
