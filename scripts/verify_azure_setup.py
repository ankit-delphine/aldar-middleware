#!/usr/bin/env python3
"""
Verification script for Azure Monitoring Setup.

This script validates all Azure configurations without requiring the full app running.
Run with: poetry run python verify_azure_setup.py
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)


def check_env_variable(name: str, required: bool = True) -> tuple[bool, str]:
    """Check if an environment variable is set."""
    value = os.getenv(name)
    if value:
        # Mask sensitive values
        if "password" in name.lower() or "key" in name.lower() or "secret" in name.lower():
            masked = value[:10] + "..." + value[-5:] if len(value) > 15 else "***"
        else:
            masked = value
        return True, f"✅ {name} = {masked}"
    else:
        status = "❌ MISSING" if required else "⚠️  OPTIONAL"
        return not required, f"{status}: {name}"


def check_url_variable(name: str) -> tuple[bool, str]:
    """Check if a URL variable is set and valid."""
    value = os.getenv(name)
    if not value:
        return False, f"❌ MISSING: {name}"
    
    if not value.startswith(("http://", "https://")):
        return False, f"❌ INVALID URL: {name} = {value}"
    
    return True, f"✅ {name} = {value}"


def main():
    """Run all verification checks."""
    print("=" * 80)
    print("AIQ BACKEND - AZURE MONITORING SETUP VERIFICATION")
    print("=" * 80)
    print()

    all_passed = True

    # ========================================
    # 1. Database Configuration
    # ========================================
    print("1. DATABASE CONFIGURATION")
    print("-" * 80)
    
    db_checks = [
        ("ALDAR_DB_HOST", True),
        ("ALDAR_DB_PORT", True),
        ("ALDAR_DB_USER", True),
        ("ALDAR_DB_PASS", False),
        ("ALDAR_DB_BASE", True),
    ]
    
    for var, required in db_checks:
        passed, msg = check_env_variable(var, required)
        print(msg)
        if required and not passed:
            all_passed = False
    
    print()

    # ========================================
    # 2. Redis Configuration
    # ========================================
    print("2. REDIS CONFIGURATION (Azure Redis Cache)")
    print("-" * 80)
    
    redis_checks = [
        ("ALDAR_REDIS_HOST", True),
        ("ALDAR_REDIS_PORT", True),
        ("ALDAR_REDIS_PASSWORD", True),
        ("ALDAR_REDIS_DB", False),
    ]
    
    for var, required in redis_checks:
        passed, msg = check_env_variable(var, required)
        print(msg)
        if required and not passed:
            all_passed = False
    
    # Verify Redis URL format
    redis_url = os.getenv("ALDAR_REDIS_URL", "")
    if redis_url:
        if redis_url.startswith("rediss://"):
            print("✅ Redis using SSL (rediss:// scheme)")
        else:
            print("⚠️  Redis not using SSL (rediss:// scheme)")
    
    print()

    # ========================================
    # 3. Azure Monitoring - Prometheus
    # ========================================
    print("3. AZURE MONITORING - PROMETHEUS")
    print("-" * 80)
    
    prom_enabled = os.getenv("ALDAR_AZURE_PROMETHEUS_ENABLED", "false").lower() == "true"
    print(f"{'✅' if prom_enabled else '❌'} Azure Prometheus: {'ENABLED' if prom_enabled else 'DISABLED'}")
    
    if prom_enabled:
        passed, msg = check_url_variable("ALDAR_AZURE_PROMETHEUS_ENDPOINT")
        print(msg)
        if not passed:
            all_passed = False
        
        passed, msg = check_env_variable("ALDAR_AZURE_PROMETHEUS_WORKSPACE_ID", True)
        print(msg)
        if not passed:
            all_passed = False
    
    print()

    # ========================================
    # 4. Azure Monitoring - Grafana
    # ========================================
    print("4. AZURE MONITORING - GRAFANA")
    print("-" * 80)
    
    grafana_enabled = os.getenv("ALDAR_AZURE_GRAFANA_ENABLED", "false").lower() == "true"
    print(f"{'✅' if grafana_enabled else '❌'} Azure Grafana: {'ENABLED' if grafana_enabled else 'DISABLED'}")
    
    if grafana_enabled:
        passed, msg = check_url_variable("ALDAR_AZURE_GRAFANA_ENDPOINT")
        print(msg)
        if not passed:
            all_passed = False
        
        passed, msg = check_env_variable("ALDAR_AZURE_GRAFANA_API_KEY", True)
        print(msg)
        if not passed:
            all_passed = False
    
    print()

    # ========================================
    # 5. Application Insights
    # ========================================
    print("5. AZURE APPLICATION INSIGHTS")
    print("-" * 80)
    
    appinsights_enabled = os.getenv("ALDAR_APP_INSIGHTS_ENABLED", "false").lower() == "true"
    print(f"{'✅' if appinsights_enabled else '❌'} Application Insights: {'ENABLED' if appinsights_enabled else 'DISABLED'}")
    
    if appinsights_enabled:
        conn_str = os.getenv("ALDAR_APP_INSIGHTS_CONNECTION_STRING", "")
        if conn_str and "InstrumentationKey=" in conn_str:
            key = conn_str.split("InstrumentationKey=")[1].split(";")[0]
            print(f"✅ Application Insights Connection String: ...{key[-10:]}")
        else:
            print("❌ MISSING or INVALID: ALDAR_APP_INSIGHTS_CONNECTION_STRING")
            all_passed = False
    
    print()

    # ========================================
    # 6. Cosmos DB Configuration
    # ========================================
    print("6. COSMOS DB CONFIGURATION")
    print("-" * 80)
    
    cosmos_checks = [
        ("ALDAR_COSMOS_ENDPOINT", True),
        ("ALDAR_COSMOS_KEY", False),
        ("ALDAR_COSMOS_DATABASE_NAME", False),
        ("ALDAR_COSMOS_CONTAINER_NAME", False),
    ]
    
    for var, required in cosmos_checks:
        passed, msg = check_env_variable(var, required)
        print(msg)
        if required and not passed:
            all_passed = False
    
    cosmos_logging_enabled = os.getenv("ALDAR_COSMOS_LOGGING_ENABLED", "false").lower() == "true"
    print(f"{'✅' if cosmos_logging_enabled else '⚠️'} Cosmos DB Logging: {'ENABLED' if cosmos_logging_enabled else 'DISABLED'}")
    
    print()

    # ========================================
    # 7. Azure Service Bus (Celery)
    # ========================================
    print("7. AZURE SERVICE BUS (Celery)")
    print("-" * 80)
    
    service_bus = os.getenv("ALDAR_SERVICE_BUS_CONNECTION_STRING", "")
    if service_bus:
        if "servicebus.windows.net" in service_bus:
            print("✅ Azure Service Bus Connection String configured")
        else:
            print("❌ INVALID: ALDAR_SERVICE_BUS_CONNECTION_STRING")
            all_passed = False
    else:
        print("❌ MISSING: ALDAR_SERVICE_BUS_CONNECTION_STRING")
        all_passed = False
    
    print()

    # ========================================
    # 8. Application Configuration
    # ========================================
    print("8. APPLICATION CONFIGURATION")
    print("-" * 80)
    
    app_checks = [
        ("ALDAR_HOST", False),
        ("ALDAR_PORT", False),
        ("ALDAR_ENVIRONMENT", False),
        ("ALDAR_LOG_LEVEL", False),
    ]
    
    for var, required in app_checks:
        passed, msg = check_env_variable(var, required)
        print(msg)
    
    print()

    # ========================================
    # Summary
    # ========================================
    print("=" * 80)
    if all_passed:
        print("✅ ALL CHECKS PASSED - Azure Setup is Complete!")
        print()
        print("Next steps:")
        print("1. Start the application: poetry run uvicorn aldar_middleware.application:get_app --reload")
        print("2. Wait 2-3 minutes for metrics to propagate to Azure")
        print("3. Access Azure Grafana: https://adq-grafa-a0haaud6fsd5hhat.nuae.grafana.azure.com")
        print("4. Access Azure Prometheus: https://adq-amw-g0bah9fdcsbcacbt.uaenorth.prometheus.monitor.azure.com")
        print("5. Access Application Insights: Azure Portal > Application Insights > adq-appinsights")
        return 0
    else:
        print("❌ SOME CHECKS FAILED - Review configuration above")
        print()
        print("Please fix the missing or invalid configuration values in your .env file")
        return 1


if __name__ == "__main__":
    sys.exit(main())