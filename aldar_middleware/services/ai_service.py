"""AI service for generating responses."""

import time
from typing import Dict, Any, Optional, List
from datetime import datetime

import openai
from loguru import logger

from aldar_middleware.settings import settings
from aldar_middleware.database.base import get_db
from aldar_middleware.models.messages import Message
from aldar_middleware.settings.context import get_correlation_id, track_agent_call
from aldar_middleware.monitoring.prometheus import (
    record_openai_call,
    record_agent_call,
    record_agent_error
)


class AIService:
    """Service for AI-powered responses."""

    def __init__(self):
        """Initialize AI service."""
        if settings.openai_api_key:
            self.client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        else:
            self.client = None
        self.model = settings.openai_model

    async def generate_response(
        self, 
        user_message: str, 
        chat_id: str, 
        user_id: str,
        context: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Generate AI response for user message using Azure Web PubSub streaming."""
        import uuid
        start_time = time.time()
        correlation_id = get_correlation_id()
        
        # Generate stream ID for PubSub
        stream_id = str(uuid.uuid4())
        
        # Track agent call in context
        track_agent_call(
            agent_type="mcp",
            agent_name="stream",
            method="generate_response"
        )
        
        logger.info(
            f"Generating AI response for chat_id={chat_id}, user_id={user_id}, "
            f"stream_id={stream_id}, correlation_id={correlation_id}"
        )
        
        # Return stream_id immediately for streaming response
        # The actual AI processing will happen asynchronously via Azure Web PubSub
        return {
            "content": None,  # Content will be streamed via PubSub
            "stream_id": stream_id,
            "streamId": stream_id,  # Support both naming conventions
            "tokens_used": 0,
            "processing_time": int((time.time() - start_time) * 1000),
            "status": "streaming"
        }

    async def _get_chat_context(self, chat_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent chat context."""
        async for db in get_db():
            from sqlalchemy import select, desc
            result = await db.execute(
                select(Message)
                .where(Message.session_id == chat_id)
                .order_by(desc(Message.created_at))
                .limit(limit)
            )
            messages = result.scalars().all()
            
            return [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.created_at.isoformat()
                }
                for msg in reversed(messages)
            ]

    def _prepare_messages(self, user_message: str, context: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """Prepare messages for OpenAI API."""
        messages = [
            {
                "role": "system",
                "content": "You are an AI assistant that helps users with their questions. Be helpful, accurate, and concise."
            }
        ]
        
        # Add context messages
        for msg in context[-10:]:  # Limit to last 10 messages
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        # Add current user message
        messages.append({
            "role": "user",
            "content": user_message
        })
        
        return messages

    async def analyze_sentiment(self, text: str) -> Dict[str, Any]:
        """Analyze sentiment of text."""
        if not self.client:
            return {
                "sentiment": "neutral",
                "confidence": 0.0,
                "error": "OpenAI API key not configured"
            }
            
        start_time = time.time()
        correlation_id = get_correlation_id()
        
        # Track agent call in context
        track_agent_call(
            agent_type="openai",
            agent_name=self.model,
            method="analyze_sentiment"
        )
        
        logger.info(
            f"Analyzing sentiment with model={self.model}, "
            f"correlation_id={correlation_id}"
        )
        
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "Analyze the sentiment of the following text and return a JSON response with 'sentiment' (positive/negative/neutral) and 'confidence' (0-1)."
                    },
                    {
                        "role": "user",
                        "content": text
                    }
                ],
                max_tokens=100,
                temperature=0.3
            )
            
            duration = time.time() - start_time
            
            # Extract token usage
            prompt_tokens = response.usage.prompt_tokens if response.usage else 0
            completion_tokens = response.usage.completion_tokens if response.usage else 0
            total_tokens = response.usage.total_tokens if response.usage else 0
            
            # Record Prometheus metrics
            record_openai_call(
                model=self.model,
                method="analyze_sentiment",
                status="success",
                duration=duration,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens
            )
            
            record_agent_call(
                agent_type="openai",
                agent_name=self.model,
                method="analyze_sentiment",
                duration=duration,
                status="success"
            )
            
            logger.info(
                f"Sentiment analysis completed: tokens={total_tokens}, "
                f"duration={duration:.2f}s, correlation_id={correlation_id}"
            )
            
            return {
                "sentiment": "neutral",
                "confidence": 0.5,
                "analysis": response.choices[0].message.content,
                "tokens_used": total_tokens
            }
            
        except Exception as e:
            duration = time.time() - start_time
            error_type = type(e).__name__
            
            logger.error(
                f"Error analyzing sentiment: {e}, "
                f"correlation_id={correlation_id}, error_type={error_type}"
            )
            
            # Record error metrics
            record_openai_call(
                model=self.model,
                method="analyze_sentiment",
                status="error",
                duration=duration
            )
            
            record_agent_call(
                agent_type="openai",
                agent_name=self.model,
                method="analyze_sentiment",
                duration=duration,
                status="error"
            )
            
            record_agent_error(
                agent_type="openai",
                agent_name=self.model,
                error_type=error_type
            )
            
            return {
                "sentiment": "neutral",
                "confidence": 0.0,
                "error": str(e)
            }
