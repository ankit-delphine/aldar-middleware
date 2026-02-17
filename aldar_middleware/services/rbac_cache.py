"""
RBAC Caching Service
Optional Redis-based caching for frequently accessed RBAC permissions and roles.

Usage:
    from aldar_middleware.services.rbac_cache import RBACCache
    
    cache = RBACCache(redis_client)
    
    # Try to get from cache
    permissions = await cache.get_user_permissions(username)
    if permissions is None:
        # Cache miss - fetch from database
        permissions = await rbac_service.get_user_all_permissions(username)
        # Store in cache
        await cache.set_user_permissions(username, permissions, ttl=300)
"""

from typing import List, Dict, Optional, Any
import json
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)


class RBACCache:
    """
    Optional caching layer for RBAC permissions.
    
    This class provides Redis-based caching for frequently accessed
    permission data to reduce database load and improve response times.
    
    Note: Redis is optional - if not available, methods will gracefully
    return None (cache miss), and the application will fall back to
    direct database queries.
    """
    
    def __init__(self, redis_client: Optional[Any] = None, enabled: bool = True):
        """
        Initialize RBAC cache.
        
        Args:
            redis_client: Redis client instance (e.g., from aioredis or redis-py)
            enabled: Enable/disable caching (default: True)
        """
        self.redis = redis_client
        self.enabled = enabled and redis_client is not None
        
        if not self.enabled and redis_client is None:
            logger.info("RBAC caching disabled - Redis client not available")
    
    def _make_key(self, prefix: str, identifier: str) -> str:
        """Generate cache key with namespace"""
        return f"rbac:{prefix}:{identifier}"
    
    async def get_user_permissions(self, username: str) -> Optional[List[Dict[str, str]]]:
        """
        Get cached user permissions.
        
        Returns:
            List of permissions if cached, None if cache miss or disabled
        """
        if not self.enabled:
            return None
        
        try:
            key = self._make_key("user_permissions", username)
            cached = await self.redis.get(key)
            
            if cached:
                logger.debug(f"Cache HIT: user permissions for {username}")
                return json.loads(cached)
            
            logger.debug(f"Cache MISS: user permissions for {username}")
            return None
            
        except Exception as e:
            logger.warning(f"Cache get error for {username}: {e}")
            return None
    
    async def set_user_permissions(
        self, 
        username: str, 
        permissions: List[Dict[str, str]], 
        ttl: int = 300
    ) -> bool:
        """
        Cache user permissions.
        
        Args:
            username: Username to cache permissions for
            permissions: List of permission dicts
            ttl: Time to live in seconds (default: 300 = 5 minutes)
        
        Returns:
            True if cached successfully, False otherwise
        """
        if not self.enabled:
            return False
        
        try:
            key = self._make_key("user_permissions", username)
            value = json.dumps(permissions)
            await self.redis.setex(key, ttl, value)
            
            logger.debug(f"Cached permissions for {username} (TTL: {ttl}s)")
            return True
            
        except Exception as e:
            logger.warning(f"Cache set error for {username}: {e}")
            return False
    
    async def invalidate_user_permissions(self, username: str) -> bool:
        """
        Invalidate cached user permissions.
        
        Call this when user's roles/permissions change.
        
        Returns:
            True if invalidated successfully, False otherwise
        """
        if not self.enabled:
            return False
        
        try:
            key = self._make_key("user_permissions", username)
            await self.redis.delete(key)
            
            logger.debug(f"Invalidated permission cache for {username}")
            return True
            
        except Exception as e:
            logger.warning(f"Cache invalidation error for {username}: {e}")
            return False
    
    async def get_role_permissions(self, role_name: str) -> Optional[List[Dict[str, str]]]:
        """Get cached role permissions"""
        if not self.enabled:
            return None
        
        try:
            key = self._make_key("role_permissions", role_name)
            cached = await self.redis.get(key)
            
            if cached:
                logger.debug(f"Cache HIT: role permissions for {role_name}")
                return json.loads(cached)
            
            return None
            
        except Exception as e:
            logger.warning(f"Cache get error for role {role_name}: {e}")
            return None
    
    async def set_role_permissions(
        self, 
        role_name: str, 
        permissions: List[Dict[str, str]], 
        ttl: int = 600
    ) -> bool:
        """Cache role permissions (longer TTL as roles change less frequently)"""
        if not self.enabled:
            return False
        
        try:
            key = self._make_key("role_permissions", role_name)
            value = json.dumps(permissions)
            await self.redis.setex(key, ttl, value)
            
            logger.debug(f"Cached role permissions for {role_name} (TTL: {ttl}s)")
            return True
            
        except Exception as e:
            logger.warning(f"Cache set error for role {role_name}: {e}")
            return False
    
    async def invalidate_role_permissions(self, role_name: str) -> bool:
        """Invalidate cached role permissions"""
        if not self.enabled:
            return False
        
        try:
            key = self._make_key("role_permissions", role_name)
            await self.redis.delete(key)
            
            logger.debug(f"Invalidated role permission cache for {role_name}")
            return True
            
        except Exception as e:
            logger.warning(f"Cache invalidation error for role {role_name}: {e}")
            return False
    
    async def check_permission_cached(
        self, 
        username: str, 
        resource: str, 
        action: str
    ) -> Optional[bool]:
        """
        Check if user has specific permission (using cache).
        
        Returns:
            True if has permission, False if doesn't have permission,
            None if cache miss or disabled
        """
        permissions = await self.get_user_permissions(username)
        
        if permissions is None:
            return None  # Cache miss
        
        # Check if permission exists in cached list
        for perm in permissions:
            if perm.get("resource") == resource and perm.get("action") == action:
                return True
        
        return False
    
    async def invalidate_all_user_caches(self) -> bool:
        """
        Invalidate all user permission caches.
        
        Use this sparingly - call when making global permission changes.
        """
        if not self.enabled:
            return False
        
        try:
            # Find all keys matching the pattern
            pattern = self._make_key("user_permissions", "*")
            cursor = 0
            deleted_count = 0
            
            # Scan and delete in batches
            while True:
                cursor, keys = await self.redis.scan(
                    cursor, 
                    match=pattern, 
                    count=100
                )
                
                if keys:
                    await self.redis.delete(*keys)
                    deleted_count += len(keys)
                
                if cursor == 0:
                    break
            
            logger.info(f"Invalidated {deleted_count} user permission caches")
            return True
            
        except Exception as e:
            logger.error(f"Error invalidating all user caches: {e}")
            return False
    
    async def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dict with cache hit/miss stats if Redis is available
        """
        if not self.enabled:
            return {"enabled": False, "message": "Caching disabled"}
        
        try:
            info = await self.redis.info("stats")
            
            return {
                "enabled": True,
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0),
                "hit_rate": self._calculate_hit_rate(
                    info.get("keyspace_hits", 0),
                    info.get("keyspace_misses", 0)
                )
            }
            
        except Exception as e:
            logger.error(f"Error getting cache stats: {e}")
            return {"enabled": True, "error": str(e)}
    
    @staticmethod
    def _calculate_hit_rate(hits: int, misses: int) -> float:
        """Calculate cache hit rate percentage"""
        total = hits + misses
        if total == 0:
            return 0.0
        return round((hits / total) * 100, 2)


# Example integration with RBACServiceLayer
class CachedRBACServiceLayer:
    """
    Extended RBAC service with caching support.
    
    This is an example of how to integrate caching into the existing
    RBACServiceLayer. You can either:
    1. Add these methods to RBACServiceLayer directly
    2. Use this as a wrapper
    3. Use RBACCache directly in your API endpoints
    """
    
    def __init__(self, rbac_service, cache: Optional[RBACCache] = None):
        """
        Initialize cached RBAC service.
        
        Args:
            rbac_service: Instance of RBACServiceLayer
            cache: Optional RBACCache instance
        """
        self.service = rbac_service
        self.cache = cache
    
    async def get_user_all_permissions_cached(
        self, 
        username: str, 
        active_only: bool = True
    ) -> List[Dict[str, str]]:
        """
        Get user permissions with caching.
        
        Tries cache first, falls back to database if cache miss.
        """
        # Try cache first
        if self.cache:
            cached_perms = await self.cache.get_user_permissions(username)
            if cached_perms is not None:
                return cached_perms
        
        # Cache miss or disabled - fetch from database
        permissions = await self.service.get_user_all_permissions(
            username, 
            active_only=active_only
        )
        
        # Store in cache for next time
        if self.cache:
            await self.cache.set_user_permissions(username, permissions, ttl=300)
        
        return permissions
    
    async def check_user_permission_cached(
        self, 
        username: str, 
        resource: str, 
        action: str
    ) -> bool:
        """Check user permission with caching"""
        # Try cache first
        if self.cache:
            cached_result = await self.cache.check_permission_cached(
                username, resource, action
            )
            if cached_result is not None:
                return cached_result
        
        # Cache miss - check via database
        return await self.service.check_user_permission(username, resource, action)


# Singleton cache instance (optional - initialize if Redis is available)
_cache_instance: Optional[RBACCache] = None


def get_rbac_cache() -> Optional[RBACCache]:
    """
    Get global RBAC cache instance.
    
    Returns None if Redis is not configured.
    """
    return _cache_instance


def init_rbac_cache(redis_client: Any) -> RBACCache:
    """
    Initialize global RBAC cache instance.
    
    Call this during application startup if Redis is available.
    
    Example:
        import aioredis
        redis = await aioredis.create_redis_pool('redis://localhost')
        init_rbac_cache(redis)
    """
    global _cache_instance
    _cache_instance = RBACCache(redis_client, enabled=True)
    logger.info("RBAC caching initialized")
    return _cache_instance

