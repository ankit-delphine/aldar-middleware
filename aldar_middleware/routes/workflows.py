"""Workflow orchestration API routes."""

from typing import List, Dict, Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from aldar_middleware.database.base import get_db
from aldar_middleware.models.user import User, UserAgent
from aldar_middleware.models.routing import Workflow
from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.services.workflow_service import WorkflowService

router = APIRouter()


# Request/Response models
class WorkflowCreateRequest(BaseModel):
    """Request to create workflow."""
    name: str
    definition: Dict[str, Any]
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    is_template: bool = False


class WorkflowUpdateRequest(BaseModel):
    """Request to update workflow."""
    name: Optional[str] = None
    definition: Optional[Dict[str, Any]] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    is_active: Optional[bool] = None


class WorkflowExecuteRequest(BaseModel):
    """Request to execute workflow."""
    inputs: Optional[Dict[str, Any]] = None


# Endpoints

@router.post("/workflows")
async def create_workflow(
    request: WorkflowCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Create new workflow.

    Args:
        request: Workflow creation request
        current_user: Current authenticated user
        db: Database session

    Returns:
        Created workflow details

    Raises:
        HTTPException: If workflow creation fails
    """
    try:
        service = WorkflowService(db)
        workflow = await service.create_workflow(
            user_id=current_user.id,
            name=request.name,
            definition=request.definition,
            description=request.description,
            tags=request.tags,
            is_template=request.is_template,
        )
        await db.commit()

        return {
            "id": str(workflow.id),
            "name": workflow.name,
            "description": workflow.description,
            "version": workflow.version,
            "is_active": workflow.is_active,
            "is_template": workflow.is_template,
            "tags": workflow.tags,
            "created_at": workflow.created_at.isoformat(),
            "updated_at": workflow.updated_at.isoformat(),
        }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create workflow: {str(e)}",
        )


@router.get("/workflows")
async def list_workflows(
    is_template: Optional[bool] = None,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> List[Dict[str, Any]]:
    """List workflows for user.

    Args:
        is_template: Filter by template flag
        limit: Result limit
        current_user: Current authenticated user
        db: Database session

    Returns:
        List of workflows
    """
    service = WorkflowService(db)
    workflows = await service.list_workflows(
        user_id=current_user.id,
        is_template=is_template,
        limit=limit,
    )

    return [
        {
            "id": str(w.id),
            "name": w.name,
            "description": w.description,
            "version": w.version,
            "is_active": w.is_active,
            "is_template": w.is_template,
            "tags": w.tags,
            "created_at": w.created_at.isoformat(),
            "updated_at": w.updated_at.isoformat(),
        }
        for w in workflows
    ]


@router.get("/workflows/{workflow_id}")
async def get_workflow(
    workflow_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Get workflow definition.

    Args:
        workflow_id: Workflow ID
        current_user: Current authenticated user
        db: Database session

    Returns:
        Workflow details

    Raises:
        HTTPException: If workflow not found or access denied
    """
    service = WorkflowService(db)
    workflow = await service.get_workflow(workflow_id)

    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    if workflow.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    return {
        "id": str(workflow.id),
        "name": workflow.name,
        "description": workflow.description,
        "version": workflow.version,
        "definition": workflow.definition,
        "is_active": workflow.is_active,
        "is_template": workflow.is_template,
        "tags": workflow.tags,
        "metadata": workflow.metadata,
        "created_at": workflow.created_at.isoformat(),
        "updated_at": workflow.updated_at.isoformat(),
    }


@router.put("/workflows/{workflow_id}")
async def update_workflow(
    workflow_id: UUID,
    request: WorkflowUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Update workflow.

    Args:
        workflow_id: Workflow ID
        request: Update request
        current_user: Current authenticated user
        db: Database session

    Returns:
        Updated workflow details

    Raises:
        HTTPException: If workflow not found or update fails
    """
    try:
        service = WorkflowService(db)
        workflow = await service.get_workflow(workflow_id)

        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow not found",
            )

        if workflow.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied",
            )

        updates = request.dict(exclude_unset=True)
        updated_workflow = await service.update_workflow(workflow_id, updates)
        await db.commit()

        return {
            "id": str(updated_workflow.id),
            "name": updated_workflow.name,
            "description": updated_workflow.description,
            "version": updated_workflow.version,
            "is_active": updated_workflow.is_active,
            "is_template": updated_workflow.is_template,
            "tags": updated_workflow.tags,
            "created_at": updated_workflow.created_at.isoformat(),
            "updated_at": updated_workflow.updated_at.isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to update workflow: {str(e)}",
        )


@router.delete("/workflows/{workflow_id}")
async def delete_workflow(
    workflow_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, str]:
    """Delete workflow.

    Args:
        workflow_id: Workflow ID
        current_user: Current authenticated user
        db: Database session

    Returns:
        Success message

    Raises:
        HTTPException: If workflow not found or deletion fails
    """
    try:
        service = WorkflowService(db)
        workflow = await service.get_workflow(workflow_id)

        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow not found",
            )

        if workflow.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied",
            )

        await service.delete_workflow(workflow_id)
        await db.commit()

        return {"message": "Workflow deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to delete workflow: {str(e)}",
        )


@router.post("/workflows/{workflow_id}/execute")
async def execute_workflow(
    workflow_id: UUID,
    request: WorkflowExecuteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Execute workflow.

    Args:
        workflow_id: Workflow ID
        request: Execution request with inputs
        current_user: Current authenticated user
        db: Database session

    Returns:
        Execution details and results

    Raises:
        HTTPException: If workflow execution fails
    """
    try:
        service = WorkflowService(db)
        workflow = await service.get_workflow(workflow_id)

        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow not found",
            )

        if workflow.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied",
            )

        execution = await service.execute_workflow(
            workflow_id=workflow_id,
            user_id=current_user.id,
            inputs=request.inputs,
        )
        await db.commit()

        return {
            "execution_id": str(execution.id),
            "workflow_id": str(execution.workflow_id),
            "status": execution.status,
            "inputs": execution.inputs,
            "outputs": execution.outputs,
            "total_duration_ms": execution.total_duration_ms,
            "started_at": execution.started_at.isoformat() if execution.started_at else None,
            "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
            "created_at": execution.created_at.isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to execute workflow: {str(e)}",
        )


