"""Starter prompts API endpoints."""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.database.base import get_db
from aldar_middleware.models import Agent, StarterPrompt, User
from aldar_middleware.schemas.starter_prompt import (
    StarterPromptResponse,
    StarterPromptsListResponse,
)

router = APIRouter(prefix="/starter-prompts", tags=["starter-prompts"])


@router.get("/", response_model=StarterPromptsListResponse)
async def get_starter_prompts(
    agent_public_id: Optional[UUID] = Query(None, description="Filter by agent public ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StarterPromptsListResponse:
    """
    Get all starter prompts.
    
    Returns a list of starter prompts, optionally filtered by agent.
    Only prompts with is_hide=True are returned.
    Results are ordered by the 'order' field.
    """
    # Build query
    query = select(StarterPrompt)

    # Always return only visible prompts
    query = query.where(StarterPrompt.is_hide == True)
    
    # Apply filters
    if agent_public_id is not None:
        # First, get the agent's internal ID from public_id
        agent_result = await db.execute(
            select(Agent.id).where(Agent.public_id == agent_public_id)
        )
        agent_id = agent_result.scalar_one_or_none()
        
        if agent_id is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        query = query.where(StarterPrompt.my_agent_id == agent_id)
    
    # Order by order field
    query = query.order_by(StarterPrompt.order)
    
    # Execute query
    result = await db.execute(query)
    prompts = result.scalars().all()
    
    # Convert to response models
    prompt_responses = [
        StarterPromptResponse(
            id=prompt.id,
            title=prompt.title,
            prompt=prompt.prompt,
            is_highlighted=prompt.is_highlighted,
            is_hide=prompt.is_hide,
            order=prompt.order,
            knowledge_agent_id=prompt.knowledge_agent_id,
            my_agent_id=prompt.my_agent_id,
            created_at=prompt.created_at,
            updated_at=prompt.updated_at,
        )
        for prompt in prompts
    ]
    
    return StarterPromptsListResponse(
        prompts=prompt_responses,
        total=len(prompt_responses),
    )


@router.get("/{prompt_id}", response_model=StarterPromptResponse)
async def get_starter_prompt(
    prompt_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StarterPromptResponse:
    """
    Get a specific starter prompt by ID.
    """
    result = await db.execute(
        select(StarterPrompt).where(
            StarterPrompt.id == prompt_id,
            StarterPrompt.is_hide == True,
        )
    )
    prompt = result.scalar_one_or_none()
    
    if not prompt:
        raise HTTPException(status_code=404, detail="Starter prompt not found")
    
    return StarterPromptResponse(
        id=prompt.id,
        title=prompt.title,
        prompt=prompt.prompt,
        is_highlighted=prompt.is_highlighted,
        is_hide=prompt.is_hide,
        order=prompt.order,
        knowledge_agent_id=prompt.knowledge_agent_id,
        my_agent_id=prompt.my_agent_id,
        created_at=prompt.created_at,
        updated_at=prompt.updated_at,
    )

