"""Starter prompt schemas."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class StarterPromptResponse(BaseModel):
    """Starter prompt response schema."""
    
    id: str
    title: str
    prompt: str  # This serves as both the description and the actual prompt text
    is_highlighted: bool = False
    is_hide: bool = False
    order: int = 0
    knowledge_agent_id: Optional[str] = None  # Legacy field
    my_agent_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class StarterPromptsListResponse(BaseModel):
    """List of starter prompts response schema."""
    
    prompts: List[StarterPromptResponse]
    total: int

