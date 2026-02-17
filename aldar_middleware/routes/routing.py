"""Intelligent routing API routes."""

from typing import List, Dict, Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from aldar_middleware.database.base import get_db
from aldar_middleware.models.user import User
from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.services.routing_service import RoutingService

router = APIRouter()


# Request/Response models
class RoutingRequest(BaseModel):
    """Request to route to best agent."""
    policy_id: Optional[UUID] = None
    request_context: Optional[Dict[str, Any]] = None
    candidates: Optional[List[UUID]] = None


class RoutingPolicyCreateRequest(BaseModel):
    """Request to create routing policy."""
    name: str
    rules: Dict[str, Any]
    description: Optional[str] = None
    is_default: bool = False
    priority: int = 0


class RoutingPolicyUpdateRequest(BaseModel):
    """Request to update routing policy."""
    name: Optional[str] = None
    description: Optional[str] = None
    rules: Optional[Dict[str, Any]] = None
    is_default: Optional[bool] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None


# Endpoints

@router.post("/routing/route")
async def route_request(
    request: RoutingRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Route request to best agent based on policy.

    Args:
        request: Routing request with policy and context
        current_user: Current authenticated user
        db: Database session

    Returns:
        Routing decision with selected agent and scores

    Raises:
        HTTPException: If routing fails
    """
    try:
        service = RoutingService(db)
        result = await service.select_agent(
            user_id=current_user.id,
            policy_id=request.policy_id,
            request_context=request.request_context,
            candidates=request.candidates,
        )

        return {
            "agent_id": str(result["agent_id"]),
            "reason": result["reason"],
            "scores": {
                str(agent_id): score for agent_id, score in result["scores"].items()
            },
            "confidence": result["confidence"],
            "matched_rules": result["matched_rules"],
        }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Routing failed: {str(e)}",
        )


@router.post("/routing/policies")
async def create_routing_policy(
    request: RoutingPolicyCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Create new routing policy.

    Args:
        request: Policy creation request
        current_user: Current authenticated user
        db: Database session

    Returns:
        Created policy details

    Raises:
        HTTPException: If policy creation fails
    """
    try:
        service = RoutingService(db)
        policy = await service.create_routing_policy(
            user_id=current_user.id,
            name=request.name,
            rules=request.rules,
            description=request.description,
            is_default=request.is_default,
        )
        await db.commit()

        return {
            "id": str(policy.id),
            "name": policy.name,
            "description": policy.description,
            "rules": policy.rules,
            "is_default": policy.is_default,
            "priority": policy.priority,
            "enabled": policy.enabled,
            "created_at": policy.created_at.isoformat(),
        }

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create policy: {str(e)}",
        )


@router.get("/routing/policies")
async def list_routing_policies(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> List[Dict[str, Any]]:
    """List routing policies for user.

    Args:
        current_user: Current authenticated user
        db: Database session

    Returns:
        List of routing policies
    """
    service = RoutingService(db)
    policies = await service.get_routing_policies(current_user.id)

    return [
        {
            "id": str(policy.id),
            "name": policy.name,
            "description": policy.description,
            "rules": policy.rules,
            "is_default": policy.is_default,
            "priority": policy.priority,
            "enabled": policy.enabled,
            "created_at": policy.created_at.isoformat(),
            "updated_at": policy.updated_at.isoformat(),
        }
        for policy in policies
    ]


@router.get("/routing/policies/{policy_id}")
async def get_routing_policy(
    policy_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Get specific routing policy.

    Args:
        policy_id: Policy ID
        current_user: Current authenticated user
        db: Database session

    Returns:
        Policy details

    Raises:
        HTTPException: If policy not found or access denied
    """
    service = RoutingService(db)
    policy = await service.get_routing_policy(policy_id)

    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Policy not found",
        )

    if policy.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    return {
        "id": str(policy.id),
        "name": policy.name,
        "description": policy.description,
        "rules": policy.rules,
        "is_default": policy.is_default,
        "priority": policy.priority,
        "enabled": policy.enabled,
        "created_at": policy.created_at.isoformat(),
        "updated_at": policy.updated_at.isoformat(),
    }


@router.put("/routing/policies/{policy_id}")
async def update_routing_policy(
    policy_id: UUID,
    request: RoutingPolicyUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Update routing policy.

    Args:
        policy_id: Policy ID
        request: Update request
        current_user: Current authenticated user
        db: Database session

    Returns:
        Updated policy details

    Raises:
        HTTPException: If policy not found or update fails
    """
    try:
        service = RoutingService(db)
        policy = await service.get_routing_policy(policy_id)

        if not policy:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Policy not found",
            )

        if policy.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied",
            )

        updates = request.dict(exclude_unset=True)
        updated_policy = await service.update_routing_policy(policy_id, updates)
        await db.commit()

        return {
            "id": str(updated_policy.id),
            "name": updated_policy.name,
            "description": updated_policy.description,
            "rules": updated_policy.rules,
            "is_default": updated_policy.is_default,
            "priority": updated_policy.priority,
            "enabled": updated_policy.enabled,
            "created_at": updated_policy.created_at.isoformat(),
            "updated_at": updated_policy.updated_at.isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to update policy: {str(e)}",
        )


@router.delete("/routing/policies/{policy_id}")
async def delete_routing_policy(
    policy_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, str]:
    """Delete routing policy.

    Args:
        policy_id: Policy ID
        current_user: Current authenticated user
        db: Database session

    Returns:
        Success message

    Raises:
        HTTPException: If policy not found or deletion fails
    """
    try:
        service = RoutingService(db)
        policy = await service.get_routing_policy(policy_id)

        if not policy:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Policy not found",
            )

        if policy.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied",
            )

        await service.delete_routing_policy(policy_id)
        await db.commit()

        return {"message": "Policy deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to delete policy: {str(e)}",
        )


@router.get("/agents/{agent_id}/stats")
async def get_agent_statistics(
    agent_id: UUID,
    time_range: int = 7,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Get agent performance statistics.

    Args:
        agent_id: Agent ID
        time_range: Time range in days
        current_user: Current authenticated user
        db: Database session

    Returns:
        Agent statistics

    Raises:
        HTTPException: If agent not found
    """
    from sqlalchemy import select
    from aldar_middleware.models.user import UserAgent

    # Verify agent ownership
    result = await db.execute(
        select(UserAgent).where(
            UserAgent.id == agent_id,
            UserAgent.user_id == current_user.id,
        )
    )
    agent = result.scalar()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )

    service = RoutingService(db)
    stats = await service.get_agent_statistics(agent_id, time_range)

    return stats