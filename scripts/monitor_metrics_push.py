#!/usr/bin/env python3
"""
Real-time monitor of metrics being pushed to Azure
Shows exactly which metrics are being sent and their values
"""

import os
import sys
import time
import asyncio
import aiohttp
import logging
from collections import defaultdict
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MetricsMonitor:
    def __init__(self):
        self.backend_host = os.getenv("ALDAR_HOST", "localhost")
        self.backend_port = os.getenv("ALDAR_PORT", "8000")
        self.metrics_url = f"http://{self.backend_host}:{self.backend_port}/metrics"
        self.previous_metrics = {}
        self.metric_counts = defaultdict(int)
    
    async def fetch_metrics(self):
        """Fetch current metrics"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.metrics_url, timeout=5) as response:
                    if response.status == 200:
                        return await response.text()
        except Exception as e:
            logger.error(f"Error fetching metrics: {e}")
        return None
    
    def parse_metrics(self, metrics_text):
        """Parse Prometheus metrics format"""
        metrics = {}
        for line in metrics_text.split('\n'):
            if line and not line.startswith('#'):
                # Parse: metric_name{labels} value
                try:
                    parts = line.split(' ')
                    if len(parts) >= 2:
                        metric_key = parts[0]
                        value = float(parts[-1])
                        metrics[metric_key] = value
                except:
                    pass
        return metrics
    
    def show_metric_changes(self, current_metrics):
        """Show which metrics changed"""
        changes = []
        
        for metric, value in current_metrics.items():
            metric_name = metric.split('{')[0]
            previous_value = self.previous_metrics.get(metric, 0)
            
            if value != previous_value:
                change = value - previous_value
                if change > 0:
                    changes.append({
                        'name': metric_name,
                        'full': metric,
                        'previous': previous_value,
                        'current': value,
                        'change': change
                    })
                    self.metric_counts[metric_name] += 1
        
        return changes
    
    async def monitor(self, interval=5):
        """Monitor metrics in real-time"""
        logger.info("=" * 80)
        logger.info("📊 REAL-TIME METRICS MONITOR")
        logger.info("=" * 80)
        logger.info(f"Backend: {self.metrics_url}")
        logger.info(f"Update interval: {interval}s")
        logger.info("Press Ctrl+C to stop\n")
        
        await asyncio.sleep(2)
        
        iteration = 0
        while True:
            try:
                iteration += 1
                metrics_text = await self.fetch_metrics()
                
                if not metrics_text:
                    logger.warning("⚠️  Could not fetch metrics")
                    await asyncio.sleep(interval)
                    continue
                
                current_metrics = self.parse_metrics(metrics_text)
                changes = self.show_metric_changes(current_metrics)
                
                self.previous_metrics = current_metrics
                
                if changes:
                    logger.info(f"\n[{iteration}] 📈 Metrics Updated ({len(changes)} changes)")
                    logger.info("-" * 80)
                    
                    # Group by metric name
                    by_name = defaultdict(list)
                    for change in changes:
                        by_name[change['name']].append(change)
                    
                    for metric_name in sorted(by_name.keys()):
                        items = by_name[metric_name]
                        for item in items:
                            logger.info(f"  📊 {item['name']}")
                            logger.info(f"     Label: {item['full'].split('{')[1].split('}')[0] if '{' in item['full'] else 'no-labels'}")
                            logger.info(f"     Change: {item['previous']:.0f} → {item['current']:.0f} (Δ {item['change']:+.0f})")
                else:
                    if iteration % 3 == 0:  # Show status every 3rd check
                        total_metrics = len(current_metrics)
                        logger.info(f"[{iteration}] ✅ Monitoring... ({total_metrics} metrics tracked, no changes)")
                
                await asyncio.sleep(interval)
            
            except KeyboardInterrupt:
                logger.info("\n" + "=" * 80)
                logger.info("📊 MONITORING SUMMARY")
                logger.info("=" * 80)
                
                if self.metric_counts:
                    logger.info("Metrics with activity (updates):")
                    for metric_name in sorted(self.metric_counts.keys(), 
                                            key=lambda x: self.metric_counts[x], 
                                            reverse=True):
                        count = self.metric_counts[metric_name]
                        logger.info(f"  • {metric_name}: {count} updates")
                else:
                    logger.info("No metrics updated during monitoring period")
                
                logger.info("\n✅ Monitoring stopped")
                break
            except Exception as e:
                logger.error(f"Error: {e}")
                await asyncio.sleep(interval)


async def main():
    monitor = MetricsMonitor()
    await monitor.monitor(interval=5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)