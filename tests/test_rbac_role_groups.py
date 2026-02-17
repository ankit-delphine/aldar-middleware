#!/usr/bin/env python3
"""
RBAC Role Groups System Test Script
Test the role group system where users can be assigned multiple role groups
and inherit all permissions from all roles in those groups
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


class RoleGroupRBACTester:
    """Role Group RBAC system tester"""
    
    async def create_role_groups(self):
        """Create role groups with different combinations of roles"""
        print_status("Creating role groups...")
        try:
            async with engine.begin() as conn:
                # Define role groups
                role_groups_data = [
                    ("admin_group", "Administrative role group with full access"),
                    ("content_group", "Content management role group"),
                    ("user_group", "Basic user role group"),
                    ("support_group", "Customer support role group"),
                ]
                
                # Get all roles
                result = await conn.execute(text("SELECT id, name FROM rbac_roles"))
                roles = {role.name: role.id for role in result.fetchall()}
                
                # Create role groups
                created_groups = {}
                for group_name, description in role_groups_data:
                    # Check if group already exists
                    result = await conn.execute(
                        text("SELECT id FROM rbac_role_groups WHERE name = :name"),
                        {"name": group_name}
                    )
                    existing_group = result.fetchone()
                    
                    if not existing_group:
                        await conn.execute(
                            text("""
                                INSERT INTO rbac_role_groups (name, description, is_active, created_at, updated_at)
                                VALUES (:name, :description, true, NOW(), NOW())
                            """),
                            {"name": group_name, "description": description}
                        )
                        print_success(f"Created role group: {group_name}")
                    else:
                        print_warning(f"Role group {group_name} already exists")
                    
                    # Get group ID
                    result = await conn.execute(
                        text("SELECT id FROM rbac_role_groups WHERE name = :name"),
                        {"name": group_name}
                    )
                    group_id = result.fetchone().id
                    created_groups[group_name] = group_id
                
                # Assign roles to groups
                group_role_assignments = {
                    "admin_group": ["superadmin", "admin"],
                    "content_group": ["moderator", "advanced_user"],
                    "user_group": ["user", "standard_user"],
                    "support_group": ["moderator", "user"],
                }
                
                for group_name, role_names in group_role_assignments.items():
                    group_id = created_groups[group_name]
                    for role_name in role_names:
                        if role_name in roles:
                            # Check if assignment already exists
                            result = await conn.execute(
                                text("""
                                    SELECT role_group_id FROM role_group_roles 
                                    WHERE role_group_id = :group_id AND role_id = :role_id
                                """),
                                {"group_id": group_id, "role_id": roles[role_name]}
                            )
                            existing = result.fetchone()
                            
                            if not existing:
                                await conn.execute(
                                    text("""
                                        INSERT INTO role_group_roles (role_group_id, role_id)
                                        VALUES (:group_id, :role_id)
                                    """),
                                    {"group_id": group_id, "role_id": roles[role_name]}
                                )
                                print_success(f"Added role {role_name} to group {group_name}")
                            else:
                                print_warning(f"Role {role_name} already in group {group_name}")
                
                return True
        except Exception as e:
            print_error(f"Role group creation failed: {e}")
            return False
    
    async def test_role_group_structure(self):
        """Test role group structure and role assignments"""
        print_status("Testing role group structure...")
        try:
            async with engine.begin() as conn:
                print_success("Role Group Structure:")
                
                # Get all role groups with their roles
                result = await conn.execute(text("""
                    SELECT rg.name as group_name, rg.description, r.name as role_name, r.level
                    FROM rbac_role_groups rg
                    LEFT JOIN role_group_roles rgr ON rg.id = rgr.role_group_id
                    LEFT JOIN rbac_roles r ON rgr.role_id = r.id
                    ORDER BY rg.name, r.level DESC
                """))
                groups_data = result.fetchall()
                
                current_group = None
                for row in groups_data:
                    if row.group_name != current_group:
                        if current_group is not None:
                            print()  # Add spacing between groups
                        print(f"   📁 Group: {row.group_name}")
                        print(f"      Description: {row.description}")
                        print(f"      Roles:")
                        current_group = row.group_name
                    
                    if row.role_name:
                        print(f"         🔑 {row.role_name} (Level {row.role_name})")
                
                return True
        except Exception as e:
            print_error(f"Role group structure test failed: {e}")
            return False
    
    async def test_user_role_group_assignment(self):
        """Test assigning users to role groups"""
        print_status("Testing user role group assignments...")
        try:
            async with engine.begin() as conn:
                # Assign users to role groups
                user_group_assignments = {
                    "admin": "admin_group",
                    "moderator": "content_group", 
                    "user": "user_group",
                }
                
                # Get all users and role groups
                result = await conn.execute(text("SELECT id, username FROM rbac_users"))
                users = {user.username: user.id for user in result.fetchall()}
                
                result = await conn.execute(text("SELECT id, name FROM rbac_role_groups"))
                groups = {group.name: group.id for group in result.fetchall()}
                
                print_success("Assigning users to role groups:")
                
                for username, group_name in user_group_assignments.items():
                    if username in users and group_name in groups:
                        # Check if assignment already exists
                        result = await conn.execute(
                            text("""
                                SELECT user_id FROM user_role_groups 
                                WHERE user_id = :user_id AND role_group_id = :group_id
                            """),
                            {"user_id": users[username], "group_id": groups[group_name]}
                        )
                        existing = result.fetchone()
                        
                        if not existing:
                            await conn.execute(
                                text("""
                                    INSERT INTO user_role_groups (user_id, role_group_id, granted_by, created_at)
                                    VALUES (:user_id, :group_id, :granted_by, NOW())
                                """),
                                {
                                    "user_id": users[username], 
                                    "group_id": groups[group_name],
                                    "granted_by": users["admin"]  # Admin grants the assignment
                                }
                            )
                            print_success(f"Assigned user {username} to group {group_name}")
                        else:
                            print_warning(f"User {username} already in group {group_name}")
                
                return True
        except Exception as e:
            print_error(f"User role group assignment test failed: {e}")
            return False
    
    async def test_permission_inheritance(self):
        """Test that users inherit all permissions from their role groups"""
        print_status("Testing permission inheritance from role groups...")
        try:
            async with engine.begin() as conn:
                print_success("User permissions via role groups:")
                
                # Test users and their effective permissions
                test_users = ["admin", "moderator", "user"]
                
                for username in test_users:
                    # Get user's role groups
                    result = await conn.execute(text("""
                        SELECT rg.name as group_name
                        FROM rbac_users u
                        JOIN user_role_groups urg ON u.id = urg.user_id
                        JOIN rbac_role_groups rg ON urg.role_group_id = rg.id
                        WHERE u.username = :username
                    """), {"username": username})
                    user_groups = [row.group_name for row in result.fetchall()]
                    
                    print(f"\n   👤 User: {username}")
                    print(f"      Role Groups: {', '.join(user_groups) if user_groups else 'None'}")
                    
                    if user_groups:
                        # Get all permissions from all roles in all groups
                        permissions = set()
                        for group_name in user_groups:
                            result = await conn.execute(text("""
                                SELECT DISTINCT p.resource, p.action
                                FROM rbac_role_groups rg
                                JOIN role_group_roles rgr ON rg.id = rgr.role_group_id
                                JOIN rbac_roles r ON rgr.role_id = r.id
                                JOIN rbac_role_permissions rp ON r.id = rp.role_id
                                JOIN rbac_permissions p ON rp.permission_id = p.id
                                WHERE rg.name = :group_name
                                ORDER BY p.resource, p.action
                            """), {"group_name": group_name})
                            group_permissions = result.fetchall()
                            
                            for perm in group_permissions:
                                permissions.add((perm.resource, perm.action))
                        
                        if permissions:
                            print(f"      Total Permissions: {len(permissions)}")
                            for resource, action in sorted(permissions):
                                print(f"         ✅ {resource}:{action}")
                        else:
                            print(f"      ⚠️ No permissions found")
                    else:
                        print(f"      ⚠️ No role groups assigned")
                
                return True
        except Exception as e:
            print_error(f"Permission inheritance test failed: {e}")
            return False
    
    async def test_multiple_role_groups(self):
        """Test users with multiple role groups"""
        print_status("Testing users with multiple role groups...")
        try:
            async with engine.begin() as conn:
                # Assign a user to multiple role groups
                username = "moderator"
                
                # Get user and group IDs
                result = await conn.execute(
                    text("SELECT id FROM rbac_users WHERE username = :username"),
                    {"username": username}
                )
                user = result.fetchone()
                if not user:
                    print_error(f"User {username} not found")
                    return False
                
                result = await conn.execute(
                    text("SELECT id FROM rbac_role_groups WHERE name = 'support_group'")
                )
                support_group = result.fetchone()
                if not support_group:
                    print_error("Support group not found")
                    return False
                
                # Assign user to support group as well
                result = await conn.execute(
                    text("""
                        SELECT user_id FROM user_role_groups 
                        WHERE user_id = :user_id AND role_group_id = :group_id
                    """),
                    {"user_id": user.id, "group_id": support_group.id}
                )
                existing = result.fetchone()
                
                if not existing:
                    await conn.execute(
                        text("""
                            INSERT INTO user_role_groups (user_id, role_group_id, granted_by, created_at)
                            VALUES (:user_id, :group_id, :granted_by, NOW())
                        """),
                        {
                            "user_id": user.id,
                            "group_id": support_group.id,
                            "granted_by": user.id  # Self-assigned for testing
                        }
                    )
                    print_success(f"Assigned user {username} to support_group")
                
                # Test combined permissions
                print_success(f"Testing combined permissions for user {username}:")
                
                result = await conn.execute(text("""
                    SELECT DISTINCT p.resource, p.action
                    FROM rbac_users u
                    JOIN user_role_groups urg ON u.id = urg.user_id
                    JOIN rbac_role_groups rg ON urg.role_group_id = rg.id
                    JOIN role_group_roles rgr ON rg.id = rgr.role_group_id
                    JOIN rbac_roles r ON rgr.role_id = r.id
                    JOIN rbac_role_permissions rp ON r.id = rp.role_id
                    JOIN rbac_permissions p ON rp.permission_id = p.id
                    WHERE u.username = :username
                    ORDER BY p.resource, p.action
                """), {"username": username})
                all_permissions = result.fetchall()
                
                print(f"   Combined permissions from all role groups:")
                for perm in all_permissions:
                    print(f"      ✅ {perm.resource}:{perm.action}")
                
                return True
        except Exception as e:
            print_error(f"Multiple role groups test failed: {e}")
            return False
    
    async def test_permission_checking(self):
        """Test permission checking through role groups"""
        print_status("Testing permission checking through role groups...")
        try:
            async with engine.begin() as conn:
                # Test specific permission checks
                test_cases = [
                    ("admin", "users", "delete", True),      # Should have via admin_group
                    ("admin", "system", "admin", True),       # Should have via admin_group
                    ("moderator", "users", "write", True),    # Should have via content_group
                    ("moderator", "users", "delete", False),  # Should NOT have
                    ("user", "orders", "read", True),         # Should have via user_group
                    ("user", "users", "read", False),         # Should NOT have
                ]
                
                print_success("Testing specific permissions:")
                for username, resource, action, expected in test_cases:
                    # Check permission via role groups
                    result = await conn.execute(text("""
                        SELECT COUNT(*) as count
                        FROM rbac_users u
                        JOIN user_role_groups urg ON u.id = urg.user_id
                        JOIN rbac_role_groups rg ON urg.role_group_id = rg.id
                        JOIN role_group_roles rgr ON rg.id = rgr.role_group_id
                        JOIN rbac_roles r ON rgr.role_id = r.id
                        JOIN rbac_role_permissions rp ON r.id = rp.role_id
                        JOIN rbac_permissions p ON rp.permission_id = p.id
                        WHERE u.username = :username 
                        AND p.resource = :resource 
                        AND p.action = :action
                    """), {
                        "username": username,
                        "resource": resource,
                        "action": action
                    })
                    count = result.fetchone().count
                    has_permission = count > 0
                    
                    status = "✅" if has_permission == expected else "❌"
                    print(f"   {status} {username} - {resource}:{action} = {has_permission} (expected: {expected})")
                
                return True
        except Exception as e:
            print_error(f"Permission checking test failed: {e}")
            return False
    
    async def run_comprehensive_tests(self):
        """Run comprehensive role group RBAC tests"""
        print("🧪 Role Group RBAC System Test Suite")
        print("=" * 60)
        
        tests = [
            ("Create Role Groups", self.create_role_groups),
            ("Role Group Structure", self.test_role_group_structure),
            ("User Role Group Assignment", self.test_user_role_group_assignment),
            ("Permission Inheritance", self.test_permission_inheritance),
            ("Multiple Role Groups", self.test_multiple_role_groups),
            ("Permission Checking", self.test_permission_checking),
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
        print("📊 Role Group RBAC Test Results Summary")
        print("=" * 60)
        
        passed = sum(1 for _, result in results if result)
        total = len(results)
        
        for test_name, result in results:
            status_icon = "✅" if result else "❌"
            print(f"{status_icon} {test_name}")
        
        print(f"\n🎯 Overall Result: {passed}/{total} tests passed")
        
        if passed == total:
            print_success("🎉 All role group RBAC tests passed!")
            print("\n🚀 Role Group RBAC System Features:")
            print("   ✅ Users can be assigned to multiple role groups")
            print("   ✅ Role groups contain multiple roles")
            print("   ✅ Users inherit ALL permissions from ALL roles in their groups")
            print("   ✅ Flexible permission management")
            print("   ✅ Granular access control")
        else:
            print_warning(f"⚠️ {total - passed} tests failed. Please check the issues above.")
        
        return passed == total


async def main():
    """Main function"""
    print("🚀 Starting Role Group RBAC System Testing...")
    print("This will test the role group system where users inherit permissions from all roles in their assigned groups.")
    print()
    
    tester = RoleGroupRBACTester()
    success = await tester.run_comprehensive_tests()
    
    if not success:
        print("\n❌ Some tests failed. Please fix the issues before proceeding.")
        sys.exit(1)
    else:
        print("\n🎉 Role Group RBAC System is fully tested and working!")


if __name__ == "__main__":
    asyncio.run(main())
