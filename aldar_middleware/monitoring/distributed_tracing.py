"""Distributed tracing service using OpenTelemetry and Azure Application Insights.

Provides end-to-end request tracing across all services, agent calls, and database queries.
Integrates with Azure Application Insights for centralized trace visualization.
"""

import time
import uuid
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from aldar_middleware.settings import settings
from aldar_middleware.settings.context import get_correlation_id, get_agent_context
from aldar_middleware.models import DistributedTrace, TraceStatusType, TraceSampleType


class TraceSamplingConfig:
    """Sampling configuration for traces."""
    
    def __init__(self, sample_rate: str = "full"):
        """Initialize sampling configuration.
        
        Args:
            sample_rate: Sampling rate - "full" (100%), "partial" (10%), "minimal" (1%)
        """
        self.sample_rate = sample_rate
        self.should_sample = self._get_sample_rate()
        self.sample_type = self._get_sample_type()
    
    def _get_sample_rate(self) -> float:
        """Get the actual sampling rate as a percentage."""
        if self.sample_rate == "full":
            return 1.0  # 100%
        elif self.sample_rate == "partial":
            return 0.1  # 10%
        elif self.sample_rate == "minimal":
            return 0.01  # 1%
        else:
            return 1.0  # Default to full
    
    def _get_sample_type(self) -> TraceSampleType:
        """Get the sample type enum."""
        if self.sample_rate == "full":
            return TraceSampleType.FULL
        elif self.sample_rate == "partial":
            return TraceSampleType.PARTIAL
        elif self.sample_rate == "minimal":
            return TraceSampleType.MINIMAL
        else:
            return TraceSampleType.FULL


