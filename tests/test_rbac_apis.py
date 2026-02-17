#!/usr/bin/env python3
"""
Comprehensive RBAC API Test Suite
Tests all RBAC endpoints with authentication
"""

import json
import sys
import os
import asyncio
from typing import Dict, Any, List
import requests
from datetime import datetime
from pathlib import Path

# Add parent directory to path to import test_auth_helper
sys.path.insert(0, str(Path(__file__).parent))
from test_auth_helper import AuthTestHelper

# Test configuration
BASE_URL = "http://localhost:8000"
RBAC_BASE_URL = f"{BASE_URL}/admin/rbac"

class RBACAPITester:
    """Comprehensive RBAC API tester with authentication"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.timeout = 30
        self.test_data = {}
        self.created_resources = {
            'roles': [],
            'users': [],
            'services': [],
            'permissions': []
        }
        self.auth_headers = None
    
    async def setup_auth(self):
        """Setup authentication for testing."""
        print("🔐 Setting up authentication...")
        async with AuthTestHelper() as auth:
            self.auth_headers = await auth.get_admin_token()
            print(f"✅ Authentication ready")
    
    def make_request(self, method: str, endpoint: str, data: Dict = None, params: Dict = None) -> Dict[str, Any]:
        """Make authenticated HTTP request and return response"""
        url = f"{RBAC_BASE_URL}{endpoint}"
        
        # Add authentication headers
        headers = self.auth_headers.copy() if self.auth_headers else {}
        headers['Content-Type'] = 'application/json'
        
        try:
            if method.upper() == "GET":
                response = self.session.get(url, params=params, headers=headers)
            elif method.upper() == "POST":
                response = self.session.post(url, json=data, headers=headers)
            elif method.upper() == "PUT":
                response = self.session.put(url, json=data, headers=headers)
            elif method.upper() == "DELETE":
                response = self.session.delete(url, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            return {
                'status_code': response.status_code,
                'data': response.json() if response.content else {},
                'headers': dict(response.headers)
            }
        except Exception as e:
            return {
                'status_code': 0,
                'error': str(e),
                'data': {}
            }
    
    def print_result(self, test_name: str, result: Dict[str, Any], expected_status: int = None):
        """Print test result in a formatted way"""
        status = result.get('status_code', 0)
        data = result.get('data', {})
        error = result.get('error')
        
        if error:
            print(f"❌ {test_name}: ERROR - {error}")
        elif expected_status and status != expected_status:
            print(f"❌ {test_name}: FAILED - Expected {expected_status}, got {status}")
            if data:
                print(f"   Response: {json.dumps(data, indent=2)}")
        else:
            print(f"✅ {test_name}: PASSED - Status {status}")
            if data and isinstance(data, dict) and len(data) > 0:
                print(f"   Response: {json.dumps(data, indent=2)}")
    
    def test_health_check(self):
        """Test RBAC health check endpoint"""
        print("\n🏥 Testing Health Check...")
        result = self.make_request("GET", f"{API_PREFIX}/health")
        self.print_result("Health Check", result, 200)
        return result
    
    def test_initialize_system(self):
        """Test RBAC system initialization"""
        print("\n🚀 Testing System Initialization...")
        result = self.make_request("POST", "/initialize")
        self.print_result("Initialize RBAC System", result, 200)
        return result
    
    def test_role_management(self):
        """Test all role management endpoints"""
        print("\n👥 Testing Role Management...")
        
        # Test data
        test_role = {
            "name": "test_admin",
            "level": 80,
            "description": "Test admin role for API testing",
            "is_active": True
        }
        
        # Create role
        result = self.make_request("POST", "/roles", test_role)
        self.print_result("Create Role", result, 201)
        
        if result['status_code'] == 201:
            role_id = result['data'].get('id')
            self.created_resources['roles'].append(role_id)
            self.test_data['role_id'] = role_id
            
            # Get role by ID
            result = self.make_request("GET", f"/roles/{role_id}")
            self.print_result("Get Role by ID", result, 200)
            
            # Update role
            update_data = {
                "description": "Updated test admin role",
                "level": 85
            }
            result = self.make_request("PUT", f"/roles/{role_id}", update_data)
            self.print_result("Update Role", result, 200)
        
        # Get all roles
        result = self.make_request("GET", "/roles")
        self.print_result("Get All Roles", result, 200)
        
        # Get roles with filters
        result = self.make_request("GET", "/roles", params={"active_only": True, "level": 80})
        self.print_result("Get Roles with Filters", result, 200)
    
    def test_user_management(self):
        """Test user management endpoints"""
        print("\n👤 Testing User Management...")
        
        # Test data
        test_user = {
            "username": "testuser123",
            "email": "testuser@example.com",
            "full_name": "Test User",
            "is_active": True
        }
        
        # Create user
        result = self.make_request("POST", "/users", test_user)
        self.print_result("Create User", result, 201)
        
        if result['status_code'] == 201:
            user_id = result['data'].get('id')
            self.created_resources['users'].append(user_id)
            self.test_data['username'] = test_user['username']
            
            # Get user roles
            result = self.make_request("GET", f"/users/{test_user['username']}/roles")
            self.print_result("Get User Roles", result, 200)
            
            # Assign role to user
            assign_data = {
                "username": test_user['username'],
                "role_name": "test_admin",
                "granted_by": "system"
            }
            result = self.make_request("POST", "/users/assign-role", assign_data)
            self.print_result("Assign Role to User", result, 200)
            
            # Get user roles again to verify assignment
            result = self.make_request("GET", f"/users/{test_user['username']}/roles")
            self.print_result("Get User Roles After Assignment", result, 200)
            
            # Remove role from user
            remove_data = {
                "username": test_user['username'],
                "role_name": "test_admin"
            }
            result = self.make_request("DELETE", "/users/remove-role", remove_data)
            self.print_result("Remove Role from User", result, 200)
    
    def test_service_management(self):
        """Test service management endpoints"""
        print("\n🔧 Testing Service Management...")
        
        # Test data
        test_service = {
            "name": "test_api_service",
            "description": "Test API service for RBAC testing",
            "service_type": "api",
            "is_active": True
        }
        
        # Create service
        result = self.make_request("POST", "/services", test_service)
        self.print_result("Create Service", result, 201)
        
        if result['status_code'] == 201:
            service_id = result['data'].get('id')
            self.created_resources['services'].append(service_id)
            
            # Get all services
            result = self.make_request("GET", "/services")
            self.print_result("Get All Services", result, 200)
            
            # Get services with filters
            result = self.make_request("GET", "/services", params={"service_type": "api", "active_only": True})
            self.print_result("Get Services with Filters", result, 200)
            
            # Assign services to role
            assign_service_data = {
                "role_name": "test_admin",
                "service_names": ["test_api_service"]
            }
            result = self.make_request("POST", "/roles/assign-services", assign_service_data)
            self.print_result("Assign Services to Role", result, 200)
    
    def test_permission_checking(self):
        """Test permission checking endpoints"""
        print("\n🔐 Testing Permission Checking...")
        
        # Test permission check
        permission_data = {
            "username": "testuser123",
            "resource": "users",
            "action": "read"
        }
        
        result = self.make_request("POST", "/permissions/check", permission_data)
        self.print_result("Check User Permission", result, 200)
        
        # Get user services
        result = self.make_request("GET", "/users/testuser123/services")
        self.print_result("Get User Services", result, 200)
    
    def test_role_hierarchy(self):
        """Test role hierarchy endpoints"""
        print("\n📊 Testing Role Hierarchy...")
        
        result = self.make_request("GET", "/hierarchy")
        self.print_result("Get Role Hierarchy", result, 200)
    
    def test_bulk_operations(self):
        """Test bulk operations"""
        print("\n📦 Testing Bulk Operations...")
        
        # Bulk role assignment
        bulk_data = {
            "username": "testuser123",
            "role_names": ["test_admin"],
            "granted_by": "system"
        }
        
        result = self.make_request("POST", "/users/bulk-assign-roles", bulk_data)
        self.print_result("Bulk Assign Roles", result, 200)
    
    def test_statistics(self):
        """Test statistics and reporting endpoints"""
        print("\n📈 Testing Statistics...")
        
        result = self.make_request("GET", "/stats")
        self.print_result("Get RBAC Statistics", result, 200)
    
    def test_error_scenarios(self):
        """Test error scenarios and edge cases"""
        print("\n⚠️ Testing Error Scenarios...")
        
        # Test non-existent role
        result = self.make_request("GET", "/roles/99999")
        self.print_result("Get Non-existent Role", result, 404)
        
        # Test invalid role creation
        invalid_role = {
            "name": "",  # Invalid empty name
            "level": 150,  # Invalid level
            "description": "Invalid role"
        }
        result = self.make_request("POST", "/roles", invalid_role)
        self.print_result("Create Invalid Role", result, 400)
        
        # Test permission check for non-existent user
        invalid_permission = {
            "username": "nonexistent_user",
            "resource": "users",
            "action": "read"
        }
        result = self.make_request("POST", "/permissions/check", invalid_permission)
        self.print_result("Check Permission for Non-existent User", result, 404)
    
    def cleanup_resources(self):
        """Clean up created test resources"""
        print("\n🧹 Cleaning up test resources...")
        
        # Delete created roles
        for role_id in self.created_resources['roles']:
            result = self.make_request("DELETE", f"/roles/{role_id}")
            print(f"   Deleted role {role_id}: Status {result['status_code']}")
        
        # Note: Users and services cleanup would need additional endpoints
        print("   Cleanup completed (some resources may need manual cleanup)")
    
    async def run_all_tests(self):
        """Run all RBAC API tests with authentication"""
        print("🚀 Starting Comprehensive RBAC API Tests with Authentication")
        print("=" * 60)
        
        # Setup authentication first
        await self.setup_auth()
        
        try:
            # Core functionality tests
            self.test_health_check()
            self.test_initialize_system()
            self.test_role_management()
            self.test_user_management()
            self.test_service_management()
            self.test_permission_checking()
            self.test_role_hierarchy()
            self.test_bulk_operations()
            self.test_statistics()
            
            # Error scenario tests
            self.test_error_scenarios()
            
            print("\n" + "=" * 60)
            print("✅ All RBAC API tests completed!")
            
        except Exception as e:
            print(f"\n❌ Test suite failed with error: {e}")
            import traceback
            traceback.print_exc()
        
        finally:
            # Cleanup
            self.cleanup_resources()


async def main():
    """Main test runner"""
    print("RBAC API Test Suite with Authentication")
    print("Make sure your FastAPI server is running on http://localhost:8000")
    print("")
    
    tester = RBACAPITester()
    await tester.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
