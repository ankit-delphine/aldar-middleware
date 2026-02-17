"""User-facing logs API routes."""

import csv
import io
import json
import logging
import uuid
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from uuid import UUID as UUIDType

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from aldar_middleware.database.base import get_db
from aldar_middleware.models.user import User
from aldar_middleware.models.sessions import Session
from aldar_middleware.models.messages import Message
from aldar_middleware.models.menu import Agent
from aldar_middleware.models.attachment import Attachment
from aldar_middleware.auth.dependencies import get_current_admin_user
from aldar_middleware.utils.agent_utils import determine_agent_type
from aldar_middleware.admin.schemas import (
    LogQueryRequest,
    UserLogEventResponse,
)
from aldar_middleware.services.logs_service import LogsService
from aldar_middleware.services.user_logs_service import user_logs_service
from aldar_middleware.services.postgres_logs_service import postgres_logs_service
from aldar_middleware.schemas.feedback import PaginatedResponse
from aldar_middleware.settings import settings
from aldar_middleware.settings.context import get_correlation_id

logger = logging.getLogger(__name__)

router = APIRouter()


def _normalize_value(value: Any) -> Optional[str]:
    """Normalize value - convert "N/A", "NONE", empty strings to None."""
    if value is None:
        return None
    str_value = str(value).strip()
    if str_value.upper() in ["N/A", "NONE", ""]:
        return None
    return str_value


