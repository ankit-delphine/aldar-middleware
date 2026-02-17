"""User settings API routes."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aldar_middleware.database.base import get_db
from aldar_middleware.models.user import User
from aldar_middleware.models.user_settings import UserSettings
from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.schemas.user_settings import (
    UserSettingsUpdate,
    UserSettingsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user-settings", tags=["User Settings"])


@router.get(
    "",
    response_model=UserSettingsResponse,
    summary="Get user settings",
    description="Get settings for the current user"
)
async def get_user_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> UserSettingsResponse:
    """
    Get user settings for the current user.
    
    Args:
        current_user: Current authenticated user
        db: Database session
        
    Returns:
        User settings
    """
    try:
        # Query for user settings
        query = select(UserSettings).where(
            UserSettings.user_id == current_user.email
        )
        result = await db.execute(query)
        user_settings = result.scalar_one_or_none()
        
        # If settings don't exist, create default settings
        if not user_settings:
            user_settings = UserSettings(
                user_id=current_user.email,
                add_memories_to_context=True
            )
            db.add(user_settings)
            await db.commit()
            await db.refresh(user_settings)
            logger.info(f"Created default user settings for user: {current_user.email}")
        
        return UserSettingsResponse.model_validate(user_settings)
        
    except Exception as e:
        logger.error(f"Error getting user settings: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get user settings: {str(e)}"
        )


@router.put(
    "",
    response_model=UserSettingsResponse,
    summary="Update user settings",
    description="Update or create settings for the current user (upsert)"
)
async def update_user_settings(
    settings_update: UserSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> UserSettingsResponse:
    """
    Update or create user settings for the current user.
    
    This endpoint performs an upsert operation:
    - If settings exist for the user, they are updated
    - If settings don't exist, they are created
    
    Args:
        settings_update: Settings update data
        current_user: Current authenticated user
        db: Database session
        
    Returns:
        Updated or created user settings
    """
    try:
        # Query for existing user settings
        query = select(UserSettings).where(
            UserSettings.user_id == current_user.email
        )
        result = await db.execute(query)
        user_settings = result.scalar_one_or_none()
        
        # If settings exist, update them
        if user_settings:
            user_settings.add_memories_to_context = settings_update.add_memories_to_context
            user_settings.updated_at = datetime.utcnow()
            logger.info(
                f"Updated user settings for user: {current_user.email}, "
                f"add_memories_to_context={settings_update.add_memories_to_context}"
            )
        else:
            # If settings don't exist, create new ones
            user_settings = UserSettings(
                user_id=current_user.email,
                add_memories_to_context=settings_update.add_memories_to_context
            )
            db.add(user_settings)
            logger.info(
                f"Created user settings for user: {current_user.email}, "
                f"add_memories_to_context={settings_update.add_memories_to_context}"
            )
        
        await db.commit()
        await db.refresh(user_settings)
        
        return UserSettingsResponse.model_validate(user_settings)
        
    except Exception as e:
        logger.error(f"Error updating user settings: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update user settings: {str(e)}"
        )
