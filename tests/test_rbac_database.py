#!/usr/bin/env python3
"""
RBAC System Direct Database Test Script
Test the RBAC system by directly interacting with the database
"""

import sys
import os
import asyncio
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text
from aldar_middleware.database.base import engine
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Colors for output
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
NC = '\033[0m'  # No Color

def print_status(message):
    print(f"{BLUE}[INFO]{NC} {message}")

def print_success(message):
    print(f"{GREEN}[SUCCESS]{NC} {message}")

def print_warning(message):
    print(f"{YELLOW}[WARNING]{NC} {message}")

def print_error(message):
    print(f"{RED}[ERROR]{NC} {message}")


class RBACDatabaseTester:
    """RBAC system database tester"""
    
    async def test_database_connection(self):
        """Test database connection"""
        print_status("Testing database connection...")
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text("SELECT 1"))
                print_success("Database connection successful")
                return True
        except Exception as e:
            print_error(f"Database connection failed: {e}")
            return False
    
    async def initialize_default_roles(self):
        """Initialize default roles directly in database"""
        print_status("Initializing default roles...")
        try:
            async with engine.begin() as conn:
                # Define role levels and names
                role_levels = [
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
                
                for level, name, description in role_levels:
                    # Check if role already exists
                    result = await conn.execute(
                        text("SELECT id FROM rbac_roles WHERE name = :name"),
                        {"name": name}
                    )
                    existing_role = result.fetchone()
                    
                    if not existing_role:
                        await conn.execute(
                            text("""
                                INSERT INTO rbac_roles (name, level, description, is_active, created_at, updated_at)
                                VALUES (:name, :level, :description, true, NOW(), NOW())
                            """),
                            {"name": name, "level": level, "description": description}
                        )
                        print_success(f"Created role: {name} (level {level})")
                    else:
                        print_warning(f"Role {name} already exists")
                
                print_success("Default roles initialized")
                return True
        except Exception as e:
            print_error(f"Role initialization failed: {e}")
            return False
    
    async def initialize_default_services(self):
        """Initialize default services directly in database"""
        print_status("Initializing default services...")
        try:
            async with engine.begin() as conn:
                # Define default services
                services = [
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
                
                for name, description, service_type in services:
                    # Check if service already exists
                    result = await conn.execute(
                        text("SELECT id FROM rbac_services WHERE name = :name"),
                        {"name": name}
                    )
                    existing_service = result.fetchone()
                    
                    if not existing_service:
                        await conn.execute(
                            text("""
                                INSERT INTO rbac_services (name, description, service_type, is_active, created_at, updated_at)
                                VALUES (:name, :description, :service_type, true, NOW(), NOW())
                            """),
                            {"name": name, "description": description, "service_type": service_type}
                        )
                        print_success(f"Created service: {name} ({service_type})")
                    else:
                        print_warning(f"Service {name} already exists")
                
                print_success("Default services initialized")
                return True
        except Exception as e:
            print_error(f"Service initialization failed: {e}")
            return False
    
    async def create_test_users(self):
        """Create test users directly in database"""
        print_status("Creating test users...")
        try:
            async with engine.begin() as conn:
                # Define test users
                users = [
                    ("admin", "admin@aiq.com", "System Administrator"),
                    ("moderator", "moderator@aiq.com", "System Moderator"),
                    ("user", "user@aiq.com", "Regular User")
                ]
                
                for username, email, full_name in users:
                    # Check if user already exists
                    result = await conn.execute(
                        text("SELECT id FROM rbac_users WHERE username = :username"),
                        {"username": username}
                    )
                    existing_user = result.fetchone()
                    
                    if not existing_user:
                        await conn.execute(
                            text("""
                                INSERT INTO rbac_users (username, email, full_name, is_active, created_at, updated_at)
                                VALUES (:username, :email, :full_name, true, NOW(), NOW())
                            """),
                            {"username": username, "email": email, "full_name": full_name}
                        )
                        print_success(f"Created user: {username}")
                    else:
                        print_warning(f"User {username} already exists")
                
                print_success("Test users created")
                return True
        except Exception as e:
            print_error(f"User creation failed: {e}")
            return False
    
    async def assign_roles_to_users(self):
        """Assign roles to users directly in database"""
        print_status("Assigning roles to users...")
        try:
            async with engine.begin() as conn:
                # Define role assignments
                assignments = [
                    ("admin", "superadmin"),
                    ("moderator", "moderator"),
                    ("user", "user")
                ]
                
                for username, role_name in assignments:
                    # Get user ID
                    result = await conn.execute(
                        text("SELECT id FROM rbac_users WHERE username = :username"),
                        {"username": username}
                    )
                    user = result.fetchone()
                    
                    # Get role ID
                    result = await conn.execute(
                        text("SELECT id FROM rbac_roles WHERE name = :name"),
                        {"name": role_name}
                    )
                    role = result.fetchone()
                    
                    if user and role:
                        # Check if assignment already exists
                        result = await conn.execute(
                            text("""
                                SELECT user_id FROM user_specific_roles 
                                WHERE user_id = :user_id AND role_id = :role_id
                            """),
                            {"user_id": user.id, "role_id": role.id}
                        )
                        existing_assignment = result.fetchone()
                        
                        if not existing_assignment:
                            await conn.execute(
                                text("""
                                    INSERT INTO user_specific_roles (user_id, role_id, granted_by, created_at)
                                    VALUES (:user_id, :role_id, 1, NOW())
                                """),
                                {"user_id": user.id, "role_id": role.id}
                            )
                            print_success(f"Assigned role {role_name} to user {username}")
                        else:
                            print_warning(f"Role {role_name} already assigned to user {username}")
                    else:
                        print_error(f"Could not find user {username} or role {role_name}")
                
                print_success("Role assignments completed")
                return True
        except Exception as e:
            print_error(f"Role assignment failed: {e}")
            return False
    
    async def assign_services_to_roles(self):
        """Assign services to roles directly in database"""
        print_status("Assigning services to roles...")
        try:
            async with engine.begin() as conn:
                # Define service assignments
                assignments = [
                    ("superadmin", ["user_management", "order_processing", "payment_gateway", 
                                   "notification_service", "analytics_engine", "report_generator",
                                   "file_storage", "database_admin", "queue_manager", "monitoring_dashboard"]),
                    ("moderator", ["user_management", "order_processing", "notification_service"]),
                    ("user", ["order_processing"])
                ]
                
                for role_name, service_names in assignments:
                    # Get role ID
                    result = await conn.execute(
                        text("SELECT id FROM rbac_roles WHERE name = :name"),
                        {"name": role_name}
                    )
                    role = result.fetchone()
                    
                    if role:
                        # Clear existing assignments for this role
                        await conn.execute(
                            text("DELETE FROM role_services WHERE role_id = :role_id"),
                            {"role_id": role.id}
                        )
                        
                        # Assign new services
                        for service_name in service_names:
                            # Get service ID
                            result = await conn.execute(
                                text("SELECT id FROM rbac_services WHERE name = :name"),
                                {"name": service_name}
                            )
                            service = result.fetchone()
                            
                            if service:
                                await conn.execute(
                                    text("""
                                        INSERT INTO role_services (role_id, service_id)
                                        VALUES (:role_id, :service_id)
                                    """),
                                    {"role_id": role.id, "service_id": service.id}
                                )
                                print_success(f"Assigned service {service_name} to role {role_name}")
                            else:
                                print_error(f"Service {service_name} not found")
                    else:
                        print_error(f"Role {role_name} not found")
                
                print_success("Service assignments completed")
                return True
        except Exception as e:
            print_error(f"Service assignment failed: {e}")
            return False
    
    async def test_rbac_data(self):
        """Test RBAC data integrity"""
        print_status("Testing RBAC data integrity...")
        try:
            async with engine.begin() as conn:
                # Test roles
                result = await conn.execute(text("SELECT COUNT(*) FROM rbac_roles"))
                role_count = result.scalar()
                print_success(f"Found {role_count} roles")
                
                # Test services
                result = await conn.execute(text("SELECT COUNT(*) FROM rbac_services"))
                service_count = result.scalar()
                print_success(f"Found {service_count} services")
                
                # Test users
                result = await conn.execute(text("SELECT COUNT(*) FROM rbac_users"))
                user_count = result.scalar()
                print_success(f"Found {user_count} users")
                
                # Test role assignments
                result = await conn.execute(text("SELECT COUNT(*) FROM user_specific_roles"))
                assignment_count = result.scalar()
                print_success(f"Found {assignment_count} role assignments")
                
                # Test service assignments
                result = await conn.execute(text("SELECT COUNT(*) FROM role_services"))
                service_assignment_count = result.scalar()
                print_success(f"Found {service_assignment_count} service assignments")
                
                return True
        except Exception as e:
            print_error(f"RBAC data test failed: {e}")
            return False
    
    async def test_role_hierarchy(self):
        """Test role hierarchy"""
        print_status("Testing role hierarchy...")
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text("""
                    SELECT name, level FROM rbac_roles 
                    ORDER BY level
                """))
                roles = result.fetchall()
                
                expected_levels = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
                actual_levels = [role.level for role in roles]
                
                if actual_levels == expected_levels:
                    print_success("✅ Role hierarchy levels are correct")
                    for role in roles:
                        print(f"   - {role.name} (level {role.level})")
                else:
                    print_warning(f"⚠️ Role levels don't match expected: {actual_levels}")
                
                return True
        except Exception as e:
            print_error(f"Role hierarchy test failed: {e}")
            return False
    
    async def test_user_roles(self):
        """Test user role assignments"""
        print_status("Testing user role assignments...")
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text("""
                    SELECT u.username, r.name as role_name, r.level
                    FROM rbac_users u
                    JOIN user_specific_roles usr ON u.id = usr.user_id
                    JOIN rbac_roles r ON usr.role_id = r.id
                    ORDER BY u.username, r.level
                """))
                user_roles = result.fetchall()
                
                if user_roles:
                    print_success(f"✅ Found {len(user_roles)} user role assignments:")
                    for role in user_roles:
                        print(f"   - {role.username}: {role.role_name} (level {role.level})")
                else:
                    print_warning("⚠️ No user role assignments found")
                
                return True
        except Exception as e:
            print_error(f"User roles test failed: {e}")
            return False
    
    async def test_service_assignments(self):
        """Test service assignments to roles"""
        print_status("Testing service assignments...")
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text("""
                    SELECT r.name as role_name, s.name as service_name, s.service_type
                    FROM rbac_roles r
                    JOIN role_services rs ON r.id = rs.role_id
                    JOIN rbac_services s ON rs.service_id = s.id
                    ORDER BY r.name, s.name
                """))
                service_assignments = result.fetchall()
                
                if service_assignments:
                    print_success(f"✅ Found {len(service_assignments)} service assignments:")
                    current_role = None
                    for assignment in service_assignments:
                        if assignment.role_name != current_role:
                            print(f"   {assignment.role_name}:")
                            current_role = assignment.role_name
                        print(f"     - {assignment.service_name} ({assignment.service_type})")
                else:
                    print_warning("⚠️ No service assignments found")
                
                return True
        except Exception as e:
            print_error(f"Service assignments test failed: {e}")
            return False
    
    async def run_comprehensive_tests(self):
        """Run comprehensive RBAC tests"""
        print("🧪 RBAC System Database Test Suite")
        print("=" * 60)
        
        # Initialize system
        init_tests = [
            ("Database Connection", self.test_database_connection),
            ("Default Roles", self.initialize_default_roles),
            ("Default Services", self.initialize_default_services),
            ("Test Users", self.create_test_users),
            ("Role Assignments", self.assign_roles_to_users),
            ("Service Assignments", self.assign_services_to_roles),
        ]
        
        print("\n📋 Initialization Phase...")
        init_results = []
        for test_name, test_func in init_tests:
            print(f"\n🔧 Running {test_name}...")
            try:
                result = await test_func()
                init_results.append((test_name, result))
                if result:
                    print_success(f"✅ {test_name} completed")
                else:
                    print_error(f"❌ {test_name} failed")
            except Exception as e:
                print_error(f"❌ {test_name} failed with exception: {e}")
                init_results.append((test_name, False))
        
        # Test system functionality
        test_tests = [
            ("RBAC Data Integrity", self.test_rbac_data),
            ("Role Hierarchy", self.test_role_hierarchy),
            ("User Roles", self.test_user_roles),
            ("Service Assignments", self.test_service_assignments),
        ]
        
        print("\n📋 Testing Phase...")
        test_results = []
        for test_name, test_func in test_tests:
            print(f"\n🧪 Running {test_name} Test...")
            try:
                result = await test_func()
                test_results.append((test_name, result))
                if result:
                    print_success(f"✅ {test_name} test passed")
                else:
                    print_error(f"❌ {test_name} test failed")
            except Exception as e:
                print_error(f"❌ {test_name} test failed with exception: {e}")
                test_results.append((test_name, False))
        
        # Summary
        print("\n" + "=" * 60)
        print("📊 Test Results Summary")
        print("=" * 60)
        
        all_results = init_results + test_results
        passed = sum(1 for _, result in all_results if result)
        total = len(all_results)
        
        print("\n🔧 Initialization Results:")
        for test_name, result in init_results:
            status_icon = "✅" if result else "❌"
            print(f"{status_icon} {test_name}")
        
        print("\n🧪 Test Results:")
        for test_name, result in test_results:
            status_icon = "✅" if result else "❌"
            print(f"{status_icon} {test_name}")
        
        print(f"\n🎯 Overall Result: {passed}/{total} tests passed")
        
        if passed == total:
            print_success("🎉 All RBAC tests passed! The system is fully functional.")
            print("\n🚀 Next steps:")
            print("1. Start the API server: uvicorn aldar_middleware.application:app --reload")
            print("2. Visit http://localhost:8000/docs to see the API documentation")
            print("3. Test the RBAC endpoints:")
            print("   - GET /rbac/roles")
            print("   - GET /rbac/users/admin/roles")
            print("   - POST /rbac/permissions/check")
            print("   - GET /rbac/hierarchy")
            print("   - GET /rbac/stats")
        else:
            print_warning(f"⚠️ {total - passed} tests failed. Please check the issues above.")
        
        return passed == total


async def main():
    """Main function"""
    print("🚀 Starting RBAC System Database Testing...")
    print("This will initialize the RBAC system with test data and run comprehensive tests.")
    print()
    
    tester = RBACDatabaseTester()
    success = await tester.run_comprehensive_tests()
    
    if not success:
        print("\n❌ Some tests failed. Please fix the issues before proceeding.")
        sys.exit(1)
    else:
        print("\n🎉 RBAC System is fully initialized and tested!")


if __name__ == "__main__":
    asyncio.run(main())
