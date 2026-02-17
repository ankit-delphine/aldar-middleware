"""RunMetrics model matching ERD specification for external team compatibility."""

from sqlalchemy import Column, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from aldar_middleware.database.base import Base


class RunMetrics(Base):
    """
    METRICS entity matching ERD specification.

    Stores performance metrics for a run.
    Uses string metrics_id as primary key for external team compatibility.
    """

    __tablename__ = "run_metrics"

    # Primary key as string per ERD specification
    metrics_id = Column(String(255), primary_key=True, nullable=False, index=True)

    # Foreign key
    run_id = Column(
        String(255), ForeignKey("runs.run_id"), nullable=False, unique=True, index=True
    )

    # Token metrics
    input_tokens = Column(Integer, nullable=True, default=0)
    output_tokens = Column(Integer, nullable=True, default=0)
    total_tokens = Column(Integer, nullable=True, default=0)

    # Performance metrics
    time_to_first_token = Column(Float, nullable=True)  # Time in seconds
    duration = Column(Float, nullable=True)  # Duration in seconds

    # Relationships
    run = relationship("Run", back_populates="metrics")

    def __repr__(self) -> str:
        return (
            f"<RunMetrics(metrics_id={self.metrics_id}, run_id={self.run_id}, "
            f"total_tokens={self.total_tokens})>"
        )

