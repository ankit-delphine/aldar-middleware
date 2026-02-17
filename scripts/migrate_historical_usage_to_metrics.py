"""
Migration Script: Copy Historical Usage Data to agent_usage_metrics
====================================================================

This script migrates historical agent usage data from the messages table 
to the agent_usage_metrics table.

Purpose:
- Ensures historical data is preserved in the new tracking system
- Creates agent_usage_metrics entries for all historical user messages
- Prevents data loss during the transition to new analytics method

Usage:
    python -m scripts.migrate_historical_usage_to_metrics
    
    Options:
    --dry-run          Show what would be migrated without making changes
    --batch-size N     Process N messages at a time (default: 1000)
    --date-from DATE   Only migrate messages from this date onwards (ISO format)

Example:
    python -m scripts.migrate_historical_usage_to_metrics --dry-run
    python -m scripts.migrate_historical_usage_to_metrics --batch-size 500
"""

import asyncio
import sys
import uuid
from datetime import datetime
from typing import Optional
import argparse

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

# Add parent directory to path for imports
sys.path.insert(0, str(__file__).rsplit('\\', 2)[0])

from aldar_middleware.database.base import engine
from aldar_middleware.models.messages import Message
from aldar_middleware.models.token_usage import TokenUsage


async def check_existing_metrics(db: AsyncSession) -> dict:
    """Check how many entries already exist in agent_usage_metrics."""
    count_query = select(func.count(TokenUsage.id))
    result = await db.execute(count_query)
    total_count = result.scalar()
    
    # Count by agent_id
    agent_count_query = select(
        TokenUsage.agent_id,
        func.count(TokenUsage.id).label("count")
    ).group_by(TokenUsage.agent_id)
    agent_result = await db.execute(agent_count_query)
    agent_counts = {row.agent_id: row.count for row in agent_result.all()}
    
    return {
        "total": total_count,
        "by_agent": agent_counts
    }


async def get_historical_messages(
    db: AsyncSession, 
    date_from: Optional[datetime] = None,
    batch_size: int = 1000,
    offset: int = 0
) -> list:
    """Get historical user messages that need to be migrated."""
    filters = [
        Message.deleted_at.is_(None),
        Message.role == 'user',
        Message.agent_id.isnot(None)  # Only messages with agent assigned
    ]
    
    if date_from:
        filters.append(Message.created_at >= date_from)
    
    query = select(Message).where(and_(*filters)).order_by(Message.created_at.asc()).limit(batch_size).offset(offset)
    result = await db.execute(query)
    return result.scalars().all()


async def create_usage_metric_from_message(db: AsyncSession, message: Message, dry_run: bool = False):
    """Create a usage metric entry from a message."""
    usage_metric = TokenUsage(
        id=uuid.uuid4(),
        public_id=uuid.uuid4(),
        user_id=message.user_id,
        session_id=message.session_id,
        message_id=message.id,
        agent_id=message.agent_id,
        agent_run_id=None,  # Not tracked in messages table
        input_tokens=0,  # Not available in messages table
        output_tokens=0,
        total_tokens=0,
        cost=0,
        currency="USD",
        model_name="historical_migration",  # Marker for migrated data
        total_request=1,  # Each message = 1 request
        total_error=0,
        average_response_time=None,
        success_time=None,
        created_at=message.created_at  # Preserve original timestamp
    )
    
    if not dry_run:
        db.add(usage_metric)
    
    return usage_metric


async def migrate_historical_data(
    dry_run: bool = False,
    batch_size: int = 1000,
    date_from: Optional[datetime] = None
):
    """Main migration function."""
    print("=" * 80)
    print("Historical Usage Data Migration")
    print("=" * 80)
    print(f"Mode: {'DRY RUN (no changes will be made)' if dry_run else 'LIVE MIGRATION'}")
    print(f"Batch size: {batch_size}")
    if date_from:
        print(f"Migrating from: {date_from}")
    print()
    
    # Create async session
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as db:
        # Check existing data
        print("Checking existing agent_usage_metrics data...")
        existing = await check_existing_metrics(db)
        print(f"✓ Found {existing['total']} existing entries in agent_usage_metrics")
        if existing['by_agent']:
            print(f"✓ Covering {len(existing['by_agent'])} agents")
        print()
        
        # Check messages to migrate
        print("Checking messages table for historical data...")
        total_messages_query = select(func.count(Message.id)).where(
            and_(
                Message.deleted_at.is_(None),
                Message.role == 'user',
                Message.agent_id.isnot(None)
            )
        )
        if date_from:
            total_messages_query = total_messages_query.where(Message.created_at >= date_from)
        
        result = await db.execute(total_messages_query)
        total_messages = result.scalar()
        print(f"✓ Found {total_messages} user messages to migrate")
        print()
        
        if total_messages == 0:
            print("No messages to migrate. Exiting.")
            return
        
        # Ask for confirmation if not dry run
        if not dry_run:
            print("⚠️  WARNING: This will create new entries in agent_usage_metrics table!")
            response = input("Continue with migration? (yes/no): ").strip().lower()
            if response != 'yes':
                print("Migration cancelled.")
                return
            print()
        
        # Process in batches
        migrated = 0
        offset = 0
        errors = 0
        
        print(f"Starting migration of {total_messages} messages...")
        print("-" * 80)
        
        while offset < total_messages:
            messages = await get_historical_messages(db, date_from, batch_size, offset)
            
            if not messages:
                break
            
            batch_migrated = 0
            for message in messages:
                try:
                    await create_usage_metric_from_message(db, message, dry_run)
                    batch_migrated += 1
                except Exception as e:
                    errors += 1
                    print(f"✗ Error migrating message {message.id}: {str(e)}")
            
            if not dry_run:
                await db.commit()
            
            migrated += batch_migrated
            offset += batch_size
            
            # Progress update
            progress = min(100, int((migrated / total_messages) * 100))
            print(f"Progress: {migrated}/{total_messages} ({progress}%) - Batch: {batch_migrated} migrated")
        
        print("-" * 80)
        print()
        print("Migration Summary")
        print("=" * 80)
        print(f"Total messages found: {total_messages}")
        print(f"Successfully migrated: {migrated}")
        if errors > 0:
            print(f"Errors: {errors}")
        print()
        
        if dry_run:
            print("✓ DRY RUN COMPLETE - No changes were made")
        else:
            print("✓ MIGRATION COMPLETE")
            
            # Verify new data
            print()
            print("Verifying migration...")
            new_stats = await check_existing_metrics(db)
            print(f"✓ Total entries in agent_usage_metrics: {new_stats['total']}")
            print(f"✓ Agents covered: {len(new_stats['by_agent'])}")
        
        print("=" * 80)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Migrate historical usage data from messages to agent_usage_metrics"
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be migrated without making changes'
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=1000,
        help='Number of messages to process at a time (default: 1000)'
    )
    parser.add_argument(
        '--date-from',
        type=str,
        help='Only migrate messages from this date onwards (ISO format: YYYY-MM-DD)'
    )
    
    args = parser.parse_args()
    
    date_from = None
    if args.date_from:
        try:
            date_from = datetime.fromisoformat(args.date_from)
        except ValueError:
            print(f"Error: Invalid date format '{args.date_from}'. Use YYYY-MM-DD format.")
            sys.exit(1)
    
    try:
        asyncio.run(migrate_historical_data(
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            date_from=date_from
        ))
    except KeyboardInterrupt:
        print("\n\nMigration interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nError during migration: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
