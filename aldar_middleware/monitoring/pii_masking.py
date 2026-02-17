"""PII (Personally Identifiable Information) masking service for logs and traces.

Provides configurable masking of sensitive data patterns based on environment settings.
"""

import re
import json
from typing import Any, Dict, Optional, Pattern
from loguru import logger

from aldar_middleware.settings import settings


class PIIMaskingConfig:
    """Configuration for PII masking."""
    
    def __init__(self):
        """Initialize PII masking configuration from settings."""
        self.enabled = settings.pii_masking_enabled
        self.mask_emails = settings.pii_mask_emails
        self.mask_phone_numbers = settings.pii_mask_phone_numbers
        self.mask_credit_cards = settings.pii_mask_credit_cards
        self.mask_tokens = settings.pii_mask_tokens
        self.mask_api_keys = settings.pii_mask_api_keys


class PIIMaskingService:
    """Service for masking personally identifiable information.
    
    Supports masking of:
    - Email addresses
    - Phone numbers
    - Credit card numbers
    - Authentication tokens and API keys
    - Custom patterns
    """
    
    # Regex patterns for common PII
    PATTERNS = {
        "email": re.compile(
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        ),
        "phone": re.compile(
            r'(?:(?:\+?1\s*(?:[.-]\s*)?)?(?:\(\s*([0-9]{3})\s*\)|([0-9]{3}))\s*(?:[.-]\s*)?)?([0-9]{3})\s*(?:[.-]\s*)?([0-9]{4})(?:\s*(?:#|x\.?|ext\.?|extension)\s*(\d+))?'
        ),
        "credit_card": re.compile(
            r'\b(?:\d[ -]*?){13,19}\b'
        ),
        "api_key": re.compile(
            r'(?i)(?:api[_-]?key|apikey|auth[_-]?token|access[_-]?token|bearer|token)\s*[:\s=]+\s*[\'"]?([^\s\'"\]}{]+)[\'"]?'
        ),
        "auth_token": re.compile(
            r'(?i)(?:authorization|auth)\s*[:\s=]+\s*(?:bearer|token)\s+[^\s]+'
        ),
        "jwt": re.compile(
            r'eyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*'
        ),
        "aws_key": re.compile(
            r'(?i)AKIA[0-9A-Z]{16}'
        ),
        "password": re.compile(
            r'(?i)(?:password|passwd|pwd)\s*[:\s=]+\s*[\'"]?([^\s\'"\]}{]+)[\'"]?'
        ),
        "connection_string": re.compile(
            r'(?i)(?:connection|conn|db)[_-]?string\s*[:\s=]+\s*[\'"]?([^\s\'"\]}{]+)[\'"]?'
        ),
        "ssn": re.compile(
            r'\b(?!000|666)[0-9]{3}-?(?!00)[0-9]{2}-?(?!0000)[0-9]{4}\b'
        ),
    }
    
    def __init__(self):
        """Initialize the PII masking service."""
        self.config = PIIMaskingConfig()
        self.masking_applied: Dict[str, bool] = {}
        self._update_masking_applied()
    
    def _update_masking_applied(self) -> None:
        """Update which patterns should be masked based on config."""
        self.masking_applied = {
            "email": self.config.mask_emails,
            "phone": self.config.mask_phone_numbers,
            "credit_card": self.config.mask_credit_cards,
            "api_key": self.config.mask_api_keys,
            "auth_token": self.config.mask_tokens,
            "jwt": self.config.mask_tokens,
            "aws_key": self.config.mask_tokens,
            "password": self.config.mask_api_keys,
            "connection_string": self.config.mask_api_keys,
            "ssn": self.config.mask_tokens,
        }
    
    def mask_string(self, value: str, preserve_length: bool = True) -> str:
        """Mask sensitive information in a string.
        
        Args:
            value: String to mask
            preserve_length: If True, replacement has same length as original
            
        Returns:
            Masked string
        """
        if not value or not isinstance(value, str):
            return value
        
        if not self.config.enabled:
            return value
        
        masked_value = value
        
        # Apply masking in order
        for pattern_name, pattern in self.PATTERNS.items():
            if not self.masking_applied.get(pattern_name, False):
                continue
            
            def replace_func(match: re.Match) -> str:
                matched_text = match.group(0)
                if preserve_length:
                    return "*" * len(matched_text)
                else:
                    return f"***{pattern_name}***"
            
            masked_value = pattern.sub(replace_func, masked_value)
        
        return masked_value
    
    def mask_dict(self, data: Dict[str, Any], preserve_length: bool = True) -> Dict[str, Any]:
        """Recursively mask sensitive information in a dictionary.
        
        Args:
            data: Dictionary to mask
            preserve_length: If True, replacement has same length as original
            
        Returns:
            Dictionary with masked values
        """
        if not data or not isinstance(data, dict):
            return data
        
        if not self.config.enabled:
            return data
        
        masked_dict = {}
        
        for key, value in data.items():
            if value is None:
                masked_dict[key] = value
            elif isinstance(value, str):
                masked_dict[key] = self.mask_string(value, preserve_length)
            elif isinstance(value, dict):
                masked_dict[key] = self.mask_dict(value, preserve_length)
            elif isinstance(value, list):
                masked_dict[key] = self.mask_list(value, preserve_length)
            else:
                masked_dict[key] = value
        
        return masked_dict
    
    def mask_list(self, data: list, preserve_length: bool = True) -> list:
        """Recursively mask sensitive information in a list.
        
        Args:
            data: List to mask
            preserve_length: If True, replacement has same length as original
            
        Returns:
            List with masked values
        """
        if not data or not isinstance(data, list):
            return data
        
        if not self.config.enabled:
            return data
        
        masked_list = []
        
        for item in data:
            if item is None:
                masked_list.append(item)
            elif isinstance(item, str):
                masked_list.append(self.mask_string(item, preserve_length))
            elif isinstance(item, dict):
                masked_list.append(self.mask_dict(item, preserve_length))
            elif isinstance(item, list):
                masked_list.append(self.mask_list(item, preserve_length))
            else:
                masked_list.append(item)
        
        return masked_list
    
    def mask_json_string(self, json_string: str, preserve_length: bool = True) -> str:
        """Mask sensitive information in a JSON string.
        
        Args:
            json_string: JSON string to mask
            preserve_length: If True, replacement has same length as original
            
        Returns:
            JSON string with masked values
        """
        if not json_string or not isinstance(json_string, str):
            return json_string
        
        if not self.config.enabled:
            return json_string
        
        try:
            data = json.loads(json_string)
            masked_data = self.mask_dict(data, preserve_length)
            return json.dumps(masked_data)
        except (json.JSONDecodeError, TypeError):
            # If it's not valid JSON, mask it as a string
            return self.mask_string(json_string, preserve_length)
    
    def mask_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        """Mask sensitive information in HTTP headers.
        
        Args:
            headers: HTTP headers to mask
            
        Returns:
            Dictionary with masked sensitive headers
        """
        if not headers or not isinstance(headers, dict):
            return headers
        
        if not self.config.enabled:
            return headers
        
        # Headers to mask
        sensitive_headers = {
            "authorization", "x-api-key", "x-auth-token", "cookie",
            "x-auth", "auth", "token", "api-key", "api_key",
            "x-token", "x-secret", "secret", "password",
        }
        
        masked_headers = {}
        
        for key, value in headers.items():
            if key.lower() in sensitive_headers:
                if value and isinstance(value, str):
                    # Mask the value but preserve structure if possible
                    if len(value) > 10:
                        masked_headers[key] = value[:5] + "*" * (len(value) - 10) + value[-5:]
                    else:
                        masked_headers[key] = "*" * len(value)
                else:
                    masked_headers[key] = value
            else:
                masked_headers[key] = value
        
        return masked_headers
    
    def should_mask(self, pattern_name: str) -> bool:
        """Check if a specific pattern should be masked.
        
        Args:
            pattern_name: Name of the pattern to check
            
        Returns:
            True if pattern should be masked, False otherwise
        """
        return self.config.enabled and self.masking_applied.get(pattern_name, False)
    
    def get_masking_config(self) -> Dict[str, bool]:
        """Get the current masking configuration.
        
        Returns:
            Dictionary of pattern names to masking flags
        """
        return self.masking_applied.copy()


