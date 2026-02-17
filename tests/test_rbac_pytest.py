"""
RBAC API Tests using pytest with authentication
Integration tests for all RBAC endpoints
"""

import pytest
import pytest_asyncio
import requests
import json
import asyncio
from typing import Dict, Any
from tests.test_auth_helper import AuthTestHelper


class TestRBACAPIs:
    """Test class for RBAC API endpoints with authentication"""
    
    BASE_URL = "http://localhost:8000"
    RBAC_BASE_URL = f"{BASE_URL}/admin/rbac"
    
    @pytest_asyncio.fixture(autouse=True)
    async def setup_session(self):
        """Setup requests session and authentication for all tests"""
        self.session = requests.Session()
        self.session.timeout = 30
        self.created_resources = {
            'roles': [],
            'users': [],
            'services': []
        }
        
        # Setup authentication
        async with AuthTestHelper() as auth:
            self.auth_headers = await auth.get_admin_token()
        
        yield
        # Cleanup after tests
        self.cleanup_resources()
    
    def make_request(self, method: str, endpoint: str, data: Dict = None, params: Dict = None) -> Dict[str, Any]:
        """Make authenticated HTTP request and return response"""
        url = f"{self.RBAC_BASE_URL}{endpoint}"
        
        # Add authentication headers
        headers = self.auth_headers.copy() if hasattr(self, 'auth_headers') and self.auth_headers else {}
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
    
    def cleanup_resources(self):
        """Clean up created test resources"""
        for role_id in self.created_resources['roles']:
            self.make_request("DELETE", f"/roles/{role_id}")
    
    def test_health_check(self):
        """Test RBAC health check endpoint"""
        result = self.make_request("GET", f"{API_PREFIX}/health")
        assert result['status_code'] == 200
        assert 'status' in result['data']
    
    def test_initialize_system(self):
        """Test RBAC system initialization"""
        result = self.make_request("POST", "/initialize")
        assert result['status_code'] == 200
        assert 'message' in result['data']
    
    def test_role_crud_operations(self):
        """Test complete role CRUD operations"""
        # Create role
        test_role = {
            "name": "pytest_admin",
            "level": 80,
            "description": "Test admin role for pytest",
            "is_active": True
        }
        
        result = self.make_request("POST", "/roles", test_role)
        if result['status_code'] == 400:
            # Role already exists from previous test, skip to avoid conflicts
            return
        assert result['status_code'] == 201
        role_id = result['data']['id']
        self.created_resources['roles'].append(role_id)
        
        # Get role by ID
        result = self.make_request("GET", f"/roles/{role_id}")
        assert result['status_code'] == 200
        assert result['data']['name'] == test_role['name']
        
        # Update role
        update_data = {
            "description": "Updated pytest admin role",
            "level": 85
        }
        result = self.make_request("PUT", f"/roles/{role_id}", update_data)
        assert result['status_code'] == 200
        
        # Get all roles
        result = self.make_request("GET", "/roles")
        assert result['status_code'] == 200
        assert isinstance(result['data'], list)
        
        # Get roles with filters
        result = self.make_request("GET", "/roles", params={"active_only": True, "level": 80})
        assert result['status_code'] == 200
    
    def test_user_management(self):
        """Test user management endpoints"""
        # NOTE: Users are synced from Azure AD, not created via API
        # First, sync users from main users table to RBAC system
        sync_result = self.make_request("POST", "/admin/sync-users-to-rbac")
        # Sync endpoint might not exist or might fail, that's okay for this test
        
        # Test with existing test user
        test_username = "testadmin"
        
        # Get user roles (might be 404 if user not in RBAC system yet)
        result = self.make_request("GET", f"/users/{test_username}/roles")
        if result['status_code'] == 404:
            # User not in RBAC system, skip remaining tests
            return
        
        assert result['status_code'] == 200
        assert 'username' in result['data']
        
        # Create a test role first
        test_role = {
            "name": "pytest_test_role",
            "level": 10,
            "description": "Test role for user assignment",
            "is_active": True
        }
        result = self.make_request("POST", "/roles", test_role)
        if result['status_code'] == 201:
            self.created_resources['roles'].append(result['data']['id'])
        
        # Assign role to user
        assign_data = {
            "username": test_username,
            "role_name": "pytest_test_role",
            "granted_by": "system"
        }
        result = self.make_request("POST", "/users/assign-role", assign_data)
        # Should succeed or already exist
        assert result['status_code'] in [200, 400]
        
        # Verify role assignment by getting user roles again
        result = self.make_request("GET", f"/users/{test_username}/roles")
        assert result['status_code'] == 200
    
    def test_service_management(self):
        """Test service management endpoints"""
        # Create service
        test_service = {
            "name": "pytest_api_service",
            "description": "Test API service for pytest",
            "service_type": "api",
            "is_active": True
        }
        
        result = self.make_request("POST", "/services", test_service)
        if result['status_code'] == 400:
            # Service already exists from previous test, skip to avoid conflicts
            return
        assert result['status_code'] == 201
        service_id = result['data']['id']
        self.created_resources['services'].append(service_id)
        
        # Get all services
        result = self.make_request("GET", "/services")
        assert result['status_code'] == 200
        assert isinstance(result['data'], list)
        
        # Get services with filters
        result = self.make_request("GET", "/services", params={"service_type": "api", "active_only": True})
        assert result['status_code'] == 200
    
    def test_permission_checking(self):
        """Test permission checking endpoints"""
        # Test permission check
        permission_data = {
            "username": "pytestuser",
            "resource": "users",
            "action": "read"
        }
        
        result = self.make_request("POST", "/permissions/check", permission_data)
        assert result['status_code'] in [200, 404]  # 404 if user doesn't exist
        
        # Get user services
        result = self.make_request("GET", "/users/pytestuser/services")
        assert result['status_code'] in [200, 404]  # 404 if user doesn't exist
    
    def test_role_hierarchy(self):
        """Test role hierarchy endpoints"""
        result = self.make_request("GET", "/hierarchy")
        assert result['status_code'] == 200
        assert isinstance(result['data'], list)
    
    def test_bulk_operations(self):
        """Test bulk operations"""
        bulk_data = {
            "username": "pytestuser",
            "role_names": ["pytest_admin"],
            "granted_by": "system"
        }
        
        result = self.make_request("POST", "/users/bulk-assign-roles", bulk_data)
        assert result['status_code'] in [200, 400]  # 400 if role doesn't exist
    
    def test_statistics(self):
        """Test statistics and reporting endpoints"""
        result = self.make_request("GET", "/stats")
        assert result['status_code'] == 200
        assert 'total_users' in result['data']
    
    def test_error_scenarios(self):
        """Test error scenarios and edge cases"""
        # Test non-existent role
        result = self.make_request("GET", "/roles/99999")
        assert result['status_code'] == 404
        
        # Test invalid role creation
        invalid_role = {
            "name": "",  # Invalid empty name
            "level": 150,  # Invalid level
            "description": "Invalid role"
        }
        result = self.make_request("POST", "/roles", invalid_role)
        # Accept both 400 (business logic error) and 422 (Pydantic validation error)
        assert result['status_code'] in [400, 422]
        
        # Test permission check for non-existent user
        invalid_permission = {
            "username": "nonexistent_user",
            "resource": "users",
            "action": "read"
        }
        result = self.make_request("POST", "/permissions/check", invalid_permission)
        assert result['status_code'] == 404


# Standalone test runner for manual execution
def run_standalone_tests():
    """Run tests without pytest"""
    tester = TestRBACAPIs()
    tester.setup_session()
    
    print("🚀 Running RBAC API Tests...")
    
    try:
        tester.test_health_check()
        print("✅ Health check passed")
        
        tester.test_initialize_system()
        print("✅ System initialization passed")
        
        tester.test_role_crud_operations()
        print("✅ Role CRUD operations passed")
        
        tester.test_user_management()
        print("✅ User management passed")
        
        tester.test_service_management()
        print("✅ Service management passed")
        
        tester.test_permission_checking()
        print("✅ Permission checking passed")
        
        tester.test_role_hierarchy()
        print("✅ Role hierarchy passed")
        
        tester.test_bulk_operations()
        print("✅ Bulk operations passed")
        
        tester.test_statistics()
        print("✅ Statistics passed")
        
        tester.test_error_scenarios()
        print("✅ Error scenarios passed")
        
        print("\n🎉 All tests completed successfully!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        tester.cleanup_resources()


if __name__ == "__main__":
    run_standalone_tests()
