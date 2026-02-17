"""Intelligent agent routing and selection service."""

import random
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select, func, desc, and_
from datetime import datetime, timedelta

from aldar_middleware.settings.context import get_correlation_id, track_agent_call
from aldar_middleware.models.routing import (
    AgentCapability,
    RoutingPolicy,
    RoutingExecution,
)
from aldar_middleware.models.mcp import AgentMethodExecution
from aldar_middleware.models.user import UserAgent


class RoutingService:
    """Intelligent agent selection and routing service."""

    def __init__(self, db: AsyncSession):
        """Initialize routing service.
        
        Args:
            db: Async database session
        """
        self.db = db
        self.correlation_id = get_correlation_id()

    async def select_agent(
        self,
        user_id: UUID,
        policy_id: Optional[UUID] = None,
        request_context: Optional[Dict] = None,
        candidates: Optional[List[UUID]] = None,
    ) -> Dict:
        """Select best agent based on routing policy.

        Args:
            user_id: User ID
            policy_id: Routing policy ID (uses default if not specified)
            request_context: Request context for routing decision
            candidates: List of candidate agent IDs (uses all if not specified)

        Returns:
            {
                "agent_id": UUID,
                "reason": str,
                "scores": {agent_id: score, ...},
                "confidence": float,
                "matched_rules": [...]
            }

        Raises:
            ValueError: If no agents available or policy not found
        """
        logger.info(
            "Selecting agent | user_id={user_id} policy_id={policy_id}",
            user_id=user_id,
            policy_id=policy_id,
            extra={"correlation_id": self.correlation_id},
        )

        # Get routing policy
        policy = await self._get_routing_policy(user_id, policy_id)
        if not policy:
            raise ValueError("No routing policy found")

        # Get candidate agents
        if not candidates:
            candidates = await self._get_user_agents(user_id)
        if not candidates:
            raise ValueError("No agents available for routing")

        # Score candidates based on policy rules
        scores, matched_rules = await self._score_agents(
            candidates=candidates,
            policy_rules=policy.rules,
        )

        if not scores:
            raise ValueError("No agents scored successfully")

        # Select best agent
        selected_agent_id = max(scores, key=scores.get)
        confidence = scores[selected_agent_id] / 100.0

        result = {
            "agent_id": selected_agent_id,
            "reason": f"Selected via policy '{policy.name}'",
            "scores": scores,
            "confidence": confidence,
            "matched_rules": matched_rules,
        }

        # Record execution
        await self._record_routing_execution(
            user_id=user_id,
            policy_id=policy.id,
            selected_agent_id=selected_agent_id,
            request_context=request_context,
            scores=scores,
            matched_rules=matched_rules,
        )

        logger.info(
            "Agent selected | selected_agent_id={selected_agent_id} confidence={confidence}",
            selected_agent_id=selected_agent_id,
            confidence=confidence,
            extra={"correlation_id": self.correlation_id},
        )

        return result

    async def score_agents(
        self,
        agents: List[UUID],
        capability: str,
        criteria: Optional[Dict] = None,
    ) -> Dict[UUID, float]:
        """Score agents based on criteria.

        Args:
            agents: List of agent IDs to score
            capability: Required capability name
            criteria: Scoring criteria (e.g., {"latency_weight": 0.3, "cost_weight": 0.2})

        Returns:
            Dictionary mapping agent_id to score (0-100)
        """
        logger.info(
            "Scoring agents | capability={capability} agent_count={count}",
            capability=capability,
            count=len(agents),
            extra={"correlation_id": self.correlation_id},
        )

        if not criteria:
            criteria = {
                "accuracy_weight": 0.4,
                "latency_weight": 0.3,
                "cost_weight": 0.2,
                "availability_weight": 0.1,
            }

        # Get capabilities for agents
        stmt = select(AgentCapability).where(
            and_(
                AgentCapability.agent_id.in_(agents),
                AgentCapability.capability_name == capability,
                AgentCapability.is_active.is_(True),
            )
        )
        result = await self.db.execute(stmt)
        capabilities = result.scalars().all()

        if not capabilities:
            logger.warning(
                "No capabilities found for agents | capability={capability}",
                capability=capability,
                extra={"correlation_id": self.correlation_id},
            )
            return {}

        # Calculate weighted scores
        scores = {}
        for cap in capabilities:
            score = 0.0

            # Accuracy component
            if cap.accuracy_score is not None:
                score += cap.accuracy_score * criteria.get("accuracy_weight", 0.4)

            # Latency component (inverse - higher is better)
            if cap.latency_score is not None:
                score += cap.latency_score * criteria.get("latency_weight", 0.3)

            # Cost component (inverse - higher is better)
            if cap.cost_score is not None:
                score += cap.cost_score * criteria.get("cost_weight", 0.2)

            # Availability component
            if cap.availability_score is not None:
                score += cap.availability_score * criteria.get("availability_weight", 0.1)

            # Base score if no specific scores available
            if score == 0:
                score = cap.score * (sum(criteria.values()) / 100.0)

            scores[cap.agent_id] = min(score, 100.0)  # Cap at 100

        return scores

    async def create_routing_policy(
        self,
        user_id: UUID,
        name: str,
        rules: Dict,
        description: Optional[str] = None,
        is_default: bool = False,
    ) -> RoutingPolicy:
        """Create new routing policy.

        Args:
            user_id: User ID
            name: Policy name
            rules: Routing rules configuration
            description: Policy description
            is_default: Set as default policy

        Returns:
            Created RoutingPolicy
        """
        logger.info(
            "Creating routing policy | user_id={user_id} name={name}",
            user_id=user_id,
            name=name,
            extra={"correlation_id": self.correlation_id},
        )

        # If setting as default, unset other defaults
        if is_default:
            stmt = select(RoutingPolicy).where(
                and_(
                    RoutingPolicy.user_id == user_id,
                    RoutingPolicy.is_default.is_(True),
                )
            )
            result = await self.db.execute(stmt)
            default_policy = result.scalar()
            if default_policy:
                default_policy.is_default = False

        policy = RoutingPolicy(
            user_id=user_id,
            name=name,
            rules=rules,
            description=description,
            is_default=is_default,
            created_by=user_id,
        )
        self.db.add(policy)
        await self.db.flush()

        logger.info(
            "Routing policy created | policy_id={policy_id}",
            policy_id=policy.id,
            extra={"correlation_id": self.correlation_id},
        )

        return policy

    async def get_routing_policies(self, user_id: UUID) -> List[RoutingPolicy]:
        """Get all routing policies for user.

        Args:
            user_id: User ID

        Returns:
            List of RoutingPolicy objects
        """
        stmt = select(RoutingPolicy).where(
            RoutingPolicy.user_id == user_id
        ).order_by(RoutingPolicy.priority, desc(RoutingPolicy.created_at))

        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_routing_policy(self, policy_id: UUID) -> Optional[RoutingPolicy]:
        """Get specific routing policy.

        Args:
            policy_id: Policy ID

        Returns:
            RoutingPolicy or None if not found
        """
        stmt = select(RoutingPolicy).where(RoutingPolicy.id == policy_id)
        result = await self.db.execute(stmt)
        return result.scalar()

    async def update_routing_policy(
        self,
        policy_id: UUID,
        updates: Dict,
    ) -> RoutingPolicy:
        """Update routing policy.

        Args:
            policy_id: Policy ID
            updates: Fields to update

        Returns:
            Updated RoutingPolicy
        """
        logger.info(
            "Updating routing policy | policy_id={policy_id}",
            policy_id=policy_id,
            extra={"correlation_id": self.correlation_id},
        )

        policy = await self.get_routing_policy(policy_id)
        if not policy:
            raise ValueError(f"Policy {policy_id} not found")

        # Handle is_default update
        if updates.get("is_default") and not policy.is_default:
            stmt = select(RoutingPolicy).where(
                and_(
                    RoutingPolicy.user_id == policy.user_id,
                    RoutingPolicy.is_default.is_(True),
                )
            )
            result = await self.db.execute(stmt)
            default_policy = result.scalar()
            if default_policy:
                default_policy.is_default = False

        # Update fields
        for key, value in updates.items():
            if hasattr(policy, key):
                setattr(policy, key, value)

        policy.updated_at = datetime.utcnow()
        await self.db.flush()

        return policy

    async def delete_routing_policy(self, policy_id: UUID) -> bool:
        """Delete routing policy.

        Args:
            policy_id: Policy ID

        Returns:
            True if deleted, False if not found
        """
        logger.info(
            "Deleting routing policy | policy_id={policy_id}",
            policy_id=policy_id,
            extra={"correlation_id": self.correlation_id},
        )

        policy = await self.get_routing_policy(policy_id)
        if not policy:
            return False

        await self.db.delete(policy)
        return True

    async def get_agent_statistics(
        self,
        agent_id: UUID,
        time_range: int = 7,
    ) -> Dict:
        """Get agent performance statistics.

        Args:
            agent_id: Agent ID
            time_range: Time range in days

        Returns:
            {
                "avg_latency_ms": float,
                "error_rate": float,
                "success_rate": float,
                "total_executions": int,
                "avg_cost": float,
                "success_count": int,
                "error_count": int
            }
        """
        cutoff_date = datetime.utcnow() - timedelta(days=time_range)

        # Get execution statistics
        stmt = select(
            func.avg(AgentMethodExecution.execution_duration_ms).label("avg_latency"),
            func.count(AgentMethodExecution.id).label("total"),
            func.sum(
                (AgentMethodExecution.status == "success").cast(int)
            ).label("success_count"),
            func.sum(
                (AgentMethodExecution.status == "error").cast(int)
            ).label("error_count"),
        ).where(
            and_(
                AgentMethodExecution.agent_id == agent_id,
                AgentMethodExecution.created_at >= cutoff_date,
            )
        )

        result = await self.db.execute(stmt)
        row = result.first()

        if not row or not row.total:
            return {
                "avg_latency_ms": 0,
                "error_rate": 0,
                "success_rate": 0,
                "total_executions": 0,
                "avg_cost": 0,
                "success_count": 0,
                "error_count": 0,
            }

        total = row.total or 1
        success_count = row.success_count or 0
        error_count = row.error_count or 0

        return {
            "avg_latency_ms": float(row.avg_latency or 0),
            "error_rate": (error_count / total) * 100,
            "success_rate": (success_count / total) * 100,
            "total_executions": total,
            "avg_cost": 0.0,  # To be populated from cost tracking
            "success_count": success_count,
            "error_count": error_count,
        }

    async def add_agent_capability(
        self,
        agent_id: UUID,
        user_id: UUID,
        capability_name: str,
        capability_category: str,
        score: float = 50.0,
        tags: Optional[List[str]] = None,
    ) -> AgentCapability:
        """Add capability to agent.

        Args:
            agent_id: Agent ID
            user_id: User ID
            capability_name: Capability name
            capability_category: Capability category
            score: Initial capability score
            tags: Categorization tags

        Returns:
            Created AgentCapability
        """
        capability = AgentCapability(
            agent_id=agent_id,
            user_id=user_id,
            capability_name=capability_name,
            capability_category=capability_category,
            score=score,
            tags=tags or [],
        )
        self.db.add(capability)
        await self.db.flush()

        return capability

    async def perform_ab_test(
        self,
        agent_a_id: UUID,
        agent_b_id: UUID,
        test_split: float = 0.5,
    ) -> UUID:
        """Select agent for A/B test.

        Args:
            agent_a_id: Control agent ID
            agent_b_id: Treatment agent ID
            test_split: Percentage (0-1) for treatment group

        Returns:
            Selected agent ID
        """
        if random.random() < test_split:
            return agent_b_id
        return agent_a_id

    # Private helper methods

    async def _get_routing_policy(
        self,
        user_id: UUID,
        policy_id: Optional[UUID] = None,
    ) -> Optional[RoutingPolicy]:
        """Get routing policy for user."""
        if policy_id:
            stmt = select(RoutingPolicy).where(
                and_(
                    RoutingPolicy.id == policy_id,
                    RoutingPolicy.user_id == user_id,
                )
            )
        else:
            # Get default policy
            stmt = select(RoutingPolicy).where(
                and_(
                    RoutingPolicy.user_id == user_id,
                    RoutingPolicy.is_default.is_(True),
                    RoutingPolicy.enabled.is_(True),
                )
            )

        result = await self.db.execute(stmt)
        return result.scalar()

    async def _get_user_agents(self, user_id: UUID) -> List[UUID]:
        """Get all active agents for user."""
        stmt = select(UserAgent.id).where(
            and_(
                UserAgent.user_id == user_id,
                UserAgent.is_active.is_(True),
            )
        )
        result = await self.db.execute(stmt)
        return [row[0] for row in result.all()]

    async def _score_agents(
        self,
        candidates: List[UUID],
        policy_rules: Dict,
    ) -> Tuple[Dict[UUID, float], List[Dict]]:
        """Score candidates based on policy rules."""
        scores = {}
        matched_rules = []

        rule_type = policy_rules.get("rule_type", "weighted")

        if rule_type == "capability":
            # Score by capability match
            capability = policy_rules.get("capability")
            scores = await self.score_agents(candidates, capability)

        elif rule_type == "cost":
            # Score by lowest cost (highest cost_score)
            for agent_id in candidates:
                stmt = select(AgentCapability).where(
                    and_(
                        AgentCapability.agent_id == agent_id,
                        AgentCapability.is_active.is_(True),
                    )
                )
                result = await self.db.execute(stmt)
                cap = result.scalar()
                if cap and cap.cost_score:
                    scores[agent_id] = cap.cost_score

        elif rule_type == "latency":
            # Score by lowest latency (highest latency_score)
            for agent_id in candidates:
                stmt = select(AgentCapability).where(
                    and_(
                        AgentCapability.agent_id == agent_id,
                        AgentCapability.is_active.is_(True),
                    )
                )
                result = await self.db.execute(stmt)
                cap = result.scalar()
                if cap and cap.latency_score:
                    scores[agent_id] = cap.latency_score

        elif rule_type == "accuracy":
            # Score by highest accuracy
            for agent_id in candidates:
                stmt = select(AgentCapability).where(
                    and_(
                        AgentCapability.agent_id == agent_id,
                        AgentCapability.is_active.is_(True),
                    )
                )
                result = await self.db.execute(stmt)
                cap = result.scalar()
                if cap and cap.accuracy_score:
                    scores[agent_id] = cap.accuracy_score

        elif rule_type == "weighted":
            # Multi-criteria weighted scoring
            criteria = policy_rules.get("criteria", {})
            capability = policy_rules.get("capability", "general")
            scores = await self.score_agents(candidates, capability, criteria)

        elif rule_type == "round_robin":
            # Simple round-robin
            for agent_id in candidates:
                scores[agent_id] = 50.0  # Equal scoring

        # Add default score if agent has no specific score
        for agent_id in candidates:
            if agent_id not in scores:
                scores[agent_id] = 50.0  # Default neutral score

        matched_rules.append({
            "rule_type": rule_type,
            "agents_scored": len(scores),
        })

        return scores, matched_rules

    async def _record_routing_execution(
        self,
        user_id: UUID,
        policy_id: UUID,
        selected_agent_id: UUID,
        request_context: Optional[Dict],
        scores: Dict,
        matched_rules: List[Dict],
    ) -> RoutingExecution:
        """Record routing execution for analytics."""
        execution = RoutingExecution(
            user_id=user_id,
            policy_id=policy_id,
            selected_agent_id=selected_agent_id,
            request_context=request_context,
            candidate_agents=list(scores.keys()),
            scores=scores,
            scoring_criteria=matched_rules,
            status="success",
        )
        self.db.add(execution)
        await self.db.flush()

        return execution