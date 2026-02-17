"""User preferences schemas for custom query settings."""

from typing import Optional, List, Union
from pydantic import BaseModel, Field, field_validator, model_validator


class UserPreferencesResponse(BaseModel):
    """Response schema for user preferences."""
    is_custom_query_enabled: bool = False
    custom_query_about_user: Optional[str] = None
    custom_query_preferred_formatting: Optional[str] = None
    custom_query_topics_of_interest: Optional[List[str]] = None

    class Config:
        from_attributes = True


class UserPreferencesUpdate(BaseModel):
    """Request schema for updating user preferences.
    
    Accepts both frontend field names (enable_for_new_messages, about_user, etc.)
    and backend field names (is_custom_query_enabled, custom_query_about_user, etc.)
    """
    # Backend field names (with aliases for frontend compatibility)
    is_custom_query_enabled: Optional[bool] = Field(None, alias="enable_for_new_messages")
    custom_query_about_user: Optional[str] = Field(
        None,
        alias="about_user",
        max_length=400,
        description="Details about the user (max 400 characters)"
    )
    custom_query_preferred_formatting: Optional[str] = Field(
        None,
        alias="preferred_formatting",
        max_length=300,
        description="Preferred formatting style (max 300 characters)"
    )
    custom_query_topics_of_interest: Optional[Union[List[str], str]] = Field(
        None,
        alias="topics_of_interest",
        description="List of topics of interest (max 50 topics) - can be array or comma-separated string"
    )
    
    @model_validator(mode='before')
    @classmethod
    def map_frontend_fields(cls, data):
        """Map frontend field names to backend field names."""
        if isinstance(data, dict):
            # Map frontend field names to backend field names
            if 'enable_for_new_messages' in data and 'is_custom_query_enabled' not in data:
                data['is_custom_query_enabled'] = data.get('enable_for_new_messages')
            if 'about_user' in data and 'custom_query_about_user' not in data:
                data['custom_query_about_user'] = data.get('about_user')
            if 'preferred_formatting' in data and 'custom_query_preferred_formatting' not in data:
                data['custom_query_preferred_formatting'] = data.get('preferred_formatting')
            if 'topics_of_interest' in data and 'custom_query_topics_of_interest' not in data:
                data['custom_query_topics_of_interest'] = data.get('topics_of_interest')
        return data

    @field_validator('custom_query_topics_of_interest', mode='before')
    @classmethod
    def validate_topics(cls, v) -> Optional[List[str]]:
        """Validate topics list - handle both string and list formats."""
        if v is None:
            return None
        
        # If it's a string, convert to list (split by comma or newline)
        if isinstance(v, str):
            if not v.strip():
                return None
            # Split by comma, newline, or both
            topics = [t.strip() for t in v.replace('\n', ',').split(',') if t.strip()]
        elif isinstance(v, list):
            topics = v
        else:
            return None
        
        if len(topics) > 50:
            raise ValueError("Maximum 50 topics allowed")
        
        # Filter out empty strings
        topics = [topic.strip() for topic in topics if topic and topic.strip()]
        return topics if topics else None

    @field_validator('custom_query_about_user', 'custom_query_preferred_formatting')
    @classmethod
    def validate_string_fields(cls, v: Optional[str]) -> Optional[str]:
        """Validate string fields are not empty strings."""
        if v is not None and isinstance(v, str):
            v = v.strip()
            if not v:
                return None
        return v

    class Config:
        from_attributes = True
        populate_by_name = True  # Allow both field name and alias
