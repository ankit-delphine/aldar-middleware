#!/usr/bin/env python3
"""
Sync environment variables from .env file to Azure Key Vault.

This script reads your .env file, identifies sensitive secrets,
and automatically creates/updates them in Azure Key Vault.

Usage:
    python scripts/sync-env-to-keyvault.py
    python scripts/sync-env-to-keyvault.py --dry-run  # Preview without creating
    python scripts/sync-env-to-keyvault.py --env-file .env.production  # Use different file
"""

import os
import sys
import re
import argparse
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urlparse

try:
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential, ClientSecretCredential
    from azure.keyvault.secrets import SecretClient
    from azure.core.exceptions import HttpResponseError
except ImportError:
    print("❌ Error: Azure Key Vault SDK not installed.")
    print("   Install it with: poetry install")
    sys.exit(1)


# Configuration
DEFAULT_KEY_VAULT_NAME = "aldar-middleware-vault"
DEFAULT_ENV_FILE = ".env"

# Patterns to identify sensitive variables
SENSITIVE_PATTERNS = [
    r'PASS',
    r'PASSWORD',
    r'SECRET',
    r'KEY',
    r'TOKEN',
    r'AUTH',
    r'CREDENTIAL',
    r'CONNECTION_STRING',
    r'API_KEY',
    r'PRIVATE',
]

# Variables to exclude (non-sensitive or configuration)
EXCLUDE_PATTERNS = [
    r'DEBUG',
    r'ENABLED',
    r'DISABLED',
    r'PORT',
    r'HOST',
    r'URL$',  # URLs that are not connection strings
    r'TIMEOUT',
    r'COUNT',
    r'SIZE',
    r'LEVEL',
    r'ALGORITHM',
    r'MODEL',
    r'ORIGINS',
    r'ENVIRONMENT',
    r'VERSION',
    r'PREFIX',
    r'LOG_LEVEL',
    r'RELOAD',
    r'ECHO',
    r'ENABLE',
    r'SAMPLE_RATE',
    r'EXPIRE_MINUTES',
    r'HEARTBEAT_INTERVAL',
    r'MAX_CONNECTIONS',
    r'CACHE_TTL',
    r'MAX_HISTORY',
    r'TIMEOUT$',
    r'RATE',
    r'REQUESTS',
    r'WINDOW',
    r'CONTAINER_NAME',
    r'QUEUE_NAME',
    r'DATABASE_NAME',
    r'STREAM_NAME',
    r'INTERVAL',
]


