"""MCP API routes."""

from typing import List, Dict, Any, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from aldar_middleware.database.base import get_db
from aldar_middleware.models.user import User
from aldar_middleware.models.mcp import MCPConnection, MCPMessage, AgentMethod
from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.orchestration.mcp import MCPService

router = APIRouter()


@router.post("/connections")
async def create_mcp_connection(
    server_url: str,
    api_key: Optional[str] = None,
    name: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Create MCP connection."""
    try:
        mcp_service = MCPService()
        connection_id = await mcp_service.connect_to_server(server_url, api_key)
        
        return {
            "id": connection_id,
            "server_url": server_url,
            "name": name or f"MCP Connection {connection_id[:8]}",
            "status": "connected"
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create MCP connection: {str(e)}"
        )


@router.get("/connections")
async def list_mcp_connections(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> List[Dict[str, Any]]:
    """List MCP connections."""
    mcp_service = MCPService()
    connections = await mcp_service.list_connections()
    return connections


@router.get("/connections/{connection_id}")
async def get_mcp_connection(
    connection_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Get MCP connection details."""
    result = await db.execute(
        select(MCPConnection).where(MCPConnection.id == connection_id)
    )
    connection = result.scalar_one_or_none()
    
    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP connection not found"
        )
    
    return {
        "id": str(connection.id),
        "name": connection.name,
        "server_url": connection.server_url,
        "connection_type": connection.connection_type,
        "is_active": connection.is_active,
        "last_connected": connection.last_connected.isoformat() if connection.last_connected else None,
        "created_at": connection.created_at.isoformat()
    }


@router.post("/connections/{connection_id}/send")
async def send_mcp_message(
    connection_id: UUID,
    method: str,
    params: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Send message to MCP server."""
    try:
        mcp_service = MCPService()
        result = await mcp_service.send_message(
            connection_id=str(connection_id),
            method=method,
            params=params
        )
        
        return {
            "status": "success",
            "result": result
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to send MCP message: {str(e)}"
        )


@router.get("/connections/{connection_id}/methods")
async def get_mcp_methods(
    connection_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> List[str]:
    """Get available MCP methods."""
    try:
        mcp_service = MCPService()
        methods = await mcp_service.get_available_methods(str(connection_id))
        return methods
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to get MCP methods: {str(e)}"
        )


@router.get("/connections/{connection_id}/info")
async def get_mcp_server_info(
    connection_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Get MCP server information."""
    try:
        mcp_service = MCPService()
        info = await mcp_service.get_server_info(str(connection_id))
        return info
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to get MCP server info: {str(e)}"
        )


@router.delete("/connections/{connection_id}")
async def disconnect_mcp_connection(
    connection_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Disconnect MCP connection."""
    try:
        mcp_service = MCPService()
        await mcp_service.disconnect(str(connection_id))
        
        return {"message": "MCP connection disconnected successfully"}
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to disconnect MCP connection: {str(e)}"
        )


@router.post("/connections/{connection_id}/sync-methods")
async def sync_methods(
    connection_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Sync available methods from MCP server to registry."""
    try:
        mcp_service = MCPService()
        result = await mcp_service.sync_methods_from_server(str(connection_id))
        
        return {
            "status": "success",
            "data": result
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to sync methods: {str(e)}"
        )


@router.get("/connections/{connection_id}/methods-registry")
async def list_methods_registry(
    connection_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> List[Dict[str, Any]]:
    """List all methods in the registry for a connection."""
    try:
        result = await db.execute(
            select(AgentMethod).where(AgentMethod.connection_id == connection_id)
        )
        methods = result.scalars().all()
        
        return [
            {
                "id": str(method.id),
                "name": method.method_name,
                "display_name": method.display_name,
                "description": method.description,
                "version": method.version,
                "is_deprecated": method.is_deprecated,
                "parameters_schema": method.parameters_schema,
                "return_type": method.return_type,
                "tags": method.tags or [],
                "created_at": method.created_at.isoformat()
            }
            for method in methods
        ]
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to list methods: {str(e)}"
        )
