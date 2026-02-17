"""Utility functions for user operations."""

from typing import Optional
from aldar_middleware.models.user import User


def get_profile_photo_blob_path(user: User) -> Optional[str]:
    """Get profile photo blob path from user preferences.
    
    Args:
        user: User object
        
    Returns:
        Blob path string if available, None otherwise
    """
    if not user.preferences or not isinstance(user.preferences, dict):
        return None
    return user.preferences.get("profile_photo_blob_path")


def get_profile_photo_url(user: User, sas_token_expiry_hours: float = 24.0) -> Optional[str]:
    """Get profile photo URL from user preferences with SAS token.
    
    This function checks preferences for blob path and generates a SAS URL.
    
    Args:
        user: User object
        sas_token_expiry_hours: Number of hours until the SAS token expires (default: 24 hours)
        
    Returns:
        Profile photo SAS URL string if available, None otherwise
    """
    if not user.preferences or not isinstance(user.preferences, dict):
        return None
    
    # Check for blob path and generate SAS URL
    blob_path = user.preferences.get("profile_photo_blob_path")
    if blob_path:
        from aldar_middleware.utils.helpers import get_image_sas_url
        return get_image_sas_url(blob_path, sas_token_expiry_hours=sas_token_expiry_hours)
    
    return None


def set_profile_photo_blob_path(user: User, blob_path: Optional[str]) -> None:
    """Set profile photo blob path in user preferences.
    
    Args:
        user: User object
        blob_path: Blob path to store, or None to remove
    """
    if user.preferences is None:
        user.preferences = {}
    elif not isinstance(user.preferences, dict):
        user.preferences = {}
    
    if blob_path:
        user.preferences["profile_photo_blob_path"] = blob_path
        # Also update the full URL in profile_photo for backward compatibility
        from aldar_middleware.orchestration.blob_storage import BlobStorageService
        from aldar_middleware.settings import settings
        try:
            blob_service = BlobStorageService()
            container_name = settings.azure_storage_container_name
            user.preferences["profile_photo"] = (
                f"https://{blob_service.client.account_name}.blob.core.windows.net/"
                f"{container_name}/{blob_path}"
            )
        except Exception:
            pass  # If blob service fails, just store the path
    else:
        user.preferences.pop("profile_photo_blob_path", None)
        user.preferences.pop("profile_photo", None)

