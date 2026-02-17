"""Script to drop admin_config table and reset migration."""
import asyncio
from sqlalchemy import text

async def main():
    from aldar_middleware.database.session import AsyncSessionLocal
    
    async with AsyncSessionLocal() as db:
        await db.execute(text("DROP TABLE IF EXISTS admin_config CASCADE"))
        await db.execute(text("DELETE FROM alembic_version WHERE version_num='0022'"))
        await db.commit()
        print("Done - admin_config dropped and migration reset")

if __name__ == "__main__":
    asyncio.run(main())
