"""Celery application configuration."""

import ssl
from celery import Celery
from aldar_middleware.settings import settings

# Ensure broker and backend URLs are strings and explicitly use Redis scheme
broker_url = str(settings.celery_broker_url_property)
result_backend_url = str(settings.celery_result_backend_property)

# Force Redis scheme if it's not already set
if not broker_url.startswith(('redis://', 'rediss://')):
    broker_url = f"redis://{broker_url}"
if not result_backend_url.startswith(('redis://', 'rediss://')):
    result_backend_url = f"redis://{result_backend_url}"

# For Azure Redis Cache (rediss://), add SSL certificate requirements to the URL
if result_backend_url.startswith('rediss://'):
    # Add ssl_cert_reqs parameter to the URL for Azure Redis Cache
    separator = '&' if '?' in result_backend_url else '?'
    result_backend_url = f"{result_backend_url}{separator}ssl_cert_reqs=CERT_NONE"
if broker_url.startswith('rediss://'):
    separator = '&' if '?' in broker_url else '?'
    broker_url = f"{broker_url}{separator}ssl_cert_reqs=CERT_NONE"

# Create Celery app
celery_app = Celery(
    "aldar-middleware",
    broker=broker_url,
    backend=result_backend_url,
    include=[
        "aldar_middleware.queue.tasks",
    ]
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    task_soft_time_limit=25 * 60,  # 25 minutes
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    # Periodic task schedule (Celery Beat)
    beat_schedule={
        'check-agent-health-periodic': {
            'task': 'check_agent_health_periodic',
            'schedule': settings.agent_health_check_interval_minutes * 60.0,  # Configurable interval (in seconds)
        },
    },
)

# Redis configuration (using Redis as broker and result backend)
# Azure Service Bus will be used for message queuing via custom service
# Explicitly set transport to 'redis' to prevent auto-detection of Azure Service Bus
celery_app.conf.update(
    broker_transport="redis",  # Explicitly use Redis transport
    # Note: result_backend_transport is not a valid Celery config key
    # The backend transport is determined from the URL scheme (redis:// or rediss://)
    broker_transport_options={
        "visibility_timeout": 3600,
        "max_retries": 3,
        "prefetch_count": 1,
        # Azure Redis Cache connection settings
        "health_check_interval": 30,
        "socket_keepalive": True,
        "socket_keepalive_options": {},
        "retry_on_timeout": True,
        "socket_connect_timeout": 30,
        "socket_timeout": 30,
    },
    # Connection settings
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=10,
    # Result backend settings (same as broker for Redis)
    result_backend_transport_options={
        "retry_policy": {
            "timeout": 5.0
        },
        "health_check_interval": 30,
        "socket_keepalive": True,
        "socket_connect_timeout": 30,
        "socket_timeout": 30,
        # SSL settings for Azure Redis Cache
        "ssl_cert_reqs": ssl.CERT_NONE,  # Azure Redis Cache - no certificate verification needed
    },
)
