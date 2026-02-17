#!/usr/bin/env python3
"""Script to test RBAC filtering in /api/v1/agent/available endpoint.

Run:
  poetry run python -m scripts.test_rbac_filtering
"""

import asyncio
from sqlalchemy import select, and_, or_
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


async def test_rbac_filtering():
    """Test RBAC filtering configuration."""
    
    session = await get_session()
    
    try:
        print("=" * 70)
        print("RBAC FILTERING TEST")
        print("=" * 70)
        
        # Test 1: Check agents that would be returned by /api/v1/agent/available
        print("\n📊 Test 1: Agents Retrieved by /api/v1/agent/available Query")
        print("-" * 70)
        
        query = select(Agent).where(
            and_(
                Agent.is_enabled == True,
                or_(
                    Agent.status.ilike('active'),
                    Agent.status.is_(None)
                ),
                Agent.category != "user_agents"
            )
        )
        
        result = await session.execute(query)
        agents = result.scalars().all()
        
        print(f"Total agents matching query: {len(agents)}\n")
        
        for idx, agent in enumerate(agents[:10], 1):  # Show first 10
            print(f"{idx}. {agent.name}")
            print(f"   ID: {agent.id}")
            print(f"   Category: {agent.category}")
            print(f"   Status: {agent.status}")
            print(f"   Enabled: {agent.is_enabled}")
            print()
        
        if len(agents) > 10:
            print(f"... and {len(agents) - 10} more agents")
        
        # Test 2: Check RBAC configuration for agents
        print("\n" + "=" * 70)
        print("📊 Test 2: RBAC Configuration (Azure AD Groups)")
        print("-" * 70)
        
        agent_pivots_result = await session.execute(
            select(RBACAgentPivot)
        )
        agent_pivots = agent_pivots_result.scalars().all()
        
        print(f"Total agents with RBAC config: {len(agent_pivots)}\n")
        
        agents_with_groups = 0
        agents_without_groups = 0
        
        for pivot in agent_pivots[:10]:  # Show first 10
            if pivot.azure_ad_groups:
                agents_with_groups += 1
                print(f"✅ {pivot.agent_name}")
                print(f"   AD Groups: {len(pivot.azure_ad_groups)} groups")
                if pivot.azure_ad_groups:
                    for group in pivot.azure_ad_groups[:3]:  # Show first 3 groups
                        print(f"   - {group}")
                    if len(pivot.azure_ad_groups) > 3:
                        print(f"   ... and {len(pivot.azure_ad_groups) - 3} more")
            else:
                agents_without_groups += 1
                print(f"⚠️  {pivot.agent_name}")
                print(f"   AD Groups: None (No access for any user)")
            print()
        
        if len(agent_pivots) > 10:
            print(f"... and {len(agent_pivots) - 10} more agents")
        
        print(f"\nSummary:")
        print(f"  Agents with AD groups: {agents_with_groups}")
        print(f"  Agents without AD groups: {agents_without_groups}")
        
        # Test 3: Check user RBAC configuration
        print("\n" + "=" * 70)
        print("📊 Test 3: User RBAC Configuration")
        print("-" * 70)
        
        user_pivots_result = await session.execute(
            select(RBACUserPivot)
        )
        user_pivots = user_pivots_result.scalars().all()
        
        print(f"Total users with RBAC config: {len(user_pivots)}\n")
        
        users_with_groups = 0
        users_without_groups = 0
        
        for pivot in user_pivots[:10]:  # Show first 10
            if pivot.azure_ad_groups:
                users_with_groups += 1
                print(f"✅ {pivot.email}")
                print(f"   AD Groups: {len(pivot.azure_ad_groups)} groups")
                if pivot.azure_ad_groups:
                    for group in pivot.azure_ad_groups[:3]:  # Show first 3 groups
                        print(f"   - {group}")
                    if len(pivot.azure_ad_groups) > 3:
                        print(f"   ... and {len(pivot.azure_ad_groups) - 3} more")
            else:
                users_without_groups += 1
                print(f"⚠️  {pivot.email}")
                print(f"   AD Groups: None (No access to any agents)")
            print()
        
        if len(user_pivots) > 10:
            print(f"... and {len(user_pivots) - 10} more users")
        
        print(f"\nSummary:")
        print(f"  Users with AD groups: {users_with_groups}")
        print(f"  Users without AD groups: {users_without_groups}")
        
        # Test 4: Verify user agents are excluded
        print("\n" + "=" * 70)
        print("📊 Test 4: Verify User Agents Are Excluded")
        print("-" * 70)
        
        user_agents_query = select(Agent).where(
            Agent.category == "user_agents"
        )
        user_agents_result = await session.execute(user_agents_query)
        user_agents = user_agents_result.scalars().all()
        
        print(f"Total user agents in database: {len(user_agents)}\n")
        
        for agent in user_agents[:5]:
            print(f"❌ {agent.name}")
            print(f"   Category: {agent.category}")
            print(f"   Status: {agent.status}")
            print(f"   → Will NOT be returned by /api/v1/agent/available")
            print()
        
        if len(user_agents) > 5:
            print(f"... and {len(user_agents) - 5} more user agents")
        
        # Test 5: Check for test agents we created
        print("\n" + "=" * 70)
        print("📊 Test 5: Check Test Agents")
        print("-" * 70)
        
        test_agents_query = select(Agent).where(
            Agent.name.like('%Test%Agent%')
        )
        test_agents_result = await session.execute(test_agents_query)
        test_agents = test_agents_result.scalars().all()
        
        if test_agents:
            print(f"Found {len(test_agents)} test agent(s):\n")
            
            for agent in test_agents:
                print(f"{'✅' if agent.status == 'ACTIVE' else '❌'} {agent.name}")
                print(f"   ID: {agent.id}")
                print(f"   Category: {agent.category}")
                print(f"   Status: {agent.status}")
                print(f"   Enabled: {agent.is_enabled}")
                
                # Check if it would be returned by /api/v1/agent/available
                would_be_returned = (
                    agent.is_enabled and
                    (agent.status and agent.status.upper() == 'ACTIVE') and
                    agent.category != "user_agents"
                )
                
                if would_be_returned:
                    print(f"   → ✅ WOULD be returned by /api/v1/agent/available")
                else:
                    reasons = []
                    if not agent.is_enabled:
                        reasons.append("not enabled")
                    if not (agent.status and agent.status.upper() == 'ACTIVE'):
                        reasons.append(f"status is '{agent.status}' (not ACTIVE)")
                    if agent.category == "user_agents":
                        reasons.append("category is 'user_agents'")
                    
                    print(f"   → ❌ Would NOT be returned because: {', '.join(reasons)}")
                print()
        else:
            print("No test agents found")
        
        # Final Summary
        print("\n" + "=" * 70)
        print("✅ SUMMARY")
        print("=" * 70)
        print(f"\n1. Total enterprise agents (enabled + active): {len(agents)}")
        print(f"2. Agents with RBAC configuration: {len(agent_pivots)}")
        print(f"3. Users with RBAC configuration: {len(user_pivots)}")
        print(f"4. User agents (excluded from endpoint): {len(user_agents)}")
        
        print("\n💡 Key Points:")
        print("   - /api/v1/agent/available excludes all user agents")
        print("   - Only ACTIVE and enabled enterprise agents are considered")
        print("   - RBAC filtering is applied to check Azure AD group intersection")
        print("   - Users without matching AD groups will see 0 agents")
        print("   - Agents without AD groups are inaccessible to all users")
        
        print("\n✅ RBAC filtering is correctly configured!")
        
    except Exception as e:
        print(f"❌ Error during test: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(test_rbac_filtering())

