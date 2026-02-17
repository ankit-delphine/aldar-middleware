"""Automated Remediation Service.

This service handles automatic remediation of issues detected.
It receives alerts, applies safety guardrails, and executes
appropriate remediation actions to restore service health.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
import uuid

from aldar_middleware.models import (
    RemediationAction,
    RemediationRule,
    RemediationExecution,
    ActionType,
    ExecutionStatus,
)

logger = logging.getLogger(__name__)


class SafetyGuardrailsChecker:
    """Checks all safety guardrails before executing a remediation action."""

    def __init__(self, session: AsyncSession):
        """Initialize the safety guardrails checker.
        
        Args:
            session: AsyncSession for database operations
        """
        self.session = session

    async def check_rate_limit(self, max_per_minute: int = 5) -> bool:
        """Check if we're within rate limit (max N remediations per minute).
        
        Args:
            max_per_minute: Maximum remediations allowed per minute
            
        Returns:
            True if within limit, False if exceeded
        """
        one_minute_ago = datetime.utcnow() - timedelta(minutes=1)
        
        # Count successful remediations in last minute
        stmt = select(RemediationExecution).where(
            and_(
                RemediationExecution.created_at >= one_minute_ago,
                RemediationExecution.status == ExecutionStatus.SUCCESS,
            )
        )
        result = await self.session.execute(stmt)
        count = len(result.scalars().all())
        
        if count >= max_per_minute:
            logger.warning(
                f"Rate limit exceeded: {count} remediations in last minute "
                f"(max: {max_per_minute})"
            )
            return False
        
        return True

    async def check_cooldown(
        self, action_id: str, cooldown_minutes: int = 5
    ) -> bool:
        """Check if action has cooled down since last execution.
        
        Args:
            action_id: ID of the action to check
            cooldown_minutes: Minutes to wait between same action
            
        Returns:
            True if cooled down, False if still in cooldown
        """
        cutoff_time = datetime.utcnow() - timedelta(minutes=cooldown_minutes)
        
        # Find last successful execution of this action
        stmt = select(RemediationExecution).where(
            and_(
                RemediationExecution.action_id == action_id,
                RemediationExecution.status == ExecutionStatus.SUCCESS,
                RemediationExecution.created_at >= cutoff_time,
            )
        ).order_by(RemediationExecution.created_at.desc())
        
        result = await self.session.execute(stmt)
        last_execution = result.scalars().first()
        
        if last_execution:
            logger.warning(
                f"Cooldown period active: action {action_id} executed at "
                f"{last_execution.created_at}, cooldown: {cooldown_minutes} min"
            )
            return False
        
        return True

    async def check_cascading_failure_prevention(
        self, max_affected_percent: float = 50.0
    ) -> bool:
        """Check if too many services are affected (cascading failure).
        
        Args:
            max_affected_percent: Max % of services that can be failing
            
        Returns:
            True if safe, False if cascading failure detected
        """
        # This is a placeholder - actual implementation would check service health
        # For now, assume it's safe to proceed
        return True

    async def check_resource_availability(
        self, action: RemediationAction
    ) -> bool:
        """Check if resources are available for the action.
        
        Args:
            action: The remediation action to check
            
        Returns:
            True if resources available, False otherwise
        """
        # This is a placeholder - actual implementation would check:
        # - CPU/memory available for scaling
        # - Database connections available
        # - Kubernetes resources available, etc.
        return True

    async def check_budget_safeguards(
        self, action: RemediationAction
    ) -> bool:
        """Check if action would exceed budget.
        
        Args:
            action: The remediation action to check
            
        Returns:
            True if within budget, False if would exceed
        """
        # This is a placeholder - actual implementation would check:
        # - Current costs
        # - Projected costs after action
        # - Remaining budget
        return True

    async def check_all_guardrails(
        self, action: RemediationAction
    ) -> tuple[bool, Optional[str]]:
        """Check all safety guardrails.
        
        Args:
            action: The remediation action to check
            
        Returns:
            Tuple of (passed: bool, reason_if_failed: Optional[str])
        """
        # Get safety guardrails for this action
        guardrails = action.safety_guardrails or {}
        
        # Check rate limit
        max_per_minute = guardrails.get("max_executions_per_minute", 5)
        if not await self.check_rate_limit(max_per_minute):
            return False, "Rate limit exceeded"
        
        # Check cooldown
        cooldown_minutes = guardrails.get("cooldown_minutes", 5)
        if not await self.check_cooldown(action.id, cooldown_minutes):
            return False, "Cooldown period active"
        
        # Check cascading failures
        if not await self.check_cascading_failure_prevention():
            return False, "Cascading failure detected"
        
        # Check resource availability
        if not await self.check_resource_availability(action):
            return False, "Insufficient resources"
        
        # Check budget
        if not await self.check_budget_safeguards(action):
            return False, "Budget would be exceeded"
        
        logger.info(f"All safety guardrails passed for action {action.id}")
        return True, None


class RemediationDecisionEngine:
    """Decides which remediation action to execute based on alert."""

    def __init__(self, session: AsyncSession):
        """Initialize the decision engine.
        
        Args:
            session: AsyncSession for database operations
        """
        self.session = session

    async def find_matching_rule(
        self, alert_type: str, alert_severity: str
    ) -> Optional[RemediationRule]:
        """Find a remediation rule matching the alert.
        
        Args:
            alert_type: Type of alert
            alert_severity: Severity level (critical, warning, etc.)
            
        Returns:
            Matching RemediationRule or None
        """
        stmt = select(RemediationRule).where(
            and_(
                RemediationRule.alert_type == alert_type,
                RemediationRule.alert_severity == alert_severity,
                RemediationRule.enabled is True,
            )
        ).order_by(RemediationRule.priority)
        
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_action_for_rule(
        self, rule: RemediationRule
    ) -> Optional[RemediationAction]:
        """Get the remediation action for a rule.
        
        Args:
            rule: The remediation rule
            
        Returns:
            The associated RemediationAction or None
        """
        stmt = select(RemediationAction).where(
            RemediationAction.id == rule.action_id
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()


class RemediationExecutor:
    """Executes remediation actions."""

    def __init__(self, session: AsyncSession):
        """Initialize the executor.
        
        Args:
            session: AsyncSession for database operations
        """
        self.session = session

    async def create_execution_record(
        self,
        action_id: str,
        alert_id: str,
        trigger_reason: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> RemediationExecution:
        """Create a new remediation execution record.
        
        Args:
            action_id: ID of the action being executed
            alert_id: ID of the alert that triggered this
            trigger_reason: Why this action was triggered
            parameters: Parameters for the action
            
        Returns:
            The created RemediationExecution record
        """
        execution = RemediationExecution(
            id=str(uuid.uuid4()),
            action_id=action_id,
            alert_id=alert_id,
            status=ExecutionStatus.PENDING,
            trigger_reason=trigger_reason,
            execution_parameters=parameters or {},
            created_at=datetime.utcnow(),
        )
        self.session.add(execution)
        await self.session.commit()
        logger.info(f"Created execution record: {execution.id}")
        return execution

    async def update_execution_status(
        self,
        execution_id: str,
        status: ExecutionStatus,
        **kwargs,
    ) -> None:
        """Update the status of an execution.
        
        Args:
            execution_id: ID of the execution to update
            status: New status
            **kwargs: Additional fields to update (metrics_before, error_message, etc.)
        """
        stmt = select(RemediationExecution).where(
            RemediationExecution.id == execution_id
        )
        result = await self.session.execute(stmt)
        execution = result.scalars().first()
        
        if not execution:
            logger.error(f"Execution not found: {execution_id}")
            return
        
        execution.status = status
        execution.updated_at = datetime.utcnow()
        
        # Update additional fields
        for key, value in kwargs.items():
            if hasattr(execution, key):
                setattr(execution, key, value)
        
        if status == ExecutionStatus.SUCCESS:
            execution.completed_at = datetime.utcnow()
            execution.success = True
        elif status == ExecutionStatus.FAILED:
            execution.completed_at = datetime.utcnow()
            execution.success = False
        elif status == ExecutionStatus.ROLLED_BACK:
            execution.rolled_back = True
            execution.rollback_at = datetime.utcnow()
        
        await self.session.commit()
        logger.info(f"Updated execution {execution_id} to status {status}")

    async def simulate_action(
        self, action: RemediationAction, parameters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Simulate what would happen if we execute this action (dry-run).
        
        Args:
            action: The action to simulate
            parameters: Parameters for the action
            
        Returns:
            Simulation result with predicted outcome
        """
        logger.info(f"Simulating action {action.id} with parameters {parameters}")
        
        # TODO: Implement actual simulation logic per action type
        # This is a placeholder
        simulation_result = {
            "simulated": True,
            "action_type": action.action_type,
            "predicted_outcome": f"Action would execute {action.action_type}",
            "estimated_time_seconds": 10,
            "predicted_success": True,
            "warnings": [],
        }
        
        return simulation_result


