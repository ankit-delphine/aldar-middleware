"""Usage quota and cost tracking service."""

from datetime import datetime, timedelta
from typing import Dict, Optional, List
from uuid import UUID

from loguru import logger
from sqlalchemy import select, and_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from aldar_middleware.settings.context import get_correlation_id
from aldar_middleware.models.quotas import (
    CostModel,
    UsageQuota,
    UserBudget,
    UsageReport,
)


class QuotaExceededError(Exception):
    """Quota exceeded exception."""

    def __init__(self, message: str, current_usage: float, limit: float):
        """Initialize quota exceeded error.

        Args:
            message: Error message
            current_usage: Current quota usage
            limit: Quota limit
        """
        self.message = message
        self.current_usage = current_usage
        self.limit = limit
        super().__init__(self.message)


class QuotaService:
    """Usage quota and cost tracking service."""

    def __init__(self, db: AsyncSession):
        """Initialize quota service.

        Args:
            db: Async database session
        """
        self.db = db
        self.correlation_id = get_correlation_id()

    async def calculate_execution_cost(
        self,
        user_id: UUID,
        agent_id: UUID,
        method_id: str,
        result_size_kb: float = 0.0,
        token_count: int = 0,
    ) -> float:
        """Calculate cost for an execution.

        Args:
            user_id: User ID
            agent_id: Agent ID
            method_id: Method name
            result_size_kb: Result size in KB
            token_count: Token count (for LLM methods)

        Returns:
            Total cost for execution
        """
        # Get cost model for this method/agent
        cost_model = await self._get_cost_model(user_id, agent_id, method_id)

        if not cost_model:
            # Default cost if no model found
            return 0.001

        cost = cost_model.per_execution

        # Add result size cost
        if result_size_kb > 0:
            cost += result_size_kb * cost_model.per_result_kb

        # Add token cost
        if token_count > 0 and cost_model.per_token:
            cost += token_count * cost_model.per_token

        # Apply minimum charge
        cost = max(cost, cost_model.minimum_charge)

        logger.debug(
            f"Calculated execution cost | agent={agent_id} method={method_id} cost={cost}",
            extra={"correlation_id": self.correlation_id},
        )

        return cost

    async def check_quota_available(
        self,
        user_id: UUID,
        cost: float,
    ) -> Dict:
        """Check if quota is available for execution.

        Args:
            user_id: User ID
            cost: Execution cost

        Returns:
            {
                "available": bool,
                "quota_id": UUID,
                "current_cost": float,
                "limit_cost": float,
                "executions_used": int,
                "max_executions": int,
                "usage_percent": float,
                "days_remaining": int
            }

        Raises:
            QuotaExceededError: If quota exceeded
        """
        # Get current active quota
        quota = await self._get_current_quota(user_id)

        if not quota:
            # No quota configured, allow
            logger.debug("No quota configured, allowing execution")
            return {"available": True}

        # Check cost limit
        if quota.max_cost:
            new_cost = quota.cost_used + cost
            if new_cost > quota.max_cost:
                usage_percent = (quota.cost_used / quota.max_cost) * 100
                logger.warning(
                    f"Quota cost limit exceeded | user={user_id} current={quota.cost_used} new={new_cost} limit={quota.max_cost}",
                    extra={"correlation_id": self.correlation_id},
                )
                raise QuotaExceededError(
                    f"Cost quota exceeded (${quota.cost_used:.2f}/${quota.max_cost:.2f})",
                    current_usage=quota.cost_used,
                    limit=quota.max_cost,
                )

        # Check execution count limit
        if quota.max_executions:
            if quota.executions_used >= quota.max_executions:
                logger.warning(
                    f"Quota execution limit exceeded | user={user_id} used={quota.executions_used} limit={quota.max_executions}",
                    extra={"correlation_id": self.correlation_id},
                )
                raise QuotaExceededError(
                    f"Execution quota exceeded ({quota.executions_used}/{quota.max_executions})",
                    current_usage=quota.executions_used,
                    limit=quota.max_executions,
                )

        # Calculate usage metrics
        cost_usage_percent = 0.0
        if quota.max_cost:
            cost_usage_percent = ((quota.cost_used + cost) / quota.max_cost) * 100

        exec_usage_percent = 0.0
        if quota.max_executions:
            exec_usage_percent = ((quota.executions_used + 1) / quota.max_executions) * 100

        days_remaining = int((quota.period_end - datetime.utcnow()).total_seconds() / 86400)

        return {
            "available": True,
            "quota_id": quota.id,
            "current_cost": quota.cost_used,
            "limit_cost": quota.max_cost,
            "executions_used": quota.executions_used,
            "max_executions": quota.max_executions,
            "cost_usage_percent": cost_usage_percent,
            "execution_usage_percent": exec_usage_percent,
            "days_remaining": days_remaining,
        }

    async def record_execution_cost(
        self,
        user_id: UUID,
        cost: float,
        agent_id: Optional[UUID] = None,
        method_id: Optional[str] = None,
    ) -> None:
        """Record execution cost against quota.

        Args:
            user_id: User ID
            cost: Execution cost
            agent_id: Agent ID (optional)
            method_id: Method name (optional)
        """
        # Update current quota
        quota = await self._get_current_quota(user_id)
        if quota:
            quota.cost_used += cost
            quota.executions_used += 1
            quota.updated_at = datetime.utcnow()

            # Check if exceeded
            is_exceeded = False
            if quota.max_cost and quota.cost_used > quota.max_cost:
                is_exceeded = True
            if quota.max_executions and quota.executions_used > quota.max_executions:
                is_exceeded = True

            if is_exceeded and not quota.is_exceeded:
                quota.is_exceeded = True
                logger.warning(
                    f"Quota exceeded | user={user_id} cost={quota.cost_used}/{quota.max_cost}",
                    extra={"correlation_id": self.correlation_id},
                )

            await self.db.commit()

        # Update user budget
        await self._update_user_budget(user_id, cost)

    async def check_budget_available(
        self,
        user_id: UUID,
        cost: float,
    ) -> Dict:
        """Check if budget is available.

        Args:
            user_id: User ID
            cost: Execution cost

        Returns:
            {
                "available": bool,
                "monthly_budget": float,
                "monthly_spent": float,
                "total_budget": float,
                "total_spent": float,
                "monthly_percent": float,
                "total_percent": float
            }

        Raises:
            QuotaExceededError: If budget exceeded and enforce_limit enabled
        """
        budget = await self._get_or_create_user_budget(user_id)

        # Check monthly budget
        if budget.monthly_budget:
            new_spent = budget.current_month_spent + cost
            if new_spent > budget.monthly_budget and budget.enforce_limit:
                raise QuotaExceededError(
                    f"Monthly budget exceeded (${budget.current_month_spent:.2f}/${budget.monthly_budget:.2f})",
                    current_usage=budget.current_month_spent,
                    limit=budget.monthly_budget,
                )

        # Check total budget
        if budget.total_budget:
            new_total = budget.total_spent + cost
            if new_total > budget.total_budget and budget.enforce_limit:
                raise QuotaExceededError(
                    f"Total budget exceeded (${budget.total_spent:.2f}/${budget.total_budget:.2f})",
                    current_usage=budget.total_spent,
                    limit=budget.total_budget,
                )

        monthly_percent = 0.0
        if budget.monthly_budget:
            monthly_percent = ((budget.current_month_spent + cost) / budget.monthly_budget) * 100

        total_percent = 0.0
        if budget.total_budget:
            total_percent = ((budget.total_spent + cost) / budget.total_budget) * 100

        return {
            "available": True,
            "monthly_budget": budget.monthly_budget,
            "monthly_spent": budget.current_month_spent,
            "total_budget": budget.total_budget,
            "total_spent": budget.total_spent,
            "monthly_percent": monthly_percent,
            "total_percent": total_percent,
        }

    async def create_usage_quota(
        self,
        user_id: UUID,
        quota_type: str = "monthly",
        max_executions: Optional[int] = None,
        max_cost: Optional[float] = None,
        max_concurrent: Optional[int] = None,
        custom_days: int = 30,
    ) -> UsageQuota:
        """Create a usage quota.

        Args:
            user_id: User ID
            quota_type: "monthly", "yearly", or "custom"
            max_executions: Max executions allowed
            max_cost: Max cost allowed
            max_concurrent: Max concurrent executions
            custom_days: Days for custom quota

        Returns:
            Created UsageQuota
        """
        now = datetime.utcnow()

        if quota_type == "monthly":
            period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            # Next month
            if now.month == 12:
                period_end = period_start.replace(year=now.year + 1, month=1)
            else:
                period_end = period_start.replace(month=now.month + 1)
        elif quota_type == "yearly":
            period_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            period_end = period_start.replace(year=now.year + 1)
        else:  # custom
            period_start = now
            period_end = now + timedelta(days=custom_days)

        quota = UsageQuota(
            user_id=user_id,
            quota_type=quota_type,
            period_start=period_start,
            period_end=period_end,
            max_executions=max_executions,
            max_cost=max_cost,
            max_concurrent=max_concurrent,
        )
        self.db.add(quota)
        await self.db.commit()
        await self.db.refresh(quota)

        logger.info(
            f"Created usage quota | user={user_id} type={quota_type} cost_limit={max_cost}",
            extra={"correlation_id": self.correlation_id},
        )

        return quota

    async def set_cost_model(
        self,
        user_id: UUID,
        agent_id: Optional[UUID] = None,
        method_id: Optional[str] = None,
        per_execution: float = 0.001,
        per_result_kb: float = 0.0001,
        per_token: Optional[float] = None,
        minimum_charge: float = 0.001,
        volume_discount_threshold: Optional[int] = None,
        volume_discount_percent: Optional[float] = None,
    ) -> CostModel:
        """Set cost model for agent/method.

        Args:
            user_id: User ID
            agent_id: Agent ID (optional, for agent-level model)
            method_id: Method name (optional, for method-level model)
            per_execution: Cost per execution
            per_result_kb: Cost per KB of result
            per_token: Cost per token
            minimum_charge: Minimum charge per call
            volume_discount_threshold: Executions to reach discount
            volume_discount_percent: Discount percentage

        Returns:
            Created or updated CostModel
        """
        # Find existing model
        stmt = select(CostModel).where(
            and_(
                CostModel.user_id == user_id,
                CostModel.agent_id == agent_id,
                CostModel.method_id == method_id,
                CostModel.is_active == True,
            )
        )
        result = await self.db.execute(stmt)
        model = result.scalars().first()

        if not model:
            model = CostModel(
                user_id=user_id,
                agent_id=agent_id,
                method_id=method_id,
            )
            self.db.add(model)

        model.per_execution = per_execution
        model.per_result_kb = per_result_kb
        model.per_token = per_token
        model.minimum_charge = minimum_charge
        model.volume_discount_threshold = volume_discount_threshold
        model.volume_discount_percent = volume_discount_percent
        model.updated_at = datetime.utcnow()

        await self.db.commit()
        await self.db.refresh(model)

        logger.info(
            f"Set cost model | user={user_id} agent={agent_id} per_exec={per_execution}",
            extra={"correlation_id": self.correlation_id},
        )

        return model

    async def get_usage_report(
        self,
        user_id: UUID,
        period_type: str = "monthly",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Optional[UsageReport]:
        """Get usage report for user.

        Args:
            user_id: User ID
            period_type: "daily", "weekly", "monthly", "yearly"
            start_date: Optional start date
            end_date: Optional end date

        Returns:
            UsageReport or None
        """
        now = datetime.utcnow()

        if not start_date or not end_date:
            if period_type == "daily":
                start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
                end_date = start_date + timedelta(days=1)
            elif period_type == "weekly":
                # Monday of this week
                days_since_monday = now.weekday()
                start_date = (now - timedelta(days=days_since_monday)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                end_date = start_date + timedelta(days=7)
            elif period_type == "yearly":
                start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
                end_date = start_date.replace(year=now.year + 1)
            else:  # monthly
                start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                # Next month
                if now.month == 12:
                    end_date = start_date.replace(year=now.year + 1, month=1)
                else:
                    end_date = start_date.replace(month=now.month + 1)

        stmt = select(UsageReport).where(
            and_(
                UsageReport.user_id == user_id,
                UsageReport.report_period == period_type,
                UsageReport.period_start >= start_date,
                UsageReport.period_end <= end_date,
                UsageReport.is_finalized == True,
            )
        ).order_by(desc(UsageReport.generated_at))

        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def generate_usage_report(
        self,
        user_id: UUID,
        period_type: str = "monthly",
    ) -> UsageReport:
        """Generate usage report.

        Args:
            user_id: User ID
            period_type: "daily", "weekly", "monthly", "yearly"

        Returns:
            Generated UsageReport
        """
        # Get period boundaries
        now = datetime.utcnow()

        if period_type == "daily":
            period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            period_end = period_start + timedelta(days=1)
        elif period_type == "weekly":
            days_since_monday = now.weekday()
            period_start = (now - timedelta(days=days_since_monday)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            period_end = period_start + timedelta(days=7)
        elif period_type == "yearly":
            period_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            period_end = period_start.replace(year=now.year + 1)
        else:  # monthly
            period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if now.month == 12:
                period_end = period_start.replace(year=now.year + 1, month=1)
            else:
                period_end = period_start.replace(month=now.month + 1)

        # Create report
        report = UsageReport(
            user_id=user_id,
            report_period=period_type,
            period_start=period_start,
            period_end=period_end,
            is_finalized=True,
        )

        self.db.add(report)
        await self.db.commit()
        await self.db.refresh(report)

        logger.info(
            f"Generated usage report | user={user_id} period={period_type}",
            extra={"correlation_id": self.correlation_id},
        )

        return report

    # Private helper methods

    async def _get_current_quota(self, user_id: UUID) -> Optional[UsageQuota]:
        """Get current active quota for user."""
        now = datetime.utcnow()
        stmt = (
            select(UsageQuota)
            .where(
                and_(
                    UsageQuota.user_id == user_id,
                    UsageQuota.is_active == True,
                    UsageQuota.period_start <= now,
                    UsageQuota.period_end > now,
                )
            )
            .order_by(desc(UsageQuota.created_at))
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def _get_cost_model(
        self,
        user_id: UUID,
        agent_id: UUID,
        method_id: str,
    ) -> Optional[CostModel]:
        """Get cost model for method/agent.

        Tries method-level > agent-level > user-level.
        """
        now = datetime.utcnow()

        # Method-level first
        stmt = select(CostModel).where(
            and_(
                CostModel.user_id == user_id,
                CostModel.agent_id == agent_id,
                CostModel.method_id == method_id,
                CostModel.is_active == True,
                or_(
                    CostModel.effective_from.is_(None),
                    CostModel.effective_from <= now,
                ),
                or_(
                    CostModel.effective_to.is_(None),
                    CostModel.effective_to > now,
                ),
            )
        )

        from sqlalchemy import or_

        result = await self.db.execute(stmt)
        model = result.scalars().first()
        if model:
            return model

        # Agent-level
        stmt = select(CostModel).where(
            and_(
                CostModel.user_id == user_id,
                CostModel.agent_id == agent_id,
                CostModel.method_id.is_(None),
                CostModel.is_active == True,
            )
        )
        result = await self.db.execute(stmt)
        model = result.scalars().first()
        if model:
            return model

        # User-level
        stmt = select(CostModel).where(
            and_(
                CostModel.user_id == user_id,
                CostModel.agent_id.is_(None),
                CostModel.method_id.is_(None),
                CostModel.is_active == True,
            )
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def _update_user_budget(
        self,
        user_id: UUID,
        cost: float,
    ) -> None:
        """Update user budget with execution cost."""
        budget = await self._get_or_create_user_budget(user_id)

        budget.total_spent += cost
        budget.current_month_spent += cost
        budget.updated_at = datetime.utcnow()

        await self.db.commit()

    async def _get_or_create_user_budget(
        self,
        user_id: UUID,
    ) -> UserBudget:
        """Get or create user budget."""
        stmt = select(UserBudget).where(UserBudget.user_id == user_id)
        result = await self.db.execute(stmt)
        budget = result.scalars().first()

        if not budget:
            now = datetime.utcnow()
            budget = UserBudget(
                user_id=user_id,
                month_start=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
            )
            self.db.add(budget)
            await self.db.commit()
            await self.db.refresh(budget)

        return budget