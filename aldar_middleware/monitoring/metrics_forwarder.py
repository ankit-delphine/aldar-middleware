"""
Collects Prometheus metrics and forwards them to Azure Monitor.
Extracts key metrics from the local Prometheus setup and sends them to Azure.
"""

import logging
from typing import Optional
from threading import Thread, Event
from datetime import datetime, timezone

from prometheus_client import REGISTRY

from aldar_middleware.monitoring.azure_metrics_ingestion import get_metrics_forwarder

logger = logging.getLogger(__name__)


class PrometheusToAzureForwarder:
    """
    Collects Prometheus metrics and forwards key metrics to Azure Monitor.
    Runs as a background thread that periodically pushes metrics.
    """

    # Key metrics to forward to Azure (metric_name -> dimension_labels)
    FORWARDED_METRICS = {
        # HTTP Metrics
        "aiq_http_requests_total": ["method", "endpoint", "status_code"],
        "aiq_http_request_duration_seconds": ["method", "endpoint"],
        # Agent Metrics
        "aiq_agent_calls_total": ["agent_type", "agent_name", "method", "status"],
        "aiq_agent_errors_total": ["agent_type", "agent_name", "error_type"],
        # OpenAI Metrics
        "aiq_openai_tokens_used_total": ["model", "token_type"],
        "aiq_openai_api_calls_total": ["model", "method", "status"],
        # MCP Metrics
        "aiq_mcp_connections_active": [],
        "aiq_mcp_requests_total": ["connection_id", "method", "status"],
    }

    def __init__(self, collection_interval: int = 60):
        """
        Initialize Prometheus to Azure Forwarder.

        Args:
            collection_interval: Seconds between metric collection and forwarding
        """
        self.collection_interval = collection_interval
        self.running = False
        self.thread: Optional[Thread] = None
        self.stop_event = Event()

    def start(self):
        """Start the background metrics forwarding thread."""
        if self.running:
            return

        self.running = True
        self.stop_event.clear()

        self.thread = Thread(daemon=True, target=self._collection_loop)
        self.thread.start()

        logger.info(
            f"Prometheus to Azure Forwarder started (interval: {self.collection_interval}s)"
        )

    def stop(self):
        """Stop the background forwarding thread."""
        if not self.running:
            return

        self.running = False
        self.stop_event.set()

        if self.thread:
            self.thread.join(timeout=10)

        logger.info("Prometheus to Azure Forwarder stopped")

    def _collection_loop(self):
        """Background loop for collecting and forwarding metrics."""
        while self.running:
            try:
                # Wait for collection interval or stop signal
                if self.stop_event.wait(timeout=self.collection_interval):
                    break

                # Collect and forward metrics
                self._collect_and_forward()

            except Exception as e:
                logger.error(f"Error in metrics collection loop: {str(e)}")

    def _collect_and_forward(self):
        """Collect Prometheus metrics and forward to Azure Monitor."""
        forwarder = get_metrics_forwarder()
        if not forwarder:
            return

        try:
            timestamp = datetime.now(timezone.utc)
            metrics_collected = 0

            # Iterate through all metrics in Prometheus registry
            for collector in REGISTRY._collector_to_names:
                try:
                    for metric in collector.collect():
                        if metric.name not in self.FORWARDED_METRICS:
                            continue

                        dimension_labels = self.FORWARDED_METRICS[metric.name]
                        metrics_collected += self._process_metric(
                            metric, dimension_labels, forwarder, timestamp
                        )

                except Exception as e:
                    logger.debug(f"Error processing metric collector: {str(e)}")

            if metrics_collected > 0:
                logger.debug(
                    f"Collected and queued {metrics_collected} metrics for Azure"
                )

        except Exception as e:
            logger.error(f"Error collecting Prometheus metrics: {str(e)}")

    def _process_metric(
        self, metric, dimension_labels, forwarder, timestamp
    ) -> int:
        """
        Process a single Prometheus metric and forward samples to Azure.

        Args:
            metric: Prometheus metric object
            dimension_labels: List of label names to include as dimensions
            forwarder: Metrics forwarder instance
            timestamp: Timestamp for all metrics

        Returns:
            Number of samples processed
        """
        processed = 0

        # Process metric samples (time series)
        for sample in metric.samples:
            try:
                # Build dimension dictionary from labels
                dimensions = {}
                if dimension_labels and sample.labels:
                    for label in dimension_labels:
                        if label in sample.labels:
                            dimensions[label] = sample.labels[label]

                # Get metric value
                value = sample.value

                # Skip NaN and infinity values
                if isinstance(value, float) and (value != value or value in [float('inf'), float('-inf')]):
                    continue

                # Forward to Azure
                forwarder.add_metric(
                    metric_name=sample.name,
                    value=float(value),
                    dimensions=dimensions if dimensions else None,
                )

                processed += 1

            except Exception as e:
                logger.debug(f"Error processing metric sample {sample.name}: {str(e)}")

        return processed


# Global instance
_forwarder: Optional[PrometheusToAzureForwarder] = None


def initialize_prometheus_forwarder(collection_interval: int = 60):
    """
    Initialize Prometheus to Azure metrics forwarder.

    Args:
        collection_interval: Seconds between collection cycles
    """
    global _forwarder

    try:
        # Only initialize if Azure metrics ingestion is enabled
        from aldar_middleware.settings import settings

        if not settings.azure_metrics_ingestion_enabled:
            logger.debug("Prometheus forwarder: Azure metrics ingestion is disabled")
            return

        # Check if Azure forwarder is available
        from aldar_middleware.monitoring.azure_metrics_ingestion import (
            get_metrics_forwarder as get_azure_forwarder,
        )

        if not get_azure_forwarder():
            logger.debug("Prometheus forwarder: Azure metrics ingestion not initialized")
            return

        # Create and start the Prometheus forwarder
        _forwarder = PrometheusToAzureForwarder(collection_interval=collection_interval)
        _forwarder.start()

        logger.info("Prometheus to Azure metrics forwarder initialized")

    except Exception as e:
        logger.error(f"Error initializing Prometheus forwarder: {str(e)}")


def shutdown_prometheus_forwarder():
    """Shutdown the Prometheus metrics forwarder."""
    global _forwarder

    if _forwarder:
        _forwarder.stop()
        logger.info("Prometheus metrics forwarder shut down")


def get_prometheus_forwarder() -> Optional[PrometheusToAzureForwarder]:
    """Get the global Prometheus forwarder instance."""
    return _forwarder