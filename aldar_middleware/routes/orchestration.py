"""Orchestration API routes using AGNO Multiagent API."""

from typing import Dict, Any, Optional, List, Union
from uuid import uuid4
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Path, Body, Form, File, UploadFile, Request
from starlette.requests import Request as StarletteRequest
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field
from typing import Optional as TypingOptional

from aldar_middleware.settings.context import get_user_id, get_correlation_id
from aldar_middleware.orchestration.agno import agno_service
from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.database.base import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from aldar_middleware.models.menu import Agent
from sqlalchemy import select, update

# Pydantic models for request/response
class AgentRunRequest(BaseModel):
    """Request model for creating agent runs."""
    input_data: Dict[str, Any] = Field(..., description="Input data for the agent run")
    parameters: Optional[Dict[str, Any]] = Field(None, description="Additional parameters")
    timeout: Optional[int] = Field(None, description="Request timeout in seconds")


class TeamRunRequest(BaseModel):
    """Request model for creating team runs."""
    input_data: Dict[str, Any] = Field(..., description="Input data for the team run")
    parameters: Optional[Dict[str, Any]] = Field(None, description="Additional parameters")
    timeout: Optional[int] = Field(None, description="Request timeout in seconds")


class WorkflowRunRequest(BaseModel):
    """Request model for executing workflows."""
    input_data: Dict[str, Any] = Field(..., description="Input data for the workflow")
    parameters: Optional[Dict[str, Any]] = Field(None, description="Additional parameters")
    timeout: Optional[int] = Field(None, description="Request timeout in seconds")


class SessionRenameRequest(BaseModel):
    """Request model for renaming sessions."""
    name: str = Field(..., description="New session name")


class MemoryCreateRequest(BaseModel):
    """Request model for creating memories."""
    content: str = Field(..., description="Memory content")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Memory metadata")


class MemoryUpdateRequest(BaseModel):
    """Request model for updating memories."""
    content: Optional[str] = Field(None, description="Updated memory content")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Updated memory metadata")


class EvaluationRequest(BaseModel):
    """Request model for executing evaluations."""
    config: Dict[str, Any] = Field(..., description="Evaluation configuration")
    inputs: Optional[Dict[str, Any]] = Field(None, description="Evaluation inputs")


class EvaluationUpdateRequest(BaseModel):
    """Request model for updating evaluations."""
    status: Optional[str] = Field(None, description="Evaluation status")
    results: Optional[Dict[str, Any]] = Field(None, description="Evaluation results")


class ContentUpdateRequest(BaseModel):
    """Request model for updating content."""
    metadata: Optional[Dict[str, Any]] = Field(None, description="Content metadata")
    status: Optional[str] = Field(None, description="Content status")


class QueryAgentRequest(BaseModel):
    """Request model for query-agent endpoint."""
    agent_name: str = Field(..., description="Agent name")
    query: str = Field(..., description="Query to send to the agent")
    stream_id: Optional[str] = Field(None, description="Stream ID for tracking")
    session_id: Optional[str] = Field(None, description="Session ID")
    stream_config: Optional[Dict[str, Any]] = Field(None, description="Stream configuration")


class AGNOAPIResponse(BaseModel):
    """Standard response model for AGNO API calls."""
    success: bool = Field(..., description="Whether the request was successful")
    data: Optional[Union[Dict[str, Any], List[Any]]] = Field(None, description="Response data (can be dict or list)")
    error: Optional[str] = Field(None, description="Error message if any")
    cached: bool = Field(False, description="Whether the response was served from cache")
    correlation_id: str = Field(..., description="Correlation ID for tracking")


# Create router
router = APIRouter(prefix="/orchestrat", tags=["Orchestration"])

# Query-agent endpoint (no prefix, direct route)
query_agent_router = APIRouter(tags=["Orchestration"])


