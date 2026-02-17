"""Shared helper functions for routes and services."""

import logging
from typing import Optional, Dict, Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from aldar_middleware.models.attachment import Attachment

logger = logging.getLogger(__name__)


def is_uuid(value: str) -> bool:
    """
    Check if a string is a valid UUID.
    
    Args:
        value: String to check
        
    Returns:
        True if value is a valid UUID, False otherwise
    """
    if not value or not isinstance(value, str):
        return False
    try:
        UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


async def resolve_attachment_data(
    attachment_id: Optional[str], 
    db: AsyncSession
) -> Optional[Dict[str, Any]]:
    """
    Resolve attachment ID to full attachment data.
    
    Args:
        attachment_id: UUID string of the attachment
        db: Database session
        
    Returns:
        Dictionary with attachment data or None if not found/invalid
    """
    if not attachment_id or not is_uuid(attachment_id):
        return None
    try:
        result = await db.execute(
            select(Attachment).where(
                Attachment.id == UUID(attachment_id),
                Attachment.is_active == True
            )
        )
        attachment = result.scalar_one_or_none()
        if attachment:
            return {
                "attachment_id": str(attachment.id),
                "file_name": attachment.file_name,
                "file_size": attachment.file_size,
                "content_type": attachment.content_type,
                "blob_url": attachment.blob_url,
                "blob_name": attachment.blob_name,
                "entity_type": attachment.entity_type,
                "entity_id": attachment.entity_id,
                "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
            }
    except Exception as e:
        logger.warning(f"Failed to fetch attachment {attachment_id}: {str(e)}")
    return None


def get_image_sas_url(
    blob_path: str,
    sas_token_expiry_hours: float = 24.0,
    container_name: Optional[str] = None,
) -> Optional[str]:
    """
    Generate a SAS URL for an image stored in Azure Blob Storage.
    
    Args:
        blob_path: The blob path/name in Azure Storage (e.g., "profile-photos/user-id/photo.jpg")
        sas_token_expiry_hours: Number of hours until the SAS token expires (default: 24 hours)
        container_name: Optional container name. Defaults to settings.azure_storage_container_name
        
    Returns:
        SAS URL string if successful, None if blob storage is unavailable or fails
    """
    if not blob_path:
        return None
    
    try:
        from aldar_middleware.orchestration.blob_storage import BlobStorageService
        from aldar_middleware.settings import settings
        
        container = container_name or settings.azure_storage_container_name
        blob_service = BlobStorageService(container_name=container)
        
        # Use the internal _generate_sas_url method with custom expiry
        return blob_service._generate_sas_url(blob_path, expiry_hours=sas_token_expiry_hours)
    except Exception as e:
        logger.warning(f"Failed to generate SAS URL for blob '{blob_path}': {str(e)}")
        return None

