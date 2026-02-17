#!/usr/bin/env python3
"""Show all enabled agents."""

import asyncio
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from aldar_middleware.settings import settings
from aldar_middleware.models import Agent


async def show_agents():
    engine = create_async_engine(str(settings.db_url_property), echo=False)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with SessionLocal() as session:
        # All enabled agents
        query = select(Agent).where(
            and_(
                Agent.is_enabled == True,
                or_(Agent.status.ilike('active'), Agent.status.is_(None))
            )
        )
        result = await session.execute(query)
        agents = result.scalars().all()
        
        print(f'Total enabled agents: {len(agents)}\n')
        for a in agents:
            print(f'- {a.name}')
            print(f'  ID: {a.id}')
            print(f'  Category: {a.category}')
            print(f'  Status: {a.status}')
            print(f'  Is User Agent: {"Yes" if a.category == "user_agents" else "No"}')
            print()

asyncio.run(show_agents())

