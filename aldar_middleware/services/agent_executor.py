"""Agent execution service for executing agent methods with validation and error handling."""

import json
import time
from typing import Dict, Any, Optional, List
from datetime import datetime
from uuid import UUID

import jsonschema
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from aldar_middleware.models.mcp import AgentMethod, AgentMethodExecution
from aldar_middleware.models.user import UserAgent
from aldar_middleware.orchestration.mcp import MCPService
from aldar_middleware.settings.context import get_correlation_id, track_agent_call
from aldar_middleware.monitoring.prometheus import record_agent_call, record_agent_error


class AgentExecutor:
    """Execute agent methods with validation, error handling, and retry logic."""

    def __init__(self):
        """Initialize agent executor."""
        self.mcp_service = MCPService()
        self.max_retries = 3
        self.retry_delay = 1.0  # seconds

    async def execute_method(
        self,
        db: AsyncSession,
        method_id: UUID,
        parameters: Dict[str, Any],
        user_id: Optional[UUID] = None,
        agent_id: Optional[UUID] = None,
    ) -> Dict[str, Any]:
        """
        Execute an agent method with full lifecycle tracking.
        
        Args:
            db: Database session
            method_id: ID of the method to execute
            parameters: Method parameters
            user_id: User ID (for tracking)
            agent_id: Agent ID (for tracking)
        
        Returns:
            Execution result with status and outcome
        """
        correlation_id = get_correlation_id()
        start_time = time.time()
        execution_record = None
        
        try:
            # Fetch method from database
            result = await db.execute(
                select(AgentMethod).where(AgentMethod.id == method_id)
            )
            method = result.scalar_one_or_none()
            
            if not method:
                raise ValueError(f"Method not found: {method_id}")
            
            if method.is_deprecated:
                logger.warning(
                    f"Executing deprecated method: {method.method_name} "
                    f"version={method.version}, correlation_id={correlation_id}"
                )
            
            # Create execution record
            execution_record = AgentMethodExecution(
                method_id=method_id,
                user_id=user_id,
                agent_id=agent_id,
                correlation_id=correlation_id,
                parameters=parameters,
                status="pending",
                created_at=datetime.utcnow()
            )
            db.add(execution_record)
            await db.flush()  # Get the ID without committing
            
            # Validate parameters against schema
            if method.parameters_schema:
                try:
                    jsonschema.validate(
                        instance=parameters,
                        schema=method.parameters_schema
                    )
                    logger.debug(
                        f"Parameter validation passed for method: {method.method_name}, "
                        f"correlation_id={correlation_id}"
                    )
                except jsonschema.ValidationError as e:
                    raise ValueError(f"Parameter validation failed: {e.message}")
            
            # Update status to running
            execution_record.status = "running"
            execution_record.started_at = datetime.utcnow()
            await db.flush()
            
            logger.info(
                f"Executing method: {method.method_name}, "
                f"execution_id={execution_record.id}, "
                f"correlation_id={correlation_id}"
            )
            
            # Execute method with retry logic
            result_data = await self._execute_with_retry(
                method=method,
                parameters=parameters,
                correlation_id=correlation_id
            )
            
            # Record successful execution
            execution_time = (time.time() - start_time) * 1000  # Convert to ms
            execution_record.status = "success"
            execution_record.result = result_data
            execution_record.completed_at = datetime.utcnow()
            execution_record.execution_duration_ms = int(execution_time)
            
            # Track metrics
            track_agent_call(
                agent_type="mcp_method",
                agent_name=method.method_name,
                method=method.method_name,
                duration=execution_time / 1000,
                status="success"
            )
            record_agent_call(
                agent_type="mcp_method",
                method=method.method_name,
                duration_seconds=execution_time / 1000,
                status="success"
            )
            
            logger.info(
                f"Method executed successfully: {method.method_name}, "
                f"execution_id={execution_record.id}, "
                f"duration={execution_time:.2f}ms, "
                f"correlation_id={correlation_id}"
            )
            
            await db.commit()
            
            return {
                "execution_id": str(execution_record.id),
                "status": "success",
                "result": result_data,
                "duration_ms": execution_record.execution_duration_ms,
                "correlation_id": correlation_id
            }
            
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            error_type = type(e).__name__
            error_msg = str(e)
            
            logger.error(
                f"Error executing method: {error_msg}, "
                f"error_type={error_type}, "
                f"execution_id={execution_record.id if execution_record else 'N/A'}, "
                f"correlation_id={correlation_id}"
            )
            
            # Update execution record with error
            if execution_record:
                execution_record.status = "error"
                execution_record.error_message = error_msg
                execution_record.completed_at = datetime.utcnow()
                execution_record.execution_duration_ms = int(execution_time)
                await db.commit()
            
            # Track error metrics
            record_agent_error(
                agent_type="mcp_method",
                error_type=error_type
            )
            
            return {
                "execution_id": str(execution_record.id) if execution_record else "N/A",
                "status": "error",
                "error": error_msg,
                "error_type": error_type,
                "duration_ms": int(execution_time),
                "correlation_id": correlation_id
            }

    async def _execute_with_retry(
        self,
        method: AgentMethod,
        parameters: Dict[str, Any],
        correlation_id: str
    ) -> Dict[str, Any]:
        """
        Execute method with retry logic and exponential backoff.
        
        Args:
            method: AgentMethod to execute
            parameters: Method parameters
            correlation_id: Correlation ID for tracing
        
        Returns:
            Method execution result
        
        Raises:
            Exception: If all retries fail
        """
        last_error = None
        
        for attempt in range(1, self.max_retries + 1):
            try:
                # Send message to MCP server
                result = await self.mcp_service.send_message(
                    connection_id=str(method.connection_id),
                    method=method.method_name,
                    params=parameters
                )
                
                logger.debug(
                    f"Method execution succeeded on attempt {attempt}, "
                    f"method={method.method_name}, correlation_id={correlation_id}"
                )
                
                return result
                
            except Exception as e:
                last_error = e
                is_last_attempt = attempt == self.max_retries
                
                if is_last_attempt:
                    logger.error(
                        f"Method execution failed after {self.max_retries} attempts, "
                        f"method={method.method_name}, error={str(e)}, "
                        f"correlation_id={correlation_id}"
                    )
                    raise
                
                # Calculate backoff delay
                wait_time = self.retry_delay * (2 ** (attempt - 1))
                
                logger.warning(
                    f"Method execution failed on attempt {attempt}, "
                    f"retrying in {wait_time}s, "
                    f"method={method.method_name}, error={str(e)}, "
                    f"correlation_id={correlation_id}"
                )
                
                # Wait before retry
                await asyncio.sleep(wait_time)
        
        raise last_error

    async def validate_method_parameters(
        self,
        db: AsyncSession,
        method_id: UUID,
        parameters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Validate parameters against method schema without executing.
        
        Args:
            db: Database session
            method_id: ID of the method
            parameters: Parameters to validate
        
        Returns:
            Validation result
        """
        try:
            result = await db.execute(
                select(AgentMethod).where(AgentMethod.id == method_id)
            )
            method = result.scalar_one_or_none()
            
            if not method:
                return {
                    "valid": False,
                    "error": f"Method not found: {method_id}"
                }
            
            if not method.parameters_schema:
                return {
                    "valid": True,
                    "message": "Method has no parameter schema"
                }
            
            try:
                jsonschema.validate(
                    instance=parameters,
                    schema=method.parameters_schema
                )
                return {
                    "valid": True,
                    "message": "Parameters are valid"
                }
            except jsonschema.ValidationError as e:
                return {
                    "valid": False,
                    "error": e.message,
                    "path": list(e.path)
                }
            
        except Exception as e:
            return {
                "valid": False,
                "error": str(e)
            }

    async def get_method_info(
        self,
        db: AsyncSession,
        method_id: UUID
    ) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a method.
        
        Args:
            db: Database session
            method_id: ID of the method
        
        Returns:
            Method information or None if not found
        """
        result = await db.execute(
            select(AgentMethod).where(AgentMethod.id == method_id)
        )
        method = result.scalar_one_or_none()
        
        if not method:
            return None
        
        return {
            "id": str(method.id),
            "name": method.method_name,
            "display_name": method.display_name,
            "description": method.description,
            "version": method.version,
            "is_deprecated": method.is_deprecated,
            "parameters_schema": method.parameters_schema,
            "return_type": method.return_type,
            "tags": method.tags or [],
            "metadata": method.metadata,
            "created_at": method.created_at.isoformat(),
            "updated_at": method.updated_at.isoformat()
        }

    async def get_execution_history(
        self,
        db: AsyncSession,
        method_id: Optional[UUID] = None,
        agent_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Get execution history with optional filtering.
        
        Args:
            db: Database session
            method_id: Filter by method ID
            agent_id: Filter by agent ID
            user_id: Filter by user ID
            limit: Maximum number of records
            offset: Offset for pagination
        
        Returns:
            List of execution records
        """
        query = select(AgentMethodExecution)
        
        if method_id:
            query = query.where(AgentMethodExecution.method_id == method_id)
        if agent_id:
            query = query.where(AgentMethodExecution.agent_id == agent_id)
        if user_id:
            query = query.where(AgentMethodExecution.user_id == user_id)
        
        # Order by creation date descending
        query = query.order_by(AgentMethodExecution.created_at.desc())
        query = query.limit(limit).offset(offset)
        
        result = await db.execute(query)
        executions = result.scalars().all()
        
        return [
            {
                "id": str(exec.id),
                "method_id": str(exec.method_id),
                "agent_id": str(exec.agent_id) if exec.agent_id else None,
                "user_id": str(exec.user_id) if exec.user_id else None,
                "status": exec.status,
                "parameters": exec.parameters,
                "result": exec.result,
                "error_message": exec.error_message,
                "duration_ms": exec.execution_duration_ms,
                "retry_count": exec.retry_count,
                "created_at": exec.created_at.isoformat(),
                "started_at": exec.started_at.isoformat() if exec.started_at else None,
                "completed_at": exec.completed_at.isoformat() if exec.completed_at else None
            }
            for exec in executions
        ]


# Import asyncio at the end to avoid circular imports
import asyncio