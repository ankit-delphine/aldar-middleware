"""API endpoints for rate limiting and usage quotas."""

from datetime import datetime
from typing import Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from aldar_middleware.auth.dependencies import get_current_user_id
from aldar_middleware.database.base import get_db
from aldar_middleware.services.rate_limit_service import RateLimitService
from aldar_middleware.services.quota_service import QuotaService, QuotaExceededError


# Request/Response models

class RateLimitConfigCreate(BaseModel):
    """Create rate limit configuration request."""

    scope_type: str = Field(..., description="Scope: 'user', 'agent', or 'method'")
    requests_per_minute: int = Field(default=100, ge=1, description="Requests per minute limit")
    concurrent_executions: int = Field(default=10, ge=1, description="Concurrent execution limit")
    agent_id: Optional[UUID] = Field(None, description="Agent ID (required for agent/method scope)")
    method_id: Optional[str] = Field(None, description="Method name (required for method scope)")
    throttle_enabled: bool = Field(default=True, description="Enable throttling vs rejection")
    burst_size: Optional[int] = Field(None, ge=1, description="Allow burst above limit")
    description: Optional[str] = Field(None, description="Configuration description")


class RateLimitConfigUpdate(BaseModel):
    """Update rate limit configuration request."""

    requests_per_minute: Optional[int] = Field(None, ge=1)
    concurrent_executions: Optional[int] = Field(None, ge=1)
    throttle_enabled: Optional[bool] = None
    burst_size: Optional[int] = Field(None, ge=1)
    description: Optional[str] = None


class RateLimitConfigResponse(BaseModel):
    """Rate limit configuration response."""

    id: UUID
    scope_type: str
    requests_per_minute: int
    concurrent_executions: int
    agent_id: Optional[UUID]
    method_id: Optional[str]
    throttle_enabled: bool
    burst_size: Optional[int]
    is_active: bool
    description: Optional[str]
    created_at: datetime
    updated_at: datetime


class CostModelCreate(BaseModel):
    """Create cost model request."""

    agent_id: Optional[UUID] = Field(None, description="Agent ID (optional, for agent-level)")
    method_id: Optional[str] = Field(None, description="Method name (optional, for method-level)")
    per_execution: float = Field(default=0.001, ge=0, description="Cost per execution")
    per_result_kb: float = Field(default=0.0001, ge=0, description="Cost per KB of result")
    per_token: Optional[float] = Field(None, ge=0, description="Cost per token")
    minimum_charge: float = Field(default=0.001, ge=0, description="Minimum charge per call")
    volume_discount_threshold: Optional[int] = Field(None, ge=1, description="Executions for discount")
    volume_discount_percent: Optional[float] = Field(None, ge=0, le=100, description="Discount %")


class CostModelResponse(BaseModel):
    """Cost model response."""

    id: UUID
    agent_id: Optional[UUID]
    method_id: Optional[str]
    per_execution: float
    per_result_kb: float
    per_token: Optional[float]
    minimum_charge: float
    volume_discount_threshold: Optional[int]
    volume_discount_percent: Optional[float]
    is_active: bool
    created_at: datetime


class UsageQuotaCreate(BaseModel):
    """Create usage quota request."""

    quota_type: str = Field(..., description="'monthly', 'yearly', or 'custom'")
    max_executions: Optional[int] = Field(None, ge=1, description="Max executions")
    max_cost: Optional[float] = Field(None, ge=0, description="Max cost in USD")
    max_concurrent: Optional[int] = Field(None, ge=1, description="Max concurrent")
    custom_days: int = Field(default=30, ge=1, description="Days for custom quota")


class UsageQuotaResponse(BaseModel):
    """Usage quota response."""

    id: UUID
    quota_type: str
    period_start: datetime
    period_end: datetime
    max_executions: Optional[int]
    max_cost: Optional[float]
    executions_used: int
    cost_used: float
    is_active: bool
    is_exceeded: bool


class UserBudgetResponse(BaseModel):
    """User budget response."""

    monthly_budget: Optional[float]
    total_budget: Optional[float]
    current_month_spent: float
    total_spent: float
    enforce_limit: bool
    alert_at_percent: float
    is_active: bool


class UsageReportResponse(BaseModel):
    """Usage report response."""

    id: UUID
    report_period: str
    period_start: datetime
    period_end: datetime
    total_executions: int
    total_cost: float
    average_cost_per_execution: float
    error_rate_percent: float
    success_count: int
    error_count: int


# Router
router = APIRouter(tags=["quotas"])


# Rate Limit Configuration Endpoints

