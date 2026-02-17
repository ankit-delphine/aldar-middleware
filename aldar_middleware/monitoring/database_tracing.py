"""Database query tracing using SQLAlchemy event listeners.

Tracks all database queries with timing, parameters, and performance metrics.
Integrates with distributed tracing to capture queries within request context.
"""

import time
import inspect
import re
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from sqlalchemy import event
from sqlalchemy.engine import Engine, ExecutionContext
from sqlalchemy.pool import Pool
from loguru import logger

from aldar_middleware.settings import settings
from aldar_middleware.settings.context import get_correlation_id
from aldar_middleware.monitoring.pii_masking import get_pii_masking_service


class DatabaseTracer:
    """Tracer for database queries using SQLAlchemy events."""
    
    # Query execution context (thread-local, will be per-request in async context)
    _query_start_times: Dict[int, float] = {}
    
    def __init__(self):
        """Initialize the database tracer."""
        self.enabled = settings.trace_database_queries
        self.slow_threshold_ms = settings.trace_query_slow_threshold_ms
        self.masking_service = get_pii_masking_service()
    
    def install_hooks(self, engine: Engine) -> None:
        """Install SQLAlchemy event hooks on the engine.
        
        Args:
            engine: SQLAlchemy engine instance
        """
        if not self.enabled:
            return
        
        # Before execute
        event.listen(engine, "before_cursor_execute", self.before_cursor_execute)
        
        # After execute
        event.listen(engine, "after_cursor_execute", self.after_cursor_execute)
        
        # Execute error
        event.listen(engine, "handle_error", self.handle_error)
        
        logger.info("Database query tracing hooks installed")
    
    def before_cursor_execute(
        self,
        conn,
        cursor,
        statement: str,
        parameters,
        context,
        executemany: bool,
    ) -> None:
        """Called before a cursor.execute() call.
        
        Args:
            conn: Database connection
            cursor: Cursor object
            statement: SQL statement
            parameters: Query parameters
            context: Execution context
            executemany: Whether executing multiple statements
        """
        try:
            if not context:
                return
            
            # Store start time using statement ID
            context._query_start_time = time.time()
            
            # Extract and sanitize query
            query_type = self._extract_query_type(statement)
            
            # Get caller info
            caller_info = self._get_caller_info()
            
            # Log at debug level
            correlation_id = get_correlation_id() or "unknown"
            logger.debug(
                f"Database query starting: type={query_type}, correlation_id={correlation_id}, "
                f"caller={caller_info['file']}:{caller_info['function']}:{caller_info['line']}"
            )
        
        except Exception as e:
            logger.error(f"Error in before_cursor_execute: {e}")
    
    def after_cursor_execute(
        self,
        conn,
        cursor,
        statement: str,
        parameters,
        context,
        executemany: bool,
    ) -> None:
        """Called after a cursor.execute() call.
        
        Args:
            conn: Database connection
            cursor: Cursor object
            statement: SQL statement
            parameters: Query parameters
            context: Execution context
            executemany: Whether executing multiple statements
        """
        try:
            if not context or not hasattr(context, "_query_start_time"):
                return
            
            # Calculate duration
            duration_ms = int((time.time() - context._query_start_time) * 1000)
            
            # Extract query info
            query_type = self._extract_query_type(statement)
            slow_query = duration_ms > self.slow_threshold_ms
            
            # Sanitize query for logging
            sanitized_query = self._sanitize_query(statement)
            
            # Log slow queries
            if slow_query:
                logger.warning(
                    f"Slow database query detected: type={query_type}, "
                    f"duration_ms={duration_ms}, threshold_ms={self.slow_threshold_ms}, "
                    f"query={sanitized_query}"
                )
            else:
                logger.debug(
                    f"Database query completed: type={query_type}, duration_ms={duration_ms}"
                )
            
            # Try to get row count
            rows_affected = None
            try:
                if query_type in ["INSERT", "UPDATE", "DELETE"]:
                    rows_affected = cursor.rowcount
                elif query_type == "SELECT":
                    rows_affected = cursor.rowcount if cursor.rowcount > 0 else None
            except Exception:
                pass
        
        except Exception as e:
            logger.error(f"Error in after_cursor_execute: {e}")
    
    def handle_error(self, exception_context) -> None:
        """Called when an error occurs during execution.
        
        Args:
            exception_context: Exception context
        """
        try:
            if not exception_context or not exception_context.original_exception:
                return
            
            error = exception_context.original_exception
            statement = exception_context.statement if hasattr(exception_context, "statement") else "unknown"
            
            logger.error(
                f"Database query error: statement={statement}, error={error}",
                exc_info=error
            )
        
        except Exception as e:
            logger.error(f"Error in handle_error: {e}")
    
    def _extract_query_type(self, statement: str) -> str:
        """Extract SQL query type from statement.
        
        Args:
            statement: SQL statement
            
        Returns:
            Query type (SELECT, INSERT, UPDATE, DELETE, etc.)
        """
        # Remove leading whitespace and comments
        statement = statement.strip()
        statement = re.sub(r'^--.*?\n', '', statement, flags=re.MULTILINE)
        statement = re.sub(r'^/\*.*?\*/', '', statement, flags=re.DOTALL)
        statement = statement.strip()
        
        # Extract first word
        match = re.match(r'(\w+)', statement)
        if match:
            return match.group(1).upper()
        
        return "UNKNOWN"
    
    def _sanitize_query(self, statement: str, max_length: int = 500) -> str:
        """Sanitize SQL query for logging (remove sensitive data).
        
        Args:
            statement: SQL statement
            max_length: Maximum length of returned query
            
        Returns:
            Sanitized query
        """
        # Mask string values (basic PII masking for SQL)
        sanitized = re.sub(
            r"'[^']*'",
            "'***'",
            statement
        )
        
        # Truncate if too long
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length] + "..."
        
        return sanitized
    
    def _get_caller_info(self) -> Dict[str, Any]:
        """Get information about the caller of the database query.
        
        Returns:
            Dictionary with file, function, and line number
        """
        try:
            # Walk up the stack to find the first frame outside SQLAlchemy
            frame = inspect.currentframe()
            while frame:
                file_name = frame.f_code.co_filename
                
                # Skip SQLAlchemy internal frames
                if "sqlalchemy" not in file_name and "aldar_middleware" in file_name:
                    return {
                        "file": file_name.split("/")[-1],
                        "function": frame.f_code.co_name,
                        "line": frame.f_lineno,
                    }
                
                frame = frame.f_back
        except Exception:
            pass
        
        return {"file": "unknown", "function": "unknown", "line": 0}


# Global instance
_database_tracer: Optional[DatabaseTracer] = None


def get_database_tracer() -> DatabaseTracer:
    """Get or create the global database tracer.
    
    Returns:
        DatabaseTracer instance
    """
    global _database_tracer
    
    if _database_tracer is None:
        _database_tracer = DatabaseTracer()
        logger.info(
            f"Database Query Tracer initialized. "
            f"Enabled: {_database_tracer.enabled}. "
            f"Slow query threshold: {_database_tracer.slow_threshold_ms}ms"
        )
    
    return _database_tracer


def install_database_tracing(engine: Engine) -> None:
    """Install database query tracing on a SQLAlchemy engine.
    
    Should be called once during application initialization.
    
    Args:
        engine: SQLAlchemy engine instance
    """
    tracer = get_database_tracer()
    tracer.install_hooks(engine)