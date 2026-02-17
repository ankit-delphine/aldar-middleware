"""Tests for question tracker functionality."""

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from aldar_middleware.models.user import User
from aldar_middleware.models.question_tracker import UserQuestionTracker


class TestUserQuestionTrackerModel:
    """Test UserQuestionTracker model methods."""
    
    def test_increment_count(self):
        """Test increment_count method."""
        tracker = UserQuestionTracker(
            user_id=uuid4(),
            year=2025,
            month=12,
            question_count=5,
            minimum_threshold=10,
            maximum_threshold=100,
        )
        
        new_count = tracker.increment_count()
        
        assert new_count == 6
        assert tracker.question_count == 6
    
    def test_is_at_minimum(self):
        """Test is_at_minimum method."""
        tracker = UserQuestionTracker(
            user_id=uuid4(),
            year=2025,
            month=12,
            question_count=9,
            minimum_threshold=10,
            maximum_threshold=100,
        )
        
        assert tracker.is_at_minimum() is False
        
        tracker.question_count = 10
        assert tracker.is_at_minimum() is True
        
        tracker.question_count = 15
        assert tracker.is_at_minimum() is True
    
    def test_is_at_maximum(self):
        """Test is_at_maximum method."""
        tracker = UserQuestionTracker(
            user_id=uuid4(),
            year=2025,
            month=12,
            question_count=99,
            minimum_threshold=10,
            maximum_threshold=100,
        )
        
        assert tracker.is_at_maximum() is False
        
        tracker.question_count = 100
        assert tracker.is_at_maximum() is True
        
        tracker.question_count = 150
        assert tracker.is_at_maximum() is True
    
    def test_percentage_used(self):
        """Test percentage_used method."""
        tracker = UserQuestionTracker(
            user_id=uuid4(),
            year=2025,
            month=12,
            question_count=50,
            minimum_threshold=10,
            maximum_threshold=100,
        )
        
        assert tracker.percentage_used() == 50.0
        
        tracker.question_count = 100
        assert tracker.percentage_used() == 100.0
        
        tracker.question_count = 150
        assert tracker.percentage_used() == 150.0
        
        tracker.question_count = 0
        assert tracker.percentage_used() == 0.0
    
    def test_percentage_used_zero_max_threshold(self):
        """Test percentage_used when max_threshold is 0."""
        tracker = UserQuestionTracker(
            user_id=uuid4(),
            year=2025,
            month=12,
            question_count=50,
            minimum_threshold=0,
            maximum_threshold=0,
        )
        
        assert tracker.percentage_used() == 0.0
    
    def test_default_values(self):
        """Test default threshold constants."""
        # Test class constants
        assert UserQuestionTracker.DEFAULT_MINIMUM_THRESHOLD == 10
        assert UserQuestionTracker.DEFAULT_MAXIMUM_THRESHOLD == 100
        
        # Test that when created with defaults, they match constants
        tracker = UserQuestionTracker(
            user_id=uuid4(),
            year=2025,
            month=12,
            question_count=0,
            minimum_threshold=UserQuestionTracker.DEFAULT_MINIMUM_THRESHOLD,
            maximum_threshold=UserQuestionTracker.DEFAULT_MAXIMUM_THRESHOLD,
        )
        
        assert tracker.question_count == 0
        assert tracker.minimum_threshold == 10
        assert tracker.maximum_threshold == 100
    
    def test_repr(self):
        """Test string representation."""
        user_id = uuid4()
        tracker = UserQuestionTracker(
            user_id=user_id,
            year=2025,
            month=12,
            question_count=42,
        )
        
        repr_str = repr(tracker)
        assert "UserQuestionTracker" in repr_str
        assert str(user_id) in repr_str
        assert "2025" in repr_str
        assert "12" in repr_str
        assert "42" in repr_str
