"""Integration tests for AGNO orchestration services."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from httpx import HTTPStatusError

from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.middleware.external_api_cache import AGNOAPICacheMiddleware
from aldar_middleware.orchestration.agno import (
    AGNOAPIService,
    AGNOService,
    _cache,
    close_http_client,
    get_http_client,
)
from aldar_middleware.settings import settings

API_BASE = settings.api_prefix
ORCHESTRATION_PREFIX = f"{API_BASE}/orchestrat"


@pytest.fixture
def integration_client(client: TestClient) -> TestClient:
    """Expose the FastAPI test client from the shared fixture with auth override."""
    client.app.dependency_overrides[get_current_user] = lambda: {"id": "integration-user"}
    try:
        yield client
    finally:
        client.app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def mock_external_api():
    """Mock payloads returned by the AGNO API."""
    return {
        "/models": {"models": ["gpt-4", "gpt-3.5-turbo"]},
        "/agents": {"agents": [{"id": "agent1", "name": "Test Agent"}]},
        "/teams": {"teams": [{"id": "team1", "name": "Test Team"}]},
        "/workflows": {"workflows": [{"id": "workflow1", "name": "Test Workflow"}]},
        "/health": {"status": "healthy", "version": "1.0.0"},
    }


@pytest.fixture
def mock_http_responses(mock_external_api):
    """Create mock HTTP responses keyed by endpoint."""
    responses = {}
    for endpoint, data in mock_external_api.items():
        response = MagicMock()
        response.json.return_value = data
        response.raise_for_status.return_value = None
        response.status_code = 200
        responses[endpoint] = response
    return responses


class TestAGNOAPIServiceIntegration:
    """Integration tests for the low-level AGNO API service."""

    @pytest.mark.asyncio
    async def test_full_request_flow_with_caching(self, mock_http_responses):
        _cache.clear()
        service = AGNOAPIService()

        with patch("aldar_middleware.orchestration.agno.get_http_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_http_responses["/models"]
            mock_get_client.return_value = mock_client

            result1 = await service.make_request(
                endpoint="/models",
                method="GET",
            )

            result2 = await service.make_request(
                endpoint="/models",
                method="GET",
            )

            assert result1 == result2 == {"models": ["gpt-4", "gpt-3.5-turbo"]}
            mock_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_expiration_flow(self, mock_http_responses):
        _cache.clear()
        service = AGNOAPIService()

        with patch("aldar_middleware.orchestration.agno.get_http_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_http_responses["/models"]
            mock_get_client.return_value = mock_client

            await service.make_request(
                endpoint="/models",
                method="GET",
                cache_ttl=1,
            )

            await service.make_request(
                endpoint="/models",
                method="GET",
            )

            time.sleep(1.1)

            await service.make_request(
                endpoint="/models",
                method="GET",
            )

            assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_error_handling_flow(self):
        service = AGNOAPIService()

        with patch("aldar_middleware.orchestration.agno.get_http_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get.side_effect = HTTPStatusError("404 Not Found", request=None, response=None)
            mock_get_client.return_value = mock_client

            with pytest.raises(HTTPStatusError):
                await service.make_request(
                    endpoint="/nonexistent",
                    method="GET",
                )

    @pytest.mark.asyncio
    async def test_cache_management_integration(self):
        service = AGNOAPIService()

        await service.save_to_cache(
            cache_key="key1",
            response_data={"data": "1"},
            endpoint="/test1",
            ttl=3600,
            user_id="user1",
            correlation_id="corr1",
        )

        await service.save_to_cache(
            cache_key="key2",
            response_data={"data": "2"},
            endpoint="/test2",
            ttl=3600,
            user_id="user2",
            correlation_id="corr2",
        )

        stats = await service.get_cache_stats()
        assert stats["valid_entries"] == 2

        cleared = await service.clear_cache(endpoint="/test2")
        assert cleared == 1

        cleared = await service.clear_cache()
        assert cleared == 1

    @pytest.mark.asyncio
    async def test_connection_pooling_integration(self):
        client1 = await get_http_client()
        client2 = await get_http_client()
        assert client1 is client2

        await close_http_client()

        client3 = await get_http_client()
        assert client3 is not client1

    @pytest.mark.asyncio
    async def test_concurrent_requests(self, mock_http_responses):
        service = AGNOAPIService()

        with patch("aldar_middleware.orchestration.agno.get_http_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_http_responses["/models"]
            mock_get_client.return_value = mock_client

            tasks = [
                service.make_request(
                    endpoint="/models",
                    method="GET",
                )
                for _ in range(5)
            ]

            results = await asyncio.gather(*tasks)
            for result in results:
                assert result == {"models": ["gpt-4", "gpt-3.5-turbo"]}
            assert mock_client.get.call_count == 1


class TestAGNOServiceIntegration:
    """Integration tests for the higher-level AGNO service wrapper."""

    @pytest.mark.asyncio
    async def test_agnoservice_methods(self, mock_http_responses):
        service = AGNOService()

        with patch("aldar_middleware.orchestration.agno.get_http_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_http_responses["/models"]
            mock_get_client.return_value = mock_client

            models = await service.get_models(user_id="tester")
            assert models == {"models": ["gpt-4", "gpt-3.5-turbo"]}

            mock_client.get.return_value = mock_http_responses["/agents"]
            agents = await service.get_agents(user_id="tester")
            assert agents == {"agents": [{"id": "agent1", "name": "Test Agent"}]}

            mock_client.get.return_value = mock_http_responses["/teams"]
            teams = await service.get_teams(user_id="tester")
            assert teams == {"teams": [{"id": "team1", "name": "Test Team"}]}

            mock_client.get.return_value = mock_http_responses["/workflows"]
            workflows = await service.get_workflows(user_id="tester")
            assert workflows == {"workflows": [{"id": "workflow1", "name": "Test Workflow"}]}


class TestEndpointIntegration:
    """Integration tests hitting FastAPI endpoints with mocked services."""

    def test_endpoints_with_mocked_services(self, integration_client):
        with patch("aldar_middleware.routes.orchestration.agno_service") as mock_agno, \
             patch("aldar_middleware.routes.orchestration.get_current_user", AsyncMock(return_value={"id": "tester"})):
            mock_agno.get_models = AsyncMock(return_value={"models": ["gpt-4"]})
            mock_agno.get_config = AsyncMock(return_value={"status": "ok"})

            response = integration_client.get(f"{ORCHESTRATION_PREFIX}/models")
            assert response.status_code == 200
            assert response.json()["data"]["models"] == ["gpt-4"]

            response = integration_client.get(f"{ORCHESTRATION_PREFIX}/config")
            assert response.status_code == 200
            assert response.json()["data"]["status"] == "ok"

    def test_error_propagation(self, integration_client):
        with patch("aldar_middleware.routes.orchestration.agno_service") as mock_agno, \
             patch("aldar_middleware.routes.orchestration.get_current_user", AsyncMock(return_value={"id": "tester"})):
            mock_agno.get_models = AsyncMock(side_effect=Exception("Service unavailable"))

            response = integration_client.get(f"{ORCHESTRATION_PREFIX}/models")
            assert response.status_code == 500
            assert "Failed to get models" in response.json()["detail"]


class TestMiddlewareIntegration:
    """Verify the middleware works with mocked AGNO service layer."""

    @pytest.mark.asyncio
    async def test_cache_middleware(self):
        app = MagicMock()
        middleware = AGNOAPICacheMiddleware(app, cache_ttl=3600)

        request = MagicMock()
        request.url.path = f"{ORCHESTRATION_PREFIX}/models"
        request.method = "GET"
        request.query_params = {}

        response = MagicMock()
        response.status_code = 200
        response.headers = {"content-type": "application/json"}
        response.body = json.dumps({"models": ["gpt-4"]}).encode()

        call_next = AsyncMock(return_value=response)

        with patch("aldar_middleware.middleware.external_api_cache.agno_service") as mock_service, \
             patch("aldar_middleware.middleware.external_api_cache.get_correlation_id", return_value="corr-id"):
            mock_service.api_service = MagicMock()
            mock_service.api_service.get_cached_response = AsyncMock(return_value=None)
            mock_service.api_service.save_to_cache = AsyncMock()

            result = await middleware.dispatch(request, call_next)
            assert result == response
            call_next.assert_called_once()

            mock_service.api_service.get_cached_response = AsyncMock(return_value={"models": ["gpt-4"]})
            result = await middleware.dispatch(request, call_next)
            assert isinstance(result, JSONResponse)
            assert json.loads(result.body.decode()) == {"models": ["gpt-4"]}


@pytest.fixture(autouse=True)
def clear_cache_between_tests():
    """Ensure the shared cache is cleared between tests."""
    _cache.clear()
    yield
    _cache.clear()
