"""
RBAC User Sync Service
Bridges the gap between main User model and RBAC User model
"""

from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import logging

from aldar_middleware.models.user import User
from aldar_middleware.models.rbac import RBACUser
from aldar_middleware.services.rbac_service import RBACServiceLayer
from aldar_middleware.exceptions import RBACError

logger = logging.getLogger(__name__)


class RBACUserSyncService:
    """Service to synchronize main User model with RBAC User model"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.rbac_service = RBACServiceLayer(db)
    
    async def sync_user_to_rbac(self, user: User) -> RBACUser:
        """
        Sync a main User to RBAC system
        
        Args:
            user: The main User model instance
            
        Returns:
            RBACUser instance (existing or newly created)
        """
        try:
            # Check if RBAC user already exists
            result = await self.db.execute(
                select(RBACUser).where(
                    (RBACUser.email == user.email) | (RBACUser.username == user.username)
                )
            )
            rbac_user = result.scalar_one_or_none()
            
            if rbac_user:
                # Update existing RBAC user
                rbac_user.username = user.username
                rbac_user.email = user.email
                rbac_user.full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
                rbac_user.is_active = user.is_active
                
                await self.db.commit()
                await self.db.refresh(rbac_user)
                
                logger.info(f"Updated RBAC user: {rbac_user.username}")
            else:
                # Create new RBAC user
                rbac_user = RBACUser(
                    username=user.username,
                    email=user.email,
                    full_name=f"{user.first_name or ''} {user.last_name or ''}".strip(),
                    is_active=user.is_active
                )
                
                self.db.add(rbac_user)
                await self.db.commit()
                await self.db.refresh(rbac_user)
                
                logger.info(f"Created RBAC user: {rbac_user.username}")
            
            return rbac_user
            
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to sync user to RBAC: {str(e)}")
            raise RBACError(f"Failed to sync user: {str(e)}")
    
    async def sync_all_users(self) -> dict:
        """
        Sync all users from main User model to RBAC
        
        Returns:
            Dictionary with sync statistics
        """
        try:
            # Get all active users
            result = await self.db.execute(
                select(User).where(User.is_active == True)
            )
            users = result.scalars().all()
            
            synced = 0
            failed = 0
            errors = []
            
            for user in users:
                try:
                    await self.sync_user_to_rbac(user)
                    synced += 1
                except Exception as e:
                    failed += 1
                    errors.append(f"{user.username}: {str(e)}")
            
            stats = {
                "total": len(users),
                "synced": synced,
                "failed": failed,
                "errors": errors[:10]  # First 10 errors
            }
            
            logger.info(f"Synced {synced}/{len(users)} users to RBAC system")
            return stats
            
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to sync all users: {str(e)}")
            raise RBACError(f"Failed to sync users: {str(e)}")
    
    async def get_rbac_user_by_username(self, username: str) -> Optional[RBACUser]:
        """Get RBAC user by username"""
        result = await self.db.execute(
            select(RBACUser).where(RBACUser.username == username)
        )
        return result.scalar_one_or_none()
    
    async def get_rbac_user_by_email(self, email: str) -> Optional[RBACUser]:
        """Get RBAC user by email"""
        result = await self.db.execute(
            select(RBACUser).where(RBACUser.email == email)
        )
        return result.scalar_one_or_none()

