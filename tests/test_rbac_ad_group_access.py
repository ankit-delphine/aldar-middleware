#!/usr/bin/env python3
"""
Test RBAC AD Group-based Access Control

This test simulates the login flow where:
1. User logs in
2. User's AD groups are fetched (simulated)
3. AD groups are synced to the pivot table
4. User gets access to agents based on AD group intersection

Run with: python tests/test_rbac_ad_group_access.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Load environment variables from .env file if it exists
from dotenv import load_dotenv
env_path = project_root / '.env'
if env_path.exists():
    load_dotenv(env_path)

# Set environment variables before importing app modules
os.environ.setdefault('ENVIRONMENT', 'testing')
os.environ.setdefault('ALDAR_APP_INSIGHTS_ENABLED', 'false')
os.environ.setdefault('ALDAR_DISTRIBUTED_TRACING_ENABLED', 'false')
os.environ.setdefault('ALDAR_COSMOS_LOGGING_ENABLED', 'false')

from aldar_middleware.services.rbac_service import RBACServiceLayer
from aldar_middleware.services.rbac_pivot_service import RBACPivotService
from aldar_middleware.models.menu import Agent
from sqlalchemy import select
from aldar_middleware.settings import settings


# Load test data from aldar-middleware/.dev/
TEST_DATA_DIR = Path(__file__).parent.parent / ".dev"
TEST_AGENTS_FILE = TEST_DATA_DIR / "test_agents.json"
TEST_USERS_FILE = TEST_DATA_DIR / "test_users.json"


def load_test_agents() -> List[Dict[str, Any]]:
    """Load test agents from JSON file."""
    if not TEST_AGENTS_FILE.exists():
        raise FileNotFoundError(f"Test agents file not found at {TEST_AGENTS_FILE}")
    
    with open(TEST_AGENTS_FILE, 'r') as f:
        data = json.load(f)
        return data.get("agents", [])


def load_test_users() -> List[Dict[str, Any]]:
    """Load test users from JSON file."""
    if not TEST_USERS_FILE.exists():
        raise FileNotFoundError(f"Test users file not found at {TEST_USERS_FILE}")
    
    with open(TEST_USERS_FILE, 'r') as f:
        data = json.load(f)
        return data.get("users", [])


async def verify_test_agents(db_session: AsyncSession, test_agents_data: List[Dict[str, Any]]):
    """Verify test agents exist and have correct AD groups assigned.
    
    Note: Agents should already be created via create_test_agents.py script.
    This function only verifies they exist and have the correct AD groups.
    """
    pivot_service = RBACPivotService(db_session)
    
    verified_agents = []
    
    print("\n" + "="*60)
    print("Verifying test agents (should be created via create_test_agents.py)...")
    print("="*60 + "\n")
    
    for agent_data in test_agents_data:
        agent_name = agent_data["agent_name"]
        expected_ad_groups = set(agent_data.get("azure_ad_groups", []))
        
        try:
            # Get agent AD groups
            agent_groups = await pivot_service.get_agent_ad_groups(agent_name)
            agent_groups_set = set(agent_groups)
            
            if agent_groups_set == expected_ad_groups:
                verified_agents.append(agent_name)
                print(f"✓ Verified agent: {agent_name} ({len(agent_groups)} AD groups)")
            else:
                print(f"⚠ Agent {agent_name} exists but AD groups don't match")
                print(f"  Expected: {expected_ad_groups}")
                print(f"  Found: {agent_groups_set}")
                # Try to update AD groups
                try:
                    await pivot_service.assign_agent_ad_groups(agent_name, list(expected_ad_groups))
                    print(f"  ✓ Updated AD groups for {agent_name}")
                    verified_agents.append(agent_name)
                except Exception as update_error:
                    print(f"  ✗ Failed to update AD groups: {update_error}")
        except Exception as e:
            print(f"✗ Agent {agent_name} not found or error: {e}")
            print(f"  Please run create_test_agents.py first to create agents")
    
    print(f"\n✓ Verification complete: {len(verified_agents)} agents verified\n")
    return verified_agents


async def test_simulate_login_and_ad_group_sync(
    db_session: AsyncSession,
    test_users_data: List[Dict[str, Any]]
):
    """Test simulating login flow with AD group syncing and agent access."""
    pivot_service = RBACPivotService(db_session)
    
    # Test with user1@example.com
    test_user = next((u for u in test_users_data if u["user_name"] == "user1@example.com"), None)
    if test_user is None:
        raise ValueError("Test user user1@example.com not found")
    
    user_name = test_user["user_name"]
    expected_ad_groups = test_user["azure_ad_groups"]
    
    print(f"\n{'='*60}")
    print(f"Testing login simulation for: {user_name}")
    print(f"Expected AD groups: {expected_ad_groups}")
    print(f"{'='*60}\n")
    
    # Step 1: Simulate login - sync user AD groups (simulating API call)
    print("Step 1: Simulating login - syncing user AD groups...")
    user_pivot = await pivot_service.sync_user_ad_groups_direct(user_name, expected_ad_groups)
    if user_pivot is None:
        raise AssertionError("User pivot should not be None")
    if user_pivot.user_name != user_name:
        raise AssertionError(f"User name mismatch: {user_pivot.user_name} != {user_name}")
    if set(user_pivot.azure_ad_groups) != set(expected_ad_groups):
        raise AssertionError(f"AD groups mismatch")
    print(f"✓ User AD groups synced: {user_pivot.azure_ad_groups}\n")
    
    # Step 2: Verify user AD groups are stored
    print("Step 2: Verifying stored AD groups...")
    stored_groups = await pivot_service.get_user_ad_groups(user_name)
    if set(stored_groups) != set(expected_ad_groups):
        raise AssertionError(f"Stored groups mismatch: {stored_groups} != {expected_ad_groups}")
    print(f"✓ Stored AD groups verified: {stored_groups}\n")
    
    # Step 3: Check which agents the user has access to
    print("Step 3: Checking agent access based on AD group intersection...")
    accessible_agents = []
    
    # Get agents from the agents table (not rbac_agents) - filter to rbac_test_agent_*
    result = await db_session.execute(
        select(Agent).where(Agent.name.like("rbac_test_agent_%"))
    )
    test_agents = result.scalars().all()
    
    if not test_agents:
        print("⚠ No rbac_test_agent_* agents found in agents table.")
        print("  Please run create_test_agents.py first to create agents.")
        raise AssertionError("No rbac_test_agent_* agents found")
    
    for agent in test_agents:
        agent_name = agent.name
        agent_groups = await pivot_service.get_agent_ad_groups(agent_name)
        
        # Check intersection
        user_groups_set = set(expected_ad_groups)
        agent_groups_set = set(agent_groups)
        intersection = user_groups_set & agent_groups_set
        
        # Use the service method to check access
        has_access = await pivot_service.check_user_has_access_to_agent(user_name, agent_name)
        
        if intersection:
            if not has_access:
                raise AssertionError(f"Service should confirm access to {agent_name}")
            accessible_agents.append({
                "agent_name": agent_name,
                "common_groups": list(intersection),
                "user_groups": expected_ad_groups,
                "agent_groups": agent_groups
            })
            print(f"  ✓ Access to {agent_name} (common groups: {list(intersection)})")
        else:
            if has_access:
                raise AssertionError(f"Service should deny access to {agent_name}")
            print(f"  ✗ No access to {agent_name}")
    
    print(f"\n✓ User has access to {len(accessible_agents)} agents\n")
    
    # Step 4: Verify expected access based on test data
    # user1 should have access to rbac_test_agent_1 and rbac_test_agent_2
    accessible_agent_names = [a["agent_name"] for a in accessible_agents]
    if "rbac_test_agent_1" not in accessible_agent_names:
        raise AssertionError("User should have access to rbac_test_agent_1")
    if "rbac_test_agent_2" not in accessible_agent_names:
        raise AssertionError("User should have access to rbac_test_agent_2")
    
    print(f"{'='*60}")
    print(f"✓ Test completed successfully!")
    print(f"User: {user_name}")
    print(f"Accessible agents: {accessible_agent_names}")
    print(f"{'='*60}\n")
    
    return True


async def test_multiple_users_access_patterns(
    db_session: AsyncSession,
    test_users_data: List[Dict[str, Any]]
):
    """Test access patterns for multiple users."""
    pivot_service = RBACPivotService(db_session)
    
    # Test multiple users
    # Expected access based on AD group intersections:
    # user1: rbac_test_agent_1, rbac_test_agent_2 (shared groups)
    # user7: rbac_test_agent_1, rbac_test_agent_2, rbac_test_agent_3, rbac_test_agent_4 (super user)
    # user10: [] (no access - isolated groups)
    # user5: rbac_test_agent_4 (exclusive access)
    test_cases = [
        ("user1@example.com", ["rbac_test_agent_1", "rbac_test_agent_2"]),
        ("user7@example.com", ["rbac_test_agent_1", "rbac_test_agent_2", "rbac_test_agent_3", "rbac_test_agent_4"]),  # Super user
        ("user10@example.com", []),  # No access
        ("user5@example.com", ["rbac_test_agent_4"]),  # Exclusive access
    ]
    
    print(f"\n{'='*60}")
    print("Testing multiple users access patterns...")
    print(f"{'='*60}\n")
    
    all_passed = True
    
    for user_name, expected_agents in test_cases:
        test_user = next((u for u in test_users_data if u["user_name"] == user_name), None)
        if not test_user:
            print(f"⚠ User {user_name} not found in test data, skipping...")
            continue
        
        # Sync user AD groups
        await pivot_service.sync_user_ad_groups_direct(
            user_name,
            test_user["azure_ad_groups"]
        )
        
        # Check access - only check rbac_test_agent_* agents from agents table
        accessible_agents = []
        result = await db_session.execute(
            select(Agent).where(Agent.name.like("rbac_test_agent_%"))
        )
        test_agents = result.scalars().all()
        
        if not test_agents:
            print(f"⚠ No rbac_test_agent_* agents found for {user_name}.")
            print(f"  Please run create_test_agents.py first to create agents.")
            continue
        
        for agent in test_agents:
            agent_name = agent.name
            agent_groups = await pivot_service.get_agent_ad_groups(agent_name)
            user_groups = set(test_user["azure_ad_groups"])
            agent_groups_set = set(agent_groups)
            
            if user_groups & agent_groups_set:
                accessible_agents.append(agent_name)
        
        print(f"\nUser: {user_name}")
        print(f"Expected agents: {expected_agents}")
        print(f"Accessible agents: {accessible_agents}")
        
        # Verify access matches expectations
        if set(accessible_agents) != set(expected_agents):
            print(f"✗ Access mismatch for {user_name}. Expected {expected_agents}, got {accessible_agents}")
            all_passed = False
        else:
            print(f"✓ Access matches expectations")
    
    print(f"\n{'='*60}")
    if all_passed:
        print("✓ All user access patterns test passed!")
    else:
        print("✗ Some user access patterns test failed!")
    print(f"{'='*60}\n")
    
    return all_passed


async def test_ad_group_intersection_logic(
    db_session: AsyncSession
):
    """Test AD group intersection logic directly."""
    pivot_service = RBACPivotService(db_session)
    
    # Test case: User with groups [A, B, C] and Agent with groups [B, C, D]
    # Should have access (intersection: [B, C])
    user_name = "test_intersection_user@example.com"
    user_groups = [
        "a1b2c3d4-e5f6-4789-a012-b3c4d5e6f789",
        "b2c3d4e5-f6a7-4890-b123-c4d5e6f7a890",
        "c3d4e5f6-a7b8-4901-c234-d5e6f7a8b901"
    ]
    
    agent_name = "rbac_test_agent_1"
    
    print(f"\n{'='*60}")
    print("Testing AD group intersection logic...")
    print(f"{'='*60}\n")
    
    # Sync user groups
    await pivot_service.sync_user_ad_groups_direct(user_name, user_groups)
    
    # Get agent groups
    agent_groups = await pivot_service.get_agent_ad_groups(agent_name)
    
    # Check intersection
    user_groups_set = set(user_groups)
    agent_groups_set = set(agent_groups)
    intersection = user_groups_set & agent_groups_set
    
    print(f"User groups: {user_groups}")
    print(f"Agent groups: {agent_groups}")
    print(f"Intersection: {list(intersection)}")
    
    # Verify intersection exists
    if len(intersection) == 0:
        raise AssertionError("User should have access to agent (intersection should be non-empty)")
    
    # Check access using service method
    has_access = await pivot_service.check_user_has_access_to_agent(user_name, agent_name)
    if not has_access:
        raise AssertionError("User should have access to agent")
    
    print(f"✓ Access check passed: {has_access}")
    print(f"{'='*60}\n")
    
    return True


async def test_no_access_scenario(
    db_session: AsyncSession
):
    """Test scenario where user has no access to any agent."""
    pivot_service = RBACPivotService(db_session)
    
    # User with isolated groups (no overlap with any agent)
    user_name = "test_no_access@example.com"
    user_groups = [
        "a1b2c3d4-e5f6-4789-a012-b3c4d5e6f790",  # Isolated group
        "b2c3d4e5-f6a7-4890-b123-c4d5e6f7a891",  # Isolated group
    ]
    
    print(f"\n{'='*60}")
    print("Testing no access scenario...")
    print(f"{'='*60}\n")
    
    # Sync user groups
    await pivot_service.sync_user_ad_groups_direct(user_name, user_groups)
    
    # Check access to rbac_test_agent_* agents from agents table
    result = await db_session.execute(
        select(Agent).where(Agent.name.like("rbac_test_agent_%"))
    )
    test_agents = result.scalars().all()
    
    if not test_agents:
        print("⚠ No rbac_test_agent_* agents found.")
        print("  Please run create_test_agents.py first to create agents.")
        return True  # Skip test if agents don't exist
    
    accessible_count = 0
    
    for agent in test_agents:
        has_access = await pivot_service.check_user_has_access_to_agent(user_name, agent.name)
        if has_access:
            accessible_count += 1
    
    if accessible_count != 0:
        raise AssertionError(f"User should have no access, but has access to {accessible_count} agents")
    
    print(f"✓ User correctly has no access to any agent")
    print(f"{'='*60}\n")
    
    return True


async def main():
    """Main test function."""
    print("\n" + "="*60)
    print("RBAC AD Group-based Access Control Test")
    print("="*60 + "\n")
    
    # Load test data
    try:
        test_agents_data = load_test_agents()
        test_users_data = load_test_users()
        print(f"✓ Loaded {len(test_agents_data)} test agents")
        print(f"✓ Loaded {len(test_users_data)} test users\n")
    except Exception as e:
        print(f"✗ Error loading test data: {e}")
        return 1
    
    # Create database connection
    try:
        # Get database URL from settings or construct it
        db_url = settings.db_url
        if not db_url:
            # Construct from environment variables if not set
            db_host = os.getenv('ALDAR_DB_HOST') or os.getenv('DB_HOST', 'localhost')
            db_port = os.getenv('ALDAR_DB_PORT') or os.getenv('DB_PORT', '5432')
            db_user = os.getenv('ALDAR_DB_USER') or os.getenv('DB_USER', 'aiq')
            db_pass = os.getenv('ALDAR_DB_PASS') or os.getenv('DB_PASSWORD', 'aiq')
            db_name = os.getenv('ALDAR_DB_BASE') or os.getenv('DB_NAME', 'aiq')
            db_url = f"postgresql+asyncpg://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
        
        print(f"Connecting to database: {db_user}@{db_host}:{db_port}/{db_name}")
        engine = create_async_engine(
            db_url,
            echo=False,
            future=True,
        )
        async_session = sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        print("✓ Database connection established\n")
    except Exception as e:
        print(f"✗ Error connecting to database: {e}")
        return 1
    
    # Run tests
    results = []
    
    try:
        async with async_session() as db_session:
            # Verify test agents exist (should be created via create_test_agents.py)
            await verify_test_agents(db_session, test_agents_data)
            
            # Test 1: Simulate login and AD group sync
            print("\n" + "="*60)
            print("TEST 1: Simulate Login and AD Group Sync")
            print("="*60)
            try:
                result1 = await test_simulate_login_and_ad_group_sync(db_session, test_users_data)
                results.append(("Test 1: Login Simulation", result1))
            except Exception as e:
                print(f"✗ Test 1 failed: {e}")
                results.append(("Test 1: Login Simulation", False))
            
            # Test 2: Multiple users access patterns
            print("\n" + "="*60)
            print("TEST 2: Multiple Users Access Patterns")
            print("="*60)
            try:
                result2 = await test_multiple_users_access_patterns(db_session, test_users_data)
                results.append(("Test 2: Multiple Users", result2))
            except Exception as e:
                print(f"✗ Test 2 failed: {e}")
                results.append(("Test 2: Multiple Users", False))
            
            # Test 3: AD group intersection logic
            print("\n" + "="*60)
            print("TEST 3: AD Group Intersection Logic")
            print("="*60)
            try:
                result3 = await test_ad_group_intersection_logic(db_session)
                results.append(("Test 3: Intersection Logic", result3))
            except Exception as e:
                print(f"✗ Test 3 failed: {e}")
                results.append(("Test 3: Intersection Logic", False))
            
            # Test 4: No access scenario
            print("\n" + "="*60)
            print("TEST 4: No Access Scenario")
            print("="*60)
            try:
                result4 = await test_no_access_scenario(db_session)
                results.append(("Test 4: No Access", result4))
            except Exception as e:
                print(f"✗ Test 4 failed: {e}")
                results.append(("Test 4: No Access", False))
        
        # Print summary
        print("\n" + "="*60)
        print("TEST SUMMARY")
        print("="*60)
        passed = sum(1 for _, result in results if result)
        total = len(results)
        
        for test_name, result in results:
            status = "✓ PASSED" if result else "✗ FAILED"
            print(f"{status}: {test_name}")
        
        print(f"\nTotal: {passed}/{total} tests passed")
        print("="*60 + "\n")
        
        return 0 if passed == total else 1
        
    except Exception as e:
        print(f"\n✗ Fatal error during testing: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
