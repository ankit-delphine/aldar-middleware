"""Feedback system service."""

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import and_, func, or_, select, asc, desc, nullslast, nullsfirst, BigInteger, String, cast
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aldar_middleware.models.feedback import (
    FeedbackData,
    FeedbackFile,
    FeedbackEntityType,
    FeedbackRating,
)
from aldar_middleware.models.user import User
from aldar_middleware.settings.context import get_correlation_id
from aldar_middleware.settings import settings

logger = logging.getLogger(__name__)


class FeedbackService:
    """Service for managing feedback operations."""

    def __init__(self, db: AsyncSession) -> None:
        """Initialize feedback service.
        
        Args:
            db: Database session
        """
        self.db = db
        self.retention_days = settings.feedback_soft_delete_retention_days

    async def create_feedback(
        self,
        user_id: str,
        user_email: Optional[str],
        entity_id: str,
        entity_type: FeedbackEntityType,
        rating: FeedbackRating,
        comment: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        correlation_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> FeedbackData:
        """
        Create a new feedback entry.

        Args:
            user_id: User identifier (SSO)
            user_email: Optional user email
            entity_id: ID of entity being rated
            entity_type: Type of entity
            rating: Feedback rating
            comment: Optional feedback comment
            agent_id: Optional agent identifier
            metadata: Optional additional metadata
            correlation_id: Correlation ID for distributed tracing

        Returns:
            Created FeedbackData object

        Raises:
            Exception: If database operation fails
        """
        correlation_id = correlation_id or get_correlation_id()

        try:
            feedback = FeedbackData(
                user_id=user_id,
                user_email=user_email,
                entity_id=entity_id,
                entity_type=entity_type,
                rating=rating,
                comment=comment,
                agent_id=agent_id,
                metadata_json=metadata or {},
                correlation_id=correlation_id,
                session_id=session_id,
            )

            self.db.add(feedback)
            await self.db.flush()

            logger.info(
                f"Feedback created",
                extra={
                    "correlation_id": correlation_id,
                    "feedback_id": str(feedback.feedback_id),
                    "user_id": user_id,
                    "entity_type": entity_type.value,
                    "rating": rating.value,
                },
            )

            return feedback

        except Exception as e:
            logger.error(
                f"Failed to create feedback: {str(e)}",
                extra={"correlation_id": correlation_id},
                exc_info=True,
            )
            raise

    async def add_file_to_feedback(
        self,
        feedback_id: UUID,
        file_name: str,
        file_url: str,
        blob_name: str,
        file_size: int,
        content_type: Optional[str] = None,
    ) -> FeedbackFile:
        """
        Add a file attachment to feedback.

        Args:
            feedback_id: Feedback ID
            file_name: Original file name
            file_url: Full file URL (with SAS token)
            blob_name: Azure blob path
            file_size: File size in bytes
            content_type: MIME type

        Returns:
            Created FeedbackFile object
        """
        correlation_id = get_correlation_id()

        try:
            file = FeedbackFile(
                feedback_id=feedback_id,
                file_name=file_name,
                file_url=file_url,
                blob_name=blob_name,
                file_size=file_size,
                content_type=content_type,
            )

            self.db.add(file)
            await self.db.flush()

            logger.info(
                f"File added to feedback",
                extra={
                    "correlation_id": correlation_id,
                    "feedback_id": str(feedback_id),
                    "file_id": str(file.file_id),
                    "file_name": file_name,
                    "file_size": file_size,
                },
            )

            return file

        except Exception as e:
            logger.error(
                f"Failed to add file to feedback: {str(e)}",
                extra={
                    "correlation_id": correlation_id,
                    "feedback_id": str(feedback_id),
                },
                exc_info=True,
            )
            raise

    async def get_feedback(
        self, feedback_id: UUID, user_id: Optional[str] = None
    ) -> Optional[FeedbackData]:
        """
        Retrieve a specific feedback entry.

        Args:
            feedback_id: Feedback ID
            user_id: Optional user ID for authorization (only show if owner or None for admins)

        Returns:
            FeedbackData object or None if not found
        """
        correlation_id = get_correlation_id()

        try:
            query = select(FeedbackData).where(
                and_(
                    FeedbackData.feedback_id == feedback_id,
                    FeedbackData.deleted_at.is_(None),
                )
            )

            # If user_id provided, filter to user's own feedback
            if user_id:
                query = query.where(FeedbackData.user_id == user_id)

            query = query.options(selectinload(FeedbackData.files))

            result = await self.db.execute(query)
            feedback = result.scalars().first()

            logger.info(
                f"Retrieved feedback",
                extra={
                    "correlation_id": correlation_id,
                    "feedback_id": str(feedback_id),
                    "found": feedback is not None,
                },
            )

            return feedback

        except Exception as e:
            logger.error(
                f"Failed to retrieve feedback: {str(e)}",
                extra={
                    "correlation_id": correlation_id,
                    "feedback_id": str(feedback_id),
                },
                exc_info=True,
            )
            raise

    async def list_feedback(
        self,
        user_id: Optional[str] = None,
        entity_type: Optional[FeedbackEntityType] = None,
        entity_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        rating: Optional[FeedbackRating] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
        exclude_user_id: bool = False,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = "DESC",
    ) -> Tuple[List[FeedbackData], int]:
        """
        List feedback with optional filters.

        Args:
            user_id: Filter by user ID
            entity_type: Filter by entity type
            entity_id: Filter by entity ID
            agent_id: Filter by agent ID
            rating: Filter by rating
            date_from: Filter from date
            date_to: Filter to date
            page: Page number (1-based)
            limit: Items per page
            exclude_user_id: If True, return feedback for admin (user_id ignored)

        Returns:
            Tuple of (feedback_list, total_count)
        """
        correlation_id = get_correlation_id()

        try:
            query = select(FeedbackData).where(FeedbackData.deleted_at.is_(None))

            # Apply filters
            if user_id and not exclude_user_id:
                query = query.where(FeedbackData.user_id == user_id)

            if entity_type:
                query = query.where(FeedbackData.entity_type == entity_type)

            if entity_id:
                query = query.where(FeedbackData.entity_id == entity_id)

            if agent_id:
                query = query.where(FeedbackData.agent_id == agent_id)

            if rating:
                query = query.where(FeedbackData.rating == rating)

            if date_from:
                # Use full datetime comparison to respect time ranges
                # Convert to UTC naive datetime for comparison
                if date_from.tzinfo is not None:
                    date_from = date_from.astimezone(timezone.utc).replace(tzinfo=None)
                query = query.where(FeedbackData.created_at >= date_from)

            if date_to:
                # Use full datetime comparison to respect time ranges
                # Convert to UTC naive datetime for comparison
                if date_to.tzinfo is not None:
                    date_to = date_to.astimezone(timezone.utc).replace(tzinfo=None)
                query = query.where(FeedbackData.created_at <= date_to)

            # Initialize join flags
            needs_user_join = sort_by and sort_by.lower() == "user_full_name"
            needs_user_join_for_search = False
            needs_agent_join_for_search = False

            # Add search filter - search across multiple fields
            if search:
                search_term = f"%{search}%"
                search_lower = search.lower()
                
                logger.debug(f"Feedback search initiated for: '{search}'")
                
                # Build search conditions for fields directly in FeedbackData
                search_conditions = [
                    FeedbackData.user_id.ilike(search_term),
                    FeedbackData.user_email.ilike(search_term) if FeedbackData.user_email else False,
                    FeedbackData.session_id.ilike(search_term) if FeedbackData.session_id else False,
                    FeedbackData.comment.ilike(search_term) if FeedbackData.comment else False,
                ]
                
                # Search in metadata_json.agent.agent_name using PostgreSQL JSONB operators
                # Cast JSON to JSONB first, then use -> operator for nested access and ->> for text extraction
                # Use coalesce to handle NULL values (when agent or agent_name doesn't exist)
                metadata_jsonb = cast(FeedbackData.metadata_json, JSONB)
                # Use -> to get 'agent' object, then -> to get 'agent_name', then ->> to get text
                agent_obj = metadata_jsonb.op('->')('agent')
                agent_name_text = agent_obj.op('->>')('agent_name')
                metadata_agent_name_condition = cast(
                    func.coalesce(agent_name_text, ''),
                    String
                ).ilike(search_term)
                search_conditions.append(metadata_agent_name_condition)
                
                # For user_full_name and agent_name, we need to join with User and Agent tables
                # We'll do left outer joins to search in these related tables
                needs_user_join_for_search = True
                needs_agent_join_for_search = True
                
                # Join with User table for searching user_full_name
                if needs_user_join_for_search:
                    if not needs_user_join:  # Only join if not already joined for sorting
                        query = query.join(
                            User,
                            func.cast(FeedbackData.user_id, PG_UUID) == User.id,
                            isouter=True
                        )
                    # Add search conditions for user fields
                    # Search in full_name, azure_display_name, first_name, last_name, email
                    user_search_conditions = [
                        User.full_name.ilike(search_term) if User.full_name else False,
                        User.azure_display_name.ilike(search_term) if User.azure_display_name else False,
                        User.first_name.ilike(search_term) if User.first_name else False,
                        User.last_name.ilike(search_term) if User.last_name else False,
                        User.email.ilike(search_term) if User.email else False,
                    ]
                    search_conditions.extend([c for c in user_search_conditions if c is not False])
                
                # Search for agents by name in Messages and Sessions
                # Since FeedbackData.agent_id is NULL, we need to search via entity relationships
                # Use subqueries to avoid collecting massive ID lists
                if needs_agent_join_for_search:
                    from aldar_middleware.models.menu import Agent
                    from aldar_middleware.models.messages import Message
                    from aldar_middleware.models.sessions import Session
                    
                    # Create subquery for message IDs with matching agents
                    # Match on both Message.id and Message.public_id
                    message_id_subquery = (
                        select(func.lower(cast(Message.id, String)))
                        .select_from(Message)
                        .join(Agent, Message.agent_id == Agent.id)
                        .where(Agent.name.ilike(search_term))
                    )
                    
                    message_public_id_subquery = (
                        select(func.lower(cast(Message.public_id, String)))
                        .select_from(Message)
                        .join(Agent, Message.agent_id == Agent.id)
                        .where(Agent.name.ilike(search_term))
                    )
                    
                    # Create subquery for session IDs with matching agents
                    # Match on both Session.id and Session.public_id
                    session_id_subquery = (
                        select(func.lower(cast(Session.id, String)))
                        .select_from(Session)
                        .join(Agent, Session.agent_id == Agent.id)
                        .where(Agent.name.ilike(search_term))
                    )
                    
                    session_public_id_subquery = (
                        select(func.lower(cast(Session.public_id, String)))
                        .select_from(Session)
                        .join(Agent, Session.agent_id == Agent.id)
                        .where(Agent.name.ilike(search_term))
                    )
                    
                    # Match feedback where entity_id is in any of these subqueries
                    agent_search_condition = or_(
                        func.lower(cast(FeedbackData.entity_id, String)).in_(message_id_subquery),
                        func.lower(cast(FeedbackData.entity_id, String)).in_(message_public_id_subquery),
                        func.lower(cast(FeedbackData.entity_id, String)).in_(session_id_subquery),
                        func.lower(cast(FeedbackData.entity_id, String)).in_(session_public_id_subquery)
                    )
                    search_conditions.append(agent_search_condition)
                    logger.debug(f"Added agent name search condition using subqueries")
                
                # Apply search filter with OR logic - match if any field contains the search term
                valid_conditions = [c for c in search_conditions if c is not False]
                if valid_conditions:
                    query = query.where(or_(*valid_conditions))

            # Apply sorting
            # If sorting by user_full_name, we need to join with User table
            # (needs_user_join already initialized above)
            
            if needs_user_join:
                # Join with User table for sorting by full_name
                # Cast user_id (String) to UUID for join with User.id
                # Use PostgreSQL's UUID casting function
                query = query.join(
                    User,
                    func.cast(FeedbackData.user_id, PG_UUID) == User.id,
                    isouter=True
                )
            
            # Get total count (before applying sorting/pagination)
            # Build count query with same filters and joins as main query
            # Check if we have joins (for search or sorting)
            has_joins_for_count = (search and (needs_user_join_for_search or needs_agent_join_for_search)) or needs_user_join
            
            # Build count query base
            if has_joins_for_count:
                # Use distinct count when we have joins to avoid counting duplicates
                count_query = select(func.count(func.distinct(FeedbackData.feedback_id)))
            else:
                count_query = select(func.count(FeedbackData.feedback_id))
            
            # Start from FeedbackData
            count_query = count_query.select_from(FeedbackData)
            
            # Apply same base filters
            count_query = count_query.where(FeedbackData.deleted_at.is_(None))
            
            if user_id and not exclude_user_id:
                count_query = count_query.where(FeedbackData.user_id == user_id)
            if entity_type:
                count_query = count_query.where(FeedbackData.entity_type == entity_type)
            if entity_id:
                count_query = count_query.where(FeedbackData.entity_id == entity_id)
            if agent_id:
                count_query = count_query.where(FeedbackData.agent_id == agent_id)
            if rating:
                count_query = count_query.where(FeedbackData.rating == rating)
            if date_from:
                # Use full datetime comparison to respect time ranges
                if date_from.tzinfo is not None:
                    date_from = date_from.astimezone(timezone.utc).replace(tzinfo=None)
                count_query = count_query.where(FeedbackData.created_at >= date_from)
            if date_to:
                # Use full datetime comparison to respect time ranges
                if date_to.tzinfo is not None:
                    date_to = date_to.astimezone(timezone.utc).replace(tzinfo=None)
                count_query = count_query.where(FeedbackData.created_at <= date_to)
            
            # Apply search filters with joins if needed
            if search:
                search_term = f"%{search}%"
                search_conditions = [
                    FeedbackData.user_id.ilike(search_term),
                    FeedbackData.user_email.ilike(search_term) if FeedbackData.user_email else False,
                    FeedbackData.session_id.ilike(search_term) if FeedbackData.session_id else False,
                    FeedbackData.comment.ilike(search_term) if FeedbackData.comment else False,
                ]
                
                # Search in metadata_json.agent.agent_name using PostgreSQL JSONB operators
                # Cast JSON to JSONB first, then use -> operator for nested access and ->> for text extraction
                # Use coalesce to handle NULL values (when agent or agent_name doesn't exist)
                metadata_jsonb = cast(FeedbackData.metadata_json, JSONB)
                # Use -> to get 'agent' object, then -> to get 'agent_name', then ->> to get text
                agent_obj = metadata_jsonb.op('->')('agent')
                agent_name_text = agent_obj.op('->>')('agent_name')
                metadata_agent_name_condition = cast(
                    func.coalesce(agent_name_text, ''),
                    String
                ).ilike(search_term)
                search_conditions.append(metadata_agent_name_condition)
                
                # Join with User for user field search
                count_query = count_query.outerjoin(
                    User,
                    func.cast(FeedbackData.user_id, PG_UUID) == User.id
                )
                user_search_conditions = [
                    User.full_name.ilike(search_term) if User.full_name else False,
                    User.azure_display_name.ilike(search_term) if User.azure_display_name else False,
                    User.first_name.ilike(search_term) if User.first_name else False,
                    User.last_name.ilike(search_term) if User.last_name else False,
                    User.email.ilike(search_term) if User.email else False,
                ]
                search_conditions.extend([c for c in user_search_conditions if c is not False])
                
                # Search for agents by name via entity relationships (Messages/Sessions)
                # Since FeedbackData.agent_id is NULL, search via linked entities
                # Use subqueries to avoid collecting massive ID lists
                from aldar_middleware.models.menu import Agent
                from aldar_middleware.models.messages import Message
                from aldar_middleware.models.sessions import Session
                
                # Create subquery for message IDs with matching agents
                # Match on both Message.id and Message.public_id
                message_id_subquery = (
                    select(func.lower(cast(Message.id, String)))
                    .select_from(Message)
                    .join(Agent, Message.agent_id == Agent.id)
                    .where(Agent.name.ilike(search_term))
                )
                
                message_public_id_subquery = (
                    select(func.lower(cast(Message.public_id, String)))
                    .select_from(Message)
                    .join(Agent, Message.agent_id == Agent.id)
                    .where(Agent.name.ilike(search_term))
                )
                
                # Create subquery for session IDs with matching agents
                # Match on both Session.id and Session.public_id
                session_id_subquery = (
                    select(func.lower(cast(Session.id, String)))
                    .select_from(Session)
                    .join(Agent, Session.agent_id == Agent.id)
                    .where(Agent.name.ilike(search_term))
                )
                
                session_public_id_subquery = (
                    select(func.lower(cast(Session.public_id, String)))
                    .select_from(Session)
                    .join(Agent, Session.agent_id == Agent.id)
                    .where(Agent.name.ilike(search_term))
                )
                
                # Match feedback where entity_id is in any of these subqueries
                agent_search_condition = or_(
                    func.lower(cast(FeedbackData.entity_id, String)).in_(message_id_subquery),
                    func.lower(cast(FeedbackData.entity_id, String)).in_(message_public_id_subquery),
                    func.lower(cast(FeedbackData.entity_id, String)).in_(session_id_subquery),
                    func.lower(cast(FeedbackData.entity_id, String)).in_(session_public_id_subquery)
                )
                search_conditions.append(agent_search_condition)
                
                valid_conditions = [c for c in search_conditions if c is not False]
                if valid_conditions:
                    count_query = count_query.where(or_(*valid_conditions))
            
            count_result = await self.db.execute(count_query)
            total_count = count_result.scalar_one()

            # Apply sorting
            order_by_clause = None
            if sort_by:
                sort_by_lower = sort_by.lower()
                sort_order_upper = (sort_order or "DESC").upper()
                
                # Validate sort_order
                if sort_order_upper not in ["ASC", "DESC"]:
                    sort_order_upper = "DESC"
                
                # Map sort_by to model fields
                if sort_by_lower == "user_email":
                    field = FeedbackData.user_email
                    if sort_order_upper == "ASC":
                        order_by_clause = nullslast(asc(field))
                    else:
                        order_by_clause = nullslast(desc(field))
                elif sort_by_lower == "user_full_name":
                    # Use COALESCE to handle NULL full_name by falling back to azure_display_name or first_name + last_name
                    # For simplicity, we'll sort by full_name, and if NULL, use azure_display_name
                    full_name_expr = func.coalesce(
                        User.full_name,
                        User.azure_display_name,
                        func.concat(func.coalesce(User.first_name, ''), ' ', func.coalesce(User.last_name, ''))
                    )
                    if sort_order_upper == "ASC":
                        order_by_clause = nullslast(asc(full_name_expr))
                    else:
                        order_by_clause = nullslast(desc(full_name_expr))
                elif sort_by_lower == "date":
                    # Sort by created_at
                    field = FeedbackData.created_at
                    if sort_order_upper == "ASC":
                        order_by_clause = asc(field)
                    else:
                        order_by_clause = desc(field)
                elif sort_by_lower == "comment":
                    # Special handling for comment sorting:
                    # - ASC: NULL/empty comments at the end
                    # - DESC: NULL/empty comments at the beginning
                    # Use NULLIF to treat empty/whitespace-only strings as NULL for consistent sorting
                    field = func.nullif(func.trim(func.coalesce(FeedbackData.comment, '')), '')
                    if sort_order_upper == "ASC":
                        # ASC: NULLs last (at the end)
                        order_by_clause = nullslast(asc(field))
                    else:
                        # DESC: NULLs first (at the beginning)
                        order_by_clause = nullsfirst(desc(field))
                else:
                    # Invalid sort_by, default to created_at desc
                    order_by_clause = desc(FeedbackData.created_at)
            else:
                # Default sorting by created_at desc (latest first)
                order_by_clause = desc(FeedbackData.created_at)

            # Apply pagination
            offset = (page - 1) * limit
            query = (
                query.order_by(order_by_clause)
                .offset(offset)
                .limit(limit)
                .options(selectinload(FeedbackData.files))
            )

            result = await self.db.execute(query)
            feedback_list = result.scalars().all()

            logger.info(
                f"Listed feedback",
                extra={
                    "correlation_id": correlation_id,
                    "total_count": total_count,
                    "page": page,
                    "limit": limit,
                    "filters": {
                        "entity_type": entity_type.value if entity_type else None,
                        "rating": rating.value if rating else None,
                    },
                },
            )

            return feedback_list, total_count

        except Exception as e:
            logger.error(
                f"Failed to list feedback: {str(e)}",
                extra={"correlation_id": correlation_id},
                exc_info=True,
            )
            raise

    async def update_feedback_comment(
        self, feedback_id: UUID, comment: str, user_id: Optional[str] = None
    ) -> Optional[FeedbackData]:
        """
        Update feedback comment.

        Args:
            feedback_id: Feedback ID
            comment: New comment text
            user_id: Optional user ID for authorization

        Returns:
            Updated FeedbackData or None if not found
        """
        correlation_id = get_correlation_id()

        try:
            feedback = await self.get_feedback(feedback_id, user_id)

            if not feedback:
                logger.warning(
                    f"Feedback not found for update",
                    extra={
                        "correlation_id": correlation_id,
                        "feedback_id": str(feedback_id),
                    },
                )
                return None

            feedback.comment = comment
            feedback.updated_at = datetime.utcnow()

            await self.db.flush()

            logger.info(
                f"Feedback comment updated",
                extra={
                    "correlation_id": correlation_id,
                    "feedback_id": str(feedback_id),
                },
            )

            return feedback

        except Exception as e:
            logger.error(
                f"Failed to update feedback comment: {str(e)}",
                extra={
                    "correlation_id": correlation_id,
                    "feedback_id": str(feedback_id),
                },
                exc_info=True,
            )
            raise

    async def soft_delete_feedback(
        self, feedback_id: UUID, user_id: Optional[str] = None
    ) -> bool:
        """
        Soft delete feedback (mark as deleted).

        Args:
            feedback_id: Feedback ID
            user_id: Optional user ID for authorization

        Returns:
            True if deleted, False if not found
        """
        correlation_id = get_correlation_id()

        try:
            feedback = await self.get_feedback(feedback_id, user_id)

            if not feedback:
                logger.warning(
                    f"Feedback not found for deletion",
                    extra={
                        "correlation_id": correlation_id,
                        "feedback_id": str(feedback_id),
                    },
                )
                return False

            feedback.deleted_at = datetime.utcnow()
            await self.db.flush()

            logger.info(
                f"Feedback soft deleted",
                extra={
                    "correlation_id": correlation_id,
                    "feedback_id": str(feedback_id),
                },
            )

            return True

        except Exception as e:
            logger.error(
                f"Failed to delete feedback: {str(e)}",
                extra={
                    "correlation_id": correlation_id,
                    "feedback_id": str(feedback_id),
                },
                exc_info=True,
            )
            raise

    async def get_feedback_count_by_rating(
        self,
        entity_type: Optional[FeedbackEntityType] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> dict:
        """
        Get feedback count aggregated by rating.

        Args:
            entity_type: Optional entity type filter
            date_from: Optional start date
            date_to: Optional end date

        Returns:
            Dictionary with rating counts
        """
        correlation_id = get_correlation_id()

        try:
            query = select(
                FeedbackData.rating,
                func.count(FeedbackData.feedback_id).label("count"),
            ).where(FeedbackData.deleted_at.is_(None))

            if entity_type:
                query = query.where(FeedbackData.entity_type == entity_type)

            if date_from:
                # Frontend sends UTC datetime already converted from local time
                # Simple UTC comparison: WHERE created_at >= date_from
                if date_from.tzinfo is not None:
                    date_from = date_from.astimezone(timezone.utc).replace(tzinfo=None)
                query = query.where(FeedbackData.created_at >= date_from)

            if date_to:
                # Frontend sends UTC datetime already converted from local time
                # Simple UTC comparison: WHERE created_at <= date_to
                if date_to.tzinfo is not None:
                    date_to = date_to.astimezone(timezone.utc).replace(tzinfo=None)
                query = query.where(FeedbackData.created_at <= date_to)

            query = query.group_by(FeedbackData.rating)

            result = await self.db.execute(query)
            rows = result.all()

            counts = {
                "thumbs_up": 0,
                "thumbs_down": 0,
                "neutral": 0,
            }

            for rating, count in rows:
                counts[rating.value] = count

            logger.info(
                f"Retrieved feedback count by rating",
                extra={
                    "correlation_id": correlation_id,
                    "counts": counts,
                },
            )

            return counts

        except Exception as e:
            logger.error(
                f"Failed to get feedback count by rating: {str(e)}",
                extra={"correlation_id": correlation_id},
                exc_info=True,
            )
            raise

    async def get_total_feedback_count(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> int:
        """
        Get total feedback count.

        Args:
            date_from: Optional start date
            date_to: Optional end date

        Returns:
            Total count
        """
        query = select(func.count(FeedbackData.feedback_id)).where(
            FeedbackData.deleted_at.is_(None)
        )

        if date_from:
            # Frontend sends UTC datetime already converted from local time
            # Simple UTC comparison: WHERE created_at >= date_from
            if date_from.tzinfo is not None:
                date_from = date_from.astimezone(timezone.utc).replace(tzinfo=None)
            query = query.where(FeedbackData.created_at >= date_from)

        if date_to:
            # Frontend sends UTC datetime already converted from local time
            # Simple UTC comparison: WHERE created_at <= date_to
            if date_to.tzinfo is not None:
                date_to = date_to.astimezone(timezone.utc).replace(tzinfo=None)
            query = query.where(FeedbackData.created_at <= date_to)

        result = await self.db.execute(query)
        return result.scalar_one() or 0