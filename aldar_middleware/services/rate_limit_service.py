"""Rate limiting service with Redis-based distributed counting."""

from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from uuid import UUID

from loguru import logger
from redis.asyncio import Redis
from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from aldar_middleware.settings.context import get_correlation_id
from aldar_middleware.models.quotas import RateLimitConfig, RateLimitUsage


class RateLimitError(Exception):
    """Rate limit exception."""

    def __init__(self, message: str, retry_after_seconds: int = 60):
        """Initialize rate limit error.

        Args:
            message: Error message
            retry_after_seconds: How long to wait before retrying
        """
        self.message = message
        self.retry_after_seconds = retry_after_seconds
        super().__init__(self.message)


class RateLimitService:
    """Rate limiting service with Redis-backed distributed counting."""

    # Redis key prefixes
    PREFIX_REQUEST_COUNT = "rate_limit:requests"
    PREFIX_CONCURRENT = "rate_limit:concurrent"
    PREFIX_WINDOW = "rate_limit:window"
    PREFIX_THROTTLE_DELAY = "rate_limit:throttle_delay"

    def __init__(self, db: AsyncSession, redis: Redis):
        """Initialize rate limit service.

        Args:
            db: Async database session
            redis: Redis async client
        """
        self.db = db
        self.redis = redis
        self.correlation_id = get_correlation_id()

    async def check_rate_limit(
        self,
        user_id: UUID,
        scope_type: str = "user",
        agent_id: Optional[UUID] = None,
        method_id: Optional[str] = None,
        increment: int = 1,
    ) -> Dict:
        """Check rate limit for a request.

        Args:
            user_id: User ID
            scope_type: Limit scope ("user", "agent", "method")
            agent_id: Agent ID (required if scope_type="agent" or "method")
            method_id: Method name (required if scope_type="method")
            increment: How much to increment counter

        Returns:
            {
                "allowed": bool,
                "current_count": int,
                "limit": int,
                "window_seconds": int,
                "reset_at": datetime,
                "throttle_seconds": int (optional),
                "retry_after": int (optional)
            }

        Raises:
            RateLimitError: If rate limit exceeded and throttle disabled
        """
        logger.info(
            "Checking rate limit | user_id={user_id} scope={scope} agent={agent}",
            user_id=user_id,
            scope=scope_type,
            agent=agent_id,
            extra={"correlation_id": self.correlation_id},
        )

        # Get rate limit config
        config = await self._get_rate_limit_config(user_id, scope_type, agent_id, method_id)
        if not config:
            # No rate limit configured, allow
            logger.debug(f"No rate limit config found, allowing request")
            return {"allowed": True, "limit": None}

        # Get current window
        window_start, window_end = self._get_window(config.requests_per_minute)
        window_seconds = int((window_end - window_start).total_seconds())

        # Build Redis key for this rate limit scope
        redis_key = self._build_redis_key(user_id, scope_type, agent_id, method_id)

        # Get current count in this window
        current_count = await self._get_request_count(redis_key)

        # Check if limit exceeded
        limit = config.requests_per_minute
        burst_limit = limit
        if config.burst_size:
            burst_limit = config.burst_size

        is_over_limit = current_count >= limit

        if is_over_limit:
            if config.throttle_enabled:
                # Calculate throttle delay
                throttle_seconds = self._calculate_throttle_delay(current_count, limit)

                # Store throttle delay in Redis
                throttle_key = f"{redis_key}:throttle"
                await self.redis.setex(throttle_key, window_seconds, str(throttle_seconds))

                logger.warning(
                    f"Rate limit throttling | user={user_id} delay={throttle_seconds}s",
                    extra={"correlation_id": self.correlation_id},
                )

                return {
                    "allowed": True,
                    "throttled": True,
                    "current_count": current_count,
                    "limit": limit,
                    "window_seconds": window_seconds,
                    "reset_at": window_end.isoformat(),
                    "throttle_seconds": throttle_seconds,
                }
            else:
                # Reject request
                logger.warning(
                    f"Rate limit exceeded | user={user_id} count={current_count} limit={limit}",
                    extra={"correlation_id": self.correlation_id},
                )

                retry_after = int((window_end - datetime.utcnow()).total_seconds())
                raise RateLimitError(
                    f"Rate limit exceeded ({current_count}/{limit}). Retry after {retry_after}s",
                    retry_after_seconds=retry_after,
                )

        # Increment counter
        new_count = await self._increment_request_count(redis_key, increment, window_seconds)

        logger.debug(
            f"Rate limit check passed | count={new_count}/{limit}",
            extra={"correlation_id": self.correlation_id},
        )

        return {
            "allowed": True,
            "throttled": False,
            "current_count": new_count,
            "limit": limit,
            "window_seconds": window_seconds,
            "reset_at": window_end.isoformat(),
        }

    async def check_concurrent_limit(
        self,
        user_id: UUID,
        scope_type: str = "user",
        agent_id: Optional[UUID] = None,
        method_id: Optional[str] = None,
    ) -> Dict:
        """Check concurrent execution limit.

        Args:
            user_id: User ID
            scope_type: Limit scope ("user", "agent", "method")
            agent_id: Agent ID (required if scope_type="agent" or "method")
            method_id: Method name (required if scope_type="method")

        Returns:
            {
                "allowed": bool,
                "current_concurrent": int,
                "limit": int,
                "execution_id": str (if allowed, use for release later)
            }

        Raises:
            RateLimitError: If concurrent limit exceeded
        """
        # Get config
        config = await self._get_rate_limit_config(user_id, scope_type, agent_id, method_id)
        if not config or not config.concurrent_executions:
            return {"allowed": True, "limit": None}

        # Get concurrent count
        concurrent_key = f"{self.PREFIX_CONCURRENT}:{user_id}:{scope_type}"
        if agent_id:
            concurrent_key += f":{agent_id}"
        if method_id:
            concurrent_key += f":{method_id}"

        current_concurrent = int(await self.redis.get(concurrent_key) or 0)
        limit = config.concurrent_executions

        if current_concurrent >= limit:
            raise RateLimitError(
                f"Concurrent execution limit exceeded ({current_concurrent}/{limit})"
            )

        # Increment and generate execution ID
        execution_id = f"{concurrent_key}:{datetime.utcnow().timestamp()}"
        await self.redis.incr(concurrent_key)

        logger.debug(
            f"Concurrent check passed | concurrent={current_concurrent + 1}/{limit}",
            extra={"correlation_id": self.correlation_id},
        )

        return {
            "allowed": True,
            "current_concurrent": current_concurrent + 1,
            "limit": limit,
            "execution_id": execution_id,
        }

    async def release_concurrent_slot(
        self,
        user_id: UUID,
        scope_type: str = "user",
        agent_id: Optional[UUID] = None,
        method_id: Optional[str] = None,
    ) -> None:
        """Release a concurrent execution slot.

        Args:
            user_id: User ID
            scope_type: Limit scope
            agent_id: Agent ID (optional)
            method_id: Method name (optional)
        """
        concurrent_key = f"{self.PREFIX_CONCURRENT}:{user_id}:{scope_type}"
        if agent_id:
            concurrent_key += f":{agent_id}"
        if method_id:
            concurrent_key += f":{method_id}"

        current = int(await self.redis.get(concurrent_key) or 0)
        if current > 0:
            await self.redis.decr(concurrent_key)

    async def record_usage(
        self,
        user_id: UUID,
        config_id: UUID,
        cost: float,
        request_count: int = 1,
        throttled: bool = False,
        rejected: bool = False,
    ) -> None:
        """Record rate limit usage in database.

        Args:
            user_id: User ID
            config_id: Rate limit config ID
            cost: Cost of this execution
            request_count: Number of requests
            throttled: Whether request was throttled
            rejected: Whether request was rejected
        """
        # Get current window
        window_start, window_end = self._get_window(60)  # 1 minute window

        # Find or create usage record
        stmt = select(RateLimitUsage).where(
            and_(
                RateLimitUsage.config_id == config_id,
                RateLimitUsage.window_start == window_start,
            )
        )
        result = await self.db.execute(stmt)
        usage = result.scalars().first()

        if not usage:
            usage = RateLimitUsage(
                config_id=config_id,
                user_id=user_id,
                window_type="minute",
                window_start=window_start,
                window_end=window_end,
            )
            self.db.add(usage)

        # Update usage
        usage.request_count += request_count
        usage.total_cost += cost
        if throttled:
            usage.throttled_count += 1
        if rejected:
            usage.rejected_count += 1
        usage.updated_at = datetime.utcnow()

        try:
            await self.db.commit()
        except Exception as e:
            logger.error(f"Failed to record usage: {e}", extra={"correlation_id": self.correlation_id})
            await self.db.rollback()

    async def create_rate_limit_config(
        self,
        user_id: UUID,
        scope_type: str,
        requests_per_minute: int = 100,
        concurrent_executions: int = 10,
        agent_id: Optional[UUID] = None,
        method_id: Optional[str] = None,
        throttle_enabled: bool = True,
        burst_size: Optional[int] = None,
        description: Optional[str] = None,
    ) -> RateLimitConfig:
        """Create a new rate limit configuration.

        Args:
            user_id: User ID
            scope_type: Limit scope ("user", "agent", "method")
            requests_per_minute: Requests allowed per minute
            concurrent_executions: Concurrent execution limit
            agent_id: Agent ID (optional)
            method_id: Method name (optional)
            throttle_enabled: Enable throttling vs rejection
            burst_size: Allow bursts above limit
            description: Configuration description

        Returns:
            Created RateLimitConfig
        """
        config = RateLimitConfig(
            user_id=user_id,
            scope_type=scope_type,
            agent_id=agent_id,
            method_id=method_id,
            requests_per_minute=requests_per_minute,
            concurrent_executions=concurrent_executions,
            throttle_enabled=throttle_enabled,
            burst_size=burst_size,
            description=description,
        )
        self.db.add(config)
        await self.db.commit()
        await self.db.refresh(config)

        logger.info(
            f"Created rate limit config | user={user_id} scope={scope_type}",
            extra={"correlation_id": self.correlation_id},
        )

        return config

    async def update_rate_limit_config(
        self,
        config_id: UUID,
        user_id: UUID,
        **updates: dict,
    ) -> Optional[RateLimitConfig]:
        """Update rate limit configuration.

        Args:
            config_id: Config ID to update
            user_id: User ID (for authorization)
            **updates: Fields to update

        Returns:
            Updated RateLimitConfig or None if not found
        """
        stmt = select(RateLimitConfig).where(
            and_(
                RateLimitConfig.id == config_id,
                RateLimitConfig.user_id == user_id,
            )
        )
        result = await self.db.execute(stmt)
        config = result.scalars().first()

        if not config:
            return None

        for key, value in updates.items():
            if hasattr(config, key):
                setattr(config, key, value)

        config.updated_at = datetime.utcnow()
        await self.db.commit()
        await self.db.refresh(config)

        logger.info(
            f"Updated rate limit config | config_id={config_id}",
            extra={"correlation_id": self.correlation_id},
        )

        return config

    async def get_rate_limit_configs(
        self,
        user_id: UUID,
        scope_type: Optional[str] = None,
    ) -> list:
        """Get rate limit configurations for user.

        Args:
            user_id: User ID
            scope_type: Optional filter by scope type

        Returns:
            List of RateLimitConfig
        """
        stmt = select(RateLimitConfig).where(RateLimitConfig.user_id == user_id)

        if scope_type:
            stmt = stmt.where(RateLimitConfig.scope_type == scope_type)

        stmt = stmt.order_by(desc(RateLimitConfig.created_at))

        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def delete_rate_limit_config(
        self,
        config_id: UUID,
        user_id: UUID,
    ) -> bool:
        """Delete rate limit configuration.

        Args:
            config_id: Config ID to delete
            user_id: User ID (for authorization)

        Returns:
            True if deleted, False if not found
        """
        stmt = select(RateLimitConfig).where(
            and_(
                RateLimitConfig.id == config_id,
                RateLimitConfig.user_id == user_id,
            )
        )
        result = await self.db.execute(stmt)
        config = result.scalars().first()

        if not config:
            return False

        await self.db.delete(config)
        await self.db.commit()

        logger.info(
            f"Deleted rate limit config | config_id={config_id}",
            extra={"correlation_id": self.correlation_id},
        )

        return True

    # Private helper methods

    async def _get_rate_limit_config(
        self,
        user_id: UUID,
        scope_type: str,
        agent_id: Optional[UUID] = None,
        method_id: Optional[str] = None,
    ) -> Optional[RateLimitConfig]:
        """Get active rate limit config for scope.

        Tries method > agent > user level configs.
        """
        stmt = select(RateLimitConfig).where(
            and_(
                RateLimitConfig.user_id == user_id,
                RateLimitConfig.is_active == True,
            )
        )

        # Build filter based on scope
        if scope_type == "method" and method_id and agent_id:
            stmt = stmt.where(
                and_(
                    RateLimitConfig.scope_type == "method",
                    RateLimitConfig.method_id == method_id,
                    RateLimitConfig.agent_id == agent_id,
                )
            )
        elif scope_type in ("agent", "method") and agent_id:
            stmt = stmt.where(
                and_(
                    RateLimitConfig.scope_type == "agent",
                    RateLimitConfig.agent_id == agent_id,
                )
            )
        else:
            stmt = stmt.where(RateLimitConfig.scope_type == "user")

        result = await self.db.execute(stmt)
        return result.scalars().first()

    def _build_redis_key(
        self,
        user_id: UUID,
        scope_type: str,
        agent_id: Optional[UUID] = None,
        method_id: Optional[str] = None,
    ) -> str:
        """Build Redis key for rate limit counting."""
        key = f"{self.PREFIX_REQUEST_COUNT}:{user_id}:{scope_type}"
        if agent_id:
            key += f":{agent_id}"
        if method_id:
            key += f":{method_id}"
        return key

    def _get_window(self, minutes: int) -> Tuple[datetime, datetime]:
        """Get current time window.

        Args:
            minutes: Window size in minutes

        Returns:
            (window_start, window_end) tuple
        """
        now = datetime.utcnow()
        # Round down to nearest window boundary
        window_size = timedelta(minutes=minutes)
        elapsed = now - datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        window_num = int(elapsed.total_seconds() // window_size.total_seconds())
        window_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + (window_num * window_size)
        window_end = window_start + window_size
        return window_start, window_end

    async def _get_request_count(self, redis_key: str) -> int:
        """Get current request count from Redis."""
        count = await self.redis.get(redis_key)
        return int(count) if count else 0

    async def _increment_request_count(
        self,
        redis_key: str,
        increment: int,
        window_seconds: int,
    ) -> int:
        """Increment request count and set expiry."""
        pipe = self.redis.pipeline()
        await pipe.incrby(redis_key, increment).execute()
        await self.redis.expire(redis_key, window_seconds)

        new_count = await self._get_request_count(redis_key)
        return new_count

    def _calculate_throttle_delay(self, current_count: int, limit: int) -> int:
        """Calculate throttle delay based on over-limit percentage.

        Args:
            current_count: Current request count
            limit: Rate limit

        Returns:
            Delay in seconds (0-60)
        """
        if current_count <= limit:
            return 0

        # Linear scale from 0-10 seconds based on how much over limit
        excess = current_count - limit
        max_excess = limit // 2  # Delay caps at 50% of limit exceeded
        delay = min(int((excess / max_excess) * 10), 10)
        return delay