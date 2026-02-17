"""Azure Managed Prometheus, Grafana and Application Insights integration."""

import logging
import json
from typing import Optional, Dict, Any
from datetime import datetime

from aldar_middleware.settings import settings

logger = logging.getLogger(__name__)


class AzureMonitoringConfig:
    """Configuration for Azure Managed Prometheus and Grafana."""

    def __init__(self):
        """Initialize Azure monitoring configuration."""
        self.prometheus_enabled = settings.azure_prometheus_enabled
        self.prometheus_endpoint = settings.azure_prometheus_endpoint
        self.prometheus_workspace_id = settings.azure_prometheus_workspace_id

        self.grafana_enabled = settings.azure_grafana_enabled
        self.grafana_endpoint = settings.azure_grafana_endpoint
        self.grafana_api_key = settings.azure_grafana_api_key

        self.app_insights_enabled = settings.app_insights_enabled
        self.app_insights_connection_string = settings.app_insights_connection_string

    def is_azure_enabled(self) -> bool:
        """Check if any Azure monitoring is enabled."""
        return (
            self.prometheus_enabled
            or self.grafana_enabled
            or self.app_insights_enabled
        )

    def validate(self) -> bool:
        """Validate Azure monitoring configuration."""
        if not self.is_azure_enabled():
            return True

        errors = []

        if self.prometheus_enabled:
            if not self.prometheus_endpoint:
                errors.append("Azure Prometheus endpoint is required when enabled")
            if not self.prometheus_workspace_id:
                errors.append("Azure Prometheus workspace ID is required when enabled")

        if self.grafana_enabled:
            if not self.grafana_endpoint:
                errors.append("Azure Grafana endpoint is required when enabled")
            if not self.grafana_api_key:
                errors.append("Azure Grafana API key is required when enabled")

        if self.app_insights_enabled:
            if not self.app_insights_connection_string:
                errors.append("Application Insights connection string is required when enabled")

        if errors:
            for error in errors:
                logger.error(error)
            return False

        return True


class AzurePrometheusClient:
    """Client for Azure Managed Prometheus."""

    def __init__(self, config: AzureMonitoringConfig):
        """Initialize Azure Prometheus client."""
        self.config = config
        self.endpoint = config.prometheus_endpoint
        self.workspace_id = config.prometheus_workspace_id
        self.initialized = False
        
        # Initialize Azure Monitor for production metrics export
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor
            from azure.monitor.opentelemetry.exporter import AzureMonitorMetricExporter
            import logging
            
            # Suppress non-critical Azure Monitor exporter errors (connection aborted during response)
            # These errors occur when the HTTP connection closes before telemetry can be sent
            # They don't affect application functionality
            azure_monitor_logger = logging.getLogger("azure.monitor.opentelemetry.exporter.export._base")
            azure_monitor_logger.setLevel(logging.CRITICAL)  # Only show critical errors
            
            # Suppress Azure SDK HTTP logging policy (too verbose)
            logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
            
            # Configure Azure Monitor with metrics export
            # Will use managed identity in Azure, or environment variables for auth
            # Pass connection string if Application Insights is enabled and configured
            kwargs = {}
            if config.app_insights_enabled and config.app_insights_connection_string:
                kwargs['connection_string'] = config.app_insights_connection_string
            
            configure_azure_monitor(**kwargs)
            
            self.initialized = True
            logger.info(f"Azure Prometheus client initialized: {self.endpoint}")
            logger.debug("Azure Monitor OpenTelemetry configured for metrics export")
        except ImportError:
            logger.warning("azure-monitor-opentelemetry not installed. Install with: pip install azure-monitor-opentelemetry")
        except Exception as e:
            logger.warning(f"Failed to initialize Azure Monitor: {str(e)}")

    def push_metrics(self, metrics: Dict[str, Any]) -> bool:
        """Push metrics to Azure Prometheus via OpenTelemetry."""
        try:
            # Metrics are automatically pushed via OpenTelemetry SDK when configured
            logger.debug(f"Metrics queued for Azure Prometheus: {json.dumps(metrics)}")
            return True
        except Exception as e:
            logger.error(f"Failed to push metrics to Azure Prometheus: {str(e)}")
            return False


