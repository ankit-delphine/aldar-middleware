#!/usr/bin/env python3
"""
RBAC API Test Script
Plain Python script that calls each RBAC API endpoint sequentially
Uses requests library to make HTTP calls to the running server
"""

import requests
import json
import time
import uuid
from typing import Dict, Any, Optional
from datetime import datetime

# ============================================================================
# CONFIGURATION - Load from test_config.py or environment variables
# ============================================================================
import os
import sys
from pathlib import Path

# Try to load from test_config.py (same directory)
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from test_config import TEST_USERNAME, TEST_JWT_TOKEN, TEST_DB_NAME
    USERNAME = TEST_USERNAME
    JWT_TOKEN = TEST_JWT_TOKEN
    DATABASE = TEST_DB_NAME
except ImportError:
    # Fallback to defaults
    USERNAME = ""
    JWT_TOKEN = ""  # MUST BE SET
    DATABASE = "aiq_test"

# Override with environment variables if set
BASE_URL = os.getenv('RBAC_TEST_BASE_URL', 'http://localhost:8080')
USERNAME = os.getenv('RBAC_TEST_USERNAME', USERNAME)
JWT_TOKEN = os.getenv('RBAC_TEST_JWT_TOKEN', JWT_TOKEN)
DATABASE = os.getenv('RBAC_TEST_DATABASE', DATABASE)

# ============================================================================
# Helper Functions
# ============================================================================

def generate_unique_name(prefix: str) -> str:
    """Generate a unique name using prefix, timestamp, and UUID"""
    timestamp = int(time.time() * 1000)
    unique_id = str(uuid.uuid4())[:8]
    return f"{prefix}_{timestamp}_{unique_id}"


def make_request(method: str, url: str, headers: Dict, data: Optional[Dict] = None, params: Optional[Dict] = None, expected_status: int = 200, api_name: str = None) -> tuple:
    """Make HTTP request and return (status_code, response_data)"""
    print(f"\n{'='*80}")
    print(f"[{method}] {url}")
    if params:
        print(f"Params: {params}")
    if data:
        print(f"Body: {json.dumps(data, indent=2)}")
    
    try:
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            json=data if data else None,
            params=params,
            timeout=30
        )
        
        status_code = response.status_code
        print(f"Status: {status_code}")
        
        try:
            result = response.json()
            print(f"Response: {json.dumps(result, indent=2)}")
            response_data = result
        except:
            print(f"Response (text): {response.text}")
            response_data = {"status": status_code, "text": response.text}
        
        # Assert status code
        if status_code == expected_status:
            print(f"✅ Status code assertion PASSED: {status_code} == {expected_status}")
        else:
            print(f"❌ Status code assertion FAILED: Expected {expected_status}, got {status_code}")
            raise AssertionError(f"Expected status {expected_status}, got {status_code}")
        
        return (status_code, response_data)
    except AssertionError:
        raise
    except Exception as e:
        print(f"ERROR: {e}")
        raise AssertionError(f"Request failed: {e}")


# ============================================================================
# Main Test Execution
# ============================================================================

