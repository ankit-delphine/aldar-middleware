"""Generic file upload API endpoints."""

import logging
from typing import List, Optional
from uuid import UUID
from urllib.parse import quote, urlparse, parse_qs
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from azure.core.exceptions import AzureError

from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.models.user import User
from aldar_middleware.database.base import get_db
from aldar_middleware.models.attachment import Attachment
from aldar_middleware.orchestration.blob_storage import BlobStorageService
from aldar_middleware.settings import settings
from aldar_middleware.settings.context import get_correlation_id
from aldar_middleware.monitoring.chat_cosmos_logger import log_conversation_share

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = File(..., description="File to upload"),
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    purpose: Optional[str] = Query(
        None, 
        description="Purpose/type of file: 'agent_icon', 'toggle_field_icon', 'dropdown_field_icon', 'dropdown_option_icon', etc. (for identification)"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a file and return attachment ID.
    
    This is a generic upload endpoint that can be used by:
    - Agent icons (entity_type='agent', entity_id=agent_id)
    - Chat images (entity_type='chat', entity_id=chat_id)
    - Feedback files (entity_type='feedback', entity_id=feedback_id)
    - Any other file uploads
    
    **Flow:**
    1. Upload file to blob storage
    2. Save metadata to attachments table
    3. Return attachment_id
    4. Use attachment_id in other APIs (agent creation, chat, etc.)
    
    **Benefits:**
    - Files are uploaded independently
    - If agent creation fails, files are still available
    - Same upload API can be reused everywhere
    - Files can be referenced later using attachment_id
    
    **Returns:**
    ```json
    {
      "success": true,
      "attachment_id": "uuid-here",
      "file_name": "image.png",
      "file_size": 12345,
      "content_type": "image/png",
      "blob_url": "https://...",
      "created_at": "2025-11-05T..."
    }
    ```
    """
    correlation_id = get_correlation_id()
    user_id = str(current_user.id)
    
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File name is required"
        )
    
    try:
        # Read file content
        file_content = await file.read()
        file_size = len(file_content)
        
        if file_size == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File is empty"
            )
        
        # Initialize blob storage service
        try:
            blob_service = BlobStorageService(container_name=settings.azure_storage_container_name)
        except ValueError as e:
            logger.error(f"Blob storage not configured: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="File upload service is not available. Please configure Azure Storage."
            )
        
        file_extension = file.filename.split(".")[-1].lower() if "." in file.filename else ""
        content_type = file.content_type or "application/octet-stream"
        image_extensions = {"png", "jpg", "jpeg"}

        # Determine upload path based on entity type
        if entity_type == "agent":
            # Use agent-specific upload method
            blob_url, blob_name, uploaded_size = await blob_service.upload_agent_icon(
                file_name=file.filename,
                file_content=file_content,
                content_type=content_type if content_type.startswith("image/") else "image/png",
                agent_id=entity_id or "temp",  # Will be updated later if entity_id provided
            )
        elif file_extension in image_extensions or content_type.startswith("image/"):
            # Image uploads use chat images container
            temp_entity_id = entity_id or "temp"
            blob_url, blob_name, uploaded_size = await blob_service.upload_chat_image(
                file_name=file.filename,
                file_content=file_content,
                content_type=content_type if content_type.startswith("image/") else "image/png",
                chat_id=temp_entity_id,  # Use entity_id as chat_id for generic uploads
                user_id=user_id,
            )
        else:
            # Generic attachment upload to support documents/text files
            blob_url, blob_name, uploaded_size = await blob_service.upload_attachment_file(
                file_name=file.filename,
                file_content=file_content,
                content_type=content_type,
                user_id=user_id,
                entity_type=entity_type,
                entity_id=entity_id,
            )
        
        # For shared_pdf purpose, generate SAS URL with configurable expiration
        if purpose == "shared_pdf":
            # Generate SAS URL with expiration from settings (default: 1 hour, configurable for testing)
            expiry_hours = settings.shared_pdf_sas_token_expiry_hours
            blob_url = blob_service.generate_blob_access_url(
                blob_name=blob_name,
                visibility="public",
                expiry_hours=expiry_hours,
            )
            logger.info(
                f"Generated SAS URL with 1 hour expiration for shared_pdf",
                extra={
                    "correlation_id": correlation_id,
                    "user_id": user_id,
                    "blob_name": blob_name,
                    "purpose": purpose,
                },
            )
        
        # Save to attachments table
        attachment = Attachment(
            user_id=UUID(user_id),
            file_name=file.filename,
            file_size=uploaded_size,
            content_type=file.content_type or "application/octet-stream",
            blob_url=blob_url,
            blob_name=blob_name,
            entity_type=entity_type,
            entity_id=entity_id,
            is_active=True,
        )
        
        db.add(attachment)
        await db.flush()
        await db.refresh(attachment)
        await db.commit()  # Commit the transaction to save to database
        
        logger.info(
            f"File uploaded successfully",
            extra={
                "correlation_id": correlation_id,
                "attachment_id": str(attachment.id),
                "user_id": user_id,
                "file_name": file.filename,
                "file_size": uploaded_size,
            }
        )
        
        response_data = {
            "success": True,
            "attachment_id": str(attachment.id),
            "file_name": attachment.file_name,
            "file_size": attachment.file_size,
            "content_type": attachment.content_type,
            "blob_url": attachment.blob_url,
            "created_at": attachment.created_at.isoformat(),
        }
        
        # Include purpose in response if provided (for identification)
        if purpose:
            response_data["purpose"] = purpose
        
        # Log conversation share for shared_pdf uploads with entity_type=chat
        if purpose == "shared_pdf" and entity_type == "chat":
            log_conversation_share(
                chat_id=entity_id or str(attachment.id),
                session_id=entity_id or str(attachment.id),
                user_id=user_id,
                username=current_user.username or current_user.email,
                share_url=blob_url,
                visibility="public",
                format="pdf",
                correlation_id=correlation_id,
                email=current_user.email,
                role="ADMIN" if current_user.is_admin else "NORMAL",
                department=current_user.azure_department,
                user_entra_id=current_user.azure_ad_id,
            )
        
        return response_data
        
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to upload file: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload file: {str(e)}"
        )


@router.get("/{attachment_id}")
async def get_attachment(
    attachment_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get attachment details by ID.
    
    Returns attachment metadata including blob URL.
    """
    user_id = str(current_user.id)
    
    result = await db.execute(
        select(Attachment).where(
            Attachment.id == attachment_id,
            Attachment.is_active == True
        )
    )
    attachment = result.scalar_one_or_none()
    
    if not attachment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment not found"
        )
    
    # Check if user owns the attachment or is admin
    if str(attachment.user_id) != user_id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    return {
        "success": True,
        "attachment_id": str(attachment.id),
        "file_name": attachment.file_name,
        "file_size": attachment.file_size,
        "content_type": attachment.content_type,
        "blob_url": attachment.blob_url,
        "entity_type": attachment.entity_type,
        "entity_id": attachment.entity_id,
        "created_at": attachment.created_at.isoformat(),
    }


