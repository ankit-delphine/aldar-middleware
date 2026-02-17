"""Seed realistic demo data across core models in one go.

Run:
  poetry run python -m scripts.seed_all_demo_data
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from aldar_middleware.settings import settings
from aldar_middleware.models import (
    # users and agents
    User,
    UserAgent,
    MCPConnection,
    # menu/launchpad and agents catalog
    Menu,
    LaunchpadApp,
    Agent,
    UserLaunchpadPin,
    UserAgentPin,
    # sessions and messages (new schema)
    Session,
    Message,
    # feedback
    FeedbackData,
    FeedbackFile,
    FeedbackEntityType,
    FeedbackRating,
    # remediation/observability
    RemediationAction,
    RemediationRule,
    ActionType,
)


async def get_session() -> AsyncSession:
    engine = create_async_engine(str(settings.db_url_property), echo=False, future=True)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return SessionLocal()  # caller should close engine by disposing session.bind.engine


async def seed_users(session: AsyncSession) -> User:
    email = "demo.user@example.com"
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user:
        return user

    user = User(
        id=uuid.uuid4(),
        email=email,
        username="demo",
        full_name="Demo User",
        is_active=True,
        is_verified=True,
        is_admin=False,
        preferences={"theme": "dark", "itemsPerPage": 50},
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(user)
    await session.flush()
    return user


async def seed_mcp(session: AsyncSession) -> MCPConnection:
    result = await session.execute(select(MCPConnection).where(MCPConnection.name == "Demo MCP"))
    mcp = result.scalar_one_or_none()
    if mcp:
        return mcp
    mcp = MCPConnection(
        id=uuid.uuid4(),
        name="Demo MCP",
        server_url="wss://demo.mcp.server/socket",
        api_key=None,
        connection_type="websocket",
        is_active=True,
        config={"retries": 3},
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(mcp)
    await session.flush()
    return mcp


async def seed_user_agents(session: AsyncSession, user: User, mcp: MCPConnection) -> List[UserAgent]:
    existing = (await session.execute(select(UserAgent).where(UserAgent.user_id == user.id))).scalars().all()
    if existing:
        return existing

    agents: List[UserAgent] = []
    payloads = [
        {
            "name": "GPT",
            "description": "GPT - Agent",
            "agent_type": "gptagent",
            "agent_config": {"model": "gpt-4o", "temperature": 0.3},
        },
        {
            "name": "Researcher",
            "description": "Deep research agent",
            "agent_type": "researcher",
            "agent_config": {"sources": ["web", "docs"]},
        },
    ]
    for p in payloads:
        ua = UserAgent(
            id=uuid.uuid4(),
            user_id=user.id,
            mcp_connection_id=mcp.id,
            name=p["name"],
            description=p["description"],
            agent_type=p["agent_type"],
            agent_config=p["agent_config"],
            is_active=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(ua)
        agents.append(ua)
    await session.flush()
    return agents


async def seed_menu_and_launchpad(session: AsyncSession, user: User) -> None:
    # Menus
    if not (await session.execute(select(Menu))).scalars().first():
        menus = [
            Menu(id=uuid.uuid4(), name="chats", display_name="Chats", icon="chat", route="/chats", order=1,
                 is_active=True, created_at=datetime.utcnow(), updated_at=datetime.utcnow()),
            Menu(id=uuid.uuid4(), name="agents", display_name="Agents", icon="robot", route="/agents", order=2,
                 is_active=True, created_at=datetime.utcnow(), updated_at=datetime.utcnow()),
            Menu(id=uuid.uuid4(), name="launchpad", display_name="Launchpad", icon="rocket", route="/launchpad", order=3,
                 is_active=True, created_at=datetime.utcnow(), updated_at=datetime.utcnow()),
        ]
        session.add_all(menus)

    # Launchpad apps
    if not (await session.execute(select(LaunchpadApp))).scalars().first():
        apps = [
            LaunchpadApp(id=uuid.uuid4(), app_id="adq-app", title="ADQ App", subtitle="Abu Dhabi Developmental",
                         description="Project management and collaboration.", tags=["Communication", "PM"],
                         logo_src="/images/adq_logo.png", category="trending", url="https://adq.ae",
                         is_active=True, order=1, created_at=datetime.utcnow(), updated_at=datetime.utcnow()),
            LaunchpadApp(id=uuid.uuid4(), app_id="jira-cloud", title="Jira Cloud", subtitle="Atlassian",
                         description="Track and manage projects.", tags=["IT", "PM"],
                         logo_src="/images/jira_logo.png", category="trending", url="https://atlassian.com",
                         is_active=True, order=2, created_at=datetime.utcnow(), updated_at=datetime.utcnow()),
        ]
        session.add_all(apps)
        await session.flush()
        # Pin first app for user
        session.add(UserLaunchpadPin(id=uuid.uuid4(), user_id=user.id, app_id=apps[0].id, is_pinned=True, order=1,
                                     created_at=datetime.utcnow(), updated_at=datetime.utcnow()))

    # Agents catalog (for menu)
    # First, ensure Super Agent exists (required as default)
    super_agent_result = await session.execute(
        select(Agent).where(Agent.name == "Super Agent")
    )
    super_agent = super_agent_result.scalar_one_or_none()
    
    if not super_agent:
        super_agent = Agent(
            public_id=uuid.uuid4(),
            name="Super Agent",
            intro="Super Agent - Default Orchestrator",
            description="Default super agent for orchestration",
            icon="/images/super_agent_logo.png",
            is_enabled=True,
            is_healthy=True,
            agent_id="super-agent",
            title="Super Agent",
            subtitle="Default Orchestrator",
            legacy_tags=["Orchestration", "Default"],
            category="all",
            status="active",
            methods=["Get", "Post"],
            order=0,  # First in order - default agent
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(super_agent)
        await session.flush()
    
    # Create other agents only if table is empty (besides Super Agent)
    existing_count = len((await session.execute(select(Agent))).scalars().all())
    if existing_count <= 1:  # Only Super Agent exists
        other_agents = [
            Agent(
                public_id=uuid.uuid4(),
                name="AiQ Knowledge",
                intro="AI Assistant",
                description="Knowledge assistant",
                icon="/images/aiq_knowledge_logo.png",
                is_enabled=True,
                is_healthy=True,
                agent_id="aiq-knowledge",
                title="AiQ Knowledge",
                subtitle="AI Assistant",
                legacy_tags=["Get", "Post"],
                category="all",
                status="active",
                methods=["Get", "Post"],
                order=1,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            ),
            Agent(
                public_id=uuid.uuid4(),
                name="Airia",
                intro="Research Agent",
                description="Research specialist",
                icon="/images/airia_logo.png",
                is_enabled=True,
                is_healthy=True,
                agent_id="airia",
                title="Airia",
                subtitle="Research Agent",
                legacy_tags=["Research"],
                category="all",
                status="active",
                methods=["Get", "Post"],
                order=2,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            ),
        ]
        session.add_all(other_agents)
        await session.flush()
    
    # Pin Super Agent for user if not already pinned
    existing_pin = (await session.execute(
        select(UserAgentPin).where(
            UserAgentPin.user_id == user.id,
            UserAgentPin.agent_id == super_agent.id
        )
    )).scalar_one_or_none()
    
    if not existing_pin:
        session.add(UserAgentPin(
            id=uuid.uuid4(),
            user_id=user.id,
            agent_id=super_agent.id,
            is_pinned=True,
            order=1,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        ))


async def seed_chats(session: AsyncSession, user: User) -> Optional[Chat]:
    result = await session.execute(select(Chat).where(Chat.user_id == user.id))
    chat = result.scalars().first()  # Get first chat if exists, instead of scalar_one_or_none
    if chat:
        return chat
    chat = Chat(
        id=uuid.uuid4(),
        user_id=user.id,
        title="Demo Session",
        session_id=str(uuid.uuid4()),
        is_active=True,
        chat_metadata={"topic": "onboarding"},
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        last_message_at=datetime.utcnow(),
    )
    session.add(chat)
    await session.flush()
    # messages
    msgs = [
        ChatMessage(
            id=uuid.uuid4(), chat_id=chat.id, message_type="user", role="user",
            content="Hi, can you summarize our launch plan?", message_metadata=None, tokens_used=12,
            processing_time=5, is_edited=False, parent_message_id=None, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ),
        ChatMessage(
            id=uuid.uuid4(), chat_id=chat.id, message_type="assistant", role="assistant",
            content="Sure. The launch plan includes discovery, build, test, and rollout phases.", message_metadata=None,
            tokens_used=48, processing_time=32, is_edited=False, parent_message_id=None, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ),
    ]
    session.add_all(msgs)
    return chat


async def seed_feedback(session: AsyncSession, user: User, chat: Chat) -> None:
    # Attach a thumbs_up to last assistant message
    last_msg = (await session.execute(
        select(ChatMessage).where(ChatMessage.chat_id == chat.id).order_by(desc(ChatMessage.created_at)).limit(1)
    )).scalar_one_or_none()
    if not last_msg:
        return

    existing = (await session.execute(
        select(FeedbackData).where(and_(FeedbackData.user_id == str(user.id), FeedbackData.entity_id == str(last_msg.id)))
    )).scalar_one_or_none()
    if existing:
        return

    fb = FeedbackData(
        feedback_id=uuid.uuid4(),
        user_id=str(user.id),
        user_email=user.email,
        entity_id=str(last_msg.id),
        entity_type=FeedbackEntityType.RESPONSE,
        agent_id=None,
        rating=FeedbackRating.THUMBS_UP,
        comment="Helpful summary",
        metadata_json={"source": "seed"},
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(fb)


async def seed_additional_chats_for_buckets(session: AsyncSession, user: User) -> None:
    """Create demo sessions spanning today, this week, this month, and older buckets."""
    # If there are already > 4 sessions, assume data exists and skip to avoid duplicates
    existing_count = (await session.execute(select(Session).where(Session.user_id == user.id))).scalars().all()
    if len(existing_count) >= 4:
        return

    # Get agent IDs for creating sessions
    agents_result = await session.execute(select(Agent).where(Agent.agent_id.in_(["knowledge", "research", "hr", "procurement"])))
    agents_by_id = {agent.agent_id: agent.id for agent in agents_result.scalars().all()}
    
    # Use a default agent if specific ones don't exist
    default_agent = (await session.execute(select(Agent).limit(1))).scalar_one_or_none()
    if not default_agent:
        return  # No agents available
    
    def get_agent_db_id(agent_id: str) -> int:
        """Get the numeric agent ID from the string identifier."""
        return agents_by_id.get(agent_id, default_agent.id)

    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Monday as start of week
    days_since_monday = now.weekday()
    week_start = today_start - timedelta(days=days_since_monday)
    month_start = today_start.replace(day=1)

    def mk_session(title: str, dt: datetime, agent_id: str, favorite: bool) -> Session:
        return Session(
            id=uuid.uuid4(),
            public_id=uuid.uuid4(),
            user_id=user.id,
            agent_id=get_agent_db_id(agent_id),
            session_name=title,
            status="active",
            session_type="chat",
            session_metadata={
                "agentId": agent_id,
                "agent_id": agent_id,
                "isFavorite": favorite,
                "is_favorite": favorite,
            },
            created_at=dt,
            updated_at=dt,
        )

    sessions: list[Session] = []
    # Today
    sessions.append(mk_session("Today - Budget Review", today_start + timedelta(hours=10), "knowledge", True))

    # This week (but not today): place on Tuesday/Wednesday
    week_mid = week_start + timedelta(days=2, hours=15)
    sessions.append(mk_session("This Week - Vendor Analysis", week_mid, "research", False))
    sessions.append(mk_session("This Week - Hiring Plan", week_mid - timedelta(days=1, hours=2), "hr", False))

    # This month (but not this week): a few days after month_start
    month_mid = month_start + timedelta(days=5, hours=11)
    sessions.append(mk_session("This Month - Risk Register", month_mid, "knowledge", False))
    sessions.append(mk_session("This Month - Policy Update", month_mid + timedelta(days=2, hours=3), "procurement", True))

    # Older (previous month)
    previous_month = (month_start - timedelta(days=2)).replace(hour=9, minute=30, second=0, microsecond=0)
    sessions.append(mk_session("Older - Q2 Postmortem", previous_month, "knowledge", False))
    sessions.append(mk_session("Older - Infra Cost Review", previous_month - timedelta(days=7), "research", False))

    session.add_all(sessions)
    await session.flush()

async def seed_remediation(session: AsyncSession) -> None:
    # Create actions if none
    if (await session.execute(select(RemediationAction))).scalars().first():
        return

    def action(name: str, at: ActionType, service: str, cfg: dict, guards: dict, triggers: list) -> RemediationAction:
        return RemediationAction(
            id=str(uuid.uuid4()),
            name=name,
            description=name,
            action_type=at,
            service=service,
            enabled=True,
            configuration=cfg,
            safety_guardrails=guards,
            trigger_alerts=triggers,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

    a1 = action(
        "Scale Agent Instances",
        ActionType.SCALE_AGENTS,
        "agents",
        {"min_replicas": 1, "max_replicas": 5},
        {"max_executions_per_hour": 5, "requires_dry_run": True},
        ["extreme_latency"],
    )
    a2 = action(
        "Enable Circuit Breaker",
        ActionType.ENABLE_CIRCUIT_BREAKER,
        "api",
        {"error_threshold_percent": 50},
        {"max_executions_per_hour": 3, "requires_dry_run": True},
        ["very_high_error_rate"],
    )
    session.add_all([a1, a2])
    await session.flush()

    r1 = RemediationRule(
        id=str(uuid.uuid4()),
        name="Scale on Extreme Latency",
        description="Scale when latency high",
        action_id=a1.id,
        alert_type="extreme_latency",
        alert_severity="critical",
        enabled=True,
        dry_run_first=True,
        auto_execute=True,
        requires_approval=False,
        condition_config={"min_latency_ms": 5000},
        priority=100,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(r1)


async def main() -> None:
    session = await get_session()
    try:
        user = await seed_users(session)
        mcp = await seed_mcp(session)
        await seed_user_agents(session, user, mcp)
        await seed_menu_and_launchpad(session, user)
        chat = await seed_chats(session, user)
        await seed_additional_chats_for_buckets(session, user)
        await seed_feedback(session, user, chat)
        await seed_remediation(session)
        await session.commit()
        print("✅ Demo data seeded successfully.")
    except Exception as e:
        await session.rollback()
        print(f"❌ Error seeding demo data: {e}")
        raise
    finally:
        # dispose engine
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
