"""Azure AD synchronization service."""

import logging
import asyncio
from typing import List, Dict, Any, Optional
import httpx
from fastapi import HTTPException

from aldar_middleware.auth.azure_ad import AzureADAuth

logger = logging.getLogger(__name__)


class AzureADSyncService:
    """Service for syncing users and groups from Azure AD."""

    def __init__(self):
        """Initialize the sync service."""
        self.azure_ad = AzureADAuth()
        self.graph_url = "https://graph.microsoft.com/v1.0"
        
        # Rate limiting configuration
        self.max_requests_per_second = 10  # Conservative limit
        self.request_delay = 0.1  # 100ms between requests
        self.max_retries = 3
        self.base_retry_delay = 1.0  # Base delay for exponential backoff

    async def _make_graph_request_with_retry(
        self, 
        client: httpx.AsyncClient, 
        method: str, 
        url: str, 
        headers: Dict[str, str], 
        params: Optional[Dict] = None,
        attempt: int = 1
    ) -> httpx.Response:
        """
        Make a Graph API request with rate limiting and throttling handling.
        
        Args:
            client: HTTP client
            method: HTTP method
            url: Request URL
            headers: Request headers
            params: Query parameters
            attempt: Current attempt number
            
        Returns:
            HTTP response
            
        Raises:
            HTTPException: If request fails after all retries
        """
        try:
            # Add delay between requests to respect rate limits
            if attempt > 1:
                await asyncio.sleep(self.request_delay)
            
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                params=params
            )
            
            # Handle throttling (HTTP 429)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                
                if retry_after:
                    # Use server-specified retry delay
                    wait_time = float(retry_after)
                    logger.warning(f"Rate limited. Waiting {wait_time} seconds (server-specified)")
                else:
                    # Use exponential backoff
                    wait_time = self.base_retry_delay * (2 ** (attempt - 1))
                    logger.warning(f"Rate limited. Waiting {wait_time} seconds (exponential backoff)")
                
                if attempt <= self.max_retries:
                    logger.info(f"Retrying request (attempt {attempt + 1}/{self.max_retries})")
                    await asyncio.sleep(wait_time)
                    return await self._make_graph_request_with_retry(
                        client, method, url, headers, params, attempt + 1
                    )
                else:
                    logger.error(f"Max retries exceeded for rate limiting")
                    raise HTTPException(
                        status_code=429,
                        detail="Rate limit exceeded. Please try again later."
                    )
            
            # Handle other HTTP errors
            elif response.status_code >= 400:
                # For 404 errors, don't log as error - they're expected for missing resources
                if response.status_code == 404:
                    logger.debug(f"Resource not found (404): {url}")
                else:
                    logger.error(f"Graph API error {response.status_code}: {response.text}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Graph API error: {response.text}"
                )
            
            return response
            
        except httpx.RequestError as e:
            logger.error(f"Request error: {e}")
            if attempt <= self.max_retries:
                wait_time = self.base_retry_delay * (2 ** (attempt - 1))
                logger.info(f"Retrying request after error (attempt {attempt + 1}/{self.max_retries})")
                await asyncio.sleep(wait_time)
                return await self._make_graph_request_with_retry(
                    client, method, url, headers, params, attempt + 1
                )
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Request failed after {self.max_retries} retries: {str(e)}"
                )

    async def get_admin_token(self) -> str:
        """
        Get admin access token for Microsoft Graph API.
        This uses client credentials flow for application permissions.
        """
        try:
            # Access settings directly since AzureADAuth has private attributes
            from aldar_middleware.settings import settings
            
            data = {
                "client_id": settings.azure_client_id,
                "client_secret": settings.azure_client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials"
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"https://login.microsoftonline.com/{settings.azure_tenant_id}/oauth2/v2.0/token",
                    data=data
                )

                if response.status_code != 200:
                    error_text = response.text
                    error_detail = error_text
                    
                    # Try to parse error response for better error messages
                    try:
                        error_json = response.json()
                        error_code = error_json.get("error")
                        error_description = error_json.get("error_description", "")
                        error_codes = error_json.get("error_codes", [])
                        
                        # Check for specific error codes
                        if 7000215 in error_codes or "invalid_client" in error_code:
                            error_detail = (
                                f"Azure AD authentication configuration error: {error_description}. "
                                f"Please verify that AZURE_CLIENT_SECRET is set to the actual secret value "
                                f"(not the secret ID) in your environment configuration."
                            )
                            logger.error(
                                f"Failed to get admin token - Invalid client secret (AADSTS7000215): {error_description}. "
                                f"This indicates the Azure AD client secret is misconfigured."
                            )
                        else:
                            error_detail = f"Failed to authenticate with Azure AD: {error_description or error_text}"
                            logger.error(f"Failed to get admin token: {error_text}")
                    except (ValueError, KeyError):
                        # If we can't parse the error, use the raw text
                        logger.error(f"Failed to get admin token: {error_text}")
                        error_detail = f"Failed to authenticate with Azure AD: {error_text}"
                    
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=error_detail
                    )

                token_data = response.json()
                return token_data.get("access_token")

        except HTTPException:
            # Re-raise HTTP exceptions as-is
            raise
        except httpx.HTTPStatusError as e:
            error_text = e.response.text
            error_detail = error_text
            
            # Try to parse error for better messages
            try:
                error_json = e.response.json()
                error_code = error_json.get("error")
                error_description = error_json.get("error_description", "")
                error_codes = error_json.get("error_codes", [])
                
                if 7000215 in error_codes or "invalid_client" in error_code:
                    error_detail = (
                        f"Azure AD authentication configuration error: {error_description}. "
                        f"Please verify that AZURE_CLIENT_SECRET is set to the actual secret value "
                        f"(not the secret ID) in your environment configuration."
                    )
                    logger.error(
                        f"HTTP error getting admin token - Invalid client secret (AADSTS7000215): {error_description}"
                    )
                else:
                    error_detail = f"Failed to authenticate with Azure AD: {error_description or error_text}"
            except (ValueError, KeyError):
                error_detail = f"Failed to authenticate with Azure AD: {error_text}"
            
            logger.error(f"HTTP error getting admin token: {e.response.status_code} - {error_detail}")
            raise HTTPException(
                status_code=e.response.status_code,
                detail=error_detail
            )
        except Exception as e:
            logger.error(f"Error getting admin token: {type(e).__name__}: {e}")
            raise

    async def get_all_users(
        self,
        domain_filter: Optional[str] = None,
        max_users: int = 10000
    ) -> List[Dict[str, Any]]:
        """
        Get all users from Azure AD.

        Args:
            domain_filter: Optional domain filter (e.g., "adq.ae")
            max_users: Maximum number of users to fetch

        Returns:
            List of user dictionaries
        """
        try:
            access_token = await self.get_admin_token()
            all_users = []
            next_link = f"{self.graph_url}/users"

            # Query parameters for Graph API
            select_fields = (
                "id,userPrincipalName,mail,displayName,givenName,surname,"
                "accountEnabled,department,officeLocation,country,jobTitle,companyName"
            )
            
            params = {
                "$select": select_fields,
                "$top": 999  # Maximum page size
            }

            # Add domain filter if provided
            if domain_filter:
                params["$filter"] = f"endsWith(mail,'@{domain_filter}') or endsWith(userPrincipalName,'@{domain_filter}')"

            async with httpx.AsyncClient() as client:
                is_first_request = True
                while next_link and len(all_users) < max_users:
                    # Only pass params on first request or if URL doesn't already have query params
                    request_params = params if (is_first_request or "?" not in next_link) else None
                    
                    # Use rate-limited request method
                    response = await self._make_graph_request_with_retry(
                        client=client,
                        method="GET",
                        url=next_link,
                        headers={"Authorization": f"Bearer {access_token}"},
                        params=request_params
                    )
                    
                    is_first_request = False

                    data = response.json()
                    users = data.get("value", [])
                    all_users.extend(users)

                    # Check for next page
                    next_link = data.get("@odata.nextLink", "")
                    
                    # Add delay between requests to respect rate limits
                    if next_link and len(all_users) < max_users:
                        await asyncio.sleep(self.request_delay)

            logger.info(f"Fetched {len(all_users)} users from Azure AD")
            return all_users[:max_users]

        except Exception as e:
            logger.error(f"Error fetching users from Azure AD: {e}")
            raise

    async def get_all_groups(self, max_groups: int = 10000) -> List[Dict[str, Any]]:
        """
        Get all groups from Azure AD.

        Args:
            max_groups: Maximum number of groups to fetch

        Returns:
            List of group dictionaries
        """
        try:
            access_token = await self.get_admin_token()
            all_groups = []
            next_link = f"{self.graph_url}/groups"
            is_first_request = True

            # Query parameters for Graph API
            params = {
                "$select": "id,displayName,description,mail,securityEnabled,mailEnabled",
                "$top": 999
            }

            async with httpx.AsyncClient() as client:
                while next_link and len(all_groups) < max_groups:
                    # Only pass params on first request or if URL doesn't already have query params
                    # @odata.nextLink URLs already contain query parameters
                    request_params = params if (is_first_request or "?" not in next_link) else None
                    
                    # Use rate-limited request method
                    response = await self._make_graph_request_with_retry(
                        client=client,
                        method="GET",
                        url=next_link,
                        headers={"Authorization": f"Bearer {access_token}"},
                        params=request_params
                    )
                    
                    is_first_request = False

                    data = response.json()
                    groups = data.get("value", [])
                    all_groups.extend(groups)

                    # Check for next page
                    next_link = data.get("@odata.nextLink", "")
                    
                    # Add delay between requests to respect rate limits
                    if next_link and len(all_groups) < max_groups:
                        await asyncio.sleep(self.request_delay)

            logger.info(f"Fetched {len(all_groups)} groups from Azure AD")
            return all_groups[:max_groups]

        except Exception as e:
            logger.error(f"Error fetching groups from Azure AD: {e}")
            raise

    async def search_groups(
        self,
        keyword: str,
        max_results: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Search Azure AD groups by keyword.
        
        Searches groups where the keyword appears in displayName or description.
        Uses case-insensitive partial matching.

        Args:
            keyword: Search keyword (e.g., "finance")
            max_results: Maximum number of results to return

        Returns:
            List of group dictionaries matching the keyword
        """
        try:
            access_token = await self.get_admin_token()
            all_groups = []
            next_link = f"{self.graph_url}/groups"

            # Query parameters for Graph API with filter
            # Use contains for case-insensitive partial matching
            keyword_lower = keyword.lower()
            filter_query = (
                f"contains(tolower(displayName),'{keyword_lower}') or "
                f"contains(tolower(description),'{keyword_lower}')"
            )
            
            params = {
                "$select": "id,displayName,description,mail,securityEnabled,mailEnabled",
                "$filter": filter_query,
                "$top": min(999, max_results)  # Use smaller of 999 (max page size) or max_results
            }

            async with httpx.AsyncClient() as client:
                is_first_request = True
                while next_link and len(all_groups) < max_results:
                    # Only pass params on first request or if URL doesn't already have query params
                    request_params = params if (is_first_request or "?" not in next_link) else None
                    
                    # Use rate-limited request method
                    response = await self._make_graph_request_with_retry(
                        client=client,
                        method="GET",
                        url=next_link,
                        headers={"Authorization": f"Bearer {access_token}"},
                        params=request_params
                    )
                    
                    is_first_request = False

                    data = response.json()
                    groups = data.get("value", [])
                    all_groups.extend(groups)

                    # Check for next page
                    next_link = data.get("@odata.nextLink", "")
                    
                    # Add delay between requests to respect rate limits
                    if next_link and len(all_groups) < max_results:
                        await asyncio.sleep(self.request_delay)
                    
                    # Break if we've reached max_results
                    if len(all_groups) >= max_results:
                        break

            # Limit to max_results
            result_groups = all_groups[:max_results]
            logger.info(f"Found {len(result_groups)} groups matching keyword '{keyword}'")
            return result_groups

        except Exception as e:
            logger.error(f"Error searching groups in Azure AD: {e}")
            raise

    async def get_group_members(self, group_id: str) -> List[Dict[str, Any]]:
        """
        Get all members of a specific group.

        Args:
            group_id: Azure AD group ID

        Returns:
            List of user dictionaries
        """
        try:
            access_token = await self.get_admin_token()
            all_members = []
            next_link = f"{self.graph_url}/groups/{group_id}/members"

            params = {"$top": 999}

            async with httpx.AsyncClient() as client:
                is_first_request = True
                while next_link:
                    try:
                        # Only pass params on first request or if URL doesn't already have query params
                        request_params = params if (is_first_request or "?" not in next_link) else None
                        
                        # Use rate-limited request method
                        response = await self._make_graph_request_with_retry(
                            client=client,
                            method="GET",
                            url=next_link,
                            headers={"Authorization": f"Bearer {access_token}"},
                            params=request_params
                        )
                        
                        is_first_request = False

                        data = response.json()
                        members = data.get("value", [])
                        all_members.extend(members)

                        # Check for next page
                        next_link = data.get("@odata.nextLink", "")
                        
                        # Add delay between requests to respect rate limits
                        if next_link:
                            await asyncio.sleep(self.request_delay)
                            
                    except HTTPException as e:
                        logger.error(f"Failed to get group members: {e.detail}")
                        break

            return all_members

        except Exception as e:
            logger.error(f"Error fetching group members: {e}")
            return []

    async def get_groups_with_pagination(
        self,
        top: int = 999,
        select_fields: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get groups from Azure AD with pagination support.
        
        Args:
            top: Maximum number of groups per page (default: 999, max page size)
            select_fields: Comma-separated list of fields to select (e.g., "id,displayName,description")
                          If None, all fields are returned
        
        Returns:
            Dictionary with groups list and pagination info
        """
        try:
            access_token = await self.get_admin_token()
            params = {}
            
            if top:
                params["$top"] = min(top, 999)  # Max page size is 999
            
            if select_fields:
                params["$select"] = select_fields
            
            async with httpx.AsyncClient() as client:
                response = await self._make_graph_request_with_retry(
                    client=client,
                    method="GET",
                    url=f"{self.graph_url}/groups",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params=params if params else None
                )
                
                data = response.json()
                groups = data.get("value", [])
                next_link = data.get("@odata.nextLink")
                
                return {
                    "groups": groups,
                    "count": len(groups),
                    "next_link": next_link,
                    "has_more": next_link is not None
                }
                
        except Exception as e:
            logger.error(f"Error fetching groups with pagination: {e}")
            raise

    async def get_security_groups_only(
        self,
        select_fields: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get only security groups from Azure AD.
        
        Args:
            select_fields: Comma-separated list of fields to select (e.g., "id,displayName,description")
                          If None, defaults to "id,displayName,description"
        
        Returns:
            List of security group dictionaries
        """
        try:
            access_token = await self.get_admin_token()
            params = {
                "$filter": "securityEnabled eq true"
            }
            
            if select_fields:
                params["$select"] = select_fields
            else:
                params["$select"] = "id,displayName,description"
            
            all_groups = []
            next_link = f"{self.graph_url}/groups"
            is_first_request = True
            
            async with httpx.AsyncClient() as client:
                while next_link:
                    # Only pass params on first request or if URL doesn't already have query params
                    request_params = params if (is_first_request or "?" not in next_link) else None
                    
                    response = await self._make_graph_request_with_retry(
                        client=client,
                        method="GET",
                        url=next_link,
                        headers={"Authorization": f"Bearer {access_token}"},
                        params=request_params
                    )
                    
                    is_first_request = False
                    
                    data = response.json()
                    groups = data.get("value", [])
                    all_groups.extend(groups)
                    
                    next_link = data.get("@odata.nextLink", "")
                    if next_link:
                        await asyncio.sleep(self.request_delay)
            
            logger.info(f"Fetched {len(all_groups)} security groups from Azure AD")
            return all_groups
            
        except Exception as e:
            logger.error(f"Error fetching security groups: {e}")
            raise

    async def validate_group_by_id(self, group_id: str) -> Optional[Dict[str, Any]]:
        """
        Validate and get a group by its ID.
        
        Args:
            group_id: Azure AD group ID (UUID)
        
        Returns:
            Group dictionary if found, None otherwise (e.g., if group was deleted from Azure AD)
        """
        try:
            access_token = await self.get_admin_token()
            
            async with httpx.AsyncClient() as client:
                response = await self._make_graph_request_with_retry(
                    client=client,
                    method="GET",
                    url=f"{self.graph_url}/groups/{group_id}",
                    headers={"Authorization": f"Bearer {access_token}"}
                )
                
                return response.json()
                
        except HTTPException as e:
            # Handle HTTPException from _make_graph_request_with_retry
            if e.status_code == 404:
                logger.debug(f"Azure AD group '{group_id[:8]}...' not found (may have been deleted)")
                return None
            # Re-raise other HTTP exceptions
            raise
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug(f"Azure AD group '{group_id[:8]}...' not found (may have been deleted)")
                return None
            # Log and re-raise other HTTP errors
            logger.error(f"HTTP error validating group '{group_id[:8]}...': {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            # Log unexpected errors but don't break the flow
            logger.warning(f"Unexpected error validating group '{group_id[:8]}...': {type(e).__name__}: {e}")
            return None

    async def search_groups_basic(
        self,
        query: str,
        select_fields: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Basic search for groups starting with query string.
        
        Args:
            query: Search query string (e.g., "Finance")
            select_fields: Comma-separated list of fields to select
        
        Returns:
            List of matching groups
        """
        try:
            access_token = await self.get_admin_token()
            params = {
                "$filter": f"startswith(displayName,'{query}')"
            }
            
            if select_fields:
                params["$select"] = select_fields
            else:
                params["$select"] = "id,displayName,description,mailEnabled,securityEnabled"
            
            all_groups = []
            next_link = f"{self.graph_url}/groups"
            is_first_request = True
            
            async with httpx.AsyncClient() as client:
                while next_link:
                    # Only pass params on first request or if URL doesn't already have query params
                    request_params = params if (is_first_request or "?" not in next_link) else None
                    
                    response = await self._make_graph_request_with_retry(
                        client=client,
                        method="GET",
                        url=next_link,
                        headers={"Authorization": f"Bearer {access_token}"},
                        params=request_params
                    )
                    
                    is_first_request = False
                    
                    data = response.json()
                    groups = data.get("value", [])
                    all_groups.extend(groups)
                    
                    next_link = data.get("@odata.nextLink", "")
                    if next_link:
                        await asyncio.sleep(self.request_delay)
            
            logger.info(f"Found {len(all_groups)} groups matching query '{query}'")
            return all_groups
            
        except Exception as e:
            logger.error(f"Error searching groups with query '{query}': {e}")
            raise

    async def search_groups_advanced(
        self,
        query: str,
        security_enabled_only: bool = False,
        select_fields: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Advanced search for groups with multiple filters.
        
        Args:
            query: Search query string (e.g., "Finance")
            security_enabled_only: If True, only return security groups
            select_fields: Comma-separated list of fields to select
        
        Returns:
            List of matching groups
        """
        try:
            access_token = await self.get_admin_token()
            
            # Build filter
            filter_parts = [f"startswith(displayName,'{query}')"]
            if security_enabled_only:
                filter_parts.append("securityEnabled eq true")
            
            params = {
                "$filter": " and ".join(filter_parts)
            }
            
            if select_fields:
                params["$select"] = select_fields
            else:
                params["$select"] = "id,displayName,description,mailEnabled,securityEnabled"
            
            all_groups = []
            next_link = f"{self.graph_url}/groups"
            is_first_request = True
            
            async with httpx.AsyncClient() as client:
                while next_link:
                    # Only pass params on first request or if URL doesn't already have query params
                    request_params = params if (is_first_request or "?" not in next_link) else None
                    
                    response = await self._make_graph_request_with_retry(
                        client=client,
                        method="GET",
                        url=next_link,
                        headers={"Authorization": f"Bearer {access_token}"},
                        params=request_params
                    )
                    
                    is_first_request = False
                    
                    data = response.json()
                    groups = data.get("value", [])
                    all_groups.extend(groups)
                    
                    next_link = data.get("@odata.nextLink", "")
                    if next_link:
                        await asyncio.sleep(self.request_delay)
            
            logger.info(f"Found {len(all_groups)} groups matching advanced query '{query}'")
            return all_groups
            
        except Exception as e:
            logger.error(f"Error in advanced group search with query '{query}': {e}")
            raise

