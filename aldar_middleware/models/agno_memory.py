"""Agno memory model - represents the agno_memories table created by another team."""

from sqlalchemy import Column, String, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from aldar_middleware.database.base import Base


class AgnoMemory(Base):
    """
    Agno memory model.
    
    Note: This table is created and managed by another team.
    We only read from and delete records in this table.
    """
    __tablename__ = "agno_memories"
    
    memory_id = Column(String, primary_key=True)  # VARCHAR storing UUID strings
    memory = Column(JSONB, nullable=False)  # JSONB containing text and confidence
    feedback = Column(Text)
    input = Column(Text)
    agent_id = Column(String)  # VARCHAR storing UUID strings
    team_id = Column(String)  # VARCHAR storing UUID strings
    user_id = Column(String, nullable=False, index=True)  # Contains user email
    topics = Column(JSONB)  # JSONB array of topics like ["occupation", "department"]
    created_at = Column(Integer)  # Unix timestamp (int8)
    updated_at = Column(Integer)  # Unix timestamp (int8)
