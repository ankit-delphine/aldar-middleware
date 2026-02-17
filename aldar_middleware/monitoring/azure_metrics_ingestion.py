"""Azure Monitor Metrics Ingestion Client for pushing custom metrics."""

import logging
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from threading import Thread, Event

from aldar_middleware.settings import settings

logger = logging.getLogger(__name__)


class AzureMetricsIngestionClient:
    """
    Client for publishing metrics to Azure Monitor via Metrics Ingestion API.
    Uses Data Collection Rules (DCR) for authentication and routing.
    """

    def __init__(
        self,
        endpoint: str,
        dcr_rule_id: str,
        stream_name: str = "Custom-Metrics",
        tenant_id: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ):
        """
        Initialize Azure Metrics Ingestion Client.

        Args:
            endpoint: Metrics ingestion endpoint URL
            dcr_rule_id: Data Collection Rule ID
            stream_name: Stream name from DCR
            tenant_id: Azure AD tenant ID (uses settings if not provided)
            client_id: Application client ID (uses settings if not provided)
            client_secret: Application client secret (uses settings if not provided)
        """
        self.endpoint = endpoint.rstrip("/")
        self.dcr_rule_id = dcr_rule_id
        self.stream_name = stream_name
        self.tenant_id = tenant_id or settings.azure_tenant_id
        self.client_id = client_id or settings.azure_client_id
        self.client_secret = client_secret or settings.azure_client_secret
        self.access_token = None
        self.token_expiry = None
        self.initialized = False

        # Validate required credentials
        if not all([self.tenant_id, self.client_id, self.client_secret]):
            logger.warning(
                "Azure Metrics Ingestion: Missing required credentials. "
                "Please set AZURE_TENANT_ID, AZURE_CLIENT_ID, and AZURE_CLIENT_SECRET"
            )
            return

        # Initialize SDK
        try:
            from azure.identity import ClientSecretCredential
            from azure.monitor.ingestion import MetricsClient as AzureMetricsClient

            self.credential = ClientSecretCredential(
                tenant_id=self.tenant_id,
                client_id=self.client_id,
                client_secret=self.client_secret,
            )

            self.metrics_client = AzureMetricsClient(
                endpoint=self.endpoint,
                credential=self.credential,
            )

            self.initialized = True
            logger.info(
                f"Azure Metrics Ingestion Client initialized. "
                f"Endpoint: {self.endpoint}, DCR: {dcr_rule_id}"
            )
        except ImportError as e:
            logger.error(
                "Failed to import Azure SDK. "
                "Install with: pip install azure-monitor-ingestion azure-identity"
            )
        except Exception as e:
            logger.error(f"Failed to initialize Azure Metrics Ingestion Client: {str(e)}")

    def send_metric(
        self,
        metric_name: str,
        value: float,
        dimensions: Optional[Dict[str, str]] = None,
        unit: str = "Count",
        timestamp: Optional[datetime] = None,
    ) -> bool:
        """
        Send a single metric to Azure Monitor.

        Args:
            metric_name: Name of the metric
            value: Metric value
            dimensions: Optional custom dimensions
            unit: Unit of measurement (Count, Bytes, Seconds, etc.)
            timestamp: Optional timestamp (uses current time if not provided)

        Returns:
            True if metric was sent successfully, False otherwise
        """
        if not self.initialized:
            return False

        try:
            if timestamp is None:
                timestamp = datetime.now(timezone.utc)

            # Format time in ISO 8601
            time_str = timestamp.isoformat()

            # Prepare metric data
            metric_data = {
                "resourceId": settings.azure_client_id,  # Use app registration as identifier
                "time": time_str,
                "data": {
                    "baseData": {
                        "metric": metric_name,
                        "namespace": "CustomMetrics/AIQ",
                        "series": [
                            {
                                "min": value,
                                "max": value,
                                "sum": value,
                                "count": 1,
                                "dimensions": dimensions or {},
                            }
                        ],
                    }
                },
            }

            # Send metric
            self.metrics_client.upload(
                rule_id=self.dcr_rule_id,
                stream_name=self.stream_name,
                logs=[metric_data],
            )

            logger.debug(
                f"Metric sent successfully: {metric_name} = {value}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to send metric '{metric_name}': {str(e)}")
            return False

    def send_batch_metrics(
        self,
        metrics: List[Dict[str, Any]],
        timestamp: Optional[datetime] = None,
    ) -> bool:
        """
        Send multiple metrics in a batch.

        Args:
            metrics: List of metric dictionaries with keys:
                     - name: metric name
                     - value: metric value
                     - dimensions: optional custom dimensions dict
                     - unit: optional unit (default: Count)
            timestamp: Optional timestamp for all metrics

        Returns:
            True if batch was sent successfully, False otherwise
        """
        if not self.initialized or not metrics:
            return False

        try:
            if timestamp is None:
                timestamp = datetime.now(timezone.utc)

            time_str = timestamp.isoformat()

            # Prepare batch metric data
            metrics_data_list = []
            for metric in metrics:
                metric_data = {
                    "resourceId": settings.azure_client_id,
                    "time": time_str,
                    "data": {
                        "baseData": {
                            "metric": metric.get("name", "Unknown"),
                            "namespace": "CustomMetrics/AIQ",
                            "series": [
                                {
                                    "min": metric.get("value", 0),
                                    "max": metric.get("value", 0),
                                    "sum": metric.get("value", 0),
                                    "count": 1,
                                    "dimensions": metric.get("dimensions", {}),
                                }
                            ],
                        }
                    },
                }
                metrics_data_list.append(metric_data)

            # Send batch
            self.metrics_client.upload(
                rule_id=self.dcr_rule_id,
                stream_name=self.stream_name,
                logs=metrics_data_list,
            )

            logger.debug(f"Batch of {len(metrics)} metrics sent successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to send batch metrics: {str(e)}")
            return False


