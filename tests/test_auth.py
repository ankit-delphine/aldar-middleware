"""Test authentication endpoints."""

import pytest
from fastapi.testclient import TestClient


def test_health_check(client: TestClient):
    """Test health check endpoint."""
    response = client.get(f"{API_PREFIX}/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_login_endpoint(client: TestClient):
    """Test login endpoint."""
    response = client.post("/api/auth/login")
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data


def test_me_endpoint_unauthorized(client: TestClient):
    """Test /me endpoint without authentication."""
    response = client.get("/api/auth/me")
    assert response.status_code == 401


def test_logout_endpoint_unauthorized(client: TestClient):
    """Test logout endpoint without authentication."""
    response = client.post("/api/auth/logout")
    assert response.status_code == 401
