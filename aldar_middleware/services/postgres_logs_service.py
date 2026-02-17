"""Service for writing and querying logs from PostgreSQL tables."""

import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy import select, func, and_, desc, asc, nullslast, String, cast
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from aldar_middleware.models.logs import UserLog, AdminLog
from aldar_middleware.models.user import User


class PostgresLogsService:
    """Service for writing and querying logs from PostgreSQL tables."""

    async def write_user_log(
        self,
        db: AsyncSession,
        log_data: Dict[str, Any]
    ) -> bool:
        """Write a user log event to PostgreSQL user_logs table.
        
        Args:
            db: Database session
            log_data: Dictionary containing the log event data (3.0 format)
            
        Returns:
            bool: True if write successful, False otherwise
        """
        try:
            # Extract fields from log_data
            log_id = log_data.get("id") or str(uuid.uuid4())
            timestamp_str = log_data.get("timestamp") or log_data.get("createdAt")
            
            # Parse timestamp
            if isinstance(timestamp_str, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                except Exception:
                    timestamp = datetime.now(timezone.utc)
            elif isinstance(timestamp_str, datetime):
                timestamp = timestamp_str
            else:
                timestamp = datetime.now(timezone.utc)
            
            action_type = log_data.get("eventType", "")  # eventType in JSON maps to action_type in DB
            user_id = log_data.get("userId")
            email = log_data.get("email")
            correlation_id = log_data.get("correlationId")
            
            # Create UserLog entry
            user_log = UserLog(
                id=log_id,
                timestamp=timestamp,
                created_at=timestamp,
                action_type=action_type,
                user_id=str(user_id) if user_id else None,
                email=email,
                correlation_id=correlation_id,
                log_data=log_data  # Store full JSON data
            )
            
            db.add(user_log)
            await db.commit()
            return True
            
        except Exception as e:
            logger.error(f"Failed to write user log to PostgreSQL: {e}")
            await db.rollback()
            return False

    async def write_admin_log(
        self,
        db: AsyncSession,
        log_data: Dict[str, Any]
    ) -> bool:
        """Write an admin log event to PostgreSQL admin_logs table.
        
        Args:
            db: Database session
            log_data: Dictionary containing the log data
            
        Returns:
            bool: True if write successful, False otherwise
        """
        try:
            # Extract fields from log_data
            log_id = log_data.get("id") or str(uuid.uuid4())
            timestamp_str = log_data.get("timestamp")
            
            # Parse timestamp
            if isinstance(timestamp_str, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                except Exception:
                    timestamp = datetime.now(timezone.utc)
            elif isinstance(timestamp_str, datetime):
                timestamp = timestamp_str
            else:
                timestamp = datetime.now(timezone.utc)
            
            level = log_data.get("level", "INFO")
            action_type = log_data.get("action_type")  # e.g., USERS_LOGS_EXPORTED, KNOWLEDGE_AGENT_UPDATED
            user_id = log_data.get("user_id")
            email = log_data.get("email")
            username = log_data.get("username")
            correlation_id = log_data.get("correlation_id")
            module = log_data.get("module")
            function_name = log_data.get("function")
            message = log_data.get("message")
            
            # Create AdminLog entry
            admin_log = AdminLog(
                id=log_id,
                timestamp=timestamp,
                level=level,
                action_type=action_type,
                user_id=str(user_id) if user_id else None,
                email=email,
                username=username,
                correlation_id=correlation_id,
                module=module,
                function=function_name,
                message=message,
                log_data=log_data  # Store full JSON data
            )
            
            db.add(admin_log)
            await db.commit()
            return True
            
        except Exception as e:
            logger.error(f"Failed to write admin log to PostgreSQL: {e}")
            await db.rollback()
            return False

    async def query_user_logs(
        self,
        db: AsyncSession,
        limit: int = 20,
        offset: int = 0,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        event_type: Optional[str] = None,  # API parameter name (backward compatibility)
        user_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = "DESC",
    ) -> Dict[str, Any]:
        """Query user logs from PostgreSQL user_logs table.
        
        Args:
            db: Database session
            limit: Maximum number of logs to return
            offset: Number of logs to skip
            date_from: Start date filter
            date_to: End date filter
            event_type: Filter by action type (e.g., USER_CONVERSATION_CREATED, USER_MESSAGE_CREATED)
            user_id: Filter by user ID
            correlation_id: Filter by correlation ID
            search: Search across email, user_id, name (via User join), and log_data JSONB fields
            
        Returns:
            Dictionary with 'items' (list of logs) and 'total' (total count)
        """
        try:
            from sqlalchemy import or_
            
            # Check if we need to join with User table for sorting by name or searching
            needs_user_join = False
            if sort_by:
                sort_by_lower = sort_by.lower()
                if sort_by_lower in ["name", "full_name", "fullname"]:
                    needs_user_join = True
            
            # If search is provided, we need to join with User table to search by name
            if search:
                needs_user_join = True
            
            # Build query with optional join
            if needs_user_join:
                # Join with User table to access full_name for sorting/searching
                # User.id is UUID, UserLog.user_id is String, so we need to cast
                query = select(UserLog).outerjoin(
                    User, UserLog.user_id == cast(User.id, String)
                )
                count_query = select(func.count(UserLog.id)).outerjoin(
                    User, UserLog.user_id == cast(User.id, String)
                )
            else:
                query = select(UserLog)
                count_query = select(func.count(UserLog.id))
            
            # Build filters
            conditions = []
            
            if date_from:
                # Frontend sends UTC datetime in ISO 8601 format (e.g., 2025-12-10T18:30:00.000Z)
                # Filter by created_at since that's what's shown in the response as createdAt
                # Ensure datetime is timezone-aware and in UTC for PostgreSQL TIMESTAMP WITH TIME ZONE comparison
                if date_from.tzinfo is None:
                    # If timezone-naive, assume it's UTC and make it timezone-aware
                    date_from = date_from.replace(tzinfo=timezone.utc)
                else:
                    # Convert to UTC if not already
                    date_from = date_from.astimezone(timezone.utc)
                logger.info(f"Filtering user logs: date_from={date_from.isoformat()} (UTC)")
                # Filter by created_at to match what's shown in response (createdAt)
                conditions.append(UserLog.created_at >= date_from)
            if date_to:
                # Frontend sends UTC datetime in ISO 8601 format (e.g., 2025-12-12T18:29:59.999Z)
                # Filter by created_at since that's what's shown in the response as createdAt
                # Ensure datetime is timezone-aware and in UTC for PostgreSQL TIMESTAMP WITH TIME ZONE comparison
                if date_to.tzinfo is None:
                    # If timezone-naive, assume it's UTC and make it timezone-aware
                    date_to = date_to.replace(tzinfo=timezone.utc)
                else:
                    # Convert to UTC if not already
                    date_to = date_to.astimezone(timezone.utc)
                logger.info(f"Filtering user logs: date_to={date_to.isoformat()} (UTC)")
                # Filter by created_at to match what's shown in response (createdAt)
                conditions.append(UserLog.created_at <= date_to)
            if event_type:
                conditions.append(UserLog.action_type == event_type)
            if user_id:
                conditions.append(UserLog.user_id == str(user_id))
            if correlation_id:
                conditions.append(UserLog.correlation_id == correlation_id)
            
            # Add search filter - search across multiple fields
            if search:
                search_pattern = f"%{search}%"
                search_conditions = []
                
                # Search in email column
                search_conditions.append(UserLog.email.ilike(search_pattern))
                
                # Search in action_type (eventType) - allows searching for event types like USER_CONVERSATION_DELETED
                search_conditions.append(UserLog.action_type.ilike(search_pattern))
                
                # Note: We do NOT search in user_id column to prevent matching UUIDs
                # user_id contains UUIDs which can cause false matches (e.g., "04" matching in "408db9ec...")
                
                # Search in User.full_name (requires join)
                if needs_user_join:
                    search_conditions.append(User.full_name.ilike(search_pattern))
                    # Also search in User.email as fallback
                    search_conditions.append(User.email.ilike(search_pattern))
                
                # Search in specific log_data JSONB fields only (excluding agentId, messageId, conversationId, userInput, role, department)
                # Instead of searching the entire log_data, search only in allowed fields
                # This prevents searching in excluded fields like agentId, messageId, conversationId, userInput, role, department
                
                # Search in log_data->>'name' (if exists, using COALESCE to handle NULL)
                search_conditions.append(
                    cast(func.coalesce(UserLog.log_data['name'].astext, ''), String).ilike(search_pattern)
                )
                
                # Search in log_data->>'email' (already searched via UserLog.email, but also check log_data)
                search_conditions.append(
                    cast(func.coalesce(UserLog.log_data['email'].astext, ''), String).ilike(search_pattern)
                )
                
                # Search in log_data->>'eventType' (if exists in JSONB)
                search_conditions.append(
                    cast(func.coalesce(UserLog.log_data['eventType'].astext, ''), String).ilike(search_pattern)
                )
                
                # Search in log_data->'eventPayload'->'agent'->>'agentName' (if exists)
                search_conditions.append(
                    cast(func.coalesce(
                        UserLog.log_data['eventPayload']['agent']['agentName'].astext, ''
                    ), String).ilike(search_pattern)
                )
                
                # Search in log_data->'eventPayload'->>'agentName' (alternative path)
                search_conditions.append(
                    cast(func.coalesce(
                        UserLog.log_data['eventPayload']['agentName'].astext, ''
                    ), String).ilike(search_pattern)
                )
                
                # Search in log_data->'body'->'agent'->>'agentName' (alternative path for body)
                search_conditions.append(
                    cast(func.coalesce(
                        UserLog.log_data['body']['agent']['agentName'].astext, ''
                    ), String).ilike(search_pattern)
                )
                
                # Search in log_data->'body'->>'agentName' (alternative path)
                search_conditions.append(
                    cast(func.coalesce(
                        UserLog.log_data['body']['agentName'].astext, ''
                    ), String).ilike(search_pattern)
                )
                
                # Note: We explicitly exclude searching in:
                # - user_id (UserLog.user_id column - contains UUIDs that cause false matches)
                # - agentId (log_data->>'agentId' or log_data->'eventPayload'->>'agentId')
                # - messageId (log_data->>'messageId' or log_data->'eventPayload'->>'messageId')
                # - conversationId (log_data->>'conversationId' or log_data->'eventPayload'->>'conversationId')
                # - userInput (log_data->>'userInput' or log_data->'eventPayload'->>'userInput')
                # - role (from user info, not in log_data typically)
                # - department (from user info, not in log_data typically)
                
                # Combine all search conditions with OR
                conditions.append(or_(*search_conditions))
            
            if conditions:
                query = query.where(and_(*conditions))
                count_query = count_query.where(and_(*conditions))
            
            # Apply sorting
            order_by_clauses = []
            if sort_by:
                sort_by_lower = sort_by.lower()
                sort_order_upper = (sort_order or "DESC").upper()
                
                # Validate sort_order
                if sort_order_upper not in ["ASC", "DESC"]:
                    sort_order_upper = "DESC"
                
                # Map sort_by to UserLog model fields or User model fields
                if sort_by_lower in ["timestamp", "created_at", "createdat"]:
                    field = UserLog.timestamp
                    if sort_order_upper == "ASC":
                        order_by_clauses.append(nullslast(asc(field)))
                    else:
                        order_by_clauses.append(nullslast(desc(field)))
                elif sort_by_lower in ["event_type", "eventtype", "action_type"]:
                    field = UserLog.action_type
                    if sort_order_upper == "ASC":
                        order_by_clauses.append(nullslast(asc(field)))
                    else:
                        order_by_clauses.append(nullslast(desc(field)))
                    # Add secondary sort by timestamp (DESC for newest first)
                    order_by_clauses.append(nullslast(desc(UserLog.timestamp)))
                elif sort_by_lower == "email":
                    field = UserLog.email
                    if sort_order_upper == "ASC":
                        order_by_clauses.append(nullslast(asc(field)))
                    else:
                        order_by_clauses.append(nullslast(desc(field)))
                    # Add secondary sort by timestamp (DESC for newest first)
                    order_by_clauses.append(nullslast(desc(UserLog.timestamp)))
                elif sort_by_lower in ["name", "full_name", "fullname"]:
                    # Sort by User.full_name (requires join)
                    if needs_user_join:
                        field = User.full_name
                        if sort_order_upper == "ASC":
                            order_by_clauses.append(nullslast(asc(field)))
                        else:
                            order_by_clauses.append(nullslast(desc(field)))
                        # Add secondary sort by timestamp (DESC for newest first)
                        order_by_clauses.append(nullslast(desc(UserLog.timestamp)))
                    else:
                        # Fallback if join wasn't set up (shouldn't happen)
                        order_by_clauses.append(nullslast(desc(UserLog.timestamp)))
                else:
                    # Invalid sort_by, default to timestamp DESC
                    order_by_clauses.append(nullslast(desc(UserLog.timestamp)))
            else:
                # Default sorting by timestamp DESC (latest first)
                order_by_clauses.append(nullslast(desc(UserLog.timestamp)))
            
            query = query.order_by(*order_by_clauses)
            
            # Apply pagination
            query = query.offset(offset).limit(limit)
            
            # Execute queries
            result = await db.execute(query)
            items = result.scalars().all()
            
            count_result = await db.execute(count_query)
            total = count_result.scalar_one()
            
            # Convert to dict format (extract log_data JSONB)
            items_list = []
            for item in items:
                log_dict = item.log_data.copy() if item.log_data else {}
                # Ensure required fields are present
                log_dict["id"] = item.id
                log_dict["timestamp"] = item.timestamp.isoformat() if item.timestamp else None
                log_dict["createdAt"] = item.created_at.isoformat() if item.created_at else None
                log_dict["eventType"] = item.action_type  # action_type in DB maps to eventType in JSON response
                items_list.append(log_dict)
            
            return {
                "items": items_list,
                "total": total
            }
            
        except Exception as e:
            logger.error(f"Failed to query user logs from PostgreSQL: {e}")
            return {"items": [], "total": 0}

    async def query_admin_logs(
        self,
        db: AsyncSession,
        limit: int = 100,
        offset: int = 0,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        level: Optional[str] = None,
        action_type: Optional[str] = None,
        module: Optional[str] = None,
        function: Optional[str] = None,
        email: Optional[str] = None,
        user_id: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = "DESC",
    ) -> Dict[str, Any]:
        """Query admin logs from PostgreSQL admin_logs table.
        
        Args:
            db: Database session
            limit: Maximum number of logs to return
            offset: Number of logs to skip
            date_from: Start date filter
            date_to: End date filter
            level: Filter by log level (INFO, WARNING, ERROR, DEBUG)
            module: Filter by module
            function: Filter by function
            email: Filter by email
            user_id: Filter by user ID
            search: Search in message field
            
        Returns:
            Dictionary with 'items' (list of logs) and 'total' (total count)
        """
        try:
            from sqlalchemy import or_
            
            # Check if we need to join with User table for sorting by name or searching
            needs_user_join = False
            if sort_by:
                sort_by_lower = sort_by.lower()
                if sort_by_lower in ["name", "full_name", "fullname"]:
                    needs_user_join = True
            
            # If search is provided, we need to join with User table to search by name
            if search:
                needs_user_join = True
            
            # Build query with optional join
            if needs_user_join:
                # Join with User table to access full_name for sorting
                # User.id is UUID, AdminLog.user_id is String, so we need to cast
                query = select(AdminLog).outerjoin(
                    User, AdminLog.user_id == cast(User.id, String)
                )
                count_query = select(func.count(AdminLog.id)).outerjoin(
                    User, AdminLog.user_id == cast(User.id, String)
                )
            else:
                query = select(AdminLog)
                count_query = select(func.count(AdminLog.id))
            
            # Build filters
            conditions = []
            
            if date_from:
                # Frontend sends UTC datetime in ISO 8601 format (e.g., 2025-12-10T18:30:00.000Z)
                # Filter by timestamp field (admin logs use timestamp, shown as timestamp in response)
                # Ensure datetime is timezone-aware and in UTC for PostgreSQL TIMESTAMP WITH TIME ZONE comparison
                if date_from.tzinfo is None:
                    # If timezone-naive, assume it's UTC and make it timezone-aware
                    date_from = date_from.replace(tzinfo=timezone.utc)
                else:
                    # Convert to UTC if not already
                    date_from = date_from.astimezone(timezone.utc)
                logger.info(f"Filtering admin logs: date_from={date_from.isoformat()} (UTC)")
                # Filter by timestamp to match what's shown in response
                conditions.append(AdminLog.timestamp >= date_from)
            if date_to:
                # Frontend sends UTC datetime in ISO 8601 format (e.g., 2025-12-12T18:29:59.999Z)
                # Filter by timestamp field (admin logs use timestamp, shown as timestamp in response)
                # Ensure datetime is timezone-aware and in UTC for PostgreSQL TIMESTAMP WITH TIME ZONE comparison
                if date_to.tzinfo is None:
                    # If timezone-naive, assume it's UTC and make it timezone-aware
                    date_to = date_to.replace(tzinfo=timezone.utc)
                else:
                    # Convert to UTC if not already
                    date_to = date_to.astimezone(timezone.utc)
                logger.info(f"Filtering admin logs: date_to={date_to.isoformat()} (UTC)")
                # Filter by timestamp to match what's shown in response
                conditions.append(AdminLog.timestamp <= date_to)
            if level:
                conditions.append(AdminLog.level == level.upper())
            if action_type:
                conditions.append(AdminLog.action_type == action_type)
            if module:
                conditions.append(AdminLog.module == module)
            if function:
                conditions.append(AdminLog.function == function)
            if email:
                conditions.append(AdminLog.email == email)
            if user_id:
                conditions.append(AdminLog.user_id == str(user_id))
            if search:
                # Search in: name, email, username, eventType, message, and eventPayload.name
                search_pattern = f"%{search}%"
                search_conditions = []
                
                logger.info(f"Admin logs search: pattern='{search_pattern}'")
                
                # Search in email column (top-level)
                search_conditions.append(AdminLog.email.ilike(search_pattern))
                
                # Search in username column (top-level)
                search_conditions.append(AdminLog.username.ilike(search_pattern))
                
                # Search in action_type (eventType - top-level)
                search_conditions.append(AdminLog.action_type.ilike(search_pattern))
                
                # Search in message column (top-level)
                search_conditions.append(AdminLog.message.ilike(search_pattern))
                
                # Search in User.full_name (name - top-level, requires join)
                if needs_user_join:
                    search_conditions.append(User.full_name.ilike(search_pattern))
                    # Also search in User.email as fallback
                    search_conditions.append(User.email.ilike(search_pattern))
                
                # Search in log_data->'body'->>'name' (eventPayload.name - nested in eventPayload/body)
                # eventPayload is stored as "body" in log_data JSONB
                # Use bracket notation for reliable nested access (same as user logs)
                search_conditions.append(
                    cast(func.coalesce(AdminLog.log_data['body']['name'].astext, ''), String).ilike(search_pattern)
                )
                
                # Also search in log_data->>'name' (if exists at top level)
                search_conditions.append(
                    cast(func.coalesce(AdminLog.log_data['name'].astext, ''), String).ilike(search_pattern)
                )
                
                # Search in log_data->'body'->>'description' (agent description)
                search_conditions.append(
                    cast(func.coalesce(AdminLog.log_data['body']['description'].astext, ''), String).ilike(search_pattern)
                )
                
                logger.info(f"Added search conditions for pattern: {search_pattern}")
                
                # Combine all search conditions with OR
                conditions.append(or_(*search_conditions))
            
            if conditions:
                query = query.where(and_(*conditions))
                count_query = count_query.where(and_(*conditions))
            
            # Apply sorting
            order_by_clauses = []
            if sort_by:
                sort_by_lower = sort_by.lower()
                sort_order_upper = (sort_order or "DESC").upper()
                
                # Validate sort_order
                if sort_order_upper not in ["ASC", "DESC"]:
                    sort_order_upper = "DESC"
                
                # Map sort_by to AdminLog model fields or User model fields
                if sort_by_lower in ["timestamp", "created_at", "createdat"]:
                    field = AdminLog.timestamp
                    if sort_order_upper == "ASC":
                        order_by_clauses.append(nullslast(asc(field)))
                    else:
                        order_by_clauses.append(nullslast(desc(field)))
                elif sort_by_lower in ["level"]:
                    field = AdminLog.level
                    if sort_order_upper == "ASC":
                        order_by_clauses.append(nullslast(asc(field)))
                    else:
                        order_by_clauses.append(nullslast(desc(field)))
                    # Add secondary sort by timestamp (DESC for newest first)
                    order_by_clauses.append(nullslast(desc(AdminLog.timestamp)))
                elif sort_by_lower in ["action_type", "actiontype", "event_type", "eventtype"]:
                    field = AdminLog.action_type
                    if sort_order_upper == "ASC":
                        order_by_clauses.append(nullslast(asc(field)))
                    else:
                        order_by_clauses.append(nullslast(desc(field)))
                    # Add secondary sort by timestamp (DESC for newest first)
                    order_by_clauses.append(nullslast(desc(AdminLog.timestamp)))
                elif sort_by_lower == "email":
                    field = AdminLog.email
                    if sort_order_upper == "ASC":
                        order_by_clauses.append(nullslast(asc(field)))
                    else:
                        order_by_clauses.append(nullslast(desc(field)))
                    # Add secondary sort by timestamp (DESC for newest first)
                    order_by_clauses.append(nullslast(desc(AdminLog.timestamp)))
                elif sort_by_lower in ["username", "user_name"]:
                    field = AdminLog.username
                    if sort_order_upper == "ASC":
                        order_by_clauses.append(nullslast(asc(field)))
                    else:
                        order_by_clauses.append(nullslast(desc(field)))
                    # Add secondary sort by timestamp (DESC for newest first)
                    order_by_clauses.append(nullslast(desc(AdminLog.timestamp)))
                elif sort_by_lower in ["name", "full_name", "fullname"]:
                    # Sort by User.full_name (requires join)
                    if needs_user_join:
                        field = User.full_name
                        if sort_order_upper == "ASC":
                            order_by_clauses.append(nullslast(asc(field)))
                        else:
                            order_by_clauses.append(nullslast(desc(field)))
                        # Add secondary sort by timestamp (DESC for newest first)
                        order_by_clauses.append(nullslast(desc(AdminLog.timestamp)))
                    else:
                        # Fallback if join wasn't set up (shouldn't happen)
                        order_by_clauses.append(nullslast(desc(AdminLog.timestamp)))
                elif sort_by_lower == "module":
                    field = AdminLog.module
                    if sort_order_upper == "ASC":
                        order_by_clauses.append(nullslast(asc(field)))
                    else:
                        order_by_clauses.append(nullslast(desc(field)))
                    # Add secondary sort by timestamp (DESC for newest first)
                    order_by_clauses.append(nullslast(desc(AdminLog.timestamp)))
                else:
                    # Invalid sort_by, default to timestamp DESC
                    order_by_clauses.append(nullslast(desc(AdminLog.timestamp)))
            else:
                # Default sorting by timestamp DESC (latest first)
                order_by_clauses.append(nullslast(desc(AdminLog.timestamp)))
            
            query = query.order_by(*order_by_clauses)
            
            # Apply pagination
            query = query.offset(offset).limit(limit)
            
            # Execute queries
            result = await db.execute(query)
            items = result.scalars().all()
            
            count_result = await db.execute(count_query)
            total = count_result.scalar_one()
            
            # Convert to LogEntryResponse format
            items_list = []
            for item in items:
                log_dict = item.log_data.copy() if item.log_data else {}
                # Override with actual column values if they exist
                log_dict["id"] = item.id
                log_dict["timestamp"] = item.timestamp.isoformat() if item.timestamp else None
                log_dict["level"] = item.level
                log_dict["action_type"] = item.action_type
                log_dict["user_id"] = item.user_id
                log_dict["email"] = item.email
                log_dict["username"] = item.username
                log_dict["correlation_id"] = item.correlation_id
                log_dict["module"] = item.module
                log_dict["function"] = item.function
                log_dict["message"] = item.message
                items_list.append(log_dict)
            
            return {
                "items": items_list,
                "total": total
            }
            
        except Exception as e:
            logger.error(f"Failed to query admin logs from PostgreSQL: {e}", exc_info=True)
            return {"items": [], "total": 0}


# Global instance
postgres_logs_service = PostgresLogsService()

