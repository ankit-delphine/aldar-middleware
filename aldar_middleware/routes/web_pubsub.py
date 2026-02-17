"""Azure Web PubSub group management and token generation API routes."""

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.models.user import User
from aldar_middleware.settings import settings

router = APIRouter()


class TokenRequest(BaseModel):
    """Request schema for generating Web PubSub client access token."""

    hub_name: str = Field(..., description="Name of the Web PubSub hub")
    user_id: Optional[str] = Field(
        default=None,
        description="Optional user ID for the token",
    )
    stream_id: Optional[str] = Field(
        default=None,
        description="Optional stream ID for logging purposes",
    )


class TokenResponse(BaseModel):
    """Response schema for Web PubSub client access token."""

    url: str = Field(..., description="WebSocket URL for client connection")
    token: str = Field(..., description="Client access token (JWT)")


class GroupManagementError(HTTPException):
    """Base exception for group management errors."""

    def __init__(self, message: str, status_code: int = 500) -> None:
        """Initialize exception."""
        super().__init__(status_code=status_code, detail=message)


async def get_web_pubsub_client(hub_name: str) -> Any:
    """Get Azure Web PubSub service client.

    Args:
        hub_name: Name of the Web PubSub hub.

    Returns:
        WebPubSubServiceClient: Initialized client for Web PubSub operations.

    Raises:
        GroupManagementError: If Web PubSub is not configured.
    """
    connection_string = settings.web_pubsub_connection_string

    if not connection_string:
        logger.error("Web PubSub connection string not configured")
        raise GroupManagementError(
            "Web PubSub service is not configured",
            status_code=500,
        )

    from azure.messaging.webpubsubservice import (
        WebPubSubServiceClient,
    )

    return WebPubSubServiceClient.from_connection_string(
        connection_string,
        hub=hub_name,
    )


@router.post("/group/add")
async def add_user_to_group(
    hub_name: str,
    group_name: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Add authenticated user to a Web PubSub group.

    Args:
        hub_name: Name of the Web PubSub hub.
        group_name: Name of the group to add user to.
        user_id: User ID to add to the group.
        current_user: Current authenticated user.

    Returns:
        dict: Success response with operation details.

    Raises:
        HTTPException: If operation fails or user not authorized.
    """
    if not hub_name or not group_name or not user_id:
        raise HTTPException(
            status_code=400,
            detail="hubName, groupName, and userId are required",
        )

    try:
        client = await get_web_pubsub_client(hub_name)
        client.add_user_to_group(group_name, user_id)

        logger.info(
            f"User {user_id} added to group {group_name} in hub {hub_name}",
            extra={"user_id": current_user.id},
        )

        return {
            "success": True,
            "action": "add",
            "hub_name": hub_name,
            "group_name": group_name,
            "user_id": user_id,
        }
    except Exception as e:
        error_msg = f"{e!s}"
        logger.error(
            f"Failed to add user to group: {error_msg}",
            extra={"user_id": current_user.id, "error": error_msg},
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to add user to group: {error_msg}",
        ) from e


@router.post("/group/remove")
async def remove_user_from_group(
    hub_name: str,
    group_name: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Remove user from a Web PubSub group.

    Args:
        hub_name: Name of the Web PubSub hub.
        group_name: Name of the group to remove user from.
        user_id: User ID to remove from the group.
        current_user: Current authenticated user.

    Returns:
        dict: Success response with operation details.

    Raises:
        HTTPException: If operation fails or user not authorized.
    """
    if not hub_name or not group_name or not user_id:
        raise HTTPException(
            status_code=400,
            detail="hubName, groupName, and userId are required",
        )

    try:
        client = await get_web_pubsub_client(hub_name)
        client.remove_user_from_group(group_name, user_id)

        logger.info(
            f"User {user_id} removed from group {group_name} in hub {hub_name}",
            extra={"user_id": current_user.id},
        )

        return {
            "success": True,
            "action": "remove",
            "hub_name": hub_name,
            "group_name": group_name,
            "user_id": user_id,
        }
    except Exception as e:
        error_msg = f"{e!s}"
        logger.error(
            f"Failed to remove user from group: {error_msg}",
            extra={"user_id": current_user.id, "error": error_msg},
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to remove user from group: {error_msg}",
        ) from e


@router.post("/group")
async def manage_group(
    hub_name: str,
    group_name: str,
    user_id: str,
    action: Literal["add", "remove"],
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Manage user group membership in Web PubSub.

    Args:
        hub_name: Name of the Web PubSub hub.
        group_name: Name of the group.
        user_id: User ID to manage.
        action: Action to perform ("add" or "remove").
        current_user: Current authenticated user.

    Returns:
        dict: Success response with operation details.

    Raises:
        HTTPException: If operation fails or invalid parameters.
    """
    if action == "add":
        return await add_user_to_group(
            hub_name,
            group_name,
            user_id,
            current_user,
        )
    if action == "remove":
        return await remove_user_from_group(
            hub_name,
            group_name,
            user_id,
            current_user,
        )
    raise HTTPException(
        status_code=400,
        detail='action must be "add" or "remove"',
    )


@router.post("/token", response_model=TokenResponse)
async def generate_token(
    token_request: TokenRequest,
    current_user: User = Depends(get_current_user),
) -> TokenResponse:
    """Generate client access token for Azure Web PubSub WebSocket connection.

    Generates a JWT token with wildcard group permissions allowing the client
    to dynamically join/leave any group for multi-stream handling.

    Args:
        token_request: Token request containing hub name and optional user/stream IDs.
        current_user: Current authenticated user.

    Returns:
        TokenResponse: WebSocket URL and access token.

    Raises:
        HTTPException: If token generation fails or service not configured.
    """
    if not token_request.hub_name:
        raise HTTPException(
            status_code=400,
            detail="hubName is required",
        )

    try:
        connection_string = settings.web_pubsub_connection_string

        if not connection_string:
            logger.error("Web PubSub connection string not configured")
            raise HTTPException(
                status_code=500,
                detail="Web PubSub service is not configured",
            )

        from azure.messaging.webpubsubservice import (
            WebPubSubServiceClient,
        )

        service_client = WebPubSubServiceClient.from_connection_string(
            connection_string,
            hub=token_request.hub_name,
        )

        user_id = token_request.user_id or str(current_user.id)
        stream_id = token_request.stream_id or "wildcard"

        token_options: dict[str, Any] = {
            "user_id": user_id,
            "roles": [
                "webpubsub.joinLeaveGroup",
                "webpubsub.sendToGroup",
            ],
        }

        logger.info(
            f"Generating Web PubSub token with wildcard permissions "
            f"(hub: {token_request.hub_name}, stream: {stream_id}, "
            f"user: {user_id})",
            extra={
                "current_user_id": current_user.id,
                "token_user_id": user_id,
                "stream_id": stream_id,
                "hub_name": token_request.hub_name,
                "roles": token_options["roles"],
            },
        )

        token = service_client.get_client_access_token(**token_options)

        logger.info(
            f"Successfully generated Web PubSub token "
            f"(hub: {token_request.hub_name}, stream: {stream_id})",
            extra={
                "current_user_id": current_user.id,
                "hub_name": token_request.hub_name,
            },
        )

        return TokenResponse(
            url=token["url"],
            token=token["token"],
        )
    except Exception as e:
        error_msg = f"{e!s}"
        logger.error(
            f"Failed to generate Web PubSub token: {error_msg}",
            extra={
                "current_user_id": current_user.id,
                "hub_name": token_request.hub_name,
                "error": error_msg,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate token: {error_msg}",
        ) from e
