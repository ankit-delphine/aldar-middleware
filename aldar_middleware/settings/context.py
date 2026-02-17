import time
from contextvars import ContextVar
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime


# Context variables for async-safe storage
_correlation_id_context: ContextVar[Optional[str]] = ContextVar(
    "correlation_id", default=None
)
_user_context: ContextVar[Optional["UserContext"]] = ContextVar(
    "user_context", default=None
)
_agent_context: ContextVar[Optional["AgentContext"]] = ContextVar(
    "agent_context", default=None
)


@dataclass
class UserContext:
    """Context for tracking user information within a request."""
    
    user_id: Optional[str] = None
    username: Optional[str] = None
    user_type: Optional[str] = None  # "admin", "user", "guest", etc.
    email: Optional[str] = None
    is_authenticated: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert user context to dictionary."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "user_type": self.user_type,
            "email": self.email,
            "is_authenticated": self.is_authenticated,
        }


@dataclass
class AgentCall:
    """Represents a single agent call within a request."""
    
    agent_type: str  # "openai", "mcp"
    agent_name: str  # "gpt-4", "mcp-server-1"
    method: str  # "chat.completion", "analyze_sentiment", "mcp_method_name"
    start_time: float
    end_time: Optional[float] = None
    duration: Optional[float] = None
    status: str = "pending"  # "pending", "success", "error", "timeout"
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def complete(self, status: str, error_type: Optional[str] = None, 
                 error_message: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None):
        """Mark the agent call as complete."""
        self.end_time = time.time()
        self.duration = self.end_time - self.start_time
        self.status = status
        self.error_type = error_type
        self.error_message = error_message
        if metadata:
            self.metadata.update(metadata)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert agent call to dictionary."""
        return {
            "agent_type": self.agent_type,
            "agent_name": self.agent_name,
            "method": self.method,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "status": self.status,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }


@dataclass
class AgentContext:
    """Context for tracking agent calls within a request."""
    
    correlation_id: str
    agent_calls: List[AgentCall] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    request_metadata: Dict[str, Any] = field(default_factory=dict)
    
    def add_agent_call(
        self,
        agent_type: str,
        agent_name: str,
        method: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> AgentCall:
        """Add a new agent call to the context."""
        agent_call = AgentCall(
            agent_type=agent_type,
            agent_name=agent_name,
            method=method,
            start_time=time.time(),
            metadata=metadata or {}
        )
        self.agent_calls.append(agent_call)
        return agent_call
    
    def get_agent_sequence(self) -> List[str]:
        """Get the sequence of agents called."""
        return [f"{call.agent_type}:{call.agent_name}" for call in self.agent_calls]
    
    def get_agent_count(self) -> int:
        """Get the total number of agents called."""
        return len(self.agent_calls)
    
    def get_total_duration(self) -> float:
        """Get the total duration of the request."""
        return time.time() - self.start_time
    
    def get_agent_statistics(self) -> Dict[str, Any]:
        """Get statistics about agent calls."""
        total_calls = len(self.agent_calls)
        successful_calls = sum(1 for call in self.agent_calls if call.status == "success")
        failed_calls = sum(1 for call in self.agent_calls if call.status == "error")
        
        agent_durations = [call.duration for call in self.agent_calls if call.duration is not None]
        total_agent_time = sum(agent_durations) if agent_durations else 0
        
        return {
            "total_calls": total_calls,
            "successful_calls": successful_calls,
            "failed_calls": failed_calls,
            "success_rate": successful_calls / total_calls if total_calls > 0 else 0,
            "total_agent_time": total_agent_time,
            "total_request_time": self.get_total_duration(),
            "agent_overhead": self.get_total_duration() - total_agent_time,
            "agent_sequence": self.get_agent_sequence(),
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert agent context to dictionary."""
        return {
            "correlation_id": self.correlation_id,
            "agent_calls": [call.to_dict() for call in self.agent_calls],
            "start_time": self.start_time,
            "total_duration": self.get_total_duration(),
            "statistics": self.get_agent_statistics(),
            "request_metadata": self.request_metadata,
        }


# Correlation ID management functions
def set_correlation_id(correlation_id: str) -> None:
    """Set the correlation ID for the current context.
    
    Args:
        correlation_id: The correlation ID to set
    """
    _correlation_id_context.set(correlation_id)
    # Initialize agent context with correlation ID only if not already set
    # This prevents overwriting existing agent context data
    existing_agent_ctx = _agent_context.get()
    if existing_agent_ctx is None:
        agent_ctx = AgentContext(correlation_id=correlation_id)
        _agent_context.set(agent_ctx)
    else:
        # Update correlation ID in existing context without losing agent calls
        existing_agent_ctx.correlation_id = correlation_id


