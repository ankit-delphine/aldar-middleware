"""Tests for external API middleware functionality."""

import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.responses import Response as StarletteResponse

from aldar_middleware.middleware.external_api_cache import (
    AGNOAPICacheMiddleware,
    AGNOAPIOptimizationMiddleware,
    AGNOAPIMonitoringMiddleware,
)
from aldar_middleware.settings import settings

API_BASE = settings.api_prefix
ORCHESTRATION_PREFIX = f"{API_BASE}/orchestrat"


@pytest.fixture
def mock_request():
    """Create mock request for testing."""
    request = MagicMock(spec=Request)
    request.url.path = f"{ORCHESTRATION_PREFIX}/models"
    request.method = "GET"
    request.query_params = {}
    request.headers = {}
    return request


@pytest.fixture
def mock_response():
    """Create mock response for testing."""
    response = MagicMock(spec=Response)
    response.status_code = 200
    response.headers = {"content-type": "application/json"}
    response.body = json.dumps({"test": "data"}).encode()
    return response


@pytest.fixture
def cache_middleware():
    """Create AGNOAPICacheMiddleware instance for testing."""
    app = MagicMock()
    return AGNOAPICacheMiddleware(app, cache_ttl=3600)


@pytest.fixture
def optimization_middleware():
    """Create AGNOAPIOptimizationMiddleware instance for testing."""
    app = MagicMock()
    return AGNOAPIOptimizationMiddleware(app)


@pytest.fixture
def monitoring_middleware():
    """Create AGNOAPIMonitoringMiddleware instance for testing."""
    app = MagicMock()
    return AGNOAPIMonitoringMiddleware(app)


