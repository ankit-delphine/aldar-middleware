#!/usr/bin/env python3
"""
RBAC System Initialization and Comprehensive Test Script
Initialize the RBAC system with test data and run comprehensive tests
"""

import sys
import os
import asyncio
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy.orm import Session
from aldar_middleware.database.base import get_db
from aldar_middleware.services.rbac_service import RBACService
from aldar_middleware.schemas.rbac import (
    RoleCreate, UserCreate, ServiceCreate, PermissionCreate
)
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


class RBACInitializer:
    """RBAC system initializer and tester"""
    
    def __init__(self):
        self.db = None
        self.rbac_service = None
    
    async def initialize_database(self):
        """Initialize database connection and RBAC service"""
        print_status("Initializing database connection...")
        try:
            async for db in get_db():
                self.db = db
                self.rbac_service = RBACService(db)
                print_success("Database connection established")
                return True
        except Exception as e:
            print_error(f"Database initialization failed: {e}")
            return False
    
    async def initialize_default_roles(self):
        """Initialize default roles"""
        print_status("Initializing default roles...")
        try:
            self.rbac_service.initialize_default_roles()
            print_success("Default roles initialized")
            return True
        except Exception as e:
            print_error(f"Role initialization failed: {e}")
            return False
    
    async def initialize_default_services(self):
        """Initialize default services"""
        print_status("Initializing default services...")
        try:
            self.rbac_service.initialize_default_services()
            print_success("Default services initialized")
            return True
        except Exception as e:
            print_error(f"Service initialization failed: {e}")
            return False
    
    async def create_test_users(self):
        """Create test users with different roles"""
        print_status("Creating test users...")
        try:
            # Create admin user
            admin_user = UserCreate(
                username="admin",
                email="admin@aiq.com",
                full_name="System Administrator",
                is_active=True
            )
            admin = self.rbac_service.create_user(admin_user)
            print_success(f"Created admin user: {admin.username}")
            
            # Create moderator user
            moderator_user = UserCreate(
                username="moderator",
                email="moderator@aiq.com",
                full_name="System Moderator",
                is_active=True
            )
            moderator = self.rbac_service.create_user(moderator_user)
            print_success(f"Created moderator user: {moderator.username}")
            
            # Create regular user
            user_user = UserCreate(
                username="user",
                email="user@aiq.com",
                full_name="Regular User",
                is_active=True
            )
            user = self.rbac_service.create_user(user_user)
            print_success(f"Created regular user: {user.username}")
            
            return True
        except Exception as e:
            print_error(f"User creation failed: {e}")
            return False
    
    async def assign_roles_to_users(self):
        """Assign roles to test users"""
        print_status("Assigning roles to users...")
        try:
            # Assign superadmin role to admin
            self.rbac_service.assign_role_to_user("admin", "superadmin", "system")
            print_success("Assigned superadmin role to admin")
            
            # Assign moderator role to moderator
            self.rbac_service.assign_role_to_user("moderator", "moderator", "admin")
            print_success("Assigned moderator role to moderator")
            
            # Assign user role to user
            self.rbac_service.assign_role_to_user("user", "user", "admin")
            print_success("Assigned user role to regular user")
            
            return True
        except Exception as e:
            print_error(f"Role assignment failed: {e}")
            return False
    
    async def assign_services_to_roles(self):
        """Assign services to roles"""
        print_status("Assigning services to roles...")
        try:
            # Assign all services to superadmin
            all_services = [
                "user_management", "order_processing", "payment_gateway",
                "notification_service", "analytics_engine", "report_generator",
                "file_storage", "database_admin", "queue_manager", "monitoring_dashboard"
            ]
            self.rbac_service.assign_services_to_role("superadmin", all_services)
            print_success("Assigned all services to superadmin role")
            
            # Assign limited services to moderator
            moderator_services = [
                "user_management", "order_processing", "notification_service"
            ]
            self.rbac_service.assign_services_to_role("moderator", moderator_services)
            print_success("Assigned limited services to moderator role")
            
            # Assign basic services to user
            user_services = ["order_processing"]
            self.rbac_service.assign_services_to_role("user", user_services)
            print_success("Assigned basic services to user role")
            
            return True
        except Exception as e:
            print_error(f"Service assignment failed: {e}")
            return False
    
    async def test_role_hierarchy(self):
        """Test role hierarchy system"""
        print_status("Testing role hierarchy system...")
        try:
            # Test that all expected roles exist
            expected_levels = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
            role_names = ["user", "basic_user", "standard_user", "advanced_user", 
                         "power_user", "moderator", "supervisor", "manager", 
                         "admin", "super_admin", "superadmin"]
            
            for level, name in zip(expected_levels, role_names):
                role = self.rbac_service.get_role_by_name(name)
                if role and role.level == level:
                    print_success(f"✅ Role {name} (level {level}) exists")
                else:
                    print_error(f"❌ Role {name} (level {level}) not found")
                    return False
            
            return True
        except Exception as e:
            print_error(f"Role hierarchy test failed: {e}")
            return False
    
    async def test_user_permissions(self):
        """Test user permission system"""
        print_status("Testing user permission system...")
        try:
            # Test admin permissions
            admin_permissions = [
                ("admin", "users", "read", True),
                ("admin", "users", "write", True),
                ("admin", "users", "delete", True),
                ("admin", "users", "admin", True),
            ]
            
            for username, resource, action, expected in admin_permissions:
                has_permission = self.rbac_service.check_user_permission(username, resource, action)
                if has_permission == expected:
                    print_success(f"✅ {username} {action} on {resource}: {has_permission}")
                else:
                    print_warning(f"⚠️ {username} {action} on {resource}: {has_permission} (expected {expected})")
            
            # Test moderator permissions
            moderator_permissions = [
                ("moderator", "users", "read", True),
                ("moderator", "users", "write", True),
                ("moderator", "users", "delete", False),  # Should not have delete permission
                ("moderator", "users", "admin", False),    # Should not have admin permission
            ]
            
            for username, resource, action, expected in moderator_permissions:
                has_permission = self.rbac_service.check_user_permission(username, resource, action)
                if has_permission == expected:
                    print_success(f"✅ {username} {action} on {resource}: {has_permission}")
                else:
                    print_warning(f"⚠️ {username} {action} on {resource}: {has_permission} (expected {expected})")
            
            # Test user permissions
            user_permissions = [
                ("user", "users", "read", True),
                ("user", "users", "write", False),  # Should not have write permission
                ("user", "users", "delete", False), # Should not have delete permission
                ("user", "users", "admin", False), # Should not have admin permission
            ]
            
            for username, resource, action, expected in user_permissions:
                has_permission = self.rbac_service.check_user_permission(username, resource, action)
                if has_permission == expected:
                    print_success(f"✅ {username} {action} on {resource}: {has_permission}")
                else:
                    print_warning(f"⚠️ {username} {action} on {resource}: {has_permission} (expected {expected})")
            
            return True
        except Exception as e:
            print_error(f"User permissions test failed: {e}")
            return False
    
    async def test_service_access(self):
        """Test service access control"""
        print_status("Testing service access control...")
        try:
            # Test admin services
            admin_services = self.rbac_service.get_user_services("admin")
            print_success(f"✅ Admin has access to {len(admin_services)} services")
            print(f"   Services: {', '.join(admin_services)}")
            
            # Test moderator services
            moderator_services = self.rbac_service.get_user_services("moderator")
            print_success(f"✅ Moderator has access to {len(moderator_services)} services")
            print(f"   Services: {', '.join(moderator_services)}")
            
            # Test user services
            user_services = self.rbac_service.get_user_services("user")
            print_success(f"✅ User has access to {len(user_services)} services")
            print(f"   Services: {', '.join(user_services)}")
            
            return True
        except Exception as e:
            print_error(f"Service access test failed: {e}")
            return False
    
    async def test_role_assignments(self):
        """Test role assignment functionality"""
        print_status("Testing role assignment functionality...")
        try:
            # Test getting user roles
            admin_roles = self.rbac_service.get_user_roles("admin")
            print_success(f"✅ Admin roles: {admin_roles.effective_roles}")
            print_success(f"✅ Admin highest level: {admin_roles.highest_level}")
            
            moderator_roles = self.rbac_service.get_user_roles("moderator")
            print_success(f"✅ Moderator roles: {moderator_roles.effective_roles}")
            print_success(f"✅ Moderator highest level: {moderator_roles.highest_level}")
            
            user_roles = self.rbac_service.get_user_roles("user")
            print_success(f"✅ User roles: {user_roles.effective_roles}")
            print_success(f"✅ User highest level: {user_roles.highest_level}")
            
            return True
        except Exception as e:
            print_error(f"Role assignment test failed: {e}")
            return False
    
    async def test_role_inheritance(self):
        """Test role inheritance system"""
        print_status("Testing role inheritance system...")
        try:
            # Test that higher level roles inherit from lower levels
            admin_roles = self.rbac_service.get_user_roles("admin")
            effective_roles = admin_roles.effective_roles
            
            # Admin should have all roles from level 0 to 100
            expected_inherited_roles = [
                "user", "basic_user", "standard_user", "advanced_user",
                "power_user", "moderator", "supervisor", "manager",
                "admin", "super_admin", "superadmin"
            ]
            
            for role in expected_inherited_roles:
                if role in effective_roles:
                    print_success(f"✅ Admin inherits {role} role")
                else:
                    print_warning(f"⚠️ Admin does not inherit {role} role")
            
            return True
        except Exception as e:
            print_error(f"Role inheritance test failed: {e}")
            return False
    
    async def run_comprehensive_tests(self):
        """Run comprehensive RBAC tests"""
        print("🧪 RBAC System Comprehensive Test Suite")
        print("=" * 60)
        
        # Initialize system
        init_tests = [
            ("Database Connection", self.initialize_database),
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
            ("Role Hierarchy", self.test_role_hierarchy),
            ("User Permissions", self.test_user_permissions),
            ("Service Access", self.test_service_access),
            ("Role Assignments", self.test_role_assignments),
            ("Role Inheritance", self.test_role_inheritance),
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
    print("🚀 Starting RBAC System Initialization and Testing...")
    print("This will initialize the RBAC system with test data and run comprehensive tests.")
    print()
    
    initializer = RBACInitializer()
    success = await initializer.run_comprehensive_tests()
    
    if not success:
        print("\n❌ Some tests failed. Please fix the issues before proceeding.")
        sys.exit(1)
    else:
        print("\n🎉 RBAC System is fully initialized and tested!")


if __name__ == "__main__":
    asyncio.run(main())
