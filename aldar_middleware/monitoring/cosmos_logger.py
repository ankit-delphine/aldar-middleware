"""Cosmos DB logging handler for centralized log storage and request/response tracking."""

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from queue import Queue, Empty
import asyncio

from loguru import logger

try:
    from azure.cosmos import CosmosClient, PartitionKey, exceptions
    COSMOS_AVAILABLE = True
except ImportError:
    COSMOS_AVAILABLE = False

from aldar_middleware.settings import settings


class CosmosLoggingConfig:
    """Configuration for Cosmos DB logging."""
    
    def __init__(self):
        """Initialize Cosmos DB logging configuration."""
        self.enabled = settings.cosmos_logging_enabled
        self.connection_string = settings.cosmos_endpoint
        self.database_name = settings.cosmos_logging_database_name
        self.container_name = settings.cosmos_logging_container_name
        self.throughput = settings.cosmos_logging_throughput
        self.batch_size = settings.cosmos_logging_batch_size
        self.flush_interval = settings.cosmos_logging_flush_interval
        
    def is_valid(self) -> bool:
        """Validate configuration."""
        if not self.enabled:
            return False
        return bool(self.connection_string)


class CosmosLoggingHandler:
    """Handler for sending logs to Azure Cosmos DB."""
    
    def __init__(self, config: CosmosLoggingConfig):
        """Initialize Cosmos DB logging handler.
        
        Args:
            config: Cosmos DB logging configuration
        """
        self.config = config
        self.client: Optional[CosmosClient] = None
        self.database: Optional[Any] = None
        self.container: Optional[Any] = None
        self.log_queue: Queue = Queue()
        self.batch_buffer: List[Dict[str, Any]] = []
        self.lock = threading.Lock()
        self.running = False
        self.batch_thread: Optional[threading.Thread] = None
        
        # Configure verbose logging based on settings
        self._configure_verbose_logging()
    
    def _configure_verbose_logging(self):
        """Configure verbose logging for Cosmos DB operations."""
        import logging
        
        if settings.cosmos_log_verbose:
            # Enable verbose logging
            logging.getLogger("azure.cosmos").setLevel(logging.INFO)
            logging.getLogger("azure.core").setLevel(logging.INFO)
            logging.getLogger("azure.monitor").setLevel(logging.INFO)
        else:
            # Disable verbose logging
            logging.getLogger("azure.cosmos").setLevel(logging.WARNING)
            logging.getLogger("azure.core").setLevel(logging.WARNING)
            logging.getLogger("azure.monitor").setLevel(logging.WARNING)
        
        # Always suppress Azure SDK HTTP logging policy (too verbose)
        # This prevents INFO logs from azure.core.pipeline.policies.http_logging_policy
        logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
        
    async def initialize(self) -> bool:
        """Initialize Cosmos DB connection and containers.
        
        Returns:
            True if initialization successful, False otherwise
        """
        if not COSMOS_AVAILABLE:
            logger.warning("Azure Cosmos SDK not available. Cosmos logging disabled.")
            return False
            
        if not self.config.is_valid():
            logger.warning("Cosmos DB logging not properly configured. Skipping initialization.")
            return False
        
        try:
            # Check connection string format
            conn_str = self.config.connection_string
            if not conn_str:
                logger.error("Cosmos DB connection string is empty")
                return False
            
            # Validate connection string format
            if "AccountEndpoint=" not in conn_str or "AccountKey=" not in conn_str:
                logger.error(
                    f"Invalid Cosmos DB connection string format. "
                    f"Expected format: 'AccountEndpoint=https://...;AccountKey=...;' "
                    f"Got: '{conn_str[:50]}...'"
                )
                logger.error(
                    "Please get your connection string from Azure Portal → "
                    "Cosmos DB → Keys → PRIMARY CONNECTION STRING"
                )
                return False
            
            # Create client using connection string
            self.client = CosmosClient.from_connection_string(conn_str)
            logger.info(f"Connected to Cosmos DB using connection string")
            
            # Create or get database (without throughput for serverless)
            try:
                self.database = self.client.create_database(
                    id=self.config.database_name
                )
                logger.info(f"Created Cosmos DB database: {self.config.database_name}")
            except exceptions.CosmosResourceExistsError:
                self.database = self.client.get_database_client(self.config.database_name)
                logger.info(f"Using existing Cosmos DB database: {self.config.database_name}")
            
            # Create or get container for logs (serverless: no throughput needed)
            try:
                self.container = self.database.create_container(
                    id=self.config.container_name,
                    partition_key=PartitionKey(path="/id")
                )
                logger.info(
                    f"Created Cosmos DB container: {self.config.container_name} "
                    f"with partition key /id"
                )
            except exceptions.CosmosResourceExistsError:
                self.container = self.database.get_container_client(self.config.container_name)
                logger.info(
                    f"Using existing Cosmos DB container: {self.config.container_name}"
                )
            
            # Start background batch writer
            self.running = True
            self.batch_thread = threading.Thread(daemon=True, target=self._batch_writer_loop)
            self.batch_thread.start()
            logger.info("Cosmos DB logging handler initialized successfully")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize Cosmos DB logging: {e}")
            return False
    
    def _batch_writer_loop(self):
        """Background thread loop for batching and writing logs."""
        import time
        
        while self.running:
            try:
                # Collect batch
                start_time = time.time()
                while len(self.batch_buffer) < self.config.batch_size:
                    elapsed = time.time() - start_time
                    if elapsed > self.config.flush_interval:
                        break
                    
                    try:
                        log_entry = self.log_queue.get(timeout=0.5)
                        with self.lock:
                            self.batch_buffer.append(log_entry)
                    except Empty:
                        continue
                
                # Flush batch if not empty
                if self.batch_buffer:
                    self._flush_batch()
                    
            except Exception as e:
                logger.error(f"Error in batch writer loop: {e}")
    
    def _make_json_serializable(self, obj: Any) -> Any:
        """Convert objects to JSON-serializable types.
        
        Args:
            obj: Object to make JSON-serializable
            
        Returns:
            JSON-serializable version of the object
        """
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj
        elif isinstance(obj, dict):
            return {k: self._make_json_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._make_json_serializable(item) for item in obj]
        elif isinstance(obj, datetime):
            return obj.isoformat()
        else:
            # Convert any other object to string
            return str(obj)
    
    def _flush_batch(self):
        """Flush accumulated log batch to Cosmos DB."""
        if not self.batch_buffer or not self.container:
            return
        
        batch_to_send = []
        with self.lock:
            batch_to_send = self.batch_buffer.copy()
            self.batch_buffer.clear()
        
        try:
            for log_entry in batch_to_send:
                # Ensure all values are JSON-serializable
                serializable_entry = self._make_json_serializable(log_entry)
                serializable_entry["_id"] = str(uuid.uuid4())
                
                # Verify it's JSON serializable before sending
                json.dumps(serializable_entry)
                
                self.container.create_item(body=serializable_entry)
                
        except Exception as e:
            logger.error(f"Error flushing logs to Cosmos DB: {e}")
            # Re-add to buffer for retry
            with self.lock:
                self.batch_buffer.extend(batch_to_send)
    
    def log_sink(self, message):
        """Loguru sink for receiving log records.
        
        Args:
            message: Log message record from loguru
        """
        try:
            record = message.record
            
            # Extract thread info safely
            thread_id = None
            thread_name = None
            if record["thread"]:
                try:
                    thread_id = int(record["thread"].id) if hasattr(record["thread"], 'id') else None
                    thread_name = str(record["thread"].name) if hasattr(record["thread"], 'name') else None
                except (AttributeError, TypeError, ValueError):
                    pass
            
            # Extract process info safely
            process_id = None
            try:
                process_id = int(record["process"]) if record["process"] is not None else None
            except (TypeError, ValueError):
                process_id = str(record["process"]) if record["process"] is not None else None
            
            # Extract correlation ID with fallback to message parsing
            correlation_id = record["extra"].get("correlation_id", "N/A")
            
            # If correlation_id is "N/A", try to extract from message
            if correlation_id == "N/A":
                import re
                message = str(record["message"])
                # Look for correlation ID in format [uuid] at the start of message
                uuid_pattern = r'^\[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]'
                match = re.match(uuid_pattern, message, re.IGNORECASE)
                if match:
                    correlation_id = match.group(1)
            
            log_entry = {
                "id": str(uuid.uuid4()),
                "timestamp": record["time"].isoformat(),
                "level": record["level"].name,
                "correlation_id": str(correlation_id),
                "user_id": str(record["extra"].get("user_id", "N/A")),
                "username": str(record["extra"].get("username", "N/A")),
                "user_type": str(record["extra"].get("user_type", "N/A")),
                "email": str(record["extra"].get("email", "N/A")),
                "is_authenticated": bool(record["extra"].get("is_authenticated", False)),
                "agent_type": str(record["extra"].get("agent_type", "N/A")),
                "agent_name": str(record["extra"].get("agent_name", "N/A")),
                "agent_method": str(record["extra"].get("agent_method", "N/A")),
                "agent_count": int(record["extra"].get("agent_count", 0)),
                "module": str(record["name"]),
                "function": str(record["function"]),
                "line": int(record["line"]),
                "message": str(record["message"]),
                "process_id": process_id,
                "thread_id": thread_id,
                "thread_name": thread_name,
            }
            
            # Add exception info if present
            if record["exception"]:
                log_entry["exception"] = {
                    "type": record["exception"].type.__name__,
                    "value": str(record["exception"].value),
                }
            
            # Add request/response data if available
            if "request_data" in record["extra"]:
                log_entry["request_data"] = record["extra"]["request_data"]
            
            if "response_data" in record["extra"]:
                log_entry["response_data"] = record["extra"]["response_data"]
            
            # Add chat_event if available (for user logs 3.0)
            if "chat_event" in record["extra"]:
                log_entry["chat_event"] = record["extra"]["chat_event"]
            
            # Add to queue for batch processing
            self.log_queue.put(log_entry)
            
        except Exception as e:
            # Fallback: ensure we don't break logging
            logger.opt(exception=True).error(f"Error in Cosmos DB sink: {e}")
    
    def shutdown(self):
        """Shutdown the handler and flush remaining logs."""
        self.running = False
        
        # Final flush
        if self.batch_thread:
            self.batch_thread.join(timeout=5)
        
        # Flush any remaining logs
        self._flush_batch()
        
        if self.client:
            # CosmosClient doesn't have a close method
            # The client will be garbage collected when the handler is destroyed
            pass
        
        logger.info("Cosmos DB logging handler shut down")