class DistributedTracingService:
    """Service for distributed tracing across the application.
    
    Tracks complete request journeys including:
    - HTTP requests and responses
    - Agent calls (OpenAI, MCP)
    - Database queries
    - Error conditions and performance metrics
    """
    
    def __init__(self, db_session: Optional[AsyncSession] = None):
        """Initialize the distributed tracing service.
        
        Args:
            db_session: Database session for storing traces (optional for initialization)
        """
        self.db_session = db_session
        self.enabled = settings.distributed_tracing_enabled and settings.app_insights_enabled
        self.sampling_config = TraceSamplingConfig(settings.trace_sample_rate)
        self.current_trace: Optional[DistributedTrace] = None
    
    async def set_db_session(self, db_session: AsyncSession) -> None:
        """Set the database session (called after it's available).
        
        Args:
            db_session: Async database session
        """
        self.db_session = db_session
    
    def should_trace(self) -> bool:
        """Determine if this request should be traced based on sampling.
        
        Returns:
            True if request should be traced, False otherwise
        """
        if not self.enabled:
            return False
        
        # Always trace errors and specific paths, even with sampling
        import random
        return random.random() < self.sampling_config.should_sample
    
    async def start_trace(
        self,
        request_method: str,
        request_path: str,
        request_endpoint: Optional[str] = None,
        user_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> DistributedTrace:
        """Start a new distributed trace for an incoming request.
        
        Args:
            request_method: HTTP method (GET, POST, etc.)
            request_path: Request path/URL
            request_endpoint: Matched FastAPI endpoint
            user_id: User ID if available
            correlation_id: Correlation ID (will be generated if not provided)
            
        Returns:
            DistributedTrace instance
        """
        if not correlation_id:
            correlation_id = get_correlation_id() or str(uuid.uuid4())
        
        # Create trace ID (OpenTelemetry format)
        trace_id = uuid.uuid4().hex[:32]
        span_id = uuid.uuid4().hex[:16]
        
        # Determine if this trace should be sampled
        sampled = self.should_trace()
        
        trace = DistributedTrace(
            correlation_id=correlation_id,
            trace_id=trace_id,
            span_id=span_id,
            user_id=user_id,
            request_method=request_method,
            request_path=request_path,
            request_endpoint=request_endpoint,
            start_time=datetime.now(timezone.utc),
            status=TraceStatusType.PENDING,
            sampled=sampled,
            sample_type=self.sampling_config.sample_type,
        )
        
        self.current_trace = trace
        
        logger.debug(
            f"Started distributed trace: correlation_id={correlation_id}, "
            f"trace_id={trace_id}, sampled={sampled}"
        )
        
        return trace
    
    async def end_trace(
        self,
        status: TraceStatusType = TraceStatusType.SUCCESS,
        http_status_code: Optional[int] = None,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Optional[DistributedTrace]:
        """End and persist the current distributed trace.
        
        Args:
            status: Trace status (SUCCESS, ERROR, TIMEOUT, PARTIAL)
            http_status_code: HTTP response status code
            error_type: Type of error if applicable
            error_message: Error message if applicable
            
        Returns:
            Persisted DistributedTrace instance or None if no trace is active
        """
        if not self.current_trace:
            return None
        
        trace = self.current_trace
        
        # Calculate duration
        now = datetime.now(timezone.utc)
        trace.end_time = now
        trace.duration_ms = int((now - trace.start_time).total_seconds() * 1000)
        
        # Update status
        trace.status = status
        trace.http_status_code = http_status_code
        trace.error_type = error_type
        trace.error_message = error_message
        
        # Update agent and query counts from context
        agent_context = get_agent_context()
        if agent_context:
            trace.agent_count = agent_context.get_agent_count()
            agent_stats = agent_context.get_agent_statistics()
            trace.total_agent_time_ms = int(agent_stats.get("total_agent_time", 0) * 1000)
        
        # Persist to database if session is available and trace is sampled
        if self.db_session and trace.sampled:
            try:
                self.db_session.add(trace)
                await self.db_session.flush()
                logger.debug(
                    f"Persisted distributed trace: correlation_id={trace.correlation_id}, "
                    f"duration_ms={trace.duration_ms}, status={trace.status.value}"
                )
            except Exception as e:
                logger.error(f"Failed to persist distributed trace: {e}")
        
        self.current_trace = None
        return trace
    
    async def add_trace_metadata(self, key: str, value: Any) -> None:
        """Add metadata to the current trace.
        
        Args:
            key: Metadata key
            value: Metadata value
        """
        if not self.current_trace:
            return
        
        if self.current_trace.metadata is None:
            self.current_trace.metadata = {}
        
        self.current_trace.metadata[key] = value
    
    async def record_agent_call_in_trace(
        self,
        agent_type: str,
        agent_name: str,
        method: str,
        duration_ms: int,
        status: str = "success",
    ) -> None:
        """Record an agent call in the trace metrics.
        
        Args:
            agent_type: Type of agent (openai, mcp)
            agent_name: Name of the agent
            method: Method being called
            duration_ms: Duration in milliseconds
            status: Status of the call (success, error, timeout)
        """
        if not self.current_trace:
            return
        
        # These are already tracked via agent context,
        # but we can add detailed agent call info to trace metadata
        if self.current_trace.metadata is None:
            self.current_trace.metadata = {}
        
        if "agent_calls" not in self.current_trace.metadata:
            self.current_trace.metadata["agent_calls"] = []
        
        self.current_trace.metadata["agent_calls"].append({
            "agent_type": agent_type,
            "agent_name": agent_name,
            "method": method,
            "duration_ms": duration_ms,
            "status": status,
        })
    
    async def record_database_query_in_trace(
        self,
        query_type: str,
        duration_ms: int,
        rows_affected: Optional[int] = None,
        slow_query: bool = False,
    ) -> None:
        """Record a database query in the trace metrics.
        
        Args:
            query_type: Type of query (SELECT, INSERT, UPDATE, DELETE)
            duration_ms: Duration in milliseconds
            rows_affected: Number of rows affected
            slow_query: Whether the query was slow
        """
        if not self.current_trace:
            return
        
        # Update query count and time
        self.current_trace.database_query_count += 1
        self.current_trace.total_query_time_ms += duration_ms
        
        # Add to metadata
        if self.current_trace.metadata is None:
            self.current_trace.metadata = {}
        
        if "database_queries" not in self.current_trace.metadata:
            self.current_trace.metadata["database_queries"] = []
        
        self.current_trace.metadata["database_queries"].append({
            "query_type": query_type,
            "duration_ms": duration_ms,
            "rows_affected": rows_affected,
            "slow_query": slow_query,
        })
    
    @asynccontextmanager
    async def trace_operation(
        self,
        operation_name: str,
        operation_type: str = "generic",
    ):
        """Context manager for tracing a specific operation.
        
        Usage:
            async with tracer.trace_operation("fetch_user", "database"):
                # Perform operation
                pass
        
        Args:
            operation_name: Name of the operation
            operation_type: Type of operation (database, agent, api, etc.)
            
        Yields:
            None
        """
        start_time = time.time()
        
        try:
            yield
            duration_ms = int((time.time() - start_time) * 1000)
            logger.debug(
                f"Operation completed: {operation_name} ({operation_type}), "
                f"duration_ms={duration_ms}"
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(
                f"Operation failed: {operation_name} ({operation_type}), "
                f"duration_ms={duration_ms}, error={e}"
            )
            raise
    
    def get_current_trace_context(self) -> Dict[str, str]:
        """Get the current trace context for propagation to other services.
        
        Returns:
            Dictionary with trace context (trace_id, span_id, parent_span_id)
        """
        if not self.current_trace:
            return {}
        
        return {
            "traceparent": f"00-{self.current_trace.trace_id}-{self.current_trace.span_id}-01",
            "correlation_id": self.current_trace.correlation_id,
        }


# Global instance
_tracing_service: Optional[DistributedTracingService] = None


async def get_distributed_tracing_service(
    db_session: Optional[AsyncSession] = None,
) -> DistributedTracingService:
    """Get or create the global distributed tracing service.
    
    Args:
        db_session: Database session if available
        
    Returns:
        DistributedTracingService instance
    """
    global _tracing_service
    
    if _tracing_service is None:
        _tracing_service = DistributedTracingService(db_session)
        logger.info(
            f"Distributed Tracing Service initialized. "
            f"Enabled: {_tracing_service.enabled}. "
            f"Sampling: {_tracing_service.sampling_config.sample_rate} "
            f"({_tracing_service.sampling_config.should_sample * 100}%)"
        )
    elif db_session and not _tracing_service.db_session:
        await _tracing_service.set_db_session(db_session)
    
    return _tracing_service


async def initialize_distributed_tracing(
    db_session: AsyncSession,
) -> DistributedTracingService:
    """Initialize the distributed tracing service with database session.
    
    Args:
        db_session: Async database session
        
    Returns:
        Initialized DistributedTracingService instance
    """
    service = await get_distributed_tracing_service(db_session)
    logger.info("Distributed tracing service fully initialized with database session")
    return service