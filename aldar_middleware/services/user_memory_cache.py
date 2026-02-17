"""
User Memory Cache Service

Redis-based caching for /api/v1/api/memory/ endpoint per user.
Cache is invalidated when user modifies memories (extract, update, delete).

Usage:
    from aldar_middleware.services.user_memory_cache import get_user_memory_cache

    cache = get_user_memory_cache()

    # Try to get from cache
    response = await cache.get_cached_memories(user_id)
    if response is None:
        # Cache miss - fetch from API
        response = await fetch_memories_from_api(...)
        # Store in cache
        await cache.set_cached_memories(user_id, response, ttl=300)
    
    # Invalidate cache when memory is modified
    await cache.invalidate_user_cache(user_id)
"""

from typing import Dict, Optional, Any, List
import json
import logging

logger = logging.getLogger(__name__)


class UserMemoryCache:
    """
    Caching layer for /api/v1/api/memory/ endpoint.

    Features:
    - User-specific caching (per-user memory lists)
    - Per-user invalidation (only invalidates specific user's cache)
    - Graceful Redis fallback to disabled state
    - Short TTL for memory freshness (5 minutes default)

    The cache stores each user's memory list separately and supports
    individual user cache invalidation when they modify their memories.
    """

    CACHE_PREFIX = "user_memory"
    DEFAULT_TTL = 300  # 5 minutes

    def __init__(self, redis_client: Optional[Any] = None, enabled: bool = True):
        """
        Initialize user memory cache.

        Args:
            redis_client: Redis client instance (e.g., from redis.asyncio)
            enabled: Enable/disable caching (default: True)
        """
        self.redis = redis_client
        self.enabled = enabled and redis_client is not None

        if not self.enabled and redis_client is None:
            logger.info("User memory caching disabled - Redis client not available")

    def _make_key(self, user_id: str) -> str:
        """
        Generate cache key for user's memories.

        Args:
            user_id: User UUID or identifier

        Returns:
            Cache key string in format: user_memory:{user_id}
        """
        return f"{self.CACHE_PREFIX}:{user_id}"

    async def get_cached_memories(self, user_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached memory list for a user.

        Args:
            user_id: User UUID or identifier

        Returns:
            Cached memory list if found, None if cache miss or disabled
        """
        if not self.enabled:
            return None

        try:
            key = self._make_key(user_id)
            cached = await self.redis.get(key)

            if cached:
                logger.debug(f"Cache HIT: user_memory for user {user_id}")
                # Parse JSON string back to list
                if isinstance(cached, bytes):
                    cached = cached.decode('utf-8')
                return json.loads(cached)

            logger.debug(f"Cache MISS: user_memory for user {user_id}")
            return None

        except Exception as e:
            logger.warning(f"Cache get error for user {user_id}: {e}")
            return None

    async def set_cached_memories(
        self,
        user_id: str,
        memories: List[Dict[str, Any]],
        ttl: int = None
    ) -> bool:
        """
        Cache memory list for a user.

        Args:
            user_id: User UUID or identifier
            memories: List of memory dicts to cache
            ttl: Time to live in seconds (default: 300 = 5 minutes)

        Returns:
            True if cached successfully, False otherwise
        """
        if not self.enabled:
            return False

        if ttl is None:
            ttl = self.DEFAULT_TTL

        try:
            key = self._make_key(user_id)
            # Serialize to JSON string
            cache_data = json.dumps(memories)
            await self.redis.set(key, cache_data, ex=ttl)
            logger.debug(f"Cached user_memory for user {user_id} with TTL={ttl}s")
            return True

        except Exception as e:
            logger.warning(f"Cache set error for user {user_id}: {e}")
            return False

    async def invalidate_user_cache(self, user_id: str) -> bool:
        """
        Invalidate cache for a specific user.

        Args:
            user_id: User UUID or identifier

        Returns:
            True if invalidated successfully, False otherwise
        """
        if not self.enabled:
            return False

        try:
            key = self._make_key(user_id)
            deleted = await self.redis.delete(key)
            if deleted:
                logger.info(f"✅ Invalidated user_memory cache for user {user_id}")
            else:
                logger.debug(f"No cache to invalidate for user {user_id}")
            return True

        except Exception as e:
            logger.warning(f"Cache invalidation error for user {user_id}: {e}")
            return False


# Global singleton instance
_user_memory_cache: Optional[UserMemoryCache] = None


def get_user_memory_cache() -> Optional[UserMemoryCache]:
    """
    Get the global UserMemoryCache instance.

    Returns:
        UserMemoryCache instance or None if not initialized
    """
    return _user_memory_cache


def init_user_memory_cache(redis_client: Optional[Any] = None) -> UserMemoryCache:
    """
    Initialize the global UserMemoryCache instance.

    Args:
        redis_client: Redis client instance

    Returns:
        Initialized UserMemoryCache instance
    """
    global _user_memory_cache
    _user_memory_cache = UserMemoryCache(redis_client=redis_client)
    return _user_memory_cache
