"""Bootstrap script to seed remediation data.

This script creates:
- 5 remediation actions
- 5 remediation rules linking actions to alerts

Run this after running alembic migrations:
    poetry run alembic upgrade head
    poetry run python scripts/seed_phase5_data.py
"""

import asyncio
import logging
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from aldar_middleware.models import (
    RemediationAction,
    RemediationRule,
    ActionType,
)
from aldar_middleware.settings import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def seed_data():
    """Seed data into the database."""
    
    # Create async engine using app settings (asyncpg URL)
    engine = create_async_engine(
        str(settings.db_url_property),
        echo=False,
    )
    
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    
    async with async_session() as session:
        logger.info("🌱 Seeding remediation data...")
        
        # ============================================================================
        # ACTION 1: Scale Agents
        # ============================================================================
        existing = (await session.execute(
            select(RemediationAction).where(RemediationAction.name == "Scale Agent Instances")
        )).scalar_one_or_none()
        if existing:
            scale_agents = existing
            logger.info("ℹ️ Action already exists: Scale Agent Instances")
        else:
            scale_agents = RemediationAction(
                id=str(uuid.uuid4()),
                name="Scale Agent Instances",
                description="Automatically scale agent instances when latency is extreme",
                action_type=ActionType.SCALE_AGENTS,
                service="agents",
                enabled=True,
                configuration={
                    "min_replicas": 1,
                    "max_replicas": 10,
                    "scale_up_increment": 2,
                    "scale_down_decrement": 1,
                    "check_metrics_interval_seconds": 30,
                },
                safety_guardrails={
                    "max_executions_per_hour": 5,
                    "cooldown_minutes": 5,
                    "requires_dry_run": True,
                    "auto_rollback_if_failed": True,
                    "rollback_timeout_seconds": 30,
                    "max_replicas_safety_check": 10,
                },
                trigger_alerts=["extreme_latency", "high_latency"],
            )
            session.add(scale_agents)
            logger.info("✅ Created action: Scale Agent Instances")
        
        # ============================================================================
        # ACTION 2: Enable Circuit Breaker
        # ============================================================================
        existing = (await session.execute(
            select(RemediationAction).where(RemediationAction.name == "Enable Circuit Breaker")
        )).scalar_one_or_none()
        if existing:
            circuit_breaker = existing
            logger.info("ℹ️ Action already exists: Enable Circuit Breaker")
        else:
            circuit_breaker = RemediationAction(
                id=str(uuid.uuid4()),
                name="Enable Circuit Breaker",
                description="Enable circuit breaker to fail fast when error rate is very high",
                action_type=ActionType.ENABLE_CIRCUIT_BREAKER,
                service="api",
                enabled=True,
                configuration={
                    "error_threshold_percent": 50,
                    "request_threshold": 100,
                    "timeout_seconds": 60,
                    "half_open_max_requests": 10,
                },
                safety_guardrails={
                    "max_executions_per_hour": 3,
                    "cooldown_minutes": 10,
                    "requires_dry_run": True,
                    "auto_rollback_if_failed": True,
                    "rollback_timeout_seconds": 30,
                },
                trigger_alerts=["very_high_error_rate", "service_unavailable"],
            )
            session.add(circuit_breaker)
            logger.info("✅ Created action: Enable Circuit Breaker")
        
        # ============================================================================
        # ACTION 3: Reduce Token Usage
        # ============================================================================
        existing = (await session.execute(
            select(RemediationAction).where(RemediationAction.name == "Reduce Token Usage")
        )).scalar_one_or_none()
        if existing:
            reduce_tokens = existing
            logger.info("ℹ️ Action already exists: Reduce Token Usage")
        else:
            reduce_tokens = RemediationAction(
                id=str(uuid.uuid4()),
                name="Reduce Token Usage",
                description="Reduce OpenAI token usage during cost spikes",
                action_type=ActionType.REDUCE_TOKEN_USAGE,
                service="ai",
                enabled=True,
                configuration={
                    "token_reduction_percent": 30,
                    "min_tokens_per_request": 100,
                    "enable_caching": True,
                    "cache_ttl_minutes": 60,
                    "batch_size": 10,
                },
                safety_guardrails={
                    "max_executions_per_hour": 2,
                    "cooldown_minutes": 15,
                    "requires_dry_run": True,
                    "auto_rollback_if_failed": True,
                    "rollback_timeout_seconds": 60,
                    "budget_check": True,
                    "max_monthly_reduction_percent": 50,
                },
                trigger_alerts=["extremely_high_cost", "cost_anomaly"],
            )
            session.add(reduce_tokens)
            logger.info("✅ Created action: Reduce Token Usage")
        
        # ============================================================================
        # ACTION 4: Reconnect MCP
        # ============================================================================
        existing = (await session.execute(
            select(RemediationAction).where(RemediationAction.name == "Reconnect MCP Client")
        )).scalar_one_or_none()
        if existing:
            reconnect_mcp = existing
            logger.info("ℹ️ Action already exists: Reconnect MCP Client")
        else:
            reconnect_mcp = RemediationAction(
                id=str(uuid.uuid4()),
                name="Reconnect MCP Client",
                description="Automatically reconnect MCP client on connection failures",
                action_type=ActionType.RECONNECT_MCP,
                service="mcp",
                enabled=True,
                configuration={
                    "max_retries": 3,
                    "initial_backoff_seconds": 1,
                    "max_backoff_seconds": 30,
                    "backoff_multiplier": 2,
                    "reconnect_timeout_seconds": 10,
                },
                safety_guardrails={
                    "max_executions_per_hour": 10,
                    "cooldown_minutes": 1,
                    "requires_dry_run": False,
                    "auto_rollback_if_failed": True,
                    "rollback_timeout_seconds": 10,
                },
                trigger_alerts=["mcp_connection_failures", "mcp_timeout"],
            )
            session.add(reconnect_mcp)
            logger.info("✅ Created action: Reconnect MCP Client")
        
        # ============================================================================
        # ACTION 5: Optimize Database Queries
        # ============================================================================
        existing = (await session.execute(
            select(RemediationAction).where(RemediationAction.name == "Optimize Database Queries")
        )).scalar_one_or_none()
        if existing:
            optimize_db = existing
            logger.info("ℹ️ Action already exists: Optimize Database Queries")
        else:
            optimize_db = RemediationAction(
                id=str(uuid.uuid4()),
                name="Optimize Database Queries",
                description="Create missing indexes and optimize slow queries",
                action_type=ActionType.OPTIMIZE_DATABASE_QUERIES,
                service="database",
                enabled=True,
                configuration={
                    "analyze_slow_queries": True,
                    "slow_query_threshold_ms": 1000,
                    "auto_create_indexes": True,
                    "max_indexes_per_run": 3,
                    "vacuum_analyze": True,
                },
                safety_guardrails={
                    "max_executions_per_hour": 2,
                    "cooldown_minutes": 30,
                    "requires_dry_run": True,
                    "auto_rollback_if_failed": True,
                    "rollback_timeout_seconds": 60,
                    "backup_before_changes": True,
                },
                trigger_alerts=["extreme_database_latency", "slow_queries"],
            )
            session.add(optimize_db)
            logger.info("✅ Created action: Optimize Database Queries")
        
        # Commit actions first so we can reference them
        await session.commit()
        
        # ============================================================================
        # RULE 1: Scale on Extreme Latency
        # ============================================================================
        existing_rule = (await session.execute(
            select(RemediationRule).where(RemediationRule.name == "Scale Agents on Extreme Latency")
        )).scalar_one_or_none()
        if existing_rule:
            rule1 = existing_rule
            logger.info("ℹ️ Rule already exists: Scale Agents on Extreme Latency")
        else:
            rule1 = RemediationRule(
                id=str(uuid.uuid4()),
                name="Scale Agents on Extreme Latency",
                description="When agent latency is extreme, scale up instances",
                action_id=scale_agents.id,
                alert_type="extreme_latency",
                alert_severity="critical",
                enabled=True,
                dry_run_first=True,
                auto_execute=True,
                requires_approval=False,
                condition_config={
                    "min_latency_ms": 5000,
                    "max_latency_ms": 30000,
                    "scale_increment": 2,
                    "max_target_replicas": 8,
                },
                priority=100,
            )
            session.add(rule1)
            logger.info("✅ Created rule: Scale Agents on Extreme Latency")
        
        # ============================================================================
        # RULE 2: Circuit Breaker on Very High Errors
        # ============================================================================
        existing_rule = (await session.execute(
            select(RemediationRule).where(RemediationRule.name == "Enable Circuit Breaker on High Errors")
        )).scalar_one_or_none()
        if existing_rule:
            rule2 = existing_rule
            logger.info("ℹ️ Rule already exists: Enable Circuit Breaker on High Errors")
        else:
            rule2 = RemediationRule(
                id=str(uuid.uuid4()),
                name="Enable Circuit Breaker on High Errors",
                description="When error rate is very high, enable circuit breaker",
                action_id=circuit_breaker.id,
                alert_type="very_high_error_rate",
                alert_severity="critical",
                enabled=True,
                dry_run_first=True,
                auto_execute=True,
                requires_approval=False,
                condition_config={
                    "error_rate_threshold": 0.5,
                    "min_requests_for_trigger": 100,
                    "circuit_breaker_timeout": 60,
                },
                priority=90,
            )
            session.add(rule2)
            logger.info("✅ Created rule: Enable Circuit Breaker on High Errors")
        
        # ============================================================================
        # RULE 3: Reduce Costs on Spike
        # ============================================================================
        existing_rule = (await session.execute(
            select(RemediationRule).where(RemediationRule.name == "Reduce Costs on Spike")
        )).scalar_one_or_none()
        if existing_rule:
            rule3 = existing_rule
            logger.info("ℹ️ Rule already exists: Reduce Costs on Spike")
        else:
            rule3 = RemediationRule(
                id=str(uuid.uuid4()),
                name="Reduce Costs on Spike",
                description="When costs spike extremely, reduce token usage",
                action_id=reduce_tokens.id,
                alert_type="extremely_high_cost",
                alert_severity="critical",
                enabled=True,
                dry_run_first=True,
                auto_execute=True,
                requires_approval=False,
                condition_config={
                    "cost_spike_percent": 150,
                    "token_reduction": 30,
                    "preserve_quality": True,
                },
                priority=110,
            )
            session.add(rule3)
            logger.info("✅ Created rule: Reduce Costs on Spike")
        
        # ============================================================================
        # RULE 4: Reconnect MCP on Failures
        # ============================================================================
        existing_rule = (await session.execute(
            select(RemediationRule).where(RemediationRule.name == "Reconnect MCP Client")
        )).scalar_one_or_none()
        if existing_rule:
            rule4 = existing_rule
            logger.info("ℹ️ Rule already exists: Reconnect MCP Client")
        else:
            rule4 = RemediationRule(
                id=str(uuid.uuid4()),
                name="Reconnect MCP Client",
                description="When MCP connection fails, automatically reconnect",
                action_id=reconnect_mcp.id,
                alert_type="mcp_connection_failures",
                alert_severity="warning",
                enabled=True,
                dry_run_first=False,
                auto_execute=True,
                requires_approval=False,
                condition_config={
                    "consecutive_failures": 3,
                    "retry_attempts": 3,
                    "backoff_strategy": "exponential",
                },
                priority=120,
            )
            session.add(rule4)
            logger.info("✅ Created rule: Reconnect MCP Client")
        
        # ============================================================================
        # RULE 5: Optimize Database Queries
        # ============================================================================
        existing_rule = (await session.execute(
            select(RemediationRule).where(RemediationRule.name == "Optimize Database on Extreme Latency")
        )).scalar_one_or_none()
        if existing_rule:
            rule5 = existing_rule
            logger.info("ℹ️ Rule already exists: Optimize Database on Extreme Latency")
        else:
            rule5 = RemediationRule(
                id=str(uuid.uuid4()),
                name="Optimize Database on Extreme Latency",
                description="When database latency is extreme, optimize queries",
                action_id=optimize_db.id,
                alert_type="extreme_database_latency",
                alert_severity="critical",
                enabled=True,
                dry_run_first=True,
                auto_execute=False,  # Requires approval for database changes
                requires_approval=True,
                condition_config={
                    "latency_threshold_ms": 2000,
                    "analyze_queries": True,
                    "create_indexes": True,
                },
                priority=80,
            )
            session.add(rule5)
            logger.info("✅ Created rule: Optimize Database on Extreme Latency")
        
        # Commit rules
        await session.commit()
        
        logger.info("✅data seeded successfully!")
        logger.info("📊 Created:")
        logger.info("   - 5 remediation actions")
        logger.info("   - 5 remediation rules")
        logger.info("")
        logger.info("Next steps:")
        logger.info("  1. Verify data: poetry run python scripts/verify_phase5_data.py")
        logger.info("  2. Test API: http://localhost:8000/api/remediation/actions")
        logger.info("  3. Continue Week 1: Implement action executors")
    
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed_data())