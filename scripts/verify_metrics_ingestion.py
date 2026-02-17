"""
Verification script for Azure Metrics Ingestion configuration.
Tests connectivity and authentication to Azure Monitor metrics ingestion endpoint.
"""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from aldar_middleware.settings import settings
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def check_configuration():
    """Check if Azure Metrics Ingestion is properly configured."""
    logger.info("=" * 60)
    logger.info("Azure Metrics Ingestion Configuration Verification")
    logger.info("=" * 60)

    # Check if enabled
    if not settings.azure_metrics_ingestion_enabled:
        logger.warning("❌ Azure Metrics Ingestion is DISABLED")
        logger.info("   To enable, set: ALDAR_AZURE_METRICS_INGESTION_ENABLED=true")
        return False

    logger.info("✓ Azure Metrics Ingestion is ENABLED")

    # Check required credentials
    checks = [
        ("Ingestion Endpoint", settings.azure_metrics_ingestion_endpoint),
        ("DCR Rule ID", settings.azure_metrics_dcr_rule_id),
        ("Azure Tenant ID", settings.azure_tenant_id),
        ("Azure Client ID", settings.azure_client_id),
        ("Azure Client Secret", "***" if settings.azure_client_secret else None),
    ]

    all_valid = True
    for check_name, check_value in checks:
        if check_value:
            logger.info(f"✓ {check_name}: Configured")
        else:
            logger.warning(f"❌ {check_name}: Missing")
            all_valid = False

    if not all_valid:
        logger.error("\nMissing configuration. Please set all required environment variables:")
        logger.error("  ALDAR_AZURE_METRICS_INGESTION_ENDPOINT")
        logger.error("  ALDAR_AZURE_METRICS_DCR_RULE_ID")
        logger.error("  ALDAR_AZURE_TENANT_ID")
        logger.error("  ALDAR_AZURE_CLIENT_ID")
        logger.error("  ALDAR_AZURE_CLIENT_SECRET")
        return False

    logger.info("\n" + "=" * 60)
    logger.info("Testing Azure Authentication...")
    logger.info("=" * 60)

    try:
        from azure.identity import ClientSecretCredential

        credential = ClientSecretCredential(
            tenant_id=settings.azure_tenant_id,
            client_id=settings.azure_client_id,
            client_secret=settings.azure_client_secret,
        )

        # Try to get a token
        token = credential.get_token("https://monitor.azure.com/.default")
        logger.info("✓ Azure Authentication: SUCCESS")
        logger.info(f"  Token obtained successfully (expires: {token.expires_on})")

    except Exception as e:
        logger.error(f"❌ Azure Authentication: FAILED")
        logger.error(f"  Error: {str(e)}")
        return False

    logger.info("\n" + "=" * 60)
    logger.info("Testing Metrics Client Initialization...")
    logger.info("=" * 60)

    try:
        from azure.monitor.ingestion import MetricsClient

        metrics_client = MetricsClient(
            endpoint=settings.azure_metrics_ingestion_endpoint,
            credential=credential,
        )
        logger.info("✓ Metrics Client: Initialized successfully")

    except Exception as e:
        logger.error(f"❌ Metrics Client: FAILED to initialize")
        logger.error(f"  Error: {str(e)}")
        return False

    logger.info("\n" + "=" * 60)
    logger.info("Testing Metrics Submission...")
    logger.info("=" * 60)

    try:
        from datetime import datetime, timezone

        # Prepare a test metric
        test_metric = {
            "resourceId": settings.azure_client_id,
            "time": datetime.now(timezone.utc).isoformat(),
            "data": {
                "baseData": {
                    "metric": "TestMetric",
                    "namespace": "CustomMetrics/AIQ",
                    "series": [
                        {
                            "min": 42,
                            "max": 42,
                            "sum": 42,
                            "count": 1,
                            "dimensions": {"Test": "true"},
                        }
                    ],
                }
            },
        }

        # Try to upload
        metrics_client.upload(
            rule_id=settings.azure_metrics_dcr_rule_id,
            stream_name=settings.azure_metrics_dcr_stream_name,
            logs=[test_metric],
        )

        logger.info("✓ Metrics Submission: SUCCESS")
        logger.info("  Test metric uploaded successfully")
        logger.info("\n✅ All checks passed! Azure Metrics Ingestion is ready.")
        return True

    except Exception as e:
        logger.error(f"❌ Metrics Submission: FAILED")
        logger.error(f"  Error: {str(e)}")
        logger.info("\nPossible causes:")
        logger.info("  1. DCR Rule ID is incorrect")
        logger.info("  2. Stream name doesn't match DCR configuration")
        logger.info("  3. Azure credentials don't have permission to submit metrics")
        logger.info("  4. Metrics ingestion endpoint is incorrect")
        return False


def main():
    """Run verification checks."""
    try:
        success = check_configuration()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"Verification failed with error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()