async def _update_agent_last_used(agent_id: int, db: AsyncSession) -> None:
    """Update the last_used timestamp for an agent."""
    try:
        result = await db.execute(
            update(Agent)
            .where(Agent.id == agent_id)
            .values(last_used=datetime.utcnow())
        )
        await db.flush()  # Flush to update without committing (commit happens in caller)
        rows_affected = result.rowcount
        if rows_affected > 0:
            logger.debug(f"Updated last_used for agent {agent_id}")
        else:
            logger.warning(f"No agent found with id {agent_id} to update last_used")
    except Exception as e:
        # Log error but don't fail the request if last_used update fails
        logger.warning(f"Failed to update last_used for agent {agent_id}: {str(e)}")


# Core endpoints
@router.get("/config", response_model=AGNOAPIResponse)
async def get_os_config(
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get OS configuration from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_config(user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting OS config: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get OS config: {str(e)}")


@router.get("/models", response_model=AGNOAPIResponse)
async def get_available_models(
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get available models from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_models(user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting models: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get models: {str(e)}")


# Agent endpoints
@router.post("/agents/{agent_id}/runs", response_model=AGNOAPIResponse)
async def create_agent_run(
    agent_id: str = Path(..., description="Agent ID"),
    message: str = Form(..., description="Message for the agent"),
    stream: bool = Form(True, description="Enable streaming"),
    session_id: TypingOptional[str] = Form(None),
    user_id_param: TypingOptional[str] = Form(None),
    files: TypingOptional[List[UploadFile]] = File(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create agent run in AGNO API (supports multipart/form-data)."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        # Resolve agent_id to agent database ID and update last_used
        # agent_id parameter could be either legacy agent_id string or public_id UUID
        from uuid import UUID
        try:
            agent_uuid = UUID(agent_id)
            result = await db.execute(
                select(Agent).where(
                    (Agent.public_id == agent_uuid) | (Agent.agent_id == agent_id)
                )
            )
        except ValueError:
            # Not a valid UUID, try legacy agent_id only
            result = await db.execute(
                select(Agent).where(Agent.agent_id == agent_id)
            )
        
        agent_record = result.scalar_one_or_none()
        if agent_record:
            await _update_agent_last_used(agent_record.id, db)
            await db.commit()  # Commit the last_used update
        
        # Use provided session_id or generate new one
        session_id_value = session_id or str(uuid4())
        user_id_value = user_id_param or user_id
        
        # Prepare data for multipart request
        data = {
            "message": message,
            "stream": str(stream).lower(),  # Convert boolean to string for AGNO API
            "session_id": session_id_value,
            "user_id": user_id_value
        }
        
        # Handle file uploads if any
        files_data = None
        if files:
            files_list = []
            for file_item in files:
                if file_item.filename:
                    file_content = await file_item.read()
                    files_list.append(("files", (file_item.filename, file_content, file_item.content_type or "application/octet-stream")))
            files_data = files_list if files_list else None
        
        response_data = await agno_service.create_agent_run(
            agent_id=agent_id,
            data=data,
            user_id=user_id,
            use_multipart=True,
            files=files_data
        )
        
        return AGNOAPIResponse(
            success=True,
            data=response_data,
            cached=False,  # POST requests are never cached
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error creating agent run: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to create agent run: {str(e)}")


@router.post("/agents/{agent_id}/runs/{run_id}/cancel", response_model=AGNOAPIResponse)
async def cancel_agent_run(
    agent_id: str = Path(..., description="Agent ID"),
    run_id: str = Path(..., description="Run ID"),
    current_user: dict = Depends(get_current_user)
):
    """Cancel agent run in AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.cancel_agent_run(
            agent_id=agent_id,
            run_id=run_id,
            user_id=user_id
        )
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error canceling agent run: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to cancel agent run: {str(e)}")


@router.post("/agents/{agent_id}/runs/{run_id}/continue", response_model=AGNOAPIResponse)
async def continue_agent_run(
    agent_id: str = Path(..., description="Agent ID"),
    run_id: str = Path(..., description="Run ID"),
    tools: str = Form(..., description="Tools parameter (e.g., '1')"),
    stream: bool = Form(True, description="Enable streaming"),
    session_id: TypingOptional[str] = Form(None, description="Session ID"),
    user_id: TypingOptional[str] = Form(None, description="User ID"),
    current_user: dict = Depends(get_current_user)
):
    """Continue agent run in AGNO API (application/x-www-form-urlencoded format)."""
    correlation_id = get_correlation_id()
    current_user_id = get_user_id()
    
    try:
        # Prepare data for form-encoded request (as per AGNO external API)
        data = {
            "tools": tools,
            "stream": str(stream).lower(),  # Convert boolean to string for AGNO API
            "session_id": session_id or str(uuid4()),
            "user_id": user_id or current_user_id
        }
        
        response_data = await agno_service.continue_agent_run(
            agent_id=agent_id,
            run_id=run_id,
            data=data,
            user_id=current_user_id,
            use_multipart=False  # Use form-encoded, not multipart
        )
        
        return AGNOAPIResponse(
            success=True,
            data=response_data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error continuing agent run: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to continue agent run: {str(e)}")


# Additional AGNO endpoints for comprehensive integration

# Agent management
@router.get("/agents", response_model=AGNOAPIResponse)
async def get_all_agents(
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get all agents from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_agents(user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting agents: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get agents: {str(e)}")


@router.get("/agents/{agent_id}", response_model=AGNOAPIResponse)
async def get_agent_details(
    agent_id: str = Path(..., description="Agent ID"),
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get agent details from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_agent_details(agent_id, user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting agent details: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get agent details: {str(e)}")


# Team endpoints
@router.get("/teams", response_model=AGNOAPIResponse)
async def get_all_teams(
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get all teams from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_teams(user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting teams: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get teams: {str(e)}")


@router.get("/teams/{team_id}", response_model=AGNOAPIResponse)
async def get_team_details(
    team_id: str = Path(..., description="Team ID"),
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get team details from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_team_details(team_id, user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting team details: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get team details: {str(e)}")


@router.post("/teams/{team_id}/runs", response_model=AGNOAPIResponse)
async def create_team_run(
    team_id: str = Path(..., description="Team ID"),
    request_data: TeamRunRequest = Body(..., description="Team run request data"),
    current_user: dict = Depends(get_current_user)
):
    """Create team run in AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.create_team_run(
            team_id=team_id,
            data=request_data.dict(),
            user_id=user_id
        )
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error creating team run: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to create team run: {str(e)}")


@router.post("/teams/{team_id}/runs/{run_id}/cancel", response_model=AGNOAPIResponse)
async def cancel_team_run(
    team_id: str = Path(..., description="Team ID"),
    run_id: str = Path(..., description="Run ID"),
    current_user: dict = Depends(get_current_user)
):
    """Cancel team run in AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.cancel_team_run(
            team_id=team_id,
            run_id=run_id,
            user_id=user_id
        )
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error canceling team run: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to cancel team run: {str(e)}")


# Workflow endpoints
@router.get("/workflows", response_model=AGNOAPIResponse)
async def get_all_workflows(
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get all workflows from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_workflows(user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting workflows: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get workflows: {str(e)}")


@router.get("/workflows/{workflow_id}", response_model=AGNOAPIResponse)
async def get_workflow_details(
    workflow_id: str = Path(..., description="Workflow ID"),
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get workflow details from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_workflow_details(workflow_id, user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting workflow details: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get workflow details: {str(e)}")


@router.post("/workflows/{workflow_id}/runs", response_model=AGNOAPIResponse)
async def execute_workflow(
    workflow_id: str = Path(..., description="Workflow ID"),
    request_data: WorkflowRunRequest = Body(..., description="Workflow run request data"),
    current_user: dict = Depends(get_current_user)
):
    """Execute workflow in AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.execute_workflow(
            workflow_id=workflow_id,
            data=request_data.dict(),
            user_id=user_id
        )
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error executing workflow: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to execute workflow: {str(e)}")


@router.post("/workflows/{workflow_id}/runs/{run_id}/cancel", response_model=AGNOAPIResponse)
async def cancel_workflow_run(
    workflow_id: str = Path(..., description="Workflow ID"),
    run_id: str = Path(..., description="Run ID"),
    current_user: dict = Depends(get_current_user)
):
    """Cancel workflow run in AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.cancel_workflow_run(
            workflow_id=workflow_id,
            run_id=run_id,
            user_id=user_id
        )
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error canceling workflow run: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to cancel workflow run: {str(e)}")


# Health endpoint
@router.get("/health", response_model=AGNOAPIResponse)
async def get_health_status(
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get health status from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_health(user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting health status: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get health status: {str(e)}")


# Additional comprehensive endpoints for all AGNO functionality

# Session management
@router.get("/sessions", response_model=AGNOAPIResponse)
async def get_sessions(
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """List sessions from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_sessions(user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting sessions: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get sessions: {str(e)}")


@router.delete("/sessions", response_model=AGNOAPIResponse)
async def delete_sessions(
    current_user: dict = Depends(get_current_user)
):
    """Delete multiple sessions from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.delete_sessions(user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error deleting sessions: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to delete sessions: {str(e)}")


@router.get("/sessions/{session_id}", response_model=AGNOAPIResponse)
async def get_session_by_id(
    session_id: str = Path(..., description="Session ID"),
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get session by ID from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_session_by_id(session_id, user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting session: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get session: {str(e)}")


@router.delete("/sessions/{session_id}", response_model=AGNOAPIResponse)
async def delete_session(
    session_id: str = Path(..., description="Session ID"),
    current_user: dict = Depends(get_current_user)
):
    """Delete session from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.delete_session(session_id, user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error deleting session: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to delete session: {str(e)}")


@router.get("/sessions/{session_id}/runs", response_model=AGNOAPIResponse)
async def get_session_runs(
    session_id: str = Path(..., description="Session ID"),
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get session runs from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_session_runs(session_id, user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting session runs: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get session runs: {str(e)}")


@router.post("/sessions/{session_id}/rename", response_model=AGNOAPIResponse)
async def rename_session(
    session_id: str = Path(..., description="Session ID"),
    request_data: SessionRenameRequest = Body(..., description="Session rename request data"),
    current_user: dict = Depends(get_current_user)
):
    """Rename session in AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.rename_session(
            session_id=session_id,
            data=request_data.dict(),
            user_id=user_id
        )
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error renaming session: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to rename session: {str(e)}")


# Memory management
@router.post("/memories", response_model=AGNOAPIResponse)
async def create_memory(
    request_data: MemoryCreateRequest = Body(..., description="Memory creation request data"),
    current_user: dict = Depends(get_current_user)
):
    """Create memory in AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.create_memory(
            data=request_data.dict(),
            user_id=user_id
        )
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error creating memory: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to create memory: {str(e)}")


@router.get("/memories", response_model=AGNOAPIResponse)
async def get_memories(
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """List memories from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_memories(user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting memories: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get memories: {str(e)}")


@router.get("/memories/{memory_id}", response_model=AGNOAPIResponse)
async def get_memory_by_id(
    memory_id: str = Path(..., description="Memory ID"),
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get memory by ID from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_memory_by_id(memory_id, user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting memory: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get memory: {str(e)}")


@router.patch("/memories/{memory_id}", response_model=AGNOAPIResponse)
async def update_memory(
    memory_id: str = Path(..., description="Memory ID"),
    request_data: MemoryUpdateRequest = Body(..., description="Memory update request data"),
    current_user: dict = Depends(get_current_user)
):
    """Update memory in AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.update_memory(
            memory_id=memory_id,
            data=request_data.dict(exclude_unset=True),
            user_id=user_id
        )
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error updating memory: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to update memory: {str(e)}")


@router.delete("/memories/{memory_id}", response_model=AGNOAPIResponse)
async def delete_memory(
    memory_id: str = Path(..., description="Memory ID"),
    current_user: dict = Depends(get_current_user)
):
    """Delete memory from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.delete_memory(memory_id, user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error deleting memory: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to delete memory: {str(e)}")


@router.get("/memory_topics", response_model=AGNOAPIResponse)
async def get_memory_topics(
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get memory topics from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_memory_topics(user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting memory topics: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get memory topics: {str(e)}")


@router.get("/user_memory_stats", response_model=AGNOAPIResponse)
async def get_user_memory_stats(
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get user memory statistics from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_user_memory_stats(user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting user memory stats: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get user memory stats: {str(e)}")


# Evaluation endpoints
@router.get("/eval-runs", response_model=AGNOAPIResponse)
async def get_eval_runs(
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """List evaluation runs from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_eval_runs(user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting eval runs: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get eval runs: {str(e)}")


@router.post("/eval-runs", response_model=AGNOAPIResponse)
async def execute_evaluation(
    request_data: EvaluationRequest = Body(..., description="Evaluation request data"),
    current_user: dict = Depends(get_current_user)
):
    """Execute evaluation in AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.execute_evaluation(
            data=request_data.dict(),
            user_id=user_id
        )
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error executing evaluation: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to execute evaluation: {str(e)}")


@router.get("/eval-runs/{eval_run_id}", response_model=AGNOAPIResponse)
async def get_eval_run(
    eval_run_id: str = Path(..., description="Evaluation Run ID"),
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get evaluation run from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_eval_run(eval_run_id, user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting eval run: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get eval run: {str(e)}")


@router.patch("/eval-runs/{eval_run_id}", response_model=AGNOAPIResponse)
async def update_eval_run(
    eval_run_id: str = Path(..., description="Evaluation Run ID"),
    request_data: EvaluationUpdateRequest = Body(..., description="Evaluation update request data"),
    current_user: dict = Depends(get_current_user)
):
    """Update evaluation run in AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.update_eval_run(
            eval_run_id=eval_run_id,
            data=request_data.dict(exclude_unset=True),
            user_id=user_id
        )
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error updating eval run: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to update eval run: {str(e)}")


# Metrics endpoints
@router.get("/metrics", response_model=AGNOAPIResponse)
async def get_metrics(
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get AgentOS metrics from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_metrics(user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting metrics: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get metrics: {str(e)}")


@router.post("/metrics/refresh", response_model=AGNOAPIResponse)
async def refresh_metrics(
    current_user: dict = Depends(get_current_user)
):
    """Refresh metrics in AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.refresh_metrics(user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error refreshing metrics: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to refresh metrics: {str(e)}")


# Knowledge endpoints
@router.post("/knowledge/content", response_model=AGNOAPIResponse)
async def upload_content(
    request_data: Dict[str, Any] = Body(..., description="Content upload request data"),
    current_user: dict = Depends(get_current_user)
):
    """Upload content to AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.upload_content(
            data=request_data,
            user_id=user_id
        )
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error uploading content: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to upload content: {str(e)}")


@router.get("/knowledge/content", response_model=AGNOAPIResponse)
async def list_content(
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """List content from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.list_content(user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error listing content: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to list content: {str(e)}")


@router.get("/knowledge/content/{content_id}", response_model=AGNOAPIResponse)
async def get_content_by_id(
    content_id: str = Path(..., description="Content ID"),
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get content by ID from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_content_by_id(content_id, user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting content: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get content: {str(e)}")


@router.patch("/knowledge/content/{content_id}", response_model=AGNOAPIResponse)
async def update_content(
    content_id: str = Path(..., description="Content ID"),
    request_data: ContentUpdateRequest = Body(..., description="Content update request data"),
    current_user: dict = Depends(get_current_user)
):
    """Update content in AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.update_content(
            content_id=content_id,
            data=request_data.dict(exclude_unset=True),
            user_id=user_id
        )
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error updating content: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to update content: {str(e)}")


@router.delete("/knowledge/content/{content_id}", response_model=AGNOAPIResponse)
async def delete_content_by_id(
    content_id: str = Path(..., description="Content ID"),
    current_user: dict = Depends(get_current_user)
):
    """Delete content by ID from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.delete_content_by_id(content_id, user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error deleting content: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to delete content: {str(e)}")


@router.get("/knowledge/content/{content_id}/status", response_model=AGNOAPIResponse)
async def get_content_status(
    content_id: str = Path(..., description="Content ID"),
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get content status from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_content_status(content_id, user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting content status: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get content status: {str(e)}")


@router.get("/knowledge/config", response_model=AGNOAPIResponse)
async def get_knowledge_config(
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get knowledge configuration from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_knowledge_config(user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting knowledge config: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get knowledge config: {str(e)}")


# Home endpoint
@router.get("/", response_model=AGNOAPIResponse)
async def get_api_info(
    force_refresh: bool = Query(False, description="Force refresh cache"),
    current_user: dict = Depends(get_current_user)
):
    """Get API information from AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        data = await agno_service.get_api_info(user_id=user_id)
        
        return AGNOAPIResponse(
            success=True,
            data=data,
            cached=not force_refresh,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting API info: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get API info: {str(e)}")


# Cache management endpoints
@router.post("/cache/clear")
async def clear_cache(
    endpoint: Optional[str] = Query(None, description="Endpoint to clear cache for"),
    current_user: dict = Depends(get_current_user)
):
    """Clear AGNO API cache."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:
        cleared_count = await agno_service.api_service.clear_cache(
            endpoint=endpoint,
            user_id=user_id
        )
        
        return AGNOAPIResponse(
            success=True,
            data={"cleared_entries": cleared_count},
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error clearing cache: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to clear cache: {str(e)}")


@router.get("/cache/stats", response_model=AGNOAPIResponse)
async def get_cache_stats(
    current_user: dict = Depends(get_current_user)
):
    """Get cache statistics."""
    correlation_id = get_correlation_id()
    
    try:
        stats = await agno_service.api_service.get_cache_stats()
        
        return AGNOAPIResponse(
            success=True,
            data=stats,
            cached=False,
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.error(f"Error getting cache stats: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get cache stats: {str(e)}")


# Query-agent endpoint (direct route, no prefix)
@query_agent_router.post("/query-agent")
async def query_agent(
    request: QueryAgentRequest = Body(...),
    current_user: dict = Depends(get_current_user),
    db: "AsyncSession" = Depends(get_db)
):
    """Query agent endpoint that forwards requests to AGNO API."""
    correlation_id = get_correlation_id()
    user_id = get_user_id()
    
    try:        
        result = await db.execute(
            select(Agent).where(Agent.name == request.agent_name)
        )
        agent = result.scalar_one_or_none()
        
        if not agent:
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{request.agent_name}' not found"
            )
        
        # Update last_used timestamp for the agent
        await _update_agent_last_used(agent.id, db)
        await db.commit()  # Commit the last_used update
        
        agent_id = agent.agent_id or str(agent.public_id)
        
        # Prepare data for AGNO API
        stream_enabled = True
        if request.stream_config:
            stream_enabled = request.stream_config.get("stream", True)
        
        data = {
            "message": request.query,
            "stream": str(stream_enabled).lower(),
            "session_id": request.session_id or str(uuid4()),
            "user_id": user_id
        }
        
        # Call AGNO API
        response_data = await agno_service.create_agent_run(
            agent_id=agent_id,
            data=data,
            user_id=user_id,
            use_multipart=False
        )
        
        # Extract stream_id and session_id from response
        stream_id = request.stream_id or response_data.get("stream_id") or response_data.get("streamId") or str(uuid4())
        session_id = request.session_id or response_data.get("session_id") or data["session_id"]
        
        return {
            "status": "started",
            "message": "Agent execution started. Events will be published to Web PubSub.",
            "stream_id": stream_id,
            "session_id": session_id,
            "agent_name": request.agent_name,
            "user_id": user_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying agent: {str(e)}, correlation_id={correlation_id}")
        raise HTTPException(status_code=500, detail=f"Failed to query agent: {str(e)}")
