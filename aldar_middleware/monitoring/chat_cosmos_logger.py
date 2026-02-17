"""Chat session logging to Cosmos DB for analytics and auditing."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from loguru import logger

from aldar_middleware.settings.context import get_correlation_id, get_user_context
from aldar_middleware.settings import settings
from aldar_middleware.services.user_logs_service import user_logs_service
from aldar_middleware.services.postgres_logs_service import postgres_logs_service


def log_chat_session_created(
    chat_id: str,
    session_id: str,
    user_id: str,
    username: str,
    title: str,
    agent_id: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    initial_message: Optional[str] = None,
    correlation_id: Optional[str] = None,
    # New fields for complete logging
    email: Optional[str] = None,
    role: Optional[str] = None,  # "ADMIN" or "NORMAL"
    department: Optional[str] = None,
    user_entra_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    agent_type: Optional[str] = None,
    agent_public_id: Optional[str] = None,
    selected_agent_type: Optional[str] = None,
    custom_query_about_user: Optional[str] = None,
    is_internet_search_used: Optional[bool] = None,
    is_sent_directly_to_openai: Optional[bool] = None,
    custom_query_topics_of_interest: Optional[str] = None,
    custom_query_preferred_formatting: Optional[str] = None,
) -> None:
    """Log chat session creation to Cosmos DB with all required fields.
    
    Args:
        chat_id: Chat UUID
        session_id: Chat session ID
        user_id: User UUID
        username: Username
        title: Chat title
        agent_id: Agent ID used for the chat
        attachments: List of attachment metadata
        initial_message: Initial message if provided
        correlation_id: Request correlation ID
        email: User email
        role: User role ("ADMIN" or "NORMAL")
        department: User department
        user_entra_id: User Azure AD ID
        agent_name: Agent name
        agent_type: Agent type (e.g., "Knowledge Agent")
        agent_public_id: Agent public ID (UUID)
        selected_agent_type: Selected agent type (e.g., "Creative", "PRECISE")
        custom_query_about_user: Custom query about user description
        is_internet_search_used: Whether internet search was used
        is_sent_directly_to_openai: Whether sent directly to OpenAI
        custom_query_topics_of_interest: Custom query topics of interest
        custom_query_preferred_formatting: Custom query preferred formatting
    """
    correlation_id = correlation_id or get_correlation_id() or str(uuid.uuid4())
    
    # Get user context for email if not provided
    user_ctx = get_user_context()
    if not email and user_ctx:
        email = user_ctx.email
    
    # Build agent object if agent info is available
    # IMPORTANT: Always create agent object if we have agent_id, even if agent_name is missing
    agent_object = None
    if agent_public_id or agent_id:
        agent_object = {
            "agentId": agent_public_id or agent_id,  # Always include agentId
            "agentName": agent_name,  # Can be None if not available
            "agentType": agent_type,  # Can be None if not available
        }
    
    log_entry = {
        "id": str(uuid.uuid4()),
        "type": "chat_session_created",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        
        # Chat info
        "chat_id": chat_id,
        "session_id": session_id,
        "conversationId": session_id,  # IMPORTANT: Use session_id, not chat_id, for USER_CONVERSATION_CREATED
        "title": title,
        "agent_id": agent_id,
        
        # User info - ALL FIELDS REQUIRED FOR USER LOGS
        "user_id": user_id,
        "username": username,
        "email": email or "N/A",
        "role": role or "NORMAL",  # "ADMIN" or "NORMAL"
        "department": department,
        "user_entra_id": user_entra_id,
        
        # Agent info - as object
        "agent": agent_object,
        
        # Event payload fields
        "selectedAgentType": selected_agent_type,
        "customQueryAboutUser": custom_query_about_user,
        "isInternetSearchUsed": is_internet_search_used,
        "isSentDirectlyToOpenAi": is_sent_directly_to_openai,
        "customQueryTopicsOfInterest": custom_query_topics_of_interest,
        "customQueryPreferredFormatting": custom_query_preferred_formatting,
        
        # Attachments
        "has_attachments": bool(attachments),
        "attachment_count": len(attachments) if attachments else 0,
        "attachments": attachments or [],
        
        # Initial message / User input
        "has_initial_message": bool(initial_message),
        "initial_message_length": len(initial_message) if initial_message else 0,
        "initial_message_preview": initial_message[:100] if initial_message else None,
        "initial_message": initial_message,  # Store full message for USER_CONVERSATION_CREATED
        "userInput": initial_message,  # Also store as userInput for consistency with USER_MESSAGE_CREATED
        
        # Metadata
        "event": "chat_session_created",
        "action": "CREATE_CHAT_SESSION",
    }
    
    # Log with user context binding
    bind_context = {
        "correlation_id": correlation_id,
        "user_id": user_id,
        "username": username,
        "chat_id": chat_id,
        "session_id": session_id,
    }
    
    if user_ctx:
        bind_context.update({
            "email": email or user_ctx.email or "N/A",
            "is_authenticated": user_ctx.is_authenticated,
        })
    
    logger.bind(**bind_context).info(
        f"CHAT_SESSION_CREATED: {title} (agent={agent_id})",
        extra={"chat_event": log_entry}
    )
    
    # Also write directly to user logs collection in 3.0 format for faster queries
    try:
        # Build event payload for USER_CONVERSATION_CREATED
        event_payload = {
            "conversationId": session_id,
            "sessionId": session_id,
        }
        if selected_agent_type:
            event_payload["selectedAgentType"] = selected_agent_type
        if custom_query_about_user:
            event_payload["customQueryAboutUser"] = custom_query_about_user
        if custom_query_topics_of_interest:
            event_payload["customQueryTopicsOfInterest"] = custom_query_topics_of_interest
        if custom_query_preferred_formatting:
            event_payload["customQueryPreferredFormatting"] = custom_query_preferred_formatting
        if agent_object:
            event_payload["agent"] = agent_object
        if initial_message:
            event_payload["userInput"] = initial_message
        
        # Build user log entry in 3.0 format
        user_log_entry = {
            "id": log_entry["id"],
            "timestamp": log_entry["timestamp"],
            "eventType": "USER_CONVERSATION_CREATED",
            "eventPayload": event_payload,
            "createdAt": log_entry["timestamp"],
            "userId": user_id,
            "email": email or "N/A",
            "role": role or "NORMAL",
            "department": department,
            "userEntraId": user_entra_id,
            "correlationId": correlation_id,
        }
        
        # Write to PostgreSQL user_logs table (async, don't wait)
        import asyncio
        from aldar_middleware.database.base import async_session
        
        async def write_to_postgres():
            try:
                async with async_session() as db:
                    await postgres_logs_service.write_user_log(db, user_log_entry)
            except Exception as e:
                logger.debug(f"Failed to write to PostgreSQL user_logs (non-blocking): {e}")
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is running, schedule it
                asyncio.create_task(write_to_postgres())
            else:
                # If no loop, run it
                loop.run_until_complete(write_to_postgres())
        except Exception as e:
            logger.debug(f"Failed to schedule PostgreSQL write (non-blocking): {e}")
    except Exception as e:
        logger.debug(f"Error preparing user log entry (non-blocking): {e}")


def log_chat_message(
    chat_id: str,
    message_id: str,
    message_type: str,
    role: str,
    content: str,
    user_id: str,
    username: str,
    tokens_used: Optional[int] = None,
    processing_time: Optional[int] = None,
    parent_message_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    # New fields for complete logging
    email: Optional[str] = None,
    role_user: Optional[str] = None,  # "ADMIN" or "NORMAL" (renamed to avoid conflict with message role)
    department: Optional[str] = None,
    user_entra_id: Optional[str] = None,
    conversation_id: Optional[str] = None,  # conversationId for USER_MESSAGE_CREATED
    selected_agent_type: Optional[str] = None,
    custom_query_about_user: Optional[str] = None,
    is_internet_search_used: Optional[bool] = None,
    is_sent_directly_to_openai: Optional[bool] = None,
    custom_query_topics_of_interest: Optional[str] = None,
    custom_query_preferred_formatting: Optional[str] = None,
    user_input: Optional[str] = None,  # The actual user message content
    agent_name: Optional[str] = None,
    agent_type: Optional[str] = None,
    agent_public_id: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,  # Attachments for USER_MESSAGE_CREATED_WITH_ATTACHMENT
) -> None:
    """Log chat message to Cosmos DB with all required fields.
    
    Args:
        chat_id: Chat UUID
        message_id: Message UUID
        message_type: Message type (user, assistant, system)
        role: Message role (user, assistant, system)
        content: Message content
        user_id: User UUID
        username: Username
        tokens_used: Tokens used for AI response
        processing_time: Processing time in milliseconds
        parent_message_id: Parent message UUID if reply
        correlation_id: Request correlation ID
        email: User email
        role_user: User role ("ADMIN" or "NORMAL")
        department: User department
        user_entra_id: User Azure AD ID
        conversation_id: Conversation ID (session_id)
        selected_agent_type: Selected agent type
        custom_query_about_user: Custom query about user description
        is_internet_search_used: Whether internet search was used
        is_sent_directly_to_openai: Whether sent directly to OpenAI
        custom_query_topics_of_interest: Custom query topics of interest
        custom_query_preferred_formatting: Custom query preferred formatting
        user_input: User message content (for USER_MESSAGE_CREATED)
        agent_name: Agent name
        agent_type: Agent type
        agent_public_id: Agent public ID (UUID)
    """
    correlation_id = correlation_id or get_correlation_id() or str(uuid.uuid4())
    
    # Determine if this is a user message or AI response
    is_ai_response = message_type == "assistant" or role == "assistant"
    
    safe_content = content or ""
    
    # Get user context for email if not provided
    user_ctx = get_user_context()
    if not email and user_ctx:
        email = user_ctx.email
    
    # Build agent object if agent info is available
    agent_object = None
    if agent_public_id or (not is_ai_response):  # Only for user messages
        agent_object = {
            "agentId": agent_public_id,
            "agentName": agent_name,
            "agentType": agent_type,
        }

    log_entry = {
        "id": str(uuid.uuid4()),
        "type": "chat_message",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        
        # Message info
        "chat_id": chat_id,
        "message_id": message_id,
        "conversationId": conversation_id or chat_id,  # For USER_MESSAGE_CREATED mapping
        "message_type": message_type,
        "role": role,
        "content_length": len(safe_content),
        "content_preview": safe_content[:200] if safe_content else None,
        "content": content if content is not None and settings.cosmos_logging_save_request_response else None,
        "userInput": user_input or (safe_content if not is_ai_response else None),  # User input for USER_MESSAGE_CREATED
        
        # User info - ALL FIELDS REQUIRED FOR USER LOGS
        "user_id": user_id,
        "username": username,
        "email": email or "N/A",
        "user_role": role_user or "NORMAL",  # "ADMIN" or "NORMAL" (renamed to avoid conflict with message role)
        "department": department,
        "user_entra_id": user_entra_id,
        
        # Agent info - as object
        "agent": agent_object,
        
        # Event payload fields for USER_MESSAGE_CREATED
        "selectedAgentType": selected_agent_type,
        "customQueryAboutUser": custom_query_about_user,
        "isInternetSearchUsed": is_internet_search_used,
        "isSentDirectlyToOpenAi": is_sent_directly_to_openai,
        "customQueryTopicsOfInterest": custom_query_topics_of_interest,
        "customQueryPreferredFormatting": custom_query_preferred_formatting,
        
        # AI response metrics (if applicable)
        "is_ai_response": is_ai_response,
        "tokens_used": tokens_used,
        "processing_time_ms": processing_time,
        
        # Thread info
        "parent_message_id": parent_message_id,
        
        # Metadata
        "event": "chat_message_sent" if not is_ai_response else "ai_response_generated",
        "action": "SEND_MESSAGE" if not is_ai_response else "AI_RESPONSE",
    }
    
    # Log with user context binding
    bind_context = {
        "correlation_id": correlation_id,
        "user_id": user_id,
        "username": username,
        "chat_id": chat_id,
        "message_id": message_id,
        "message_type": message_type,
    }
    
    if user_ctx:
        bind_context.update({
            "email": email or user_ctx.email or "N/A",
            "is_authenticated": user_ctx.is_authenticated,
        })
    
    # Include AI metrics if available
    if is_ai_response and tokens_used:
        bind_context["tokens_used"] = tokens_used
    if is_ai_response and processing_time:
        bind_context["processing_time_ms"] = processing_time
    
    log_message = (
        f"CHAT_MESSAGE: {message_type} message in chat {chat_id[:8]}... "
        f"({len(safe_content)} chars)"
    )
    if is_ai_response and tokens_used:
        log_message += f" [tokens={tokens_used}]"
    if is_ai_response and processing_time:
        log_message += f" [time={processing_time}ms]"
    
    logger.bind(**bind_context).info(
        log_message,
        extra={"chat_event": log_entry}
    )
    
    # Also write user messages directly to user logs collection in 3.0 format for faster queries
    # Only write user messages, not AI responses
    if not is_ai_response:
        try:
            # Build event payload for USER_MESSAGE_CREATED
            event_payload = {}
            if message_id:
                event_payload["messageId"] = message_id
            if conversation_id or chat_id:
                event_payload["conversationId"] = conversation_id or chat_id
            if user_input:
                event_payload["userInput"] = user_input
            if selected_agent_type:
                event_payload["selectedAgentType"] = selected_agent_type
            if custom_query_about_user:
                event_payload["customQueryAboutUser"] = custom_query_about_user
            if custom_query_topics_of_interest:
                event_payload["customQueryTopicsOfInterest"] = custom_query_topics_of_interest
            if custom_query_preferred_formatting:
                event_payload["customQueryPreferredFormatting"] = custom_query_preferred_formatting
            if agent_object:
                event_payload["agent"] = agent_object
            if tokens_used or processing_time:
                event_payload["metrics"] = {}
                if tokens_used:
                    event_payload["metrics"]["tokensUsed"] = tokens_used
                if processing_time:
                    event_payload["metrics"]["processingTimeMs"] = processing_time
            
            # Determine event type based on attachments
            event_type = "USER_MESSAGE_CREATED_WITH_ATTACHMENT" if attachments and len(attachments) > 0 else "USER_MESSAGE_CREATED"
            
            # Add attachments to event payload if present
            if attachments and len(attachments) > 0:
                event_payload["attachments"] = attachments
            
            # Build user log entry in 3.0 format
            user_log_entry = {
                "id": log_entry["id"],
                "timestamp": log_entry["timestamp"],
                "eventType": event_type,
                "eventPayload": event_payload,
                "createdAt": log_entry["timestamp"],
                "userId": user_id,
                "email": email or "N/A",
                "role": role_user or "NORMAL",
                "department": department,
                "userEntraId": user_entra_id,
                "correlationId": correlation_id,
            }
            
            # Write to PostgreSQL user_logs table (async, don't wait)
            import asyncio
            from aldar_middleware.database.base import async_session
            
            async def write_to_postgres():
                try:
                    async with async_session() as db:
                        await postgres_logs_service.write_user_log(db, user_log_entry)
                except Exception as e:
                    logger.debug(f"Failed to write to PostgreSQL user_logs (non-blocking): {e}")
            
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If loop is running, schedule it
                    asyncio.create_task(write_to_postgres())
                else:
                    # If no loop, run it
                    loop.run_until_complete(write_to_postgres())
            except Exception as e:
                logger.debug(f"Failed to schedule PostgreSQL write (non-blocking): {e}")
        except Exception as e:
            logger.debug(f"Error preparing user log entry (non-blocking): {e}")


def log_chat_session_updated(
    chat_id: str,
    session_id: str,
    user_id: str,
    username: str,
    updates: Dict[str, Any],
    correlation_id: Optional[str] = None,
) -> None:
    """Log chat session update to Cosmos DB.
    
    Args:
        chat_id: Chat UUID
        session_id: Chat session ID
        user_id: User UUID
        username: Username
        updates: Dictionary of updated fields
        correlation_id: Request correlation ID
    """
    correlation_id = correlation_id or get_correlation_id() or str(uuid.uuid4())
    
    log_entry = {
        "id": str(uuid.uuid4()),
        "type": "chat_session_updated",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        
        # Chat info
        "chat_id": chat_id,
        "session_id": session_id,
        
        # User info
        "user_id": user_id,
        "username": username,
        
        # Update details
        "updates": updates,
        "updated_fields": list(updates.keys()),
        
        # Metadata
        "event": "chat_session_updated",
        "action": "UPDATE_CHAT_SESSION",
    }
    
    # Log with user context binding
    user_ctx = get_user_context()
    bind_context = {
        "correlation_id": correlation_id,
        "user_id": user_id,
        "username": username,
        "chat_id": chat_id,
        "session_id": session_id,
    }
    
    if user_ctx:
        bind_context.update({
            "email": user_ctx.email or "N/A",
            "is_authenticated": user_ctx.is_authenticated,
        })
    
    logger.bind(**bind_context).info(
        f"CHAT_SESSION_UPDATED: {', '.join(updates.keys())}",
        extra={"chat_event": log_entry}
    )


def log_chat_session_deleted(
    chat_id: str,
    session_id: str,
    user_id: str,
    username: str,
    message_count: int = 0,
    correlation_id: Optional[str] = None,
    # New fields for complete logging
    email: Optional[str] = None,
    role: Optional[str] = None,  # "ADMIN" or "NORMAL"
    department: Optional[str] = None,
    user_entra_id: Optional[str] = None,
) -> None:
    """Log chat session deletion to Cosmos DB.
    
    Args:
        chat_id: Chat UUID
        session_id: Chat session ID
        user_id: User UUID
        username: Username
        message_count: Number of messages in the deleted chat
        correlation_id: Request correlation ID
        email: User email
        role: User role ("ADMIN" or "NORMAL")
        department: User department
        user_entra_id: User Azure AD ID
    """
    correlation_id = correlation_id or get_correlation_id() or str(uuid.uuid4())
    
    # Get user context for email if not provided
    user_ctx = get_user_context()
    if not email and user_ctx:
        email = user_ctx.email
    
    log_entry = {
        "id": str(uuid.uuid4()),
        "type": "chat_session_deleted",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        
        # Chat info
        "chat_id": chat_id,
        "session_id": session_id,
        "conversationId": session_id,  # For USER_CONVERSATION_DELETED mapping
        "message_count": message_count,
        
        # User info - ALL FIELDS REQUIRED FOR USER LOGS
        "user_id": user_id,
        "username": username,
        "email": email or "N/A",
        "role": role or "NORMAL",  # "ADMIN" or "NORMAL"
        "department": department,
        "user_entra_id": user_entra_id,
        
        # Metadata
        "event": "chat_session_deleted",
        "action": "DELETE_CHAT_SESSION",
    }
    
    # Log with user context binding
    bind_context = {
        "correlation_id": correlation_id,
        "user_id": user_id,
        "username": username,
        "chat_id": chat_id,
        "session_id": session_id,
    }
    
    if user_ctx:
        bind_context.update({
            "email": email or user_ctx.email or "N/A",
            "is_authenticated": user_ctx.is_authenticated,
        })
    
    logger.bind(**bind_context).info(
        f"CHAT_SESSION_DELETED: {session_id} ({message_count} messages)",
        extra={"chat_event": log_entry}
    )
    
    # Also write directly to PostgreSQL user_logs table in 3.0 format for faster queries
    try:
        # Build event payload for USER_CONVERSATION_DELETED
        event_payload = {
            "conversationId": session_id,
        }
        
        # Build user log entry in 3.0 format
        user_log_entry = {
            "id": log_entry["id"],
            "timestamp": log_entry["timestamp"],
            "eventType": "USER_CONVERSATION_DELETED",
            "eventPayload": event_payload,
            "createdAt": log_entry["timestamp"],
            "userId": user_id,
            "email": email or "N/A",
            "role": role or "NORMAL",
            "department": department,
            "userEntraId": user_entra_id,
            "correlationId": correlation_id,
        }
        
        # Write to PostgreSQL user_logs table (async, don't wait)
        import asyncio
        from aldar_middleware.database.base import async_session
        
        async def write_to_postgres():
            try:
                async with async_session() as db:
                    await postgres_logs_service.write_user_log(db, user_log_entry)
            except Exception as e:
                logger.debug(f"Failed to write to PostgreSQL user_logs (non-blocking): {e}")
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is running, schedule it
                asyncio.create_task(write_to_postgres())
            else:
                # If no loop, run it
                loop.run_until_complete(write_to_postgres())
        except Exception as e:
            logger.debug(f"Failed to schedule PostgreSQL write (non-blocking): {e}")
    except Exception as e:
        logger.debug(f"Error preparing user log entry (non-blocking): {e}")


def log_chat_favorite_toggled(
    chat_id: str,
    session_id: str,
    user_id: str,
    username: str,
    is_favorite: bool,
    correlation_id: Optional[str] = None,
    email: Optional[str] = None,
    role: Optional[str] = None,
    department: Optional[str] = None,
    user_entra_id: Optional[str] = None,
) -> None:
    """Log chat favorite status change to Cosmos DB.
    
    Args:
        chat_id: Chat UUID
        session_id: Chat session ID
        user_id: User UUID
        username: Username
        is_favorite: New favorite status
        correlation_id: Request correlation ID
        email: User email
        role: User role ("ADMIN" or "NORMAL")
        department: User department
        user_entra_id: User Azure AD ID
    """
    correlation_id = correlation_id or get_correlation_id() or str(uuid.uuid4())
    
    user_ctx = get_user_context()
    if not email and user_ctx:
        email = user_ctx.email
    
    log_entry = {
        "id": str(uuid.uuid4()),
        "type": "chat_favorite_toggled",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        
        # Chat info
        "chat_id": chat_id,
        "session_id": session_id,
        "conversationId": chat_id,
        "is_favorite": is_favorite,
        
        # User info
        "user_id": user_id,
        "username": username,
        "email": email or "N/A",
        "role": role or "NORMAL",
        "department": department,
        "user_entra_id": user_entra_id,
        
        # Metadata
        "event": "chat_favorite_toggled",
        "action": "TOGGLE_CHAT_FAVORITE",
    }
    
    # Log with user context binding
    bind_context = {
        "correlation_id": correlation_id,
        "user_id": user_id,
        "username": username,
        "chat_id": chat_id,
        "session_id": session_id,
    }
    
    if user_ctx:
        bind_context.update({
            "email": email or user_ctx.email or "N/A",
            "is_authenticated": user_ctx.is_authenticated,
        })
    
    action = "favorited" if is_favorite else "unfavorited"
    logger.bind(**bind_context).info(
        f"CHAT_FAVORITE_TOGGLED: Chat {action}",
        extra={"chat_event": log_entry}
    )
    
    # Also write directly to PostgreSQL user_logs table in 3.0 format for faster queries
    try:
        # Determine event type based on favorite status
        event_type = "USER_CONVERSATION_FAVORITED" if is_favorite else "USER_CONVERSATION_UNFAVORITED"
        
        # Build event payload
        event_payload = {
            "conversationId": chat_id or session_id,
            "isFavorite": is_favorite,
        }
        
        # Build user log entry in 3.0 format
        user_log_entry = {
            "id": log_entry["id"],
            "timestamp": log_entry["timestamp"],
            "eventType": event_type,
            "eventPayload": event_payload,
            "createdAt": log_entry["timestamp"],
            "userId": user_id,
            "email": email or "N/A",
            "role": role or "NORMAL",
            "department": department,
            "userEntraId": user_entra_id,
            "correlationId": correlation_id,
        }
        
        # Write to PostgreSQL user_logs table (async, don't wait)
        import asyncio
        from aldar_middleware.database.base import async_session
        
        async def write_to_postgres():
            try:
                async with async_session() as db:
                    await postgres_logs_service.write_user_log(db, user_log_entry)
            except Exception as e:
                logger.debug(f"Failed to write to PostgreSQL user_logs (non-blocking): {e}")
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is running, schedule it
                asyncio.create_task(write_to_postgres())
            else:
                # If no loop, run it
                loop.run_until_complete(write_to_postgres())
        except Exception as e:
            logger.debug(f"Failed to schedule PostgreSQL write (non-blocking): {e}")
    except Exception as e:
        logger.debug(f"Error preparing user log entry (non-blocking): {e}")


def log_chat_analytics_event(
    event_type: str,
    chat_id: Optional[str] = None,
    user_id: Optional[str] = None,
    metrics: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
) -> None:
    """Log chat analytics event to Cosmos DB.
    
    Args:
        event_type: Analytics event type (session_duration, message_count, etc.)
        chat_id: Chat UUID
        user_id: User UUID
        metrics: Event metrics
        correlation_id: Request correlation ID
    """
    correlation_id = correlation_id or get_correlation_id() or str(uuid.uuid4())
    
    log_entry = {
        "id": str(uuid.uuid4()),
        "type": "chat_analytics",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        
        # Event info
        "event_type": event_type,
        "chat_id": chat_id,
        "user_id": user_id,
        
        # Metrics
        "metrics": metrics or {},
        
        # Metadata
        "event": f"analytics_{event_type}",
        "action": f"ANALYTICS_{event_type.upper()}",
    }
    
    # Log with user context binding
    user_ctx = get_user_context()
    bind_context = {
        "correlation_id": correlation_id,
        "event_type": event_type,
    }
    
    if user_id:
        bind_context["user_id"] = user_id
    if chat_id:
        bind_context["chat_id"] = chat_id
    
    if user_ctx:
        bind_context.update({
            "username": user_ctx.username or "N/A",
            "email": user_ctx.email or "N/A",
            "is_authenticated": user_ctx.is_authenticated,
        })
    
    logger.bind(**bind_context).info(
        f"CHAT_ANALYTICS: {event_type}",
        extra={"chat_event": log_entry}
    )


def log_conversation_renamed(
    chat_id: str,
    session_id: str,
    user_id: str,
    username: str,
    old_title: str,
    new_title: str,
    correlation_id: Optional[str] = None,
    email: Optional[str] = None,
    role: Optional[str] = None,
    department: Optional[str] = None,
    user_entra_id: Optional[str] = None,
) -> None:
    """Log conversation rename to Cosmos DB.
    
    Args:
        chat_id: Chat UUID
        session_id: Chat session ID
        user_id: User UUID
        username: Username
        old_title: Previous title
        new_title: New title
        correlation_id: Request correlation ID
        email: User email
        role: User role ("ADMIN" or "NORMAL")
        department: User department
        user_entra_id: User Azure AD ID
    """
    correlation_id = correlation_id or get_correlation_id() or str(uuid.uuid4())
    
    user_ctx = get_user_context()
    if not email and user_ctx:
        email = user_ctx.email
    
    log_entry = {
        "id": str(uuid.uuid4()),
        "type": "conversation_renamed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        
        # Chat info
        "chat_id": chat_id,
        "session_id": session_id,
        "conversationId": chat_id,
        "old_title": old_title,
        "new_title": new_title,
        
        # User info
        "user_id": user_id,
        "username": username,
        "email": email or "N/A",
        "role": role or "NORMAL",
        "department": department,
        "user_entra_id": user_entra_id,
        
        # Metadata
        "event": "conversation_renamed",
        "action": "RENAME_CONVERSATION",
    }
    
    user_ctx = get_user_context()
    bind_context = {
        "correlation_id": correlation_id,
        "user_id": user_id,
        "username": username,
        "chat_id": chat_id,
        "session_id": session_id,
    }
    
    if user_ctx:
        bind_context.update({
            "email": email or user_ctx.email or "N/A",
            "is_authenticated": user_ctx.is_authenticated,
        })
    
    logger.bind(**bind_context).info(
        f"CONVERSATION_RENAMED: '{old_title}' -> '{new_title}'",
        extra={"chat_event": log_entry}
    )
    
    # Also write directly to PostgreSQL user_logs table in 3.0 format for faster queries
    try:
        # Build event payload for USER_CONVERSATION_RENAMED
        event_payload = {
            "conversationId": chat_id or session_id,
            "oldTitle": old_title,
            "newTitle": new_title,
        }
        
        # Build user log entry in 3.0 format
        user_log_entry = {
            "id": log_entry["id"],
            "timestamp": log_entry["timestamp"],
            "eventType": "USER_CONVERSATION_RENAMED",
            "eventPayload": event_payload,
            "createdAt": log_entry["timestamp"],
            "userId": user_id,
            "email": email or "N/A",
            "role": role or "NORMAL",
            "department": department,
            "userEntraId": user_entra_id,
            "correlationId": correlation_id,
        }
        
        # Write to PostgreSQL user_logs table (async, don't wait)
        import asyncio
        from aldar_middleware.database.base import async_session
        
        async def write_to_postgres():
            try:
                async with async_session() as db:
                    await postgres_logs_service.write_user_log(db, user_log_entry)
            except Exception as e:
                logger.debug(f"Failed to write to PostgreSQL user_logs (non-blocking): {e}")
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(write_to_postgres())
            else:
                loop.run_until_complete(write_to_postgres())
        except Exception as e:
            logger.debug(f"Failed to schedule PostgreSQL write (non-blocking): {e}")
    except Exception as e:
        logger.debug(f"Error preparing user log entry (non-blocking): {e}")


def log_starting_prompt_chosen(
    chat_id: str,
    session_id: str,
    user_id: str,
    username: str,
    prompt_id: Optional[str] = None,
    prompt_text: Optional[str] = None,
    correlation_id: Optional[str] = None,
    email: Optional[str] = None,
    role: Optional[str] = None,
    department: Optional[str] = None,
    user_entra_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    agent_type: Optional[str] = None,
) -> None:
    """Log starting prompt chosen to Cosmos DB.
    
    Args:
        chat_id: Chat UUID
        session_id: Chat session ID
        user_id: User UUID
        username: Username
        prompt_id: Prompt ID if available
        prompt_text: Prompt text content
        correlation_id: Request correlation ID
        email: User email
        role: User role ("ADMIN" or "NORMAL")
        department: User department
        user_entra_id: User Azure AD ID
        agent_id: Agent ID
        agent_name: Agent name
        agent_type: Agent type
    """
    correlation_id = correlation_id or get_correlation_id() or str(uuid.uuid4())
    
    user_ctx = get_user_context()
    if not email and user_ctx:
        email = user_ctx.email
    
    agent_object = None
    if agent_id:
        agent_object = {
            "agentId": agent_id,
            "agentName": agent_name,
            "agentType": agent_type,
        }
    
    log_entry = {
        "id": str(uuid.uuid4()),
        "type": "starting_prompt_chosen",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        
        # Chat info
        "chat_id": chat_id,
        "session_id": session_id,
        "conversationId": chat_id,
        
        # User info
        "user_id": user_id,
        "username": username,
        "email": email or "N/A",
        "role": role or "NORMAL",
        "department": department,
        "user_entra_id": user_entra_id,
        
        # Prompt info
        "prompt_id": prompt_id,
        "prompt_text": prompt_text,
        
        # Agent info
        "agent": agent_object,
        
        # Metadata
        "event": "starting_prompt_chosen",
        "action": "CHOOSE_STARTING_PROMPT",
    }
    
    bind_context = {
        "correlation_id": correlation_id,
        "user_id": user_id,
        "username": username,
        "chat_id": chat_id,
        "session_id": session_id,
    }
    
    if user_ctx:
        bind_context.update({
            "email": email or user_ctx.email or "N/A",
            "is_authenticated": user_ctx.is_authenticated,
        })
    
    logger.bind(**bind_context).info(
        f"STARTING_PROMPT_CHOSEN: prompt_id={prompt_id}",
        extra={"chat_event": log_entry}
    )
    
    # Also write directly to PostgreSQL user_logs table in 3.0 format for faster queries
    try:
        # Build event payload for USER_CONVERSATION_STARTING_PROMPT_CHOSEN
        event_payload = {
            "conversationId": chat_id or session_id,
        }
        if prompt_id:
            event_payload["promptId"] = prompt_id
        if prompt_text:
            event_payload["promptText"] = prompt_text
        if agent_object:
            event_payload["agent"] = agent_object
        
        # Build user log entry in 3.0 format
        user_log_entry = {
            "id": log_entry["id"],
            "timestamp": log_entry["timestamp"],
            "eventType": "USER_CONVERSATION_STARTING_PROMPT_CHOSEN",
            "eventPayload": event_payload,
            "createdAt": log_entry["timestamp"],
            "userId": user_id,
            "email": email or "N/A",
            "role": role or "NORMAL",
            "department": department,
            "userEntraId": user_entra_id,
            "correlationId": correlation_id,
        }
        
        # Write to PostgreSQL user_logs table (async, don't wait)
        import asyncio
        from aldar_middleware.database.base import async_session
        
        async def write_to_postgres():
            try:
                async with async_session() as db:
                    await postgres_logs_service.write_user_log(db, user_log_entry)
            except Exception as e:
                logger.debug(f"Failed to write to PostgreSQL user_logs (non-blocking): {e}")
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(write_to_postgres())
            else:
                loop.run_until_complete(write_to_postgres())
        except Exception as e:
            logger.debug(f"Failed to schedule PostgreSQL write (non-blocking): {e}")
    except Exception as e:
        logger.debug(f"Error preparing user log entry (non-blocking): {e}")


def log_my_agent_knowledge_sources_updated(
    user_id: str,
    username: str,
    agent_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    knowledge_sources: Optional[List[Dict[str, Any]]] = None,
    correlation_id: Optional[str] = None,
    email: Optional[str] = None,
    role: Optional[str] = None,
    department: Optional[str] = None,
    user_entra_id: Optional[str] = None,
) -> None:
    """Log my agent knowledge sources updated to Cosmos DB.
    
    Args:
        user_id: User UUID
        username: Username
        agent_id: Agent ID
        agent_name: Agent name
        knowledge_sources: List of knowledge sources
        correlation_id: Request correlation ID
        email: User email
        role: User role ("ADMIN" or "NORMAL")
        department: User department
        user_entra_id: User Azure AD ID
    """
    correlation_id = correlation_id or get_correlation_id() or str(uuid.uuid4())
    
    user_ctx = get_user_context()
    if not email and user_ctx:
        email = user_ctx.email
    
    agent_object = None
    if agent_id:
        agent_object = {
            "agentId": agent_id,
            "agentName": agent_name,
            "agentType": None,
        }
    
    log_entry = {
        "id": str(uuid.uuid4()),
        "type": "my_agent_knowledge_sources_updated",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        
        # User info
        "user_id": user_id,
        "username": username,
        "email": email or "N/A",
        "role": role or "NORMAL",
        "department": department,
        "user_entra_id": user_entra_id,
        
        # Agent info
        "agent": agent_object,
        
        # Knowledge sources
        "knowledge_sources": knowledge_sources or [],
        "knowledge_sources_count": len(knowledge_sources) if knowledge_sources else 0,
        
        # Metadata
        "event": "my_agent_knowledge_sources_updated",
        "action": "UPDATE_MY_AGENT_KNOWLEDGE_SOURCES",
    }
    
    bind_context = {
        "correlation_id": correlation_id,
        "user_id": user_id,
        "username": username,
    }
    
    if user_ctx:
        bind_context.update({
            "email": email or user_ctx.email or "N/A",
            "is_authenticated": user_ctx.is_authenticated,
        })
    
    logger.bind(**bind_context).info(
        f"MY_AGENT_KNOWLEDGE_SOURCES_UPDATED: agent={agent_id}, sources={len(knowledge_sources) if knowledge_sources else 0}",
        extra={"chat_event": log_entry}
    )


def log_message_regenerated(
    chat_id: str,
    session_id: str,
    message_id: str,
    user_id: str,
    username: str,
    original_message_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    email: Optional[str] = None,
    role: Optional[str] = None,
    department: Optional[str] = None,
    user_entra_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> None:
    """Log message regenerated to Cosmos DB.
    
    Args:
        chat_id: Chat UUID
        session_id: Chat session ID
        message_id: New regenerated message ID
        user_id: User UUID
        username: Username
        original_message_id: Original message ID that was regenerated
        correlation_id: Request correlation ID
        email: User email
        role: User role ("ADMIN" or "NORMAL")
        department: User department
        user_entra_id: User Azure AD ID
        conversation_id: Conversation ID
    """
    correlation_id = correlation_id or get_correlation_id() or str(uuid.uuid4())
    
    user_ctx = get_user_context()
    if not email and user_ctx:
        email = user_ctx.email
    
    log_entry = {
        "id": str(uuid.uuid4()),
        "type": "message_regenerated",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        
        # Chat info
        "chat_id": chat_id,
        "session_id": session_id,
        "conversationId": conversation_id or chat_id,
        "message_id": message_id,
        "original_message_id": original_message_id,
        
        # User info
        "user_id": user_id,
        "username": username,
        "email": email or "N/A",
        "role": role or "NORMAL",
        "department": department,
        "user_entra_id": user_entra_id,
        
        # Metadata
        "event": "message_regenerated",
        "action": "REGENERATE_MESSAGE",
    }
    
    bind_context = {
        "correlation_id": correlation_id,
        "user_id": user_id,
        "username": username,
        "chat_id": chat_id,
        "session_id": session_id,
        "message_id": message_id,
    }
    
    if user_ctx:
        bind_context.update({
            "email": email or user_ctx.email or "N/A",
            "is_authenticated": user_ctx.is_authenticated,
        })
    
    logger.bind(**bind_context).info(
        f"MESSAGE_REGENERATED: message_id={message_id}, original={original_message_id}",
        extra={"chat_event": log_entry}
    )


def log_user_created(
    user_id: str,
    email: str,
    username: Optional[str] = None,
    correlation_id: Optional[str] = None,
    role: Optional[str] = None,
    department: Optional[str] = None,
    user_entra_id: Optional[str] = None,
) -> None:
    """Log user created to Cosmos DB.
    
    Args:
        user_id: User UUID
        email: User email
        username: Username
        correlation_id: Request correlation ID
        role: User role ("ADMIN" or "NORMAL")
        department: User department
        user_entra_id: User Azure AD ID
    """
    correlation_id = correlation_id or get_correlation_id() or str(uuid.uuid4())
    
    log_entry = {
        "id": str(uuid.uuid4()),
        "type": "user_created",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        
        # User info
        "user_id": user_id,
        "email": email,
        "username": username,
        "role": role or "NORMAL",
        "department": department,
        "user_entra_id": user_entra_id,
        
        # Metadata
        "event": "user_created",
        "action": "CREATE_USER",
    }
    
    bind_context = {
        "correlation_id": correlation_id,
        "user_id": user_id,
        "email": email,
        "username": username or email,
    }
    
    logger.bind(**bind_context).info(
        f"USER_CREATED: email={email}, user_id={user_id}",
        extra={"chat_event": log_entry}
    )


def log_conversation_download(
    chat_id: str,
    session_id: str,
    user_id: str,
    username: str,
    format: str = "json",  # json, pdf
    correlation_id: Optional[str] = None,
    email: Optional[str] = None,
    role: Optional[str] = None,
    department: Optional[str] = None,
    user_entra_id: Optional[str] = None,
) -> None:
    """Log conversation download to Cosmos DB.
    
    Args:
        chat_id: Chat UUID
        session_id: Chat session ID
        user_id: User UUID
        username: Username
        format: Download format (json, pdf)
        correlation_id: Request correlation ID
        email: User email
        role: User role ("ADMIN" or "NORMAL")
        department: User department
        user_entra_id: User Azure AD ID
    """
    correlation_id = correlation_id or get_correlation_id() or str(uuid.uuid4())
    
    user_ctx = get_user_context()
    if not email and user_ctx:
        email = user_ctx.email
    
    log_entry = {
        "id": str(uuid.uuid4()),
        "type": "conversation_download",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        
        # Chat info
        "chat_id": chat_id,
        "session_id": session_id,
        "conversationId": chat_id,
        "format": format,
        
        # User info
        "user_id": user_id,
        "username": username,
        "email": email or "N/A",
        "role": role or "NORMAL",
        "department": department,
        "user_entra_id": user_entra_id,
        
        # Metadata
        "event": "conversation_download",
        "action": "DOWNLOAD_CONVERSATION",
    }
    
    bind_context = {
        "correlation_id": correlation_id,
        "user_id": user_id,
        "username": username,
        "chat_id": chat_id,
        "session_id": session_id,
    }
    
    if user_ctx:
        bind_context.update({
            "email": email or user_ctx.email or "N/A",
            "is_authenticated": user_ctx.is_authenticated,
        })
    
    logger.bind(**bind_context).info(
        f"CONVERSATION_DOWNLOAD: format={format}, session_id={session_id}",
        extra={"chat_event": log_entry}
    )
    
    # Also write directly to PostgreSQL user_logs table in 3.0 format for faster queries
    try:
        # Build event payload for USER_CONVERSATION_DOWNLOAD
        event_payload = {
            "conversationId": chat_id or session_id,
            "format": format,
        }
        
        # Build user log entry in 3.0 format
        user_log_entry = {
            "id": log_entry["id"],
            "timestamp": log_entry["timestamp"],
            "eventType": "USER_CONVERSATION_DOWNLOAD",
            "eventPayload": event_payload,
            "createdAt": log_entry["timestamp"],
            "userId": user_id,
            "email": email or "N/A",
            "role": role or "NORMAL",
            "department": department,
            "userEntraId": user_entra_id,
            "correlationId": correlation_id,
        }
        
        # Write to PostgreSQL user_logs table (async, don't wait)
        import asyncio
        from aldar_middleware.database.base import async_session
        
        async def write_to_postgres():
            try:
                async with async_session() as db:
                    await postgres_logs_service.write_user_log(db, user_log_entry)
            except Exception as e:
                logger.debug(f"Failed to write to PostgreSQL user_logs (non-blocking): {e}")
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(write_to_postgres())
            else:
                loop.run_until_complete(write_to_postgres())
        except Exception as e:
            logger.debug(f"Failed to schedule PostgreSQL write (non-blocking): {e}")
    except Exception as e:
        logger.debug(f"Error preparing user log entry (non-blocking): {e}")


def log_conversation_share(
    chat_id: str,
    session_id: str,
    user_id: str,
    username: str,
    share_url: Optional[str] = None,
    visibility: Optional[str] = None,  # public, private
    format: str = "json",  # json, pdf
    correlation_id: Optional[str] = None,
    email: Optional[str] = None,
    role: Optional[str] = None,
    department: Optional[str] = None,
    user_entra_id: Optional[str] = None,
) -> None:
    """Log conversation share to Cosmos DB.
    
    Args:
        chat_id: Chat UUID
        session_id: Chat session ID
        user_id: User UUID
        username: Username
        share_url: Shared URL if available
        visibility: Share visibility (public, private)
        format: Share format (json, pdf)
        correlation_id: Request correlation ID
        email: User email
        role: User role ("ADMIN" or "NORMAL")
        department: User department
        user_entra_id: User Azure AD ID
    """
    correlation_id = correlation_id or get_correlation_id() or str(uuid.uuid4())
    
    user_ctx = get_user_context()
    if not email and user_ctx:
        email = user_ctx.email
    
    log_entry = {
        "id": str(uuid.uuid4()),
        "type": "conversation_share",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        
        # Chat info
        "chat_id": chat_id,
        "session_id": session_id,
        "conversationId": chat_id,
        "share_url": share_url,
        "visibility": visibility,
        "format": format,
        
        # User info
        "user_id": user_id,
        "username": username,
        "email": email or "N/A",
        "role": role or "NORMAL",
        "department": department,
        "user_entra_id": user_entra_id,
        
        # Metadata
        "event": "conversation_share",
        "action": "SHARE_CONVERSATION",
    }
    
    bind_context = {
        "correlation_id": correlation_id,
        "user_id": user_id,
        "username": username,
        "chat_id": chat_id,
        "session_id": session_id,
    }
    
    if user_ctx:
        bind_context.update({
            "email": email or user_ctx.email or "N/A",
            "is_authenticated": user_ctx.is_authenticated,
        })
    
    logger.bind(**bind_context).info(
        f"CONVERSATION_SHARE: visibility={visibility}, format={format}, session_id={session_id}",
        extra={"chat_event": log_entry}
    )
    
    # Also write directly to PostgreSQL user_logs table in 3.0 format for faster queries
    try:
        # Build event payload for USER_CONVERSATION_SHARE
        event_payload = {
            "conversationId": chat_id or session_id,
            "format": format,
        }
        if share_url:
            event_payload["shareUrl"] = share_url
        if visibility:
            event_payload["visibility"] = visibility
        
        # Build user log entry in 3.0 format
        user_log_entry = {
            "id": log_entry["id"],
            "timestamp": log_entry["timestamp"],
            "eventType": "USER_CONVERSATION_SHARE",
            "eventPayload": event_payload,
            "createdAt": log_entry["timestamp"],
            "userId": user_id,
            "email": email or "N/A",
            "role": role or "NORMAL",
            "department": department,
            "userEntraId": user_entra_id,
            "correlationId": correlation_id,
        }
        
        # Write to PostgreSQL user_logs table (async, don't wait)
        import asyncio
        from aldar_middleware.database.base import async_session
        
        async def write_to_postgres():
            try:
                async with async_session() as db:
                    await postgres_logs_service.write_user_log(db, user_log_entry)
            except Exception as e:
                logger.debug(f"Failed to write to PostgreSQL user_logs (non-blocking): {e}")
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(write_to_postgres())
            else:
                loop.run_until_complete(write_to_postgres())
        except Exception as e:
            logger.debug(f"Failed to schedule PostgreSQL write (non-blocking): {e}")
    except Exception as e:
        logger.debug(f"Error preparing user log entry (non-blocking): {e}")


__all__ = [
    "log_chat_session_created",
    "log_chat_message",
    "log_chat_session_updated",
    "log_chat_session_deleted",
    "log_chat_favorite_toggled",
    "log_chat_analytics_event",
    "log_conversation_renamed",
    "log_starting_prompt_chosen",
    "log_my_agent_knowledge_sources_updated",
    "log_message_regenerated",
    "log_user_created",
    "log_conversation_download",
    "log_conversation_share",
]

