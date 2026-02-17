#!/usr/bin/env python3
"""Diagnose why a user has no access to agents.

Run:
  poetry run python -m scripts.diagnose_user_agent_access
"""

import asyncio
from sqlalchemy import select, and_, or_, cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from aldar_middleware.settings import settings
from aldar_middleware.models import Agent
from aldar_middleware.models.rbac import RBACUserPivot, RBACAgentPivot


async def get_session() -> AsyncSession:
    """Create async database session."""
    engine = create_async_engine(str(settings.db_url_property), echo=False, future=True)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return SessionLocal()


async def diagnose_access():
    """Diagnose agent access for a specific user."""
    
    user_email = "spandey@delphime.com"
    
    session = await get_session()
    
    try:
        print("=" * 80)
        print(f"AGENT ACCESS DIAGNOSIS FOR: {user_email}")
        print("=" * 80)
        
        # Get user's AD groups
        user_pivot_result = await session.execute(
            select(RBACUserPivot).where(RBACUserPivot.email == user_email)
        )
        user_pivot = user_pivot_result.scalar_one_or_none()
        
        if not user_pivot:
            print(f"\n❌ User '{user_email}' not found in RBAC configuration!")
            print("   User needs to login to sync AD groups")
            return
        
        user_ad_groups = user_pivot.azure_ad_groups or []
        
        print(f"\n👤 User: {user_email}")
        print(f"   Azure AD Groups: {len(user_ad_groups)}")
        if user_ad_groups:
            for group in user_ad_groups:
                print(f"   - {group}")
        else:
            print("   ⚠️  No AD groups!")
        
        # Get all enabled + active enterprise agents (same query as API)
        print("\n" + "=" * 80)
        print("ENABLED & ACTIVE ENTERPRISE AGENTS (API Query)")
        print("=" * 80)
        
        query = select(Agent).where(
            and_(
                Agent.is_enabled == True,
                or_(
                    Agent.status.ilike('active'),
                    Agent.status.is_(None)
                ),
                Agent.category != "user_agents",
                or_(
                    Agent.legacy_tags.is_(None),
                    ~cast(Agent.legacy_tags, JSONB).op('@>')(cast(["user_agents"], JSONB))
                )
            )
        )
        
        result = await session.execute(query)
        agents = result.scalars().all()
        
        print(f"\nTotal agents matching API query: {len(agents)}")
        
        if not agents:
            print("\n⚠️  No enabled agents found!")
            print("   This means all agents are either:")
            print("   - Disabled (is_enabled = False)")
            print("   - Not active (status != 'ACTIVE')")
            print("   - User agents (category = 'user_agents')")
            return
        
        # Check RBAC for each agent
        print("\n" + "=" * 80)
        print("RBAC ACCESS CHECK FOR EACH AGENT")
        print("=" * 80)
        
        accessible_count = 0
        inaccessible_count = 0
        
        for idx, agent in enumerate(agents, 1):
            # Get agent's AD groups
            agent_pivot_result = await session.execute(
                select(RBACAgentPivot).where(RBACAgentPivot.agent_name == agent.name)
            )
            agent_pivot = agent_pivot_result.scalar_one_or_none()
            
            agent_ad_groups = agent_pivot.azure_ad_groups if agent_pivot else []
            
            # Check intersection
            user_groups_set = set(user_ad_groups)
            agent_groups_set = set(agent_ad_groups)
            intersection = user_groups_set & agent_groups_set
            has_access = bool(intersection)
            
            print(f"\n{'-' * 80}")
            print(f"Agent #{idx}: {agent.name}")
            print(f"{'-' * 80}")
            print(f"ID: {agent.id}")
            print(f"Status: {agent.status}")
            print(f"Enabled: {agent.is_enabled}")
            print(f"Category: {agent.category}")
            
            if agent_ad_groups:
                print(f"\nAgent's AD Groups ({len(agent_ad_groups)}):")
                for group in agent_ad_groups:
                    is_match = group in user_groups_set
                    print(f"  {'✅' if is_match else '❌'} {group}")
            else:
                print(f"\n⚠️  Agent has NO AD groups assigned!")
                print(f"   → Agent is inaccessible to ALL users")
            
            if has_access:
                accessible_count += 1
                print(f"\n✅ USER HAS ACCESS")
                print(f"   Matching AD Groups: {len(intersection)}")
                for group in intersection:
                    print(f"   - {group}")
            else:
                inaccessible_count += 1
                print(f"\n❌ USER HAS NO ACCESS")
                if agent_ad_groups:
                    print(f"   Reason: No matching AD groups")
                    print(f"   User needs one of these groups:")
                    for group in agent_ad_groups:
                        print(f"   - {group}")
                else:
                    print(f"   Reason: Agent has no AD groups assigned")
        
        # Summary
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"\nUser: {user_email}")
        print(f"User's AD Groups: {len(user_ad_groups)}")
        print(f"\nTotal Enabled Agents: {len(agents)}")
        print(f"Accessible Agents: {accessible_count} ✅")
        print(f"Inaccessible Agents: {inaccessible_count} ❌")
        
        if accessible_count == 0:
            print(f"\n{'=' * 80}")
            print("❌ WHY USER HAS NO ACCESS")
            print("=" * 80)
            print("\nPossible reasons:")
            print("1. User's AD groups don't match any agent's AD groups")
            print("2. Agents have no AD groups assigned")
            print("3. User needs to be added to agent-specific AD groups in Azure")
            
            print(f"\n{'=' * 80}")
            print("🔧 HOW TO FIX")
            print("=" * 80)
            print("\nOption 1: Add User to Agent's AD Groups (in Azure AD)")
            print("   - Go to Azure Active Directory")
            print("   - Find the agent's AD group(s)")
            print("   - Add user to the group(s)")
            
            print("\nOption 2: Assign Agent's AD Groups (via API)")
            print("   - Use RBAC API to assign user's AD groups to agents")
            print(f"   - POST /api/v1/rbac/agents/{{agent_name}}/ad-groups")
            print(f"   - Body: {{'azure_ad_groups': {user_ad_groups[:1]}}}")
            
            print("\nOption 3: Check if agents need AD group configuration")
            if inaccessible_count > 0:
                no_groups = [a.name for a in agents if not (
                    await session.execute(
                        select(RBACAgentPivot).where(RBACAgentPivot.agent_name == a.name)
                    )
                ).scalar_one_or_none() or not (
                    await session.execute(
                        select(RBACAgentPivot).where(RBACAgentPivot.agent_name == a.name)
                    )
                ).scalar_one_or_none().azure_ad_groups]
                
                if no_groups:
                    print(f"   Agents without AD groups: {len(no_groups)}")
                    for name in no_groups[:5]:
                        print(f"   - {name}")
        else:
            print(f"\n✅ User has access to {accessible_count} agent(s)")
        
    except Exception as e:
        print(f"❌ Error during diagnosis: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(diagnose_access())