@router.get("/")
async def list_attachments(
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List attachments for the current user.
    
    Can filter by entity_type and entity_id.
    """
    user_id = str(current_user.id)
    
    query = select(Attachment).where(
        Attachment.user_id == UUID(user_id),
        Attachment.is_active == True
    )
    
    if entity_type:
        query = query.where(Attachment.entity_type == entity_type)
    
    if entity_id:
        query = query.where(Attachment.entity_id == entity_id)
    
    query = query.order_by(Attachment.created_at.desc()).limit(limit).offset(offset)
    
    result = await db.execute(query)
    attachments = result.scalars().all()
    
    return {
        "success": True,
        "attachments": [
            {
                "attachment_id": str(att.id),
                "file_name": att.file_name,
                "file_size": att.file_size,
                "content_type": att.content_type,
                "blob_url": att.blob_url,
                "entity_type": att.entity_type,
                "entity_id": att.entity_id,
                "created_at": att.created_at.isoformat(),
            }
            for att in attachments
        ],
        "total": len(attachments),
    }


def _check_sas_token_expiry(blob_url: str) -> bool:
    """
    Check if a SAS token in the blob URL has expired.
    
    Args:
        blob_url: The blob URL that may contain a SAS token
        
    Returns:
        True if token is expired or invalid, False if still valid or no token
    """
    try:
        parsed = urlparse(blob_url)
        params = parse_qs(parsed.query)
        
        # Check if URL has a SAS token (has 'se' parameter)
        if 'se' in params:
            expiry_str = params['se'][0]
            try:
                # Parse ISO 8601 format (Azure SAS tokens use this)
                expiry_str_clean = expiry_str.rstrip('Z')
                expiry_time = datetime.strptime(expiry_str_clean, '%Y-%m-%dT%H:%M:%S')
            except ValueError:
                # Fallback to Unix timestamp
                try:
                    expiry_timestamp = int(expiry_str)
                    expiry_time = datetime.fromtimestamp(expiry_timestamp)
                except (ValueError, TypeError):
                    # Invalid format, assume expired for safety
                    return True
            
            # Check if expired
            now = datetime.utcnow()
            if expiry_time < now:
                return True  # Expired
        
        return False  # Not expired or no token
    except Exception:
        # If we can't parse, assume not expired (don't block access)
        return False


@router.get("/{attachment_id}/download")
async def download_attachment(
    attachment_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Download an attachment by ID.
    
    For shared_pdf attachments with expired SAS tokens, access will be denied.
    """
    user_id = str(current_user.id)

    result = await db.execute(
        select(Attachment).where(
            Attachment.id == attachment_id,
            Attachment.is_active == True,
        )
    )
    attachment = result.scalar_one_or_none()

    if not attachment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment not found",
        )

    if str(attachment.user_id) != user_id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    
    # Check if SAS token has expired (for shared_pdf files)
    if attachment.blob_url and _check_sas_token_expiry(attachment.blob_url):
        logger.warning(
            "Attempted download of expired shared PDF",
            extra={
                "attachment_id": str(attachment.id),
                "user_id": user_id,
                "blob_url": attachment.blob_url[:100] + "..." if len(attachment.blob_url) > 100 else attachment.blob_url,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This shared document link has expired. Please request a new share link.",
        )

    if not attachment.blob_name:
        logger.error(
            "Attachment is missing blob reference",
            extra={
                "attachment_id": str(attachment.id),
                "user_id": user_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Attachment is missing blob reference",
        )

    try:
        blob_service = BlobStorageService(container_name=settings.azure_storage_container_name)
    except ValueError as exc:
        logger.error(
            "Blob storage not configured",
            extra={
                "attachment_id": str(attachment.id),
                "user_id": user_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="File storage service is not configured",
        ) from exc

    try:
        file_content = await blob_service.download_blob(attachment.blob_name)
    except FileNotFoundError as exc:
        logger.warning(
            "Attachment blob not found in storage",
            extra={
                "attachment_id": str(attachment.id),
                "user_id": user_id,
                "blob_name": attachment.blob_name,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment file not found",
        ) from exc
    except AzureError as exc:
        logger.error(
            "Failed to download attachment from blob storage",
            extra={
                "attachment_id": str(attachment.id),
                "user_id": user_id,
                "blob_name": attachment.blob_name,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to retrieve attachment from storage",
        ) from exc
    except Exception as exc:
        logger.error(
            "Unexpected error while downloading attachment",
            extra={
                "attachment_id": str(attachment.id),
                "user_id": user_id,
                "blob_name": attachment.blob_name,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error while retrieving attachment",
        ) from exc

    content_type = attachment.content_type or "application/octet-stream"

    quoted_filename = quote(attachment.file_name)
    content_disposition = f'attachment; filename="{attachment.file_name}"'
    if quoted_filename != attachment.file_name:
        content_disposition += f"; filename*=UTF-8''{quoted_filename}"

    headers = {
        "Content-Disposition": content_disposition,
    }

    return Response(content=file_content, media_type=content_type, headers=headers)

