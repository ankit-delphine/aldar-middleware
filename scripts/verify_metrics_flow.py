#!/usr/bin/env python3
"""
Verify metrics flow from backend → forwarder → Azure Grafana
"""

import os
import sys
import asyncio
import aiohttp
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MetricsVerifier:
    def __init__(self):
        self.backend_host = os.getenv("ALDAR_HOST", "localhost")
        self.backend_port = os.getenv("ALDAR_PORT", "8000")
        self.metrics_url = f"http://{self.backend_host}:{self.backend_port}/metrics"
        
        self.grafana_endpoint = os.getenv("ALDAR_AZURE_GRAFANA_ENDPOINT", "").strip()
        self.grafana_api_key = os.getenv("ALDAR_AZURE_GRAFANA_API_KEY")
    
    async def check_backend_metrics(self):
        """Check if backend is exposing metrics"""
        logger.info("=" * 60)
        logger.info("1️⃣  CHECKING BACKEND METRICS ENDPOINT")
        logger.info("=" * 60)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.metrics_url, timeout=5) as response:
                    if response.status == 200:
                        metrics = await response.text()
                        lines = metrics.split('\n')
                        metric_lines = [l for l in lines if l and not l.startswith('#')]
                        
                        logger.info(f"✅ Backend is responding at {self.metrics_url}")
                        logger.info(f"✅ Metrics collected: {len(metric_lines)} metrics")
                        
                        # Show sample metrics
                        logger.info("\n📊 Sample Metrics:")
                        for line in metric_lines[:10]:
                            logger.info(f"   {line}")
                        
                        return True
                    else:
                        logger.error(f"❌ Backend returned HTTP {response.status}")
                        return False
        except Exception as e:
            logger.error(f"❌ Cannot reach backend: {e}")
            logger.error(f"   Make sure backend is running at {self.metrics_url}")
            return False
    
    async def check_grafana_connection(self):
        """Check Grafana connectivity"""
        logger.info("\n" + "=" * 60)
        logger.info("2️⃣  CHECKING AZURE GRAFANA CONNECTION")
        logger.info("=" * 60)
        
        if not self.grafana_endpoint:
            logger.warning("⚠️  ALDAR_AZURE_GRAFANA_ENDPOINT not configured")
            return False
        
        if not self.grafana_api_key:
            logger.warning("⚠️  ALDAR_AZURE_GRAFANA_API_KEY not configured")
            return False
        
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {self.grafana_api_key}"}
                async with session.get(
                    f"{self.grafana_endpoint}/api/v1/health",
                    headers=headers,
                    timeout=10
                ) as response:
                    if response.status == 200:
                        logger.info(f"✅ Connected to Grafana: {self.grafana_endpoint}")
                        return True
                    else:
                        logger.error(f"❌ Grafana returned HTTP {response.status}")
                        return False
        except Exception as e:
            logger.error(f"❌ Cannot reach Grafana: {e}")
            logger.error(f"   Endpoint: {self.grafana_endpoint}")
            return False
    
    async def check_forwarder_config(self):
        """Check metrics forwarder configuration"""
        logger.info("\n" + "=" * 60)
        logger.info("3️⃣  CHECKING METRICS FORWARDER CONFIGURATION")
        logger.info("=" * 60)
        
        required_vars = {
            "ALDAR_AZURE_TENANT_ID": os.getenv("ALDAR_AZURE_TENANT_ID"),
            "ALDAR_AZURE_CLIENT_ID": os.getenv("ALDAR_AZURE_CLIENT_ID"),
            "ALDAR_AZURE_CLIENT_SECRET": "***" if os.getenv("ALDAR_AZURE_CLIENT_SECRET") else None,
            "AZURE_METRICS_INGESTION_URL": os.getenv("AZURE_METRICS_INGESTION_URL"),
        }
        
        all_good = True
        for var, value in required_vars.items():
            if value:
                logger.info(f"✅ {var}: Configured")
            else:
                logger.error(f"❌ {var}: Missing")
                all_good = False
        
        push_interval = os.getenv("AZURE_METRICS_PUSH_INTERVAL", "60")
        logger.info(f"✅ AZURE_METRICS_PUSH_INTERVAL: {push_interval}s")
        
        return all_good
    
    async def run(self):
        """Run all verifications"""
        logger.info("\n🔍 METRICS FLOW VERIFICATION\n")
        
        results = {}
        results["backend"] = await self.check_backend_metrics()
        results["grafana"] = await self.check_grafana_connection()
        results["forwarder"] = await self.check_forwarder_config()
        
        logger.info("\n" + "=" * 60)
        logger.info("VERIFICATION SUMMARY")
        logger.info("=" * 60)
        
        status_map = {True: "✅", False: "❌"}
        logger.info(f"{status_map[results['backend']]} Backend Metrics: {'OK' if results['backend'] else 'FAILED'}")
        logger.info(f"{status_map[results['grafana']]} Grafana Connection: {'OK' if results['grafana'] else 'FAILED'}")
        logger.info(f"{status_map[results['forwarder']]} Forwarder Config: {'OK' if results['forwarder'] else 'FAILED'}")
        
        logger.info("\n" + "=" * 60)
        logger.info("NEXT STEPS")
        logger.info("=" * 60)
        
        if all(results.values()):
            logger.info("✅ All checks passed!")
            logger.info("\n1. Start the metrics forwarder in a separate terminal:")
            logger.info("   poetry run python scripts/azure_metrics_forwarder.py")
            logger.info("\n2. Watch for log messages like:")
            logger.info("   ✅ Fetched XXXXX bytes of metrics")
            logger.info("   ✅ Metrics sent successfully (HTTP 204)")
            logger.info("\n3. Go to Azure Grafana and check your dashboard:")
            logger.info(f"   {self.grafana_endpoint}")
        else:
            if not results["backend"]:
                logger.info("❌ Backend issue:")
                logger.info("   → Ensure backend is running: poetry run uvicorn aldar_middleware.application:get_app --reload")
            if not results["grafana"]:
                logger.info("❌ Grafana issue:")
                logger.info("   → Check Azure Grafana credentials in .env")
            if not results["forwarder"]:
                logger.info("❌ Forwarder config issue:")
                logger.info("   → Update .env with Azure AD and ingestion endpoint")
        
        return all(results.values())


async def main():
    verifier = MetricsVerifier()
    success = await verifier.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())