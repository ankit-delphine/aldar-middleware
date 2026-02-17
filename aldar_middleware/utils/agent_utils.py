"""Agent utility functions."""

import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, and_

from aldar_middleware.models.menu import Agent
from aldar_middleware.models.agent_tags import AgentTag

logger = logging.getLogger(__name__)


async def determine_agent_type(agent_record: Optional[Agent], db: AsyncSession) -> Optional[str]:
    """Determine agent type from agent record.
    
    Args:
        agent_record: The Agent model instance
        db: Database session for querying agent tags
        
    Returns:
        Agent type string (e.g., "Enterprise Agent", "Knowledge Agent") or None
    """
    if not agent_record:
        return None
    
    # Check if agent has knowledge sources - indicates Enterprise/Knowledge Agent
    if agent_record.knowledge_sources:
        # Check if it's a list/array with items
        if isinstance(agent_record.knowledge_sources, list) and len(agent_record.knowledge_sources) > 0:
            return "Enterprise Agent"
        elif isinstance(agent_record.knowledge_sources, dict) and agent_record.knowledge_sources:
            return "Enterprise Agent"
    
    # For "Super Agent", default to "Enterprise Agent"
    if agent_record.name == "Super Agent":
        return "Enterprise Agent"
    
    # Try to get agent type from tags
    try:
        result = await db.execute(
            select(AgentTag.tag)
            .where(
                AgentTag.agent_id == agent_record.id,
                AgentTag.tag_type == "type",
                AgentTag.is_active == True
            )
            .limit(1)
        )
        tag_result = result.scalar_one_or_none()
        if tag_result:
            return tag_result
    except Exception:
        pass
    
    # Default to "Enterprise Agent" if no specific type found
    return "Enterprise Agent"


async def set_agent_type(db: AsyncSession, agent_id: int, agent_type: Optional[str]) -> None:
    """Set agent type by creating/updating an AgentTag with tag_type='type'.
    
    Args:
        db: Database session
        agent_id: Agent ID (BigInteger)
        agent_type: Agent type string (e.g., "Enterprise Agent", "Knowledge Agent", "Creative Agent")
                   If None, removes the type tag
    """
    try:
        # Delete existing type tag
        await db.execute(
            delete(AgentTag).where(
                and_(
                    AgentTag.agent_id == agent_id,
                    AgentTag.tag_type == "type"
                )
            )
        )
        
        # Create new type tag if agent_type is provided
        if agent_type:
            tag = AgentTag(
                agent_id=agent_id,
                tag=agent_type,
                tag_type="type",
                is_active=True
            )
            db.add(tag)
            await db.flush()
    except Exception as e:
        logger.error(f"Error setting agent type: {str(e)}")
        raise


async def set_agent_type(db: AsyncSession, agent_id: int, agent_type: str) -> None:
    """Set agent type by creating/updating an AgentTag with tag_type='type'.
    
    Args:
        db: Database session
        agent_id: Agent ID (BigInteger)
        agent_type: Agent type string (e.g., "Enterprise Agent", "Knowledge Agent", "Creative Agent")
    """
    from sqlalchemy import delete, and_
    
    try:
        # Delete existing type tag
        await db.execute(
            delete(AgentTag).where(
                and_(
                    AgentTag.agent_id == agent_id,
                    AgentTag.tag_type == "type"
                )
            )
        )
        
        # Create new type tag
        tag = AgentTag(
            agent_id=agent_id,
            tag=agent_type,
            tag_type="type",
            is_active=True
        )
        db.add(tag)
        await db.flush()
    except Exception as e:
        logger.error(f"Error setting agent type: {str(e)}")
        raise
