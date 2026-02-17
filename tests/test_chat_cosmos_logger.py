"""Tests for chat Cosmos DB logging functionality."""

import uuid
from datetime import datetime, timezone
from unittest.mock import Mock, patch, call
import pytest

from aldar_middleware.monitoring.chat_cosmos_logger import (
    log_chat_session_created,
    log_chat_message,
    log_chat_session_updated,
    log_chat_session_deleted,
    log_chat_favorite_toggled,
    log_chat_analytics_event,
)


@pytest.fixture
def mock_logger():
    """Mock logger for testing."""
    with patch('aldar_middleware.monitoring.chat_cosmos_logger.logger') as mock:
        yield mock


@pytest.fixture
def mock_correlation_id():
    """Mock correlation ID."""
    correlation_id = str(uuid.uuid4())
    with patch('aldar_middleware.monitoring.chat_cosmos_logger.get_correlation_id', return_value=correlation_id):
        yield correlation_id


@pytest.fixture
def mock_user_context():
    """Mock user context."""
    mock_context = Mock()
    mock_context.user_id = str(uuid.uuid4())
    mock_context.username = "test_user"
    mock_context.email = "test@example.com"
    mock_context.is_authenticated = True
    
    with patch('aldar_middleware.monitoring.chat_cosmos_logger.get_user_context', return_value=mock_context):
        yield mock_context


@pytest.fixture
def sample_chat_data():
    """Sample chat data for testing."""
    return {
        "chat_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "username": "test_user",
        "title": "Test Chat",
        "agent_id": "super-agent",
    }


@pytest.fixture
def sample_message_data():
    """Sample message data for testing."""
    return {
        "chat_id": str(uuid.uuid4()),
        "message_id": str(uuid.uuid4()),
        "message_type": "user",
        "role": "user",
        "content": "Hello, this is a test message",
        "user_id": str(uuid.uuid4()),
        "username": "test_user",
    }


class TestLogChatSessionCreated:
    """Tests for log_chat_session_created function."""
    
    def test_logs_basic_chat_creation(self, mock_logger, mock_correlation_id, sample_chat_data):
        """Test logging basic chat session creation."""
        log_chat_session_created(
            chat_id=sample_chat_data["chat_id"],
            session_id=sample_chat_data["session_id"],
            user_id=sample_chat_data["user_id"],
            username=sample_chat_data["username"],
            title=sample_chat_data["title"],
            agent_id=sample_chat_data["agent_id"],
        )
        
        # Verify logger.bind was called
        assert mock_logger.bind.called
        bind_call = mock_logger.bind.call_args
        bind_kwargs = bind_call[1]
        
        # Check that correlation_id is in the bind context
        assert "correlation_id" in bind_kwargs
        assert bind_kwargs["correlation_id"] == mock_correlation_id
        assert bind_kwargs["user_id"] == sample_chat_data["user_id"]
        assert bind_kwargs["username"] == sample_chat_data["username"]
        assert bind_kwargs["chat_id"] == sample_chat_data["chat_id"]
        assert bind_kwargs["session_id"] == sample_chat_data["session_id"]
        
        # Verify info was called with the right message
        mock_logger.bind.return_value.info.assert_called_once()
        info_call = mock_logger.bind.return_value.info.call_args
        assert "CHAT_SESSION_CREATED" in info_call[0][0]
        assert sample_chat_data["title"] in info_call[0][0]
        assert sample_chat_data["agent_id"] in info_call[0][0]
    
    def test_logs_chat_with_attachments(self, mock_logger, mock_correlation_id, sample_chat_data):
        """Test logging chat creation with attachments."""
        attachments = [
            {
                "filename": "image.png",
                "content_type": "image/png",
                "size": 12345,
            }
        ]
        
        log_chat_session_created(
            chat_id=sample_chat_data["chat_id"],
            session_id=sample_chat_data["session_id"],
            user_id=sample_chat_data["user_id"],
            username=sample_chat_data["username"],
            title=sample_chat_data["title"],
            agent_id=sample_chat_data["agent_id"],
            attachments=attachments,
        )
        
        # Verify extra data includes attachment info
        info_call = mock_logger.bind.return_value.info.call_args
        extra = info_call[1]["extra"]
        chat_event = extra["chat_event"]
        
        assert chat_event["has_attachments"] is True
        assert chat_event["attachment_count"] == 1
        assert chat_event["attachments"] == attachments
    
    def test_logs_chat_with_initial_message(self, mock_logger, mock_correlation_id, sample_chat_data):
        """Test logging chat creation with initial message."""
        initial_message = "This is the first message in the chat"
        
        log_chat_session_created(
            chat_id=sample_chat_data["chat_id"],
            session_id=sample_chat_data["session_id"],
            user_id=sample_chat_data["user_id"],
            username=sample_chat_data["username"],
            title=sample_chat_data["title"],
            agent_id=sample_chat_data["agent_id"],
            initial_message=initial_message,
        )
        
        # Verify extra data includes initial message info
        info_call = mock_logger.bind.return_value.info.call_args
        extra = info_call[1]["extra"]
        chat_event = extra["chat_event"]
        
        assert chat_event["has_initial_message"] is True
        assert chat_event["initial_message_length"] == len(initial_message)
        assert chat_event["initial_message_preview"] == initial_message[:100]
    
    def test_uses_custom_correlation_id(self, mock_logger, sample_chat_data):
        """Test using custom correlation ID."""
        custom_correlation_id = str(uuid.uuid4())
        
        log_chat_session_created(
            chat_id=sample_chat_data["chat_id"],
            session_id=sample_chat_data["session_id"],
            user_id=sample_chat_data["user_id"],
            username=sample_chat_data["username"],
            title=sample_chat_data["title"],
            correlation_id=custom_correlation_id,
        )
        
        bind_call = mock_logger.bind.call_args
        assert bind_call[1]["correlation_id"] == custom_correlation_id


