"""Tests for external API endpoints."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.routes.orchestration import (
    router,
    AGNOAPIResponse,
    AgentRunRequest,
    TeamRunRequest,
    WorkflowRunRequest,
)
from aldar_middleware.settings import settings

API_BASE = settings.api_prefix
ORCHESTRATION_PREFIX = f"{API_BASE}/orchestrat"


@pytest.fixture
def mock_agno_service():
    """Mock AGNO service used by orchestration routes."""
    service = MagicMock()
    service.get_config = AsyncMock(return_value={"os": "linux", "version": "1.0"})
    service.get_models = AsyncMock(return_value={"models": ["model1", "model2"]})
    service.get_agents = AsyncMock(return_value={"agents": ["agent1", "agent2"]})
    service.get_agent_details = AsyncMock(return_value={"agent": {"id": "agent1", "name": "Test Agent"}})
    service.create_agent_run = AsyncMock(return_value={"run_id": "run123", "status": "started"})
    service.cancel_agent_run = AsyncMock(return_value={"status": "cancelled"})
    service.get_teams = AsyncMock(return_value={"teams": ["team1", "team2"]})
    service.get_team_details = AsyncMock(return_value={"team": {"id": "team1", "name": "Test Team"}})
    service.create_team_run = AsyncMock(return_value={"run_id": "run123", "status": "started"})
    service.cancel_team_run = AsyncMock(return_value={"status": "cancelled"})
    service.get_workflows = AsyncMock(return_value={"workflows": ["workflow1", "workflow2"]})
    service.get_workflow_details = AsyncMock(return_value={"workflow": {"id": "workflow1", "name": "Test Workflow"}})
    service.execute_workflow = AsyncMock(return_value={"run_id": "run123", "status": "started"})
    service.cancel_workflow_run = AsyncMock(return_value={"status": "cancelled"})
    service.get_health = AsyncMock(return_value={"status": "healthy", "version": "1.0.0"})

    api_service = MagicMock()
    api_service.clear_cache = AsyncMock(return_value=5)
    api_service.get_cache_stats = AsyncMock(
        return_value={
            "valid_entries": 10,
            "expired_entries": 2,
            "total_entries": 12,
            "cache_type": "in_memory",
        }
    )
    service.api_service = api_service

    return service


@pytest.fixture
def client_with_mocks(client, mock_agno_service):
    """Create test client with mocked AGNO service and auth dependency."""
    with patch("aldar_middleware.routes.orchestration.agno_service", mock_agno_service):
        client.app.dependency_overrides[get_current_user] = lambda: {"id": "test-user"}
        try:
            yield client
        finally:
            client.app.dependency_overrides.pop(get_current_user, None)


class TestExternalAPIEndpoints:
    """Test cases for external API endpoints."""

    def test_get_os_config_success(self, client_with_mocks, mock_agno_service):
        """Test successful OS config retrieval."""
        mock_agno_service.get_config.return_value = {"os": "linux", "version": "1.0"}
        
        response = client_with_mocks.get(f"{ORCHESTRATION_PREFIX}/config")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == {"os": "linux", "version": "1.0"}
        assert data["cached"] is True
        assert "correlation_id" in data

    def test_get_os_config_with_force_refresh(self, client_with_mocks, mock_agno_service):
        """Test OS config retrieval with force refresh."""
        mock_agno_service.get_config.return_value = {"os": "linux", "version": "1.0"}
        
        response = client_with_mocks.get(f"{ORCHESTRATION_PREFIX}/config?force_refresh=true")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["cached"] is False  # Force refresh should set cached to False

    def test_get_os_config_error(self, client_with_mocks, mock_agno_service):
        """Test OS config retrieval with error."""
        mock_agno_service.get_config.side_effect = Exception("API Error")
        
        response = client_with_mocks.get(f"{ORCHESTRATION_PREFIX}/config")
        
        assert response.status_code == 500
        data = response.json()
        assert "Failed to get OS config" in data["detail"]

    def test_get_available_models_success(self, client_with_mocks, mock_agno_service):
        """Test successful models retrieval."""
        response = client_with_mocks.get(f"{ORCHESTRATION_PREFIX}/models")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == {"models": ["model1", "model2"]}
        assert data["cached"] is True

    def test_get_available_models_error(self, client_with_mocks, mock_agno_service):
        """Test models retrieval with error."""
        mock_agno_service.get_models.side_effect = Exception("Service Error")
        
        response = client_with_mocks.get(f"{ORCHESTRATION_PREFIX}/models")
        
        assert response.status_code == 500
        data = response.json()
        assert "Failed to get models" in data["detail"]

    def test_get_all_agents_success(self, client_with_mocks, mock_agno_service):
        """Test successful agents retrieval."""
        response = client_with_mocks.get(f"{ORCHESTRATION_PREFIX}/agents")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == {"agents": ["agent1", "agent2"]}

    def test_get_agent_details_success(self, client_with_mocks, mock_agno_service):
        """Test successful agent details retrieval."""
        response = client_with_mocks.get(f"{ORCHESTRATION_PREFIX}/agents/agent1")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == {"agent": {"id": "agent1", "name": "Test Agent"}}

    def test_create_agent_run_success(self, client_with_mocks, mock_agno_service):
        """Test successful agent run creation."""
        request_data = {
            "input_data": {"prompt": "test prompt"},
            "parameters": {"temperature": 0.7}
        }
        
        response = client_with_mocks.post(
            f"{ORCHESTRATION_PREFIX}/agents/agent1/runs",
            data={"message": "test prompt"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == {"run_id": "run123", "status": "started"}
        assert data["cached"] is False  # POST requests are never cached

    def test_create_agent_run_invalid_data(self, client_with_mocks):
        """Test agent run creation with invalid data."""
        request_data = {
            "invalid_field": "test"
        }
        
        response = client_with_mocks.post(
            f"{ORCHESTRATION_PREFIX}/agents/agent1/runs",
            data={}
        )
        
        assert response.status_code == 422  # Validation error

    def test_cancel_agent_run_success(self, client_with_mocks, mock_agno_service):
        """Test successful agent run cancellation."""
        mock_agno_service.cancel_agent_run.return_value = {"status": "cancelled"}
        
        response = client_with_mocks.post(f"{ORCHESTRATION_PREFIX}/agents/agent1/runs/run123/cancel")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == {"status": "cancelled"}

    def test_get_all_teams_success(self, client_with_mocks, mock_agno_service):
        """Test successful teams retrieval."""
        response = client_with_mocks.get(f"{ORCHESTRATION_PREFIX}/teams")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == {"teams": ["team1", "team2"]}

    def test_get_team_details_success(self, client_with_mocks, mock_agno_service):
        """Test successful team details retrieval."""
        mock_agno_service.get_team_details.return_value = {"team": {"id": "team1", "name": "Test Team"}}
        
        response = client_with_mocks.get(f"{ORCHESTRATION_PREFIX}/teams/team1")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == {"team": {"id": "team1", "name": "Test Team"}}

    def test_create_team_run_success(self, client_with_mocks, mock_agno_service):
        """Test successful team run creation."""
        request_data = {
            "input_data": {"task": "test task"},
            "parameters": {"priority": "high"}
        }
        
        mock_agno_service.create_team_run.return_value = {"run_id": "run123", "status": "started"}
        
        response = client_with_mocks.post(
            f"{ORCHESTRATION_PREFIX}/teams/team1/runs",
            json=request_data
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == {"run_id": "run123", "status": "started"}

    def test_get_all_workflows_success(self, client_with_mocks, mock_agno_service):
        """Test successful workflows retrieval."""
        response = client_with_mocks.get(f"{ORCHESTRATION_PREFIX}/workflows")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == {"workflows": ["workflow1", "workflow2"]}

    def test_get_workflow_details_success(self, client_with_mocks, mock_agno_service):
        """Test successful workflow details retrieval."""
        mock_agno_service.get_workflow_details.return_value = {"workflow": {"id": "workflow1", "name": "Test Workflow"}}
        
        response = client_with_mocks.get(f"{ORCHESTRATION_PREFIX}/workflows/workflow1")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == {"workflow": {"id": "workflow1", "name": "Test Workflow"}}

    def test_execute_workflow_success(self, client_with_mocks, mock_agno_service):
        """Test successful workflow execution."""
        request_data = {
            "input_data": {"workflow_input": "test"},
            "parameters": {"timeout": 300}
        }
        
        response = client_with_mocks.post(
            f"{ORCHESTRATION_PREFIX}/workflows/workflow1/runs",
            json=request_data
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == {"run_id": "run123", "status": "started"}

    def test_get_health_status_success(self, client_with_mocks, mock_agno_service):
        """Test successful health status retrieval."""
        response = client_with_mocks.get(f"{ORCHESTRATION_PREFIX}/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == {"status": "healthy", "version": "1.0.0"}

    def test_clear_cache_success(self, client_with_mocks, mock_agno_service):
        """Test successful cache clearing."""
        response = client_with_mocks.post(f"{ORCHESTRATION_PREFIX}/cache/clear")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == {"cleared_entries": 5}

    def test_clear_cache_with_filters(self, client_with_mocks, mock_agno_service):
        """Test cache clearing with filters."""
        response = client_with_mocks.post(
            f"{ORCHESTRATION_PREFIX}/cache/clear?endpoint=/models"
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_clear_cache_invalid_api_type(self, client_with_mocks, mock_agno_service):
        """Test cache clearing with invalid API type."""
        mock_agno_service.api_service.clear_cache.side_effect = ValueError("Invalid endpoint")
        
        response = client_with_mocks.post(f"{ORCHESTRATION_PREFIX}/cache/clear?endpoint=invalid")
        
        assert response.status_code == 500
        data = response.json()
        assert "Failed to clear cache" in data["detail"]

    def test_get_cache_stats_success(self, client_with_mocks, mock_agno_service):
        """Test successful cache stats retrieval."""
        response = client_with_mocks.get(f"{ORCHESTRATION_PREFIX}/cache/stats")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["valid_entries"] == 10
        assert data["data"]["total_entries"] == 12
        assert data["data"]["cache_type"] == "in_memory"

    def test_get_cache_stats_error(self, client_with_mocks, mock_agno_service):
        """Test cache stats retrieval with error."""
        mock_agno_service.api_service.get_cache_stats.side_effect = Exception("Stats Error")
        
        response = client_with_mocks.get(f"{ORCHESTRATION_PREFIX}/cache/stats")
        
        assert response.status_code == 500
        data = response.json()
        assert "Failed to get cache stats" in data["detail"]


class TestExternalAPIResponseModel:
    """Test cases for ExternalAPIResponse model."""

    def test_external_api_response_creation(self):
        """Test ExternalAPIResponse model creation."""
        response = AGNOAPIResponse(
            success=True,
            data={"test": "data"},
            cached=False,
            correlation_id="test-correlation"
        )
        
        assert response.success is True
        assert response.data == {"test": "data"}
        assert response.cached is False
        assert response.correlation_id == "test-correlation"
        assert response.error is None

    def test_external_api_response_with_error(self):
        """Test ExternalAPIResponse model with error."""
        response = AGNOAPIResponse(
            success=False,
            error="Test error",
            cached=False,
            correlation_id="test-correlation"
        )
        
        assert response.success is False
        assert response.error == "Test error"
        assert response.data is None


class TestRequestModels:
    """Test cases for request models."""

    def test_agent_run_request(self):
        """Test AgentRunRequest model."""
        request = AgentRunRequest(
            input_data={"prompt": "test"},
            parameters={"temperature": 0.7},
            timeout=30
        )
        
        assert request.input_data == {"prompt": "test"}
        assert request.parameters == {"temperature": 0.7}
        assert request.timeout == 30

    def test_team_run_request(self):
        """Test TeamRunRequest model."""
        request = TeamRunRequest(
            input_data={"task": "test task"},
            parameters={"priority": "high"},
            timeout=60
        )
        
        assert request.input_data == {"task": "test task"}
        assert request.parameters == {"priority": "high"}
        assert request.timeout == 60

    def test_workflow_run_request(self):
        """Test WorkflowRunRequest model."""
        request = WorkflowRunRequest(
            input_data={"workflow_input": "test"},
            parameters={"timeout": 300},
            timeout=120
        )
        
        assert request.input_data == {"workflow_input": "test"}
        assert request.parameters == {"timeout": 300}
        assert request.timeout == 120
