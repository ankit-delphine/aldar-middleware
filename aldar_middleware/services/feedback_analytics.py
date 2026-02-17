"""Feedback analytics service."""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

from aldar_middleware.models.feedback import (
    FeedbackData,
    FeedbackEntityType,
    FeedbackRating,
)
from aldar_middleware.settings.context import get_correlation_id
from aldar_middleware.settings import settings

logger = logging.getLogger(__name__)


class FeedbackAnalyticsService:
    """Service for feedback analytics and insights."""

    def __init__(self, db: AsyncSession, redis: Optional[Redis] = None) -> None:
        """Initialize analytics service.
        
        Args:
            db: Database session
            redis: Optional Redis client for caching
        """
        self.db = db
        self.redis = redis
        self.cache_ttl = settings.feedback_analytics_cache_ttl_seconds

    async def get_analytics_summary(
        self,
        entity_type: Optional[FeedbackEntityType] = None,
        agent_id: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        use_cache: bool = True,
    ) -> Dict:
        """
        Get comprehensive feedback analytics summary.

        Args:
            entity_type: Optional entity type filter
            agent_id: Optional agent ID filter
            date_from: Optional start date
            date_to: Optional end date
            use_cache: Whether to use Redis cache

        Returns:
            Analytics summary dictionary
        """
        correlation_id = get_correlation_id()

        # Generate cache key
        cache_key = self._generate_cache_key(
            "summary", entity_type, agent_id, date_from, date_to
        )

        # Try to get from cache
        if use_cache and self.redis:
            cached = await self._get_from_cache(cache_key)
            if cached:
                logger.info(
                    f"Retrieved analytics from cache",
                    extra={
                        "correlation_id": correlation_id,
                        "cache_key": cache_key,
                    },
                )
                return cached

        try:
            # Get date range (default to last 30 days)
            if not date_from:
                date_from = datetime.utcnow() - timedelta(days=30)
            if not date_to:
                date_to = datetime.utcnow()

            # Get counts by rating
            rating_counts = await self._get_rating_counts(
                entity_type, agent_id, date_from, date_to
            )

            total = sum(rating_counts.values())
            positive = rating_counts.get(FeedbackRating.THUMBS_UP.value, 0)
            negative = rating_counts.get(FeedbackRating.THUMBS_DOWN.value, 0)
            neutral = rating_counts.get(FeedbackRating.NEUTRAL.value, 0)

            positive_ratio = (positive / total * 100) if total > 0 else 0
            sentiment_score = (
                ((positive - negative) / total * 100) if total > 0 else 0
            )

            # Get breakdown by entity type
            by_entity_type = await self._get_breakdown_by_entity_type(
                agent_id, date_from, date_to
            )

            # Get breakdown by date (daily)
            by_date = await self._get_breakdown_by_date(
                entity_type, agent_id, date_from, date_to
            )

            # Get breakdown by agent
            by_agent = await self._get_breakdown_by_agent(entity_type, date_from, date_to)

            result = {
                "total_count": total,
                "positive_count": positive,
                "negative_count": negative,
                "neutral_count": neutral,
                "positive_ratio": round(positive_ratio, 2),
                "average_sentiment_score": round(sentiment_score, 2),
                "by_entity_type": by_entity_type,
                "by_date": by_date,
                "by_agent": by_agent,
                "date_range_from": date_from.isoformat(),
                "date_range_to": date_to.isoformat(),
            }

            # Cache result
            if self.redis:
                await self._set_in_cache(cache_key, result)

            logger.info(
                f"Generated analytics summary",
                extra={
                    "correlation_id": correlation_id,
                    "total_count": total,
                    "positive_ratio": positive_ratio,
                },
            )

            return result

        except Exception as e:
            logger.error(
                f"Failed to generate analytics summary: {str(e)}",
                extra={"correlation_id": correlation_id},
                exc_info=True,
            )
            raise

    async def get_trends(
        self,
        entity_type: Optional[FeedbackEntityType] = None,
        days_back: int = 7,
    ) -> List[Dict]:
        """
        Get feedback trends over time.

        Args:
            entity_type: Optional entity type filter
            days_back: Number of days to analyze

        Returns:
            List of trend data points
        """
        correlation_id = get_correlation_id()

        try:
            now = datetime.utcnow()
            date_from = now - timedelta(days=days_back)

            # Get current period data
            query_current = select(
                func.count(FeedbackData.feedback_id).label("count"),
                FeedbackData.rating,
            ).where(
                and_(
                    FeedbackData.created_at >= date_from,
                    FeedbackData.created_at <= now,
                    FeedbackData.deleted_at.is_(None),
                )
            )

            if entity_type:
                query_current = query_current.where(
                    FeedbackData.entity_type == entity_type
                )

            query_current = query_current.group_by(FeedbackData.rating)

            result_current = await self.db.execute(query_current)
            current_counts = {row[1].value: row[0] for row in result_current.all()}

            # Get previous period data
            date_from_prev = date_from - timedelta(days=days_back)
            query_prev = select(
                func.count(FeedbackData.feedback_id).label("count"),
                FeedbackData.rating,
            ).where(
                and_(
                    FeedbackData.created_at >= date_from_prev,
                    FeedbackData.created_at < date_from,
                    FeedbackData.deleted_at.is_(None),
                )
            )

            if entity_type:
                query_prev = query_prev.where(
                    FeedbackData.entity_type == entity_type
                )

            query_prev = query_prev.group_by(FeedbackData.rating)

            result_prev = await self.db.execute(query_prev)
            prev_counts = {row[1].value: row[0] for row in result_prev.all()}

            # Calculate trends
            trends = []
            for rating_val in ["thumbs_up", "thumbs_down", "neutral"]:
                current_val = current_counts.get(rating_val, 0)
                prev_val = prev_counts.get(rating_val, 0)

                change_percent = (
                    ((current_val - prev_val) / prev_val * 100)
                    if prev_val > 0
                    else 0
                )

                trend_direction = (
                    "up" if change_percent > 5 else "down" if change_percent < -5 else "stable"
                )

                trends.append(
                    {
                        "metric_name": f"feedback_{rating_val}",
                        "trend_direction": trend_direction,
                        "current_value": current_val,
                        "previous_value": prev_val,
                        "change_percent": round(change_percent, 2),
                        "period": f"last_{days_back}_days",
                    }
                )

            logger.info(
                f"Generated feedback trends",
                extra={
                    "correlation_id": correlation_id,
                    "days_back": days_back,
                    "trends_count": len(trends),
                },
            )

            return trends

        except Exception as e:
            logger.error(
                f"Failed to generate trends: {str(e)}",
                extra={"correlation_id": correlation_id},
                exc_info=True,
            )
            raise

    async def _get_rating_counts(
        self,
        entity_type: Optional[FeedbackEntityType],
        agent_id: Optional[str],
        date_from: datetime,
        date_to: datetime,
    ) -> Dict[str, int]:
        """Get counts aggregated by rating."""
        query = select(
            FeedbackData.rating, func.count(FeedbackData.feedback_id)
        ).where(
            and_(
                FeedbackData.created_at >= date_from,
                FeedbackData.created_at <= date_to,
                FeedbackData.deleted_at.is_(None),
            )
        )

        if entity_type:
            query = query.where(FeedbackData.entity_type == entity_type)

        if agent_id:
            query = query.where(FeedbackData.agent_id == agent_id)

        query = query.group_by(FeedbackData.rating)

        result = await self.db.execute(query)
        counts = {row[0].value: row[1] for row in result.all()}

        return counts

    async def _get_breakdown_by_entity_type(
        self,
        agent_id: Optional[str],
        date_from: datetime,
        date_to: datetime,
    ) -> List[Dict]:
        """Get breakdown by entity type."""
        query = select(
            FeedbackData.entity_type,
            FeedbackData.rating,
            func.count(FeedbackData.feedback_id),
        ).where(
            and_(
                FeedbackData.created_at >= date_from,
                FeedbackData.created_at <= date_to,
                FeedbackData.deleted_at.is_(None),
            )
        )

        if agent_id:
            query = query.where(FeedbackData.agent_id == agent_id)

        query = query.group_by(FeedbackData.entity_type, FeedbackData.rating)

        result = await self.db.execute(query)

        # Aggregate by entity type
        breakdown = {}
        for entity_type, rating, count in result.all():
            key = entity_type.value
            if key not in breakdown:
                breakdown[key] = {
                    "entity_type": key,
                    "total_count": 0,
                    "positive_count": 0,
                    "negative_count": 0,
                    "neutral_count": 0,
                }

            breakdown[key]["total_count"] += count
            if rating == FeedbackRating.THUMBS_UP:
                breakdown[key]["positive_count"] += count
            elif rating == FeedbackRating.THUMBS_DOWN:
                breakdown[key]["negative_count"] += count
            else:
                breakdown[key]["neutral_count"] += count

        # Calculate ratios
        for key in breakdown:
            total = breakdown[key]["total_count"]
            positive = breakdown[key]["positive_count"]
            breakdown[key]["positive_ratio"] = (
                round(positive / total * 100, 2) if total > 0 else 0
            )

        return list(breakdown.values())

    async def _get_breakdown_by_date(
        self,
        entity_type: Optional[FeedbackEntityType],
        agent_id: Optional[str],
        date_from: datetime,
        date_to: datetime,
    ) -> List[Dict]:
        """Get daily breakdown."""
        query = select(
            func.date(FeedbackData.created_at).label("date"),
            FeedbackData.rating,
            func.count(FeedbackData.feedback_id),
        ).where(
            and_(
                FeedbackData.created_at >= date_from,
                FeedbackData.created_at <= date_to,
                FeedbackData.deleted_at.is_(None),
            )
        )

        if entity_type:
            query = query.where(FeedbackData.entity_type == entity_type)

        if agent_id:
            query = query.where(FeedbackData.agent_id == agent_id)

        query = query.group_by(func.date(FeedbackData.created_at), FeedbackData.rating)
        query = query.order_by(func.date(FeedbackData.created_at))

        result = await self.db.execute(query)

        # Aggregate by date
        breakdown = {}
        for date, rating, count in result.all():
            key = date.isoformat()
            if key not in breakdown:
                breakdown[key] = {
                    "date": key,
                    "total_count": 0,
                    "positive_count": 0,
                    "negative_count": 0,
                    "neutral_count": 0,
                }

            breakdown[key]["total_count"] += count
            if rating == FeedbackRating.THUMBS_UP:
                breakdown[key]["positive_count"] += count
            elif rating == FeedbackRating.THUMBS_DOWN:
                breakdown[key]["negative_count"] += count
            else:
                breakdown[key]["neutral_count"] += count

        # Calculate ratios
        for key in breakdown:
            total = breakdown[key]["total_count"]
            positive = breakdown[key]["positive_count"]
            breakdown[key]["positive_ratio"] = (
                round(positive / total * 100, 2) if total > 0 else 0
            )

        return list(breakdown.values())

    async def _get_breakdown_by_agent(
        self,
        entity_type: Optional[FeedbackEntityType],
        date_from: datetime,
        date_to: datetime,
    ) -> List[Dict]:
        """Get breakdown by agent."""
        query = select(
            FeedbackData.agent_id,
            FeedbackData.rating,
            func.count(FeedbackData.feedback_id),
        ).where(
            and_(
                FeedbackData.created_at >= date_from,
                FeedbackData.created_at <= date_to,
                FeedbackData.deleted_at.is_(None),
                FeedbackData.agent_id.isnot(None),
            )
        )

        if entity_type:
            query = query.where(FeedbackData.entity_type == entity_type)

        query = query.group_by(FeedbackData.agent_id, FeedbackData.rating)

        result = await self.db.execute(query)

        # Aggregate by agent
        breakdown = {}
        for agent_id, rating, count in result.all():
            if agent_id not in breakdown:
                breakdown[agent_id] = {
                    "agent_id": agent_id,
                    "total_count": 0,
                    "positive_count": 0,
                    "negative_count": 0,
                    "neutral_count": 0,
                }

            breakdown[agent_id]["total_count"] += count
            if rating == FeedbackRating.THUMBS_UP:
                breakdown[agent_id]["positive_count"] += count
            elif rating == FeedbackRating.THUMBS_DOWN:
                breakdown[agent_id]["negative_count"] += count
            else:
                breakdown[agent_id]["neutral_count"] += count

        # Calculate ratios
        for agent_id in breakdown:
            total = breakdown[agent_id]["total_count"]
            positive = breakdown[agent_id]["positive_count"]
            breakdown[agent_id]["positive_ratio"] = (
                round(positive / total * 100, 2) if total > 0 else 0
            )

        return list(breakdown.values())

    def _generate_cache_key(
        self,
        operation: str,
        entity_type: Optional[FeedbackEntityType],
        agent_id: Optional[str],
        date_from: Optional[datetime],
        date_to: Optional[datetime],
    ) -> str:
        """Generate a cache key for analytics."""
        parts = [
            "feedback_analytics",
            operation,
            entity_type.value if entity_type else "all",
            agent_id or "all",
            date_from.date().isoformat() if date_from else "start",
            date_to.date().isoformat() if date_to else "end",
        ]
        return ":".join(parts)

    async def _get_from_cache(self, key: str) -> Optional[Dict]:
        """Get value from Redis cache."""
        if not self.redis:
            return None

        try:
            value = await self.redis.get(key)
            if value:
                return json.loads(value)
        except Exception as e:
            logger.warning(f"Cache retrieval failed: {str(e)}")

        return None

    async def _set_in_cache(self, key: str, value: Dict) -> None:
        """Set value in Redis cache."""
        if not self.redis:
            return

        try:
            await self.redis.setex(
                key, self.cache_ttl, json.dumps(value, default=str)
            )
        except Exception as e:
            logger.warning(f"Cache write failed: {str(e)}")