class TestLogChatMessage:
    """Tests for log_chat_message function."""
    
    def test_logs_user_message(self, mock_logger, mock_correlation_id, sample_message_data):
        """Test logging user message."""
        log_chat_message(
            chat_id=sample_message_data["chat_id"],
            message_id=sample_message_data["message_id"],
            message_type=sample_message_data["message_type"],
            role=sample_message_data["role"],
            content=sample_message_data["content"],
            user_id=sample_message_data["user_id"],
            username=sample_message_data["username"],
        )
        
        # Verify logger.bind was called
        bind_call = mock_logger.bind.call_args
        bind_kwargs = bind_call[1]
        
        assert bind_kwargs["correlation_id"] == mock_correlation_id
        assert bind_kwargs["chat_id"] == sample_message_data["chat_id"]
        assert bind_kwargs["message_id"] == sample_message_data["message_id"]
        assert bind_kwargs["message_type"] == "user"
        
        # Verify info was called
        info_call = mock_logger.bind.return_value.info.call_args
        assert "CHAT_MESSAGE" in info_call[0][0]
        
        # Check extra data
        extra = info_call[1]["extra"]
        chat_event = extra["chat_event"]
        
        assert chat_event["type"] == "chat_message"
        assert chat_event["message_type"] == "user"
        assert chat_event["role"] == "user"
        assert chat_event["is_ai_response"] is False
        assert chat_event["content_length"] == len(sample_message_data["content"])
        assert chat_event["content_preview"] == sample_message_data["content"][:200]
    
    def test_logs_ai_response_with_metrics(self, mock_logger, mock_correlation_id, sample_message_data):
        """Test logging AI response with metrics."""
        tokens_used = 450
        processing_time = 2340
        
        log_chat_message(
            chat_id=sample_message_data["chat_id"],
            message_id=sample_message_data["message_id"],
            message_type="assistant",
            role="assistant",
            content="This is an AI response",
            user_id=sample_message_data["user_id"],
            username=sample_message_data["username"],
            tokens_used=tokens_used,
            processing_time=processing_time,
        )
        
        # Verify bind includes AI metrics
        bind_call = mock_logger.bind.call_args
        bind_kwargs = bind_call[1]
        
        assert bind_kwargs["tokens_used"] == tokens_used
        assert bind_kwargs["processing_time_ms"] == processing_time
        
        # Verify info message includes metrics
        info_call = mock_logger.bind.return_value.info.call_args
        log_message = info_call[0][0]
        assert f"tokens={tokens_used}" in log_message
        assert f"time={processing_time}ms" in log_message
        
        # Check extra data
        extra = info_call[1]["extra"]
        chat_event = extra["chat_event"]
        
        assert chat_event["is_ai_response"] is True
        assert chat_event["tokens_used"] == tokens_used
        assert chat_event["processing_time_ms"] == processing_time
        assert chat_event["event"] == "ai_response_generated"
        assert chat_event["action"] == "AI_RESPONSE"
    
    def test_logs_message_with_parent(self, mock_logger, mock_correlation_id, sample_message_data):
        """Test logging message with parent message ID."""
        parent_id = str(uuid.uuid4())
        
        log_chat_message(
            chat_id=sample_message_data["chat_id"],
            message_id=sample_message_data["message_id"],
            message_type=sample_message_data["message_type"],
            role=sample_message_data["role"],
            content=sample_message_data["content"],
            user_id=sample_message_data["user_id"],
            username=sample_message_data["username"],
            parent_message_id=parent_id,
        )
        
        # Check extra data includes parent
        info_call = mock_logger.bind.return_value.info.call_args
        extra = info_call[1]["extra"]
        chat_event = extra["chat_event"]
        
        assert chat_event["parent_message_id"] == parent_id
    
    @patch('aldar_middleware.monitoring.chat_cosmos_logger.settings')
    def test_respects_content_saving_setting(self, mock_settings, mock_logger, mock_correlation_id, sample_message_data):
        """Test that content saving respects settings."""
        # Test with content saving enabled
        mock_settings.cosmos_logging_save_request_response = True
        
        log_chat_message(
            chat_id=sample_message_data["chat_id"],
            message_id=sample_message_data["message_id"],
            message_type=sample_message_data["message_type"],
            role=sample_message_data["role"],
            content=sample_message_data["content"],
            user_id=sample_message_data["user_id"],
            username=sample_message_data["username"],
        )
        
        info_call = mock_logger.bind.return_value.info.call_args
        extra = info_call[1]["extra"]
        chat_event = extra["chat_event"]
        
        assert chat_event["content"] == sample_message_data["content"]
        
        # Test with content saving disabled
        mock_settings.cosmos_logging_save_request_response = False
        mock_logger.reset_mock()
        
        log_chat_message(
            chat_id=sample_message_data["chat_id"],
            message_id=sample_message_data["message_id"],
            message_type=sample_message_data["message_type"],
            role=sample_message_data["role"],
            content=sample_message_data["content"],
            user_id=sample_message_data["user_id"],
            username=sample_message_data["username"],
        )
        
        info_call = mock_logger.bind.return_value.info.call_args
        extra = info_call[1]["extra"]
        chat_event = extra["chat_event"]
        
        assert chat_event["content"] is None


