"""RunInput model matching ERD specification for external team compatibility."""

from sqlalchemy import Column, ForeignKey, String, Text
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class RunInput(Base):
    """
    INPUT entity matching ERD specification.

    Stores the input provided for a run.
    Uses string input_id as primary key for external team compatibility.
    """

    __tablename__ = "run_inputs"

    # Primary key as string per ERD specification
    input_id = Column(String(255), primary_key=True, nullable=False, index=True)

    # Foreign key
    run_id = Column(
        String(255), ForeignKey("runs.run_id"), nullable=False, unique=True, index=True
    )

    # Input content
    input_content = Column(Text, nullable=True)

    # Relationships
    run = relationship("Run", back_populates="input_data")

    def __repr__(self) -> str:
        return f"<RunInput(input_id={self.input_id}, run_id={self.run_id})>"

