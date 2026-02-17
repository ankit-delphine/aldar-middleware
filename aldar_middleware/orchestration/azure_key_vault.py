"""Azure Key Vault service for secure secret management."""

import os
from typing import Optional, Dict, Any
from functools import lru_cache

from azure.identity import (
    DefaultAzureCredential,
    ManagedIdentityCredential,
    ClientSecretCredential,
    CredentialUnavailableError,
)
from azure.keyvault.secrets import SecretClient
from azure.core.exceptions import (
    ResourceNotFoundError,
    HttpResponseError,
    ClientAuthenticationError,
)
from loguru import logger

from aldar_middleware.settings import settings


class AzureKeyVaultService:
    """Azure Key Vault service for retrieving secrets."""

    def __init__(
        self,
        vault_url: Optional[str] = None,
        use_managed_identity: bool = True,
    ):
        """Initialize Azure Key Vault service.
        
        Args:
            vault_url: Azure Key Vault URL (e.g., https://your-vault.vault.azure.net/)
                     If None, will use ALDAR_AZURE_KEY_VAULT_URL from settings
            use_managed_identity: If True, use Managed Identity (for AKS/VM).
                                 If False, use Client Secret (for local dev).
        """
        self.vault_url = vault_url or settings.azure_key_vault_url
        self.use_managed_identity = use_managed_identity
        self.client: Optional[SecretClient] = None
        self._credential: Optional[Any] = None
        self._initialized = False

    def _get_credential(self):
        """Get Azure credential based on environment."""
        if self._credential:
            return self._credential

        try:
            if self.use_managed_identity:
                # Try Managed Identity first (for AKS, Azure VMs, etc.)
                try:
                    managed_identity = ManagedIdentityCredential()
                    managed_identity.get_token("https://vault.azure.net/.default")
                    self._credential = managed_identity
                    logger.debug("Using Managed Identity credential")
                except (CredentialUnavailableError, ClientAuthenticationError) as exc:
                    logger.debug(
                        "Managed Identity unavailable (%s). Falling back to DefaultAzureCredential without MI.",
                        exc,
                    )
                    self._credential = DefaultAzureCredential(
                        exclude_managed_identity_credential=True
                    )
                except Exception as exc:  # pragma: no cover - defensive fallback
                    self._credential = DefaultAzureCredential(
                        exclude_managed_identity_credential=True
                    )
                    logger.debug(
                        "Unexpected Managed Identity error (%s). Falling back to DefaultAzureCredential.",
                        exc,
                    )
            else:
                # Use Client Secret for local development
                if (
                    settings.azure_tenant_id
                    and settings.azure_client_id
                    and settings.azure_client_secret
                ):
                    # Key Vault tenant ID (where Key Vault exists)
                    key_vault_tenant_id = "902eab19-c66d-43b3-91e5-d4c00ec64e88"
                    
                    # If Service Principal is in different tenant, enable cross-tenant access
                    if settings.azure_tenant_id != key_vault_tenant_id:
                        logger.debug(f"Cross-tenant access: SP tenant {settings.azure_tenant_id[:8]}... → Key Vault tenant {key_vault_tenant_id[:8]}...")
                        self._credential = ClientSecretCredential(
                            tenant_id=settings.azure_tenant_id,
                            client_id=settings.azure_client_id,
                            client_secret=settings.azure_client_secret,
                            additionally_allowed_tenants=["*"]  # Allow cross-tenant access
                        )
                    else:
                        self._credential = ClientSecretCredential(
                            tenant_id=settings.azure_tenant_id,
                            client_id=settings.azure_client_id,
                            client_secret=settings.azure_client_secret,
                        )
                    logger.debug("Using Client Secret credential")
                else:
                    # Fallback to DefaultAzureCredential
                    self._credential = DefaultAzureCredential()
                    logger.debug("Using DefaultAzureCredential (fallback)")
        except Exception as e:
            logger.warning(f"Failed to create credential: {e}")
            self._credential = None

        return self._credential

    def initialize(self) -> bool:
        """Initialize the Key Vault client.
        
        Returns:
            bool: True if initialization successful, False otherwise
        """
        if self._initialized and self.client:
            return True

        try:
            if not self.vault_url:
                logger.warning("Azure Key Vault URL not configured")
                return False

            credential = self._get_credential()
            if not credential:
                logger.warning("Failed to obtain Azure credential for Key Vault")
                return False

            self.client = SecretClient(vault_url=self.vault_url, credential=credential)
            
            # Test connection by getting a test secret or listing secrets
            # We'll skip this to avoid unnecessary API calls
            self._initialized = True
            logger.info(f"Azure Key Vault client initialized for: {self.vault_url}")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Azure Key Vault client: {e}")
            self._initialized = False
            return False

    def get_secret(self, secret_name: str, default_value: Optional[str] = None) -> Optional[str]:
        """Get a secret from Key Vault.
        
        Args:
            secret_name: Name of the secret in Key Vault
            default_value: Default value if secret not found or error occurs
            
        Returns:
            str: Secret value or default_value if not found
        """
        if not self._initialized:
            if not self.initialize():
                logger.warning(f"Key Vault not initialized, returning default for: {secret_name}")
                return default_value

        try:
            if not self.client:
                logger.warning("Key Vault client not available")
                return default_value

            secret = self.client.get_secret(secret_name)
            logger.debug(f"Retrieved secret from Key Vault: {secret_name}")
            return secret.value

        except ResourceNotFoundError:
            logger.warning(f"Secret not found in Key Vault: {secret_name}")
            return default_value
        except HttpResponseError as e:
            logger.error(f"HTTP error retrieving secret '{secret_name}': {e}")
            return default_value
        except Exception as e:
            logger.error(f"Error retrieving secret '{secret_name}' from Key Vault: {e}")
            return default_value

    def get_secret_with_version(self, secret_name: str, version: Optional[str] = None) -> Optional[str]:
        """Get a specific version of a secret from Key Vault.
        
        Args:
            secret_name: Name of the secret in Key Vault
            version: Version ID of the secret (optional)
            
        Returns:
            str: Secret value or None if not found
        """
        if not self._initialized:
            if not self.initialize():
                return None

        try:
            if not self.client:
                return None

            if version:
                secret = self.client.get_secret(secret_name, version=version)
            else:
                secret = self.client.get_secret(secret_name)
            
            return secret.value

        except Exception as e:
            logger.error(f"Error retrieving secret version '{secret_name}/{version}': {e}")
            return None

    def set_secret(self, secret_name: str, secret_value: str) -> bool:
        """Set a secret in Key Vault.
        
        Args:
            secret_name: Name of the secret
            secret_value: Value to store
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not self._initialized:
            if not self.initialize():
                return False

        try:
            if not self.client:
                return False

            self.client.set_secret(secret_name, secret_value)
            logger.info(f"Secret set in Key Vault: {secret_name}")
            return True

        except Exception as e:
            logger.error(f"Error setting secret '{secret_name}' in Key Vault: {e}")
            return False

    def list_secrets(self) -> list[str]:
        """List all secret names in the Key Vault.
        
        Returns:
            list: List of secret names
        """
        if not self._initialized:
            if not self.initialize():
                return []

        try:
            if not self.client:
                return []

            secrets = []
            for secret_properties in self.client.list_properties_of_secrets():
                secrets.append(secret_properties.name)
            
            return secrets

        except Exception as e:
            logger.error(f"Error listing secrets from Key Vault: {e}")
            return []

    def delete_secret(self, secret_name: str) -> bool:
        """Delete a secret from Key Vault.
        
        Args:
            secret_name: Name of the secret to delete
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not self._initialized:
            if not self.initialize():
                return False

        try:
            if not self.client:
                return False

            delete_operation = self.client.begin_delete_secret(secret_name)
            delete_operation.wait()
            logger.info(f"Secret deleted from Key Vault: {secret_name}")
            return True

        except Exception as e:
            logger.error(f"Error deleting secret '{secret_name}' from Key Vault: {e}")
            return False

    def load_secrets_to_env(self, secret_mapping: Dict[str, str]) -> Dict[str, bool]:
        """Load multiple secrets from Key Vault and set them as environment variables.
        
        Args:
            secret_mapping: Dictionary mapping environment variable names to Key Vault secret names
                          Example: {"ALDAR_DB_PASS": "database-password", "ALDAR_JWT_SECRET_KEY": "jwt-secret"}
        
        Returns:
            Dict[str, bool]: Dictionary mapping env var names to success status
        """
        results = {}
        
        for env_var_name, secret_name in secret_mapping.items():
            secret_value = self.get_secret(secret_name)
            if secret_value:
                os.environ[env_var_name] = secret_value
                results[env_var_name] = True
                logger.debug(f"Loaded secret '{secret_name}' to environment variable '{env_var_name}'")
            else:
                results[env_var_name] = False
                logger.warning(f"Failed to load secret '{secret_name}' for environment variable '{env_var_name}'")
        
        return results


