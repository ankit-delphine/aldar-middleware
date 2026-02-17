"""Main API router."""

from fastapi import APIRouter

from aldar_middleware.routes.admin_agents import router as admin_agents_router
from aldar_middleware.routes.agents import router as agents_router
from aldar_middleware.routes.attachments import router as attachments_router
from aldar_middleware.routes.auth import router as auth_router
from aldar_middleware.routes.azure_ad_obo import router as azure_ad_obo_router
from aldar_middleware.routes.chat import router as chat_router
from aldar_middleware.routes.chat_download import router as chat_download_router
from aldar_middleware.routes.chat_messages_data import router as chat_messages_data_router
from aldar_middleware.routes.context_memory import router as context_memory_router
from aldar_middleware.routes.demo import router as demo_router
from aldar_middleware.routes.deployment_versions import router as deployment_versions_router
from aldar_middleware.routes.feedback import router as feedback_router
from aldar_middleware.routes.mcp import router as mcp_router
from aldar_middleware.routes.menu import router as menu_router
from aldar_middleware.routes.observability import router as observability_router
from aldar_middleware.routes.orchestration import (
    query_agent_router,
)
from aldar_middleware.routes.orchestration import (
    router as orchestration_router,
)
from aldar_middleware.routes.question_tracker import router as question_tracker_router
from aldar_middleware.routes.quotas import router as quotas_router
from aldar_middleware.routes.remediation import router as remediation_router
from aldar_middleware.routes.routing import router as routing_router
from aldar_middleware.routes.starter_prompts import router as starter_prompts_router
from aldar_middleware.routes.user_agents import router as user_agents_router
from aldar_middleware.routes.user_logs import router as user_logs_router
from aldar_middleware.routes.user_memory import router as user_memory_router
from aldar_middleware.routes.user_settings import router as user_settings_router
from aldar_middleware.routes.web_pubsub import router as web_pubsub_router
from aldar_middleware.routes.workflows import router as workflows_router

api_router = APIRouter()

# Include all API routers
# Attachments API first (for documentation ordering)
api_router.include_router(
    auth_router, prefix="/auth", tags=["authentication"],
)
api_router.include_router(
    azure_ad_obo_router,
)
api_router.include_router(
    attachments_router, prefix="/attachments", tags=["attachments"],
)
api_router.include_router(chat_router, prefix="/chat", tags=["chat"])
api_router.include_router(chat_download_router, prefix="/chat", tags=["chat"])
api_router.include_router(chat_messages_data_router, prefix="/chat", tags=["chat"])
api_router.include_router(mcp_router, prefix="/mcp", tags=["mcp"])
api_router.include_router(agents_router, prefix="/agents", tags=["agents"])
# User-facing agent routes - Get Available Agents
api_router.include_router(
    user_agents_router, prefix="/agent", tags=["agents"],
)
# Admin agent management routes
api_router.include_router(
    admin_agents_router, prefix="/admin/agent", tags=["agents"],
)
api_router.include_router(demo_router, tags=["monitoring-demo"])
api_router.include_router(
    feedback_router, prefix="/feedback", tags=["feedback"],
)
api_router.include_router(
    routing_router, prefix="/routing", tags=["routing"],
)
api_router.include_router(
    workflows_router, prefix="/workflows", tags=["workflows"],
)
api_router.include_router(quotas_router, prefix="/quotas", tags=["quotas"])
api_router.include_router(
    observability_router, tags=["observability"],
)
api_router.include_router(
    remediation_router, prefix="/remediation", tags=["remediation"],
)
api_router.include_router(menu_router, tags=["menu"])
api_router.include_router(
    user_logs_router, prefix="/users", tags=["users"],
)
api_router.include_router(
    question_tracker_router, prefix="/users", tags=["users"],
)
api_router.include_router(
    context_memory_router, prefix="/users", tags=["users"],
)
api_router.include_router(
    user_memory_router, prefix="/users", tags=["users"],
)
api_router.include_router(
    user_settings_router, prefix="/users", tags=["users"],
)
api_router.include_router(starter_prompts_router)
api_router.include_router(
    web_pubsub_router, prefix="/webpubsub", tags=["webpubsub"],
)
api_router.include_router(orchestration_router)
api_router.include_router(query_agent_router)
api_router.include_router(
    deployment_versions_router, tags=["deployment"],
)

