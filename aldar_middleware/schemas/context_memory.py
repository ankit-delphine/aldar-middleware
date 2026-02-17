"""Context memory schemas."""

from typing import Optional, List
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field


class ContextMemoryResponse(BaseModel):
    """Context memory response schema."""
    
    user_id: str = Field(..., description="User email ID")
    memory_id: UUID = Field(..., description="Unique memory identifier")
    memory: str = Field(..., description="Memory content")
    topics: Optional[List[str]] = Field(None, description="Topics associated with the memory")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    
    class Config:
        from_attributes = True


class ContextMemoryListResponse(BaseModel):
    """Response for listing context memories."""
    
    success: bool = Field(True, description="Operation success status")
    total: int = Field(..., description="Total number of memories")
    page: int = Field(..., description="Current page number")
    limit: int = Field(..., description="Number of items per page")
    total_pages: int = Field(..., description="Total number of pages")
    category: List[str] = Field(default_factory=list, description="All unique topics/categories from memories")
    memories: List[ContextMemoryResponse] = Field(..., description="List of memories")
    filtered_by_topic: Optional[str] = Field(None, description="Topic filter applied, if any")
    search_query: Optional[str] = Field(None, description="Search query applied, if any")


class DeleteMemoryResponse(BaseModel):
    """Response for deleting a memory."""
    
    success: bool = Field(True, description="Operation success status")
    message: str = Field(..., description="Operation result message")
    memory_id: UUID = Field(..., description="ID of the deleted memory")


class BulkDeleteMemoryRequest(BaseModel):
    """Request for deleting multiple memories."""

    memory_ids: List[UUID] = Field(
        ...,
        min_length=1,
        description="List of context memory IDs to delete",
    )


class BulkDeleteMemoryResponse(BaseModel):
    """Response for deleting multiple memories."""

    success: bool = Field(True, description="Operation success status")
    message: str = Field(..., description="Operation result message")
    deleted_count: int = Field(..., description="Number of memories deleted")
    deleted_memory_ids: List[UUID] = Field(
        default_factory=list,
        description="List of successfully deleted memory IDs",
    )
    not_found_memory_ids: List[UUID] = Field(
        default_factory=list,
        description="List of memory IDs not found or not owned by the user",
    )
