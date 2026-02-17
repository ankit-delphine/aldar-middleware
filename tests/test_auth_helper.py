"""Test authentication helper for API testing."""

import asyncio
import os
from typing import Dict, Optional
from pathlib import Path
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, defer
from dotenv import load_dotenv
from aldar_middleware.models import User
from aldar_middleware.auth.azure_ad import azure_ad_auth
import logging

logger = logging.getLogger(__name__)

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path)


class AuthTestHelper:
    """Helper class for test authentication."""
    
    def __init__(self, db_url: Optional[str] = None):
        """Initialize test auth helper."""
        # Get database URL from environment or use provided one
        if db_url:
            self.db_url = db_url
        else:
            # Build database URL from environment variables (try ALDAR_ prefix first)
            db_host = os.getenv('ALDAR_DB_HOST') or os.getenv('DB_HOST', 'localhost')
            db_port = os.getenv('ALDAR_DB_PORT') or os.getenv('DB_PORT', '5432')
            db_user = os.getenv('ALDAR_DB_USER') or os.getenv('DB_USER', 'aiq')
            db_pass = os.getenv('ALDAR_DB_PASS') or os.getenv('DB_PASSWORD', 'aiq')
            # Database name from environment (aiq_test for testing, aiq for production)
            db_name = os.getenv('ALDAR_DB_BASE') or os.getenv('DB_NAME', 'aiq_test')
            self.db_url = f"postgresql+asyncpg://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
            logger.debug(f"Using database: {db_user}@{db_host}:{db_port}/{db_name}")
        
        self.engine = None
        self.session = None
        self.test_users = {}
        
    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
        
    async def connect(self):
        """Connect to database."""
        self.engine = create_async_engine(self.db_url, echo=False)
        async_session_maker = sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        self.session = async_session_maker()
        
    async def close(self):
        """Close database connection."""
        if self.session:
            await self.session.close()
        if self.engine:
            await self.engine.dispose()
    
    async def create_test_user(
        self, 
        email: str = "testadmin@example.com",
        username: str = "testadmin",
        first_name: str = "Test",
        last_name: str = "Admin",
        is_admin: bool = True,
        is_active: bool = True
    ) -> User:
        """Create or get test user."""
        try:
            # Check if user already exists
            result = await self.session.execute(
                select(User).where(User.email == email)
            )
            user = result.scalar_one_or_none()
            
            if user:
                # Update existing user (only update fields that exist)
                try:
                    if hasattr(user, 'is_admin'):
                        user.is_admin = is_admin
                    if hasattr(user, 'is_active'):
                        user.is_active = is_active
                    if hasattr(user, 'is_verified'):
                        user.is_verified = True
                    await self.session.commit()
                    await self.session.refresh(user)
                    logger.info(f"Updated existing test user: {email}")
                except Exception as e:
                    logger.warning(f"Could not update user fields: {e}")
                    await self.session.rollback()
            else:
                # Create new user with only required fields
                user_data = {
                    'email': email,
                    'username': username,
                    'first_name': first_name,
                    'last_name': last_name,
                }
                # Add optional fields if they exist in the model
                if hasattr(User, 'is_admin'):
                    user_data['is_admin'] = is_admin
                if hasattr(User, 'is_active'):
                    user_data['is_active'] = is_active
                if hasattr(User, 'is_verified'):
                    user_data['is_verified'] = True
                    
                user = User(**user_data)
                self.session.add(user)
                await self.session.commit()
                await self.session.refresh(user)
                logger.info(f"Created new test user: {email}")
            
            return user
        except Exception as e:
            logger.error(f"Error creating/updating test user: {e}")
            await self.session.rollback()
            raise
    
    def create_jwt_token(self, user: User) -> str:
        """Create JWT token for user."""
        return azure_ad_auth.create_jwt_token(
            user_id=str(user.id),
            email=user.email
        )
    
    async def get_admin_token(self, use_existing_user: bool = True) -> Dict[str, str]:
        """Get admin user and token for testing."""
        try:
            # Generate a fresh JWT token for Rishabh's admin account
            # User ID: 50234d04-ab16-4723-a0b7-df7f43d93384
            # Email: Rishabh_DDAIS@delphimeuat.com
            user_id = '50234d04-ab16-4723-a0b7-df7f43d93384'
            email = 'Rishabh_DDAIS@delphimeuat.com'
            
            # Generate a fresh token using the application's JWT secret
            from datetime import timedelta
            token = azure_ad_auth.create_jwt_token(
                user_id=user_id,
                email=email,
                expires_delta=timedelta(hours=24)  # Valid for 24 hours
            )
            
            self.test_users['admin'] = {
                'user_id': user_id,
                'email': email,
                'token': token,
                'headers': {'Authorization': f'Bearer {token}'}
            }
            
            logger.info(f"Generated fresh JWT token for auth: {email}")
            return self.test_users['admin']['headers']
            
        except Exception as e:
            logger.error(f"Failed to get admin token: {e}")
            # Last resort: generate a token with mock data
            import uuid
            mock_user_id = str(uuid.uuid4())
            from datetime import timedelta
            token = azure_ad_auth.create_jwt_token(
                user_id=mock_user_id,
                email="testadmin@example.com",
                expires_delta=timedelta(hours=24)
            )
            return {'Authorization': f'Bearer {token}'}
    
    async def get_regular_user_token(self) -> Dict[str, str]:
        """Get regular (non-admin) user and token for testing."""
        user = await self.create_test_user(
            email="testuser@example.com",
            username="testuser",
            first_name="Test",
            last_name="User",
            is_admin=False
        )
        token = self.create_jwt_token(user)
        
        self.test_users['user'] = {
            'user': user,
            'token': token,
            'headers': {'Authorization': f'Bearer {token}'}
        }
        
        return self.test_users['user']['headers']
    
    async def get_multiple_users(self, count: int = 3) -> Dict[str, Dict]:
        """Create multiple test users."""
        users = {}
        
        for i in range(count):
            email = f"testuser{i+1}@example.com"
            username = f"testuser{i+1}"
            
            user = await self.create_test_user(
                email=email,
                username=username,
                first_name=f"Test{i+1}",
                last_name="User",
                is_admin=(i == 0)  # First user is admin
            )
            token = self.create_jwt_token(user)
            
            users[username] = {
                'user': user,
                'token': token,
                'headers': {'Authorization': f'Bearer {token}'}
            }
        
        self.test_users.update(users)
        return users


def get_auth_headers(token: str) -> Dict[str, str]:
    """Get authorization headers for a given token."""
    return {'Authorization': f'Bearer {token}'}


async def setup_test_auth() -> Dict[str, str]:
    """Quick setup for test authentication - returns admin headers."""
    async with AuthTestHelper() as auth:
        return await auth.get_admin_token()


async def setup_test_users() -> Dict[str, Dict]:
    """Setup multiple test users - returns dict of user data."""
    async with AuthTestHelper() as auth:
        await auth.get_admin_token()
        await auth.get_regular_user_token()
        return auth.test_users