@router.post("/workflows/{workflow_id}/executions/{execution_id}/cancel")
async def cancel_workflow_execution(
    workflow_id: UUID,
    execution_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Cancel workflow execution.

    Args:
        workflow_id: Workflow ID
        execution_id: Execution ID
        current_user: Current authenticated user
        db: Database session

    Returns:
        Updated execution details

    Raises:
        HTTPException: If execution not found or cancellation fails
    """
    try:
        from aldar_middleware.models.routing import WorkflowExecution

        # Verify workflow ownership
        result = await db.execute(
            select(Workflow).where(
                Workflow.id == workflow_id,
                Workflow.user_id == current_user.id,
            )
        )
        workflow = result.scalar()

        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow not found",
            )

        service = WorkflowService(db)
        execution = await service.cancel_workflow_execution(execution_id)
        await db.commit()

        return {
            "execution_id": str(execution.id),
            "status": execution.status,
            "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to cancel execution: {str(e)}",
        )


@router.get("/workflows/{workflow_id}/executions")
async def list_workflow_executions(
    workflow_id: UUID,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> List[Dict[str, Any]]:
    """List workflow executions.

    Args:
        workflow_id: Workflow ID
        limit: Result limit
        current_user: Current authenticated user
        db: Database session

    Returns:
        List of executions

    Raises:
        HTTPException: If workflow not found
    """
    result = await db.execute(
        select(Workflow).where(
            Workflow.id == workflow_id,
            Workflow.user_id == current_user.id,
        )
    )
    workflow = result.scalar()

    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    service = WorkflowService(db)
    executions = await service.get_execution_history(workflow_id, limit)

    return [
        {
            "execution_id": str(e.id),
            "status": e.status,
            "total_duration_ms": e.total_duration_ms,
            "started_at": e.started_at.isoformat() if e.started_at else None,
            "completed_at": e.completed_at.isoformat() if e.completed_at else None,
            "created_at": e.created_at.isoformat(),
        }
        for e in executions
    ]


@router.get("/workflows/{workflow_id}/executions/{execution_id}")
async def get_workflow_execution_status(
    workflow_id: UUID,
    execution_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Get workflow execution status.

    Args:
        workflow_id: Workflow ID
        execution_id: Execution ID
        current_user: Current authenticated user
        db: Database session

    Returns:
        Execution status details

    Raises:
        HTTPException: If execution not found
    """
    result = await db.execute(
        select(Workflow).where(
            Workflow.id == workflow_id,
            Workflow.user_id == current_user.id,
        )
    )
    workflow = result.scalar()

    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    service = WorkflowService(db)
    status = await service.get_execution_status(execution_id)

    if not status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execution not found",
        )

    return status