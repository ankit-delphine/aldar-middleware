"""Circuit breaker service for handling failures and fast-fail scenarios."""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple
from uuid import UUID
from enum import Enum

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from aldar_middleware.models.monitoring import CircuitBreakerState
from aldar_middleware.monitoring.prometheus import record_metric


class CircuitState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "CLOSED"  # Normal operation
    OPEN = "OPEN"  # Failing, reject requests
    HALF_OPEN = "HALF_OPEN"  # Testing if service recovered


class CircuitBreakerException(Exception):
    """Exception raised when circuit is open."""

    pass


class CircuitBreakerConfig:
    """Circuit breaker configuration."""

    def __init__(
        self,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout_seconds: int = 60,
        backoff_multiplier: float = 2.0,
    ):
        """
        Initialize circuit breaker config.

        Args:
            failure_threshold: Number of failures before opening
            success_threshold: Number of successes in HALF_OPEN before closing
            timeout_seconds: Timeout before attempting recovery
            backoff_multiplier: Exponential backoff multiplier
        """
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout_seconds = timeout_seconds
        self.backoff_multiplier = backoff_multiplier


class CircuitBreaker:
    """Circuit breaker for preventing cascading failures."""

    def __init__(self):
        """Initialize circuit breaker."""
        self.default_config = CircuitBreakerConfig()

    async def check_circuit(
        self,
        db: AsyncSession,
        agent_id: UUID,
        method_id: Optional[UUID] = None,
    ) -> Tuple[CircuitState, bool]:
        """
        Check circuit state and determine if request should be allowed.

        Args:
            db: Database session
            agent_id: Agent ID
            method_id: Optional method ID for method-level circuit breaking

        Returns:
            Tuple of (current_state, is_allowed)

        Raises:
            CircuitBreakerException: If circuit is OPEN
        """
        breaker = await self._get_or_create_breaker(db, agent_id, method_id)

        current_state = CircuitState(breaker.state)

        if current_state == CircuitState.CLOSED:
            # Normal operation
            return current_state, True

        elif current_state == CircuitState.OPEN:
            # Check if timeout has elapsed
            if self._should_attempt_recovery(breaker):
                logger.info(
                    f"Circuit breaker for agent {agent_id} transitioning to HALF_OPEN"
                )
                await self._transition_state(
                    db, breaker, CircuitState.HALF_OPEN, reset_counts=True
                )
                return CircuitState.HALF_OPEN, True
            else:
                # Still in open state, reject request
                raise CircuitBreakerException(
                    f"Circuit breaker is OPEN for agent {agent_id}. "
                    f"Last failure: {breaker.last_failure_time}. "
                    f"Recovery attempt in {self._time_until_recovery(breaker)}s"
                )

        elif current_state == CircuitState.HALF_OPEN:
            # Allow request to test recovery
            return current_state, True

        return current_state, True

    async def record_success(
        self,
        db: AsyncSession,
        agent_id: UUID,
        method_id: Optional[UUID] = None,
    ) -> None:
        """
        Record successful execution.

        Args:
            db: Database session
            agent_id: Agent ID
            method_id: Optional method ID
        """
        breaker = await self._get_or_create_breaker(db, agent_id, method_id)
        current_state = CircuitState(breaker.state)

        if current_state == CircuitState.HALF_OPEN:
            breaker.success_count += 1

            if breaker.success_count >= breaker.success_threshold:
                logger.info(
                    f"Circuit breaker for agent {agent_id} closing after successful recovery"
                )
                await self._transition_state(
                    db, breaker, CircuitState.CLOSED, reset_counts=True
                )
        elif current_state == CircuitState.CLOSED:
            breaker.failure_count = max(0, breaker.failure_count - 1)  # Decay failures

        breaker.updated_at = datetime.utcnow()
        await db.commit()

        record_metric(
            "circuit_breaker_success",
            1,
            labels={"agent_id": str(agent_id), "state": current_state.value},
        )

    async def record_failure(
        self,
        db: AsyncSession,
        agent_id: UUID,
        method_id: Optional[UUID] = None,
    ) -> None:
        """
        Record failed execution and potentially open circuit.

        Args:
            db: Database session
            agent_id: Agent ID
            method_id: Optional method ID
        """
        breaker = await self._get_or_create_breaker(db, agent_id, method_id)
        current_state = CircuitState(breaker.state)

        breaker.failure_count += 1
        breaker.last_failure_time = datetime.utcnow()

        if current_state == CircuitState.CLOSED:
            if breaker.failure_count >= breaker.failure_threshold:
                logger.warning(
                    f"Circuit breaker for agent {agent_id} opening after "
                    f"{breaker.failure_count} failures"
                )
                await self._transition_state(
                    db, breaker, CircuitState.OPEN, reset_counts=False
                )
        elif current_state == CircuitState.HALF_OPEN:
            # Even one failure in HALF_OPEN reopens the circuit
            logger.warning(
                f"Circuit breaker for agent {agent_id} reopening after failure in HALF_OPEN state"
            )
            await self._transition_state(
                db, breaker, CircuitState.OPEN, reset_counts=False
            )

        breaker.updated_at = datetime.utcnow()
        await db.commit()

        record_metric(
            "circuit_breaker_failure",
            1,
            labels={"agent_id": str(agent_id), "state": current_state.value},
        )

    async def get_state(
        self,
        db: AsyncSession,
        agent_id: UUID,
        method_id: Optional[UUID] = None,
    ) -> Dict[str, Any]:
        """
        Get current circuit breaker state.

        Args:
            db: Database session
            agent_id: Agent ID
            method_id: Optional method ID

        Returns:
            Circuit breaker state information
        """
        breaker = await self._get_or_create_breaker(db, agent_id, method_id)

        return {
            "state": breaker.state,
            "failure_count": breaker.failure_count,
            "success_count": breaker.success_count,
            "failure_threshold": breaker.failure_threshold,
            "success_threshold": breaker.success_threshold,
            "last_failure_time": (
                breaker.last_failure_time.isoformat()
                if breaker.last_failure_time
                else None
            ),
            "opened_at": (
                breaker.opened_at.isoformat() if breaker.opened_at else None
            ),
            "last_state_change": breaker.last_state_change.isoformat(),
        }

    async def reset_circuit(
        self,
        db: AsyncSession,
        agent_id: UUID,
        method_id: Optional[UUID] = None,
    ) -> None:
        """
        Manually reset circuit breaker.

        Args:
            db: Database session
            agent_id: Agent ID
            method_id: Optional method ID
        """
        breaker = await self._get_or_create_breaker(db, agent_id, method_id)

        await self._transition_state(
            db, breaker, CircuitState.CLOSED, reset_counts=True
        )

        logger.info(f"Circuit breaker for agent {agent_id} manually reset")

    # Private helper methods

    async def _get_or_create_breaker(
        self,
        db: AsyncSession,
        agent_id: UUID,
        method_id: Optional[UUID] = None,
    ) -> CircuitBreakerState:
        """Get or create circuit breaker state."""
        result = await db.execute(
            select(CircuitBreakerState).where(
                CircuitBreakerState.agent_id == agent_id,
                CircuitBreakerState.method_id == method_id,
            )
        )
        breaker = result.scalars().first()

        if not breaker:
            breaker = CircuitBreakerState(
                agent_id=agent_id,
                method_id=method_id,
                state=CircuitState.CLOSED.value,
                failure_threshold=self.default_config.failure_threshold,
                success_threshold=self.default_config.success_threshold,
                timeout_seconds=self.default_config.timeout_seconds,
                backoff_multiplier=self.default_config.backoff_multiplier,
            )
            db.add(breaker)
            await db.flush()

        return breaker

    def _should_attempt_recovery(self, breaker: CircuitBreakerState) -> bool:
        """Check if enough time has passed to attempt recovery."""
        if not breaker.last_failure_time:
            return True

        elapsed = datetime.utcnow() - breaker.last_failure_time
        timeout = timedelta(seconds=breaker.timeout_seconds)

        return elapsed >= timeout

    def _time_until_recovery(self, breaker: CircuitBreakerState) -> int:
        """Calculate seconds until recovery can be attempted."""
        if not breaker.last_failure_time:
            return 0

        elapsed = datetime.utcnow() - breaker.last_failure_time
        timeout = timedelta(seconds=breaker.timeout_seconds)

        remaining = timeout - elapsed
        return max(0, int(remaining.total_seconds()))

    async def _transition_state(
        self,
        db: AsyncSession,
        breaker: CircuitBreakerState,
        new_state: CircuitState,
        reset_counts: bool = False,
    ) -> None:
        """Transition to new state."""
        old_state = breaker.state
        breaker.state = new_state.value
        breaker.last_state_change = datetime.utcnow()

        if new_state == CircuitState.OPEN:
            breaker.opened_at = datetime.utcnow()

        if reset_counts:
            breaker.failure_count = 0
            breaker.success_count = 0

        await db.flush()

        logger.info(
            f"Circuit breaker state transition: {old_state} → {new_state.value} "
            f"(agent_id={breaker.agent_id})"
        )

        record_metric(
            "circuit_breaker_state",
            1,
            labels={
                "agent_id": str(breaker.agent_id),
                "state": new_state.value,
            },
        )