class TestAGNOAPICacheMiddleware:
    """Test cases for AGNOAPICacheMiddleware."""

    def test_is_external_api_request(self, cache_middleware):
        """Test external API request detection."""
        # External API request
        request = MagicMock()
        request.url.path = f"{ORCHESTRATION_PREFIX}/models"
        assert cache_middleware._is_external_api_request(request) is True
        
        # Non-external API request
        request.url.path = "/api/internal/models"
        assert cache_middleware._is_external_api_request(request) is False

    def test_should_bypass_cache(self, cache_middleware):
        """Test cache bypass logic."""
        request = MagicMock()
        
        # GET request should not bypass
        request.method = "GET"
        request.url.path = f"{ORCHESTRATION_PREFIX}/models"
        request.query_params = {}
        assert cache_middleware._should_bypass_cache(request) is False
        
        # POST request should bypass
        request.method = "POST"
        assert cache_middleware._should_bypass_cache(request) is True
        
        # PUT request should bypass
        request.method = "PUT"
        assert cache_middleware._should_bypass_cache(request) is True
        
        # DELETE request should bypass
        request.method = "DELETE"
        assert cache_middleware._should_bypass_cache(request) is True
        
        # Force refresh should bypass
        request.method = "GET"
        request.query_params = {"force_refresh": "true"}
        assert cache_middleware._should_bypass_cache(request) is True

    def test_path_matches_pattern(self, cache_middleware):
        """Test path pattern matching."""
        # Exact match
        assert cache_middleware._path_matches_pattern(f"{ORCHESTRATION_PREFIX}/models", f"{ORCHESTRATION_PREFIX}/models") is True
        
        # Wildcard match
        assert cache_middleware._path_matches_pattern(f"{ORCHESTRATION_PREFIX}/agents/123/runs", f"{ORCHESTRATION_PREFIX}/agents/*/runs") is True
        
        # No match
        assert cache_middleware._path_matches_pattern(f"{ORCHESTRATION_PREFIX}/models", f"{ORCHESTRATION_PREFIX}/agents") is False

    @pytest.mark.asyncio
    async def test_get_cached_response_cache_hit(self, cache_middleware, mock_request):
        """Test getting cached response when cache hit."""
        with patch('aldar_middleware.middleware.external_api_cache.agno_service') as mock_service:
            mock_service.api_service = MagicMock()
            mock_service.api_service.get_cached_response = AsyncMock(return_value={"cached": "data"})
            
            result = await cache_middleware._get_cached_response(
                mock_request, "test_user", "test_correlation"
            )
            
            assert result == {"cached": "data"}
            mock_service.api_service.get_cached_response.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_cached_response_cache_miss(self, cache_middleware, mock_request):
        """Test getting cached response when cache miss."""
        with patch('aldar_middleware.middleware.external_api_cache.agno_service') as mock_service:
            mock_service.api_service = MagicMock()
            mock_service.api_service.get_cached_response = AsyncMock(return_value=None)
            
            result = await cache_middleware._get_cached_response(
                mock_request, "test_user", "test_correlation"
            )
            
            assert result is None

    @pytest.mark.asyncio
    async def test_cache_response(self, cache_middleware, mock_request, mock_response):
        """Test caching response."""
        with patch('aldar_middleware.middleware.external_api_cache.agno_service') as mock_service:
            mock_service.api_service = MagicMock()
            mock_service.api_service.save_to_cache = AsyncMock()
            
            await cache_middleware._cache_response(
                mock_request, mock_response, "test_user", "test_correlation"
            )
            
            mock_service.api_service.save_to_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_response_non_json(self, cache_middleware, mock_request):
        """Test caching non-JSON response."""
        response = MagicMock()
        response.headers = {"content-type": "text/plain"}
        
        with patch('aldar_middleware.middleware.external_api_cache.agno_service') as mock_service:
            mock_service.api_service = MagicMock()
            mock_service.api_service.save_to_cache = AsyncMock()
            
            await cache_middleware._cache_response(
                mock_request, response, "test_user", "test_correlation"
            )
            
            # Should not call save_to_cache for non-JSON
            mock_service.api_service.save_to_cache.assert_not_called()

    def test_generate_cache_key(self, cache_middleware, mock_request):
        """Test cache key generation."""
        key = cache_middleware._generate_cache_key(mock_request)
        assert "agno_middleware_cache" in key
        assert f"{ORCHESTRATION_PREFIX}/models" in key
        assert "GET" in key

    def test_generate_cache_key_with_params(self, cache_middleware):
        """Test cache key generation with query parameters."""
        request = MagicMock()
        request.url.path = f"{ORCHESTRATION_PREFIX}/models"
        request.method = "GET"
        request.query_params = {"param1": "value1", "param2": "value2"}
        
        key = cache_middleware._generate_cache_key(request)
        assert "agno_middleware_cache" in key
        assert f"{ORCHESTRATION_PREFIX}/models" in key
        assert "GET" in key
        assert "param1" in key

    @pytest.mark.asyncio
    async def test_dispatch_cache_hit(self, cache_middleware, mock_request):
        """Test middleware dispatch with cache hit."""
        mock_request.url.path = f"{ORCHESTRATION_PREFIX}/models"
        mock_request.method = "GET"
        mock_request.query_params = {}
        
        call_next = AsyncMock()
        
        with patch.object(cache_middleware, "_get_cached_response") as mock_get_cache, \
             patch("aldar_middleware.middleware.external_api_cache.get_correlation_id", return_value="corr-id"):
            mock_get_cache.return_value = {"cached": "data"}
            
            result = await cache_middleware.dispatch(mock_request, call_next)
            
            assert isinstance(result, JSONResponse)
            assert json.loads(result.body.decode()) == {"cached": "data"}
            call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_cache_miss(self, cache_middleware, mock_request, mock_response):
        """Test middleware dispatch with cache miss."""
        mock_request.url.path = f"{ORCHESTRATION_PREFIX}/models"
        mock_request.method = "GET"
        mock_request.query_params = {}
        
        call_next = AsyncMock(return_value=mock_response)
        
        with patch.object(cache_middleware, "_get_cached_response") as mock_get_cache, \
             patch.object(cache_middleware, "_cache_response") as mock_cache, \
             patch("aldar_middleware.middleware.external_api_cache.get_correlation_id", return_value="corr-id"):
            mock_get_cache.return_value = None
            
            result = await cache_middleware.dispatch(mock_request, call_next)
            
            assert result == mock_response
            call_next.assert_called_once_with(mock_request)
            mock_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_non_external_api(self, cache_middleware):
        """Test middleware dispatch for non-external API requests."""
        request = MagicMock()
        request.url.path = "/api/internal/models"
        
        call_next = AsyncMock(return_value=MagicMock())
        
        result = await cache_middleware.dispatch(request, call_next)
        
        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_dispatch_bypass_cache(self, cache_middleware, mock_request):
        """Test middleware dispatch with cache bypass."""
        mock_request.url.path = f"{ORCHESTRATION_PREFIX}/models"
        mock_request.method = "POST"  # POST requests bypass cache
        mock_request.query_params = {}
        
        call_next = AsyncMock(return_value=MagicMock())
        
        with patch.object(cache_middleware, '_should_bypass_cache') as mock_bypass:
            mock_bypass.return_value = True
            
            result = await cache_middleware.dispatch(mock_request, call_next)
            
            call_next.assert_called_once_with(mock_request)


