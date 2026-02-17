#!/usr/bin/env python3
"""
Simple RBAC API Test Runner
Automatically tests all RBAC endpoints with authentication
"""

import json
import requests
import asyncio
import sys
from typing import Dict, Any
from pathlib import Path

# Add parent directory to path to import test_auth_helper
sys.path.insert(0, str(Path(__file__).parent))
from test_auth_helper import AuthTestHelper


class SimpleRBACTester:
    """Simple RBAC API tester with authentication"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.timeout = 30
        self.base_url = "http://localhost:8000/admin/rbac"
        self.created_resources = {'roles': [], 'users': [], 'services': []}
        self.auth_headers = None
    
    async def setup_auth(self):
        """Setup authentication for testing."""
        print("🔐 Setting up authentication...")
        async with AuthTestHelper() as auth:
            self.auth_headers = await auth.get_admin_token()
            print(f"✅ Authentication ready: {list(self.auth_headers.keys())}")
    
    def make_request(self, method: str, endpoint: str, data: Dict = None, params: Dict = None) -> Dict[str, Any]:
        """Make authenticated HTTP request and return response"""
        url = f"{self.base_url}{endpoint}"
        
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
                'success': 200 <= response.status_code < 300
            }
        except Exception as e:
            return {
                'status_code': 0,
                'error': str(e),
                'success': False
            }
    
    def test_endpoint(self, name: str, method: str, endpoint: str, data: Dict = None, params: Dict = None, expected_status: int = None):
        """Test a single endpoint and print result"""
        result = self.make_request(method, endpoint, data, params)
        status = result['status_code']
        success = result['success']
        
        if expected_status:
            success = success and status == expected_status
        
        status_icon = "✅" if success else "❌"
        print(f"{status_icon} {name}: Status {status}")
        
        if not success and result.get('data'):
            print(f"   Response: {json.dumps(result['data'], indent=2)}")
        
        return result
    
    async def run_all_tests(self):
        """Run all RBAC API tests with authentication"""
        print("🚀 Running RBAC API Tests with Authentication")
        print("=" * 50)
        
        # Setup authentication first
        await self.setup_auth()
        
        # Health check
        self.test_endpoint("Health Check", "GET", f"{API_PREFIX}/health", expected_status=200)
        
        # Initialize system
        self.test_endpoint("Initialize System", "POST", "/initialize", expected_status=200)
        
        # Role Management
        print("\n👥 Role Management Tests:")
        role_data = {
            "name": "test_admin_role",
            "level": 80,
            "description": "Test admin role",
            "is_active": True
        }
        
        create_result = self.test_endpoint("Create Role", "POST", "/roles", role_data, expected_status=201)
        
        if create_result['success']:
            role_id = create_result['data'].get('id')
            self.created_resources['roles'].append(role_id)
            
            self.test_endpoint("Get Role by ID", "GET", f"/roles/{role_id}", expected_status=200)
            
            update_data = {"description": "Updated test role"}
            self.test_endpoint("Update Role", "PUT", f"/roles/{role_id}", update_data, expected_status=200)
        
        self.test_endpoint("Get All Roles", "GET", "/roles", expected_status=200)
        self.test_endpoint("Get Roles with Filters", "GET", "/roles", params={"active_only": True}, expected_status=200)
        
        # User Management (Azure AD SSO - users are synced, not created)
        print("\n👤 User Management Tests:")
        # Note: Users are synced from Azure AD SSO, so we test role assignment for existing users
        user_data = {
            "username": "testuser_api",
            "email": "testuser@example.com",
            "full_name": "Test User",
            "is_active": True
        }
        
        # Test role assignment for existing user (assuming user exists from Azure AD sync)
        self.test_endpoint("Get User Roles", "GET", f"/users/{user_data['username']}/roles", expected_status=200)
        
        assign_data = {
            "username": user_data['username'],
            "role_name": "test_admin_role",
            "granted_by": "system"
        }
        self.test_endpoint("Assign Role to User", "POST", "/users/assign-role", assign_data)
        
        # Service Management
        print("\n🔧 Service Management Tests:")
        service_data = {
            "name": "test_api_service",
            "description": "Test API service",
            "service_type": "api",
            "is_active": True
        }
        
        create_service_result = self.test_endpoint("Create Service", "POST", "/services", service_data, expected_status=201)
        
        if create_service_result['success']:
            service_id = create_service_result['data'].get('id')
            self.created_resources['services'].append(service_id)
        
        self.test_endpoint("Get All Services", "GET", "/services", expected_status=200)
        self.test_endpoint("Get Services with Filters", "GET", "/services", params={"service_type": "api"}, expected_status=200)
        
        # Permission Checking
        print("\n🔐 Permission Checking Tests:")
        permission_data = {
            "username": "testuser_api",
            "resource": "users",
            "action": "read"
        }
        self.test_endpoint("Check User Permission", "POST", "/permissions/check", permission_data)
        self.test_endpoint("Get User Services", "GET", "/users/testuser_api/services")
        
        # Role Hierarchy
        print("\n📊 Role Hierarchy Tests:")
        self.test_endpoint("Get Role Hierarchy", "GET", "/hierarchy", expected_status=200)
        
        # Bulk Operations
        print("\n📦 Bulk Operations Tests:")
        bulk_data = {
            "username": "testuser_api",
            "role_names": ["test_admin_role"],
            "granted_by": "system"
        }
        self.test_endpoint("Bulk Assign Roles", "POST", "/users/bulk-assign-roles", bulk_data)
        
        # Statistics
        print("\n📈 Statistics Tests:")
        self.test_endpoint("Get RBAC Statistics", "GET", "/stats", expected_status=200)
        
        # Error Scenarios
        print("\n⚠️ Error Scenario Tests:")
        self.test_endpoint("Get Non-existent Role", "GET", "/roles/99999", expected_status=404)
        
        invalid_role = {"name": "", "level": 150}
        self.test_endpoint("Create Invalid Role", "POST", "/roles", invalid_role, expected_status=400)
        
        # Cleanup
        print("\n🧹 Cleanup:")
        for role_id in self.created_resources['roles']:
            self.test_endpoint(f"Delete Role {role_id}", "DELETE", f"/roles/{role_id}", expected_status=204)
        
        print("\n" + "=" * 50)
        print("✅ RBAC API tests completed!")


async def main():
    """Main test runner"""
    tester = SimpleRBACTester()
    await tester.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
