#!/usr/bin/env python3
"""
Azure Metrics Forwarder
Fetches metrics from the local backend and forwards them to Azure Monitor Prometheus
"""

import os
import sys
import time
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta
from msal import PublicClientApplication

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AzureMetricsForwarder:
    def __init__(self):
        # Azure AD Configuration
        self.tenant_id = os.getenv("ALDAR_AZURE_TENANT_ID")
        self.client_id = os.getenv("ALDAR_AZURE_CLIENT_ID")
        self.client_secret = os.getenv("ALDAR_AZURE_CLIENT_SECRET")
        
        # Backend Configuration
        self.backend_host = os.getenv("ALDAR_HOST", "localhost")
        self.backend_port = os.getenv("ALDAR_PORT", "8000")
        self.metrics_url = f"http://{self.backend_host}:{self.backend_port}/metrics"
        
        # Azure Ingestion Endpoint
        self.ingestion_url = os.getenv(
            "AZURE_METRICS_INGESTION_URL",
            "https://adq-monitor-napp.eastus-1.metrics.ingest.monitor.azure.com/dataCollectionRules/dcr-71ba7d2391a140c2bc0a93520e7a812e/streams/Microsoft-PrometheusMetrics/api/v1/write?api-version=2023-04-24"
        )
        
        # Token cache
        self.token = None
        self.token_expiry = None
        
        # MSAL app for authentication
        self.app = PublicClientApplication(
            client_id=self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}"
        )
        
        # Interval between metric pushes (in seconds)
        self.push_interval = int(os.getenv("AZURE_METRICS_PUSH_INTERVAL", "60"))
        
        logger.info(f"Initialized Azure Metrics Forwarder")
        logger.info(f"  Backend URL: {self.metrics_url}")
        logger.info(f"  Ingestion URL: {self.ingestion_url}")
        logger.info(f"  Push interval: {self.push_interval}s")
    
    def get_token(self):
        """Get a valid Azure AD token, refreshing if necessary"""
        now = datetime.now()
        
        # Return cached token if still valid (with 5 min buffer)
        if self.token and self.token_expiry and now < self.token_expiry - timedelta(minutes=5):
            return self.token
        
        try:
            logger.info("Acquiring Azure AD token...")
            result = self.app.acquire_token_by_client_credentials(
                scopes=["https://monitor.azure.com/.default"],
                client_secret=self.client_secret
            )
            
            if "access_token" in result:
                self.token = result["access_token"]
                # Token expires in 'expires_in' seconds
                expires_in = result.get("expires_in", 3600)
                self.token_expiry = now + timedelta(seconds=expires_in)
                logger.info(f"Token acquired successfully. Expires in {expires_in}s")
                return self.token
            else:
                logger.error(f"Failed to acquire token: {result.get('error_description', result)}")
                return None
        except Exception as e:
            logger.error(f"Error acquiring token: {e}")
            return None
    
    async def fetch_metrics(self, session):
        """Fetch metrics from the backend"""
        try:
            async with session.get(self.metrics_url, timeout=10) as response:
                if response.status == 200:
                    metrics = await response.text()
                    logger.info(f"Fetched {len(metrics)} bytes of metrics")
                    return metrics
                else:
                    logger.error(f"Failed to fetch metrics: HTTP {response.status}")
                    return None
        except asyncio.TimeoutError:
            logger.error("Timeout fetching metrics from backend")
            return None
        except Exception as e:
            logger.error(f"Error fetching metrics: {e}")
            return None
    
    async def send_metrics(self, session, metrics):
        """Send metrics to Azure ingestion endpoint"""
        token = self.get_token()
        if not token:
            logger.error("Cannot send metrics without valid token")
            return False
        
        try:
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "text/plain; charset=utf-8"
            }
            
            async with session.post(
                self.ingestion_url,
                data=metrics,
                headers=headers,
                timeout=30
            ) as response:
                if response.status in [200, 201, 202, 204]:
                    logger.info(f"Metrics sent successfully (HTTP {response.status})")
                    return True
                else:
                    logger.error(f"Failed to send metrics: HTTP {response.status}")
                    error_text = await response.text()
                    logger.error(f"Response: {error_text[:200]}")
                    return False
        except asyncio.TimeoutError:
            logger.error("Timeout sending metrics to Azure")
            return False
        except Exception as e:
            logger.error(f"Error sending metrics: {e}")
            return False
    
    async def run(self):
        """Main loop to periodically fetch and forward metrics"""
        logger.info("Starting Azure Metrics Forwarder...")
        
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    logger.info("Fetching and forwarding metrics...")
                    metrics = await self.fetch_metrics(session)
                    
                    if metrics:
                        await self.send_metrics(session, metrics)
                    
                    logger.info(f"Waiting {self.push_interval}s before next push...")
                    await asyncio.sleep(self.push_interval)
                
                except KeyboardInterrupt:
                    logger.info("Shutting down...")
                    break
                except Exception as e:
                    logger.error(f"Unexpected error: {e}")
                    await asyncio.sleep(5)


def main():
    # Validate required environment variables
    required_vars = [
        "ALDAR_AZURE_TENANT_ID",
        "ALDAR_AZURE_CLIENT_ID",
        "ALDAR_AZURE_CLIENT_SECRET"
    ]
    
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        sys.exit(1)
    
    forwarder = AzureMetricsForwarder()
    
    try:
        asyncio.run(forwarder.run())
    except KeyboardInterrupt:
        logger.info("Shutdown complete")
        sys.exit(0)


if __name__ == "__main__":
    main()