"""Add default pinned apps (Workspaces, Data Camp, Oracle) for existing users who don't have any pinned apps.

Run this script once to migrate existing users:
  poetry run python -m scripts.add_default_pins_for_existing_users
"""

import asyncio
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables first, before any aldar_middleware imports
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    print(f"Warning: .env file not found at {env_path}")

# Add parent directory to path so we can import aldar_middleware modules
script_dir = Path(__file__).parent
parent_dir = script_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

# Import directly from modules to avoid triggering app initialization in __init__.py
from sqlalchemy import select, and_, func

# Direct imports to avoid aldar_middleware.__init__
from aldar_middleware.database.base import async_session
from aldar_middleware.models.user import User
from aldar_middleware.models.menu import LaunchpadApp, UserLaunchpadPin


async def add_default_pins_for_existing_users():
    """Add default pins for all existing users who don't have any pinned apps."""
    async with async_session() as session:
        # Get all users
        users_result = await session.execute(select(User))
        all_users = users_result.scalars().all()
        
        print(f"Found {len(all_users)} users in database")
        
        # Get default apps
        default_app_ids = ["workspaces", "data-camp", "oracle"]
        apps_result = await session.execute(
            select(LaunchpadApp).where(
                and_(
                    LaunchpadApp.app_id.in_(default_app_ids),
                    LaunchpadApp.is_active == True
                )
            )
        )
        default_apps = apps_result.scalars().all()
        
        if not default_apps:
            print("❌ Default apps not found in database!")
            print("Please run: poetry run python -m scripts.add_default_pinned_apps")
            return
        
        print(f"Found {len(default_apps)} default apps: {[app.title for app in default_apps]}")
        
        users_updated = 0
        users_skipped = 0
        
        for user in all_users:
            # Check if user has any pinned apps
            pinned_result = await session.execute(
                select(UserLaunchpadPin).where(
                    and_(
                        UserLaunchpadPin.user_id == user.id,
                        UserLaunchpadPin.is_pinned == True
                    )
                )
            )
            existing_pinned = pinned_result.scalars().all()
            
            # If user has no pinned apps, add default pins
            if not existing_pinned:
                for order, app in enumerate(default_apps, start=1):
                    # Check if pin already exists (even if unpinned)
                    existing_pin_result = await session.execute(
                        select(UserLaunchpadPin).where(
                            and_(
                                UserLaunchpadPin.user_id == user.id,
                                UserLaunchpadPin.app_id == app.id
                            )
                        )
                    )
                    existing_pin = existing_pin_result.scalar_one_or_none()
                    
                    if not existing_pin:
                        # Create new pinned pin
                        user_pin = UserLaunchpadPin(
                            user_id=user.id,
                            app_id=app.id,
                            is_pinned=True,
                            order=order
                        )
                        session.add(user_pin)
                    elif not existing_pin.is_pinned:
                        # Update existing unpinned pin to pinned
                        existing_pin.is_pinned = True
                        existing_pin.order = order
                
                users_updated += 1
                if users_updated % 100 == 0:
                    print(f"  Processed {users_updated} users...")
            else:
                users_skipped += 1
        
        await session.commit()
        
        print(f"\n✅ Migration complete!")
        print(f"   - Users updated (got default pins): {users_updated}")
        print(f"   - Users skipped (already have pins): {users_skipped}")
        print(f"   - Total users: {len(all_users)}")


if __name__ == "__main__":
    asyncio.run(add_default_pins_for_existing_users())