@router.post(
    "/rate-limits",
    response_model=RateLimitConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create rate limit configuration",
)
async def create_rate_limit(
    request: RateLimitConfigCreate,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    redis=Depends(lambda: None),
) -> RateLimitConfigResponse:
    """Create a new rate limit configuration for user/agent/method.

    Args:
        request: Configuration details
        user_id: Current user ID
        db: Database session
        redis: Redis client (from dependency)

    Returns:
        Created RateLimitConfigResponse

    Raises:
        HTTPException: If validation fails
    """
    service = RateLimitService(db, redis)

    try:
        config = await service.create_rate_limit_config(
            user_id=user_id,
            scope_type=request.scope_type,
            requests_per_minute=request.requests_per_minute,
            concurrent_executions=request.concurrent_executions,
            agent_id=request.agent_id,
            method_id=request.method_id,
            throttle_enabled=request.throttle_enabled,
            burst_size=request.burst_size,
            description=request.description,
        )

        return RateLimitConfigResponse(
            id=config.id,
            scope_type=config.scope_type,
            requests_per_minute=config.requests_per_minute,
            concurrent_executions=config.concurrent_executions,
            agent_id=config.agent_id,
            method_id=config.method_id,
            throttle_enabled=config.throttle_enabled,
            burst_size=config.burst_size,
            is_active=config.is_active,
            description=config.description,
            created_at=config.created_at,
            updated_at=config.updated_at,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create rate limit config: {str(e)}",
        )


@router.get(
    "/rate-limits",
    response_model=List[RateLimitConfigResponse],
    summary="List rate limit configurations",
)
async def list_rate_limits(
    user_id: UUID = Depends(get_current_user_id),
    scope_type: Optional[str] = Query(None, description="Filter by scope type"),
    db: AsyncSession = Depends(get_db),
    redis=Depends(lambda: None),
) -> List[RateLimitConfigResponse]:
    """List rate limit configurations for current user.

    Args:
        user_id: Current user ID
        scope_type: Optional filter by scope type
        db: Database session
        redis: Redis client

    Returns:
        List of RateLimitConfigResponse
    """
    service = RateLimitService(db, redis)

    configs = await service.get_rate_limit_configs(user_id, scope_type)

    return [
        RateLimitConfigResponse(
            id=config.id,
            scope_type=config.scope_type,
            requests_per_minute=config.requests_per_minute,
            concurrent_executions=config.concurrent_executions,
            agent_id=config.agent_id,
            method_id=config.method_id,
            throttle_enabled=config.throttle_enabled,
            burst_size=config.burst_size,
            is_active=config.is_active,
            description=config.description,
            created_at=config.created_at,
            updated_at=config.updated_at,
        )
        for config in configs
    ]


@router.get(
    "/rate-limits/{config_id}",
    response_model=RateLimitConfigResponse,
    summary="Get rate limit configuration",
)
async def get_rate_limit(
    config_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    redis=Depends(lambda: None),
) -> RateLimitConfigResponse:
    """Get specific rate limit configuration.

    Args:
        config_id: Configuration ID
        user_id: Current user ID
        db: Database session
        redis: Redis client

    Returns:
        RateLimitConfigResponse

    Raises:
        HTTPException: If not found
    """
    # TODO: Implement get_rate_limit_config method in service
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


@router.put(
    "/rate-limits/{config_id}",
    response_model=RateLimitConfigResponse,
    summary="Update rate limit configuration",
)
async def update_rate_limit(
    config_id: UUID,
    request: RateLimitConfigUpdate,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    redis=Depends(lambda: None),
) -> RateLimitConfigResponse:
    """Update rate limit configuration.

    Args:
        config_id: Configuration ID
        request: Update details
        user_id: Current user ID
        db: Database session
        redis: Redis client

    Returns:
        Updated RateLimitConfigResponse

    Raises:
        HTTPException: If not found or update fails
    """
    service = RateLimitService(db, redis)

    try:
        updates = {k: v for k, v in request.dict().items() if v is not None}

        config = await service.update_rate_limit_config(config_id, user_id, **updates)

        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Rate limit configuration not found",
            )

        return RateLimitConfigResponse(
            id=config.id,
            scope_type=config.scope_type,
            requests_per_minute=config.requests_per_minute,
            concurrent_executions=config.concurrent_executions,
            agent_id=config.agent_id,
            method_id=config.method_id,
            throttle_enabled=config.throttle_enabled,
            burst_size=config.burst_size,
            is_active=config.is_active,
            description=config.description,
            created_at=config.created_at,
            updated_at=config.updated_at,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to update rate limit config: {str(e)}",
        )