class TestAGNOAPIOptimizationMiddleware:
    """Test cases for AGNOAPIOptimizationMiddleware."""

    def test_generate_request_key(self, optimization_middleware):
        """Test request key generation."""
        request = MagicMock()
        request.url.path = f"{ORCHESTRATION_PREFIX}/models"
        request.method = "GET"
        request.query_params = {"user_id": "test_user", "force_refresh": "false"}
        
        key = optimization_middleware._generate_request_key(request)
        assert "dedup" in key
        assert f"{ORCHESTRATION_PREFIX}/models" in key
        assert "GET" in key
        assert "test_user" in key

    @pytest.mark.asyncio
    async def test_is_request_in_progress(self, optimization_middleware):
        """Test request in progress check."""
        result = await optimization_middleware._is_request_in_progress("test_key")
        assert result is False  # Currently returns False (no deduplication)

    @pytest.mark.asyncio
    async def test_wait_for_request(self, optimization_middleware):
        """Test waiting for request."""
        result = await optimization_middleware._wait_for_request("test_key", "test_correlation")
        assert isinstance(result, JSONResponse)
        assert result.status_code == 408

    @pytest.mark.asyncio
    async def test_mark_request_in_progress(self, optimization_middleware):
        """Test marking request as in progress."""
        # Should not raise any exceptions
        await optimization_middleware._mark_request_in_progress("test_key", "test_correlation")

    @pytest.mark.asyncio
    async def test_cache_response_for_dedup(self, optimization_middleware):
        """Test caching response for deduplication."""
        response = MagicMock()
        # Should not raise any exceptions
        await optimization_middleware._cache_response_for_dedup("test_key", response)

    @pytest.mark.asyncio
    async def test_cleanup_request(self, optimization_middleware):
        """Test cleaning up request."""
        # Should not raise any exceptions
        await optimization_middleware._cleanup_request("test_key")

    @pytest.mark.asyncio
    async def test_dispatch_non_external_api(self, optimization_middleware):
        """Test middleware dispatch for non-external API requests."""
        request = MagicMock()
        request.url.path = "/api/internal/models"
        
        call_next = AsyncMock(return_value=MagicMock())
        
        result = await optimization_middleware.dispatch(request, call_next)
        
        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_dispatch_external_api(self, optimization_middleware):
        """Test middleware dispatch for external API requests."""
        request = MagicMock()
        request.url.path = f"{ORCHESTRATION_PREFIX}/models"
        request.method = "GET"
        request.query_params = {}
        
        call_next = AsyncMock(return_value=MagicMock())
        
        with patch.object(optimization_middleware, '_is_request_in_progress') as mock_in_progress:
            mock_in_progress.return_value = False
            
            result = await optimization_middleware.dispatch(request, call_next)
            
            call_next.assert_called_once_with(request)


class TestAGNOAPIMonitoringMiddleware:
    """Test cases for AGNOAPIMonitoringMiddleware."""

    @pytest.mark.asyncio
    async def test_dispatch_non_external_api(self, monitoring_middleware):
        """Test middleware dispatch for non-external API requests."""
        request = MagicMock()
        request.url.path = "/api/internal/models"
        
        call_next = AsyncMock(return_value=MagicMock())
        
        result = await monitoring_middleware.dispatch(request, call_next)
        
        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_dispatch_external_api_success(self, monitoring_middleware):
        """Test middleware dispatch for successful external API requests."""
        request = MagicMock()
        request.url.path = f"{ORCHESTRATION_PREFIX}/models"
        request.method = "GET"
        
        response = MagicMock()
        response.status_code = 200
        
        call_next = AsyncMock(return_value=response)
        
        with patch('aldar_middleware.middleware.external_api_cache.record_external_api_request') as mock_record:
            result = await monitoring_middleware.dispatch(request, call_next)
            
            assert result == response
            call_next.assert_called_once_with(request)
            mock_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_external_api_error(self, monitoring_middleware):
        """Test middleware dispatch for external API requests with errors."""
        request = MagicMock()
        request.url.path = f"{ORCHESTRATION_PREFIX}/models"
        request.method = "GET"
        
        response = MagicMock()
        response.status_code = 500
        
        call_next = AsyncMock(return_value=response)
        
        with patch('aldar_middleware.middleware.external_api_cache.record_external_api_request') as mock_record:
            result = await monitoring_middleware.dispatch(request, call_next)
            
            assert result == response
            call_next.assert_called_once_with(request)
            mock_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_external_api_exception(self, monitoring_middleware):
        """Test middleware dispatch for external API requests with exceptions."""
        request = MagicMock()
        request.url.path = f"{ORCHESTRATION_PREFIX}/models"
        request.method = "GET"
        
        call_next = AsyncMock(side_effect=Exception("Test error"))
        
        result = await monitoring_middleware.dispatch(request, call_next)
        
        assert isinstance(result, JSONResponse)
        assert result.status_code == 500
        body = json.loads(result.body.decode())
        assert body["error"] == "AGNO API request failed"
        assert body["details"] == "Test error"
