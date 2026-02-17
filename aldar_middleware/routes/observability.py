"""API endpoints for distributed tracing and audit logs."""

from typing import List, Optional
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_

from aldar_middleware.database.base import get_async_session
from aldar_middleware.models import (
    DistributedTrace,
    RequestResponseAudit,
    DatabaseQueryTrace,
    TraceStatusType,
    User,
)
from aldar_middleware.auth.dependencies import get_current_user_id
from pydantic import BaseModel
from datetime import datetime


# Response schemas
class TraceResponse(BaseModel):
    """Response model for distributed trace."""
    id: str
    correlation_id: str
    trace_id: str
    user_id: Optional[str]
    request_method: str
    request_path: str
    request_endpoint: Optional[str]
    start_time: str
    end_time: Optional[str]
    duration_ms: Optional[int]
    status: str
    http_status_code: Optional[int]
    error_type: Optional[str]
    error_message: Optional[str]
    agent_count: int
    database_query_count: int
    total_agent_time_ms: int
    total_query_time_ms: int
    sampled: bool
    sample_type: str


class RequestResponseAuditResponse(BaseModel):
    """Response model for request/response audit."""
    id: str
    correlation_id: str
    request_method: str
    request_path: str
    response_status_code: int
    response_time_ms: int
    user_id: Optional[str]
    client_ip: Optional[str]
    pii_masked: bool
    created_at: str


class DatabaseQueryTraceResponse(BaseModel):
    """Response model for database query trace."""
    id: str
    correlation_id: str
    query_type: str
    duration_ms: int
    rows_affected: Optional[int]
    rows_returned: Optional[int]
    slow_query: bool
    status: str
    error_message: Optional[str]


router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/traces", response_model=List[TraceResponse])
async def get_traces(
    user_id: str = Depends(get_current_user_id),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    hours: int = Query(24, ge=1, le=720),
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_async_session),
) -> List[TraceResponse]:
    """Get distributed traces for the current user.
    
    Args:
        user_id: Current user ID
        limit: Number of traces to return
        offset: Offset for pagination
        hours: Look back hours (default: 24)
        status: Filter by status (success, error, timeout, pending)
        db: Database session
        
    Returns:
        List of distributed traces
    """
    try:
        # Ensure user_id is a string to match DistributedTrace.user_id (VARCHAR)
        user_id = str(user_id)
        # Build query
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        
        query = select(DistributedTrace).where(
            and_(
                DistributedTrace.user_id == user_id,
                DistributedTrace.created_at >= cutoff_time,
                DistributedTrace.sampled == True,  # Only return sampled traces
            )
        )
        
        # Apply status filter if provided
        if status:
            try:
                status_enum = TraceStatusType[status.upper()]
                query = query.where(DistributedTrace.status == status_enum)
            except KeyError:
                raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
        
        # Order by created_at descending and apply pagination
        query = query.order_by(desc(DistributedTrace.created_at)).limit(limit).offset(offset)
        
        result = await db.execute(query)
        traces = result.scalars().all()
        
        return [
            TraceResponse(
                id=str(trace.id),
                correlation_id=trace.correlation_id,
                trace_id=trace.trace_id,
                user_id=trace.user_id,
                request_method=trace.request_method,
                request_path=trace.request_path,
                request_endpoint=trace.request_endpoint,
                start_time=trace.start_time.isoformat(),
                end_time=trace.end_time.isoformat() if trace.end_time else None,
                duration_ms=trace.duration_ms,
                status=trace.status.value,
                http_status_code=trace.http_status_code,
                error_type=trace.error_type,
                error_message=trace.error_message,
                agent_count=trace.agent_count,
                database_query_count=trace.database_query_count,
                total_agent_time_ms=trace.total_agent_time_ms,
                total_query_time_ms=trace.total_query_time_ms,
                sampled=trace.sampled,
                sample_type=trace.sample_type.value,
            )
            for trace in traces
        ]
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving traces: {str(e)}")