@router.delete(
    "/rate-limits/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete rate limit configuration",
)
async def delete_rate_limit(
    config_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    redis=Depends(lambda: None),
) -> None:
    """Delete rate limit configuration.

    Args:
        config_id: Configuration ID
        user_id: Current user ID
        db: Database session
        redis: Redis client

    Raises:
        HTTPException: If not found
    """
    service = RateLimitService(db, redis)

    deleted = await service.delete_rate_limit_config(config_id, user_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rate limit configuration not found",
        )


# Cost Model Endpoints

@router.post(
    "/cost-models",
    response_model=CostModelResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create cost model",
)
async def create_cost_model(
    request: CostModelCreate,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> CostModelResponse:
    """Create cost model for agent/method.

    Args:
        request: Cost model details
        user_id: Current user ID
        db: Database session

    Returns:
        Created CostModelResponse

    Raises:
        HTTPException: If validation fails
    """
    service = QuotaService(db)

    try:
        model = await service.set_cost_model(
            user_id=user_id,
            agent_id=request.agent_id,
            method_id=request.method_id,
            per_execution=request.per_execution,
            per_result_kb=request.per_result_kb,
            per_token=request.per_token,
            minimum_charge=request.minimum_charge,
            volume_discount_threshold=request.volume_discount_threshold,
            volume_discount_percent=request.volume_discount_percent,
        )

        return CostModelResponse(
            id=model.id,
            agent_id=model.agent_id,
            method_id=model.method_id,
            per_execution=model.per_execution,
            per_result_kb=model.per_result_kb,
            per_token=model.per_token,
            minimum_charge=model.minimum_charge,
            volume_discount_threshold=model.volume_discount_threshold,
            volume_discount_percent=model.volume_discount_percent,
            is_active=model.is_active,
            created_at=model.created_at,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create cost model: {str(e)}",
        )


# Quota Endpoints

@router.post(
    "/quotas",
    response_model=UsageQuotaResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create usage quota",
)
async def create_quota(
    request: UsageQuotaCreate,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> UsageQuotaResponse:
    """Create usage quota for user.

    Args:
        request: Quota details
        user_id: Current user ID
        db: Database session

    Returns:
        Created UsageQuotaResponse

    Raises:
        HTTPException: If validation fails
    """
    service = QuotaService(db)

    try:
        quota = await service.create_usage_quota(
            user_id=user_id,
            quota_type=request.quota_type,
            max_executions=request.max_executions,
            max_cost=request.max_cost,
            max_concurrent=request.max_concurrent,
            custom_days=request.custom_days,
        )

        return UsageQuotaResponse(
            id=quota.id,
            quota_type=quota.quota_type,
            period_start=quota.period_start,
            period_end=quota.period_end,
            max_executions=quota.max_executions,
            max_cost=quota.max_cost,
            executions_used=quota.executions_used,
            cost_used=quota.cost_used,
            is_active=quota.is_active,
            is_exceeded=quota.is_exceeded,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create quota: {str(e)}",
        )


# Usage Reporting Endpoints

@router.get(
    "/usage-report",
    response_model=UsageReportResponse,
    summary="Get usage report",
)
async def get_usage_report(
    user_id: UUID = Depends(get_current_user_id),
    period_type: str = Query("monthly", description="'daily', 'weekly', 'monthly', or 'yearly'"),
    db: AsyncSession = Depends(get_db),
) -> Dict:
    """Get usage report for current user.

    Args:
        user_id: Current user ID
        period_type: Report period type
        db: Database session

    Returns:
        Usage report data

    Raises:
        HTTPException: If report not found
    """
    service = QuotaService(db)

    try:
        report = await service.get_usage_report(user_id, period_type)

        if not report:
            # Generate new report
            report = await service.generate_usage_report(user_id, period_type)

        return UsageReportResponse(
            id=report.id,
            report_period=report.report_period,
            period_start=report.period_start,
            period_end=report.period_end,
            total_executions=report.total_executions,
            total_cost=report.total_cost,
            average_cost_per_execution=report.average_cost_per_execution,
            error_rate_percent=report.error_rate_percent,
            success_count=report.success_count,
            error_count=report.error_count,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to get usage report: {str(e)}",
        )


@router.get(
    "/current-quota",
    response_model=Dict,
    summary="Get current quota usage",
)
async def get_current_quota(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> Dict:
    """Get current quota usage for user.

    Args:
        user_id: Current user ID
        db: Database session

    Returns:
        Current quota status

    Raises:
        HTTPException: If error occurs
    """
    service = QuotaService(db)

    try:
        # Check 0 cost (just to get quota info)
        result = await service.check_quota_available(user_id, 0.0)
        return result
    except QuotaExceededError as e:
        return {
            "exceeded": True,
            "message": e.message,
            "current_usage": e.current_usage,
            "limit": e.limit,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to get quota: {str(e)}",
        )