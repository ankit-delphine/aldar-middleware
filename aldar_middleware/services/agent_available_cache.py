"""
Agent Available Cache Service

Redis-based caching for /api/v1/agent/available endpoint to improve performance.
Uses version-based global invalidation for efficient cache management.

Usage:
    from aldar_middleware.services.agent_available_cache import get_agent_available_cache

    cache = get_agent_available_cache()

    # Try to get from cache
    response = await cache.get_cached_response(user_id, category, limit, offset)
    if response is None:
        # Cache miss - fetch from database
        response = await fetch_agents_from_db(...)
        # Store in cache
        await cache.set_cached_response(user_id, category, limit, offset, response, ttl=900)
"""

from typing import Dict, Optional, Any
import json
import logging

logger = logging.getLogger(__name__)


class AgentAvailableCache:
    """
    Caching layer for /api/v1/agent/available endpoint.

    Features:
    - User-specific caching (RBAC-aware)
    - Global version-based invalidation
    - Graceful Redis fallback to disabled state
    - Query parameter awareness (category, limit, offset)

    The cache uses a global version number to invalidate all user caches
    efficiently. When an agent is created/updated/deleted, the version
    is incremented, making all existing cache entries unreachable.
    """

    GLOBAL_VERSION_KEY = "agent_available:global_version"

    def __init__(self, redis_client: Optional[Any] = None, enabled: bool = True):
        """
        Initialize agent available cache.

        Args:
            redis_client: Redis client instance (e.g., from redis.asyncio)
            enabled: Enable/disable caching (default: True)
        """
        self.redis = redis_client
        self.enabled = enabled and redis_client is not None

        if not self.enabled and redis_client is None:
            logger.info("Agent available caching disabled - Redis client not available")

    def _make_key(
        self,
        user_id: str,
        category: Optional[str],
        limit: int,
        offset: int,
        version: int
    ) -> str:
        """
        Generate cache key with namespace and version.

        Args:
            user_id: User UUID
            category: Category filter (or "ALL" if None)
            limit: Pagination limit
            offset: Pagination offset
            version: Global cache version

        Returns:
            Cache key string in format: agent_available:{user_id}:{category}:{limit}:{offset}:{version}
        """
        category_key = category if category else "ALL"
        return f"agent_available:{user_id}:{category_key}:{limit}:{offset}:{version}"

    async def get_current_version(self) -> int:
        """
        Get current global cache version.

        Returns:
            Current version number (default: 1 if not set)
        """
        if not self.enabled:
            return 1

        try:
            version = await self.redis.get(self.GLOBAL_VERSION_KEY)
            if version is None:
                # Initialize version to 1 if not set
                await self.redis.set(self.GLOBAL_VERSION_KEY, 1)
                return 1
            return int(version)
        except Exception as e:
            logger.warning(f"Failed to get cache version: {e}")
            return 1

    async def increment_version(self) -> int:
        """
        Increment global cache version (invalidates all caches).

        Returns:
            New version number
        """
        if not self.enabled:
            return 1

        try:
            new_version = await self.redis.incr(self.GLOBAL_VERSION_KEY)
            logger.info(f"Incremented global cache version to {new_version}")
            return new_version
        except Exception as e:
            logger.error(f"Failed to increment cache version: {e}")
            return 1

    async def get_cached_response(
        self,
        user_id: str,
        category: Optional[str],
        limit: int,
        offset: int
    ) -> Optional[Dict[str, Any]]:
        """
        Get cached agent list response for a user.

        Args:
            user_id: User UUID
            category: Category filter (or None for all)
            limit: Pagination limit
            offset: Pagination offset

        Returns:
            Cached response dict if found, None if cache miss or disabled
        """
        if not self.enabled:
            return None

        try:
            # Get current version
            version = await self.get_current_version()

            # Generate cache key with version
            key = self._make_key(user_id, category, limit, offset, version)

            # Try to get from cache
            cached = await self.redis.get(key)

            if cached:
                logger.debug(
                    f"Cache HIT: agent_available for user {user_id}, "
                    f"category={category or 'ALL'}, limit={limit}, offset={offset}"
                )
                # Parse JSON string back to dict
                if isinstance(cached, bytes):
                    cached = cached.decode('utf-8')
                return json.loads(cached)

            logger.debug(
                f"Cache MISS: agent_available for user {user_id}, "
                f"category={category or 'ALL'}, limit={limit}, offset={offset}"
            )
            return None

        except Exception as e:
            logger.warning(f"Cache get error for user {user_id}: {e}")
            return None

    async def set_cached_response(
        self,
        user_id: str,
        category: Optional[str],
        limit: int,
        offset: int,
        response_data: Dict[str, Any],
        ttl: int = 900
    ) -> bool:
        """
        Cache agent list response for a user.

        Args:
            user_id: User UUID
            category: Category filter (or None for all)
            limit: Pagination limit
            offset: Pagination offset
            response_data: Complete response dict to cache
            ttl: Time to live in seconds (default: 900 = 15 minutes)

        Returns:
            True if cached successfully, False otherwise
        """
        if not self.enabled:
            return False

        try:
            # Get current version
            version = await self.get_current_version()

            # Generate cache key with version
            key = self._make_key(user_id, category, limit, offset, version)

            # Serialize response
            value = json.dumps(response_data)

            # Store in cache with TTL
            await self.redis.setex(key, ttl, value)

            logger.debug(
                f"Cached agent_available for user {user_id}, "
                f"category={category or 'ALL'}, limit={limit}, offset={offset} "
                f"(TTL: {ttl}s, version: {version})"
            )
            return True

        except Exception as e:
            logger.warning(f"Cache set error for user {user_id}: {e}")
            return False

    async def invalidate_all(self) -> int:
        """
        Invalidate all cached agent responses (all users).

        This is done by incrementing the global version number.
        All existing cache entries (with old version in key) become
        unreachable and will eventually expire via TTL.

        Returns:
            New version number
        """
        new_version = await self.increment_version()
        logger.info(f"Invalidated all agent_available caches (new version: {new_version})")
        return new_version

    async def invalidate_user(self, user_id: str) -> bool:
        """
        Invalidate cached responses for a specific user (optional, not currently used).

        This would require scanning for all keys matching the user pattern,
        which is expensive. The global version-based invalidation is preferred.

        Args:
            user_id: User UUID

        Returns:
            True if invalidated successfully, False otherwise
        """
        if not self.enabled:
            return False

        try:
            # This would require SCAN operation which is expensive
            # For now, we use global version-based invalidation instead
            logger.warning(
                f"User-specific invalidation requested for {user_id}, "
                "but global invalidation is preferred for performance"
            )
            return False

        except Exception as e:
            logger.warning(f"Cache invalidation error for user {user_id}: {e}")
            return False


# Singleton instance
_agent_available_cache: Optional[AgentAvailableCache] = None


def get_agent_available_cache() -> Optional[AgentAvailableCache]:
    """
    Get the global agent available cache instance.

    Used as a FastAPI dependency for endpoint injection.

    Returns:
        AgentAvailableCache instance if initialized, None otherwise
    """
    return _agent_available_cache


async def init_agent_available_cache(redis_client: Optional[Any] = None) -> AgentAvailableCache:
    """
    Initialize the global agent available cache instance.

    Should be called during application startup in lifespan.py.

    Args:
        redis_client: Redis client instance

    Returns:
        Initialized AgentAvailableCache instance
    """
    global _agent_available_cache
    _agent_available_cache = AgentAvailableCache(redis_client)

    if _agent_available_cache.enabled:
        logger.info("✓ Agent available cache initialized with Redis")
    else:
        logger.info("⚠ Agent available cache disabled (Redis not available)")

    return _agent_available_cache
