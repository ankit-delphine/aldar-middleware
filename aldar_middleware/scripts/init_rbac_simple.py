#!/usr/bin/env python3
"""
Simple RBAC Initialization Script
Initialize the RBAC system with default roles, services, and permissions
"""

import sys
import os
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import asyncio
from sqlalchemy import text
from aldar_middleware.database.base import engine
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def initialize_rbac_data():
    """Initialize RBAC data directly with SQL"""
    logger.info("Initializing RBAC data...")
    
    async with engine.begin() as conn:
        # Insert default roles
        roles_data = [
            (0, "user", "Level 0 role: user"),
            (10, "basic_user", "Level 10 role: basic_user"),
            (20, "standard_user", "Level 20 role: standard_user"),
            (30, "advanced_user", "Level 30 role: advanced_user"),
            (40, "power_user", "Level 40 role: power_user"),
            (50, "moderator", "Level 50 role: moderator"),
            (60, "supervisor", "Level 60 role: supervisor"),
            (70, "manager", "Level 70 role: manager"),
            (80, "admin", "Level 80 role: admin"),
            (90, "super_admin", "Level 90 role: super_admin"),
            (100, "superadmin", "Level 100 role: superadmin")
        ]
        
        for level, name, description in roles_data:
            await conn.execute(text("""
                INSERT INTO rbac_roles (name, level, description, is_active, created_at)
                VALUES (:name, :level, :description, true, NOW())
                ON CONFLICT (name) DO NOTHING
            """), {"name": name, "level": level, "description": description})
        
        logger.info("✅ Default roles created")
        
        # Insert default services
        services_data = [
            ("user_management", "User Management API", "api"),
            ("order_processing", "Order Processing Service", "api"),
            ("payment_gateway", "Payment Gateway Service", "api"),
            ("notification_service", "Notification Service", "notification"),
            ("analytics_engine", "Analytics Engine", "analytics"),
            ("report_generator", "Report Generator", "reporting"),
            ("file_storage", "File Storage Service", "file_storage"),
            ("database_admin", "Database Administration", "database"),
            ("queue_manager", "Queue Management", "message_queue"),
            ("monitoring_dashboard", "Monitoring Dashboard", "monitoring")
        ]
        
        for name, description, service_type in services_data:
            await conn.execute(text("""
                INSERT INTO rbac_services (name, description, service_type, is_active, created_at)
                VALUES (:name, :description, :service_type, true, NOW())
                ON CONFLICT (name) DO NOTHING
            """), {"name": name, "description": description, "service_type": service_type})
        
        logger.info("✅ Default services created")
        
        # Create admin user
        await conn.execute(text("""
            INSERT INTO rbac_users (username, email, full_name, is_active, created_at)
            VALUES ('admin', 'admin@aiq.com', 'System Administrator', true, NOW())
            ON CONFLICT (username) DO NOTHING
        """))
        
        logger.info("✅ Admin user created")
        
        # Assign superadmin role to admin user
        await conn.execute(text("""
            INSERT INTO user_specific_roles (user_id, role_id, granted_by, created_at)
            SELECT u.id, r.id, u.id, NOW()
            FROM rbac_users u, rbac_roles r
            WHERE u.username = 'admin' AND r.name = 'superadmin'
            ON CONFLICT (user_id, role_id) DO NOTHING
        """))
        
        logger.info("✅ Superadmin role assigned to admin user")
        
        # Assign services to roles
        role_service_assignments = {
            "superadmin": [
                "user_management", "order_processing", "payment_gateway",
                "notification_service", "analytics_engine", "report_generator",
                "file_storage", "database_admin", "queue_manager", "monitoring_dashboard"
            ],
            "admin": [
                "user_management", "order_processing", "payment_gateway",
                "notification_service", "analytics_engine", "report_generator",
                "file_storage", "queue_manager", "monitoring_dashboard"
            ],
            "manager": [
                "user_management", "order_processing", "notification_service",
                "analytics_engine", "report_generator", "monitoring_dashboard"
            ],
            "moderator": [
                "user_management", "order_processing", "notification_service"
            ],
            "power_user": [
                "order_processing", "analytics_engine", "report_generator"
            ],
            "standard_user": [
                "order_processing", "notification_service"
            ],
            "basic_user": [
                "notification_service"
            ]
        }
        
        for role_name, service_names in role_service_assignments.items():
            for service_name in service_names:
                await conn.execute(text("""
                    INSERT INTO role_services (role_id, service_id)
                    SELECT r.id, s.id
                    FROM rbac_roles r, rbac_services s
                    WHERE r.name = :role_name AND s.name = :service_name
                    ON CONFLICT (role_id, service_id) DO NOTHING
                """), {"role_name": role_name, "service_name": service_name})
        
        logger.info("✅ Service assignments completed")
        
        # Create example users
        example_users = [
            ("john_doe", "john@example.com", "John Doe", "manager"),
            ("jane_smith", "jane@example.com", "Jane Smith", "moderator"),
            ("bob_wilson", "bob@example.com", "Bob Wilson", "power_user"),
            ("alice_brown", "alice@example.com", "Alice Brown", "standard_user"),
            ("charlie_davis", "charlie@example.com", "Charlie Davis", "basic_user")
        ]
        
        for username, email, full_name, role_name in example_users:
            # Create user
            await conn.execute(text("""
                INSERT INTO rbac_users (username, email, full_name, is_active, created_at)
                VALUES (:username, :email, :full_name, true, NOW())
                ON CONFLICT (username) DO NOTHING
            """), {"username": username, "email": email, "full_name": full_name})
            
            # Assign role
            await conn.execute(text("""
                INSERT INTO user_specific_roles (user_id, role_id, granted_by, created_at)
                SELECT u.id, r.id, admin.id, NOW()
                FROM rbac_users u, rbac_roles r, rbac_users admin
                WHERE u.username = :username AND r.name = :role_name AND admin.username = 'admin'
                ON CONFLICT (user_id, role_id) DO NOTHING
            """), {"username": username, "role_name": role_name})
        
        logger.info("✅ Example users created")
        
        logger.info("🎉 RBAC system initialization completed successfully!")


async def main():
    """Main initialization function"""
    logger.info("Starting RBAC system initialization...")
    
    try:
        await initialize_rbac_data()
        
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
    asyncio.run(main())
