"""Timezone utilities for handling UTC storage and user timezone conversion."""

from datetime import datetime, timezone
from typing import Optional


def utcnow_naive() -> datetime:
    """
    Get current UTC time as a naive datetime.
    
    This is used for storing in TIMESTAMP WITHOUT TIME ZONE columns.
    The datetime is UTC, but stored without timezone info.
    
    Returns:
        datetime: Current UTC time as naive datetime
    """
    return datetime.utcnow()


def naive_to_utc_aware(naive_dt: datetime) -> datetime:
    """
    Convert a naive datetime (assumed to be UTC) to timezone-aware UTC datetime.
    
    This is used when reading from database - we assume all naive datetimes
    stored in TIMESTAMP WITHOUT TIME ZONE columns are in UTC.
    
    Args:
        naive_dt: Naive datetime from database (assumed to be UTC)
        
    Returns:
        datetime: Timezone-aware UTC datetime
    """
    if naive_dt.tzinfo is None:
        return naive_dt.replace(tzinfo=timezone.utc)
    return naive_dt


def to_user_timezone(utc_dt: datetime, user_tz: Optional[str] = None) -> datetime:
    """
    Convert UTC datetime to user's local timezone.
    
    Args:
        utc_dt: UTC datetime (aware or naive, if naive assumed UTC)
        user_tz: User's timezone (e.g., 'Asia/Kolkata', 'America/New_York', 'Asia/Dubai')
                 If None, returns UTC
        
    Returns:
        datetime: Datetime in user's timezone (or UTC if no timezone specified)
    """
    from zoneinfo import ZoneInfo
    
    # Ensure we have UTC-aware datetime
    if utc_dt.tzinfo is None:
        utc_dt = naive_to_utc_aware(utc_dt)
    
    if user_tz:
        try:
            user_tz_obj = ZoneInfo(user_tz)
            return utc_dt.astimezone(user_tz_obj)
        except Exception:
            # If timezone is invalid, return UTC
            return utc_dt
    
    return utc_dt


def format_datetime_for_display(
    dt: datetime,
    user_tz: Optional[str] = None,
    include_tz: bool = True
) -> str:
    """
    Format datetime for display, optionally converting to user's timezone.
    
    Args:
        dt: Datetime (aware or naive, if naive assumed UTC)
        user_tz: User's timezone (e.g., 'Asia/Kolkata', 'America/New_York')
        include_tz: Whether to include timezone info in output
        
    Returns:
        str: ISO formatted datetime string
    """
    # Convert to user timezone if specified
    if user_tz:
        dt = to_user_timezone(dt, user_tz)
    elif dt.tzinfo is None:
        # If naive, assume UTC and make it aware
        dt = naive_to_utc_aware(dt)
    
    if include_tz:
        return dt.isoformat()
    else:
        # Return without timezone info
        return dt.replace(tzinfo=None).isoformat()

