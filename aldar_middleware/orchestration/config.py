"""Configuration for external API integrations."""

from typing import Dict, Any, Optional
from pydantic import BaseModel, Field


class ExternalAPIConfig(BaseModel):
    """Configuration for external API integration."""
    
    # Agno Multiagent API
    agno_base_url: str = Field(default="https://agno-multiagent.onrender.com")
    agno_timeout: int = Field(default=30)
    agno_cache_ttl: int = Field(default=3600)  # 1 hour
    
    # OpenAI API
    openai_base_url: str = Field(default="https://api.openai.com/v1")
    openai_timeout: int = Field(default=30)
    openai_cache_ttl: int = Field(default=1800)  # 30 minutes
    
    # Anthropic API
    anthropic_base_url: str = Field(default="https://api.anthropic.com/v1")
    anthropic_timeout: int = Field(default=30)
    anthropic_cache_ttl: int = Field(default=1800)  # 30 minutes
    
    # General settings
    max_retries: int = Field(default=3)
    retry_delay: float = Field(default=1.0)
    enable_caching: bool = Field(default=True)
    enable_metrics: bool = Field(default=True)
    
    # Rate limiting
    rate_limit_per_minute: int = Field(default=60)
    rate_limit_burst: int = Field(default=10)


# Global configuration instance
external_api_config = ExternalAPIConfig()


def get_external_api_config() -> ExternalAPIConfig:
    """Get external API configuration."""
    return external_api_config


def update_external_api_config(config: Dict[str, Any]) -> None:
    """Update external API configuration."""
    global external_api_config
    external_api_config = ExternalAPIConfig(**config)