# Global instance
_masking_service: Optional[PIIMaskingService] = None


def get_pii_masking_service() -> PIIMaskingService:
    """Get or create the global PII masking service.
    
    Returns:
        PIIMaskingService instance
    """
    global _masking_service
    
    if _masking_service is None:
        _masking_service = PIIMaskingService()
        logger.info(
            f"PII Masking Service initialized. "
            f"Masking enabled: {_masking_service.config.enabled}. "
            f"Patterns: {_masking_service.get_masking_config()}"
        )
    
    return _masking_service


def mask_string(value: str, preserve_length: bool = True) -> str:
    """Convenience function to mask a string.
    
    Args:
        value: String to mask
        preserve_length: If True, replacement has same length as original
        
    Returns:
        Masked string
    """
    return get_pii_masking_service().mask_string(value, preserve_length)


def mask_dict(data: Dict[str, Any], preserve_length: bool = True) -> Dict[str, Any]:
    """Convenience function to mask a dictionary.
    
    Args:
        data: Dictionary to mask
        preserve_length: If True, replacement has same length as original
        
    Returns:
        Dictionary with masked values
    """
    return get_pii_masking_service().mask_dict(data, preserve_length)


def mask_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Convenience function to mask HTTP headers.
    
    Args:
        headers: HTTP headers to mask
        
    Returns:
        Dictionary with masked sensitive headers
    """
    return get_pii_masking_service().mask_headers(headers)