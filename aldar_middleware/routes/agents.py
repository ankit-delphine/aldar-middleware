"""Agent management API routes."""

# Note: All agent management CRUD operations have been moved to admin-only routes.
# 
# Admin-only routes (at /api/admin/agent/):
# - POST /api/admin/agent/ - Create Agent
# - GET /api/admin/agent/ - List Agents
# - GET /api/admin/agent/{agent_id} - Get Agent
# - PUT /api/admin/agent/{agent_id} - Update Agent
# - DELETE /api/admin/agent/{agent_id} - Delete Agent
# - GET /api/admin/agent/{agent_id}/health - Check Agent Health
# - GET /api/admin/agent/categories - Get Agent Categories
#
# User routes:
# - GET /api/agent/available - Get Available Agents (only enabled agents)
#
# Advanced features like method execution, health monitoring, and circuit breaker
# endpoints have been removed to simplify the API. If needed, they can be added
# back as admin-only endpoints in the future.

from fastapi import APIRouter

router = APIRouter()

# This router is kept for backward compatibility but contains no endpoints.
# All agent management is now handled through /api/admin/agent/ (admin) and 
# /api/agent/available (users).
