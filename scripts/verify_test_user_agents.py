#!/usr/bin/env python3
"""Script to verify test user agents exist in database.

Run:
  poetry run python -m scripts.verify_test_user_agents
"""

import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from aldar_middleware.settings import settings
from aldar_middleware.models import User, Agent, UserAgentAccess


async def get_session() -> AsyncSession:
    """Create async database session."""
    engine = create_async_engine(str(settings.db_url_property), echo=False, future=True)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return SessionLocal()


async def verify_agents():
    """Verify test agents exist for user."""
    user_id = "7fb93cf6-38dd-4308-b716-f11fd9b8dd23"
    
    session = await get_session()
    
    try:
        # Get user
        result = await session.execute(
            select(User).where(User.id == uuid.UUID(user_id))
        )
        user = result.scalar_one_or_none()
        
        if not user:
            print(f"❌ User with ID {user_id} not found!")
            return
        
        print("="*70)
        print("USER INFORMATION")
        print("="*70)
        print(f"User ID: {user.id}")
        print(f"Email: {user.email}")
        print(f"Username: {user.username}")
        print(f"Full Name: {user.full_name}")
        print(f"Is Active: {user.is_active}")
        
        # Get all agents linked to this user via UserAgentAccess
        access_result = await session.execute(
            select(UserAgentAccess)
            .where(UserAgentAccess.user_id == uuid.UUID(user_id))
        )
        access_records = access_result.scalars().all()
        
        print(f"\n{'='*70}")
        print(f"TOTAL AGENT ACCESS RECORDS: {len(access_records)}")
        print("="*70)
        
        for idx, access in enumerate(access_records, 1):
            # Get the actual agent
            agent_result = await session.execute(
                select(Agent).where(Agent.id == access.agent_id)
            )
            agent = agent_result.scalar_one_or_none()
            
            if not agent:
                print(f"\n⚠️  Access record {idx} points to non-existent agent (ID: {access.agent_id})")
                continue
            
            print(f"\n{'-'*70}")
            print(f"AGENT #{idx}")
            print(f"{'-'*70}")
            print(f"Agent ID (DB): {agent.id}")
            print(f"Public ID: {agent.public_id}")
            print(f"Agent ID (Legacy): {agent.agent_id}")
            print(f"Name: {agent.name}")
            print(f"Intro: {agent.intro}")
            print(f"Status: {agent.status}")
            print(f"Category: {agent.category}")
            print(f"Is Enabled: {agent.is_enabled}")
            print(f"Is Healthy: {agent.is_healthy}")
            print(f"Health Status: {agent.health_status}")
            print(f"Include in Teams: {agent.include_in_teams}")
            print(f"Model: {agent.model_name} ({agent.model_provider})")
            print(f"MCP URL: {agent.mcp_url}")
            print(f"Description: {agent.description}")
            print(f"Created At: {agent.created_at}")
            print(f"Updated At: {agent.updated_at}")
            
            print(f"\nAccess Details:")
            print(f"  - Access Level: {access.access_level}")
            print(f"  - Is Active: {access.is_active}")
            print(f"  - Granted At: {access.granted_at}")
            print(f"  - Expires At: {access.expires_at}")
        
        # Specifically look for Draft and Active agents
        print(f"\n{'='*70}")
        print("STATUS SUMMARY")
        print("="*70)
        
        draft_count = 0
        active_count = 0
        other_count = 0
        
        for access in access_records:
            agent_result = await session.execute(
                select(Agent).where(Agent.id == access.agent_id)
            )
            agent = agent_result.scalar_one_or_none()
            
            if agent:
                if agent.status and agent.status.upper() == "DRAFT":
                    draft_count += 1
                elif agent.status and agent.status.upper() == "ACTIVE":
                    active_count += 1
                else:
                    other_count += 1
        
        print(f"Draft Agents: {draft_count}")
        print(f"Active Agents: {active_count}")
        print(f"Other Status Agents: {other_count}")
        
        if draft_count > 0 and active_count > 0:
            print("\n✅ SUCCESS: User has both Draft and Active agents!")
        elif draft_count > 0:
            print("\n⚠️  User has Draft agent(s) but no Active agents")
        elif active_count > 0:
            print("\n⚠️  User has Active agent(s) but no Draft agents")
        else:
            print("\n❌ User has no Draft or Active agents")
        
    except Exception as e:
        print(f"❌ Error verifying agents: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(verify_agents())