class AzureGrafanaClient:
    """Client for Azure Managed Grafana."""

    def __init__(self, config: AzureMonitoringConfig):
        """Initialize Azure Grafana client."""
        self.config = config
        self.endpoint = config.grafana_endpoint.strip()
        self.api_key = config.grafana_api_key
        self.endpoint_url = self.endpoint.rstrip("/")

        logger.info(f"Azure Grafana client initialized: {self.endpoint_url}")

    def dashboard_exists(self, dashboard_title: str) -> bool:
        """Check if a dashboard with the given title already exists."""
        try:
            import requests

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            response = requests.get(
                f"{self.endpoint_url}/api/search?query={dashboard_title}&type=dash-db",
                headers=headers,
                timeout=10,
                verify=True,
            )

            if response.status_code == 200:
                dashboards = response.json()
                for dashboard in dashboards:
                    if dashboard.get("title") == dashboard_title:
                        logger.debug(f"Dashboard '{dashboard_title}' already exists")
                        return True
                return False
            else:
                logger.debug(f"Failed to check if dashboard exists: {response.status_code}")
                return False
        except Exception as e:
            logger.debug(f"Error checking dashboard existence: {str(e)}")
            return False

    def create_dashboard(self, dashboard_config: Dict[str, Any]) -> bool:
        """Create a dashboard in Azure Grafana."""
        try:
            import requests

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            response = requests.post(
                f"{self.endpoint_url}/api/dashboards/db",
                headers=headers,
                json=dashboard_config,
                timeout=10,
                verify=True,
            )

            if response.status_code in [200, 201]:
                logger.info("Dashboard created successfully in Azure Grafana")
                return True
            elif response.status_code == 403:
                logger.warning("Cannot create dashboard in Azure Grafana: Insufficient permissions. "
                              "Please ensure the API key has 'dashboards:create' or 'dashboards:write' permissions.")
                return False
            else:
                logger.warning(f"Failed to create dashboard: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.debug(f"Error creating dashboard in Azure Grafana: {str(e)}")
            return False

    def datasource_exists(self, datasource_name: str) -> bool:
        """Check if a data source with the given name already exists."""
        try:
            import requests

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            response = requests.get(
                f"{self.endpoint_url}/api/datasources/name/{datasource_name}",
                headers=headers,
                timeout=10,
                verify=True,
            )

            if response.status_code == 200:
                logger.debug(f"Data source '{datasource_name}' already exists")
                return True
            elif response.status_code == 404:
                return False
            else:
                logger.debug(f"Failed to check if datasource exists: {response.status_code}")
                return False
        except Exception as e:
            logger.debug(f"Error checking datasource existence: {str(e)}")
            return False

    def create_datasource(self, datasource_config: Dict[str, Any]) -> bool:
        """Create a data source in Azure Grafana."""
        try:
            import requests

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            response = requests.post(
                f"{self.endpoint_url}/api/datasources",
                headers=headers,
                json=datasource_config,
                timeout=10,
                verify=True,
            )

            if response.status_code in [200, 201]:
                logger.info("Data source created successfully in Azure Grafana")
                return True
            elif response.status_code == 403:
                logger.warning("Cannot create datasource in Azure Grafana: Insufficient permissions. "
                              "Please ensure the API key has 'datasources:create' permission.")
                return False
            else:
                logger.warning(f"Failed to create data source: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.debug(f"Error creating data source in Azure Grafana: {str(e)}")
            return False

    def create_aiq_dashboard(self) -> bool:
        """Create AIQ Backend dashboard in Azure Grafana (only if it doesn't exist)."""
        dashboard_title = "AIQ Backend Monitoring"
        
        # Check if dashboard already exists
        if self.dashboard_exists(dashboard_title):
            logger.info(f"Dashboard '{dashboard_title}' already exists. Skipping creation.")
            return True
        
        dashboard_config = {
            "dashboard": {
                "title": dashboard_title,
                "tags": ["aiq", "backend"],
                "timezone": "browser",
                "panels": [
                    {
                        "title": "HTTP Requests",
                        "targets": [
                            {
                                "expr": "rate(aiq_http_requests_total[5m])",
                                "legendFormat": "{{method}} {{endpoint}}",
                            }
                        ],
                        "type": "graph",
                        "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8},
                    },
                    {
                        "title": "Agent Calls",
                        "targets": [
                            {
                                "expr": "rate(aiq_agent_calls_total[5m])",
                                "legendFormat": "{{agent_type}} {{agent_name}}",
                            }
                        ],
                        "type": "graph",
                        "gridPos": {"x": 12, "y": 0, "w": 12, "h": 8},
                    },
                    {
                        "title": "OpenAI Tokens Used",
                        "targets": [
                            {
                                "expr": "increase(aiq_openai_tokens_used_total[1h])",
                                "legendFormat": "{{model}} {{token_type}}",
                            }
                        ],
                        "type": "graph",
                        "gridPos": {"x": 0, "y": 8, "w": 12, "h": 8},
                    },
                ],
            }
        }
        return self.create_dashboard(dashboard_config)

    def create_prometheus_datasource(self, prometheus_endpoint: str) -> bool:
        """Create Prometheus data source in Azure Grafana (only if it doesn't exist)."""
        datasource_name = "Azure Prometheus"
        
        # Check if datasource already exists
        if self.datasource_exists(datasource_name):
            logger.info(f"Data source '{datasource_name}' already exists. Skipping creation.")
            return True
        
        datasource_config = {
            "name": datasource_name,
            "type": "prometheus",
            "url": prometheus_endpoint,
            "access": "proxy",
            "isDefault": True,
        }
        return self.create_datasource(datasource_config)


class ApplicationInsightsClient:
    """Client for Azure Application Insights."""

    def __init__(self, config: AzureMonitoringConfig):
        """Initialize Application Insights client."""
        self.config = config
        self.connection_string = config.app_insights_connection_string
        self.client = None
        self.instrumentation_key = self._extract_instrumentation_key()
        self.initialized = False

        # Initialize Azure Application Insights SDK
        try:
            if not self.connection_string:
                logger.warning("Application Insights: Connection string not configured")
                return
            
            if not self.instrumentation_key:
                logger.warning(f"Application Insights: Could not extract instrumentation key from connection string. "
                              f"Connection string: {self.connection_string[:50]}...")
                return
            
            from azure.monitor.opentelemetry import configure_azure_monitor
            import logging
            
            # Suppress non-critical Azure Monitor exporter errors (connection aborted during response)
            # These errors occur when the HTTP connection closes before telemetry can be sent
            # They don't affect application functionality
            azure_monitor_logger = logging.getLogger("azure.monitor.opentelemetry.exporter.export._base")
            azure_monitor_logger.setLevel(logging.CRITICAL)  # Only show critical errors
            
            # Suppress Azure SDK HTTP logging policy (too verbose)
            logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
            
            configure_azure_monitor(connection_string=self.connection_string)
            self.initialized = True
            logger.info("Application Insights client initialized successfully")
        except ImportError:
            logger.warning("azure-monitor-opentelemetry not installed. Install with: pip install azure-monitor-opentelemetry")
        except Exception as e:
            logger.warning(f"Failed to initialize Application Insights: {str(e)}")
    
    def _extract_instrumentation_key(self) -> Optional[str]:
        """Extract instrumentation key from connection string."""
        if not self.connection_string:
            logger.debug("Application Insights: No connection string provided")
            return None
        try:
            # Connection string format: InstrumentationKey=<key>;IngestionEndpoint=<url>;...
            parts = self.connection_string.split(";")
            for part in parts:
                if part.startswith("InstrumentationKey="):
                    key = part.split("=", 1)[1].strip()
                    if key:
                        logger.debug(f"Application Insights: Extracted instrumentation key")
                        return key
            logger.debug(f"Application Insights: InstrumentationKey not found in connection string parts: {parts[:3]}")
        except Exception as e:
            logger.debug(f"Application Insights: Failed to extract instrumentation key: {str(e)}")
        return None

    def track_event(
        self, event_name: str, properties: Optional[Dict[str, str]] = None
    ) -> bool:
        """Track a custom event in Application Insights."""
        try:
            from opentelemetry import trace

            tracer = trace.get_tracer(__name__)
            with tracer.start_as_current_span(event_name) as span:
                if properties:
                    for key, value in properties.items():
                        span.set_attribute(key, value)

            logger.debug(f"Tracked event in Application Insights: {event_name}")
            return True
        except Exception as e:
            logger.error(f"Error tracking event in Application Insights: {str(e)}")
            return False

    def track_metric(self, metric_name: str, value: float) -> bool:
        """Track a custom metric in Application Insights."""
        try:
            from opentelemetry import metrics

            meter = metrics.get_meter(__name__)
            counter = meter.create_counter(metric_name)
            counter.add(value)

            logger.debug(f"Tracked metric in Application Insights: {metric_name}={value}")
            return True
        except Exception as e:
            logger.error(f"Error tracking metric in Application Insights: {str(e)}")
            return False

    def track_request(
        self,
        method: str,
        path: str,
        status_code: int,
        duration: float,
        correlation_id: Optional[str] = None,
    ) -> bool:
        """Track HTTP request in Application Insights."""
        try:
            from opentelemetry import trace

            tracer = trace.get_tracer(__name__)
            span_name = f"{method} {path}"

            with tracer.start_as_current_span(span_name) as span:
                span.set_attribute("http.method", method)
                span.set_attribute("http.url", path)
                span.set_attribute("http.status_code", status_code)
                span.set_attribute("http.duration_ms", duration * 1000)
                if correlation_id:
                    span.set_attribute("correlation_id", correlation_id)

            logger.debug(f"Tracked HTTP request: {method} {path} - {status_code}")
            return True
        except Exception as e:
            logger.error(f"Error tracking request in Application Insights: {str(e)}")
            return False


# Global instances
azure_config: Optional[AzureMonitoringConfig] = None
azure_prometheus_client: Optional[AzurePrometheusClient] = None
azure_grafana_client: Optional[AzureGrafanaClient] = None
app_insights_client: Optional[ApplicationInsightsClient] = None


def initialize_azure_monitoring():
    """Initialize Azure monitoring clients."""
    global azure_config, azure_prometheus_client, azure_grafana_client, app_insights_client

    try:
        logger.debug("Starting Azure monitoring initialization...")
        azure_config = AzureMonitoringConfig()

        if not azure_config.is_azure_enabled():
            logger.info("Azure monitoring is not enabled")
            return

        if not azure_config.validate():
            logger.warning("Azure monitoring configuration validation failed")
            return

        if azure_config.prometheus_enabled:
            try:
                azure_prometheus_client = AzurePrometheusClient(azure_config)
                logger.info("Azure Prometheus client initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize Azure Prometheus: {e}")

        if azure_config.grafana_enabled:
            try:
                azure_grafana_client = AzureGrafanaClient(azure_config)
                logger.info("Azure Grafana client initialized")

                # NOTE: Automatic creation disabled - using Azure Portal for setup
                # Create Prometheus data source in Grafana
                # if azure_config.prometheus_endpoint:
                #     try:
                #         azure_grafana_client.create_prometheus_datasource(
                #             azure_config.prometheus_endpoint
                #         )
                #         logger.info("Prometheus data source created in Azure Grafana")
                #     except Exception as e:
                #         logger.debug(f"Could not create data source in Grafana: {e}")

                # Create AIQ dashboard
                # try:
                #     azure_grafana_client.create_aiq_dashboard()
                #     logger.info("AIQ Backend dashboard created in Azure Grafana")
                # except Exception as e:
                #     logger.debug(f"Could not create dashboard in Grafana: {e}")
                
                logger.info("Azure Grafana configured (manual setup via Azure Portal)")
            except Exception as e:
                logger.warning(f"Failed to initialize Azure Grafana: {e}")

        if azure_config.app_insights_enabled:
            try:
                app_insights_client = ApplicationInsightsClient(azure_config)
                if app_insights_client.initialized:
                    logger.info("Application Insights client initialized")
                else:
                    logger.warning("Application Insights client initialized but not fully configured")
            except Exception as e:
                logger.warning(f"Failed to initialize Application Insights: {e}")

        logger.info("Azure monitoring initialization completed")
    except Exception as e:
        logger.error(f"Failed to initialize Azure monitoring: {str(e)}")


def get_azure_config() -> Optional[AzureMonitoringConfig]:
    """Get Azure monitoring configuration."""
    return azure_config


def get_azure_prometheus_client() -> Optional[AzurePrometheusClient]:
    """Get Azure Prometheus client."""
    return azure_prometheus_client


def get_azure_grafana_client() -> Optional[AzureGrafanaClient]:
    """Get Azure Grafana client."""
    return azure_grafana_client


def get_app_insights_client() -> Optional[ApplicationInsightsClient]:
    """Get Application Insights client."""
    return app_insights_client