"""Service for writing and querying user logs directly to a dedicated Cosmos DB collection."""

import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from azure.cosmos import CosmosClient, PartitionKey, exceptions
from loguru import logger

from aldar_middleware.settings import settings


class UserLogsService:
    """Service for writing and querying user activity logs in a dedicated Cosmos DB collection."""
    
    def __init__(self):
        """Initialize the user logs service."""
        self.client = None
        self.database = None
        self.container = None
        self._initialized = False
    
    async def initialize(self) -> bool:
        """Initialize Cosmos DB connection for user logs service.
        
        Returns:
            bool: True if initialization successful, False otherwise
        """
        try:
            if not settings.cosmos_logging_enabled:
                logger.warning("Cosmos DB logging is not enabled")
                return False
            
            # Check if we have a connection string or separate endpoint/key
            if settings.cosmos_endpoint and "AccountEndpoint=" in settings.cosmos_endpoint:
                # Use connection string
                self.client = CosmosClient.from_connection_string(settings.cosmos_endpoint)
            elif settings.cosmos_endpoint and settings.cosmos_key:
                # Use separate endpoint and key
                self.client = CosmosClient(
                    settings.cosmos_endpoint,
                    settings.cosmos_key
                )
            else:
                logger.error("Cosmos DB configuration is incomplete")
                return False
            
            # Get or create database
            try:
                self.database = self.client.create_database(
                    id=settings.cosmos_logging_database_name
                )
                logger.info(f"Created Cosmos DB database: {settings.cosmos_logging_database_name}")
            except exceptions.CosmosResourceExistsError:
                self.database = self.client.get_database_client(settings.cosmos_logging_database_name)
                logger.info(f"Using existing Cosmos DB database: {settings.cosmos_logging_database_name}")
            
            # Create or get container for user logs
            try:
                self.container = self.database.create_container(
                    id=settings.cosmos_logging_user_logs_container_name,
                    partition_key=PartitionKey(path="/timestamp")  # Partition by timestamp for better query performance
                )
                logger.info(
                    f"Created Cosmos DB container: {settings.cosmos_logging_user_logs_container_name} "
                    f"with partition key /timestamp"
                )
            except exceptions.CosmosResourceExistsError:
                self.container = self.database.get_container_client(settings.cosmos_logging_user_logs_container_name)
                logger.info(
                    f"Using existing Cosmos DB container: {settings.cosmos_logging_user_logs_container_name}"
                )
            
            self._initialized = True
            logger.info("User logs service initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize user logs service: {e}")
            return False
    
    async def write_user_log(self, log_data: Dict[str, Any]) -> bool:
        """Write a user log event directly to the user logs collection.
        
        Args:
            log_data: Dictionary containing the log event data (already in 3.0 format)
            
        Returns:
            bool: True if write successful, False otherwise
        """
        if not self._initialized:
            await self.initialize()
        
        if not self._initialized or not self.container:
            return False
        
        try:
            # Ensure required fields
            if "id" not in log_data:
                log_data["id"] = str(uuid.uuid4())
            
            if "timestamp" not in log_data:
                log_data["timestamp"] = datetime.now(timezone.utc).isoformat()
            
            # Write directly to container
            self.container.create_item(body=log_data)
            return True
            
        except Exception as e:
            logger.error(f"Failed to write user log: {e}")
            return False
    
    async def query_user_logs(
        self,
        limit: int = 20,
        offset: int = 0,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        event_type: Optional[str] = None,
        user_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query user logs from the dedicated collection.
        
        Args:
            limit: Maximum number of logs to return
            offset: Number of logs to skip
            date_from: Start date filter
            date_to: End date filter
            event_type: Filter by event type
            user_id: Filter by user ID
            correlation_id: Filter by correlation ID
            
        Returns:
            Dictionary with 'items' (list of logs) and 'total' (total count)
        """
        if not self._initialized:
            await self.initialize()
        
        if not self._initialized or not self.container:
            return {"items": [], "total": 0}
        
        try:
            # Build WHERE clause
            where_clause = "WHERE 1=1"
            parameters = []
            
            if date_from:
                where_clause += " AND c.timestamp >= @date_from"
                parameters.append({"name": "@date_from", "value": date_from.isoformat()})
            
            if date_to:
                where_clause += " AND c.timestamp <= @date_to"
                parameters.append({"name": "@date_to", "value": date_to.isoformat()})
            
            if event_type:
                where_clause += " AND c.eventType = @event_type"
                parameters.append({"name": "@event_type", "value": event_type})
            
            if user_id:
                where_clause += " AND c.userId = @user_id"
                parameters.append({"name": "@user_id", "value": user_id})
            
            if correlation_id:
                where_clause += " AND c.correlationId = @correlation_id"
                parameters.append({"name": "@correlation_id", "value": correlation_id})
            
            # Build query
            # SECURITY: Validate offset and limit to prevent injection
            # Cosmos DB doesn't support parameterized OFFSET/LIMIT, but we validate the values
            safe_offset = max(0, int(offset)) if isinstance(offset, (int, str)) and str(offset).isdigit() else 0
            safe_limit = min(1000, max(1, int(limit))) if isinstance(limit, (int, str)) and str(limit).isdigit() else 20
            
            query = f"SELECT * FROM c {where_clause}"
            query += " ORDER BY c.timestamp DESC"
            query += f" OFFSET {safe_offset} LIMIT {safe_limit}"
            
            # Execute query
            if parameters:
                query_dict = {
                    "query": query,
                    "parameters": parameters
                }
                items = list(self.container.query_items(
                    query=query_dict,
                    enable_cross_partition_query=True
                ))
            else:
                items = list(self.container.query_items(
                    query=query,
                    enable_cross_partition_query=True
                ))
            
            # Get total count
            count_query = f"SELECT VALUE COUNT(1) FROM c {where_clause}"
            if parameters:
                count_query_dict = {
                    "query": count_query,
                    "parameters": parameters
                }
                total_result = list(self.container.query_items(
                    query=count_query_dict,
                    enable_cross_partition_query=True
                ))
            else:
                total_result = list(self.container.query_items(
                    query=count_query,
                    enable_cross_partition_query=True
                ))
            
            total = total_result[0] if total_result else 0
            
            return {
                "items": items,
                "total": total
            }
            
        except Exception as e:
            logger.error(f"Failed to query user logs: {e}")
            return {"items": [], "total": 0}


# Global instance
user_logs_service = UserLogsService()

