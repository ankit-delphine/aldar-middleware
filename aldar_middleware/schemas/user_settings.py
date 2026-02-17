"""User settings Pydantic schemas."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class UserSettingsBase(BaseModel):
    """Base schema for user settings."""
    add_memories_to_context: bool = Field(default=True, description="Whether to add memories to context")


class UserSettingsCreate(UserSettingsBase):
    """Schema for creating user settings."""
    user_id: EmailStr = Field(..., description="Email of the user")


class UserSettingsUpdate(BaseModel):
    """Schema for updating user settings."""
    add_memories_to_context: bool = Field(..., description="Whether to add memories to context")


class UserSettingsResponse(UserSettingsBase):
    """Schema for user settings response."""
    id: UUID
    user_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        """Pydantic config."""
        from_attributes = True
