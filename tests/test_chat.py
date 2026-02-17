"""Test chat endpoints."""

import pytest
from fastapi.testclient import TestClient


def test_create_chat_session_unauthorized(client: TestClient):
    """Test creating chat session without authentication."""
    response = client.post("/api/chat/sessions")
    assert response.status_code == 401


def test_list_chat_sessions_unauthorized(client: TestClient):
    """Test listing chat sessions without authentication."""
    response = client.get("/api/chat/sessions")
    assert response.status_code == 401


def test_get_chat_session_unauthorized(client: TestClient):
    """Test getting chat session without authentication."""
    response = client.get("/api/chat/sessions/123e4567-e89b-12d3-a456-426614174000")
    assert response.status_code == 401


def test_send_message_unauthorized(client: TestClient):
    """Test sending message without authentication."""
    response = client.post(
        "/api/chat/sessions/123e4567-e89b-12d3-a456-426614174000/messages",
        json={"content": "Hello"}
    )
    assert response.status_code == 401
