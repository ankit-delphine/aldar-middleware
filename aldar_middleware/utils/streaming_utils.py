"""Utility functions for checking streaming status in Redis."""

from typing import Dict, Optional, Any
from uuid import UUID
from loguru import logger
import redis.asyncio as redis


async def check_streaming_status(
    redis_client: Optional[redis.Redis],
    session_id: UUID,
) -> Optional[Dict[str, Any]]:
    """Check if there's an active stream for the given session in Redis.
    
    The Data team stores streaming information in Redis with the following pattern:
    - Key: stream_id:<uuid>
    - Value: user:<email>, team:<uuid>, session:<session_uuid>, run_id:<uuid>, status:streaming
    - TTL: ~3600 seconds (1 hour)
    
    When the stream completes, the data is stored in DB and the Redis key is deleted.
    
    Args:
        redis_client: Redis client instance (can be None if Redis unavailable)
        session_id: Session UUID to check for active streaming
    
    Returns:
        Dict with streaming info if stream is active, None otherwise:
        {
            "stream_id": str,
            "status": str,
            "user": str,
            "team": str,
            "session": str,
            "run_id": str
        }
    """
    if not redis_client:
        logger.debug("Redis client not available - skipping streaming status check")
        return None
    
    try:
        session_id_str = str(session_id)
        
        # Search for all stream_id:* keys
        pattern = "stream_id:*"
        logger.debug(f"Searching Redis for streaming keys with pattern: {pattern}")
        
        # Use scan_iter for better performance with large key sets
        async for key in redis_client.scan_iter(match=pattern, count=100):
            # Decode key if it's bytes
            if isinstance(key, bytes):
                key = key.decode('utf-8')
            
            # Get the value for this key
            value = await redis_client.get(key)
            if not value:
                continue
            
            # Decode value if it's bytes
            if isinstance(value, bytes):
                value = value.decode('utf-8')
            
            logger.debug(f"Checking Redis key: {key}, value: {value}")
            
            # Parse the value to check if it matches our session_id
            # Expected format: "user:email@example.com, team:uuid, session:uuid, run_id:uuid, status:streaming"
            if f"session:{session_id_str}" in value or f"session: {session_id_str}" in value:
                # Extract stream_id from the key (format: stream_id:<uuid>)
                stream_id = key.split(":", 1)[1] if ":" in key else key
                
                # Parse the value into a dictionary
                streaming_info = {
                    "stream_id": stream_id,
                    "status": "streaming"  # Default status
                }
                
                # Parse key-value pairs from the comma-separated string
                parts = value.split(",")
                for part in parts:
                    part = part.strip()
                    if ":" in part:
                        key_name, key_value = part.split(":", 1)
                        key_name = key_name.strip()
                        key_value = key_value.strip()
                        streaming_info[key_name] = key_value
                
                logger.info(
                    f"✓ Found active stream for session {session_id_str}: "
                    f"stream_id={stream_id}, status={streaming_info.get('status', 'streaming')}"
                )
                
                return streaming_info
        
        logger.debug(f"No active streaming found for session {session_id_str}")
        return None
        
    except redis.RedisError as e:
        logger.warning(f"Redis error while checking streaming status: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error checking streaming status: {e}")
        return None


async def get_ttl_for_stream(
    redis_client: Optional[redis.Redis],
    stream_id: str,
) -> Optional[int]:
    """Get the TTL (time to live) for a stream_id key in Redis.
    
    Args:
        redis_client: Redis client instance
        stream_id: Stream ID to check TTL for
    
    Returns:
        TTL in seconds, or None if key doesn't exist or Redis unavailable
    """
    if not redis_client:
        return None
    
    try:
        key = f"stream_id:{stream_id}"
        ttl = await redis_client.ttl(key)
        
        if ttl == -2:
            # Key doesn't exist
            return None
        elif ttl == -1:
            # Key exists but has no expiration
            return -1
        else:
            # TTL in seconds
            return ttl
    except Exception as e:
        logger.warning(f"Error getting TTL for stream {stream_id}: {e}")
        return None
