"""Azure Service Bus service for message queuing."""

import json
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime

from azure.servicebus import ServiceBusClient, ServiceBusMessage
from loguru import logger

from aldar_middleware.settings import settings


class AzureServiceBusService:
    """Azure Service Bus service for message queuing."""
    
    def __init__(self):
        """Initialize Azure Service Bus service."""
        self.connection_string = settings.service_bus_connection_string
        self.queue_name = settings.service_bus_queue_name
        self.client: Optional[ServiceBusClient] = None
        
    async def connect(self) -> bool:
        """Connect to Azure Service Bus."""
        try:
            if not self.connection_string:
                logger.warning("Azure Service Bus connection string not provided")
                return False
                
            self.client = ServiceBusClient.from_connection_string(
                conn_str=self.connection_string
            )
            logger.info("Connected to Azure Service Bus")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to Azure Service Bus: {e}")
            return False
    
    async def send_message(self, message_body: Dict[str, Any], message_type: str = "default") -> bool:
        """Send message to Azure Service Bus queue."""
        try:
            if not self.client:
                if not await self.connect():
                    return False
            
            # Create message
            message_data = {
                "type": message_type,
                "payload": message_body,
                "timestamp": datetime.utcnow().isoformat(),
                "source": "aldar-middleware"
            }
            
            message = ServiceBusMessage(
                body=json.dumps(message_data).encode('utf-8'),
                content_type="application/json"
            )
            
            # Send message
            sender = self.client.get_queue_sender(queue_name=self.queue_name)
            sender.send_messages(message)
            logger.info(f"Message sent to Azure Service Bus: {message_type}")
            return True
                    
        except Exception as e:
            logger.error(f"Failed to send message to Azure Service Bus: {e}")
            return False
    
    async def receive_messages(self, max_messages: int = 10) -> List[Dict[str, Any]]:
        """Receive messages from Azure Service Bus queue."""
        try:
            if not self.client:
                if not await self.connect():
                    return []
            
            messages = []
            receiver = self.client.get_queue_receiver(queue_name=self.queue_name)
            received_msgs = receiver.receive_messages(max_message_count=max_messages, max_wait_time=5)
            
            for msg in received_msgs:
                try:
                    message_data = json.loads(str(msg))
                    messages.append(message_data)
                    
                    # Complete the message
                    receiver.complete_message(msg)
                    logger.info(f"Message processed: {message_data.get('type', 'unknown')}")
                    
                except Exception as e:
                    logger.error(f"Failed to process message: {e}")
                    # Dead letter the message
                    receiver.dead_letter_message(msg, reason="Processing failed")
            
            return messages
            
        except Exception as e:
            logger.error(f"Failed to receive messages from Azure Service Bus: {e}")
            return []
    
    async def get_queue_properties(self) -> Optional[Dict[str, Any]]:
        """Get Azure Service Bus queue properties."""
        try:
            if not self.client:
                if not await self.connect():
                    return None
            
            async with self.client:
                # This would require additional Azure Service Bus management operations
                # For now, return basic info
                return {
                    "queue_name": self.queue_name,
                    "connected": True,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
        except Exception as e:
            logger.error(f"Failed to get queue properties: {e}")
            return None
    
    async def health_check(self) -> Dict[str, Any]:
        """Perform health check on Azure Service Bus."""
        try:
            if not self.connection_string:
                return {
                    "status": "error",
                    "message": "Azure Service Bus connection string not configured",
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            if await self.connect():
                return {
                    "status": "healthy",
                    "service": "azure_service_bus",
                    "queue_name": self.queue_name,
                    "timestamp": datetime.utcnow().isoformat()
                }
            else:
                return {
                    "status": "unhealthy",
                    "service": "azure_service_bus",
                    "message": "Failed to connect to Azure Service Bus",
                    "timestamp": datetime.utcnow().isoformat()
                }
                
        except Exception as e:
            logger.error(f"Azure Service Bus health check failed: {e}")
            return {
                "status": "error",
                "service": "azure_service_bus",
                "message": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
    
    async def close(self):
        """Close Azure Service Bus connection."""
        if self.client:
            self.client.close()
            self.client = None
            logger.info("Azure Service Bus connection closed")


# Global instance
azure_service_bus = AzureServiceBusService()
