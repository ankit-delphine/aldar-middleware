"""
Script to sync agent status field with is_enabled field for all existing agents.

This script updates the status column in the agents table to match the is_enabled field:
- is_enabled = True  -> status = 'active'
- is_enabled = False -> status = 'inactive'

Usage:
    python -m scripts.sync_agent_status
"""

import asyncio
import logging
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aldar_middleware.database.base import async_session
from aldar_middleware.models.menu import Agent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def sync_agent_status():
    """Sync status field with is_enabled for all existing agents."""
    async with async_session() as db:
        try:
            # Get all agents
            result = await db.execute(
                select(Agent).where(Agent.is_deleted == False)
            )
            agents = result.scalars().all()
            
            logger.info(f"Found {len(agents)} agents to process")
            
            updated_count = 0
            for agent in agents:
                expected_status = "active" if agent.is_enabled else "inactive"
                
                if agent.status != expected_status:
                    logger.info(
                        f"Updating agent '{agent.name}' (ID: {agent.id}): "
                        f"is_enabled={agent.is_enabled}, status='{agent.status}' -> '{expected_status}'"
                    )
                    agent.status = expected_status
                    updated_count += 1
                else:
                    logger.debug(
                        f"Agent '{agent.name}' (ID: {agent.id}) already in sync: "
                        f"is_enabled={agent.is_enabled}, status='{agent.status}'"
                    )
            
            # Commit all changes
            await db.commit()
            
            logger.info(f"✅ Successfully updated {updated_count} agents")
            logger.info(f"✓ {len(agents) - updated_count} agents were already in sync")
            
        except Exception as e:
            await db.rollback()
            logger.error(f"❌ Failed to sync agent status: {str(e)}", exc_info=True)
            raise


async def main():
    """Main entry point."""
    logger.info("Starting agent status synchronization...")
    await sync_agent_status()
    logger.info("Agent status synchronization completed!")


if __name__ == "__main__":
    asyncio.run(main())