def parse_env_file(env_file: Path) -> Dict[str, str]:
    """Parse .env file and return dictionary of key-value pairs."""
    env_vars = {}
    
    if not env_file.exists():
        print(f"❌ Error: {env_file} not found")
        sys.exit(1)
    
    with open(env_file, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
            
            # Handle inline comments
            if '#' in line:
                line = line.split('#')[0].strip()
            
            # Parse KEY=VALUE
            if '=' in line:
                parts = line.split('=', 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip()
                    
                    # Remove quotes if present
                    if (value.startswith('"') and value.endswith('"')) or \
                       (value.startswith("'") and value.endswith("'")):
                        value = value[1:-1]
                    
                    # Skip empty values
                    if value:
                        env_vars[key] = value
    
    return env_vars


def is_sensitive(key: str) -> bool:
    """Check if an environment variable is sensitive."""
    key_upper = key.upper()
    
    # Check exclude patterns first
    for pattern in EXCLUDE_PATTERNS:
        if re.search(pattern, key_upper):
            return False
    
    # Check sensitive patterns
    for pattern in SENSITIVE_PATTERNS:
        if re.search(pattern, key_upper):
            return True
    
    return False


def env_var_to_keyvault_name(env_var: str) -> str:
    """Convert environment variable name to Key Vault secret name.
    
    Examples:
        ALDAR_DB_PASS -> database-password
        ALDAR_JWT_SECRET_KEY -> jwt-secret-key
        ALDAR_AZURE_CLIENT_SECRET -> azure-client-secret
    """
    # Remove ALDAR_ prefix if present
    name = env_var
    if name.startswith('ALDAR_'):
        name = name[4:]
    
    # Convert to lowercase and replace underscores with hyphens
    name = name.lower().replace('_', '-')
    
    return name


def get_key_vault_client(key_vault_url: str, use_managed_identity: bool = False) -> SecretClient:
    """Get Azure Key Vault client with appropriate credentials."""
    try:
        # Check for Service Principal credentials in environment
        # Try both standard Azure env vars and AIQ-prefixed ones
        tenant_id = os.getenv("AZURE_TENANT_ID") or os.getenv("ALDAR_AZURE_TENANT_ID")
        client_id = os.getenv("AZURE_CLIENT_ID") or os.getenv("ALDAR_AZURE_CLIENT_ID")
        client_secret = os.getenv("AZURE_CLIENT_SECRET") or os.getenv("ALDAR_AZURE_CLIENT_SECRET")
        
        # If not in env, try reading from .env file if it exists
        if not all([tenant_id, client_id, client_secret]):
            try:
                env_file = Path(__file__).parent.parent / ".env"
                if env_file.exists():
                    from dotenv import load_dotenv
                    load_dotenv(env_file)
                    tenant_id = tenant_id or os.getenv("AZURE_TENANT_ID") or os.getenv("ALDAR_AZURE_TENANT_ID")
                    client_id = client_id or os.getenv("AZURE_CLIENT_ID") or os.getenv("ALDAR_AZURE_CLIENT_ID")
                    client_secret = client_secret or os.getenv("AZURE_CLIENT_SECRET") or os.getenv("ALDAR_AZURE_CLIENT_SECRET")
            except ImportError:
                pass  # dotenv not available, skip
            except Exception:
                pass  # Failed to load .env, continue with other methods
        
        if tenant_id and client_id and client_secret:
            # Use Service Principal (bypasses device management requirements)
            print("🔐 Using Service Principal authentication")
            
            # Extract target tenant from Key Vault URL if it's in a different tenant
            # Key Vault tenant ID: 902eab19-c66d-43b3-91e5-d4c00ec64e88
            # Service Principal tenant: might be different
            target_tenant_id = "902eab19-c66d-43b3-91e5-d4c00ec64e88"  # Key Vault tenant
            
            # If Service Principal tenant is different from Key Vault tenant, allow cross-tenant
            if tenant_id != target_tenant_id:
                print(f"   ⚠️  Cross-tenant access: SP tenant {tenant_id[:8]}... → Key Vault tenant {target_tenant_id[:8]}...")
                credential = ClientSecretCredential(
                    tenant_id=tenant_id,
                    client_id=client_id,
                    client_secret=client_secret,
                    additionally_allowed_tenants=["*"]  # Allow cross-tenant access
                )
            else:
                credential = ClientSecretCredential(
                    tenant_id=tenant_id,
                    client_id=client_id,
                    client_secret=client_secret
                )
        elif use_managed_identity:
            credential = ManagedIdentityCredential()
        else:
            # Try DefaultAzureCredential (supports multiple auth methods)
            print("🔐 Attempting DefaultAzureCredential...")
            credential = DefaultAzureCredential()
        
        client = SecretClient(vault_url=key_vault_url, credential=credential)
        return client
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Error creating Key Vault client: {e}")
        print("\n💡 Authentication options:")
        
        if "AADSTS530003" in error_msg or "device is required to be managed" in error_msg.lower():
            print("\n   🔒 Device Management Policy Detected")
            print("   Your organization requires managed devices. Use Service Principal instead:")
            print("\n   Option 1: Use Service Principal (Recommended)")
            print("   Set these environment variables:")
            print("     export AZURE_TENANT_ID=your-tenant-id")
            print("     export AZURE_CLIENT_ID=your-client-id")
            print("     export AZURE_CLIENT_SECRET=your-client-secret")
            print("\n   Then run the script again.")
            print("\n   Option 2: Use Azure Portal")
            print("   Manually add secrets via https://portal.azure.com")
            print("   The mapping string has already been generated above!")
        else:
            print("\n   1. Login with Azure CLI:")
            print("      az login --scope https://vault.azure.net/.default")
            print("\n   2. Or use Service Principal (set AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET)")
            print("\n   3. Or use Azure Portal to manually add secrets")
        
        sys.exit(1)


def sync_secrets_to_keyvault(
    env_vars: Dict[str, str],
    key_vault_url: str,
    dry_run: bool = False,
    use_managed_identity: bool = False,
    skip_existing: bool = False
) -> Tuple[List[str], List[str], List[str]]:
    """Sync secrets from env_vars to Key Vault.
    
    Returns:
        Tuple of (created, updated, skipped) secret names
    """
    created = []
    updated = []
    skipped = []
    
    # Filter sensitive variables
    sensitive_vars = {k: v for k, v in env_vars.items() if is_sensitive(k)}
    
    if not sensitive_vars:
        print("⚠️  No sensitive variables found in .env file")
        return created, updated, skipped
    
    print(f"\n📋 Found {len(sensitive_vars)} sensitive variables:")
    for key in sorted(sensitive_vars.keys()):
        print(f"   - {key}")
    
    if dry_run:
        print("\n🔍 DRY RUN MODE - No secrets will be created/updated")
        return created, updated, skipped
    
    # Get Key Vault client (create once, reuse for all secrets)
    print(f"\n🔗 Connecting to Key Vault: {key_vault_url}")
    client = None
    try:
        client = get_key_vault_client(key_vault_url, use_managed_identity)
        # Test connection with a simple operation
        try:
            # Try to list secrets to verify connection works
            list(client.list_properties_of_secrets())
            print("✅ Connected successfully\n")
        except Exception as test_error:
            error_msg = str(test_error)
            # Check if this is a cross-tenant error
            if "AADSTS7000229" in error_msg or "missing service principal in the tenant" in error_msg.lower():
                raise test_error  # Re-raise to be caught by outer handler
            # If listing fails for other reasons, that's okay - we'll try individual operations
            print("⚠️  Connection test failed, but will try individual operations\n")
    except SystemExit:
        # get_key_vault_client already printed error and exited
        raise
    except Exception as e:
        error_msg = str(e)
        
        if "AADSTS7000229" in error_msg or "missing service principal in the tenant" in error_msg.lower():
            print("\n" + "="*60)
            print("❌ CROSS-TENANT ISSUE DETECTED")
            print("="*60)
            print("\nYour Service Principal exists in a different tenant than your Key Vault.")
            print("\nService Principal Tenant: 51f3e25f-bad6-4f7a-b95b-d2b59f4d07e4")
            print("Key Vault Tenant:          902eab19-c66d-43b3-91e5-d4c00ec64e88")
            print("\n✅ SOLUTION OPTIONS:")
            print("\nOption 1: Use Azure Portal (Easiest)")
            print("   1. Go to https://portal.azure.com")
            print("   2. Navigate to aldar-middleware-vault → Secrets")
            print("   3. Manually add the 12 secrets listed above")
            print("   4. Use the mapping string already generated below!")
            print("\nOption 2: Create Service Principal in Key Vault Tenant")
            print("   1. Switch to Key Vault tenant:")
            print("      az login --tenant 902eab19-c66d-43b3-91e5-d4c00ec64e88")
            print("   2. Create new Service Principal:")
            print("      az ad sp create-for-rbac --name aldar-middleware-keyvault")
            print("   3. Grant it Key Vault access (see AZURE_KEY_VAULT_SETUP.md)")
            print("   4. Update your .env with new credentials")
            print("\nOption 3: Get existing SP from Key Vault tenant")
            print("   Ask your Azure admin for a Service Principal that exists")
            print("   in tenant 902eab19-c66d-43b3-91e5-d4c00ec64e88")
            # Still generate mapping before exiting
            return created, updated, skipped
        elif "AADSTS530003" in error_msg or "device is required to be managed" in error_msg.lower():
            print("\n" + "="*60)
            print("🔒 DEVICE MANAGEMENT POLICY DETECTED")
            print("="*60)
            print("\nYour organization requires a managed device to access Key Vault.")
            print("Azure CLI authentication won't work on unmanaged devices.")
            print("\n✅ SOLUTION: Use Service Principal Authentication")
            print("\n1. Create a Service Principal (if you haven't already):")
            print("   az ad sp create-for-rbac --name aldar-middleware-keyvault")
            print("\n2. Grant it access to Key Vault (RBAC):")
            print("   az role assignment create \\")
            print("     --role 'Key Vault Secrets Officer' \\")
            print("     --assignee <SERVICE_PRINCIPAL_OBJECT_ID> \\")
            print("     --scope '/subscriptions/b349695a-6377-4b64-8e7f-3114fed4bfd7/resourceGroups/adq/providers/Microsoft.KeyVault/vaults/aldar-middleware-vault'")
            print("\n3. Set environment variables and run script again:")
            print("   export AZURE_TENANT_ID='902eab19-c66d-43b3-91e5-d4c00ec64e88'")
            print("   export AZURE_CLIENT_ID='<your-client-id>'")
            print("   export AZURE_CLIENT_SECRET='<your-client-secret>'")
            print("   poetry run python scripts/sync-env-to-keyvault.py")
            print("\n💡 The mapping string is already generated above - you can also")
            print("   manually add secrets via Azure Portal if preferred.")
            return created, updated, skipped
        else:
            print(f"❌ Failed to connect to Key Vault: {e}")
            print("\n💡 Try:")
            print("   - Using Service Principal (set AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET)")
            print("   - Or manually add secrets via Azure Portal")
            return created, updated, skipped
    
    if client is None:
        print("❌ Failed to create Key Vault client")
        return created, updated, skipped
    
    # Sync each secret
    for env_var, value in sorted(sensitive_vars.items()):
        secret_name = env_var_to_keyvault_name(env_var)
        
        try:
            # Check if secret already exists
            try:
                existing_secret = client.get_secret(secret_name)
                
                if skip_existing:
                    print(f"⏭️  Skipping {secret_name} (already exists)")
                    skipped.append(secret_name)
                    continue
                
                # Update existing secret
                client.set_secret(secret_name, value)
                print(f"✅ Updated: {secret_name} ({env_var})")
                updated.append(secret_name)
                
            except HttpResponseError as e:
                if e.status_code == 404:
                    # Secret doesn't exist, create it
                    client.set_secret(secret_name, value)
                    print(f"✅ Created: {secret_name} ({env_var})")
                    created.append(secret_name)
                else:
                    print(f"❌ Error with {secret_name}: {e.status_code} - {e.message}")
                    skipped.append(secret_name)
        
        except Exception as e:
            error_msg = str(e)
            # Don't print the full stack trace for each secret - just a concise message
            if "AADSTS7000229" in error_msg or "missing service principal in the tenant" in error_msg.lower():
                # Stop processing - Service Principal doesn't exist in Key Vault tenant
                print(f"\n" + "="*60)
                print("❌ CROSS-TENANT ISSUE DETECTED")
                print("="*60)
                print("\nYour Service Principal exists in a different tenant than your Key Vault.")
                print("\nService Principal Tenant: 51f3e25f-bad6-4f7a-b95b-d2b59f4d07e4")
                print("Key Vault Tenant:          902eab19-c66d-43b3-91e5-d4c00ec64e88")
                print("\n✅ SOLUTION OPTIONS:")
                print("\nOption 1: Use Azure Portal (Easiest)")
                print("   1. Go to https://portal.azure.com")
                print("   2. Navigate to aldar-middleware-vault → Secrets")
                print("   3. Manually add the 12 secrets listed above")
                print("   4. Use the mapping string already generated below!")
                print("\nOption 2: Create Service Principal in Key Vault Tenant")
                print("   1. Switch to Key Vault tenant:")
                print("      az login --tenant 902eab19-c66d-43b3-91e5-d4c00ec64e88")
                print("   2. Create new Service Principal:")
                print("      az ad sp create-for-rbac --name aldar-middleware-keyvault")
                print("   3. Grant it Key Vault access (see AZURE_KEY_VAULT_SETUP.md)")
                print("   4. Update your .env with new credentials")
                print("\nOption 3: Get existing SP from Key Vault tenant")
                print("   Ask your Azure admin for a Service Principal that exists")
                print("   in tenant 902eab19-c66d-43b3-91e5-d4c00ec64e88")
                # Stop processing - all will fail with same error
                skipped.append(secret_name)
                break
            elif "AADSTS530003" in error_msg or "device is required to be managed" in error_msg.lower():
                # Stop processing - all will fail with same error
                print(f"\n❌ Authentication failed: Device management policy blocking access")
                print("   See error message above for solution.")
                skipped.append(secret_name)
                break
            else:
                print(f"❌ Error with {secret_name}: {type(e).__name__}")
                skipped.append(secret_name)
    
    return created, updated, skipped


def generate_mapping(env_vars: Dict[str, str]) -> str:
    """Generate ALDAR_AZURE_KEY_VAULT_SECRET_MAPPING string."""
    sensitive_vars = [k for k in env_vars.keys() if is_sensitive(k)]
    
    mappings = []
    for env_var in sorted(sensitive_vars):
        kv_name = env_var_to_keyvault_name(env_var)
        mappings.append(f"{env_var}={kv_name}")
    
    return ",".join(mappings)


def main():
    parser = argparse.ArgumentParser(
        description="Sync environment variables from .env to Azure Key Vault",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview what will be synced (dry run)
  python scripts/sync-env-to-keyvault.py --dry-run
  
  # Sync all secrets to Key Vault
  python scripts/sync-env-to-keyvault.py
  
  # Use different .env file
  python scripts/sync-env-to-keyvault.py --env-file .env.production
  
  # Skip updating existing secrets
  python scripts/sync-env-to-keyvault.py --skip-existing
        """
    )
    
    parser.add_argument(
        '--env-file',
        type=str,
        default=DEFAULT_ENV_FILE,
        help=f'Path to .env file (default: {DEFAULT_ENV_FILE})'
    )
    
    parser.add_argument(
        '--key-vault-url',
        type=str,
        default=f"https://{DEFAULT_KEY_VAULT_NAME}.vault.azure.net/",
        help=f'Key Vault URL (default: https://{DEFAULT_KEY_VAULT_NAME}.vault.azure.net/)'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without actually creating/updating secrets'
    )
    
    parser.add_argument(
        '--use-managed-identity',
        action='store_true',
        help='Use Managed Identity for authentication (for AKS/Azure VMs)'
    )
    
    parser.add_argument(
        '--skip-existing',
        action='store_true',
        help='Skip updating secrets that already exist in Key Vault'
    )
    
    parser.add_argument(
        '--generate-mapping',
        action='store_true',
        help='Generate and display the ALDAR_AZURE_KEY_VAULT_SECRET_MAPPING string'
    )
    
    args = parser.parse_args()
    
    # Change to project root
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    os.chdir(project_root)
    
    env_file = project_root / args.env_file
    
    print("=" * 60)
    print("🔐 Sync .env to Azure Key Vault")
    print("=" * 60)
    print(f"\n📄 Reading: {env_file}")
    
    # Parse .env file
    env_vars = parse_env_file(env_file)
    print(f"✅ Found {len(env_vars)} environment variables")
    
    # Sync to Key Vault
    created, updated, skipped = sync_secrets_to_keyvault(
        env_vars=env_vars,
        key_vault_url=args.key_vault_url,
        dry_run=args.dry_run,
        use_managed_identity=args.use_managed_identity,
        skip_existing=args.skip_existing
    )
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 Summary")
    print("=" * 60)
    print(f"✅ Created: {len(created)}")
    print(f"🔄 Updated: {len(updated)}")
    print(f"⏭️  Skipped: {len(skipped)}")
    
    # Generate mapping
    if args.generate_mapping or not args.dry_run:
        print("\n" + "=" * 60)
        print("📝 Generated Secret Mapping")
        print("=" * 60)
        mapping = generate_mapping(env_vars)
        print(f"\nALDAR_AZURE_KEY_VAULT_SECRET_MAPPING=\"{mapping}\"")
        print("\n💡 Add this to your .env file to use Key Vault secrets")
    
    print("\n✅ Done!")


if __name__ == "__main__":
    main()