class TestLogChatSessionUpdated:
    """Tests for log_chat_session_updated function."""
    
    def test_logs_session_update(self, mock_logger, mock_correlation_id, sample_chat_data):
        """Test logging session update."""
        updates = {
            "title": "Updated Title",
            "agent_id": "new-agent",
        }
        
        log_chat_session_updated(
            chat_id=sample_chat_data["chat_id"],
            session_id=sample_chat_data["session_id"],
            user_id=sample_chat_data["user_id"],
            username=sample_chat_data["username"],
            updates=updates,
        )
        
        # Verify info was called
        info_call = mock_logger.bind.return_value.info.call_args
        assert "CHAT_SESSION_UPDATED" in info_call[0][0]
        assert "title" in info_call[0][0]
        assert "agent_id" in info_call[0][0]
        
        # Check extra data
        extra = info_call[1]["extra"]
        chat_event = extra["chat_event"]
        
        assert chat_event["type"] == "chat_session_updated"
        assert chat_event["updates"] == updates
        assert set(chat_event["updated_fields"]) == {"title", "agent_id"}


class TestLogChatSessionDeleted:
    """Tests for log_chat_session_deleted function."""
    
    def test_logs_session_deletion(self, mock_logger, mock_correlation_id, sample_chat_data):
        """Test logging session deletion."""
        message_count = 47
        
        log_chat_session_deleted(
            chat_id=sample_chat_data["chat_id"],
            session_id=sample_chat_data["session_id"],
            user_id=sample_chat_data["user_id"],
            username=sample_chat_data["username"],
            message_count=message_count,
        )
        
        # Verify info was called
        info_call = mock_logger.bind.return_value.info.call_args
        assert "CHAT_SESSION_DELETED" in info_call[0][0]
        assert str(message_count) in info_call[0][0]
        
        # Check extra data
        extra = info_call[1]["extra"]
        chat_event = extra["chat_event"]
        
        assert chat_event["type"] == "chat_session_deleted"
        assert chat_event["message_count"] == message_count
        assert chat_event["event"] == "chat_session_deleted"


