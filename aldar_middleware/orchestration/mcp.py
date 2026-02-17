"""MCP (Model Context Protocol) service."""

import json
import time
import uuid
from typing import Dict, Any, Optional, List
from datetime import datetime

import httpx
from loguru import logger

from aldar_middleware.settings import settings
from aldar_middleware.database.base import get_db
from aldar_middleware.models.mcp import MCPConnection, MCPMessage, AgentMethod
from aldar_middleware.settings.context import get_correlation_id, track_agent_call
from aldar_middleware.monitoring.prometheus import (
    record_mcp_request,
    record_agent_call,
    record_agent_error,
    record_mcp_error,
    update_mcp_connections
)


class MCPService:
    """Service for MCP integration."""

    def __init__(self):
        """Initialize MCP service."""
        self.connections: Dict[str, MCPConnection] = {}

    async def connect_to_server(self, server_url: str, api_key: Optional[str] = None) -> str:
        """Connect to MCP server."""
        start_time = time.time()
        correlation_id = get_correlation_id()
        connection_id = str(uuid.uuid4())
        
        logger.info(
            f"Connecting to MCP server: {server_url}, "
            f"connection_id={connection_id}, correlation_id={correlation_id}"
        )
        
        try:
            # Test connection
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{server_url}/health",
                    headers={"Authorization": f"Bearer {api_key}"} if api_key else {}
                )
                response.raise_for_status()
            
            # Save connection to database
            async for db in get_db():
                connection = MCPConnection(
                    id=connection_id,
                    name=f"MCP Connection {connection_id[:8]}",
                    server_url=server_url,
                    api_key=api_key,
                    connection_type="http",
                    is_active=True,
                    last_connected=datetime.utcnow()
                )
                db.add(connection)
                await db.commit()
                await db.refresh(connection)
                
                self.connections[connection_id] = connection
                
                # Update metrics
                update_mcp_connections(len(self.connections))
                
                duration = time.time() - start_time
                logger.info(
                    f"Connected to MCP server successfully: {server_url}, "
                    f"connection_id={connection_id}, duration={duration:.2f}s, "
                    f"correlation_id={correlation_id}"
                )
                
                return connection_id
                
        except Exception as e:
            duration = time.time() - start_time
            error_type = type(e).__name__
            
            logger.error(
                f"Error connecting to MCP server: {e}, "
                f"server_url={server_url}, connection_id={connection_id}, "
                f"correlation_id={correlation_id}, error_type={error_type}"
            )
            
            # Record error metrics
            record_mcp_error(connection_id, error_type)
            
            raise

    async def send_message(
        self, 
        connection_id: str, 
        method: str, 
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send message to MCP server."""
        if connection_id not in self.connections:
            error_msg = f"Connection {connection_id} not found"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        connection = self.connections[connection_id]
        start_time = time.time()
        correlation_id = get_correlation_id()
        
        # Track agent call in context
        track_agent_call(
            agent_type="mcp",
            agent_name=connection.name,
            method=method
        )
        
        logger.info(
            f"Sending MCP message: connection_id={connection_id}, "
            f"method={method}, correlation_id={correlation_id}"
        )
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{connection.server_url}/mcp",
                    json={
                        "method": method,
                        "params": params,
                        "id": str(uuid.uuid4())
                    },
                    headers={"Authorization": f"Bearer {connection.api_key}"} if connection.api_key else {}
                )
                response.raise_for_status()
                result = response.json()
                
                duration = time.time() - start_time
                
                # Save message to database
                await self._save_message(connection_id, "request", method, json.dumps(params))
                await self._save_message(connection_id, "response", method, json.dumps(result))
                
                # Record Prometheus metrics
                record_mcp_request(
                    connection_id=connection_id,
                    method=method,
                    status="success",
                    duration=duration
                )
                
                record_agent_call(
                    agent_type="mcp",
                    agent_name=connection.name,
                    method=method,
                    duration=duration,
                    status="success"
                )
                
                logger.info(
                    f"MCP message sent successfully: connection_id={connection_id}, "
                    f"method={method}, duration={duration:.2f}s, correlation_id={correlation_id}"
                )
                
                return result
                
        except Exception as e:
            duration = time.time() - start_time
            error_type = type(e).__name__
            
            logger.error(
                f"Error sending MCP message: {e}, "
                f"connection_id={connection_id}, method={method}, "
                f"correlation_id={correlation_id}, error_type={error_type}"
            )
            
            await self._save_message(connection_id, "error", method, str(e))
            
            # Record error metrics
            record_mcp_request(
                connection_id=connection_id,
                method=method,
                status="error",
                duration=duration
            )
            
            record_agent_call(
                agent_type="mcp",
                agent_name=connection.name,
                method=method,
                duration=duration,
                status="error"
            )
            
            record_agent_error(
                agent_type="mcp",
                agent_name=connection.name,
                error_type=error_type
            )
            
            record_mcp_error(connection_id, error_type)
            
            raise

    async def get_available_methods(self, connection_id: str) -> List[str]:
        """Get available methods from MCP server."""
        try:
            result = await self.send_message(connection_id, "list_methods", {})
            return result.get("methods", [])
        except Exception as e:
            logger.error(f"Error getting available methods: {e}")
            return []

    async def get_server_info(self, connection_id: str) -> Dict[str, Any]:
        """Get server information."""
        try:
            result = await self.send_message(connection_id, "get_info", {})
            return result
        except Exception as e:
            logger.error(f"Error getting server info: {e}")
            return {}

    async def _save_message(
        self, 
        connection_id: str, 
        message_type: str, 
        method: str, 
        content: str
    ):
        """Save MCP message to database."""
        async for db in get_db():
            message = MCPMessage(
                connection_id=connection_id,
                message_type=message_type,
                method=method,
                content=content,
                status="success" if message_type != "error" else "error"
            )
            db.add(message)
            await db.commit()

    async def disconnect(self, connection_id: str):
        """Disconnect from MCP server."""
        correlation_id = get_correlation_id()
        
        logger.info(
            f"Disconnecting from MCP server: connection_id={connection_id}, "
            f"correlation_id={correlation_id}"
        )
        
        if connection_id in self.connections:
            async for db in get_db():
                connection = await db.get(MCPConnection, connection_id)
                if connection:
                    connection.is_active = False
                    await db.commit()
            
            del self.connections[connection_id]
            
            # Update metrics
            update_mcp_connections(len(self.connections))
            
            logger.info(
                f"Disconnected from MCP server successfully: connection_id={connection_id}, "
                f"correlation_id={correlation_id}"
            )

    async def list_connections(self) -> List[Dict[str, Any]]:
        """List all active MCP connections."""
        async for db in get_db():
            from sqlalchemy import select
            result = await db.execute(
                select(MCPConnection).where(MCPConnection.is_active == True)
            )
            connections = result.scalars().all()
            
            return [
                {
                    "id": conn.id,
                    "name": conn.name,
                    "server_url": conn.server_url,
                    "connection_type": conn.connection_type,
                    "last_connected": conn.last_connected.isoformat() if conn.last_connected else None
                }
                for conn in connections
            ]

    async def sync_methods_from_server(self, connection_id: str) -> Dict[str, Any]:
        """
        Sync available methods from MCP server and store in registry.
        
        Args:
            connection_id: MCP connection ID
        
        Returns:
            Sync result with count of methods synced
        """
        correlation_id = get_correlation_id()
        logger.info(
            f"Syncing methods from MCP server: connection_id={connection_id}, "
            f"correlation_id={correlation_id}"
        )
        
        try:
            # Get methods from server
            methods_list = await self.get_available_methods(connection_id)
            
            async for db in get_db():
                # Clear existing methods for this connection
                await db.execute(
                    db.delete(AgentMethod).where(AgentMethod.connection_id == connection_id)
                )
                
                # Add new methods to registry
                added_count = 0
                for method_name in methods_list:
                    try:
                        # Try to get detailed info about the method
                        method_info = await self._get_method_details(
                            connection_id, method_name
                        )
                        
                        agent_method = AgentMethod(
                            connection_id=connection_id,
                            method_name=method_name,
                            display_name=method_info.get("display_name", method_name),
                            description=method_info.get("description"),
                            parameters_schema=method_info.get("parameters_schema"),
                            return_type=method_info.get("return_type"),
                            tags=method_info.get("tags", []),
                            metadata=method_info.get("metadata")
                        )
                        db.add(agent_method)
                        added_count += 1
                        
                    except Exception as e:
                        logger.warning(
                            f"Failed to get details for method {method_name}: {e}"
                        )
                        # Still add method without details
                        agent_method = AgentMethod(
                            connection_id=connection_id,
                            method_name=method_name,
                            display_name=method_name
                        )
                        db.add(agent_method)
                        added_count += 1
                
                await db.commit()
                
                logger.info(
                    f"Synced {added_count} methods from MCP server: connection_id={connection_id}, "
                    f"correlation_id={correlation_id}"
                )
                
                return {
                    "connection_id": connection_id,
                    "methods_synced": added_count,
                    "status": "success"
                }
                
        except Exception as e:
            logger.error(
                f"Error syncing methods from MCP server: {e}, "
                f"connection_id={connection_id}, correlation_id={correlation_id}"
            )
            raise

    async def _get_method_details(
        self, 
        connection_id: str, 
        method_name: str
    ) -> Dict[str, Any]:
        """
        Get detailed information about a specific method.
        
        Args:
            connection_id: MCP connection ID
            method_name: Method name
        
        Returns:
            Method details (display_name, description, parameters_schema, etc.)
        """
        try:
            result = await self.send_message(
                connection_id,
                f"get_method_details",
                {"method": method_name}
            )
            return result
        except Exception as e:
            logger.debug(f"Could not get detailed info for method {method_name}: {e}")
            return {}

    async def register_method(
        self,
        connection_id: str,
        method_name: str,
        display_name: str,
        description: Optional[str] = None,
        parameters_schema: Optional[Dict[str, Any]] = None,
        return_type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Register a new method in the agent method registry.
        
        Args:
            connection_id: MCP connection ID
            method_name: Full method name
            display_name: Human-readable name
            description: Method description
            parameters_schema: JSON Schema for parameters
            return_type: Return type description
            tags: Method tags
            metadata: Additional metadata
        
        Returns:
            Registered method details
        """
        correlation_id = get_correlation_id()
        logger.info(
            f"Registering method: {method_name}, connection_id={connection_id}, "
            f"correlation_id={correlation_id}"
        )
        
        try:
            async for db in get_db():
                agent_method = AgentMethod(
                    connection_id=connection_id,
                    method_name=method_name,
                    display_name=display_name,
                    description=description,
                    parameters_schema=parameters_schema,
                    return_type=return_type,
                    tags=tags or [],
                    metadata=metadata
                )
                db.add(agent_method)
                await db.commit()
                await db.refresh(agent_method)
                
                logger.info(
                    f"Method registered successfully: {method_name}, "
                    f"method_id={agent_method.id}, correlation_id={correlation_id}"
                )
                
                return {
                    "id": str(agent_method.id),
                    "name": agent_method.method_name,
                    "display_name": agent_method.display_name,
                    "status": "registered"
                }
                
        except Exception as e:
            logger.error(
                f"Error registering method: {e}, method={method_name}, "
                f"correlation_id={correlation_id}"
            )
            raise

    async def ping_connection(self, connection_id: str) -> None:
        """
        Ping an MCP connection to verify it's still available.

        Args:
            connection_id: MCP connection ID

        Raises:
            ValueError: If connection not found
            Exception: If ping fails
        """
        if connection_id not in self.connections:
            # Try to load from database
            async for db in get_db():
                from sqlalchemy import select
                result = await db.execute(
                    select(MCPConnection).where(MCPConnection.id == connection_id)
                )
                conn = result.scalars().first()
                if conn and conn.is_active:
                    self.connections[connection_id] = conn
                else:
                    raise ValueError(f"Connection {connection_id} not found or inactive")
                break

        connection = self.connections[connection_id]

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{connection.server_url}/health",
                    headers={"Authorization": f"Bearer {connection.api_key}"}
                    if connection.api_key
                    else {},
                )
                response.raise_for_status()
            logger.debug(f"Ping successful for connection {connection_id}")
        except Exception as e:
            logger.debug(f"Ping failed for connection {connection_id}: {e}")
            raise