@router.get("/traces/{correlation_id}", response_model=TraceResponse)
async def get_trace_by_correlation_id(
    correlation_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
) -> TraceResponse:
    """Get a specific trace by correlation ID.
    
    Args:
        correlation_id: Correlation ID of the trace
        user_id: Current user ID
        db: Database session
        
    Returns:
        Distributed trace details
    """
    try:
        user_id = str(user_id)
        query = select(DistributedTrace).where(
            and_(
                DistributedTrace.correlation_id == correlation_id,
                DistributedTrace.user_id == user_id,
            )
        )
        
        result = await db.execute(query)
        trace = result.scalar_one_or_none()
        
        if not trace:
            raise HTTPException(status_code=404, detail="Trace not found")
        
        return TraceResponse(
            id=str(trace.id),
            correlation_id=trace.correlation_id,
            trace_id=trace.trace_id,
            user_id=trace.user_id,
            request_method=trace.request_method,
            request_path=trace.request_path,
            request_endpoint=trace.request_endpoint,
            start_time=trace.start_time.isoformat(),
            end_time=trace.end_time.isoformat() if trace.end_time else None,
            duration_ms=trace.duration_ms,
            status=trace.status.value,
            http_status_code=trace.http_status_code,
            error_type=trace.error_type,
            error_message=trace.error_message,
            agent_count=trace.agent_count,
            database_query_count=trace.database_query_count,
            total_agent_time_ms=trace.total_agent_time_ms,
            total_query_time_ms=trace.total_query_time_ms,
            sampled=trace.sampled,
            sample_type=trace.sample_type.value,
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving trace: {str(e)}")


@router.get("/traces/{correlation_id}/requests", response_model=List[RequestResponseAuditResponse])
async def get_trace_requests(
    correlation_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_async_session),
) -> List[RequestResponseAuditResponse]:
    """Get request/response audits for a specific trace.
    
    Args:
        correlation_id: Correlation ID of the trace
        user_id: Current user ID
        db: Database session
        
    Returns:
        List of request/response audits
    """
    try:
        user_id = str(user_id)
        # First, get the trace to verify ownership
        trace_query = select(DistributedTrace).where(
            and_(
                DistributedTrace.correlation_id == correlation_id,
                DistributedTrace.user_id == user_id,
            )
        )
        
        trace_result = await db.execute(trace_query)
        trace = trace_result.scalar_one_or_none()
        
        if not trace:
            raise HTTPException(status_code=404, detail="Trace not found")
        
        # Get audits for this trace
        audit_query = select(RequestResponseAudit).where(
            RequestResponseAudit.trace_id == trace.id
        ).order_by(RequestResponseAudit.created_at)
        
        result = await db.execute(audit_query)
        audits = result.scalars().all()
        
        return [
            RequestResponseAuditResponse(
                id=str(audit.id),
                correlation_id=audit.correlation_id,
                request_method=audit.request_method,
                request_path=audit.request_path,
                response_status_code=audit.response_status_code,
                response_time_ms=audit.response_time_ms,
                user_id=audit.user_id,
                client_ip=audit.client_ip,
                pii_masked=audit.pii_masked,
                created_at=audit.created_at.isoformat(),
            )
            for audit in audits
        ]
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving audits: {str(e)}")


@router.get("/traces/{correlation_id}/queries", response_model=List[DatabaseQueryTraceResponse])
async def get_trace_database_queries(
    correlation_id: str,
    user_id: str = Depends(get_current_user_id),
    slow_only: bool = Query(False),
    db: AsyncSession = Depends(get_async_session),
) -> List[DatabaseQueryTraceResponse]:
    """Get database query traces for a specific trace.
    
    Args:
        correlation_id: Correlation ID of the trace
        user_id: Current user ID
        slow_only: Only return slow queries
        db: Database session
        
    Returns:
        List of database query traces
    """
    try:
        user_id = str(user_id)
        # First, get the trace to verify ownership
        trace_query = select(DistributedTrace).where(
            and_(
                DistributedTrace.correlation_id == correlation_id,
                DistributedTrace.user_id == user_id,
            )
        )
        
        trace_result = await db.execute(trace_query)
        trace = trace_result.scalar_one_or_none()
        
        if not trace:
            raise HTTPException(status_code=404, detail="Trace not found")
        
        # Get queries for this trace
        query = select(DatabaseQueryTrace).where(
            DatabaseQueryTrace.trace_id == trace.id
        )
        
        # Filter by slow queries if requested
        if slow_only:
            query = query.where(DatabaseQueryTrace.slow_query == True)
        
        query = query.order_by(DatabaseQueryTrace.created_at)
        
        result = await db.execute(query)
        queries = result.scalars().all()
        
        return [
            DatabaseQueryTraceResponse(
                id=str(query.id),
                correlation_id=query.correlation_id,
                query_type=query.query_type,
                duration_ms=query.duration_ms,
                rows_affected=query.rows_affected,
                rows_returned=query.rows_returned,
                slow_query=query.slow_query,
                status=query.status,
                error_message=query.error_message,
            )
            for query in queries
        ]
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving queries: {str(e)}")


@router.get("/slow-queries")
async def get_slow_queries(
    user_id: str = Depends(get_current_user_id),
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_async_session),
) -> List[DatabaseQueryTraceResponse]:
    """Get slow database queries from the current user's recent traces.
    
    Args:
        user_id: Current user ID
        hours: Look back hours (default: 24)
        limit: Maximum number of queries to return
        db: Database session
        
    Returns:
        List of slow database query traces
    """
    try:
        user_id = str(user_id)
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        
        # Get slow queries from user's recent traces
        query = select(DatabaseQueryTrace).join(
            DistributedTrace,
            DatabaseQueryTrace.trace_id == DistributedTrace.id
        ).where(
            and_(
                DistributedTrace.user_id == user_id,
                DistributedTrace.created_at >= cutoff_time,
                DatabaseQueryTrace.slow_query == True,
            )
        ).order_by(
            desc(DatabaseQueryTrace.duration_ms)
        ).limit(limit)
        
        result = await db.execute(query)
        queries = result.scalars().all()
        
        return [
            DatabaseQueryTraceResponse(
                id=str(q.id),
                correlation_id=q.correlation_id,
                query_type=q.query_type,
                duration_ms=q.duration_ms,
                rows_affected=q.rows_affected,
                rows_returned=q.rows_returned,
                slow_query=q.slow_query,
                status=q.status,
                error_message=q.error_message,
            )
            for q in queries
        ]
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving slow queries: {str(e)}")