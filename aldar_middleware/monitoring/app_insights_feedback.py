"""Application Insights integration for feedback analytics."""

import logging
from datetime import datetime
from typing import Dict, Optional

from aldar_middleware.settings import settings
from aldar_middleware.settings.context import get_correlation_id

logger = logging.getLogger(__name__)


class AppInsightsFeedbackReporter:
    """Report feedback metrics to Application Insights."""

    def __init__(self) -> None:
        """Initialize Application Insights feedback reporter."""
        self.enabled = settings.app_insights_enabled
        
        if self.enabled:
            try:
                from applicationinsights import TelemetryClient
                self.client = TelemetryClient(
                    settings.app_insights_connection_string
                )
            except ImportError:
                logger.warning("Application Insights not installed")
                self.enabled = False

    async def report_feedback_created(
        self,
        feedback_id: str,
        user_id: str,
        entity_type: str,
        rating: str,
        file_count: int,
        correlation_id: Optional[str] = None,
    ) -> None:
        """
        Report feedback creation to Application Insights.

        Args:
            feedback_id: Feedback ID
            user_id: User ID
            entity_type: Entity type
            rating: Feedback rating
            file_count: Number of files attached
            correlation_id: Correlation ID
        """
        if not self.enabled:
            return

        correlation_id = correlation_id or get_correlation_id()

        try:
            properties = {
                "feedback_id": feedback_id,
                "user_id": user_id,
                "entity_type": entity_type,
                "rating": rating,
                "file_count": str(file_count),
                "correlation_id": correlation_id,
            }

            measurements = {
                "file_count": file_count,
            }

            self.client.track_event(
                "FeedbackCreated",
                properties=properties,
                measurements=measurements,
            )

            logger.debug(
                "Reported feedback creation to Application Insights",
                extra={
                    "correlation_id": correlation_id,
                    "feedback_id": feedback_id,
                },
            )

        except Exception as e:
            logger.warning(
                f"Failed to report to Application Insights: {str(e)}",
                extra={"correlation_id": correlation_id},
            )

    async def report_analytics(
        self,
        summary: Dict,
        correlation_id: Optional[str] = None,
    ) -> None:
        """
        Report analytics to Application Insights.

        Args:
            summary: Analytics summary dictionary
            correlation_id: Correlation ID
        """
        if not self.enabled:
            return

        correlation_id = correlation_id or get_correlation_id()

        try:
            properties = {
                "correlation_id": correlation_id,
                "date_range_from": summary.get("date_range_from"),
                "date_range_to": summary.get("date_range_to"),
            }

            measurements = {
                "total_count": summary.get("total_count", 0),
                "positive_count": summary.get("positive_count", 0),
                "negative_count": summary.get("negative_count", 0),
                "neutral_count": summary.get("neutral_count", 0),
                "positive_ratio": summary.get("positive_ratio", 0),
                "average_sentiment_score": summary.get("average_sentiment_score", 0),
            }

            self.client.track_event(
                "FeedbackAnalytics",
                properties=properties,
                measurements=measurements,
            )

            logger.debug(
                "Reported analytics to Application Insights",
                extra={
                    "correlation_id": correlation_id,
                    "total_feedback": summary.get("total_count"),
                },
            )

        except Exception as e:
            logger.warning(
                f"Failed to report analytics to Application Insights: {str(e)}",
                extra={"correlation_id": correlation_id},
            )

    async def report_file_upload(
        self,
        feedback_id: str,
        file_name: str,
        file_size: int,
        success: bool,
        error_message: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        """
        Report file upload to Application Insights.

        Args:
            feedback_id: Feedback ID
            file_name: File name
            file_size: File size in bytes
            success: Whether upload was successful
            error_message: Optional error message
            correlation_id: Correlation ID
        """
        if not self.enabled:
            return

        correlation_id = correlation_id or get_correlation_id()

        try:
            properties = {
                "feedback_id": feedback_id,
                "file_name": file_name,
                "success": str(success),
                "correlation_id": correlation_id,
            }

            if error_message:
                properties["error_message"] = error_message

            measurements = {
                "file_size_bytes": file_size,
            }

            event_name = "FeedbackFileUploadSuccess" if success else "FeedbackFileUploadFailure"

            self.client.track_event(
                event_name,
                properties=properties,
                measurements=measurements,
            )

            logger.debug(
                f"Reported file upload ({event_name}) to Application Insights",
                extra={
                    "correlation_id": correlation_id,
                    "feedback_id": feedback_id,
                },
            )

        except Exception as e:
            logger.warning(
                f"Failed to report file upload to Application Insights: {str(e)}",
                extra={"correlation_id": correlation_id},
            )

    async def report_negative_feedback_alert(
        self,
        feedback_id: str,
        user_id: str,
        entity_type: str,
        entity_id: str,
        comment: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        """
        Report negative feedback as an alert to Application Insights.

        Args:
            feedback_id: Feedback ID
            user_id: User ID
            entity_type: Entity type
            entity_id: Entity ID
            comment: Feedback comment
            correlation_id: Correlation ID
        """
        if not self.enabled:
            return

        correlation_id = correlation_id or get_correlation_id()

        try:
            properties = {
                "feedback_id": feedback_id,
                "user_id": user_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "correlation_id": correlation_id,
                "comment": (comment or "")[:500],  # Truncate long comments
            }

            # Use severity level to escalate
            self.client.track_event(
                "NegativeFeedbackAlert",
                properties=properties,
            )

            logger.info(
                "Reported negative feedback alert to Application Insights",
                extra={
                    "correlation_id": correlation_id,
                    "feedback_id": feedback_id,
                },
            )

        except Exception as e:
            logger.warning(
                f"Failed to report negative feedback alert: {str(e)}",
                extra={"correlation_id": correlation_id},
            )

    async def flush(self) -> None:
        """Flush pending telemetry to Application Insights."""
        if self.enabled and hasattr(self.client, "flush"):
            try:
                self.client.flush()
            except Exception as e:
                logger.warning(f"Failed to flush Application Insights: {str(e)}")


# Global instance
_app_insights_reporter: Optional[AppInsightsFeedbackReporter] = None


def get_feedback_reporter() -> AppInsightsFeedbackReporter:
    """Get or create Application Insights feedback reporter."""
    global _app_insights_reporter
    if _app_insights_reporter is None:
        _app_insights_reporter = AppInsightsFeedbackReporter()
    return _app_insights_reporter