class TestLogChatFavoriteToggled:
    """Tests for log_chat_favorite_toggled function."""
    
    def test_logs_favorite_toggle_on(self, mock_logger, mock_correlation_id, sample_chat_data):
        """Test logging favorite toggle to true."""
        log_chat_favorite_toggled(
            chat_id=sample_chat_data["chat_id"],
            session_id=sample_chat_data["session_id"],
            user_id=sample_chat_data["user_id"],
            username=sample_chat_data["username"],
            is_favorite=True,
        )
        
        # Verify info message
        info_call = mock_logger.bind.return_value.info.call_args
        assert "favorited" in info_call[0][0]
        
        # Check extra data
        extra = info_call[1]["extra"]
        chat_event = extra["chat_event"]
        
        assert chat_event["type"] == "chat_favorite_toggled"
        assert chat_event["is_favorite"] is True
    
    def test_logs_favorite_toggle_off(self, mock_logger, mock_correlation_id, sample_chat_data):
        """Test logging favorite toggle to false."""
        log_chat_favorite_toggled(
            chat_id=sample_chat_data["chat_id"],
            session_id=sample_chat_data["session_id"],
            user_id=sample_chat_data["user_id"],
            username=sample_chat_data["username"],
            is_favorite=False,
        )
        
        # Verify info message
        info_call = mock_logger.bind.return_value.info.call_args
        assert "unfavorited" in info_call[0][0]
        
        # Check extra data
        extra = info_call[1]["extra"]
        chat_event = extra["chat_event"]
        
        assert chat_event["is_favorite"] is False


class TestLogChatAnalyticsEvent:
    """Tests for log_chat_analytics_event function."""
    
    def test_logs_analytics_event(self, mock_logger, mock_correlation_id):
        """Test logging analytics event."""
        chat_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        metrics = {
            "duration_seconds": 1847,
            "message_count": 23,
            "total_tokens": 5640,
        }
        
        log_chat_analytics_event(
            event_type="session_duration",
            chat_id=chat_id,
            user_id=user_id,
            metrics=metrics,
        )
        
        # Verify bind context
        bind_call = mock_logger.bind.call_args
        bind_kwargs = bind_call[1]
        
        assert bind_kwargs["event_type"] == "session_duration"
        assert bind_kwargs["chat_id"] == chat_id
        assert bind_kwargs["user_id"] == user_id
        
        # Verify info message
        info_call = mock_logger.bind.return_value.info.call_args
        assert "CHAT_ANALYTICS" in info_call[0][0]
        assert "session_duration" in info_call[0][0]
        
        # Check extra data
        extra = info_call[1]["extra"]
        chat_event = extra["chat_event"]
        
        assert chat_event["type"] == "chat_analytics"
        assert chat_event["metrics"] == metrics
        assert chat_event["event"] == "analytics_session_duration"