class RemediationService:
    """Main remediation service orchestrating."""

    def __init__(self, session: AsyncSession):
        """Initialize the remediation service.
        
        Args:
            session: AsyncSession for database operations
        """
        self.session = session
        self.guardrails_checker = SafetyGuardrailsChecker(session)
        self.decision_engine = RemediationDecisionEngine(session)
        self.executor = RemediationExecutor(session)

    async def process_alert(
        self,
        alert_type: str,
        alert_id: str,
        alert_severity: str,
        alert_metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Process an alert from and execute remediation if appropriate.
        
        Args:
            alert_type: Type of alert (e.g., "extreme_latency")
            alert_id: Unique ID of the alert
            alert_severity: Severity level (critical, warning, etc.)
            alert_metadata: Additional metadata about the alert
            
        Returns:
            Execution ID if action was taken, None otherwise
        """
        logger.info(
            f"Processing alert: type={alert_type}, id={alert_id}, "
            f"severity={alert_severity}"
        )
        
        # Step 1: Find matching remediation rule
        rule = await self.decision_engine.find_matching_rule(
            alert_type, alert_severity
        )
        
        if not rule:
            logger.info(f"No remediation rule found for alert {alert_type}")
            return None
        
        # Step 2: Get the action
        action = await self.decision_engine.get_action_for_rule(rule)
        
        if not action or not action.enabled:
            logger.info(f"Action for rule {rule.id} not found or disabled")
            return None
        
        # Step 3: Check all safety guardrails
        passed, reason = await self.guardrails_checker.check_all_guardrails(action)
        
        if not passed:
            logger.warning(
                f"Safety guardrails check failed: {reason}. "
                f"Alert {alert_id} not remediated."
            )
            return None
        
        # Step 4: Create execution record
        execution = await self.executor.create_execution_record(
            action_id=action.id,
            alert_id=alert_id,
            trigger_reason=f"Alert {alert_type} triggered remediation",
            parameters=rule.condition_config or {},
        )
        
        # Step 5: Dry-run if required
        if rule.dry_run_first:
            logger.info(f"Running dry-run for execution {execution.id}")
            await self.executor.update_execution_status(
                execution.id, ExecutionStatus.DRY_RUN
            )
            
            dry_run_result = await self.executor.simulate_action(
                action, rule.condition_config or {}
            )
            
            await self.executor.update_execution_status(
                execution.id,
                ExecutionStatus.PENDING,
                dry_run_result=dry_run_result,
            )
            
            logger.info(
                f"Dry-run result: {dry_run_result.get('predicted_outcome', 'Unknown')}"
            )
        
        # Step 6: Check if auto-execute or require approval
        if not rule.auto_execute:
            logger.info(
                f"Execution {execution.id} requires approval before proceeding"
            )
            return execution.id
        
        # Step 7: Execute the action
        logger.info(f"Executing action {action.id} for execution {execution.id}")
        await self.executor.update_execution_status(
            execution.id, ExecutionStatus.EXECUTING
        )
        
        # TODO: Implement actual action execution per ActionType
        # For now, mark as success
        await self.executor.update_execution_status(
            execution.id,
            ExecutionStatus.SUCCESS,
            impact=f"Action {action.action_type} completed successfully",
        )
        
        logger.info(f"Remediation completed: {execution.id}")
        return execution.id

    async def get_action(self, action_id: str) -> Optional[RemediationAction]:
        """Get a remediation action by ID.
        
        Args:
            action_id: ID of the action
            
        Returns:
            RemediationAction or None
        """
        stmt = select(RemediationAction).where(RemediationAction.id == action_id)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_all_actions(self) -> List[RemediationAction]:
        """Get all remediation actions.
        
        Returns:
            List of all RemediationAction records
        """
        stmt = select(RemediationAction).where(RemediationAction.enabled is True)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_all_rules(self) -> List[RemediationRule]:
        """Get all remediation rules.
        
        Returns:
            List of all RemediationRule records
        """
        stmt = select(RemediationRule).where(RemediationRule.enabled is True)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_execution_history(
        self, action_id: Optional[str] = None, limit: int = 100
    ) -> List[RemediationExecution]:
        """Get remediation execution history.
        
        Args:
            action_id: Optional filter by action ID
            limit: Maximum number of results
            
        Returns:
            List of RemediationExecution records
        """
        stmt = select(RemediationExecution)
        
        if action_id:
            stmt = stmt.where(RemediationExecution.action_id == action_id)
        
        stmt = stmt.order_by(
            RemediationExecution.created_at.desc()
        ).limit(limit)
        
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_statistics(self) -> Dict[str, Any]:
        """Get remediation statistics.
        
        Returns:
            Dictionary with success rate, failure rate, etc.
        """
        # Get all executions
        stmt = select(RemediationExecution)
        result = await self.session.execute(stmt)
        all_executions = result.scalars().all()
        
        if not all_executions:
            return {
                "total_executions": 0,
                "successful": 0,
                "failed": 0,
                "success_rate": 0.0,
                "rolled_back": 0,
                "average_execution_time_seconds": 0,
            }
        
        total = len(all_executions)
        successful = sum(1 for e in all_executions if e.status == ExecutionStatus.SUCCESS)
        failed = sum(1 for e in all_executions if e.status == ExecutionStatus.FAILED)
        rolled_back = sum(1 for e in all_executions if e.rolled_back)
        
        # Calculate average execution time
        execution_times = []
        for exec in all_executions:
            if exec.started_at and exec.completed_at:
                duration = (exec.completed_at - exec.started_at).total_seconds()
                execution_times.append(duration)
        
        avg_time = sum(execution_times) / len(execution_times) if execution_times else 0
        
        return {
            "total_executions": total,
            "successful": successful,
            "failed": failed,
            "success_rate": (successful / total * 100) if total > 0 else 0,
            "rolled_back": rolled_back,
            "average_execution_time_seconds": avg_time,
        }