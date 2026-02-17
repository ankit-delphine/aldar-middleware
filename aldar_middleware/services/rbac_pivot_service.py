"""RBAC Pivot Service for managing user and agent AD group mappings."""

import logging
from typing import List, Optional, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from aldar_middleware.models.rbac import RBACUserPivot, RBACAgentPivot
from aldar_middleware.auth.azure_ad import azure_ad_auth

logger = logging.getLogger(__name__)


class RBACPivotService:
    """Service for managing RBAC pivot tables (user and agent AD group mappings).
    
    Access control is based solely on Azure AD group intersection:
    - Users have a list of AD group UUIDs (synced on login)
    - Agents have a list of AD group UUIDs (assigned via API)
    - Access is granted if user's AD groups ∩ agent's AD groups is non-empty
    """

    def __init__(self, db: AsyncSession):
        """Initialize the pivot service."""
        self.db = db

    async def sync_user_ad_groups(
        self,
        user_name: str,
        access_token: str
    ) -> RBACUserPivot:
        """Sync user's Azure AD groups on login.
        
        This method implements the RBAC flow:
        1. Calls MS Graph API internally to get all AD groups for the user
        2. Deletes old pivot entry for the user (if exists)
        3. Creates new pivot entry with the latest AD groups (or updates if entry existed)
        
        This ensures the user pivot table always has the latest AD groups from Azure AD.
        If the entry doesn't exist, it will be created. If it exists, it will be replaced.
        
        Args:
            user_name: User email (primary identifier, field renamed from user_name to email)
            access_token: Azure AD access token to fetch groups from MS Graph API
            
        Returns:
            Updated or newly created RBACUserPivot entry
        """
        try:
            # Step 1: Get user's AD group UUIDs from Azure AD via MS Graph API
            # This internally calls: GET https://graph.microsoft.com/v1.0/me/memberOf
            ad_group_uuids = await azure_ad_auth.get_user_groups(access_token)
            
            logger.info(
                f"Syncing AD group UUIDs for user '{user_name}': {len(ad_group_uuids)} groups found from MS Graph API"
            )
            
            # Step 2: Delete old pivot entry if exists (this handles the "replace" part)
            # If entry doesn't exist, this is a no-op
            await self.db.execute(
                delete(RBACUserPivot).where(RBACUserPivot.email == user_name)
            )
            
            # Step 3: Create new pivot entry with latest AD groups
            # This will create a new entry if one didn't exist, effectively replacing old groups
            user_pivot = RBACUserPivot(
                email=user_name,
                azure_ad_groups=ad_group_uuids
            )
            self.db.add(user_pivot)
            await self.db.commit()
            await self.db.refresh(user_pivot)
            
            logger.info(
                f"Successfully synced AD group UUIDs for user '{user_name}': {ad_group_uuids}"
            )
            
            return user_pivot
            
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error syncing AD groups for user '{user_name}': {e}", exc_info=True)
            raise

    async def sync_user_ad_groups_direct(
        self,
        user_name: str,
        ad_group_uuids: List[str]
    ) -> RBACUserPivot:
        """Sync user's Azure AD groups directly with provided list (for testing).
        
        This method:
        1. Deletes old pivot entry for the user
        2. Creates new pivot entry with the provided group UUIDs
        
        Args:
            user_name: Username (email or username)
            ad_group_uuids: List of Azure AD group UUIDs (as strings) to assign
            
        Returns:
            Updated RBACUserPivot entry
        """
        try:
            logger.info(
                f"Syncing AD group UUIDs directly for user '{user_name}': {len(ad_group_uuids)} groups"
            )
            
            # Delete old pivot entry if exists
            await self.db.execute(
                delete(RBACUserPivot).where(RBACUserPivot.email == user_name)
            )
            
            # Create new pivot entry
            user_pivot = RBACUserPivot(
                email=user_name,
                azure_ad_groups=ad_group_uuids
            )
            self.db.add(user_pivot)
            await self.db.commit()
            await self.db.refresh(user_pivot)
            
            logger.info(
                f"Successfully synced AD group UUIDs directly for user '{user_name}': {ad_group_uuids}"
            )
            
            return user_pivot
            
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error syncing AD groups directly for user '{user_name}': {e}", exc_info=True)
            raise

    async def get_user_ad_groups(self, user_name: str) -> List[str]:
        """Get Azure AD group UUIDs for a user.
        
        Args:
            user_name: Username to lookup
            
        Returns:
            List of Azure AD group UUIDs (as strings), empty list if user not found
        """
        result = await self.db.execute(
            select(RBACUserPivot).where(RBACUserPivot.email == user_name)
        )
        user_pivot = result.scalar_one_or_none()
        
        if not user_pivot:
            return []
        
        return user_pivot.azure_ad_groups or []

    async def assign_agent_ad_groups(
        self,
        agent_name: str,
        ad_groups: List[str],
        ad_groups_metadata: List[Dict[str, str]]
    ) -> RBACAgentPivot:
        """Assign Azure AD group UUIDs and metadata to an agent.
        
        This method:
        1. Deletes old pivot entry for the agent if exists
        2. Creates new pivot entry with the provided group UUIDs and metadata
        
        Multiple agents can share the same AD group UUIDs. If a user has at least
        one matching AD group UUID, they have access to all agents with that group.
        
        Args:
            agent_name: Agent name
            ad_groups: List of Azure AD group UUIDs (as strings) to assign - REQUIRED
            ad_groups_metadata: List of metadata objects with id and name - REQUIRED:
                [{"id": "uuid1", "name": "group1"}, {"id": "uuid2", "name": "group2"}, ...]
            
        Returns:
            Updated RBACAgentPivot entry
        """
        try:
            logger.info(
                f"Assigning AD group UUIDs to agent '{agent_name}': {ad_groups}"
            )
            logger.info(
                f"Assigning AD group metadata to agent '{agent_name}': {len(ad_groups_metadata)} entries"
            )
            
            # Delete old pivot entry if exists and flush to ensure constraint is satisfied
            await self.db.execute(
                delete(RBACAgentPivot).where(RBACAgentPivot.agent_name == agent_name)
            )
            await self.db.flush()  # Flush delete before adding to avoid unique constraint issues
            
            # Create new pivot entry
            agent_pivot = RBACAgentPivot(
                agent_name=agent_name,
                azure_ad_groups=ad_groups,
                agent_ad_groups_metadata=ad_groups_metadata
            )
            self.db.add(agent_pivot)
            await self.db.commit()
            await self.db.refresh(agent_pivot)
            
            logger.info(
                f"Successfully assigned AD group UUIDs to agent '{agent_name}': {ad_groups}"
            )
            
            return agent_pivot
            
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error assigning AD groups to agent '{agent_name}': {e}", exc_info=True)
            raise

    async def get_agent_ad_groups(self, agent_name: str) -> List[str]:
        """Get Azure AD group UUIDs assigned to an agent.
        
        Args:
            agent_name: Agent name to lookup
            
        Returns:
            List of Azure AD group UUIDs (as strings), empty list if agent not found
        """
        result = await self.db.execute(
            select(RBACAgentPivot).where(RBACAgentPivot.agent_name == agent_name)
        )
        agent_pivot = result.scalar_one_or_none()
        
        if not agent_pivot:
            return []
        
        return agent_pivot.azure_ad_groups or []

    async def check_user_has_access_to_agent(
        self,
        user_name: str,
        agent_name: str
    ) -> bool:
        """Check if a user has access to an agent based on AD group UUID intersection.
        
        Access control is based solely on Azure AD group intersection:
        - User's AD groups ∩ Agent's AD groups = non-empty → User has access
        - If there's any overlap in AD group UUIDs, access is granted
        
        No roles or service types are involved - only AD group intersection determines access.
        Multiple agents can share the same AD group UUIDs, and users with those
        UUIDs will have access to all such agents.
        
        Args:
            user_name: Username to check
            agent_name: Agent name to check access for
            
        Returns:
            True if user has access (has at least one matching AD group UUID), False otherwise
        """
        user_groups = await self.get_user_ad_groups(user_name)
        agent_groups = await self.get_agent_ad_groups(agent_name)
        
        if not agent_groups:
            # If agent has no groups assigned, no one has access
            return False
        
        if not user_groups:
            # If user has no groups, no access
            return False
        
        # Check if there's any intersection between user group UUIDs and agent group UUIDs
        # If user has even one matching UUID, they have access
        user_groups_set = set(user_groups)
        agent_groups_set = set(agent_groups)
        
        has_access = bool(user_groups_set & agent_groups_set)
        
        logger.debug(
            f"Access check for user '{user_name}' to agent '{agent_name}': "
            f"user_group_uuids={user_groups}, agent_group_uuids={agent_groups}, has_access={has_access}"
        )
        
        return has_access

    async def list_all_user_pivots(self) -> List[RBACUserPivot]:
        """List all user pivot entries."""
        result = await self.db.execute(select(RBACUserPivot))
        return list(result.scalars().all())

    async def list_all_agent_pivots(self) -> List[RBACAgentPivot]:
        """List all agent pivot entries."""
        result = await self.db.execute(select(RBACAgentPivot))
        return list(result.scalars().all())

