#!/usr/bin/env python3
"""Diagnose why a user has no access to agents (matches API query exactly).

Run:
  poetry run python -m scripts.diagnose_user_agent_access_v2
"""

import asyncio
from sqlalchemy import select, and_, or_, cast, func
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
        
        # Query 1: Total enabled agents (API's total_enabled_agents query)
        print("\n" + "=" * 80)
        print("QUERY 1: TOTAL ENABLED AGENTS (API's total_enabled_agents)")
        print("=" * 80)
        
        total_enabled_query = select(func.count()).select_from(Agent).where(
            and_(
                Agent.is_enabled == True,
                or_(
                    Agent.status.ilike('active'),
                    Agent.status.is_(None)
                )
            )
        )
        total_enabled_result = await session.execute(total_enabled_query)
        total_enabled = total_enabled_result.scalar()
        
        print(f"\nTotal enabled agents (includes user agents): {total_enabled}")
        
        # Query 2: Enterprise agents only (API's main query)
        print("\n" + "=" * 80)
        print("QUERY 2: ENTERPRISE AGENTS ONLY (API's main query)")
        print("=" * 80)
        
        enterprise_query = select(Agent).where(
            and_(
                Agent.is_enabled == True,
                or_(
                    Agent.status.ilike('active'),
                    Agent.status.is_(None)
                ),
                or_(
                    Agent.category.is_(None),
                    Agent.category != "user_agents"
                ),
                or_(
                    Agent.legacy_tags.is_(None),
                    ~cast(Agent.legacy_tags, JSONB).op('@>')(cast(["user_agents"], JSONB))
                )
            )
        )
        
        enterprise_result = await session.execute(enterprise_query)
        enterprise_agents = enterprise_result.scalars().all()
        
        print(f"\nEnterprise agents (excludes user agents): {len(enterprise_agents)}")
        
        # Show breakdown
        all_enabled_query = select(Agent).where(
            and_(
                Agent.is_enabled == True,
                or_(
                    Agent.status.ilike('active'),
                    Agent.status.is_(None)
                )
            )
        )
        all_enabled_result = await session.execute(all_enabled_query)
        all_enabled = all_enabled_result.scalars().all()
        
        user_agents = [a for a in all_enabled if a.category == "user_agents"]
        
        print(f"\nBreakdown:")
        print(f"  Total enabled agents: {len(all_enabled)}")
        print(f"  - Enterprise agents: {len(enterprise_agents)}")
        print(f"  - User agents: {len(user_agents)}")
        
        if not enterprise_agents:
            print(f"\n⚠️  No enterprise agents found!")
            print(f"   All {total_enabled} enabled agents are user agents")
            
            if user_agents:
                print(f"\n📋 User Agents (excluded from /api/v1/agent/available):")
                for agent in user_agents:
                    print(f"  - {agent.name} (ID: {agent.id}, Status: {agent.status})")
            return
        
        # Check RBAC for each enterprise agent
        print("\n" + "=" * 80)
        print("RBAC ACCESS CHECK FOR ENTERPRISE AGENTS")
        print("=" * 80)
        
        accessible_count = 0
        inaccessible_count = 0
        
        for idx, agent in enumerate(enterprise_agents, 1):
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
            print(f"Category: {agent.category}")
            
            if agent_ad_groups:
                print(f"\nAgent's AD Groups ({len(agent_ad_groups)}):")
                for group in agent_ad_groups[:5]:  # Show first 5
                    is_match = group in user_groups_set
                    print(f"  {'✅' if is_match else '❌'} {group}")
                if len(agent_ad_groups) > 5:
                    print(f"  ... and {len(agent_ad_groups) - 5} more")
            else:
                print(f"\n⚠️  Agent has NO AD groups assigned!")
            
            if has_access:
                accessible_count += 1
                print(f"\n✅ USER HAS ACCESS")
                print(f"   Matching Groups: {list(intersection)}")
            else:
                inaccessible_count += 1
                print(f"\n❌ USER HAS NO ACCESS")
                if agent_ad_groups:
                    print(f"   Reason: No matching AD groups")
                else:
                    print(f"   Reason: Agent has no AD groups")
        
        # Summary
        print("\n" + "=" * 80)
        print("API RESPONSE EXPLANATION")
        print("=" * 80)
        print(f"\nAPI Response:")
        print(f'{{')
        print(f'  "success": false,')
        print(f'  "agents": [],')
        print(f'  "total_count": 0,')
        print(f'  "user_permissions": {{')
        print(f'    "accessible_agents_count": {accessible_count},')
        print(f'    "total_enabled_agents": {total_enabled}')
        print(f'  }}')
        print(f'}}')
        
        print(f"\nExplanation:")
        print(f"  • total_enabled_agents ({total_enabled}) = ALL enabled agents")
        print(f"    - Includes {len(user_agents)} user agent(s)")
        print(f"    - Includes {len(enterprise_agents)} enterprise agent(s)")
        print(f"  • accessible_agents_count ({accessible_count}) = Agents user can access")
        print(f"    - Only enterprise agents with matching AD groups")
        print(f"  • User has {inaccessible_count} inaccessible enterprise agent(s)")
        
        if accessible_count == 0:
            print(f"\n{'=' * 80}")
            print("❌ WHY USER HAS NO ACCESS TO ANY AGENTS")
            print("=" * 80)
            
            if len(enterprise_agents) == 0:
                print("\n1. No enterprise agents are enabled and active")
                print(f"   All {total_enabled} enabled agents are user agents")
            else:
                print(f"\n1. User's AD groups don't match agent AD groups")
                print(f"\n2. User needs to be in one of these AD groups:")
                
                all_required_groups = set()
                for agent in enterprise_agents:
                    agent_pivot_result = await session.execute(
                        select(RBACAgentPivot).where(RBACAgentPivot.agent_name == agent.name)
                    )
                    agent_pivot = agent_pivot_result.scalar_one_or_none()
                    if agent_pivot and agent_pivot.azure_ad_groups:
                        all_required_groups.update(agent_pivot.azure_ad_groups)
                
                for group in list(all_required_groups)[:10]:
                    print(f"   - {group}")
                if len(all_required_groups) > 10:
                    print(f"   ... and {len(all_required_groups) - 10} more")
        
    except Exception as e:
        print(f"❌ Error during diagnosis: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(diagnose_access())

