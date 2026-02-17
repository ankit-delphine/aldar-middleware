"""Token blacklist service for managing invalidated JWT tokens."""

import time
from typing import Set, Optional
from loguru import logger


class TokenBlacklist:
    """In-memory token blacklist for managing invalidated tokens."""
    
    def __init__(self):
        """Initialize token blacklist."""
        # Store blacklisted tokens with their expiration time
        self._blacklisted_tokens: Set[str] = set()
        self._token_expiry_times: dict[str, float] = {}
    
    def blacklist_token(self, token: str, expiry_time: Optional[float] = None):
        """Add a token to the blacklist.
        
        Args:
            token: The JWT token to blacklist
            expiry_time: The expiry time of the token (unix timestamp)
        """
        self._blacklisted_tokens.add(token)
        
        if expiry_time:
            # Store the expiry time for cleanup
            self._token_expiry_times[token] = expiry_time
        
        logger.info(f"Token blacklisted: {token[:20]}...")
    
    def is_blacklisted(self, token: str) -> bool:
        """Check if a token is blacklisted.
        
        Args:
            token: The JWT token to check
            
        Returns:
            True if token is blacklisted, False otherwise
        """
        if token in self._blacklisted_tokens:
            # Check if token has expired
            expiry_time = self._token_expiry_times.get(token)
            if expiry_time and time.time() > expiry_time:
                # Token has expired, remove from blacklist
                self._blacklisted_tokens.discard(token)
                self._token_expiry_times.pop(token, None)
                logger.debug(f"Expired token removed from blacklist: {token[:20]}...")
                return False
            return True
        return False
    
    def remove_expired_tokens(self):
        """Remove expired tokens from blacklist."""
        current_time = time.time()
        tokens_to_remove = []
        
        for token, expiry_time in self._token_expiry_times.items():
            if current_time > expiry_time:
                tokens_to_remove.append(token)
        
        for token in tokens_to_remove:
            self._blacklisted_tokens.discard(token)
            self._token_expiry_times.pop(token, None)
        
        if tokens_to_remove:
            logger.debug(f"Removed {len(tokens_to_remove)} expired tokens from blacklist")
    
    def clear_all(self):
        """Clear all blacklisted tokens."""
        count = len(self._blacklisted_tokens)
        self._blacklisted_tokens.clear()
        self._token_expiry_times.clear()
        logger.info(f"Cleared all {count} blacklisted tokens")
    
    def get_stats(self) -> dict:
        """Get blacklist statistics.
        
        Returns:
            Dictionary with blacklist statistics
        """
        self.remove_expired_tokens()
        return {
            "total_blacklisted": len(self._blacklisted_tokens),
            "active_blacklisted": len([t for t, exp in self._token_expiry_times.items() 
                                      if time.time() < exp])
        }


# Global token blacklist instance
token_blacklist = TokenBlacklist()

