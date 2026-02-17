"""Tests for AGNO orchestration services and caching layer."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import HTTPStatusError

from aldar_middleware.orchestration.agno import (
    AGNOAPIService,
    AGNOService,
    _cache,
    close_http_client,
    get_http_client,
)


@pytest.fixture
def external_api_service():
    """Create AGNOAPIService instance for testing."""
    return AGNOAPIService()


@pytest.fixture
def agno_service():
    """Create AGNOService instance for testing."""
    return AGNOService()


@pytest.fixture
def mock_response_data():
    """Sample response data for testing."""
    return {
        "success": True,
        "data": {"test": "value"},
        "message": "Test response",
    }


@pytest.fixture
def mock_http_response(mock_response_data):
    """Mock HTTP response."""
    response = MagicMock()
    response.json.return_value = mock_response_data
    response.raise_for_status.return_value = None
    response.status_code = 200
    response.headers = {"content-type": "application/json"}
    return response


class TestAGNOAPIService:
    """Unit tests for AGNOAPIService."""

    @pytest.mark.asyncio
    async def test_make_request_success(self, external_api_service, mock_http_response, mock_response_data):
        """Successful GET request caches the response."""
        with patch("aldar_middleware.orchestration.agno.get_http_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_http_response
            mock_get_client.return_value = mock_client

            result = await external_api_service.make_request(endpoint="/test", method="GET")

            assert result == mock_response_data
            mock_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_make_request_with_caching(self, external_api_service, mock_http_response):
        """Repeated GET requests should hit the cache after the first call."""
        with patch("aldar_middleware.orchestration.agno.get_http_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_http_response
            mock_get_client.return_value = mock_client

            result1 = await external_api_service.make_request(endpoint="/test", method="GET")
            result2 = await external_api_service.make_request(endpoint="/test", method="GET")

            assert result1 == result2
            mock_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_make_request_force_refresh(self, external_api_service, mock_http_response):
        """Force refresh bypasses the cache."""
        with patch("aldar_middleware.orchestration.agno.get_http_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_http_response
            mock_get_client.return_value = mock_client

            await external_api_service.make_request(endpoint="/test", method="GET")
            await external_api_service.make_request(endpoint="/test", method="GET", force_refresh=True)

            assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_make_request_http_error(self, external_api_service):
        """HTTP errors from the client propagate."""
        with patch("aldar_middleware.orchestration.agno.get_http_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get.side_effect = HTTPStatusError("404 Not Found", request=None, response=None)
            mock_get_client.return_value = mock_client

            with pytest.raises(HTTPStatusError):
                await external_api_service.make_request(endpoint="/test", method="GET")

    @pytest.mark.asyncio
    async def test_make_request_unsupported_method(self, external_api_service):
        """Unsupported HTTP verbs raise a ValueError."""
        with pytest.raises(ValueError, match="Unsupported HTTP method"):
            await external_api_service.make_request(endpoint="/test", method="TRACE")

    @pytest.mark.asyncio
    async def test_cache_round_trip(self, external_api_service):
        """Cache save and retrieval works as expected."""
        cache_key = "test_key"
        payload = {"test": "data"}

        cached_before = await external_api_service.get_cached_response(cache_key, "user", "corr")
        assert cached_before is None

        await external_api_service.save_to_cache(
            cache_key=cache_key,
            response_data=payload,
            endpoint="/test",
            ttl=3600,
            user_id="user",
            correlation_id="corr",
        )

        cached_after = await external_api_service.get_cached_response(cache_key, "user", "corr")
        assert cached_after == payload

    @pytest.mark.asyncio
    async def test_cache_expiration(self, external_api_service):
        """Expired cache entries are evicted."""
        cache_key = "expiring"
        payload = {"test": "data"}

        await external_api_service.save_to_cache(
            cache_key=cache_key,
            response_data=payload,
            endpoint="/test",
            ttl=1,
            user_id="user",
            correlation_id="corr",
        )

        assert await external_api_service.get_cached_response(cache_key, "user", "corr") == payload
        time.sleep(1.1)
        assert await external_api_service.get_cached_response(cache_key, "user", "corr") is None

    @pytest.mark.asyncio
    async def test_clear_cache(self, external_api_service):
        """Cache entries can be cleared selectively or entirely."""
        await external_api_service.save_to_cache(
            cache_key="key1",
            response_data={"data": "1"},
            endpoint="/test1",
            ttl=3600,
            user_id="user1",
            correlation_id="corr1",
        )
        await external_api_service.save_to_cache(
            cache_key="key2",
            response_data={"data": "2"},
            endpoint="/test2",
            ttl=3600,
            user_id="user2",
            correlation_id="corr2",
        )

        cleared = await external_api_service.clear_cache(endpoint="/test1")
        assert cleared == 1

        cleared = await external_api_service.clear_cache()
        assert cleared == 1

    @pytest.mark.asyncio
    async def test_get_cache_stats(self, external_api_service):
        """Cache statistics include valid entry counts."""
        await external_api_service.save_to_cache(
            cache_key="stats-key",
            response_data={"data": "value"},
            endpoint="/stats",
            ttl=3600,
            user_id="user",
            correlation_id="corr",
        )

        stats = await external_api_service.get_cache_stats()
        assert stats["valid_entries"] == 1
        assert stats["total_entries"] == 1
        assert stats["cache_type"] == "in_memory"

    def test_generate_cache_key(self, external_api_service):
        """Cache key generation is deterministic."""
        key_without_data = external_api_service._generate_cache_key("/test", "GET", None)
        assert key_without_data == "agno_api:agno_multiagent:/test:GET"

        payload = {"param": "value", "other": "data"}
        key_with_data = external_api_service._generate_cache_key("/test", "POST", payload)
        assert key_with_data.startswith("agno_api:agno_multiagent:/test:POST")
        assert "param" in key_with_data


class TestAGNOService:
    """Unit tests for the higher-level AGNOService."""

    @pytest.mark.asyncio
    async def test_get_models(self, agno_service):
        with patch.object(agno_service.api_service, "make_request") as mock_make_request:
            mock_make_request.return_value = {"models": ["model1", "model2"]}

            result = await agno_service.get_models(user_id="user")

            assert result == {"models": ["model1", "model2"]}
            mock_make_request.assert_called_once_with(endpoint="/models", method="GET", user_id="user")

    @pytest.mark.asyncio
    async def test_get_agents(self, agno_service):
        with patch.object(agno_service.api_service, "make_request") as mock_make_request:
            mock_make_request.return_value = {"agents": ["agent1", "agent2"]}

            result = await agno_service.get_agents(user_id="user")

            assert result == {"agents": ["agent1", "agent2"]}
            mock_make_request.assert_called_once_with(endpoint="/agents", method="GET", user_id="user")

    @pytest.mark.asyncio
    async def test_get_agent_details(self, agno_service):
        with patch.object(agno_service.api_service, "make_request") as mock_make_request:
            mock_make_request.return_value = {"agent": {"id": "agent1", "name": "Test"}}

            result = await agno_service.get_agent_details("agent1", user_id="user")

            assert result == {"agent": {"id": "agent1", "name": "Test"}}
            mock_make_request.assert_called_once_with(endpoint="/agents/agent1", method="GET", user_id="user")

    @pytest.mark.asyncio
    async def test_create_agent_run(self, agno_service):
        payload = {"input": "value"}
        with patch.object(agno_service.api_service, "make_request") as mock_make_request:
            mock_make_request.return_value = {"run_id": "run123"}

            result = await agno_service.create_agent_run("agent1", data=payload, user_id="user")

            assert result == {"run_id": "run123"}
            mock_make_request.assert_called_once_with(
                endpoint="/agents/agent1/runs",
                method="POST",
                data=payload,
                user_id="user",
                content_type="application/json",
                files=None,
            )

    @pytest.mark.asyncio
    async def test_get_teams(self, agno_service):
        with patch.object(agno_service.api_service, "make_request") as mock_make_request:
            mock_make_request.return_value = {"teams": ["team1"]}

            result = await agno_service.get_teams(user_id="user")

            assert result == {"teams": ["team1"]}
            mock_make_request.assert_called_once_with(endpoint="/teams", method="GET", user_id="user")

    @pytest.mark.asyncio
    async def test_get_workflows(self, agno_service):
        with patch.object(agno_service.api_service, "make_request") as mock_make_request:
            mock_make_request.return_value = {"workflows": ["wf"]}

            result = await agno_service.get_workflows(user_id="user")

            assert result == {"workflows": ["wf"]}
            mock_make_request.assert_called_once_with(endpoint="/workflows", method="GET", user_id="user")

    @pytest.mark.asyncio
    async def test_execute_workflow(self, agno_service):
        payload = {"input": "value"}
        with patch.object(agno_service.api_service, "make_request") as mock_make_request:
            mock_make_request.return_value = {"run_id": "run123"}

            result = await agno_service.execute_workflow("workflow1", data=payload, user_id="user")

            assert result == {"run_id": "run123"}
            mock_make_request.assert_called_once_with(
                endpoint="/workflows/workflow1/runs",
                method="POST",
                data=payload,
                user_id="user",
            )

    @pytest.mark.asyncio
    async def test_get_health(self, agno_service):
        with patch.object(agno_service.api_service, "make_request") as mock_make_request:
            mock_make_request.return_value = {"status": "healthy"}

            result = await agno_service.get_health(user_id="user")

            assert result == {"status": "healthy"}
            mock_make_request.assert_called_once_with(endpoint="/health", method="GET", user_id="user")


class TestHTTPClient:
    """Tests for the shared HTTP client helpers."""

    @pytest.mark.asyncio
    async def test_get_http_client(self):
        client = await get_http_client()
        assert isinstance(client, httpx.AsyncClient)

    @pytest.mark.asyncio
    async def test_close_http_client(self):
        client = await get_http_client()
        assert client is not None

        await close_http_client()

        new_client = await get_http_client()
        assert new_client is not None
        assert new_client is not client


@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure cache isolation across tests."""
    _cache.clear()
    yield
    _cache.clear()
