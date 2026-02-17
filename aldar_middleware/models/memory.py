"""Memory model matching ERD specification for external team compatibility."""

from sqlalchemy import Column, String, Text
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class Memory(Base):
    """
    MEMORY entity matching ERD specification.

    Stores memory content associated with a session.
    Uses string memory_id as primary key for external team compatibility.
    """

    __tablename__ = "memories"

    # Primary key as string per ERD specification
    memory_id = Column(String(255), primary_key=True, nullable=False, index=True)

    # Note: session_id stores Session.public_id as string (UUID converted to string) for ERD compatibility
    session_id = Column(String(255), nullable=False, index=True)

    # Memory content
    memory_content = Column(Text, nullable=True)
    memory_type = Column(String(50), nullable=True)

    # Relationships
    # Note: session relationship uses public_id converted to string
    session = relationship(
        "Session",
        foreign_keys=[session_id],
        primaryjoin="Session.public_id.cast(String) == Memory.session_id",
        back_populates="memories",
    )

    def __repr__(self) -> str:
        return (
            f"<Memory(memory_id={self.memory_id}, session_id={self.session_id}, "
            f"memory_type={self.memory_type})>"
        )

