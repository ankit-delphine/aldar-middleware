"""Health monitoring service for tracking agent health and uptime."""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from aldar_middleware.models.monitoring import (
    DegradationEvent,
)
from aldar_middleware.models.menu import Agent
from aldar_middleware.models.user import UserAgent
from aldar_middleware.orchestration.mcp import MCPService
from aldar_middleware.monitoring.prometheus import record_metric
from aldar_middleware.monitoring.cosmos_logger import log_agent_health_check


class HealthMonitor:
    """Monitor agent health and track uptime metrics."""

    def __init__(self) -> None:
        """Initialize health monitor."""
        self.mcp_service = MCPService()
        self.check_interval = 60  # seconds between checks
        self.max_response_time = 5000  # ms
        self.consecutive_failure_threshold = 3  # failures before marking unhealthy

    async def check_agent_health(
        self,
        db: AsyncSession,
        agent_id: UUID,
    ) -> Dict[str, Any]:
        """
        Perform a health check on an agent.

        Args:
            db: Database session
            agent_id: Agent ID to check

        Returns:
            Health check result with status and metrics
        """
        start_time = time.time()

        try:
            # Get agent
            agent_result = await db.execute(
                select(UserAgent).where(UserAgent.id == agent_id)
            )
            agent = agent_result.scalars().first()

            if not agent:
                raise ValueError(f"Agent {agent_id} not found")

            if not agent.mcp_connection_id:
                status = "unknown"
                reason = "No MCP connection configured"
                response_time_ms = None
            else:
                # Try to ping the MCP server
                try:
                    response_time_ms = await self._ping_mcp_server(agent.mcp_connection_id)
                    status = "healthy"
                    reason = "Health check passed"
                except asyncio.TimeoutError:
                    status = "unhealthy"
                    reason = "Health check timeout"
                    response_time_ms = self.max_response_time + 1
                except Exception as e:
                    status = "degraded"
                    reason = f"Health check failed: {str(e)}"
                    response_time_ms = None

            # Update Agent row health fields (match on public_id which is UUID)
            await db.execute(
                update(Agent)
                .where(Agent.public_id == agent_id)
                .values(
                    is_healthy=(status == "healthy"),
                    health_status=status,
                    last_health_check=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                ),
            )

            # Log to Cosmos DB
            log_agent_health_check(
                agent_id=str(agent_id),
                status=status,
                response_time_ms=response_time_ms,
                reason=reason,
                details={"source": "health_monitor"},
            )

            # Check for degradation
            if status != "healthy":
                await self._handle_degradation(db, agent_id, status, reason)

            response_time = (time.time() - start_time) * 1000  # Convert to ms

            logger.info(
                f"Health check completed for agent {agent_id}: status={status}, "
                f"response_time={response_time_ms}ms, duration={response_time:.2f}ms"
            )

            return {
                "agent_id": str(agent_id),
                "status": status,
                "response_time_ms": response_time_ms,
                "reason": reason,
                "check_duration_ms": response_time,
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Health check failed for agent {agent_id}: {str(e)}")
            raise

    async def get_health_status(
        self,
        db: AsyncSession,
        agent_id: UUID,
    ) -> Optional[Dict[str, Any]]:
        """
        Get current health status for an agent.

        Args:
            db: Database session
            agent_id: Agent ID

        Returns:
            Health status or None if not found
        """
        # Read by public_id (UUID)
        result = await db.execute(select(Agent).where(Agent.public_id == agent_id))
        agent = result.scalar_one_or_none()

        if not agent:
            return None

        return {
            "agent_id": str(agent.public_id),
            "status": agent.health_status or "unknown",
            "is_healthy": agent.is_healthy,
            "last_check": agent.last_health_check.isoformat() if agent.last_health_check else None,
        }

    async def get_health_history(
        self,
        db: AsyncSession,
        agent_id: UUID,
        days: int = 7,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get health check history for an agent.

        Args:
            db: Database session
            agent_id: Agent ID
            days: Number of days to look back
            limit: Maximum number of records to return

        Returns:
            List of health check records
        """
        # For history, logs are written to Cosmos DB; API can integrate later.
        # Returning empty list to keep endpoint stable.
        return []

    async def disable_agent(
        self,
        db: AsyncSession,
        agent_id: UUID,
        reason: str = "Failed health checks",
    ) -> None:
        """
        Disable an agent due to health issues.

        Args:
            db: Database session
            agent_id: Agent ID
            reason: Reason for disabling
        """
        await db.execute(
            update(UserAgent).where(UserAgent.id == agent_id).values(is_active=False)
        )

        # Record degradation event
        degradation = DegradationEvent(
            agent_id=agent_id,
            degradation_type="auto_disabled",
            reason=reason,
            severity="major",
            resolution_status="pending",
        )
        db.add(degradation)
        await db.commit()

        logger.warning(f"Agent {agent_id} auto-disabled: {reason}")
        record_metric("agent_auto_disabled", 1, labels={"agent_id": str(agent_id)})

    # Private helper methods

    async def _ping_mcp_server(self, connection_id: UUID) -> int:
        """
        Ping an MCP server and return response time in milliseconds.

        Args:
            connection_id: MCP connection ID

        Returns:
            Response time in milliseconds

        Raises:
            asyncio.TimeoutError: If ping times out
            Exception: If ping fails
        """
        start_time = time.time()

        try:
            # Use asyncio.wait_for to enforce timeout
            await asyncio.wait_for(
                self.mcp_service.ping_connection(connection_id),
                timeout=5.0,
            )
            response_time_ms = int((time.time() - start_time) * 1000)
            return response_time_ms
        except asyncio.TimeoutError:
            raise
        except Exception as e:
            logger.debug(f"MCP ping failed: {str(e)}")
            raise

    # Removed SQL-based health status helpers; health state is stored on Agent row

    # Removed SQL-based health metrics aggregation

    # History is now captured in Cosmos DB via log_agent_health_check

    async def _handle_degradation(
        self,
        db: AsyncSession,
        agent_id: UUID,
        status: str,
        reason: str,
    ) -> None:
        """Handle degradation event."""
        degradation = DegradationEvent(
            agent_id=agent_id,
            degradation_type="health_check_failure",
            reason=reason,
            severity="moderate" if status == "degraded" else "major",
            resolution_status="pending",
        )
        db.add(degradation)
        await db.flush()

        logger.warning(f"Degradation event recorded for agent {agent_id}: {reason}")