def _transform_to_user_log_event(
    item: Dict[str, Any],
    user_info: Optional[Dict[str, Any]] = None
) -> Optional[UserLogEventResponse]:
    """Transform Cosmos DB log item to UserLogEventResponse (3.0 format).
    
    Args:
        item: Raw Cosmos DB log item
        user_info: Optional user information dict with full_name, email, department, etc.
        
    Returns:
        UserLogEventResponse if the item can be transformed, None otherwise
    """
    try:
        # Extract chat_event if present, otherwise use the item directly
        # Check multiple possible locations for chat_event data
        # When logs are written, chat_event is in extra["chat_event"]
        # When retrieved from Cosmos DB, it might be at top level or in extra
        # For chat_session_created, fields are stored at top level of item
        chat_event = None
        # Priority 1: Check extra["chat_event"] (where loguru stores it)
        extra = item.get("extra", {})
        if isinstance(extra, dict):
            chat_event = extra.get("chat_event")
        # Priority 2: Check top-level chat_event
        if not chat_event:
            chat_event = item.get("chat_event")
        # Priority 3: For chat_session_created, use item itself (fields are at top level)
        if not chat_event:
            chat_event = item
        
        # For chat_session_created, also check top-level item and extra["chat_event"] for fields
        # (since they're stored directly in the log entry or in extra["chat_event"])
        if item.get("type") == "chat_session_created" or (chat_event and chat_event.get("type") == "chat_session_created"):
            # Merge item fields into chat_event for easier access
            # IMPORTANT: Always merge from item to ensure we get all fields
            # CRITICAL: session_id should be merged BEFORE conversationId to ensure correct priority
            # Also check extra["chat_event"] for fields
            sources = [item]
            if extra and isinstance(extra, dict) and extra.get("chat_event"):
                sources.append(extra["chat_event"])
            
            for source in sources:
                for key in ["session_id", "selectedAgentType", "customQueryAboutUser", "isInternetSearchUsed", 
                           "isSentDirectlyToOpenAi", "customQueryTopicsOfInterest", 
                           "customQueryPreferredFormatting", "agent", "conversationId",
                           "agent_id", "agent_public_id", "agent_name", "agent_type", "chat_id"]:
                    if key in source and key not in chat_event:
                        chat_event[key] = source[key]
        
        # Also check if this is a LogEntryResponse converted to dict
        message = item.get("message", "")
        
        # Determine event type based on chat_event.type or message content
        event_type = None
        event_payload: Dict[str, Any] = {}
        
        # Check for chat_session_created -> USER_CONVERSATION_CREATED
        # Look for CHAT_SESSION_CREATED in message or chat_event.type
        if (chat_event.get("type") == "chat_session_created" or 
            "CHAT_SESSION_CREATED" in message.upper()):
            event_type = "USER_CONVERSATION_CREATED"
            
            # Extract conversation ID from chat_event, item, or try to parse from message
            # IMPORTANT: Use session_id first (this is the actual session UUID from agno_sessions)
            # Priority: session_id > conversationId > chat_id (but NOT agent_id)
            conversation_id = None
            # Priority 1: session_id (this is the actual session UUID from agno_sessions)
            if chat_event.get("session_id"):
                conversation_id = chat_event.get("session_id")
            elif item.get("session_id"):
                conversation_id = item.get("session_id")
            # Priority 2: conversationId (should be same as session_id after fix)
            elif chat_event.get("conversationId"):
                conversation_id = chat_event.get("conversationId")
            elif item.get("conversationId"):
                conversation_id = item.get("conversationId")
            # Priority 3: chat_id (fallback, but should not be used)
            elif chat_event.get("chat_id"):
                conversation_id = chat_event.get("chat_id")
            elif item.get("chat_id"):
                conversation_id = item.get("chat_id")
            
            # Try to extract from message if not found
            if not conversation_id and "chat" in message.lower():
                import re
                # Look for UUID pattern in message
                uuid_pattern = r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})'
                uuids = re.findall(uuid_pattern, message, re.IGNORECASE)
                if uuids:
                    conversation_id = uuids[0]
            
            # Extract all fields from Cosmos DB (chat_event or item)
            # Get session_id (same as conversationId but for clarity)
            session_id = (chat_event.get("session_id") or 
                         item.get("session_id") or
                         conversation_id)
            
            # Get initial message / user input - check multiple possible locations
            user_input = None
            if chat_event.get("initial_message"):
                user_input = chat_event.get("initial_message")
            elif chat_event.get("userInput"):
                user_input = chat_event.get("userInput")
            elif item.get("initial_message"):
                user_input = item.get("initial_message")
            elif item.get("userInput"):
                user_input = item.get("userInput")
            
            # Get message_id if available (from first message in session)
            message_id = None
            if chat_event.get("message_id"):
                message_id = chat_event.get("message_id")
            elif item.get("message_id"):
                message_id = item.get("message_id")
            
            # Extract all fields with proper None checking
            selected_agent_type = None
            if chat_event.get("selectedAgentType"):
                selected_agent_type = chat_event.get("selectedAgentType")
            elif item.get("selectedAgentType"):
                selected_agent_type = item.get("selectedAgentType")
            elif chat_event.get("selected_agent_type"):
                selected_agent_type = chat_event.get("selected_agent_type")
            elif item.get("selected_agent_type"):
                selected_agent_type = item.get("selected_agent_type")
            
            custom_query_about_user = None
            if chat_event.get("customQueryAboutUser"):
                custom_query_about_user = chat_event.get("customQueryAboutUser")
            elif item.get("customQueryAboutUser"):
                custom_query_about_user = item.get("customQueryAboutUser")
            
            is_internet_search_used = None
            if chat_event.get("isInternetSearchUsed") is not None:
                is_internet_search_used = chat_event.get("isInternetSearchUsed")
            elif item.get("isInternetSearchUsed") is not None:
                is_internet_search_used = item.get("isInternetSearchUsed")
            
            is_sent_directly_to_openai = None
            if chat_event.get("isSentDirectlyToOpenAi") is not None:
                is_sent_directly_to_openai = chat_event.get("isSentDirectlyToOpenAi")
            elif item.get("isSentDirectlyToOpenAi") is not None:
                is_sent_directly_to_openai = item.get("isSentDirectlyToOpenAi")
            
            custom_query_topics_of_interest = None
            if chat_event.get("customQueryTopicsOfInterest"):
                custom_query_topics_of_interest = chat_event.get("customQueryTopicsOfInterest")
            elif item.get("customQueryTopicsOfInterest"):
                custom_query_topics_of_interest = item.get("customQueryTopicsOfInterest")
            
            custom_query_preferred_formatting = None
            if chat_event.get("customQueryPreferredFormatting"):
                custom_query_preferred_formatting = chat_event.get("customQueryPreferredFormatting")
            elif item.get("customQueryPreferredFormatting"):
                custom_query_preferred_formatting = item.get("customQueryPreferredFormatting")
            
            # Build event_payload for USER_CONVERSATION_CREATED
            # Always include conversationId and sessionId
            event_payload = {}
            
            if conversation_id or chat_event.get("conversationId"):
                event_payload["conversationId"] = conversation_id or chat_event.get("conversationId")
            
            if session_id:
                event_payload["sessionId"] = session_id
            
            # Add messageId if available
            if message_id:
                event_payload["messageId"] = message_id
            
            # Add selectedAgentType if available
            if selected_agent_type:
                event_payload["selectedAgentType"] = selected_agent_type
            
            # Add custom query fields if available
            if custom_query_about_user:
                event_payload["customQueryAboutUser"] = custom_query_about_user
            
            if custom_query_topics_of_interest:
                event_payload["customQueryTopicsOfInterest"] = custom_query_topics_of_interest
            
            if custom_query_preferred_formatting:
                event_payload["customQueryPreferredFormatting"] = custom_query_preferred_formatting
            
            # Boolean fields - include even if False (required for USER_CONVERSATION_CREATED)
            if is_internet_search_used is not None:
                event_payload["isInternetSearchUsed"] = is_internet_search_used
            
            if is_sent_directly_to_openai is not None:
                event_payload["isSentDirectlyToOpenAi"] = is_sent_directly_to_openai
            
            # Extract agent info from Cosmos DB - check for agent object first, then individual fields
            # Check multiple locations: extra["chat_event"]["agent"], chat_event["agent"], item["agent"]
            agent_object = None
            # Priority 1: Check extra["chat_event"]["agent"] (where loguru stores it)
            if extra and isinstance(extra, dict):
                extra_chat_event = extra.get("chat_event")
                if isinstance(extra_chat_event, dict) and extra_chat_event.get("agent"):
                    agent_object = extra_chat_event.get("agent")
            # Priority 2: Check chat_event["agent"]
            if not agent_object and chat_event and isinstance(chat_event, dict):
                if chat_event.get("agent"):
                    agent_object = chat_event.get("agent")
            # Priority 3: Check item["agent"]
            if not agent_object and item.get("agent"):
                agent_object = item.get("agent")
            
            # Extract agentId - always show agentId even if agentName is missing
            agent_id = None
            agent_name = None
            agent_type = None
            
            if agent_object and isinstance(agent_object, dict):
                # Agent object found - extract fields
                agent_id = (agent_object.get("agentId") or 
                           agent_object.get("agent_public_id") or 
                           agent_object.get("agent_id"))
                agent_name = (agent_object.get("agentName") or 
                             agent_object.get("agent_name"))
                agent_type = (agent_object.get("agentType") or 
                             agent_object.get("agent_type"))
            else:
                # Fallback to individual fields from chat_event or item
                # Check multiple possible field names for agent_id
                agent_id = (chat_event.get("agent_public_id") or 
                           chat_event.get("agent_id") or 
                           item.get("agent_public_id") or
                           item.get("agent_id"))
                agent_name = (chat_event.get("agent_name") or 
                       item.get("agent_name"))
            agent_type = (chat_event.get("agent_type") or 
                         item.get("agent_type"))
            
            # Always include agent if we have agentId (even if agentName is missing)
            # This ensures agentId is shown even if agentName is not available
            if agent_id:
                event_payload["agent"] = {
                    "agentId": agent_id,
                    "agentName": agent_name if agent_name else None,  # Show None if not available
                    "agentType": agent_type if agent_type else None   # Show None if not available
                }
        
        # Check for chat_message -> USER_MESSAGE_CREATED
        elif (chat_event.get("type") == "chat_message" or 
              "CHAT_MESSAGE" in message.upper()):
            # Only user messages, not AI responses
            # Check if this is a user message (not AI response)
            is_ai_response = chat_event.get("is_ai_response", False)
            role = chat_event.get("role", "")
            message_type = chat_event.get("message_type", "")
            # If message contains "AI_RESPONSE" or "ai_response_generated", skip it
            # Also check role and message_type to ensure we only process user messages
            is_user_message = (
                not is_ai_response 
                and role.lower() in ["user", ""]  # Empty role might be user message
                and message_type.lower() in ["user", ""]  # Empty message_type might be user message
                and "AI_RESPONSE" not in message.upper() 
                and "ai_response_generated" not in message.lower()
            )
            if is_user_message:
                event_type = "USER_MESSAGE_CREATED"
                
                # Extract message and conversation IDs from chat_event or item
                # IMPORTANT: Do NOT use chat_event.get("id") as fallback for message_id
                # chat_event["id"] is the log entry's ID, not the message ID
                message_id = (chat_event.get("message_id") or 
                             item.get("message_id"))
                conversation_id = (chat_event.get("chat_id") or 
                                 chat_event.get("session_id") or
                                 item.get("chat_id") or
                                 item.get("session_id"))
                
                # Try to extract from message if not found
                if not conversation_id and "chat" in message.lower():
                    import re
                    # Look for UUID pattern in message
                    uuid_pattern = r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})'
                    uuids = re.findall(uuid_pattern, message, re.IGNORECASE)
                    if uuids:
                        # Use first UUID as conversation_id if we don't have one
                        if not conversation_id:
                            conversation_id = uuids[0]
                        # Use second UUID as message_id if we don't have one
                        if not message_id and len(uuids) > 1:
                            message_id = uuids[1]
                
                # Extract all fields from Cosmos DB (chat_event or item)
                event_payload = {
                    "messageId": message_id or chat_event.get("messageId"),
                    "conversationId": conversation_id or chat_event.get("conversationId"),
                    "selectedAgentType": (chat_event.get("selectedAgentType") or 
                                         item.get("selectedAgentType") or
                                         chat_event.get("selected_agent_type") or
                                         item.get("selected_agent_type")),
                    "customQueryAboutUser": (chat_event.get("customQueryAboutUser") or 
                                           item.get("customQueryAboutUser")),
                    "isInternetSearchUsed": (chat_event.get("isInternetSearchUsed") if chat_event.get("isInternetSearchUsed") is not None else
                                            item.get("isInternetSearchUsed") if item.get("isInternetSearchUsed") is not None else None),
                    "isSentDirectlyToOpenAi": (chat_event.get("isSentDirectlyToOpenAi") if chat_event.get("isSentDirectlyToOpenAi") is not None else
                                               item.get("isSentDirectlyToOpenAi") if item.get("isSentDirectlyToOpenAi") is not None else None),
                    "customQueryTopicsOfInterest": (chat_event.get("customQueryTopicsOfInterest") or 
                                                   item.get("customQueryTopicsOfInterest")),
                    "customQueryPreferredFormatting": (chat_event.get("customQueryPreferredFormatting") or 
                                                     item.get("customQueryPreferredFormatting")),
                    "userInput": (chat_event.get("userInput") or 
                                item.get("userInput") or
                                chat_event.get("user_input") or
                                item.get("user_input")),
                }
                
                # Add metrics if available
                tokens_used = (chat_event.get("tokens_used") or 
                              item.get("tokens_used"))
                processing_time = (chat_event.get("processing_time_ms") or 
                                 item.get("processing_time_ms"))
                if tokens_used or processing_time:
                    event_payload["metrics"] = {
                        "tokenUsage": {
                            "used": tokens_used or 0,
                            "available": 0
                        },
                        "processingTime": processing_time or 0
                    }
        
        # Check for conversation_renamed -> USER_CONVERSATION_RENAMED
        # Check both chat_event.type and item.type (in case chat_event is the item itself)
        elif (chat_event.get("type") == "conversation_renamed" or 
              item.get("type") == "conversation_renamed" or
              "CONVERSATION_RENAMED" in message.upper()):
            event_type = "USER_CONVERSATION_RENAMED"
            conversation_id = (chat_event.get("chat_id") or 
                             chat_event.get("session_id") or
                             chat_event.get("conversationId") or
                             item.get("chat_id") or
                             item.get("session_id"))
            
            # Extract old_title and new_title from chat_event or item
            # chat_event is the log_entry dict that was stored in extra["chat_event"]
            # So old_title and new_title should be directly in chat_event
            old_title = chat_event.get("old_title")
            new_title = chat_event.get("new_title")
            
            # Fallback to item level if not in chat_event
            if old_title is None:
                old_title = item.get("old_title")
            if new_title is None:
                new_title = item.get("new_title")
            
            event_payload = {}
            
            # Add conversationId if available
            if conversation_id:
                event_payload["conversationId"] = conversation_id
            
            # Add titles if available (even if None, we'll filter later)
            if old_title is not None:
                event_payload["oldTitle"] = old_title
            if new_title is not None:
                event_payload["newTitle"] = new_title
        
        # Check for starting_prompt_chosen -> USER_CONVERSATION_STARTING_PROMPT_CHOSEN
        elif (chat_event.get("type") == "starting_prompt_chosen" or 
              item.get("type") == "starting_prompt_chosen" or
              "STARTING_PROMPT_CHOSEN" in message.upper()):
            event_type = "USER_CONVERSATION_STARTING_PROMPT_CHOSEN"
            conversation_id = (chat_event.get("chat_id") or 
                             chat_event.get("session_id") or
                             chat_event.get("conversationId") or
                             item.get("chat_id") or
                             item.get("session_id"))
            
            # Extract agent info
            agent_object = chat_event.get("agent") or item.get("agent")
            agent_info = None
            if agent_object and isinstance(agent_object, dict):
                agent_info = {
                    "agentId": agent_object.get("agentId"),
                    "agentName": agent_object.get("agentName"),
                    "agentType": agent_object.get("agentType")
                }
            
            # Extract prompt info from chat_event or item
            prompt_id = chat_event.get("prompt_id")
            if prompt_id is None:
                prompt_id = item.get("prompt_id")
            
            prompt_text = chat_event.get("prompt_text")
            if prompt_text is None:
                prompt_text = item.get("prompt_text")
            
            event_payload = {}
            
            # Add conversationId if available
            if conversation_id:
                event_payload["conversationId"] = conversation_id
            
            # Add prompt info if available
            if prompt_id is not None:
                event_payload["promptId"] = prompt_id
            if prompt_text is not None:
                event_payload["promptText"] = prompt_text
            
            # Add agent info if available
            if agent_info:
                event_payload["agent"] = agent_info
        
        # Check for my_agent_knowledge_sources_updated -> USER_MY_AGENT_KNOWLEDGE_SOURCES_UPDATED
        elif (chat_event.get("type") == "my_agent_knowledge_sources_updated" or 
              item.get("type") == "my_agent_knowledge_sources_updated" or
              "MY_AGENT_KNOWLEDGE_SOURCES_UPDATED" in message.upper()):
            event_type = "USER_MY_AGENT_KNOWLEDGE_SOURCES_UPDATED"
            
            # Extract agent info
            agent_object = chat_event.get("agent") or item.get("agent")
            agent_info = None
            if agent_object and isinstance(agent_object, dict):
                agent_info = {
                    "agentId": agent_object.get("agentId"),
                    "agentName": agent_object.get("agentName"),
                    "agentType": agent_object.get("agentType")
                }
            
            event_payload = {
                "agent": agent_info,
                "knowledgeSources": chat_event.get("knowledge_sources") or item.get("knowledge_sources") or [],
            }
        
        # Check for message_regenerated -> USER_MESSAGE_REGENERATED
        elif (chat_event.get("type") == "message_regenerated" or 
              item.get("type") == "message_regenerated" or
              "MESSAGE_REGENERATED" in message.upper()):
            event_type = "USER_MESSAGE_REGENERATED"
            conversation_id = (chat_event.get("chat_id") or 
                             chat_event.get("session_id") or
                             chat_event.get("conversationId") or
                             item.get("chat_id") or
                             item.get("session_id"))
            event_payload = {
                "conversationId": conversation_id,
                "messageId": chat_event.get("message_id") or item.get("message_id"),
                "originalMessageId": chat_event.get("original_message_id") or item.get("original_message_id"),
            }
        
        # Check for user_created -> USER_CREATED
        elif (chat_event.get("type") == "user_created" or 
              item.get("type") == "user_created" or
              "USER_CREATED" in message.upper()):
            event_type = "USER_CREATED"
            event_payload = {}  # User creation doesn't need additional payload
        
        # Check for conversation_download -> USER_CONVERSATION_DOWNLOAD
        elif (chat_event.get("type") == "conversation_download" or 
              item.get("type") == "conversation_download" or
              "CONVERSATION_DOWNLOAD" in message.upper()):
            event_type = "USER_CONVERSATION_DOWNLOAD"
            conversation_id = (chat_event.get("chat_id") or 
                             chat_event.get("session_id") or
                             chat_event.get("conversationId") or
                             item.get("chat_id") or
                             item.get("session_id"))
            event_payload = {
                "conversationId": conversation_id,
                "format": chat_event.get("format") or item.get("format") or "json",
            }
        
        # Check for conversation_share -> USER_CONVERSATION_SHARE
        elif (chat_event.get("type") == "conversation_share" or 
              item.get("type") == "conversation_share" or
              "CONVERSATION_SHARE" in message.upper()):
            event_type = "USER_CONVERSATION_SHARE"
            conversation_id = (chat_event.get("chat_id") or 
                             chat_event.get("session_id") or
                             chat_event.get("conversationId") or
                             item.get("chat_id") or
                             item.get("session_id"))
            event_payload = {
                "conversationId": conversation_id,
                "shareUrl": chat_event.get("share_url") or item.get("share_url"),
                "visibility": chat_event.get("visibility") or item.get("visibility"),
                "format": chat_event.get("format") or item.get("format") or "json",
            }
        
        # Check for chat_favorite_toggled -> USER_CONVERSATION_FAVORITED or USER_CONVERSATION_UNFAVORITED
        elif (chat_event.get("type") == "chat_favorite_toggled" or 
              item.get("type") == "chat_favorite_toggled" or
              "CHAT_FAVORITE_TOGGLED" in message.upper()):
            conversation_id = (chat_event.get("chat_id") or 
                             chat_event.get("session_id") or
                             chat_event.get("conversationId") or
                             item.get("chat_id") or
                             item.get("session_id"))
            is_favorite = chat_event.get("is_favorite") if chat_event.get("is_favorite") is not None else item.get("is_favorite")
            # Determine event type based on favorite status
            event_type = "USER_CONVERSATION_FAVORITED" if is_favorite else "USER_CONVERSATION_UNFAVORITED"
            event_payload = {
                "conversationId": conversation_id,
                "isFavorite": is_favorite,
                    }
        
        # Check for chat_session_deleted -> USER_CONVERSATION_DELETED
        elif (chat_event.get("type") == "chat_session_deleted" or 
              item.get("type") == "chat_session_deleted" or
              "CHAT_SESSION_DELETED" in message.upper() or
              "DELETE_CHAT_SESSION" in message.upper()):
            event_type = "USER_CONVERSATION_DELETED"
            conversation_id = (chat_event.get("session_id") or
                             chat_event.get("conversationId") or
                             chat_event.get("chat_id") or
                             item.get("session_id") or
                             item.get("conversationId") or
                             item.get("chat_id"))
            event_payload = {
                "conversationId": conversation_id,
            }
        
        # If we couldn't determine the event type, return None
        if not event_type:
            return None
        
        # Extract user information from Cosmos DB (chat_event or item) - ALL FROM COSMOS DB NOW
        # Try chat_event first (new format), then item (legacy format)
        email = _normalize_value(chat_event.get("email")) or _normalize_value(item.get("email"))
        # Check for user_role first (from log_chat_message), then role (from other events)
        role = (chat_event.get("user_role") or 
               chat_event.get("role") or 
               item.get("user_role") or
               item.get("role") or 
               "NORMAL")  # "ADMIN" or "NORMAL"
        department = (chat_event.get("department") or item.get("department"))
        name = (_normalize_value(chat_event.get("name")) or 
               _normalize_value(item.get("name")) or
               _normalize_value(chat_event.get("username")) or 
               _normalize_value(item.get("username")))
        user_entra_id = (chat_event.get("user_entra_id") or 
                        chat_event.get("userEntraId") or
                        item.get("user_entra_id") or
                        item.get("userEntraId"))
        profile_photo = (chat_event.get("profile_photo") or 
                       item.get("profile_photo"))
        
        # Fallback to user_info only if fields are missing (backward compatibility)
        if user_info:
            if not email and user_info.get("email"):
                email = user_info.get("email")
            if not role or role == "NORMAL":
                role = "ADMIN" if user_info.get("is_admin") else "NORMAL"
            if not department:
                department = user_info.get("department")
            if not name:
                name = user_info.get("full_name") or user_info.get("azure_display_name")
            if not user_entra_id:
                user_entra_id = user_info.get("azure_ad_id")
            if not profile_photo:
                profile_photo = user_info.get("profile_photo")
        
        # Parse timestamp - check multiple possible locations
        timestamp_str = (item.get("timestamp") or 
                        chat_event.get("timestamp") or
                        item.get("created_at"))
        if timestamp_str:
            try:
                if isinstance(timestamp_str, str):
                    # Handle ISO format with or without timezone
                    if timestamp_str.endswith("Z"):
                        timestamp_str = timestamp_str.replace("Z", "+00:00")
                    created_at = datetime.fromisoformat(timestamp_str)
                else:
                    created_at = timestamp_str
            except (ValueError, TypeError):
                created_at = datetime.utcnow()
        else:
            created_at = datetime.utcnow()
        
        # Ensure we have email - final fallback (but filter "N/A")
        if not email:
            # Try one more time from item directly
            email = _normalize_value(item.get("email"))
        
        return UserLogEventResponse(
            id=item.get("id", chat_event.get("id", "")),
            role=role,
            department=department,
            name=name,
            email=email,
            userEntraId=user_entra_id,
            profile_photo=profile_photo,
            eventType=event_type,
            eventPayload=event_payload,
            createdAt=created_at
        )
        
    except Exception as e:
        logger.error(f"Error transforming log item to user log event: {e}", exc_info=True)
        return None


