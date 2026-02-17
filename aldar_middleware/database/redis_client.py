"""Redis client configuration and dependency."""

from typing import Optional
from loguru import logger
import redis.asyncio as redis

from aldar_middleware.settings import settings

# Global Redis client instance
_redis_client: Optional[redis.Redis] = None


def init_redis_client(client: Optional[redis.Redis]) -> None:
    """Initialize the global Redis client.
    
    This is called during application startup (lifespan).
    
    Args:
        client: Redis client instance or None if Redis is unavailable
    """
    global _redis_client
    _redis_client = client
    if client:
        logger.info("✓ Redis client initialized for dependency injection")
    else:
        logger.warning("⚠ Redis client not available - streaming status checks will be disabled")


async def get_redis() -> Optional[redis.Redis]:
    """Get Redis client dependency.
    
    Returns:
        Redis client instance or None if Redis is unavailable
    """
    return _redis_client


def get_redis_sync() -> Optional[redis.Redis]:
    """Get Redis client synchronously (for non-async contexts).
    
    Returns:
        Redis client instance or None if Redis is unavailable
    """
    return _redis_client
