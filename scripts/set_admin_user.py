#!/usr/bin/env python3
"""Script to set a user as admin."""

import asyncio
import sys
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aldar_middleware.database.base import get_async_session
from aldar_middleware.models.user import User


async def set_user_as_admin(email: str) -> None:
    """Set a user as admin by email."""
    async for db in get_async_session():
        # Find user by email
        result = await db.execute(
            select(User).where(User.email == email)
        )
        user = result.scalar_one_or_none()
        
        if not user:
            print(f"❌ User with email '{email}' not found")
            return
        
        # Set as admin
        user.is_admin = True
        await db.commit()
        await db.refresh(user)
        
        print(f"✅ User '{user.email}' ({user.username}) is now an admin")


async def list_admin_users() -> None:
    """List all admin users."""
    async for db in get_async_session():
        result = await db.execute(
            select(User).where(User.is_admin == True)
        )
        admin_users = result.scalars().all()
        
        if not admin_users:
            print("📋 No admin users found")
            return
        
        print("📋 Admin users:")
        for user in admin_users:
            print(f"  - {user.email} ({user.username}) - {user.first_name} {user.last_name}")


async def main():
    """Main function."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python scripts/set_admin_user.py <email>     # Set user as admin")
        print("  python scripts/set_admin_user.py --list    # List admin users")
        return
    
    if sys.argv[1] == "--list":
        await list_admin_users()
    else:
        email = sys.argv[1]
        await set_user_as_admin(email)


if __name__ == "__main__":
    asyncio.run(main())