def get_correlation_id() -> Optional[str]:
    """Get the correlation ID from the current context.
    
    Returns:
        The correlation ID if set, None otherwise
    """
    return _correlation_id_context.get()


def clear_correlation_id() -> None:
    """Clear the correlation ID from the current context."""
    _correlation_id_context.set(None)
    _agent_context.set(None)


# Agent context management functions
def get_agent_context() -> Optional[AgentContext]:
    """Get the agent context from the current context.
    
    Returns:
        The agent context if set, None otherwise
    """
    return _agent_context.get()


def set_agent_context(agent_context: AgentContext) -> None:
    """Set the agent context for the current context.
    
    Args:
        agent_context: The agent context to set
    """
    _agent_context.set(agent_context)


def add_agent_call(
    agent_type: str,
    agent_name: str,
    method: str,
    metadata: Optional[Dict[str, Any]] = None
) -> Optional[AgentCall]:
    """Add an agent call to the current context.
    
    Args:
        agent_type: Type of agent (e.g., "openai", "mcp")
        agent_name: Name of the agent (e.g., "gpt-4", "mcp-server-1")
        method: Method being called
        metadata: Optional metadata for the agent call
        
    Returns:
        The created AgentCall object, or None if no context is set
    """
    agent_ctx = get_agent_context()
    if agent_ctx:
        return agent_ctx.add_agent_call(agent_type, agent_name, method, metadata)
    return None


def track_agent_call(
    agent_type: str,
    agent_name: str,
    method: str,
    metadata: Optional[Dict[str, Any]] = None
) -> Optional[AgentCall]:
    """Track an agent call in the current context.
    
    Alias for add_agent_call - tracks an agent call within the request context.
    
    Args:
        agent_type: Type of agent (e.g., "openai", "mcp")
        agent_name: Name of the agent (e.g., "gpt-4", "mcp-server-1")
        method: Method being called
        metadata: Optional metadata for the agent call
        
    Returns:
        The created AgentCall object, or None if no context is set
    """
    return add_agent_call(agent_type, agent_name, method, metadata)


def get_agent_statistics() -> Optional[Dict[str, Any]]:
    """Get agent statistics from the current context.
    
    Returns:
        Agent statistics dictionary, or None if no context is set
    """
    agent_ctx = get_agent_context()
    if agent_ctx:
        return agent_ctx.get_agent_statistics()
    return None


def set_request_metadata(key: str, value: Any) -> None:
    """Set metadata for the current request.
    
    Args:
        key: Metadata key
        value: Metadata value
    """
    agent_ctx = get_agent_context()
    if agent_ctx:
        agent_ctx.request_metadata[key] = value


# User context management functions
def set_user_context(
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    user_type: Optional[str] = None,
    email: Optional[str] = None,
    is_authenticated: bool = False
) -> None:
    """Set the user context for the current request.
    
    Args:
        user_id: User ID
        username: Username
        user_type: User type (admin, user, guest, etc.)
        email: User email
        is_authenticated: Whether user is authenticated
    """
    user_ctx = UserContext(
        user_id=user_id,
        username=username,
        user_type=user_type,
        email=email,
        is_authenticated=is_authenticated
    )
    _user_context.set(user_ctx)


def get_user_context() -> Optional[UserContext]:
    """Get the user context from the current context.
    
    Returns:
        The user context if set, None otherwise
    """
    return _user_context.get()


def clear_user_context() -> None:
    """Clear the user context from the current context."""
    _user_context.set(None)


def get_user_id() -> Optional[str]:
    """Get the user ID from the current context.
    
    Returns:
        The user ID if set, None otherwise
    """
    user_ctx = get_user_context()
    return user_ctx.user_id if user_ctx else None


def get_username() -> Optional[str]:
    """Get the username from the current context.
    
    Returns:
        The username if set, None otherwise
    """
    user_ctx = get_user_context()
    return user_ctx.username if user_ctx else None


def get_user_type() -> Optional[str]:
    """Get the user type from the current context.
    
    Returns:
        The user type if set, None otherwise
    """
    user_ctx = get_user_context()
    return user_ctx.user_type if user_ctx else None