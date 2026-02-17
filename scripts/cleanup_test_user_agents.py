#!/usr/bin/env python3
"""Script to cleanup test user agents.

Run:
  poetry run python -m scripts.cleanup_test_user_agents
"""

import asyncio
import uuid

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from aldar_middleware.settings import settings
from aldar_middleware.models import Agent, UserAgentAccess


async def get_session() -> AsyncSession:
    """Create async database session."""
    engine = create_async_engine(str(settings.db_url_property), echo=False, future=True)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return SessionLocal()


async def cleanup_agents():
    """Remove test agents created for testing."""
    user_id = "7fb93cf6-38dd-4308-b716-f11fd9b8dd23"
    
    session = await get_session()
    
    try:
        # Find all test agents for this user (those with 'Test' in name and user_agents category)
        result = await session.execute(
            select(Agent).where(
                Agent.name.like(f'Test%{user_id.split("-")[0]}%')
            )
        )
        test_agents = result.scalars().all()
        
        if not test_agents:
            print("ℹ️  No test agents found to cleanup.")
            return
        
        print(f"Found {len(test_agents)} test agent(s) to cleanup:\n")
        
        agent_ids = []
        for agent in test_agents:
            agent_ids.append(agent.id)
            print(f"  - {agent.name} (ID: {agent.id}, Status: {agent.status})")
        
        # Ask for confirmation
        print(f"\n⚠️  This will delete {len(test_agents)} agent(s) and their access records.")
        response = input("Are you sure you want to continue? (yes/no): ")
        
        if response.lower() != 'yes':
            print("❌ Cleanup cancelled.")
            return
        
        # Delete UserAgentAccess records first (foreign key constraint)
        access_result = await session.execute(
            delete(UserAgentAccess).where(
                UserAgentAccess.agent_id.in_(agent_ids)
            )
        )
        deleted_access = access_result.rowcount
        print(f"\n✅ Deleted {deleted_access} UserAgentAccess record(s)")
        
        # Delete agents
        agent_result = await session.execute(
            delete(Agent).where(Agent.id.in_(agent_ids))
        )
        deleted_agents = agent_result.rowcount
        print(f"✅ Deleted {deleted_agents} Agent record(s)")
        
        # Commit changes
        await session.commit()
        
        print("\n" + "="*70)
        print("CLEANUP SUMMARY")
        print("="*70)
        print(f"User ID: {user_id}")
        print(f"Agents Removed: {deleted_agents}")
        print(f"Access Records Removed: {deleted_access}")
        print("\n✅ Cleanup completed successfully!")
        
    except Exception as e:
        await session.rollback()
        print(f"❌ Error during cleanup: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await session.close()


async def cleanup_specific_agents():
    """Remove specific test agents by ID."""
    # The IDs from the creation script
    draft_agent_id = 138
    active_agent_id = 139
    
    session = await get_session()
    
    try:
        # Check if agents exist
        result = await session.execute(
            select(Agent).where(Agent.id.in_([draft_agent_id, active_agent_id]))
        )
        agents = result.scalars().all()
        
        if not agents:
            print("ℹ️  Specified test agents not found.")
            return
        
        print(f"Found {len(agents)} test agent(s):\n")
        for agent in agents:
            print(f"  - {agent.name} (ID: {agent.id}, Status: {agent.status})")
        
        print(f"\n⚠️  This will delete these specific agents and their access records.")
        response = input("Are you sure you want to continue? (yes/no): ")
        
        if response.lower() != 'yes':
            print("❌ Cleanup cancelled.")
            return
        
        # Delete UserAgentAccess records
        access_result = await session.execute(
            delete(UserAgentAccess).where(
                UserAgentAccess.agent_id.in_([draft_agent_id, active_agent_id])
            )
        )
        deleted_access = access_result.rowcount
        print(f"\n✅ Deleted {deleted_access} UserAgentAccess record(s)")
        
        # Delete agents
        agent_result = await session.execute(
            delete(Agent).where(Agent.id.in_([draft_agent_id, active_agent_id]))
        )
        deleted_agents = agent_result.rowcount
        print(f"✅ Deleted {deleted_agents} Agent record(s)")
        
        # Commit changes
        await session.commit()
        print("\n✅ Specific agents cleaned up successfully!")
        
    except Exception as e:
        await session.rollback()
        print(f"❌ Error during cleanup: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await session.close()


if __name__ == "__main__":
    print("="*70)
    print("TEST AGENT CLEANUP")
    print("="*70)
    print("\nSelect cleanup method:")
    print("1. Cleanup all test agents (by name pattern)")
    print("2. Cleanup specific agents (IDs: 138, 139)")
    print("3. Cancel")
    
    choice = input("\nEnter choice (1/2/3): ")
    
    if choice == "1":
        asyncio.run(cleanup_agents())
    elif choice == "2":
        asyncio.run(cleanup_specific_agents())
    else:
        print("❌ Cleanup cancelled.")

