"""Automated Remediation System."""

import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from aldar_middleware.database.base import get_db
from aldar_middleware.services.remediation_service import RemediationService
from aldar_middleware.models import RemediationAction, RemediationRule, RemediationExecution

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_remediation_service(
    session: AsyncSession = Depends(get_db),
) -> RemediationService:
    """Get remediation service instance.
    
    Args:
        session: Database session
        
    Returns:
        RemediationService instance
    """
    return RemediationService(session)


@router.get("/actions", tags=["remediation"])
async def get_actions(
    service: RemediationService = Depends(get_remediation_service),
) -> Dict[str, Any]:
    """Get all available remediation actions.
    
    Returns:
        List of remediation actions with details
        
    Example:
        GET /api/remediation/actions
        
        Response:
        {
            "actions": [
                {
                    "id": "uuid",
                    "name": "Scale Agents",
                    "description": "...",
                    "action_type": "scale_agents",
                    "service": "agents",
                    "enabled": true,
                    "configuration": {...},
                    "trigger_alerts": [...],
                    "created_at": "2024-01-01T00:00:00"
                }
            ],
            "total": 5
        }
    """
    try:
        actions = await service.get_all_actions()
        
        return {
            "actions": [
                {
                    "id": action.id,
                    "name": action.name,
                    "description": action.description,
                    "action_type": action.action_type.value,
                    "service": action.service,
                    "enabled": action.enabled,
                    "configuration": action.configuration,
                    "trigger_alerts": action.trigger_alerts,
                    "created_at": action.created_at.isoformat(),
                }
                for action in actions
            ],
            "total": len(actions),
        }
    except Exception as e:
        logger.error(f"Error getting remediation actions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve remediation actions",
        )


@router.get("/rules", tags=["remediation"])
async def get_rules(
    service: RemediationService = Depends(get_remediation_service),
) -> Dict[str, Any]:
    """Get all active remediation rules.
    
    Returns:
        List of remediation rules linking alerts to actions
        
    Example:
        GET /api/remediation/rules
        
        Response:
        {
            "rules": [
                {
                    "id": "uuid",
                    "name": "Scale on Extreme Latency",
                    "alert_type": "extreme_latency",
                    "alert_severity": "critical",
                    "action_id": "uuid",
                    "auto_execute": true,
                    "dry_run_first": true,
                    "enabled": true,
                    "priority": 100
                }
            ],
            "total": 5
        }
    """
    try:
        rules = await service.get_all_rules()
        
        return {
            "rules": [
                {
                    "id": rule.id,
                    "name": rule.name,
                    "description": rule.description,
                    "alert_type": rule.alert_type,
                    "alert_severity": rule.alert_severity,
                    "action_id": rule.action_id,
                    "auto_execute": rule.auto_execute,
                    "dry_run_first": rule.dry_run_first,
                    "requires_approval": rule.requires_approval,
                    "enabled": rule.enabled,
                    "priority": rule.priority,
                    "created_at": rule.created_at.isoformat(),
                }
                for rule in rules
            ],
            "total": len(rules),
        }
    except Exception as e:
        logger.error(f"Error getting remediation rules: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve remediation rules",
        )


@router.get("/executions", tags=["remediation"])
async def get_executions(
    action_id: Optional[str] = None,
    limit: int = 100,
    service: RemediationService = Depends(get_remediation_service),
) -> Dict[str, Any]:
    """Get remediation execution history.
    
    Args:
        action_id: Optional filter by action ID
        limit: Maximum number of results (default 100)
    
    Returns:
        List of remediation executions with details
        
    Example:
        GET /api/remediation/executions?limit=50
        
        Response:
        {
            "executions": [
                {
                    "id": "uuid",
                    "action_id": "uuid",
                    "alert_id": "grafana-alert-123",
                    "status": "success",
                    "started_at": "2024-01-01T00:00:00",
                    "completed_at": "2024-01-01T00:00:05",
                    "success": true,
                    "impact": "Latency reduced by 40%",
                    "rolled_back": false,
                    "metrics_before": {"latency_ms": 5000},
                    "metrics_after": {"latency_ms": 3000}
                }
            ],
            "total": 42
        }
    """
    try:
        executions = await service.get_execution_history(action_id, limit)
        
        return {
            "executions": [
                {
                    "id": exec.id,
                    "action_id": exec.action_id,
                    "alert_id": exec.alert_id,
                    "status": exec.status.value,
                    "started_at": exec.started_at.isoformat() if exec.started_at else None,
                    "completed_at": exec.completed_at.isoformat() if exec.completed_at else None,
                    "success": exec.success,
                    "impact": exec.impact,
                    "rolled_back": exec.rolled_back,
                    "metrics_before": exec.metrics_before,
                    "metrics_after": exec.metrics_after,
                    "created_at": exec.created_at.isoformat(),
                }
                for exec in executions
            ],
            "total": len(executions),
        }
    except Exception as e:
        logger.error(f"Error getting remediation executions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve remediation executions",
        )


@router.get("/statistics", tags=["remediation"])
async def get_statistics(
    service: RemediationService = Depends(get_remediation_service),
) -> Dict[str, Any]:
    """Get remediation statistics and metrics.
    
    Returns:
        Dictionary with success rates, execution counts, etc.
        
    Example:
        GET /api/remediation/statistics
        
        Response:
        {
            "total_executions": 150,
            "successful": 142,
            "failed": 8,
            "success_rate": 94.67,
            "rolled_back": 3,
            "average_execution_time_seconds": 8.5,
            "timestamp": "2024-01-01T00:00:00"
        }
    """
    try:
        stats = await service.get_statistics()
        
        return {
            **stats,
            "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Error getting remediation statistics: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve remediation statistics",
        )