# Global handler instance
_cosmos_handler: Optional[CosmosLoggingHandler] = None


async def initialize_cosmos_logging() -> bool:
    """Initialize Cosmos DB logging globally.
    
    Returns:
        True if initialization successful, False otherwise
    """
    global _cosmos_handler
    
    config = CosmosLoggingConfig()
    
    if not config.is_valid():
        logger.debug("Cosmos DB logging not configured")
        return False
    
    _cosmos_handler = CosmosLoggingHandler(config)
    
    if await _cosmos_handler.initialize():
        # Add to loguru
        logger.add(
            _cosmos_handler.log_sink,
            level="DEBUG",
            format="{message}",
            backtrace=True,
            diagnose=True,
            colorize=False,
        )
        logger.info("Cosmos DB logging enabled")
        return True
    
    return False


def shutdown_cosmos_logging():
    """Shutdown Cosmos DB logging handler."""
    global _cosmos_handler
    
    if _cosmos_handler:
        _cosmos_handler.shutdown()
        _cosmos_handler = None


def log_request_response(
    correlation_id: str,
    method: str,
    path: str,
    status_code: int,
    request_body: Optional[Dict[str, Any]] = None,
    response_body: Optional[Dict[str, Any]] = None,
    duration_ms: float = 0,
    user_id: Optional[str] = None,
) -> None:
    """Log request and response data to Cosmos DB.
    
    Args:
        correlation_id: Request correlation ID
        method: HTTP method
        path: Request path
        status_code: Response status code
        request_body: Request body data
        response_body: Response body data
        duration_ms: Request duration in milliseconds
        user_id: User ID if available
    """
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        "request": {
            "method": method,
            "path": path,
            "body": request_body,
        },
        "response": {
            "status_code": status_code,
            "body": response_body,
        },
        "performance": {
            "duration_ms": duration_ms,
        },
    }
    
    if user_id:
        log_entry["user_id"] = user_id
    
    logger.bind(
        request_data=log_entry["request"],
        response_data=log_entry["response"],
    ).info(
        f"API Request: {method} {path} -> {status_code} ({duration_ms}ms)"
    )


def get_cosmos_handler() -> Optional[CosmosLoggingHandler]:
    """Get the global Cosmos DB logging handler.
    
    Returns:
        The Cosmos DB logging handler or None if not initialized
    """
    return _cosmos_handler


def log_agent_health_check(
    agent_id: str,
    status: str,
    response_time_ms: Optional[int] = None,
    reason: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Log an agent health check event to Cosmos DB.

    The document schema is lightweight and optimized for append-only logging.
    """
    handler = get_cosmos_handler()
    if not handler:
        # Fallback to normal logger; cosmos disabled
        logger.info(
            f"[agent_health_check] agent_id={agent_id} status={status} response_time_ms={response_time_ms} reason={reason} details={details}"
        )
        return

    log_item: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "type": "agent_health_check",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": agent_id,
        "status": status,
        "response_time_ms": response_time_ms,
        "reason": reason,
        "details": details or {},
    }

    # Enqueue directly to the batch buffer/queue via the handler
    try:
        handler.enqueue_log(log_item)
    except Exception as e:
        logger.warning(f"Failed to enqueue agent health check log to Cosmos: {e}")