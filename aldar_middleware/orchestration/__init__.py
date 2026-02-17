"""
Orchestration module for external API integrations.

This module contains all external third-party API integrations and orchestration services:
- AGNO Multiagent API
- MCP (Multiagent Control Protocol)
- Azure Service Bus
- Azure Key Vault
- Azure Blob Storage

All external API calls and orchestrations should be placed here.
"""

from aldar_middleware.orchestration.agno import agno_service
from aldar_middleware.orchestration.azure_service_bus import azure_service_bus
from aldar_middleware.orchestration.config import external_api_config

__all__ = [
    "agno_service",
    "azure_service_bus",
    "external_api_config",
]