@router.post("/execute", tags=["remediation"])
async def execute_remediation(
    request: Dict[str, Any],
    service: RemediationService = Depends(get_remediation_service),
) -> Dict[str, Any]:
    """Manually trigger a remediation action.
    
    Request body:
    {
        "action_id": "uuid",
        "alert_type": "extreme_latency",
        "alert_id": "manual-trigger-001",
        "alert_severity": "critical",
        "dry_run": false,
        "metadata": {}
    }
    
    Returns:
        Execution details
        
    Example:
        POST /api/remediation/execute
        {
            "action_id": "uuid-scale-agents",
            "alert_type": "manual",
            "alert_id": "manual-001",
            "alert_severity": "critical"
        }
        
        Response:
        {
            "execution_id": "uuid",
            "status": "executing",
            "action_id": "uuid-scale-agents",
            "message": "Remediation action triggered"
        }
    """
    try:
        action_id = request.get("action_id")
        alert_type = request.get("alert_type", "manual")
        alert_id = request.get("alert_id", "manual-trigger")
        alert_severity = request.get("alert_severity", "warning")
        
        if not action_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="action_id is required",
            )
        
        # Verify action exists and is enabled
        action = await service.get_action(action_id)
        if not action or not action.enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Remediation action {action_id} not found or disabled",
            )
        
        # Process the alert (this will trigger remediation if rules match)
        execution_id = await service.process_alert(
            alert_type=alert_type,
            alert_id=alert_id,
            alert_severity=alert_severity,
            alert_metadata=request.get("metadata", {}),
        )
        
        if not execution_id:
            return {
                "execution_id": None,
                "status": "not_triggered",
                "message": "Remediation was not triggered (failed safety checks or no matching rule)",
            }
        
        return {
            "execution_id": execution_id,
            "status": "triggered",
            "action_id": action_id,
            "message": "Remediation action triggered successfully",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error executing remediation: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to execute remediation",
        )


@router.post("/rules/{rule_id}/dry-run", tags=["remediation"])
async def dry_run_rule(
    rule_id: str,
    request: Optional[Dict[str, Any]] = None,
    service: RemediationService = Depends(get_remediation_service),
) -> Dict[str, Any]:
    """Test a remediation rule with dry-run (simulate without executing).
    
    Path parameters:
        rule_id: ID of the rule to test
    
    Request body (optional):
    {
        "test_parameters": {...},
        "metadata": {...}
    }
    
    Returns:
        Simulation results
        
    Example:
        POST /api/remediation/rules/rule-uuid/dry-run
        
        Response:
        {
            "rule_id": "rule-uuid",
            "simulated": true,
            "predicted_outcome": "Action would scale from 2 to 4 replicas",
            "estimated_duration_seconds": 10,
            "predicted_success": true,
            "warnings": []
        }
    """
    try:
        # TODO: Implement dry-run logic for rules
        # For now, return a placeholder response
        return {
            "rule_id": rule_id,
            "simulated": True,
            "predicted_outcome": "Rule simulation completed",
            "estimated_duration_seconds": 10,
            "predicted_success": True,
            "warnings": [],
            "message": "Dry-run simulation successful (placeholder)",
        }
    except Exception as e:
        logger.error(f"Error running dry-run for rule {rule_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to run dry-run simulation",
        )


@router.post("/webhook/alert", tags=["remediation"])
async def receive_alert_webhook(
    payload: Dict[str, Any],
    service: RemediationService = Depends(get_remediation_service),
) -> Dict[str, Any]:
    """Webhook endpoint for receiving alerts.
    
    This endpoint is called by Grafana when alerts fire.
    
    Request body:
    {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "ExtremeLatency",
                    "severity": "critical"
                },
                "annotations": {
                    "summary": "Extreme latency detected"
                },
                "fingerprint": "abc123"
            }
        ]
    }
    
    Returns:
        Processing result
        
    Example Response:
        {
            "received_alerts": 1,
            "processed": 1,
            "remediation_triggered": 1,
            "execution_ids": ["uuid-1"]
        }
    """
    try:
        alerts = payload.get("alerts", [])
        processed = 0
        remediation_triggered = 0
        execution_ids = []
        
        for alert in alerts:
            status = alert.get("status", "unknown")
            labels = alert.get("labels", {})
            alert_name = labels.get("alertname", "unknown")
            severity = labels.get("severity", "warning")
            fingerprint = alert.get("fingerprint", "unknown")
            
            logger.info(
                f"Received alert from webhook: {alert_name} "
                f"(fingerprint: {fingerprint}, severity: {severity})"
            )
            
            # Convert alert name to alert type (e.g., "ExtremeLatency" -> "extreme_latency")
            alert_type = alert_name.lower().replace(" ", "_")
            
            # Process the alert
            execution_id = await service.process_alert(
                alert_type=alert_type,
                alert_id=fingerprint,
                alert_severity=severity,
                alert_metadata=alert,
            )
            
            if execution_id:
                remediation_triggered += 1
                execution_ids.append(execution_id)
            
            processed += 1
        
        logger.info(
            f"Webhook processing complete: {processed} alerts, "
            f"{remediation_triggered} remediations triggered"
        )
        
        return {
            "received_alerts": len(alerts),
            "processed": processed,
            "remediation_triggered": remediation_triggered,
            "execution_ids": execution_ids,
        }
    except Exception as e:
        logger.error(f"Error processing alert webhook: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process alert webhook",
        )