@router.get("/logs", response_model=PaginatedResponse[UserLogEventResponse])
async def query_user_logs(
    date_from: Optional[datetime] = Query(None, description="Filter logs from this date (ISO format)"),
    date_to: Optional[datetime] = Query(None, description="Filter logs to this date (ISO format)"),
    correlation_id: Optional[str] = Query(None, description="Filter by correlation ID to track user activities"),
    event_type: Optional[str] = Query(None, description="Filter by event type (USER_CONVERSATION_CREATED, USER_MESSAGE_CREATED)"),
    search: Optional[str] = Query(None, description="Search across name, email, user_id, and log data fields"),
    page: Optional[int] = Query(None, ge=1, description="Page number (1-based)"),
    size: Optional[int] = Query(None, ge=1, le=1000, description="Number of logs per page"),
    # Backward compatibility
    limit: Optional[int] = Query(None, ge=1, le=1000, description="Number of logs to return (deprecated, use size)"),
    offset: Optional[int] = Query(None, ge=0, description="Number of logs to skip (deprecated, use page)"),
    sort_by: Optional[str] = Query(None, description="Sort by field: timestamp, createdAt, eventType, email, name"),
    sort_order: Optional[str] = Query("DESC", description="Sort order: ASC or DESC"),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[UserLogEventResponse]:
    """Query user logs in 3.0 format matching Figma design.
    
    Returns USER_CONVERSATION_CREATED and USER_MESSAGE_CREATED events
    with all data structured according to the 3.0 specification.
    
    Now uses dedicated user logs collection for fast queries.
    """
    try:
        # Handle backward compatibility for limit/offset
        if limit is not None:
            size = limit
        if offset is not None:
            # Calculate page from offset, but need size first
            if size is None:
                size = 30  # Default size if not provided
            page = (offset // size) + 1 if size else 1
        
        # Default values
        if size is None:
            size = 30
        if page is None:
            page = 1
        
        # Calculate offset from page
        offset = (page - 1) * size
        
        # Query from PostgreSQL user_logs table (much faster than Cosmos DB!)
        # Data is already in 3.0 format, just need to enrich with user info
        result = await postgres_logs_service.query_user_logs(
            db=db,
            limit=size,
            offset=offset,
            date_from=date_from,
            date_to=date_to,
            event_type=event_type,
            correlation_id=correlation_id,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        
        items = result.get("items", [])
        total = result.get("total", 0)
        
        logger.info(f"Retrieved {len(items)} user log items from dedicated collection (page: {page}, size: {size}, total: {total})")
        
        # Enrich items with user information from database
        # Collect user IDs and emails from items
        user_ids_to_fetch = set()
        emails_to_fetch = set()
        
        for item in items:
            user_id = item.get("userId")
            if user_id:
                try:
                    UUIDType(user_id)
                    user_ids_to_fetch.add(user_id)
                except (ValueError, TypeError):
                    pass
            
            email = item.get("email")
            if email and email != "N/A":
                emails_to_fetch.add(email)
        
        # Fetch user information
        users_map = {}
        try:
            # Fetch by user_id
            if user_ids_to_fetch:
                user_uuid_ids = []
                for user_id_str in user_ids_to_fetch:
                    try:
                        user_uuid_ids.append(UUIDType(user_id_str))
                    except (ValueError, TypeError):
                        pass
                
                if user_uuid_ids:
                    user_query = select(User).where(User.id.in_(user_uuid_ids))
                    user_result = await db.execute(user_query)
                    users = user_result.scalars().all()
                    
                    for user in users:
                        user_id_str = str(user.id)
                        full_name = user.full_name
                        if not full_name:
                            if user.azure_display_name:
                                full_name = user.azure_display_name
                            elif user.first_name or user.last_name:
                                full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
                        
                        # Get profile photo URL - check preferences first, then fallback to proxy endpoint
                        from aldar_middleware.utils.user_utils import get_profile_photo_url
                        profile_photo_url = get_profile_photo_url(user)
                        if not profile_photo_url and user.azure_ad_id:
                            profile_photo_url = f"{settings.api_prefix}/auth/users/{user.id}/profile-photo"
                        
                        users_map[user_id_str] = {
                            "full_name": full_name,
                            "email": user.email,
                            "department": user.azure_department,
                            "job_title": user.azure_job_title,
                            "company": user.company,
                            "azure_ad_id": user.azure_ad_id,
                            "azure_display_name": user.azure_display_name,
                            "is_admin": user.is_admin,
                            "profile_photo": profile_photo_url,
                        }
            
            # Fetch by email
            if emails_to_fetch:
                emails_to_query = [e for e in emails_to_fetch if e != "N/A"]
                if emails_to_query:
                    email_query = select(User).where(User.email.in_(emails_to_query))
                    email_result = await db.execute(email_query)
                    users_by_email_list = email_result.scalars().all()
                    
                    for user in users_by_email_list:
                        user_id_str = str(user.id)
                        if user_id_str not in users_map:
                            full_name = user.full_name
                            if not full_name:
                                if user.azure_display_name:
                                    full_name = user.azure_display_name
                                elif user.first_name or user.last_name:
                                    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
                            
                            # Get profile photo URL - check preferences first, then fallback to proxy endpoint
                            from aldar_middleware.utils.user_utils import get_profile_photo_url
                            profile_photo_url = get_profile_photo_url(user)
                            if not profile_photo_url and user.azure_ad_id:
                                profile_photo_url = f"{settings.api_prefix}/auth/users/{user.id}/profile-photo"
                            
                            users_map[user_id_str] = {
                                "full_name": full_name,
                                "email": user.email,
                                "department": user.azure_department,
                                "job_title": user.azure_job_title,
                                "company": user.company,
                                "azure_ad_id": user.azure_ad_id,
                                "azure_display_name": user.azure_display_name,
                                "is_admin": user.is_admin,
                                "profile_photo": profile_photo_url,
                            }
        except Exception as e:
            logger.error(f"Error fetching user information: {str(e)}", exc_info=True)
        
        # Enrich agentType and agentThumbnail for items where agent info is present
        # Collect agent IDs that need enrichment
        agent_ids_to_enrich = set()
        agent_enrichment_map = {}  # Cache for agent records
        
        for item in items:
            event_payload = item.get("eventPayload", {})
            agent = event_payload.get("agent", {})
            if isinstance(agent, dict):
                agent_id = agent.get("agentId")
                if agent_id:
                    # Enrich all agents (for thumbnail) and those missing agentType
                    agent_ids_to_enrich.add(agent_id)
        
        # Helper function to resolve agent thumbnail
        async def _resolve_agent_thumbnail(agent_icon: Optional[str]) -> Optional[str]:
            """Resolve agent icon to thumbnail URL.
            
            If agent_icon is a UUID (attachment ID), resolve it to blob URL.
            Otherwise, use it as-is (already a URL).
            """
            if not agent_icon:
                return None
            
            # Check if it's a UUID (attachment ID)
            try:
                icon_uuid = UUIDType(agent_icon)
                # It's a UUID, try to resolve to blob URL
                result = await db.execute(
                    select(Attachment).where(
                        Attachment.id == icon_uuid,
                        Attachment.is_active
                    )
                )
                attachment = result.scalar_one_or_none()
                if attachment and attachment.blob_url:
                    return attachment.blob_url
                else:
                    # UUID but no attachment found, return None
                    return None
            except (ValueError, TypeError):
                # Not a UUID, assume it's already a URL
                return agent_icon
        
        # Fetch agent records for enrichment
        if agent_ids_to_enrich:
            try:
                agent_uuid_ids = []
                for agent_id_str in agent_ids_to_enrich:
                    try:
                        agent_uuid_ids.append(UUIDType(agent_id_str))
                    except (ValueError, TypeError):
                        pass
                
                if agent_uuid_ids:
                    agent_query = select(Agent).where(Agent.public_id.in_(agent_uuid_ids))
                    agent_result = await db.execute(agent_query)
                    agents = agent_result.scalars().all()
                    
                    for agent in agents:
                        agent_id_str = str(agent.public_id)
                        agent_enrichment_map[agent_id_str] = agent
            except Exception as e:
                logger.warning(f"Error fetching agent records for enrichment: {str(e)}")
        
        # Convert items to UserLogEventResponse and enrich with user info and agent type
        enriched_items = []
        for item in items:
            # Enrich agentType and agentThumbnail if missing
            event_payload = item.get("eventPayload", {}).copy() if item.get("eventPayload") else {}
            agent = event_payload.get("agent", {})
            if isinstance(agent, dict):
                agent_id = agent.get("agentId")
                agent_type = agent.get("agentType")
                agent_thumbnail = agent.get("agentThumbnail")
                
                # Enrich agent data if we have agent record
                agent_record = agent_enrichment_map.get(agent_id) if agent_id else None
                if agent_record:
                    agent = agent.copy()
                    
                    # Enrich agentType if missing
                    if agent_type is None or agent_type == "null":
                        try:
                            enriched_agent_type = await determine_agent_type(agent_record, db)
                            if enriched_agent_type:
                                agent["agentType"] = enriched_agent_type
                        except Exception as e:
                            logger.warning(f"Error determining agent type for {agent_id}: {str(e)}")
                    
                    # Enrich agentThumbnail if missing
                    if not agent_thumbnail and agent_record.icon:
                        try:
                            resolved_thumbnail = await _resolve_agent_thumbnail(agent_record.icon)
                            if resolved_thumbnail:
                                agent["agentThumbnail"] = resolved_thumbnail
                        except Exception as e:
                            logger.warning(f"Error resolving agent thumbnail for {agent_id}: {str(e)}")
                    
                    event_payload["agent"] = agent
            
            # Get user info
            user_id = item.get("userId")
            user_info = None
            if user_id and user_id in users_map:
                user_info = users_map[user_id]
            elif item.get("email") and item.get("email") != "N/A":
                # Try to find by email
                for uid, info in users_map.items():
                    if info.get("email") == item.get("email"):
                        user_info = info
                        break
            
            # Build UserLogEventResponse
            # Parse createdAt from timestamp string
            created_at_str = item.get("createdAt") or item.get("timestamp")
            try:
                if isinstance(created_at_str, str):
                    # Try parsing ISO format
                    created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                else:
                    created_at = created_at_str
            except Exception:
                created_at = datetime.now()
            
            # Enrich with user info if available
            role = item.get("role")
            department = item.get("department")
            name = item.get("name")
            email = item.get("email")
            user_entra_id = item.get("userEntraId")
            profile_photo = item.get("profile_photo")
            
            if user_info:
                role = role or ("ADMIN" if user_info.get("is_admin") else "NORMAL")
                department = department or user_info.get("department")
                name = name or user_info.get("full_name")
                email = email or user_info.get("email")
                user_entra_id = user_entra_id or user_info.get("azure_ad_id")
                profile_photo = profile_photo or user_info.get("profile_photo")
            
            enriched_item = UserLogEventResponse(
                id=item.get("id", ""),
                role=role,
                department=department,
                name=name,
                email=email,
                userEntraId=user_entra_id,
                profile_photo=profile_photo,
                eventType=item.get("eventType", ""),
                eventPayload=event_payload,  # Use enriched eventPayload
                createdAt=created_at,
            )
            enriched_items.append(enriched_item)
        
        # Calculate pagination
        total_pages = (total + size - 1) // size if total > 0 and size > 0 else 1
        
        return PaginatedResponse(
            items=enriched_items,
            total=total,
            page=page,
            limit=size,
            total_pages=total_pages,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid query parameters: {e!s}",
        ) from e
    except Exception as e:
        logger.error(f"Failed to query user logs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to query logs. Please try again later.",
        ) from e


@router.get("/logs/export/csv")
async def export_user_logs_csv(
    date_from: Optional[datetime] = Query(None, description="Filter logs from this date (ISO format)"),
    date_to: Optional[datetime] = Query(None, description="Filter logs to this date (ISO format)"),
    correlation_id: Optional[str] = Query(None, description="Filter by correlation ID"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    search: Optional[str] = Query(None, description="Search across name, email, user_id, and log data fields"),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Export user logs as CSV (admin only)."""
    try:
        # Query all matching logs (no pagination for export)
        result = await postgres_logs_service.query_user_logs(
            db=db,
            limit=100000,  # Large limit for export
            offset=0,
            date_from=date_from,
            date_to=date_to,
            correlation_id=correlation_id,
            event_type=event_type,
            search=search,
            sort_by="timestamp",
            sort_order="DESC",
        )
        
        items = result.get("items", [])
        
        # Fetch user information for enrichment
        from aldar_middleware.models.user import User as UserModel
        user_ids_to_fetch = set()
        emails_to_fetch = set()
        for item in items:
            # user_id can be in userId or user_id field
            user_id = item.get("userId") or item.get("user_id")
            if user_id:
                try:
                    UUIDType(user_id)
                    user_ids_to_fetch.add(user_id)
                except (ValueError, TypeError):
                    pass
            
            # Also collect emails for lookup
            email = item.get("email")
            if email and email != "N/A":
                emails_to_fetch.add(email)
        
        users_map = {}
        if user_ids_to_fetch or emails_to_fetch:
            try:
                user_uuid_ids = []
                for user_id_str in user_ids_to_fetch:
                    try:
                        user_uuid_ids.append(UUIDType(user_id_str))
                    except (ValueError, TypeError):
                        pass
                
                # Build query to fetch users by ID or email
                user_query = select(UserModel)
                conditions = []
                if user_uuid_ids:
                    conditions.append(UserModel.id.in_(user_uuid_ids))
                if emails_to_fetch:
                    conditions.append(UserModel.email.in_(emails_to_fetch))
                
                if conditions:
                    from sqlalchemy import or_
                    user_query = user_query.where(or_(*conditions))
                    user_result = await db.execute(user_query)
                    users = user_result.scalars().all()
                    
                    for user in users:
                        full_name = user.full_name
                        if not full_name:
                            if user.azure_display_name:
                                full_name = user.azure_display_name
                            elif user.first_name or user.last_name:
                                full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
                        
                        profile_photo = None
                        if user.azure_ad_id:
                            profile_photo = f"{settings.api_prefix}/auth/users/{user.id}/profile-photo"
                        
                        user_info = {
                            "full_name": full_name,
                            "department": user.azure_department,
                            "role": "ADMIN" if user.is_admin else "NORMAL",
                            "azure_ad_id": user.azure_ad_id,
                            "email": user.email,
                            "profile_photo": profile_photo,
                        }
                        # Map by both ID and email for easier lookup
                        users_map[str(user.id)] = user_info
                        users_map[user.email] = user_info
            except Exception as e:
                logger.warning(f"Error fetching user information for logs: {str(e)}")
        
        # Create CSV with specific columns as requested
        csv_buffer = io.StringIO()
        csv_writer = csv.DictWriter(
            csv_buffer,
            fieldnames=[
                "ID",
                "Timestamp",
                "EventType",
                "Name",
                "Email",
                "Role",
                "Operation type",
                "User department",
                "Agent ID",
                "agentName",
                "agentType",
                "userInput",
                "conversationId",
                "messageId",
                "tokens_used",
                "processing_time_ms",
                "correlation_id",
                "log_data",
            ],
        )
        
        # Track if any tokens_used or processing_time_ms are present
        tokens_used_found = False
        processing_time_found = False
        csv_rows = []
        for item in items:
            # The item structure from query_user_logs has log_data fields at top level
            # plus id, timestamp, createdAt, eventType fields
            # log_data JSONB is already merged into the item dict
            
            # Get raw fields
            item_id = item.get("id") or item.get("_id") or ""
            created_at = item.get("createdAt") or item.get("timestamp") or ""
            event_type = item.get("eventType") or item.get("action_type") or ""

            # Get user_id - can be in userId or user_id
            user_id = item.get("userId") or item.get("user_id")
            email = item.get("email", "")

            # Get user info if available - try by user_id first, then by email
            user_info = {}
            if user_id and user_id in users_map:
                user_info = users_map[user_id]
            elif email and email in users_map:
                user_info = users_map[email]

            # Extract event payload - check multiple possible locations
            event_payload = (
                item.get("eventPayload", {}) or
                item.get("body", {}) or
                {}
            )

            # agent fields
            agent = event_payload.get("agent", {})
            agent_id = None
            agent_name = None
            agent_type = None
            if isinstance(agent, dict):
                agent_id = agent.get("agentId") or agent.get("agent_id") or agent.get("agent_public_id")
                agent_name = agent.get("agentName") or agent.get("agent_name")
                agent_type = agent.get("agentType") or agent.get("agent_type")
            else:
                # sometimes agent is present at top level
                agent_id = event_payload.get("agentId") or event_payload.get("agent_id")
                agent_name = event_payload.get("agentName") or event_payload.get("agent_name")
                agent_type = event_payload.get("agentType") or event_payload.get("agent_type")

            # userInput and conversationId
            user_input = (
                event_payload.get("userInput", "") or
                event_payload.get("user_input", "") or
                item.get("userInput", "") or
                ""
            )

            conversation_id = (
                event_payload.get("conversationId", "") or
                event_payload.get("conversation_id", "") or
                event_payload.get("sessionId", "") or
                item.get("conversationId", "") or
                item.get("conversation_id", "") or
                ""
            )

            # messageId if present
            message_id = event_payload.get("messageId") or event_payload.get("message_id") or item.get("messageId") or item.get("message_id") or ""

            # metrics: try multiple locations/fieldnames (eventPayload.metrics, top-level metrics, tokens_used, processing_time_ms, etc.)
            tokens_used = None
            processing_time = None

            # Common locations for metrics
            metrics_candidates = []
            if isinstance(event_payload, dict):
                metrics_candidates.append(event_payload.get("metrics"))
                metrics_candidates.append(event_payload.get("metrics_ms"))
                metrics_candidates.append(event_payload.get("token_usage"))
                metrics_candidates.append(event_payload.get("tokens"))

            # also check top-level item fields
            metrics_candidates.append(item.get("metrics"))
            metrics_candidates.append(item.get("log_data", {}).get("metrics") if isinstance(item.get("log_data"), dict) else None)

            # Walk candidates looking for tokenUsage/used or processingTime
            for m in metrics_candidates:
                if not m or not isinstance(m, dict):
                    continue
                # tokenUsage shape: {"used": <n>, ...}
                token_usage = m.get("tokenUsage") or m.get("token_usage") or m.get("tokens") or m.get("tokenCount")
                if isinstance(token_usage, dict):
                    if tokens_used is None:
                        tokens_used = token_usage.get("used") or token_usage.get("count") or token_usage.get("total")
                else:
                    # token usage might be a plain number on the metrics object
                    if tokens_used is None:
                        tokens_used = m.get("tokens_used") or m.get("tokens") or m.get("token_count")

                # processing time may be stored as processingTime (ms), processing_time_ms, or processingTimeMs
                if processing_time is None:
                    processing_time = m.get("processingTime") or m.get("processing_time_ms") or m.get("processingTimeMs") or m.get("processing_time")

                # break early if we found both
                if tokens_used is not None and processing_time is not None:
                    break

            # Fallback: check event_payload and item for direct fields
            if tokens_used is None:
                tokens_used = event_payload.get("tokens_used") if isinstance(event_payload, dict) else None
            if tokens_used is None:
                tokens_used = item.get("tokens_used") or item.get("token_usage") or item.get("tokens")

            if processing_time is None:
                processing_time = event_payload.get("processing_time_ms") if isinstance(event_payload, dict) else None
            if processing_time is None:
                processing_time = item.get("processing_time_ms") or item.get("processing_time") or item.get("processingTimeMs")

            # Normalize numeric values to strings for CSV output
            try:
                if tokens_used is not None:
                    tokens_used = int(tokens_used)
            except Exception:
                # leave as-is (string) if conversion fails
                pass

            try:
                if processing_time is not None:
                    processing_time = int(processing_time)
            except Exception:
                pass

            # Operation type is the action_type/eventType
            operation_type = event_type

            # Get user fields - prioritize user_info (from DB join), then item fields
            name = (
                user_info.get("full_name", "") or
                item.get("name", "") or
                item.get("full_name", "")
            )
            email = (
                item.get("email", "") or
                user_info.get("email", "")
            )
            role = (
                user_info.get("role", "") or
                item.get("role", "") or
                item.get("user_role", "")
            )
            department = (
                user_info.get("department", "") or
                item.get("department", "")
            )

            # Clean up text fields - remove newlines and extra spaces
            if user_input:
                user_input = str(user_input).replace("\n", " ").replace("\r", "").strip()

            # Prepare log_data as JSON string - look in multiple possible fields
            log_data = None
            if isinstance(item.get("log_data"), dict):
                log_data = item.get("log_data")
            elif isinstance(item.get("body"), dict):
                log_data = item.get("body")
            elif isinstance(item.get("data"), dict):
                log_data = item.get("data")
            elif isinstance(item.get("log"), dict):
                log_data = item.get("log")
            else:
                # last resort: use event_payload if it seems informative
                if isinstance(event_payload, dict) and event_payload:
                    log_data = event_payload
                else:
                    log_data = item.get("log_data") or item.get("body") or {}

            try:
                log_data_str = json.dumps(log_data) if log_data else ""
            except Exception:
                log_data_str = str(log_data)

            if tokens_used not in (None, ""):
                tokens_used_found = True
            if processing_time not in (None, ""):
                processing_time_found = True
            row = {
                "ID": item_id or "",
                "Timestamp": created_at if isinstance(created_at, str) else (created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at)),
                "EventType": operation_type or "",
                "Name": name or "",
                "Email": email or "",
                "Role": role or "",
                "Operation type": operation_type or "",
                "User department": department or "",
                "Agent ID": agent_id or "",
                "agentName": agent_name or "",
                "agentType": agent_type or "",
                "userInput": user_input or "",
                "conversationId": conversation_id or "",
                "messageId": message_id or "",
                "tokens_used": tokens_used if tokens_used is not None else "",
                "processing_time_ms": processing_time if processing_time is not None else "",
                "correlation_id": item.get("correlationId") or item.get("correlation_id") or item.get("correlation_id", "") or "",
                "log_data": log_data_str,
            }
            csv_rows.append(row)
        # Remove tokens_used and/or processing_time_ms columns if not found in any row
        fieldnames = [
            "ID",
            "Timestamp",
            "EventType",
            "Name",
            "Email",
            "Role",
            "Operation type",
            "User department",
            "Agent ID",
            "agentName",
            "agentType",
            "userInput",
            "conversationId",
            "messageId",
            "correlation_id",
            "log_data",
        ]
        if tokens_used_found:
            fieldnames.insert(fieldnames.index("correlation_id"), "tokens_used")
        if processing_time_found:
            fieldnames.insert(fieldnames.index("correlation_id"), "processing_time_ms")
        # Write header and rows with only the present columns
        csv_buffer = io.StringIO()
        csv_writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
        csv_writer.writeheader()
        for row in csv_rows:
            filtered_row = {k: v for k, v in row.items() if k in fieldnames}
            csv_writer.writerow(filtered_row)
        csv_data = csv_buffer.getvalue()
        
        logger.info(
            "User logs exported as CSV",
            extra={
                "user_id": str(current_user.id),
                "row_count": len(items),
            },
        )
        
        # Log admin action: ADMIN_USER_LOGS_EXPORTED
        try:
            request_correlation_id = get_correlation_id() or str(uuid.uuid4())
            admin_log_data = {
                "id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": "INFO",
                "action_type": "ADMIN_USER_LOGS_EXPORTED",
                "user_id": str(current_user.id),
                "email": current_user.email,
                "username": current_user.username or current_user.email,
                "correlation_id": request_correlation_id,
                "module": "user_logs",
                "function": "export_user_logs_csv",
                "message": f"User logs exported as CSV: {len(items)} rows",
                "log_data": {
                    "action_type": "ADMIN_USER_LOGS_EXPORTED",
                    "row_count": len(items),
                    "filters": {
                        "date_from": date_from.isoformat() if date_from else None,
                        "date_to": date_to.isoformat() if date_to else None,
                        "correlation_id": correlation_id,
                        "event_type": event_type,
                        "search": search,
                    }
                }
            }
            
            # Write to PostgreSQL synchronously using the same db session
            await postgres_logs_service.write_admin_log(db, admin_log_data)
        except Exception as e:
            logger.error(f"Failed to write admin log for user logs export: {e}", exc_info=True)
        
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=user_logs_export.csv"},
        )
    
    except Exception as e:
        logger.error(
            f"Failed to export user logs: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to export user logs",
        )
