"""Database base configuration."""

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from aldar_middleware.settings import settings

# Naming convention for constraints
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=convention)
Base = declarative_base(metadata=metadata)

# Create async engine with proper connection pooling for Celery workers
engine = create_async_engine(
    str(settings.db_url_property),
    echo=settings.db_echo,
    future=True,
    pool_pre_ping=True,  # Verify connections before using them
    pool_recycle=3600,  # Recycle connections after 1 hour
    pool_size=20,  # Number of connections to maintain (increased for high load)
    max_overflow=30,  # Maximum overflow connections (increased for performance tests)
    pool_timeout=30,  # Timeout for getting connection from pool
    connect_args={"command_timeout": 60}  # Query timeout in seconds
)

# Create async session factory
async_session = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    """Get database session."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_async_session() -> AsyncSession:
    """Get async database session (alias for get_db)."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
