#!/usr/bin/env python3
"""Script to create admin user."""

import asyncio
import uuid
from datetime import datetime

from aldar_middleware.database.base import engine, Base
from aldar_middleware.models.user import User, UserAgent, UserPermission
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker


async def create_admin_user():
    """Create admin user and permissions."""
    # Create async session
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            # Create admin user
            admin_user = User(
                id=uuid.uuid4(),
                email="admin@aiq.local",
                username="admin",
                first_name="Admin",
                last_name="User",
                is_active=True,
                is_verified=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            session.add(admin_user)
            await session.commit()
            await session.refresh(admin_user)
            
            print(f"✅ Created admin user: {admin_user.email}")
            
            # Create admin agent
            admin_agent = UserAgent(
                id=uuid.uuid4(),
                user_id=admin_user.id,
                name="Admin Assistant",
                description="Administrative AI assistant",
                agent_type="admin",
                model_config={"model": "gpt-4", "temperature": 0.7},
                is_active=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            session.add(admin_agent)
            await session.commit()
            await session.refresh(admin_agent)
            
            print(f"✅ Created admin agent: {admin_agent.name}")
            
            # Create admin permissions
            admin_permissions = [
                UserPermission(
                    id=uuid.uuid4(),
                    user_id=admin_user.id,
                    agent_id=admin_agent.id,
                    permission_type="admin",
                    resource="*",
                    is_granted=True,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                ),
                UserPermission(
                    id=uuid.uuid4(),
                    user_id=admin_user.id,
                    permission_type="read",
                    resource="*",
                    is_granted=True,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                ),
                UserPermission(
                    id=uuid.uuid4(),
                    user_id=admin_user.id,
                    permission_type="write",
                    resource="*",
                    is_granted=True,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
            ]
            
            for permission in admin_permissions:
                session.add(permission)
            
            await session.commit()
            
            print("✅ Created admin permissions")
            print(f"Admin user ID: {admin_user.id}")
            print(f"Admin agent ID: {admin_agent.id}")
            
        except Exception as e:
            print(f"❌ Error creating admin user: {e}")
            await session.rollback()
            raise
        finally:
            await session.close()


if __name__ == "__main__":
    asyncio.run(create_admin_user())
