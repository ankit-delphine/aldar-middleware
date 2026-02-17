"""Admin Configuration API routes - Admin only access."""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from aldar_middleware.database.base import get_db
from aldar_middleware.models.admin_config import AdminConfig
from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/config", tags=["Admin Config"])


# Pydantic Schemas
class AdminConfigCreate(BaseModel):
    """Schema for creating admin config."""
    key: str = Field(..., min_length=1, max_length=255, description="Configuration key")
    value: Any = Field(..., description="Configuration value as JSON")


class AdminConfigUpdate(BaseModel):
    """Schema for updating admin config."""
    value: Any = Field(..., description="Configuration value as JSON")


class AdminConfigResponse(BaseModel):
    """Schema for admin config response."""
    id: str
    key: str
    value: Any
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Dependency to require admin access."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user


@router.get("", response_model=List[AdminConfigResponse])
async def list_admin_configs(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
) -> List[AdminConfigResponse]:
    """
    List all admin configurations.
    
    **Admin only**
    """
    result = await db.execute(select(AdminConfig).order_by(AdminConfig.key))
    configs = result.scalars().all()
    
    return [
        AdminConfigResponse(
            id=str(config.id),
            key=config.key,
            value=config.value,
            created_by=str(config.created_by) if config.created_by else None,
            updated_by=str(config.updated_by) if config.updated_by else None,
            created_at=config.created_at.isoformat(),
            updated_at=config.updated_at.isoformat()
        )
        for config in configs
    ]


@router.get("/{key}", response_model=AdminConfigResponse)
async def get_admin_config(
    key: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
) -> AdminConfigResponse:
    """
    Get a specific admin configuration by key.
    
    **Admin only**
    """
    result = await db.execute(select(AdminConfig).where(AdminConfig.key == key))
    config = result.scalar_one_or_none()
    
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Configuration '{key}' not found"
        )
    
    return AdminConfigResponse(
        id=str(config.id),
        key=config.key,
        value=config.value,
        created_by=str(config.created_by) if config.created_by else None,
        updated_by=str(config.updated_by) if config.updated_by else None,
        created_at=config.created_at.isoformat(),
        updated_at=config.updated_at.isoformat()
    )


@router.post("", response_model=AdminConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_admin_config(
    data: AdminConfigCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
) -> AdminConfigResponse:
    """
    Create a new admin configuration.
    
    **Admin only**
    
    Request body:
    - **key**: Configuration key (unique)
    - **value**: Configuration value as JSON (can be object, array, string, number, boolean, null)
    """
    # Check if key already exists
    existing = await db.execute(select(AdminConfig).where(AdminConfig.key == data.key))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Configuration '{data.key}' already exists"
        )
    
    config = AdminConfig(
        key=data.key,
        value=data.value,
        created_by=current_user.id,
        updated_by=current_user.id
    )
    
    db.add(config)
    await db.commit()
    await db.refresh(config)
    
    logger.info(f"Admin config created: {data.key} by user {current_user.email}")
    
    return AdminConfigResponse(
        id=str(config.id),
        key=config.key,
        value=config.value,
        created_by=str(config.created_by) if config.created_by else None,
        updated_by=str(config.updated_by) if config.updated_by else None,
        created_at=config.created_at.isoformat(),
        updated_at=config.updated_at.isoformat()
    )


@router.put("/{key}", response_model=AdminConfigResponse)
async def update_admin_config(
    key: str,
    data: AdminConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
) -> AdminConfigResponse:
    """
    Update an existing admin configuration.
    
    **Admin only**
    
    Request body:
    - **value**: New configuration value as JSON
    """
    result = await db.execute(select(AdminConfig).where(AdminConfig.key == key))
    config = result.scalar_one_or_none()
    
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Configuration '{key}' not found"
        )
    
    config.value = data.value
    config.updated_by = current_user.id
    
    await db.commit()
    await db.refresh(config)
    
    logger.info(f"Admin config updated: {key} by user {current_user.email}")
    
    return AdminConfigResponse(
        id=str(config.id),
        key=config.key,
        value=config.value,
        created_by=str(config.created_by) if config.created_by else None,
        updated_by=str(config.updated_by) if config.updated_by else None,
        created_at=config.created_at.isoformat(),
        updated_at=config.updated_at.isoformat()
    )


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_admin_config(
    key: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """
    Delete an admin configuration.
    
    **Admin only**
    """
    result = await db.execute(select(AdminConfig).where(AdminConfig.key == key))
    config = result.scalar_one_or_none()
    
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Configuration '{key}' not found"
        )
    
    await db.execute(delete(AdminConfig).where(AdminConfig.key == key))
    await db.commit()
    
    logger.info(f"Admin config deleted: {key} by user {current_user.email}")


@router.post("/upsert", response_model=AdminConfigResponse)
async def upsert_admin_config(
    data: AdminConfigCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
) -> AdminConfigResponse:
    """
    Create or update an admin configuration (upsert).
    
    **Admin only**
    
    If key exists, updates the value. If not, creates new config.
    """
    result = await db.execute(select(AdminConfig).where(AdminConfig.key == data.key))
    config = result.scalar_one_or_none()
    
    if config:
        # Update existing
        config.value = data.value
        config.updated_by = current_user.id
        logger.info(f"Admin config upserted (updated): {data.key} by user {current_user.email}")
    else:
        # Create new
        config = AdminConfig(
            key=data.key,
            value=data.value,
            created_by=current_user.id,
            updated_by=current_user.id
        )
        db.add(config)
        logger.info(f"Admin config upserted (created): {data.key} by user {current_user.email}")
    
    await db.commit()
    await db.refresh(config)
    
    return AdminConfigResponse(
        id=str(config.id),
        key=config.key,
        value=config.value,
        created_by=str(config.created_by) if config.created_by else None,
        updated_by=str(config.updated_by) if config.updated_by else None,
        created_at=config.created_at.isoformat(),
        updated_at=config.updated_at.isoformat()
    )