def main():
    """Execute all RBAC API tests sequentially"""
    
    if not JWT_TOKEN:
        print("ERROR: JWT_TOKEN is not set. Please update it in the script.")
        return
    
    # Setup headers
    headers = {
        "Authorization": f"Bearer {JWT_TOKEN}",
        "Content-Type": "application/json"
    }
    
    print(f"\n{'='*80}")
    print(f"RBAC API Test Script")
    print(f"Username: {USERNAME}")
    print(f"Database: {DATABASE}")
    print(f"Base URL: {BASE_URL}")
    print(f"{'='*80}")
    
    # Store created IDs for cleanup
    created_roles = []
    created_services = []
    created_role_id = None
    
    # Track API test results
    api_results = {
        "passed": [],
        "failed": []
    }
    
    def track_api_result(api_name: str, status_code: int, expected_status: int = 200):
        """Track API test result"""
        if status_code == expected_status:
            api_results["passed"].append(api_name)
        else:
            api_results["failed"].append((api_name, status_code, expected_status))
    
    try:
        # ====================================================================
        # 1. ROLE MANAGEMENT APIs
        # ====================================================================
        print(f"\n{'#'*80}")
        print("# 1. ROLE MANAGEMENT APIs")
        print(f"{'#'*80}")
        
        # 1.1 Create a role
        role_name = generate_unique_name("TestRole")
        role_data = {
            "name": role_name,
            "description": "Test role description",
            "is_active": True,
            "service_names": []
        }
        status, response = make_request("POST", f"{BASE_URL}/admin/access/roles", headers, role_data, expected_status=201, api_name="1.1 Create Role")
        if response.get("success") and response.get("data"):
            created_role_id = response["data"]["id"]
            created_roles.append(created_role_id)
            print(f"✅ Created role: {role_name} (ID: {created_role_id})")
        track_api_result("1.1 Create Role", status)
        time.sleep(0.5)
        
        # 1.2 Get all roles
        status, response = make_request("GET", f"{BASE_URL}/admin/access/roles", headers, api_name="1.2 Get All Roles")
        track_api_result("1.2 Get All Roles", status)
        print(f"✅ Retrieved all roles")
        time.sleep(0.5)
        
        # 1.3 Get roles with filters
        status, response = make_request("GET", f"{BASE_URL}/admin/access/roles", headers, params={"active_status": "active"}, api_name="1.3 Get Active Roles")
        track_api_result("1.3 Get Active Roles", status)
        print(f"✅ Retrieved active roles")
        time.sleep(0.5)
        
        status, response = make_request("GET", f"{BASE_URL}/admin/access/roles", headers, params={"name": role_name}, api_name="1.4 Get Roles By Name")
        track_api_result("1.4 Get Roles By Name", status)
        print(f"✅ Retrieved roles filtered by name")
        time.sleep(0.5)
        
        # 1.5 Get role by ID
        if created_role_id:
            status, response = make_request("GET", f"{BASE_URL}/admin/access/roles/{created_role_id}", headers, api_name="1.5 Get Role By ID")
            track_api_result("1.5 Get Role By ID", status)
            print(f"✅ Retrieved role by ID")
            time.sleep(0.5)
        
        # 1.6 Update role
        if created_role_id:
            update_data = {
                "description": "Updated description",
                "is_active": True
            }
            status, response = make_request("PUT", f"{BASE_URL}/admin/access/roles/{created_role_id}", headers, update_data, api_name="1.6 Update Role")
            track_api_result("1.6 Update Role", status)
            print(f"✅ Updated role")
            time.sleep(0.5)
        
        # 1.7 Activate role
        if created_role_id:
            status, response = make_request("POST", f"{BASE_URL}/admin/access/roles/{created_role_id}/activate", headers, api_name="1.7 Activate Role")
            track_api_result("1.7 Activate Role", status)
            print(f"✅ Activated role")
            time.sleep(0.5)
        
        # 1.8 Deactivate role
        if created_role_id:
            status, response = make_request("POST", f"{BASE_URL}/admin/access/roles/{created_role_id}/deactivate", headers, api_name="1.8 Deactivate Role")
            track_api_result("1.8 Deactivate Role", status)
            print(f"✅ Deactivated role")
            time.sleep(0.5)
        
        # 1.9 Activate role again (for cleanup)
        if created_role_id:
            status, response = make_request("POST", f"{BASE_URL}/admin/access/roles/{created_role_id}/activate", headers, api_name="1.9 Activate Role (Cleanup)")
            track_api_result("1.9 Activate Role (Cleanup)", status)
            time.sleep(0.5)
        
        # ====================================================================
        # 2. SERVICE MANAGEMENT APIs
        # ====================================================================
        print(f"\n{'#'*80}")
        print("# 2. SERVICE MANAGEMENT APIs")
        print(f"{'#'*80}")
        
        # 2.1 Create a service
        service_name = generate_unique_name("TestService")
        service_data = {
            "name": service_name,
            "description": "Test service description",
            "service_type": "api",
            "is_active": True
        }
        status, response = make_request("POST", f"{BASE_URL}/admin/access/services", headers, service_data, expected_status=201, api_name="2.1 Create Service")
        track_api_result("2.1 Create Service", status)
        if response.get("success") and response.get("data"):
            created_service_id = response["data"]["id"]
            created_services.append(created_service_id)
            print(f"✅ Created service: {service_name} (ID: {created_service_id})")
        time.sleep(0.5)
        
        # 2.2 Get all services
        status, response = make_request("GET", f"{BASE_URL}/admin/access/services", headers, api_name="2.2 Get All Services")
        track_api_result("2.2 Get All Services", status)
        print(f"✅ Retrieved all services")
        time.sleep(0.5)
        
        # 2.3 Get services with filters
        status, response = make_request("GET", f"{BASE_URL}/admin/access/services", headers, params={"active_status": "active"}, api_name="2.3 Get Active Services")
        track_api_result("2.3 Get Active Services", status)
        print(f"✅ Retrieved active services")
        time.sleep(0.5)
        
        status, response = make_request("GET", f"{BASE_URL}/admin/access/services", headers, params={"name": service_name}, api_name="2.4 Get Services By Name")
        track_api_result("2.4 Get Services By Name", status)
        print(f"✅ Retrieved services filtered by name")
        time.sleep(0.5)
        
        # 2.5 Get service types
        status, response = make_request("GET", f"{BASE_URL}/admin/access/service-types", headers, api_name="2.5 Get Service Types")
        track_api_result("2.5 Get Service Types", status)
        print(f"✅ Retrieved service types")
        time.sleep(0.5)
        
        status, response = make_request("GET", f"{BASE_URL}/admin/access/service-types", headers, params={"service_type": "api"}, api_name="2.6 Get Service Types Filtered")
        track_api_result("2.6 Get Service Types Filtered", status)
        print(f"✅ Retrieved service types filtered by type")
        time.sleep(0.5)
        
        # ====================================================================
        # 3. ROLE-SERVICE ASSIGNMENT APIs
        # ====================================================================
        print(f"\n{'#'*80}")
        print("# 3. ROLE-SERVICE ASSIGNMENT APIs")
        print(f"{'#'*80}")
        
        # 3.1 Assign services to role
        if created_role_id and service_name:
            # Use the role name we created (not UUID)
            assign_data = {
                "role_name": role_name,  # Use role name, not UUID
                "service_names": [service_name]
            }
            status, response = make_request("POST", f"{BASE_URL}/admin/access/roles/assign-services", headers, assign_data, api_name="3.1 Assign Services To Role")
            track_api_result("3.1 Assign Services To Role", status)
            print(f"✅ Assigned services to role")
            time.sleep(0.5)
        
        # ====================================================================
        # 4. USER ROLE ASSIGNMENT APIs
        # ====================================================================
        print(f"\n{'#'*80}")
        print("# 4. USER ROLE ASSIGNMENT APIs")
        print(f"{'#'*80}")
        
        # 4.1 Assign role to user
        if created_role_id:
            assign_data = {
                "username": USERNAME,
                "role_name": str(created_role_id)
            }
            status, response = make_request("POST", f"{BASE_URL}/admin/access/users/assign-role", headers, assign_data, api_name="4.1 Assign Role To User")
            track_api_result("4.1 Assign Role To User", status)
            print(f"✅ Assigned role to user")
            time.sleep(0.5)
        
        # 4.2 Get user roles
        status, response = make_request("GET", f"{BASE_URL}/admin/access/users/{USERNAME}/roles", headers, api_name="4.2 Get User Roles")
        track_api_result("4.2 Get User Roles", status)
        print(f"✅ Retrieved user roles")
        time.sleep(0.5)
        
        # 4.3 Get user services
        status, response = make_request("GET", f"{BASE_URL}/admin/access/users/{USERNAME}/services", headers, api_name="4.3 Get User Services")
        track_api_result("4.3 Get User Services", status)
        print(f"✅ Retrieved user services")
        time.sleep(0.5)
        
        # 4.4 Get user effective services
        status, response = make_request("GET", f"{BASE_URL}/admin/access/users/{USERNAME}/effective-services", headers, api_name="4.4 Get User Effective Services")
        track_api_result("4.4 Get User Effective Services", status)
        print(f"✅ Retrieved user effective services")
        time.sleep(0.5)
        
        # 4.5 Remove role from user
        if created_role_id:
            remove_data = {
                "username": USERNAME,
                "role_name": str(created_role_id)
            }
            status, response = make_request("DELETE", f"{BASE_URL}/admin/access/users/remove-role", headers, remove_data, api_name="4.5 Remove Role From User")
            track_api_result("4.5 Remove Role From User", status)
            print(f"✅ Removed role from user")
            time.sleep(0.5)
        
        # ====================================================================
        # 5. ROLE INHERITANCE APIs
        # ====================================================================
        print(f"\n{'#'*80}")
        print("# 5. ROLE INHERITANCE APIs")
        print(f"{'#'*80}")
        
        # 5.1 Create child role
        child_role_name = generate_unique_name("ChildRole")
        child_role_data = {
            "name": child_role_name,
            "description": "Child role for inheritance test",
            "is_active": True,
            "service_names": []
        }
        status, response = make_request("POST", f"{BASE_URL}/admin/access/roles", headers, child_role_data, expected_status=201, api_name="5.1 Create Child Role")
        track_api_result("5.1 Create Child Role", status)
        child_role_id = None
        if response.get("success") and response.get("data"):
            child_role_id = response["data"]["id"]
            created_roles.append(child_role_id)
            print(f"✅ Created child role: {child_role_name} (ID: {child_role_id})")
        time.sleep(0.5)
        
        # 5.2 Assign parent role to child
        if created_role_id and child_role_id:
            inheritance_data = {
                "parent_role_id": str(created_role_id),
                "child_role_id": str(child_role_id)
            }
            status, response = make_request("POST", f"{BASE_URL}/admin/access/roles/inheritance/assign-parent", headers, inheritance_data, api_name="5.2 Assign Parent Role")
            track_api_result("5.2 Assign Parent Role", status)
            print(f"✅ Assigned parent role to child")
            time.sleep(0.5)
        
        # 5.3 Get role hierarchy
        status, response = make_request("GET", f"{BASE_URL}/admin/access/hierarchy", headers, api_name="5.3 Get Role Hierarchy")
        track_api_result("5.3 Get Role Hierarchy", status)
        print(f"✅ Retrieved role hierarchy")
        time.sleep(0.5)
        
        # 5.4 Remove parent role from child
        if created_role_id and child_role_id:
            remove_parent_data = {
                "parent_role_id": str(created_role_id),
                "child_role_id": str(child_role_id)
            }
            status, response = make_request("DELETE", f"{BASE_URL}/admin/access/roles/inheritance/remove-parent", headers, remove_parent_data, api_name="5.4 Remove Parent Role")
            track_api_result("5.4 Remove Parent Role", status)
            print(f"✅ Removed parent role from child")
            time.sleep(0.5)
        
        # ====================================================================
        # 6. STATS AND HEALTH APIs
        # ====================================================================
        print(f"\n{'#'*80}")
        print("# 6. STATS AND HEALTH APIs")
        print(f"{'#'*80}")
        
        # 6.1 Get RBAC stats
        status, response = make_request("GET", f"{BASE_URL}/admin/access/stats", headers, api_name="6.1 Get RBAC Stats")
        track_api_result("6.1 Get RBAC Stats", status)
        print(f"✅ Retrieved RBAC stats")
        time.sleep(0.5)
        
        # 6.2 Health check
        status, response = make_request("GET", f"{BASE_URL}/api/v1/admin/access/health", headers, api_name="6.2 Health Check")
        track_api_result("6.2 Health Check", status)
        print(f"✅ Health check")
        time.sleep(0.5)
        
        # 6.3 Get all users access
        status, response = make_request("GET", f"{BASE_URL}/admin/access/users/all-access", headers, api_name="6.3 Get All Users Access")
        track_api_result("6.3 Get All Users Access", status)
        print(f"✅ Retrieved all users access")
        time.sleep(0.5)
        
        # ====================================================================
        # 7. AZURE AD MAPPING APIs
        # ====================================================================
        print(f"\n{'#'*80}")
        print("# 7. AZURE AD MAPPING APIs")
        print(f"{'#'*80}")
        
        # 7.1 Create Azure AD group role mapping
        if created_role_id:
            azure_ad_data = {
                "azure_ad_group_id": "test-group-123",
                "azure_ad_group_name": "Test Group",
                "role_id": str(created_role_id),  # Fixed: should be role_id not role_name
                "is_active": True
            }
            status, response = make_request("POST", f"{BASE_URL}/admin/access/azure-ad-mappings", headers, azure_ad_data, expected_status=201, api_name="7.1 Create Azure AD Mapping")
            track_api_result("7.1 Create Azure AD Mapping", status)
            mapping_id = None
            if response.get("success") and response.get("data"):
                mapping_id = response["data"]["id"]
                print(f"✅ Created Azure AD mapping (ID: {mapping_id})")
            time.sleep(0.5)
            
            # 7.2 Get Azure AD mappings
            status, response = make_request("GET", f"{BASE_URL}/admin/access/azure-ad-mappings", headers, api_name="7.2 Get Azure AD Mappings")
            track_api_result("7.2 Get Azure AD Mappings", status)
            print(f"✅ Retrieved Azure AD mappings")
            time.sleep(0.5)
            
            # 7.3 Get specific mapping
            if mapping_id:
                status, response = make_request("GET", f"{BASE_URL}/admin/access/azure-ad-mappings/{mapping_id}", headers, api_name="7.3 Get Azure AD Mapping By ID")
                track_api_result("7.3 Get Azure AD Mapping By ID", status)
                print(f"✅ Retrieved Azure AD mapping by ID")
                time.sleep(0.5)
            
            # 7.4 Delete mapping
            if mapping_id:
                status, response = make_request("DELETE", f"{BASE_URL}/admin/access/azure-ad-mappings/{mapping_id}", headers, api_name="7.4 Delete Azure AD Mapping")
                track_api_result("7.4 Delete Azure AD Mapping", status)
                print(f"✅ Deleted Azure AD mapping")
                time.sleep(0.5)
        
        # ====================================================================
        # 8. BULK OPERATIONS
        # ====================================================================
        print(f"\n{'#'*80}")
        print("# 8. BULK OPERATIONS")
        print(f"{'#'*80}")
        
        # 8.1 Bulk assign roles
        if created_role_id:
            bulk_data = {
                "username": USERNAME,
                "role_names": [str(created_role_id)]
            }
            status, response = make_request("POST", f"{BASE_URL}/admin/access/users/bulk-assign-roles", headers, bulk_data, api_name="8.1 Bulk Assign Roles")
            track_api_result("8.1 Bulk Assign Roles", status)
            print(f"✅ Bulk assigned roles")
            time.sleep(0.5)
        
        # ====================================================================
        # 9. PERMISSION CHECK
        # ====================================================================
        print(f"\n{'#'*80}")
        print("# 9. PERMISSION CHECK")
        print(f"{'#'*80}")
        
        if service_name:
            permission_data = {
                "username": USERNAME,
                "resource": service_name,  # Fixed: should be resource not service_name
                "action": "read"  # Fixed: added required action field
            }
            status, response = make_request("POST", f"{BASE_URL}/admin/access/permissions/check", headers, permission_data, api_name="9.1 Check Permission")
            track_api_result("9.1 Check Permission", status)
            print(f"✅ Checked permission")
            time.sleep(0.5)
        
        # ====================================================================
        # CLEANUP
        # ====================================================================
        print(f"\n{'#'*80}")
        print("# CLEANUP")
        print(f"{'#'*80}")
        
        # Delete created roles
        for idx, role_id in enumerate(created_roles, 1):
            status, response = make_request("DELETE", f"{BASE_URL}/admin/access/roles/{role_id}", headers, api_name=f"Cleanup {idx}: Delete Role")
            track_api_result(f"Cleanup {idx}: Delete Role", status)
            print(f"✅ Deleted role: {role_id}")
            time.sleep(0.5)
        
        print(f"\n{'='*80}")
        print("✅ All API tests completed!")
        print(f"{'='*80}\n")
        
        # Print summary
        print(f"\n{'='*80}")
        print("TEST SUMMARY")
        print(f"{'='*80}")
        print(f"✅ Passed: {len(api_results['passed'])}")
        print(f"❌ Failed: {len(api_results['failed'])}")
        
        if api_results['passed']:
            print(f"\n✅ PASSED APIs:")
            for api in api_results['passed']:
                print(f"  - {api}")
        
        if api_results['failed']:
            print(f"\n❌ FAILED APIs:")
            for api, got_status, expected_status in api_results['failed']:
                print(f"  - {api}: Expected {expected_status}, got {got_status}")
        
        print(f"{'='*80}\n")
        
    except AssertionError as e:
        print(f"\n❌ ASSERTION ERROR: {e}")
        print(f"\n{'='*80}")
        print("TEST SUMMARY (Partial)")
        print(f"{'='*80}")
        print(f"✅ Passed: {len(api_results['passed'])}")
        print(f"❌ Failed: {len(api_results['failed'])}")
        if api_results['failed']:
            print(f"\n❌ FAILED APIs:")
            for api, got_status, expected_status in api_results['failed']:
                print(f"  - {api}: Expected {expected_status}, got {got_status}")
        print(f"{'='*80}\n")
        import traceback
        traceback.print_exc()
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