class TestUserContextIntegration:
    """Tests for user context integration."""
    
    def test_includes_user_context_when_available(self, mock_logger, mock_correlation_id, mock_user_context, sample_chat_data):
        """Test that user context is included in logs when available."""
        log_chat_session_created(
            chat_id=sample_chat_data["chat_id"],
            session_id=sample_chat_data["session_id"],
            user_id=sample_chat_data["user_id"],
            username=sample_chat_data["username"],
            title=sample_chat_data["title"],
        )
        
        # Verify bind includes user context
        bind_call = mock_logger.bind.call_args
        bind_kwargs = bind_call[1]
        
        assert bind_kwargs["email"] == mock_user_context.email
        assert bind_kwargs["is_authenticated"] == mock_user_context.is_authenticated
    
    def test_handles_missing_user_context(self, mock_logger, mock_correlation_id, sample_chat_data):
        """Test that logging works when user context is not available."""
        with patch('aldar_middleware.monitoring.chat_cosmos_logger.get_user_context', return_value=None):
            log_chat_session_created(
                chat_id=sample_chat_data["chat_id"],
                session_id=sample_chat_data["session_id"],
                user_id=sample_chat_data["user_id"],
                username=sample_chat_data["username"],
                title=sample_chat_data["title"],
            )
        
        # Should still log successfully
        assert mock_logger.bind.called
        assert mock_logger.bind.return_value.info.called


class TestTimestampGeneration:
    """Tests for timestamp generation."""
    
    @patch('aldar_middleware.monitoring.chat_cosmos_logger.datetime')
    def test_uses_utc_timestamp(self, mock_datetime, mock_logger, mock_correlation_id, sample_chat_data):
        """Test that timestamps are in UTC."""
        fixed_time = datetime(2025, 11, 2, 15, 30, 45, 123456, tzinfo=timezone.utc)
        mock_datetime.now.return_value = fixed_time
        
        log_chat_session_created(
            chat_id=sample_chat_data["chat_id"],
            session_id=sample_chat_data["session_id"],
            user_id=sample_chat_data["user_id"],
            username=sample_chat_data["username"],
            title=sample_chat_data["title"],
        )
        
        # Verify datetime.now was called with timezone.utc
        mock_datetime.now.assert_called_with(timezone.utc)
        
        # Check that timestamp is in ISO format
        info_call = mock_logger.bind.return_value.info.call_args
        extra = info_call[1]["extra"]
        chat_event = extra["chat_event"]
        
        assert chat_event["timestamp"] == fixed_time.isoformat()


class TestLogEntryStructure:
    """Tests for log entry structure."""
    
    def test_log_entry_has_required_fields(self, mock_logger, mock_correlation_id, sample_message_data):
        """Test that log entries have all required fields."""
        log_chat_message(
            chat_id=sample_message_data["chat_id"],
            message_id=sample_message_data["message_id"],
            message_type=sample_message_data["message_type"],
            role=sample_message_data["role"],
            content=sample_message_data["content"],
            user_id=sample_message_data["user_id"],
            username=sample_message_data["username"],
        )
        
        # Get the logged event
        info_call = mock_logger.bind.return_value.info.call_args
        extra = info_call[1]["extra"]
        chat_event = extra["chat_event"]
        
        # Check required fields
        required_fields = ["id", "type", "timestamp", "correlation_id", "event", "action"]
        for field in required_fields:
            assert field in chat_event, f"Missing required field: {field}"
        
        # Check ID is a valid UUID
        assert len(chat_event["id"]) == 36  # UUID format
        
        # Check timestamp is ISO format
        assert "T" in chat_event["timestamp"]
        assert "Z" in chat_event["timestamp"] or "+" in chat_event["timestamp"]

