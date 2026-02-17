import asyncio
from sqlalchemy import select, text
from aldar_middleware.database.base import async_session

async def check():
    async with async_session() as db:
        # Check if the attachment exists
        result = await db.execute(text("""
            SELECT id, file_name, blob_url, entity_type, content_type
            FROM attachments 
            WHERE id = 'a5753e28-f845-400a-bffd-fe792e30c074'
        """))
        rows = result.fetchall()
        print('Attachment lookup:')
        for row in rows:
            print(f'  id={row[0]}, file_name={row[1]}, blob_url={row[2]}, entity_type={row[3]}, content_type={row[4]}')
        
        if not rows:
            print('  No attachment found with that ID!')
            
        # Also list recent attachments
        result2 = await db.execute(text("""
            SELECT id, file_name, entity_type, created_at
            FROM attachments 
            ORDER BY created_at DESC
            LIMIT 5
        """))
        rows2 = result2.fetchall()
        print('\nRecent attachments:')
        for row in rows2:
            print(f'  id={row[0]}, file_name={row[1]}, entity_type={row[2]}, created_at={row[3]}')

asyncio.run(check())
