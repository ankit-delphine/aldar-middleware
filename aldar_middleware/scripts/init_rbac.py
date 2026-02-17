#!/usr/bin/env python3
"""
RBAC Initialization Script
Initialize the RBAC system with default roles, services, and permissions
"""

import sys
import os
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy.orm import Session
from aldar_middleware.database.base import get_db, engine
from aldar_middleware.models.rbac import (
    RBACRole,
    RBACPermission,
    RBACService,
    RBACRoleGroup,
    RBACUser,
    RBACUserAccess,
    RBACRolePermission,
    RBACUserSession,
    SERVICE_TYPES,
    COMMON_PERMISSIONS,
)
from aldar_middleware.services.rbac_service import RBACService
from aldar_middleware.schemas.rbac import RoleCreate, ServiceCreate, UserCreate
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def create_tables():
    """Create all RBAC tables"""
    logger.info("Creating RBAC tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("RBAC tables created successfully")


def initialize_default_data():
    """Initialize default roles, services, and users"""
    db = next(get_db())
    rbac_service = RBACService(db)
    
    try:
        # Initialize default roles
        logger.info("Initializing default roles...")
        rbac_service.initialize_default_roles()
        
        # Initialize default services
        logger.info("Initializing default services...")
        rbac_service.initialize_default_services()
        
        # Create default admin user
        logger.info("Creating default admin user...")
        admin_user_data = UserCreate(
            username="admin",
            email="admin@aiq.com",
            full_name="System Administrator",
            is_active=True
        )
        
        try:
            admin_user = rbac_service.create_user(admin_user_data)
            logger.info(f"Created admin user: {admin_user.username}")
        except Exception as e:
            logger.warning(f"Admin user might already exist: {e}")
        
        # Assign superadmin role to admin user
        try:
            rbac_service.assign_role_to_user("admin", "superadmin", "admin")
            logger.info("Assigned superadmin role to admin user")
        except Exception as e:
            logger.warning(f"Could not assign superadmin role: {e}")
        
        # Assign services to roles
        logger.info("Assigning services to roles...")
        
        # Superadmin gets all services
        all_services = [
            "user_management", "order_processing", "payment_gateway",
            "notification_service", "analytics_engine", "report_generator",
            "file_storage", "database_admin", "queue_manager", "monitoring_dashboard"
        ]
        rbac_service.assign_services_to_role("superadmin", all_services)
        
        # Admin gets most services except superadmin-specific ones
        admin_services = [
            "user_management", "order_processing", "payment_gateway",
            "notification_service", "analytics_engine", "report_generator",
            "file_storage", "queue_manager", "monitoring_dashboard"
        ]
        rbac_service.assign_services_to_role("admin", admin_services)
        
        # Manager gets management services
        manager_services = [
            "user_management", "order_processing", "notification_service",
            "analytics_engine", "report_generator", "monitoring_dashboard"
        ]
        rbac_service.assign_services_to_role("manager", manager_services)
        
        # Moderator gets moderation services
        moderator_services = [
            "user_management", "order_processing", "notification_service"
        ]
        rbac_service.assign_services_to_role("moderator", moderator_services)
        
        # Power user gets advanced services
        power_user_services = [
            "order_processing", "analytics_engine", "report_generator"
        ]
        rbac_service.assign_services_to_role("power_user", power_user_services)
        
        # Standard user gets basic services
        standard_user_services = [
            "order_processing", "notification_service"
        ]
        rbac_service.assign_services_to_role("standard_user", standard_user_services)
        
        # Basic user gets minimal services
        basic_user_services = [
            "notification_service"
        ]
        rbac_service.assign_services_to_role("basic_user", basic_user_services)
        
        logger.info("Service assignments completed")
        
        # Create some example users with different roles
        logger.info("Creating example users...")
        
        example_users = [
            ("john_doe", "john@example.com", "John Doe", "manager"),
            ("jane_smith", "jane@example.com", "Jane Smith", "moderator"),
            ("bob_wilson", "bob@example.com", "Bob Wilson", "power_user"),
            ("alice_brown", "alice@example.com", "Alice Brown", "standard_user"),
            ("charlie_davis", "charlie@example.com", "Charlie Davis", "basic_user")
        ]
        
        for username, email, full_name, role_name in example_users:
            try:
                user_data = UserCreate(
                    username=username,
                    email=email,
                    full_name=full_name,
                    is_active=True
                )
                user = rbac_service.create_user(user_data)
                rbac_service.assign_role_to_user(username, role_name, "admin")
                logger.info(f"Created user {username} with role {role_name}")
            except Exception as e:
                logger.warning(f"Could not create user {username}: {e}")
        
        logger.info("RBAC system initialization completed successfully!")
        
    except Exception as e:
        logger.error(f"Error during initialization: {e}")
        raise
    finally:
        db.close()


async def main():
    """Main initialization function"""
    logger.info("Starting RBAC system initialization...")
    
    try:
        # Create tables
        await create_tables()
        
        # Initialize default data
        initialize_default_data()
        
        logger.info("RBAC system initialization completed successfully!")
        print("\n🎉 RBAC System Initialized Successfully!")
        print("=" * 50)
        print("✅ Database tables created")
        print("✅ Default roles created (0-100 levels)")
        print("✅ Default services created")
        print("✅ Admin user created (username: admin)")
        print("✅ Service assignments completed")
        print("✅ Example users created")
        print("\nYou can now use the RBAC API endpoints:")
        print("- GET /rbac/roles - List all roles")
        print("- GET /rbac/users/{username}/roles - Get user roles")
        print("- POST /rbac/users/assign-role - Assign role to user")
        print("- POST /rbac/permissions/check - Check user permissions")
        print("- GET /rbac/hierarchy - View role hierarchy")
        
    except Exception as e:
        logger.error(f"Initialization failed: {e}")
        print(f"\n❌ Initialization failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