# Global Key Vault service instance
_key_vault_service: Optional[AzureKeyVaultService] = None


@lru_cache(maxsize=1)
def get_key_vault_service() -> Optional[AzureKeyVaultService]:
    """Get or create the global Key Vault service instance.
    
    Returns:
        AzureKeyVaultService: Key Vault service instance or None if not configured
    """
    global _key_vault_service
    
    if _key_vault_service is None:
        if not settings.azure_key_vault_enabled:
            logger.debug("Azure Key Vault is disabled")
            return None
        
        if not settings.azure_key_vault_url:
            logger.warning("Azure Key Vault URL not configured")
            return None
        
        _key_vault_service = AzureKeyVaultService(
            vault_url=settings.azure_key_vault_url,
            use_managed_identity=settings.azure_key_vault_use_managed_identity,
        )
        
        if not _key_vault_service.initialize():
            logger.warning("Failed to initialize Azure Key Vault service")
            _key_vault_service = None
    
    return _key_vault_service


def load_secrets_from_key_vault() -> bool:
    """Load secrets from Azure Key Vault and set them as environment variables.
    
    This function uses the secret mapping configuration from settings to automatically
    load secrets from Key Vault into environment variables before settings are loaded.
    
    Returns:
        bool: True if any secrets were loaded, False otherwise
    """
    if not settings.azure_key_vault_enabled:
        return False
    
    if not settings.azure_key_vault_secret_mapping:
        logger.debug("No secret mapping configured for Key Vault")
        return False
    
    service = get_key_vault_service()
    if not service:
        return False
    
    # Parse secret mapping from settings
    # Format: "ENV_VAR1=SECRET_NAME1,ENV_VAR2=SECRET_NAME2"
    secret_mapping = {}
    for mapping in settings.azure_key_vault_secret_mapping.split(","):
        mapping = mapping.strip()
        if "=" in mapping:
            env_var, secret_name = mapping.split("=", 1)
            secret_mapping[env_var.strip()] = secret_name.strip()
    
    if not secret_mapping:
        return False
    
    results = service.load_secrets_to_env(secret_mapping)
    success_count = sum(1 for success in results.values() if success)
    
    if success_count > 0:
        logger.info(f"Loaded {success_count}/{len(secret_mapping)} secrets from Key Vault")
    
    return success_count > 0