class PrometheusMetricsForwarder:
    """
    Forwards Prometheus metrics to Azure Monitor Metrics Ingestion.
    Collects metrics from prometheus_client and sends them to Azure Monitor.
    """

    def __init__(
        self,
        ingestion_client: AzureMetricsIngestionClient,
        batch_size: int = 50,
        flush_interval: int = 30,
    ):
        """
        Initialize Prometheus Metrics Forwarder.

        Args:
            ingestion_client: Azure Metrics Ingestion Client instance
            batch_size: Number of metrics to batch before sending
            flush_interval: Seconds between automatic flushes
        """
        self.ingestion_client = ingestion_client
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.batch_queue: List[Dict[str, Any]] = []
        self.running = False
        self.flush_thread: Optional[Thread] = None
        self.stop_event = Event()

    def start(self):
        """Start the metrics forwarder background thread."""
        if self.running:
            return

        self.running = True
        self.stop_event.clear()

        # Start background thread for periodic flush
        self.flush_thread = Thread(daemon=True, target=self._flush_loop)
        self.flush_thread.start()

        logger.info("Prometheus Metrics Forwarder started")

    def stop(self):
        """Stop the metrics forwarder and flush remaining metrics."""
        if not self.running:
            return

        self.running = False
        self.stop_event.set()

        # Flush remaining metrics
        if self.batch_queue:
            self.flush()

        # Wait for thread to finish
        if self.flush_thread:
            self.flush_thread.join(timeout=5)

        logger.info("Prometheus Metrics Forwarder stopped")

    def add_metric(
        self,
        metric_name: str,
        value: float,
        dimensions: Optional[Dict[str, str]] = None,
    ):
        """
        Add a metric to the batch queue.

        Args:
            metric_name: Name of the metric
            value: Metric value
            dimensions: Optional custom dimensions
        """
        if not self.ingestion_client.initialized:
            return

        metric = {
            "name": metric_name,
            "value": value,
            "dimensions": dimensions or {},
        }

        self.batch_queue.append(metric)

        # Flush if batch is full
        if len(self.batch_queue) >= self.batch_size:
            self.flush()

    def flush(self):
        """Flush all queued metrics to Azure Monitor."""
        if not self.batch_queue:
            return

        metrics_to_send = self.batch_queue.copy()
        self.batch_queue.clear()

        if self.ingestion_client.send_batch_metrics(metrics_to_send):
            logger.debug(f"Flushed {len(metrics_to_send)} metrics to Azure Monitor")
        else:
            # Re-queue failed metrics (with limit to prevent unbounded growth)
            if len(self.batch_queue) < 1000:
                self.batch_queue.extend(metrics_to_send)

    def _flush_loop(self):
        """Background loop for periodic metric flushing."""
        while self.running:
            try:
                # Wait for flush interval or stop signal
                if self.stop_event.wait(timeout=self.flush_interval):
                    break

                # Flush any pending metrics
                if self.batch_queue:
                    self.flush()

            except Exception as e:
                logger.error(f"Error in metrics forwarder flush loop: {str(e)}")


# Global instances
_ingestion_client: Optional[AzureMetricsIngestionClient] = None
_metrics_forwarder: Optional[PrometheusMetricsForwarder] = None


def initialize_metrics_ingestion():
    """Initialize Azure Metrics Ingestion on application startup."""
    global _ingestion_client, _metrics_forwarder

    if not settings.azure_metrics_ingestion_enabled:
        logger.debug("Azure Metrics Ingestion is disabled")
        return

    if not all(
        [
            settings.azure_metrics_ingestion_endpoint,
            settings.azure_metrics_dcr_rule_id,
        ]
    ):
        logger.warning(
            "Azure Metrics Ingestion enabled but missing configuration. "
            "Please set AZURE_METRICS_INGESTION_ENDPOINT and AZURE_METRICS_DCR_RULE_ID"
        )
        return

    try:
        # Create ingestion client
        _ingestion_client = AzureMetricsIngestionClient(
            endpoint=settings.azure_metrics_ingestion_endpoint,
            dcr_rule_id=settings.azure_metrics_dcr_rule_id,
            stream_name=settings.azure_metrics_dcr_stream_name,
        )

        if _ingestion_client.initialized:
            # Create and start metrics forwarder
            _metrics_forwarder = PrometheusMetricsForwarder(
                ingestion_client=_ingestion_client,
                batch_size=50,
                flush_interval=30,
            )
            _metrics_forwarder.start()

            logger.info("Azure Metrics Ingestion initialized successfully")
        else:
            logger.error("Failed to initialize Azure Metrics Ingestion Client")

    except Exception as e:
        logger.error(f"Error initializing Azure Metrics Ingestion: {str(e)}")


def shutdown_metrics_ingestion():
    """Shutdown Azure Metrics Ingestion on application shutdown."""
    global _metrics_forwarder

    if _metrics_forwarder:
        _metrics_forwarder.stop()
        logger.info("Azure Metrics Ingestion shutdown complete")


def get_metrics_forwarder() -> Optional[PrometheusMetricsForwarder]:
    """Get the global metrics forwarder instance."""
    return _metrics_forwarder


def send_metric_to_azure(
    metric_name: str,
    value: float,
    dimensions: Optional[Dict[str, str]] = None,
):
    """
    Send a metric to Azure Monitor (convenience function).

    Args:
        metric_name: Name of the metric
        value: Metric value
        dimensions: Optional custom dimensions
    """
    forwarder = get_metrics_forwarder()
    if forwarder:
        forwarder.add_metric(metric_name, value, dimensions)