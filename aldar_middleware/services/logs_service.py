"""Service for querying logs from Cosmos DB."""

from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from azure.cosmos import CosmosClient

from aldar_middleware.settings import settings
from aldar_middleware.admin.schemas import LogQueryRequest, LogEntryResponse, LogsResponse
from loguru import logger


class LogsService:
    """Service for querying application logs from Cosmos DB."""
    
    def __init__(self):
        """Initialize the logs service."""
        self.client = None
        self.database = None
        self.container = None
        self._initialized = False
    
    async def initialize(self) -> bool:
        """Initialize Cosmos DB connection for logs service.
        
        Returns:
            bool: True if initialization successful, False otherwise
        """
        try:
            logger.info(f"Initializing logs service - cosmos_logging_enabled: {settings.cosmos_logging_enabled}")
            logger.info(f"Cosmos endpoint: {settings.cosmos_endpoint}")
            logger.info(f"Cosmos key: {'***' if settings.cosmos_key else 'None'}")
            logger.info(f"Cosmos database: {settings.cosmos_logging_database_name}")
            logger.info(f"Cosmos container: {settings.cosmos_logging_container_name}")
            
            if not settings.cosmos_logging_enabled:
                logger.warning("Cosmos DB logging is not enabled")
                return False
            
            # Check if we have a connection string or separate endpoint/key
            if settings.cosmos_endpoint and "AccountEndpoint=" in settings.cosmos_endpoint:
                # Use connection string
                logger.info("Using Cosmos DB connection string")
                self.client = CosmosClient.from_connection_string(settings.cosmos_endpoint)
            elif settings.cosmos_endpoint and settings.cosmos_key:
                # Use separate endpoint and key
                logger.info("Using Cosmos DB endpoint and key")
                self.client = CosmosClient(
                    settings.cosmos_endpoint,
                    settings.cosmos_key
                )
            else:
                logger.error("Cosmos DB configuration is incomplete")
                return False
            
            # Get or create database
            self.database = self.client.get_database_client(settings.cosmos_logging_database_name)
            
            # Get or create container
            self.container = self.database.get_container_client(settings.cosmos_logging_container_name)
            
            self._initialized = True
            logger.info("Logs service initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize logs service: {e}")
            return False
    
    async def query_logs(self, query_params: LogQueryRequest) -> LogsResponse:
        """Query logs from Cosmos DB with filtering.
        
        Args:
            query_params: Query parameters for filtering logs
            
        Returns:
            LogsResponse: Query results with logs and metadata
            
        Raises:
            Exception: If logs service is not initialized or query fails
        """
        if not self._initialized:
            await self.initialize()
        
        if not self._initialized:
            raise Exception("Logs service not initialized")
        
        # Validate query parameters
        if query_params.limit <= 0 or query_params.limit > 1000:
            raise ValueError("Limit must be between 1 and 1000")
        
        if query_params.offset < 0:
            raise ValueError("Offset must be non-negative")
        
        try:
            # Build SQL query and parameters
            query, parameters = self._build_query(query_params)
            
            # Execute query with parameters
            # Cosmos DB Python SDK expects parameters in the query dict format
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
            
            # Convert to response format
            logs = []
            for item in items:
                log_entry = self._convert_to_log_entry(item)
                logs.append(log_entry)
            
            # Get total count of all matching logs (without pagination)
            total_count = await self._get_total_count(query_params)
            
            # Calculate has_more based on whether we got a full page
            has_more = len(logs) == query_params.limit
            
            return LogsResponse(
                logs=logs,
                total_count=total_count,
                has_more=has_more,
                query_params=query_params
            )
            
        except Exception as e:
            logger.error(f"Failed to query logs: {e}")
            raise Exception(f"Failed to query logs: {str(e)}")
    
    def _build_where_clause(self, query_params: LogQueryRequest) -> Tuple[str, List[Dict[str, Any]]]:
        """Build WHERE clause from query parameters.
        
        Args:
            query_params: Query parameters
            
        Returns:
            tuple: (WHERE clause string, parameters list)
        """
        where_clause = "WHERE 1=1"
        parameters = []
        
        # Add filters
        if query_params.correlation_id:
            where_clause += " AND c.correlation_id = @correlation_id"
            parameters.append({"name": "@correlation_id", "value": query_params.correlation_id})
        
        # Handle user_id and email with OR logic if both are provided
        # This is useful for user logs endpoint where we want to match either user_id OR email
        if query_params.user_id and query_params.email:
            # Use OR to match either user_id or email
            where_clause += " AND (c.user_id = @user_id OR c.email = @email)"
            parameters.append({"name": "@user_id", "value": query_params.user_id})
            parameters.append({"name": "@email", "value": query_params.email})
        elif query_params.user_id:
            where_clause += " AND c.user_id = @user_id"
            parameters.append({"name": "@user_id", "value": query_params.user_id})
        elif query_params.email:
            where_clause += " AND c.email = @email"
            parameters.append({"name": "@email", "value": query_params.email})
        
        if query_params.username:
            where_clause += " AND c.username = @username"
            parameters.append({"name": "@username", "value": query_params.username})
        
        if query_params.user_type:
            where_clause += " AND c.user_type = @user_type"
            parameters.append({"name": "@user_type", "value": query_params.user_type})
        
        if query_params.level:
            where_clause += " AND c.level = @level"
            parameters.append({"name": "@level", "value": query_params.level})
        
        if query_params.module:
            where_clause += " AND c.module = @module"
            parameters.append({"name": "@module", "value": query_params.module})
        
        if query_params.function:
            where_clause += " AND c.function = @function"
            parameters.append({"name": "@function", "value": query_params.function})
        
        if query_params.start_time:
            where_clause += " AND c.timestamp >= @start_time"
            parameters.append({"name": "@start_time", "value": query_params.start_time.isoformat()})
        
        if query_params.end_time:
            where_clause += " AND c.timestamp <= @end_time"
            parameters.append({"name": "@end_time", "value": query_params.end_time.isoformat()})
        
        # Add search filter - search across multiple fields
        if query_params.search:
            search_term = query_params.search
            # Search in user_id, username, email, and message fields
            # Cosmos DB CONTAINS is case-sensitive, so we search with the original term
            # For case-insensitive search, we'll search both original and lowercase versions
            where_clause += " AND ("
            where_clause += "CONTAINS(c.user_id, @search) OR "
            where_clause += "CONTAINS(c.username, @search) OR "
            where_clause += "CONTAINS(c.email, @search) OR "
            where_clause += "CONTAINS(c.message, @search) OR "
            where_clause += "CONTAINS(LOWER(c.user_id), @search_lower) OR "
            where_clause += "CONTAINS(LOWER(c.username), @search_lower) OR "
            where_clause += "CONTAINS(LOWER(c.email), @search_lower) OR "
            where_clause += "CONTAINS(LOWER(c.message), @search_lower)"
            where_clause += ")"
            parameters.append({"name": "@search", "value": search_term})
            parameters.append({"name": "@search_lower", "value": search_term.lower()})
        
        return where_clause, parameters
    
    def _build_query(self, query_params: LogQueryRequest) -> Tuple[str, List[Dict[str, Any]]]:
        """Build SQL query from query parameters.
        
        Args:
            query_params: Query parameters
            
        Returns:
            tuple: (SQL query string, parameters list)
        """
        where_clause, parameters = self._build_where_clause(query_params)
        
        # Build full query with ordering and pagination
        query = f"SELECT * FROM c {where_clause}"
        query += " ORDER BY c.timestamp DESC"
        query += f" OFFSET {query_params.offset} LIMIT {query_params.limit}"
        
        return query, parameters
    
    async def _get_total_count(self, query_params: LogQueryRequest) -> int:
        """Get total count of logs matching the query parameters.
        
        Args:
            query_params: Query parameters (offset and limit are ignored for count)
            
        Returns:
            int: Total count of matching logs
        """
        try:
            where_clause, parameters = self._build_where_clause(query_params)
            
            # Build COUNT query
            count_query = f"SELECT VALUE COUNT(1) FROM c {where_clause}"
            
            # Execute count query
            if parameters:
                query_dict = {
                    "query": count_query,
                    "parameters": parameters
                }
                items = list(self.container.query_items(
                    query=query_dict,
                    enable_cross_partition_query=True
                ))
            else:
                items = list(self.container.query_items(
                    query=count_query,
                    enable_cross_partition_query=True
                ))
            
            # COUNT query returns a single number
            if items:
                return items[0] if isinstance(items[0], int) else 0
            return 0
            
        except Exception as e:
            logger.error(f"Failed to get total count: {e}")
            # Return 0 on error, will be handled by the calling code
            return 0
    
    async def query_user_log_events(
        self, 
        query_params: LogQueryRequest,
        event_types: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Query user log events from Cosmos DB (for 3.0 format).
        
        Args:
            query_params: Query parameters for filtering logs
            event_types: Optional list of event types to filter (e.g., ['USER_CONVERSATION_CREATED', 'USER_MESSAGE_CREATED'])
            
        Returns:
            List of raw log items from Cosmos DB
        """
        if not self._initialized:
            await self.initialize()
        
        if not self._initialized:
            raise Exception("Logs service not initialized")
        
        try:
            # Build WHERE clause for user log events
            where_clause = "WHERE 1=1"
            parameters = []
            
            # Filter by correlation_id if provided
            if query_params.correlation_id:
                where_clause += " AND c.correlation_id = @correlation_id"
                parameters.append({"name": "@correlation_id", "value": query_params.correlation_id})
            
            # Filter by user_id or email
            if query_params.user_id and query_params.email:
                where_clause += " AND (c.user_id = @user_id OR c.email = @email)"
                parameters.append({"name": "@user_id", "value": query_params.user_id})
                parameters.append({"name": "@email", "value": query_params.email})
            elif query_params.user_id:
                where_clause += " AND c.user_id = @user_id"
                parameters.append({"name": "@user_id", "value": query_params.user_id})
            elif query_params.email:
                where_clause += " AND c.email = @email"
                parameters.append({"name": "@email", "value": query_params.email})
            
            # Filter by event types if provided
            # Map 3.0 event types to actual Cosmos DB event types
            if event_types:
                event_type_conditions = []
                for idx, event_type in enumerate(event_types):
                    # Map 3.0 event types to actual Cosmos DB event types
                    cosmos_event_type = None
                    if event_type == "USER_CONVERSATION_CREATED":
                        cosmos_event_type = "chat_session_created"
                    elif event_type == "USER_MESSAGE_CREATED":
                        cosmos_event_type = "chat_message"
                    elif event_type == "USER_CONVERSATION_RENAMED":
                        cosmos_event_type = "conversation_renamed"
                    elif event_type == "USER_CONVERSATION_STARTING_PROMPT_CHOSEN":
                        cosmos_event_type = "starting_prompt_chosen"
                    elif event_type == "USER_MY_AGENT_KNOWLEDGE_SOURCES_UPDATED":
                        cosmos_event_type = "my_agent_knowledge_sources_updated"
                    elif event_type == "USER_MESSAGE_REGENERATED":
                        cosmos_event_type = "message_regenerated"
                    elif event_type == "USER_CREATED":
                        cosmos_event_type = "user_created"
                    elif event_type == "USER_CONVERSATION_DOWNLOAD":
                        cosmos_event_type = "conversation_download"
                    elif event_type == "USER_CONVERSATION_SHARE":
                        cosmos_event_type = "conversation_share"
                    elif event_type == "USER_CONVERSATION_FAVORITED":
                        cosmos_event_type = "chat_favorite_toggled"
                    elif event_type == "USER_CONVERSATION_UNFAVORITED":
                        cosmos_event_type = "chat_favorite_toggled"
                    else:
                        # Fallback: try lowercase version
                        cosmos_event_type = event_type.lower()
                    
                    param_name = f"@event_type_{idx}"
                    # Check in message field, chat_event.type, or type field
                    # Also check for chat_event structure
                    event_type_conditions.append(
                        f"(CONTAINS(c.message, {param_name}) OR c.type = {param_name} OR "
                        f"(IS_DEFINED(c.chat_event) AND c.chat_event.type = {param_name}))"
                    )
                    parameters.append({"name": param_name, "value": cosmos_event_type})
                if event_type_conditions:
                    where_clause += " AND (" + " OR ".join(event_type_conditions) + ")"
            
            # Time range filters
            if query_params.start_time:
                where_clause += " AND c.timestamp >= @start_time"
                parameters.append({"name": "@start_time", "value": query_params.start_time.isoformat()})
            
            if query_params.end_time:
                where_clause += " AND c.timestamp <= @end_time"
                parameters.append({"name": "@end_time", "value": query_params.end_time.isoformat()})
            
            # Build query
            query = f"SELECT * FROM c {where_clause}"
            query += " ORDER BY c.timestamp DESC"
            query += f" OFFSET {query_params.offset} LIMIT {query_params.limit}"
            
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
            
            return items
            
        except Exception as e:
            logger.error(f"Failed to query user log events: {e}")
            raise Exception(f"Failed to query user log events: {str(e)}")
    
    def _convert_to_log_entry(self, item: Dict[str, Any]) -> LogEntryResponse:
        """Convert Cosmos DB item to LogEntryResponse.
        
        Converts "N/A" strings to None for cleaner JSON responses.
        Preserves all actual values.
        
        Args:
            item: Cosmos DB item
            
        Returns:
            LogEntryResponse: Converted log entry
        """
        def _normalize_value(value: Any) -> Optional[str]:
            """Convert "N/A" strings to None, otherwise return the actual value."""
            if value is None:
                return None
            # Convert to string to handle all types (UUIDs, numbers, etc.)
            str_value = str(value)
            # Only convert "N/A" (case-insensitive, trimmed) to None
            # Preserve ALL other values including empty strings, UUIDs, etc.
            if str_value.strip().upper() == "N/A":
                return None
            # Return the actual string value (preserve all real values)
            return str_value
        
        return LogEntryResponse(
            id=item.get("id", ""),
            timestamp=datetime.fromisoformat(item.get("timestamp", "").replace("Z", "+00:00")),
            level=item.get("level", ""),
            correlation_id=_normalize_value(item.get("correlation_id")),
            user_id=_normalize_value(item.get("user_id")),
            username=_normalize_value(item.get("username")),
            user_type=_normalize_value(item.get("user_type")),
            email=_normalize_value(item.get("email")),
            is_authenticated=item.get("is_authenticated", False),
            agent_type=_normalize_value(item.get("agent_type")),
            agent_name=_normalize_value(item.get("agent_name")),
            agent_method=_normalize_value(item.get("agent_method")),
            agent_count=item.get("agent_count", 0),
            module=item.get("module", ""),
            function=item.get("function", ""),
            line=item.get("line", 0),
            message=item.get("message", ""),
            process_id=item.get("process_id", ""),
            thread_id=item.get("thread_id", 0),
            thread_name=item.get("thread_name", "")
        )


# Global instance
logs_service = LogsService()
