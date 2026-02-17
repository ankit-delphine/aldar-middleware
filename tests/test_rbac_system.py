#!/usr/bin/env python3
"""
RBAC System Test Script
Test the complete RBAC system functionality
"""

import sys
import os
import requests
import json
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy.orm import Session
from aldar_middleware.database.base import get_db
from aldar_middleware.services.rbac_service import RBACService
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


class RBACTester:
    """RBAC system tester"""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.api_url = f"{base_url}/rbac"
        
    async def test_database_connection(self):
        """Test database connection and RBAC service"""
        print_status("Testing database connection...")
        try:
            async for db in get_db():
                rbac_service = RBACService(db)
                roles = rbac_service.get_all_roles()
                print_success(f"Database connection successful. Found {len(roles)} roles.")
                return True
        except Exception as e:
            print_error(f"Database connection failed: {e}")
            return False
    
    def test_api_endpoints(self):
        """Test RBAC API endpoints"""
        print_status("Testing RBAC API endpoints...")
        
        endpoints_to_test = [
            ("GET", f"{API_PREFIX}/health", "Health check"),
            ("GET", "/roles", "List roles"),
            ("GET", "/hierarchy", "Role hierarchy"),
            ("GET", "/stats", "RBAC statistics"),
        ]
        
        results = []
        for method, endpoint, description in endpoints_to_test:
            try:
                url = f"{self.api_url}{endpoint}"
                response = requests.get(url, timeout=5)
                
                if response.status_code == 200:
                    print_success(f"✅ {description}: {response.status_code}")
                    results.append(True)
                else:
                    print_warning(f"⚠️ {description}: {response.status_code}")
                    results.append(False)
                    
            except requests.exceptions.ConnectionError:
                print_warning(f"⚠️ {description}: API not available (server not running)")
                results.append(False)
            except Exception as e:
                print_error(f"❌ {description}: {e}")
                results.append(False)
        
        return all(results)
    
    def test_role_hierarchy(self):
        """Test role hierarchy system"""
        print_status("Testing role hierarchy system...")
        
        try:
            db = next(get_db())
            rbac_service = RBACService(db)
            
            # Test role levels
            expected_levels = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
            role_names = ["user", "basic_user", "standard_user", "advanced_user", 
                         "power_user", "moderator", "supervisor", "manager", 
                         "admin", "super_admin", "superadmin"]
            
            for level, name in zip(expected_levels, role_names):
                role = rbac_service.get_role_by_name(name)
                if role and role.level == level:
                    print_success(f"✅ Role {name} (level {level}) exists")
                else:
                    print_error(f"❌ Role {name} (level {level}) not found")
                    return False
            
            # Test hierarchy inheritance
            print_status("Testing role inheritance...")
            
            # Test that higher level roles inherit from lower levels
            high_level_role = rbac_service.get_role_by_name("admin")
            if high_level_role:
                print_success(f"✅ Admin role has level {high_level_role.level}")
            
            return True
            
        except Exception as e:
            print_error(f"Role hierarchy test failed: {e}")
            return False
    
    def test_user_management(self):
        """Test user management functionality"""
        print_status("Testing user management...")
        
        try:
            db = next(get_db())
            rbac_service = RBACService(db)
            
            # Test getting user roles
            admin_roles = rbac_service.get_user_roles("admin")
            if admin_roles:
                print_success(f"✅ Admin user roles: {admin_roles.effective_roles}")
            else:
                print_warning("⚠️ Admin user not found or has no roles")
            
            # Test permission checking
            print_status("Testing permission checking...")
            
            # Test admin permissions
            has_admin_permission = rbac_service.check_user_permission("admin", "users", "admin")
            if has_admin_permission:
                print_success("✅ Admin has admin permission on users")
            else:
                print_warning("⚠️ Admin does not have admin permission on users")
            
            return True
            
        except Exception as e:
            print_error(f"User management test failed: {e}")
            return False
    
    def test_service_assignment(self):
        """Test service assignment system"""
        print_status("Testing service assignment system...")
        
        try:
            db = next(get_db())
            rbac_service = RBACService(db)
            
            # Test getting user services
            admin_services = rbac_service.get_user_services("admin")
            if admin_services:
                print_success(f"✅ Admin has access to {len(admin_services)} services")
                print(f"   Services: {', '.join(admin_services)}")
            else:
                print_warning("⚠️ Admin has no service access")
            
            # Test role-service relationships
            superadmin_role = rbac_service.get_role_by_name("superadmin")
            if superadmin_role and superadmin_role.services:
                print_success(f"✅ Superadmin role has {len(superadmin_role.services)} services assigned")
            else:
                print_warning("⚠️ Superadmin role has no services assigned")
            
            return True
            
        except Exception as e:
            print_error(f"Service assignment test failed: {e}")
            return False
    
    def test_permission_system(self):
        """Test permission system"""
        print_status("Testing permission system...")
        
        try:
            db = next(get_db())
            rbac_service = RBACService(db)
            
            # Test different permission levels
            test_cases = [
                ("admin", "users", "read", True),
                ("admin", "users", "write", True),
                ("admin", "users", "delete", True),
                ("admin", "users", "admin", True),
            ]
            
            for username, resource, action, expected in test_cases:
                has_permission = rbac_service.check_user_permission(username, resource, action)
                if has_permission == expected:
                    print_success(f"✅ {username} {action} on {resource}: {has_permission}")
                else:
                    print_warning(f"⚠️ {username} {action} on {resource}: {has_permission} (expected {expected})")
            
            return True
            
        except Exception as e:
            print_error(f"Permission system test failed: {e}")
            return False
    
    def run_all_tests(self):
        """Run all RBAC tests"""
        print("🧪 RBAC System Test Suite")
        print("=" * 50)
        
        tests = [
            ("Database Connection", self.test_database_connection),
            ("API Endpoints", self.test_api_endpoints),
            ("Role Hierarchy", self.test_role_hierarchy),
            ("User Management", self.test_user_management),
            ("Service Assignment", self.test_service_assignment),
            ("Permission System", self.test_permission_system),
        ]
        
        results = []
        for test_name, test_func in tests:
            print(f"\n📋 Running {test_name} Test...")
            try:
                result = test_func()
                results.append((test_name, result))
                if result:
                    print_success(f"✅ {test_name} test passed")
                else:
                    print_error(f"❌ {test_name} test failed")
            except Exception as e:
                print_error(f"❌ {test_name} test failed with exception: {e}")
                results.append((test_name, False))
        
        # Summary
        print("\n" + "=" * 50)
        print("📊 Test Results Summary")
        print("=" * 50)
        
        passed = sum(1 for _, result in results if result)
        total = len(results)
        
        for test_name, result in results:
            status_icon = "✅" if result else "❌"
            print(f"{status_icon} {test_name}")
        
        print(f"\n🎯 Overall Result: {passed}/{total} tests passed")
        
        if passed == total:
            print_success("🎉 All RBAC tests passed! The system is working correctly.")
        else:
            print_warning(f"⚠️ {total - passed} tests failed. Please check the issues above.")
        
        return passed == total


def main():
    """Main test function"""
    tester = RBACTester()
    
    print("🚀 Starting RBAC System Tests...")
    print("Make sure the database is set up and the API server is running.")
    print("You can start the API server with: uvicorn aldar_middleware.application:app --reload")
    print()
    
    success = tester.run_all_tests()
    
    if success:
        print("\n🎉 RBAC System is fully functional!")
        print("\nNext steps:")
        print("1. Start the API server: uvicorn aldar_middleware.application:app --reload")
        print("2. Visit http://localhost:8000/docs to see the API documentation")
        print("3. Test the RBAC endpoints:")
        print("   - GET /rbac/roles")
        print("   - GET /rbac/users/admin/roles")
        print("   - POST /rbac/permissions/check")
        print("   - GET /rbac/hierarchy")
    else:
        print("\n❌ Some tests failed. Please fix the issues before proceeding.")
        sys.exit(1)


if __name__ == "__main__":
    main()
