#!/usr/bin/env python
"""Run database migrations."""

import asyncio
from alembic.config import Config
from alembic import command

async def run_migrations():
    """Run migrations asynchronously."""
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    print("✅ Migration completed successfully!")

if __name__ == "__main__":
    asyncio.run(run_migrations())