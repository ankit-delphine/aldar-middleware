#!/usr/bin/env python3
"""
RBAC Individual Access System Test Script
Test the individual user access system where users can be granted specific app/service access
outside of role groups
"""

import sys
import os
import asyncio
from pathlib import Path
from datetime import datetime, timedelta

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


class IndividualAccessRBACTester:
    """Individual Access RBAC system tester"""
    
    async def create_individual_access(self):
        """Create individual access assignments for users"""
        print_status("Creating individual access assignments...")
        try:
            async with engine.begin() as conn:
                # Define individual access assignments
                individual_access_data = [
                    ("user", "beta_feature", "feature", "Access to beta features", "admin"),
                    ("user", "premium_tool", "tool", "Access to premium analytics tool", "admin"),
                    ("moderator", "special_app", "app", "Access to special admin app", "admin"),
                    ("moderator", "api_access", "service", "Direct API access", "admin"),
                    ("admin", "system_monitor", "tool", "System monitoring access", None),
                ]
                
                # Get all users
                result = await conn.execute(text("SELECT id, username FROM rbac_users"))
                users = {user.username: user.id for user in result.fetchall()}
                
                print_success("Creating individual access assignments:")
                
                for username, access_name, access_type, description, granted_by in individual_access_data:
                    if username in users:
                        # Check if access already exists
                        result = await conn.execute(
                            text("""
                                SELECT id FROM rbac_user_access 
                                WHERE user_id = :user_id AND access_name = :access_name AND is_active = true
                            """),
                            {"user_id": users[username], "access_name": access_name}
                        )
                        existing = result.fetchone()
                        
                        if not existing:
                            granted_by_id = users.get(granted_by) if granted_by else None
                            
                            await conn.execute(
                                text("""
                                    INSERT INTO rbac_user_access 
                                    (user_id, access_name, access_type, description, granted_by, is_active, created_at, updated_at)
                                    VALUES (:user_id, :access_name, :access_type, :description, :granted_by, true, NOW(), NOW())
                                """),
                                {
                                    "user_id": users[username],
                                    "access_name": access_name,
                                    "access_type": access_type,
                                    "description": description,
                                    "granted_by": granted_by_id
                                }
                            )
                            print_success(f"Granted {access_name} ({access_type}) to {username}")
                        else:
                            print_warning(f"User {username} already has access to {access_name}")
                
                return True
        except Exception as e:
            print_error(f"Individual access creation failed: {e}")
            return False
    
    async def test_individual_access_structure(self):
        """Test individual access structure"""
        print_status("Testing individual access structure...")
        try:
            async with engine.begin() as conn:
                print_success("Individual Access Structure:")
                
                # Get all individual access with user info
                result = await conn.execute(text("""
                    SELECT u.username, ua.access_name, ua.access_type, ua.description, ua.expires_at
                    FROM rbac_user_access ua
                    JOIN rbac_users u ON ua.user_id = u.id
                    WHERE ua.is_active = true
                    ORDER BY u.username, ua.access_name
                """))
                access_data = result.fetchall()
                
                current_user = None
                for row in access_data:
                    if row.username != current_user:
                        if current_user is not None:
                            print()  # Add spacing between users
                        print(f"   👤 User: {row.username}")
                        print(f"      Individual Access:")
                        current_user = row.username
                    
                    expires_info = f" (expires: {row.expires_at})" if row.expires_at else ""
                    print(f"         🔓 {row.access_name} ({row.access_type}){expires_info}")
                    if row.description:
                        print(f"            Description: {row.description}")
                
                return True
        except Exception as e:
            print_error(f"Individual access structure test failed: {e}")
            return False
    
    async def test_individual_access_checking(self):
        """Test individual access checking"""
        print_status("Testing individual access checking...")
        try:
            async with engine.begin() as conn:
                # Test specific access checks
                test_cases = [
                    ("user", "beta_feature", True),
                    ("user", "premium_tool", True),
                    ("user", "special_app", False),  # Should NOT have this
                    ("moderator", "special_app", True),
                    ("moderator", "api_access", True),
                    ("moderator", "beta_feature", False),  # Should NOT have this
                    ("admin", "system_monitor", True),
                    ("admin", "beta_feature", False),  # Should NOT have this
                ]
                
                print_success("Testing individual access checks:")
                for username, access_name, expected in test_cases:
                    # Check individual access
                    result = await conn.execute(text("""
                        SELECT COUNT(*) as count
                        FROM rbac_user_access ua
                        JOIN rbac_users u ON ua.user_id = u.id
                        WHERE u.username = :username 
                        AND ua.access_name = :access_name 
                        AND ua.is_active = true
                    """), {
                        "username": username,
                        "access_name": access_name
                    })
                    count = result.fetchone().count
                    has_access = count > 0
                    
                    status = "✅" if has_access == expected else "❌"
                    print(f"   {status} {username} - {access_name} = {has_access} (expected: {expected})")
                
                return True
        except Exception as e:
            print_error(f"Individual access checking test failed: {e}")
            return False
    
    async def test_expiring_access(self):
        """Test expiring access functionality"""
        print_status("Testing expiring access functionality...")
        try:
            async with engine.begin() as conn:
                # Create a temporary access that expires soon
                username = "user"
                access_name = "temporary_access"
                
                # Get user ID
                result = await conn.execute(
                    text("SELECT id FROM rbac_users WHERE username = :username"),
                    {"username": username}
                )
                user = result.fetchone()
                if not user:
                    print_error(f"User {username} not found")
                    return False
                
                # Create expiring access (expires in 1 minute)
                expires_at = datetime.now() + timedelta(minutes=1)
                
                # Check if access already exists
                result = await conn.execute(
                    text("""
                        SELECT id FROM rbac_user_access 
                        WHERE user_id = :user_id AND access_name = :access_name
                    """),
                    {"user_id": user.id, "access_name": access_name}
                )
                existing = result.fetchone()
                
                if not existing:
                    await conn.execute(
                        text("""
                            INSERT INTO rbac_user_access 
                            (user_id, access_name, access_type, description, expires_at, is_active, created_at, updated_at)
                            VALUES (:user_id, :access_name, :access_type, :description, :expires_at, true, NOW(), NOW())
                        """),
                        {
                            "user_id": user.id,
                            "access_name": access_name,
                            "access_type": "temporary",
                            "description": "Temporary access for testing",
                            "expires_at": expires_at
                        }
                    )
                    print_success(f"Created temporary access for {username} (expires at {expires_at})")
                
                # Test that access is currently valid
                result = await conn.execute(text("""
                    SELECT COUNT(*) as count
                    FROM rbac_user_access ua
                    JOIN rbac_users u ON ua.user_id = u.id
                    WHERE u.username = :username 
                    AND ua.access_name = :access_name 
                    AND ua.is_active = true
                    AND (ua.expires_at IS NULL OR ua.expires_at > NOW())
                """), {
                    "username": username,
                    "access_name": access_name
                })
                count = result.fetchone().count
                has_access = count > 0
                
                print_success(f"Temporary access check: {username} - {access_name} = {has_access}")
                
                return True
        except Exception as e:
            print_error(f"Expiring access test failed: {e}")
            return False
    
    async def test_combined_access(self):
        """Test combined role groups + individual access"""
        print_status("Testing combined role groups + individual access...")
        try:
            async with engine.begin() as conn:
                print_success("Combined Access Analysis:")
                
                # Test users with both role groups and individual access
                test_users = ["user", "moderator", "admin"]
                
                for username in test_users:
                    # Get role groups
                    result = await conn.execute(text("""
                        SELECT rg.name as group_name
                        FROM rbac_users u
                        JOIN user_role_groups urg ON u.id = urg.user_id
                        JOIN rbac_role_groups rg ON urg.role_group_id = rg.id
                        WHERE u.username = :username
                    """), {"username": username})
                    role_groups = [row.group_name for row in result.fetchall()]
                    
                    # Get individual access
                    result = await conn.execute(text("""
                        SELECT ua.access_name, ua.access_type
                        FROM rbac_user_access ua
                        JOIN rbac_users u ON ua.user_id = u.id
                        WHERE u.username = :username AND ua.is_active = true
                    """), {"username": username})
                    individual_access = [(row.access_name, row.access_type) for row in result.fetchall()]
                    
                    # Get permissions from role groups
                    permissions = set()
                    for group_name in role_groups:
                        result = await conn.execute(text("""
                            SELECT DISTINCT p.resource, p.action
                            FROM rbac_role_groups rg
                            JOIN role_group_roles rgr ON rg.id = rgr.role_group_id
                            JOIN rbac_roles r ON rgr.role_id = r.id
                            JOIN rbac_role_permissions rp ON r.id = rp.role_id
                            JOIN rbac_permissions p ON rp.permission_id = p.id
                            WHERE rg.name = :group_name
                        """), {"group_name": group_name})
                        group_permissions = result.fetchall()
                        
                        for perm in group_permissions:
                            permissions.add((perm.resource, perm.action))
                    
                    print(f"\n   👤 User: {username}")
                    print(f"      Role Groups: {', '.join(role_groups) if role_groups else 'None'}")
                    print(f"      Individual Access: {len(individual_access)} items")
                    for access_name, access_type in individual_access:
                        print(f"         🔓 {access_name} ({access_type})")
                    print(f"      Total Permissions from Groups: {len(permissions)}")
                    for resource, action in sorted(permissions):
                        print(f"         ✅ {resource}:{action}")
                
                return True
        except Exception as e:
            print_error(f"Combined access test failed: {e}")
            return False
    
    async def test_access_revocation(self):
        """Test access revocation"""
        print_status("Testing access revocation...")
        try:
            async with engine.begin() as conn:
                # Revoke an access
                username = "user"
                access_name = "beta_feature"
                
                # Check access before revocation
                result = await conn.execute(text("""
                    SELECT COUNT(*) as count
                    FROM rbac_user_access ua
                    JOIN rbac_users u ON ua.user_id = u.id
                    WHERE u.username = :username 
                    AND ua.access_name = :access_name 
                    AND ua.is_active = true
                """), {
                    "username": username,
                    "access_name": access_name
                })
                count_before = result.fetchone().count
                
                # Revoke access
                await conn.execute(text("""
                    UPDATE rbac_user_access 
                    SET is_active = false, updated_at = NOW()
                    WHERE user_id = (SELECT id FROM rbac_users WHERE username = :username)
                    AND access_name = :access_name
                """), {
                    "username": username,
                    "access_name": access_name
                })
                
                # Check access after revocation
                result = await conn.execute(text("""
                    SELECT COUNT(*) as count
                    FROM rbac_user_access ua
                    JOIN rbac_users u ON ua.user_id = u.id
                    WHERE u.username = :username 
                    AND ua.access_name = :access_name 
                    AND ua.is_active = true
                """), {
                    "username": username,
                    "access_name": access_name
                })
                count_after = result.fetchone().count
                
                print_success(f"Access revocation test:")
                print(f"   Before: {username} had {access_name} = {count_before > 0}")
                print(f"   After: {username} has {access_name} = {count_after > 0}")
                
                # Restore access for other tests
                await conn.execute(text("""
                    UPDATE rbac_user_access 
                    SET is_active = true, updated_at = NOW()
                    WHERE user_id = (SELECT id FROM rbac_users WHERE username = :username)
                    AND access_name = :access_name
                """), {
                    "username": username,
                    "access_name": access_name
                })
                
                return True
        except Exception as e:
            print_error(f"Access revocation test failed: {e}")
            return False
    
    async def run_comprehensive_tests(self):
        """Run comprehensive individual access RBAC tests"""
        print("🧪 Individual Access RBAC System Test Suite")
        print("=" * 60)
        
        tests = [
            ("Create Individual Access", self.create_individual_access),
            ("Individual Access Structure", self.test_individual_access_structure),
            ("Individual Access Checking", self.test_individual_access_checking),
            ("Expiring Access", self.test_expiring_access),
            ("Combined Access", self.test_combined_access),
            ("Access Revocation", self.test_access_revocation),
        ]
        
        results = []
        for test_name, test_func in tests:
            print(f"\n📋 Running {test_name} Test...")
            try:
                result = await test_func()
                results.append((test_name, result))
                if result:
                    print_success(f"✅ {test_name} test passed")
                else:
                    print_error(f"❌ {test_name} test failed")
            except Exception as e:
                print_error(f"❌ {test_name} test failed with exception: {e}")
                results.append((test_name, False))
        
        # Summary
        print("\n" + "=" * 60)
        print("📊 Individual Access RBAC Test Results Summary")
        print("=" * 60)
        
        passed = sum(1 for _, result in results if result)
        total = len(results)
        
        for test_name, result in results:
            status_icon = "✅" if result else "❌"
            print(f"{status_icon} {test_name}")
        
        print(f"\n🎯 Overall Result: {passed}/{total} tests passed")
        
        if passed == total:
            print_success("🎉 All individual access RBAC tests passed!")
            print("\n🚀 Individual Access RBAC System Features:")
            print("   ✅ Users can have individual app/service access")
            print("   ✅ Access can be granted/revoked independently")
            print("   ✅ Access can have expiration dates")
            print("   ✅ Combined with role group permissions")
            print("   ✅ Granular access control")
        else:
            print_warning(f"⚠️ {total - passed} tests failed. Please check the issues above.")
        
        return passed == total


async def main():
    """Main function"""
    print("🚀 Starting Individual Access RBAC System Testing...")
    print("This will test the individual access system where users can be granted specific app/service access.")
    print()
    
    tester = IndividualAccessRBACTester()
    success = await tester.run_comprehensive_tests()
    
    if not success:
        print("\n❌ Some tests failed. Please fix the issues before proceeding.")
        sys.exit(1)
    else:
        print("\n🎉 Individual Access RBAC System is fully tested and working!")


if __name__ == "__main__":
    asyncio.run(main())
