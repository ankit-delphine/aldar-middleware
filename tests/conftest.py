"""Test configuration and fixtures."""

import os

# Ensure tests run with the lightweight configuration before importing application modules.
# Disable optional integrations that spawn background threads or call external Azure services.
os.environ.setdefault('ENVIRONMENT', 'testing')
os.environ.setdefault('ALDAR_APP_INSIGHTS_ENABLED', 'false')
os.environ.setdefault('ALDAR_DISTRIBUTED_TRACING_ENABLED', 'false')
os.environ.setdefault('ALDAR_COSMOS_LOGGING_ENABLED', 'false')
os.environ.setdefault('ALDAR_AZURE_METRICS_INGESTION_ENABLED', 'false')
os.environ.setdefault('ALDAR_AZURE_PROMETHEUS_ENABLED', 'false')
os.environ.setdefault('ALDAR_AZURE_GRAFANA_ENABLED', 'false')
os.environ.setdefault('ALDAR_PROMETHEUS_ENABLED', 'false')

import pytest
import asyncio
from typing import AsyncGenerator
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from aldar_middleware.application import get_app
from aldar_middleware.database.base import Base, get_db
from aldar_middleware.settings import settings


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def test_db():
    """
    Create test database engine.
    
    Uses the main 'aiq' database instead of aiq_test for RBAC tests.
    This allows tests to work with actual data and existing migrations.
    """
    # Use the main database (don't replace with aiq_test)
    test_engine = create_async_engine(
        settings.db_url,  # Use main database (aiq)
        echo=False,
        future=True,
    )
    
    # Don't create/drop tables - use existing database with migrations
    # This allows RBAC tests to work with real data
    
    yield test_engine
    
    # Clean up - just dispose the engine, don't drop tables
    await test_engine.dispose()


@pytest.fixture
async def db_session(test_db) -> AsyncGenerator[AsyncSession, None]:
    """Create database session for testing."""
    async_session = sessionmaker(
        test_db,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    async with async_session() as session:
        yield session


@pytest.fixture
def client(db_session):
    """Create test client."""
    def override_get_db():
        return db_session
    
    app = get_app()
    app.dependency_overrides[get_db] = override_get_db
    
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def test_user():
    """Create test user data."""
    return {
        "email": "test@example.com",
        "username": "testuser",
        "first_name": "Test",
        "last_name": "User"
    }
