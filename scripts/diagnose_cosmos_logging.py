#!/usr/bin/env python3
"""
Diagnostic script to check Cosmos DB logging configuration.

Usage:
    poetry run python scripts/diagnose_cosmos_logging.py
"""
import asyncio
import sys
from loguru import logger


def print_section(title: str):
    """Print a section header."""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def print_status(check: str, passed: bool, details: str = ""):
    """Print a check status."""
    status = "✅" if passed else "❌"
    print(f"{status} {check}")
    if details:
        print(f"   → {details}")


async def main():
    """Run diagnostics."""
    print("\n🔍 Cosmos DB Logging Diagnostic Tool")
    print("=" * 60)
    
    # Check 1: Import settings
    print_section("1. Checking Settings Configuration")
    try:
        from aldar_middleware.settings import settings
        print_status("Settings module imported", True)
        
        print_status(
            "COSMOS_LOGGING_ENABLED",
            settings.cosmos_logging_enabled,
            f"Value: {settings.cosmos_logging_enabled}"
        )
        
        endpoint_configured = bool(settings.cosmos_endpoint)
        endpoint_valid = False
        if endpoint_configured:
            cs = settings.cosmos_endpoint
            endpoint_valid = "AccountEndpoint=" in cs and "AccountKey=" in cs
        
        print_status(
            "COSMOS_ENDPOINT configured",
            endpoint_configured,
            f"Set to: {settings.cosmos_endpoint[:50]}..." if endpoint_configured else "NOT SET - This is required!"
        )
        
        if endpoint_configured:
            print_status(
                "COSMOS_ENDPOINT format valid",
                endpoint_valid,
                "Valid connection string format" if endpoint_valid else 
                "INVALID - Must be: AccountEndpoint=https://...;AccountKey=...;"
            )
        
        print_status(
            "Database name",
            True,
            settings.cosmos_logging_database_name
        )
        
        print_status(
            "Container name",
            True,
            settings.cosmos_logging_container_name
        )
        
        print_status(
            "Batch size",
            True,
            f"{settings.cosmos_logging_batch_size} logs"
        )
        
        print_status(
            "Flush interval",
            True,
            f"{settings.cosmos_logging_flush_interval} seconds"
        )
        
    except Exception as e:
        print_status("Settings module", False, str(e))
        return False
    
    # Check 2: Azure Cosmos SDK
    print_section("2. Checking Azure Cosmos SDK")
    try:
        import azure.cosmos
        print_status("azure-cosmos package installed", True, f"Version: {azure.cosmos.__version__}")
    except ImportError as e:
        print_status("azure-cosmos package", False, "NOT INSTALLED - Run: poetry add azure-cosmos")
        return False
    
    # Check 3: Cosmos Logger Module
    print_section("3. Checking Cosmos Logger Module")
    try:
        from aldar_middleware.monitoring.cosmos_logger import (
            initialize_cosmos_logging,
            CosmosLoggingConfig,
            CosmosLoggingHandler,
        )
        print_status("Cosmos logger module imported", True)
        
        # Check config validity
        config = CosmosLoggingConfig()
        config_valid = config.is_valid()
        print_status(
            "Cosmos logging config valid",
            config_valid,
            "Config is valid" if config_valid else "INVALID - Check COSMOS_ENDPOINT"
        )
        
    except Exception as e:
        print_status("Cosmos logger module", False, str(e))
        return False
    
    # Check 4: Try to initialize
    print_section("4. Testing Cosmos DB Connection")
    if not settings.cosmos_logging_enabled:
        print_status(
            "Cosmos logging initialization",
            False,
            "SKIPPED - COSMOS_LOGGING_ENABLED=false"
        )
        print("\n⚠️  MAIN ISSUE IDENTIFIED:")
        print("   Cosmos DB logging is DISABLED in your configuration!")
        print("\n📝 TO FIX:")
        print("   1. Add this to your .env file:")
        print("      COSMOS_LOGGING_ENABLED=true")
        print("   2. Restart your application")
        return False
    
    if not settings.cosmos_endpoint:
        print_status(
            "Cosmos logging initialization",
            False,
            "SKIPPED - COSMOS_ENDPOINT not configured"
        )
        print("\n⚠️  MAIN ISSUE IDENTIFIED:")
        print("   Cosmos DB endpoint is NOT configured!")
        print("\n📝 TO FIX:")
        print("   1. Get your connection string from Azure Portal:")
        print("      Azure Portal → Cosmos DB → Keys → PRIMARY CONNECTION STRING")
        print("   2. Add this to your .env file:")
        print("      COSMOS_ENDPOINT=AccountEndpoint=https://...;AccountKey=...;")
        print("   3. Restart your application")
        return False
    
    # Check connection string format
    cs = settings.cosmos_endpoint
    if "AccountEndpoint=" not in cs or "AccountKey=" not in cs:
        print_status(
            "Cosmos connection string format",
            False,
            f"INVALID FORMAT - Got: {cs[:50]}..."
        )
        print("\n⚠️  MAIN ISSUE IDENTIFIED:")
        print("   Cosmos DB connection string has INVALID format!")
        print(f"\n❌ Current value: {cs[:80]}...")
        print("\n✅ Required format:")
        print("   AccountEndpoint=https://YOUR-ACCOUNT.documents.azure.com:443/;AccountKey=YOUR-KEY==;")
        print("\n📝 TO FIX:")
        print("   1. Go to Azure Portal → Your Cosmos DB Account")
        print("   2. Click 'Keys' in the left menu")
        print("   3. Copy the FULL 'PRIMARY CONNECTION STRING' (not just the endpoint URL)")
        print("   4. Update your .env file:")
        print("      COSMOS_ENDPOINT=AccountEndpoint=https://...;AccountKey=...;")
        print("   5. Make sure to copy the ENTIRE connection string including AccountKey")
        print("   6. Restart your application")
        return False
    
    try:
        logger.info("Attempting to initialize Cosmos DB logging...")
        result = await initialize_cosmos_logging()
        
        if result:
            print_status(
                "Cosmos DB initialization",
                True,
                "Successfully connected and initialized!"
            )
            
            print_section("5. Testing Log Write")
            try:
                from aldar_middleware.monitoring import log_chat_session_created
                from aldar_middleware.context import get_correlation_id
                
                # Try to log a test event
                log_chat_session_created(
                    chat_id="diagnostic-test-123",
                    session_id="diag-session-123",
                    user_id="diagnostic-user",
                    username="diagnostic",
                    title="Diagnostic Test Chat",
                    agent_id="test-agent",
                    correlation_id="diag-correlation-id",
                )
                
                print_status(
                    "Test log generated",
                    True,
                    "Log function executed successfully"
                )
                
                print("\n⏱️  NOTE: Logs are batched!")
                print(f"   • Batch size: {settings.cosmos_logging_batch_size} logs")
                print(f"   • Flush interval: {settings.cosmos_logging_flush_interval} seconds")
                print(f"   • Your test log will appear in Cosmos DB within {settings.cosmos_logging_flush_interval} seconds")
                print(f"   • Or after {settings.cosmos_logging_batch_size} logs are accumulated")
                
            except Exception as e:
                print_status("Test log write", False, str(e))
                
        else:
            print_status(
                "Cosmos DB initialization",
                False,
                "Initialization returned False - check application logs for details"
            )
            return False
            
    except Exception as e:
        print_status("Cosmos DB initialization", False, str(e))
        print("\n⚠️  ERROR DETAILS:")
        print(f"   {str(e)}")
        return False
    
    # Final Summary
    print_section("✅ Diagnostic Summary")
    print("\n🎉 Cosmos DB logging is properly configured!")
    print("\n📊 To verify logs are being written:")
    print("   1. Make some API calls (e.g., create a chat session)")
    print(f"   2. Wait {settings.cosmos_logging_flush_interval}-10 seconds")
    print("   3. Check Azure Portal → Cosmos DB → Data Explorer")
    print(f"   4. Navigate to: {settings.cosmos_logging_database_name} → {settings.cosmos_logging_container_name} → Items")
    print("\n💡 TIP: If you don't see logs yet:")
    print("   • Make sure you're calling APIs that trigger logging")
    print("   • Check that the application was started AFTER setting COSMOS_LOGGING_ENABLED=true")
    print("   • Look for 'Cosmos DB logging initialized' in startup logs")
    
    return True


if __name__ == "__main__":
    try:
        result = asyncio.run(main())
        sys.exit(0 if result else 1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Diagnostic interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

