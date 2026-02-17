"""Azure Blob Storage service for feedback files."""

import logging
from io import BytesIO
from typing import Optional, Tuple, Literal
from datetime import datetime, timedelta
from uuid import uuid4

from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions, ContentSettings
from azure.core.exceptions import AzureError, ResourceNotFoundError

from aldar_middleware.settings import settings
from aldar_middleware.settings.context import get_correlation_id

logger = logging.getLogger(__name__)


class BlobStorageService:
    """Service for managing file uploads to Azure Blob Storage."""

    def __init__(self, container_name: Optional[str] = None) -> None:
        """Initialize blob storage service.
        
        Args:
            container_name: Optional container name. Defaults to feedback container.
        """
        # Check if we have connection string or separate account name/key
        if settings.azure_storage_connection_string:
            # Use connection string
            self.connection_string = settings.azure_storage_connection_string
            self.client = BlobServiceClient.from_connection_string(
                self.connection_string
            )
        elif settings.azure_storage_account_name and settings.azure_storage_account_key:
            # Build connection string from account name and key
            self.connection_string = (
                f"DefaultEndpointsProtocol=https;"
                f"AccountName={settings.azure_storage_account_name};"
                f"AccountKey={settings.azure_storage_account_key};"
                f"EndpointSuffix=core.windows.net"
            )
            self.client = BlobServiceClient.from_connection_string(
                self.connection_string
            )
        else:
            raise ValueError("Azure Storage connection string or account name/key not configured")

        self.container_name = container_name or settings.feedback_blob_container_name
        self.max_file_size = settings.feedback_max_file_size_mb * 1024 * 1024
        self.allowed_extensions = set(settings.feedback_allowed_extensions)
        self.sas_token_expiry_hours = settings.feedback_sas_token_expiry_hours

    async def upload_feedback_file(
        self,
        file_name: str,
        file_content: bytes,
        content_type: str,
        feedback_id: str,
        user_id: str,
    ) -> Tuple[str, str, int]:
        """
        Upload a feedback file to Azure Blob Storage.

        Args:
            file_name: Original file name
            file_content: File content as bytes
            content_type: MIME type of file
            feedback_id: Feedback ID for organizing files
            user_id: User ID for organizing files

        Returns:
            Tuple of (file_url, blob_name, file_size)

        Raises:
            ValueError: If file is invalid
            AzureError: If upload fails
        """
        correlation_id = get_correlation_id()
        
        try:
            # Validate file
            self._validate_file(file_name, file_content, content_type)

            # Generate unique blob name
            file_extension = file_name.split(".")[-1].lower()
            unique_id = str(uuid4())
            
            # SECURITY: Sanitize file name to prevent path traversal attacks
            safe_file_name = file_name.replace("\\", "").replace("/", "").replace("..", "")
            safe_file_name = "".join(c for c in safe_file_name if c.isalnum() or c in "._-")
            if not safe_file_name:
                safe_file_name = "file"
            
            blob_name = f"feedback/{user_id}/{feedback_id}/{unique_id}_{safe_file_name}"

            logger.info(
                f"Uploading feedback file",
                extra={
                    "correlation_id": correlation_id,
                    "feedback_id": feedback_id,
                    "user_id": user_id,
                    "blob_name": blob_name,
                    "file_size": len(file_content),
                },
            )

            # Get container client
            container_client = self.client.get_container_client(self.container_name)

            # Upload blob
            blob_client = container_client.get_blob_client(blob_name)
            blob_client.upload_blob(
                file_content,
                overwrite=False,
                content_settings=ContentSettings(content_type=content_type),
            )

            # Generate SAS URL
            file_url = self._generate_sas_url(blob_name)

            logger.info(
                f"Feedback file uploaded successfully",
                extra={
                    "correlation_id": correlation_id,
                    "feedback_id": feedback_id,
                    "blob_name": blob_name,
                    "file_size": len(file_content),
                },
            )

            return file_url, blob_name, len(file_content)

        except ValueError as e:
            logger.warning(
                f"File validation failed: {str(e)}",
                extra={
                    "correlation_id": correlation_id,
                    "file_name": file_name,
                    "feedback_id": feedback_id,
                },
            )
            raise
        except AzureError as e:
            logger.error(
                f"Azure Blob Storage error: {str(e)}",
                extra={
                    "correlation_id": correlation_id,
                    "feedback_id": feedback_id,
                },
                exc_info=True,
            )
            raise

    async def upload_chat_image(
        self,
        file_name: str,
        file_content: bytes,
        content_type: str,
        chat_id: str,
        user_id: str,
    ) -> Tuple[str, str, int]:
        """
        Upload a chat image to Azure Blob Storage.

        Args:
            file_name: Original file name
            file_content: File content as bytes
            content_type: MIME type of file
            chat_id: Chat ID for organizing files
            user_id: User ID for organizing files

        Returns:
            Tuple of (file_url, blob_name, file_size)

        Raises:
            ValueError: If file is invalid
            AzureError: If upload fails
        """
        correlation_id = get_correlation_id()
        
        try:
            # Validate image file
            self._validate_image(file_name, file_content, content_type)

            # Generate unique blob name
            file_extension = file_name.split(".")[-1].lower()
            unique_id = str(uuid4())
            
            # SECURITY: Sanitize file name to prevent path traversal attacks
            safe_file_name = file_name.replace("\\", "").replace("/", "").replace("..", "")
            safe_file_name = "".join(c for c in safe_file_name if c.isalnum() or c in "._-")
            if not safe_file_name:
                safe_file_name = "file"
            
            blob_name = f"chat-images/{user_id}/{chat_id}/{unique_id}_{safe_file_name}"

            logger.info(
                f"Uploading chat image",
                extra={
                    "correlation_id": correlation_id,
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "blob_name": blob_name,
                    "file_size": len(file_content),
                },
            )

            # Ensure container exists
            container_client = self.client.get_container_client(self.container_name)
            try:
                # Check if container exists
                container_client.get_container_properties()
                logger.debug(f"Container '{self.container_name}' already exists")
            except ResourceNotFoundError:
                # Container doesn't exist, create it
                try:
                    container_client.create_container()
                    logger.info(f"Created container: {self.container_name}")
                except Exception as e:
                    logger.error(
                        f"Failed to create container '{self.container_name}': {str(e)}",
                        exc_info=True
                    )
                    raise AzureError(f"Container '{self.container_name}' does not exist and could not be created") from e
            except Exception as e:
                logger.warning(
                    f"Error checking container '{self.container_name}': {str(e)}",
                    exc_info=True
                )
                # Continue anyway - might be a permission issue but container exists

            # Upload blob
            blob_client = container_client.get_blob_client(blob_name)
            blob_client.upload_blob(
                file_content,
                overwrite=False,
                content_settings=ContentSettings(content_type=content_type),
            )

            # Generate plain URL (without SAS token)
            file_url = self._generate_plain_url(blob_name)

            logger.info(
                f"Chat image uploaded successfully",
                extra={
                    "correlation_id": correlation_id,
                    "chat_id": chat_id,
                    "blob_name": blob_name,
                    "file_size": len(file_content),
                },
            )

            return file_url, blob_name, len(file_content)

        except ValueError as e:
            logger.warning(
                f"Image validation failed: {str(e)}",
                extra={
                    "correlation_id": correlation_id,
                    "file_name": file_name,
                    "chat_id": chat_id,
                },
            )
            raise
        except AzureError as e:
            logger.error(
                f"Azure Blob Storage error: {str(e)}",
                extra={
                    "correlation_id": correlation_id,
                    "chat_id": chat_id,
                },
                exc_info=True,
            )
            raise

    async def upload_agent_icon(
        self,
        file_name: str,
        file_content: bytes,
        content_type: str,
        agent_id: str,
    ) -> Tuple[str, str, int]:
        """
        Upload an agent icon to Azure Blob Storage.

        Args:
            file_name: Original file name
            file_content: File content as bytes
            content_type: MIME type of file
            agent_id: Agent ID for organizing files

        Returns:
            Tuple of (file_url, blob_name, file_size)

        Raises:
            ValueError: If file is invalid
            AzureError: If upload fails
        """
        correlation_id = get_correlation_id()
        
        try:
            # Validate image file
            self._validate_image(file_name, file_content, content_type)

            # Generate unique blob name
            file_extension = file_name.split(".")[-1].lower()
            unique_id = str(uuid4())
            
            # SECURITY: Sanitize file name to prevent path traversal attacks
            safe_file_name = file_name.replace("\\", "").replace("/", "").replace("..", "")
            safe_file_name = "".join(c for c in safe_file_name if c.isalnum() or c in "._-")
            if not safe_file_name:
                safe_file_name = "file"
            
            blob_name = f"agent-icons/{agent_id}/{unique_id}_{safe_file_name}"

            logger.info(
                f"Uploading agent icon",
                extra={
                    "correlation_id": correlation_id,
                    "agent_id": agent_id,
                    "blob_name": blob_name,
                    "file_size": len(file_content),
                },
            )

            # Ensure container exists
            container_client = self.client.get_container_client(self.container_name)
            try:
                # Check if container exists
                container_client.get_container_properties()
                logger.debug(f"Container '{self.container_name}' already exists")
            except ResourceNotFoundError:
                # Container doesn't exist, create it
                try:
                    container_client.create_container()
                    logger.info(f"Created container: {self.container_name}")
                except Exception as e:
                    logger.error(
                        f"Failed to create container '{self.container_name}': {str(e)}",
                        exc_info=True
                    )
                    raise AzureError(f"Container '{self.container_name}' does not exist and could not be created") from e
            except Exception as e:
                logger.warning(
                    f"Error checking container '{self.container_name}': {str(e)}",
                    exc_info=True
                )
                # Continue anyway - might be a permission issue but container exists

            # Upload blob
            blob_client = container_client.get_blob_client(blob_name)
            blob_client.upload_blob(
                file_content,
                overwrite=False,
                content_settings=ContentSettings(content_type=content_type),
            )

            # Generate plain URL (without SAS token)
            file_url = self._generate_plain_url(blob_name)

            logger.info(
                f"Agent icon uploaded successfully",
                extra={
                    "correlation_id": correlation_id,
                    "agent_id": agent_id,
                    "blob_name": blob_name,
                    "file_size": len(file_content),
                },
            )

            return file_url, blob_name, len(file_content)

        except ValueError as e:
            logger.warning(
                f"Image validation failed: {str(e)}",
                extra={
                    "correlation_id": correlation_id,
                    "file_name": file_name,
                    "agent_id": agent_id,
                },
            )
            raise
        except AzureError as e:
            logger.error(
                f"Azure Blob Storage error: {str(e)}",
                extra={
                    "correlation_id": correlation_id,
                    "agent_id": agent_id,
                },
                exc_info=True,
            )
            raise

    async def upload_profile_photo(
        self,
        file_content: bytes,
        user_id: str,
        overwrite: bool = True,
    ) -> Tuple[str, str, int]:
        """
        Upload a user profile photo to Azure Blob Storage.

        Args:
            file_content: Photo content as bytes
            user_id: User ID (internal UUID)
            overwrite: Whether to overwrite existing photo (default: True)

        Returns:
            Tuple of (file_url, blob_name, file_size)

        Raises:
            ValueError: If file is invalid
            AzureError: If upload fails
        """
        correlation_id = get_correlation_id()
        
        try:
            # Validate image
            if len(file_content) == 0:
                raise ValueError("Profile photo is empty")
            
            max_image_size = 5 * 1024 * 1024  # 5MB
            if len(file_content) > max_image_size:
                raise ValueError(
                    f"Profile photo size ({len(file_content)} bytes) exceeds maximum ({max_image_size} bytes)"
                )

            # Generate blob name - use consistent path per user
            blob_name = f"profile-photos/{user_id}/photo.jpg"

            # Use main storage container for profile photos
            container_name = settings.azure_storage_container_name

            logger.info(
                f"Uploading profile photo",
                extra={
                    "correlation_id": correlation_id,
                    "user_id": user_id,
                    "blob_name": blob_name,
                    "file_size": len(file_content),
                },
            )

            # Ensure container exists
            container_client = self.client.get_container_client(container_name)
            try:
                container_client.get_container_properties()
                logger.debug(f"Container '{container_name}' already exists")
            except ResourceNotFoundError:
                try:
                    container_client.create_container()
                    logger.info(f"Created container: {container_name}")
                except Exception as e:
                    logger.error(
                        f"Failed to create container '{container_name}': {str(e)}",
                        exc_info=True
                    )
                    raise AzureError(f"Container '{container_name}' does not exist and could not be created") from e
            except Exception as e:
                logger.warning(
                    f"Error checking container '{container_name}': {str(e)}",
                    exc_info=True
                )

            # Upload blob (overwrite if exists)
            blob_client = container_client.get_blob_client(blob_name)
            blob_client.upload_blob(
                file_content,
                overwrite=overwrite,
                content_settings=ContentSettings(content_type="image/jpeg"),
            )

            # Generate plain URL (public access) - need to use correct container
            file_url = (
                f"https://{self.client.account_name}.blob.core.windows.net/"
                f"{container_name}/{blob_name}"
            )

            logger.info(
                f"Profile photo uploaded successfully",
                extra={
                    "correlation_id": correlation_id,
                    "user_id": user_id,
                    "blob_name": blob_name,
                    "file_size": len(file_content),
                },
            )

            return file_url, blob_name, len(file_content)

        except ValueError as e:
            logger.warning(
                f"Profile photo validation failed: {str(e)}",
                extra={
                    "correlation_id": correlation_id,
                    "user_id": user_id,
                },
            )
            raise
        except AzureError as e:
            logger.error(
                f"Azure Blob Storage error: {str(e)}",
                extra={
                    "correlation_id": correlation_id,
                    "user_id": user_id,
                },
                exc_info=True,
            )
            raise

    async def delete_blob(self, blob_name: str) -> bool:
        """
        Delete a blob from Azure Blob Storage.

        Args:
            blob_name: Azure blob path

        Returns:
            True if deleted successfully

        Raises:
            AzureError: If deletion fails
        """
        correlation_id = get_correlation_id()
        
        try:
            logger.info(
                f"Deleting blob",
                extra={
                    "correlation_id": correlation_id,
                    "blob_name": blob_name,
                },
            )

            container_client = self.client.get_container_client(self.container_name)
            blob_client = container_client.get_blob_client(blob_name)
            blob_client.delete_blob()

            logger.info(
                f"Blob deleted successfully",
                extra={
                    "correlation_id": correlation_id,
                    "blob_name": blob_name,
                },
            )

            return True

        except ResourceNotFoundError:
            logger.warning(
                "Blob not found (may have already been deleted)",
                extra={
                    "correlation_id": correlation_id,
                    "blob_name": blob_name,
                },
            )
            return True  # Consider it successful if already deleted
        except AzureError as e:
            logger.error(
                f"Failed to delete blob: {str(e)}",
                extra={
                    "correlation_id": correlation_id,
                    "blob_name": blob_name,
                },
                exc_info=True,
            )
            raise

    async def download_blob(self, blob_name: str) -> bytes:
        """
        Download a blob from Azure Blob Storage.

        Args:
            blob_name: Azure blob path

        Returns:
            Blob content as bytes

        Raises:
            FileNotFoundError: If blob is not found
            AzureError: If download fails
        """
        correlation_id = get_correlation_id()

        try:
            logger.info(
                "Downloading blob",
                extra={
                    "correlation_id": correlation_id,
                    "blob_name": blob_name,
                },
            )

            container_client = self.client.get_container_client(self.container_name)
            blob_client = container_client.get_blob_client(blob_name)
            downloader = blob_client.download_blob()
            blob_data = downloader.readall()

            logger.info(
                "Blob downloaded successfully",
                extra={
                    "correlation_id": correlation_id,
                    "blob_name": blob_name,
                    "blob_size": len(blob_data),
                },
            )

            return blob_data

        except ResourceNotFoundError:
            logger.warning(
                "Blob not found",
                extra={
                    "correlation_id": correlation_id,
                    "blob_name": blob_name,
                },
            )
            raise FileNotFoundError(f"Blob {blob_name} not found")
        except AzureError as e:
            logger.error(
                "Azure Blob Storage error while downloading blob",
                extra={
                    "correlation_id": correlation_id,
                    "blob_name": blob_name,
                },
                exc_info=True,
            )
            raise

    async def upload_attachment_file(
        self,
        file_name: str,
        file_content: bytes,
        content_type: str,
        user_id: str,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
    ) -> Tuple[str, str, int]:
        """
        Upload a generic attachment file to Azure Blob Storage.

        Args:
            file_name: Original file name
            file_content: File content as bytes
            content_type: MIME type of file
            user_id: User ID for organizing files
            entity_type: Optional entity type for folder structure
            entity_id: Optional entity id for folder structure

        Returns:
            Tuple of (file_url, blob_name, file_size)

        Raises:
            ValueError: If file is invalid
            AzureError: If upload fails
        """
        correlation_id = get_correlation_id()

        try:
            self._validate_file(file_name, file_content, content_type)

            file_extension = file_name.split(".")[-1].lower()
            unique_id = str(uuid4())
            entity_segment = entity_type or "general"
            entity_id_segment = entity_id or "unassigned"
            
            # SECURITY: Sanitize file name to prevent path traversal attacks
            # Remove any path separators and dangerous characters
            safe_file_name = file_name.replace("\\", "").replace("/", "").replace("..", "")
            safe_file_name = "".join(c for c in safe_file_name if c.isalnum() or c in "._-")
            if not safe_file_name:
                safe_file_name = "file"
            
            # SECURITY: Validate entity segments to prevent path traversal
            safe_entity_segment = "".join(c for c in entity_segment if c.isalnum() or c in "_-") if entity_segment else "general"
            safe_entity_id = "".join(c for c in entity_id_segment if c.isalnum() or c in "_-") if entity_id_segment else "unassigned"
            
            blob_name = (
                f"attachments/{user_id}/{safe_entity_segment}/{safe_entity_id}/"
                f"{unique_id}_{safe_file_name}"
            )

            logger.info(
                "Uploading attachment file",
                extra={
                    "correlation_id": correlation_id,
                    "user_id": user_id,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "blob_name": blob_name,
                    "file_size": len(file_content),
                },
            )

            # Ensure container exists before uploading
            container_client = self.client.get_container_client(self.container_name)
            try:
                # Check if container exists
                container_client.get_container_properties()
                logger.debug(f"Container '{self.container_name}' already exists")
            except ResourceNotFoundError:
                # Container doesn't exist, create it
                try:
                    container_client.create_container()
                    logger.info(f"Created container: {self.container_name}")
                except Exception as e:
                    logger.error(
                        f"Failed to create container '{self.container_name}': {str(e)}",
                        exc_info=True
                    )
                    raise AzureError(f"Container '{self.container_name}' does not exist and could not be created") from e
            except Exception as e:
                logger.warning(
                    f"Error checking container '{self.container_name}': {str(e)}",
                    exc_info=True
                )
                # Continue anyway - might be a permission issue but container exists

            blob_client = container_client.get_blob_client(blob_name)
            blob_client.upload_blob(
                file_content,
                overwrite=False,
                content_settings=ContentSettings(content_type=content_type),
            )

            file_url = self._generate_plain_url(blob_name)

            logger.info(
                "Attachment file uploaded successfully",
                extra={
                    "correlation_id": correlation_id,
                    "user_id": user_id,
                    "blob_name": blob_name,
                    "file_size": len(file_content),
                },
            )

            return file_url, blob_name, len(file_content)

        except ValueError as e:
            logger.warning(
                "File validation failed",
                extra={
                    "correlation_id": correlation_id,
                    "file_name": file_name,
                    "user_id": user_id,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                },
            )
            raise
        except AzureError as e:
            logger.error(
                "Azure Blob Storage error while uploading attachment",
                extra={
                    "correlation_id": correlation_id,
                    "user_id": user_id,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                },
                exc_info=True,
            )
            raise

    def generate_blob_access_url(
        self,
        blob_name: str,
        visibility: Literal["public", "private"] = "private",
        expiry_hours: Optional[int] = None,
    ) -> str:
        """
        Generate a blob access URL based on desired visibility.

        Args:
            blob_name: Azure blob path
            visibility: 'public' to generate a SAS URL, 'private' for plain URL
            expiry_hours: Optional override for SAS token expiry (defaults to settings)

        Returns:
            Blob URL string
        """
        if visibility == "public":
            hours = expiry_hours or self.sas_token_expiry_hours
            return self._generate_sas_url(blob_name, expiry_hours=hours)
        return self._generate_plain_url(blob_name)

    async def delete_feedback_file(self, blob_name: str) -> bool:
        """
        Delete a file from Azure Blob Storage.

        Args:
            blob_name: Azure blob path

        Returns:
            True if deleted successfully

        Raises:
            AzureError: If deletion fails
        """
        correlation_id = get_correlation_id()
        
        try:
            logger.info(
                f"Deleting feedback file",
                extra={
                    "correlation_id": correlation_id,
                    "blob_name": blob_name,
                },
            )

            container_client = self.client.get_container_client(self.container_name)
            blob_client = container_client.get_blob_client(blob_name)
            blob_client.delete_blob()

            logger.info(
                f"Feedback file deleted successfully",
                extra={
                    "correlation_id": correlation_id,
                    "blob_name": blob_name,
                },
            )

            return True

        except AzureError as e:
            logger.error(
                f"Failed to delete blob: {str(e)}",
                extra={
                    "correlation_id": correlation_id,
                    "blob_name": blob_name,
                },
                exc_info=True,
            )
            raise

    def _validate_image(
        self, file_name: str, file_content: bytes, content_type: str
    ) -> None:
        """
        Validate image file before upload.

        Args:
            file_name: File name
            file_content: File content as bytes
            content_type: MIME type

        Raises:
            ValueError: If file is invalid
        """
        # Check file size (5MB max for images)
        max_image_size = 5 * 1024 * 1024  # 5MB
        file_size = len(file_content)
        if file_size > max_image_size:
            raise ValueError(
                f"Image size ({file_size} bytes) exceeds maximum ({max_image_size} bytes)"
            )

        if file_size == 0:
            raise ValueError("Image file is empty")

        # Check file extension
        if "." not in file_name:
            raise ValueError("Image file must have an extension")

        file_extension = file_name.split(".")[-1].lower()
        allowed_image_extensions = {"png", "jpg", "jpeg"}
        if file_extension not in allowed_image_extensions:
            raise ValueError(
                f"Image type .{file_extension} not allowed. "
                f"Allowed types: {', '.join(sorted(allowed_image_extensions))}"
            )

        # Validate content type is image
        if not content_type or not content_type.startswith("image/"):
            raise ValueError(f"Invalid content type for image: {content_type}")

    def _validate_file(
        self, file_name: str, file_content: bytes, content_type: str
    ) -> None:
        """
        Validate file before upload.

        Args:
            file_name: File name
            file_content: File content as bytes
            content_type: MIME type

        Raises:
            ValueError: If file is invalid
        """
        # Check file size
        file_size = len(file_content)
        if file_size > self.max_file_size:
            raise ValueError(
                f"File size ({file_size} bytes) exceeds maximum "
                f"({self.max_file_size} bytes)"
            )

        # Check file extension
        if "." not in file_name:
            raise ValueError("File must have an extension")

        file_extension = file_name.split(".")[-1].lower()
        if file_extension not in self.allowed_extensions:
            raise ValueError(
                f"File type .{file_extension} not allowed. "
                f"Allowed types: {', '.join(self.allowed_extensions)}"
            )

        # Validate content type matches extension
        content_type_category = content_type.split("/")[0]
        if content_type_category not in ["text", "image", "application"]:
            raise ValueError(f"Invalid content type: {content_type}")

    def _generate_plain_url(self, blob_name: str) -> str:
        """
        Generate a plain blob URL (without SAS token).

        Args:
            blob_name: Azure blob path

        Returns:
            Plain blob URL (format: https://account.blob.core.windows.net/container/blob-name)
        """
        blob_url = (
            f"https://{self.client.account_name}.blob.core.windows.net/"
            f"{self.container_name}/{blob_name}"
        )
        return blob_url

    def _generate_sas_url(self, blob_name: str, expiry_hours: Optional[int] = None) -> str:
        """
        Generate a SAS URL for temporary file access.

        Args:
            blob_name: Azure blob path

        Returns:
            Full SAS URL for the blob
        """
        # Calculate expiry time
        hours = expiry_hours or self.sas_token_expiry_hours
        expiry_time = datetime.utcnow() + timedelta(hours=hours)

        # Generate SAS token
        sas_token = generate_blob_sas(
            account_name=self.client.account_name,
            container_name=self.container_name,
            blob_name=blob_name,
            account_key=self.client.credential.account_key,
            permission=BlobSasPermissions(read=True),
            expiry=expiry_time,
        )

        # Build full URL
        blob_url = (
            f"https://{self.client.account_name}.blob.core.windows.net/"
            f"{self.container_name}/{blob_name}"
        )
        sas_url = f"{blob_url}?{sas_token}"

        return sas_url