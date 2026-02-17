"""Authentication dependencies."""

from typing import Optional
from uuid import UUID
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from aldar_middleware.auth.azure_ad import azure_ad_auth
from aldar_middleware.auth.token_blacklist import token_blacklist
from aldar_middleware.database.base import get_db
from aldar_middleware.models.user import User
from sqlalchemy import select


security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db = Depends(get_db)
) -> User:
    """Get current authenticated user from Azure AD token."""
    token = credentials.credentials
    
    # Check if token is blacklisted
    if token_blacklist.is_blacklisted(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been invalidated (logged out)"
        )
    
    try:
        # Validate Azure AD token using Microsoft's public keys
        # This verifies signature, expiration, audience, and issuer
        payload = await azure_ad_auth.validate_token(token)
        
        # Extract Azure AD user ID from token (oid is the object ID, sub is the subject)
        azure_ad_user_id = payload.get("oid") or payload.get("sub")
        
        if not azure_ad_user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing user identifier"
            )
        
        # Get user from database using Azure AD ID
        result = await db.execute(
            select(User).where(User.azure_ad_id == azure_ad_user_id)
        )
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found"
            )
        
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is disabled"
            )
        
        return user
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed"
        )


async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """Get current active user."""
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    return current_user


async def get_current_verified_user(
    current_user: User = Depends(get_current_active_user)
) -> User:
    """Get current verified user."""
    if not current_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User not verified"
        )
    return current_user


async def get_current_user_id(
    current_user: User = Depends(get_current_active_user)
) -> UUID:
    """Get current authenticated user's ID."""
    return current_user.id


async def get_current_admin_user(
    current_user: User = Depends(get_current_verified_user)
) -> User:
    """Get current admin user (defensive check for is_admin attribute)."""
    if not getattr(current_user, "is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user
