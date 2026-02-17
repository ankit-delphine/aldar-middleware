#!/usr/bin/env python3
"""Script to create test user agents (Draft and Active) for a specific user.

Run:
  poetry run python -m scripts.create_test_user_agents
"""

import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from aldar_middleware.settings import settings
from aldar_middleware.models import User, Agent, UserAgentAccess


async def get_session() -> AsyncSession:
    """Create async database session."""
    engine = create_async_engine(str(settings.db_url_property), echo=False, future=True)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return SessionLocal()


async def create_test_agents():
    """Create Draft and Active test agents for user."""
    user_id = "7fb93cf6-38dd-4308-b716-f11fd9b8dd23"
    
    session = await get_session()
    
    try:
        # Check if user exists
        result = await session.execute(
            select(User).where(User.id == uuid.UUID(user_id))
        )
        user = result.scalar_one_or_none()
        
        if not user:
            print(f"❌ User with ID {user_id} not found in database!")
            print("Please create the user first or check the user ID.")
            return
        
        print(f"✅ Found user: {user.email} ({user.username})")
        
        # Create Draft Agent
        draft_agent = Agent(
            public_id=uuid.uuid4(),
            name=f"Test Draft Agent - {user.username}",
            intro="Draft Agent for Testing",
            description="This is a draft user agent for testing purposes. It should not appear in production agent lists.",
            icon="/images/test_draft_agent.png",
            mcp_url="https://test.mcp.server/draft",
            health_url=None,
            model_name="gpt-4o",
            model_provider="openai",
            knowledge_sources={"sources": ["test_docs"]},
            is_enabled=True,
            include_in_teams=False,
            agent_header={"Authorization": "Bearer test_token"},
            instruction="You are a test draft agent. Your responses should be helpful and informative.",
            agent_capabilities="Test draft agent capabilities",
            add_history_to_context=True,
            agent_metadata={
                "created_by": "test_script",
                "purpose": "testing",
                "user_id": user_id
            },
            is_healthy=True,
            health_status="healthy",
            # Legacy fields
            agent_id=f"test-draft-{user.username}-{uuid.uuid4().hex[:8]}",
            title=f"Test Draft Agent - {user.username}",
            subtitle="Draft Testing Agent",
            legacy_tags=["user_agents", "testing", "draft"],
            category="user_agents",
            status="DRAFT",  # DRAFT status
            methods=["Get", "Post"],
            order=100,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        
        session.add(draft_agent)
        await session.flush()
        
        print(f"✅ Created Draft Agent: {draft_agent.name} (ID: {draft_agent.id}, Status: {draft_agent.status})")
        
        # Create Active Agent
        active_agent = Agent(
            public_id=uuid.uuid4(),
            name=f"Test Active Agent - {user.username}",
            intro="Active Agent for Testing",
            description="This is an active user agent for testing purposes. It should appear in agent lists.",
            icon="/images/test_active_agent.png",
            mcp_url="https://test.mcp.server/active",
            health_url="https://test.mcp.server/active/health",
            model_name="gpt-4o",
            model_provider="openai",
            knowledge_sources={"sources": ["production_docs", "knowledge_base"]},
            is_enabled=True,
            include_in_teams=True,
            agent_header={"Authorization": "Bearer prod_token"},
            instruction="You are a test active agent. Provide comprehensive and accurate responses.",
            agent_capabilities="Test active agent with full capabilities",
            add_history_to_context=True,
            agent_metadata={
                "created_by": "test_script",
                "purpose": "testing",
                "user_id": user_id,
                "environment": "production"
            },
            is_healthy=True,
            health_status="healthy",
            last_health_check=datetime.utcnow(),
            # Legacy fields
            agent_id=f"test-active-{user.username}-{uuid.uuid4().hex[:8]}",
            title=f"Test Active Agent - {user.username}",
            subtitle="Active Testing Agent",
            legacy_tags=["user_agents", "testing", "active"],
            category="user_agents",
            status="ACTIVE",  # ACTIVE status
            methods=["Get", "Post", "Put", "Delete"],
            order=101,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        
        session.add(active_agent)
        await session.flush()
        
        print(f"✅ Created Active Agent: {active_agent.name} (ID: {active_agent.id}, Status: {active_agent.status})")
        
        # Create UserAgentAccess records to link user to both agents
        draft_access = UserAgentAccess(
            id=uuid.uuid4(),
            user_id=uuid.UUID(user_id),
            agent_id=draft_agent.id,
            access_level="admin",  # User has admin access to their own agents
            granted_at=datetime.utcnow(),
            granted_by=uuid.UUID(user_id),  # Self-granted
            is_active=True,
            expires_at=None,
            access_metadata={
                "granted_reason": "test_agent_creation",
                "agent_status": "DRAFT"
            },
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        
        active_access = UserAgentAccess(
            id=uuid.uuid4(),
            user_id=uuid.UUID(user_id),
            agent_id=active_agent.id,
            access_level="admin",  # User has admin access to their own agents
            granted_at=datetime.utcnow(),
            granted_by=uuid.UUID(user_id),  # Self-granted
            is_active=True,
            expires_at=None,
            access_metadata={
                "granted_reason": "test_agent_creation",
                "agent_status": "ACTIVE"
            },
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        
        session.add_all([draft_access, active_access])
        await session.commit()
        
        print(f"✅ Linked user to both agents via UserAgentAccess")
        print("\n" + "="*70)
        print("SUMMARY:")
        print("="*70)
        print(f"User ID: {user_id}")
        print(f"User Email: {user.email}")
        print(f"User Username: {user.username}")
        print(f"\nDraft Agent:")
        print(f"  - ID: {draft_agent.id}")
        print(f"  - Public ID: {draft_agent.public_id}")
        print(f"  - Name: {draft_agent.name}")
        print(f"  - Status: {draft_agent.status}")
        print(f"  - Agent ID: {draft_agent.agent_id}")
        print(f"\nActive Agent:")
        print(f"  - ID: {active_agent.id}")
        print(f"  - Public ID: {active_agent.public_id}")
        print(f"  - Name: {active_agent.name}")
        print(f"  - Status: {active_agent.status}")
        print(f"  - Agent ID: {active_agent.agent_id}")
        print("\n✅ Test agents created successfully!")
        
    except Exception as e:
        await session.rollback()
        print(f"❌ Error creating test agents: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(create_test_agents())

