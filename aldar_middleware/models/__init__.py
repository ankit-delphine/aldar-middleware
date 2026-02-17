"""Database models package."""

from aldar_middleware.models.user import User, UserAgent, UserPermission, UserGroup, UserGroupMembership
from aldar_middleware.models.mcp import MCPConnection, MCPMessage, AgentMethod, AgentMethodExecution
from aldar_middleware.models.monitoring import (
    Metric,
    Alert,
    CircuitBreakerState,
    DegradationEvent,
)
from aldar_middleware.models.feedback import (
    FeedbackData,
    FeedbackFile,
    FeedbackEntityType,
    FeedbackRating,
)
from aldar_middleware.models.routing import (
    AgentCapability,
    RoutingPolicy,
    RoutingExecution,
    Workflow,
    WorkflowExecution,
    WorkflowStep,
)
from aldar_middleware.models.quotas import (
    RateLimitConfig,
    RateLimitUsage,
    CostModel,
    UsageQuota,
    UserBudget,
    UsageReport,
)
from aldar_middleware.models.observability import (
    DistributedTrace,
    RequestResponseAudit,
    DatabaseQueryTrace,
    TraceSampleType,
    TraceStatusType,
)
from aldar_middleware.models.remediation import (
    RemediationAction,
    RemediationRule,
    RemediationExecution,
    ActionType,
    ExecutionStatus,
)
from aldar_middleware.models.menu import (
    Menu,
    LaunchpadApp,
    Agent,
    UserLaunchpadPin,
    UserAgentPin,
)
from aldar_middleware.models.rbac import (
    RBACRoleGroup,
    RBACRole,
    RBACService,
    RBACUser,
    RBACPermission,
    RBACRolePermission,
    RBACUserSession,
    RBACUserAccess,
    AzureADGroupRoleMapping,
    SERVICE_TYPES,
    COMMON_PERMISSIONS,
)

# New models for the schema requirements
from aldar_middleware.models.sessions import Session
from aldar_middleware.models.messages import Message
from aldar_middleware.models.agent_runs import AgentRun
from aldar_middleware.models.token_usage import TokenUsage
from aldar_middleware.models.user_agent_access import UserAgentAccess
from aldar_middleware.models.agent_tags import AgentTag
from aldar_middleware.models.agent_configuration import AgentConfiguration
from aldar_middleware.models.agent_tools import AgentTool
from aldar_middleware.models.attachment import Attachment

# ERD compatibility models for external team
from aldar_middleware.models.run import Run
from aldar_middleware.models.memory import Memory
from aldar_middleware.models.event import Event
from aldar_middleware.models.run_message import RunMessage
from aldar_middleware.models.run_metrics import RunMetrics
from aldar_middleware.models.run_input import RunInput

# Config and StarterPrompt models for 2.0 migration
from aldar_middleware.models.config import Config
from aldar_middleware.models.starter_prompt import StarterPrompt

# Log models for PostgreSQL storage
from aldar_middleware.models.logs import UserLog, AdminLog

# Admin config model
from aldar_middleware.models.admin_config import AdminConfig

# Agno memory model (external team's table)
from aldar_middleware.models.agno_memory import AgnoMemory

# User settings model
from aldar_middleware.models.user_settings import UserSettings

__all__ = [
    "User",
    "UserAgent",
    "UserPermission",
    "UserGroup",
    "UserGroupMembership",
    "MCPConnection",
    "MCPMessage",
    "AgentMethod",
    "AgentMethodExecution",
    "Metric",
    "Alert",
    "CircuitBreakerState",
    "DegradationEvent",
    "FeedbackData",
    "FeedbackFile",
    "FeedbackEntityType",
    "FeedbackRating",
    "AgentCapability",
    "RoutingPolicy",
    "RoutingExecution",
    "Workflow",
    "WorkflowExecution",
    "WorkflowStep",
    "RateLimitConfig",
    "RateLimitUsage",
    "CostModel",
    "UsageQuota",
    "UserBudget",
    "UsageReport",
    "DistributedTrace",
    "RequestResponseAudit",
    "DatabaseQueryTrace",
    "TraceSampleType",
    "TraceStatusType",
    "RemediationAction",
    "RemediationRule",
    "RemediationExecution",
    "ActionType",
    "ExecutionStatus",
    "Menu",
    "LaunchpadApp",
    "Agent",
    "UserLaunchpadPin",
    "UserAgentPin",
    # New models
    "Session",
    "Message",
    "AgentRun",
    "TokenUsage",
    "UserAgentAccess",
    "AgentTag",
    "AgentConfiguration",
    "AgentTool",
    "Attachment",
    # ERD compatibility models
    "Run",
    "Memory",
    "Event",
    "RunMessage",
    "RunMetrics",
    "RunInput",
    "Config",
    "StarterPrompt",
    "UserLog",
    "AdminLog",
    "RBACRoleGroup",
    "RBACRole",
    "RBACService",
    "RBACUser",
    "RBACPermission",
    "RBACRolePermission",
    "RBACUserSession",
    "RBACUserAccess",
    "AzureADGroupRoleMapping",
    "SERVICE_TYPES",
    "COMMON_PERMISSIONS",
    "AdminConfig",
    "AgnoMemory",
    "UserSettings",
]
