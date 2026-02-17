#!/usr/bin/env python3
"""Test Azure Key Vault connectivity and verify secrets."""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_file = project_root / ".env"
    if env_file.exists():
        load_dotenv(env_file)
        print("✅ Loaded .env file")
except ImportError:
    pass  # dotenv not available, skip

try:
    from azure.identity import ClientSecretCredential, DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
    from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
except ImportError:
    print("❌ Azure SDK not installed. Install with: poetry install")
    sys.exit(1)


def get_key_vault_client():
    """Create Key Vault client with appropriate authentication."""
    vault_url = os.getenv("ALDAR_AZURE_KEY_VAULT_URL", "https://aldar-middleware-vault.vault.azure.net/")
    
    # Extract Key Vault tenant from URL (default to known tenant)
    key_vault_tenant = "902eab19-c66d-43b3-91e5-d4c00ec64e88"
    
    # Try Service Principal first (for local dev)
    tenant_id = os.getenv("AZURE_TENANT_ID") or os.getenv("ALDAR_AZURE_TENANT_ID")
    client_id = os.getenv("AZURE_CLIENT_ID") or os.getenv("ALDAR_AZURE_CLIENT_ID")
    client_secret = os.getenv("AZURE_CLIENT_SECRET") or os.getenv("ALDAR_AZURE_CLIENT_SECRET")
    
    if tenant_id and client_id and client_secret:
        print("🔐 Using Service Principal authentication")
        
        # If Service Principal tenant is different from Key Vault tenant, enable cross-tenant access
        if tenant_id != key_vault_tenant:
            print(f"   ⚠️  Cross-tenant access: SP tenant {tenant_id[:8]}... → Key Vault tenant {key_vault_tenant[:8]}...")
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
    else:
        print("🔐 Trying DefaultAzureCredential (Azure CLI, Managed Identity, etc.)")
        credential = DefaultAzureCredential()
    
    client = SecretClient(vault_url=vault_url, credential=credential)
    return client


def test_connection(client):
    """Test Key Vault connection by listing secrets."""
    print("\n" + "=" * 60)
    print("🔗 Testing Key Vault Connection")
    print("=" * 60)
    
    try:
        print("\n📋 Listing secrets in Key Vault...")
        secrets = list(client.list_properties_of_secrets())
        
        if secrets:
            print(f"✅ Connected! Found {len(secrets)} secrets:")
            for secret in sorted(secrets, key=lambda x: x.name)[:20]:  # Show first 20
                print(f"   ✓ {secret.name}")
            if len(secrets) > 20:
                print(f"   ... and {len(secrets) - 20} more secrets")
        else:
            print("⚠️  Connected but no secrets found")
        
        return True, secrets
        
    except HttpResponseError as e:
        print(f"❌ Connection failed: {e.status_code} - {e.message}")
        if "401" in str(e.status_code) or "AADSTS" in str(e.message):
            print("\n💡 Authentication failed. To test Key Vault locally, you need:")
            print("\n   1. Service Principal credentials (create via Azure Portal):")
            print("      - Go to Azure Portal → App registrations")
            print("      - Create new registration: aldar-middleware-keyvault-sp")
            print("      - Create client secret")
            print("      - Grant 'Key Vault Secrets Officer' role")
            print("\n   2. Add to .env file:")
            print("      AZURE_TENANT_ID=902eab19-c66d-43b3-91e5-d4c00ec64e88")
            print("      AZURE_CLIENT_ID=<your-client-id>")
            print("      AZURE_CLIENT_SECRET=<your-client-secret>")
            print("\n   See PORTAL_SP_SETUP.md for detailed instructions")
        return False, []
    except Exception as e:
        print(f"❌ Error: {e}")
        return False, []


def test_secret_retrieval(client, secret_name):
    """Test retrieving a specific secret."""
    try:
        secret = client.get_secret(secret_name)
        # Mask the value for display
        value = secret.value
        if len(value) > 20:
            masked = value[:8] + "..." + value[-8:]
        else:
            masked = "***" if len(value) > 0 else "(empty)"
        return True, masked
    except ResourceNotFoundError:
        return False, "NOT FOUND"
    except Exception as e:
        return False, f"ERROR: {str(e)[:50]}"


def main():
    """Main test function."""
    print("=" * 60)
    print("🔐 Azure Key Vault Verification Test")
    print("=" * 60)
    
    # Check configuration
    vault_url = os.getenv("ALDAR_AZURE_KEY_VAULT_URL")
    if not vault_url:
        vault_url = "https://aldar-middleware-vault.vault.azure.net/"
        print(f"\n⚠️  ALDAR_AZURE_KEY_VAULT_URL not set, using default: {vault_url}")
    else:
        print(f"\n✅ Key Vault URL: {vault_url}")
    
    # Check authentication method
    tenant_id = os.getenv("AZURE_TENANT_ID") or os.getenv("ALDAR_AZURE_TENANT_ID")
    client_id = os.getenv("AZURE_CLIENT_ID") or os.getenv("ALDAR_AZURE_CLIENT_ID")
    client_secret = os.getenv("AZURE_CLIENT_SECRET") or os.getenv("ALDAR_AZURE_CLIENT_SECRET")
    
    if tenant_id and client_id and client_secret:
        print("✅ Service Principal credentials found")
        print(f"   Tenant ID: {tenant_id[:8]}...")
        print(f"   Client ID: {client_id[:8]}...")
    else:
        print("⚠️  Service Principal credentials not found")
        print("   Set AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET in .env")
        print("   Will try DefaultAzureCredential (Azure CLI, etc.)")
    
    print("\n" + "-" * 60)
    
    # Create client
    try:
        client = get_key_vault_client()
    except Exception as e:
        print(f"❌ Failed to create Key Vault client: {e}")
        sys.exit(1)
    
    # Test connection
    success, secrets = test_connection(client)
    
    if not success:
        print("\n❌ Cannot proceed - connection failed")
        sys.exit(1)
    
    # Test specific secrets from mapping
    print("\n" + "=" * 60)
    print("📥 Testing Secret Retrieval")
    print("=" * 60)
    
    # Get secret mapping from env
    mapping_str = os.getenv("ALDAR_AZURE_KEY_VAULT_SECRET_MAPPING", "")
    
    if mapping_str:
        print("\n📋 Testing secrets from ALDAR_AZURE_KEY_VAULT_SECRET_MAPPING...")
        
        # Parse mapping
        mappings = {}
        for item in mapping_str.split(","):
            item = item.strip()
            if "=" in item:
                env_var, secret_name = item.split("=", 1)
                mappings[env_var.strip()] = secret_name.strip()
        
        if mappings:
            print(f"\nTesting {len(mappings)} secrets...\n")
            
            success_count = 0
            failed = []
            
            for env_var, secret_name in sorted(mappings.items()):
                success, result = test_secret_retrieval(client, secret_name)
                if success:
                    print(f"✅ {env_var:40} → {secret_name:35} : {result}")
                    success_count += 1
                else:
                    print(f"❌ {env_var:40} → {secret_name:35} : {result}")
                    failed.append((env_var, secret_name))
            
            print("\n" + "-" * 60)
            print(f"📊 Results: {success_count}/{len(mappings)} secrets retrieved successfully")
            
            if failed:
                print(f"\n⚠️  {len(failed)} secrets failed:")
                for env_var, secret_name in failed:
                    print(f"   - {secret_name} ({env_var})")
        else:
            print("⚠️  No mappings found in ALDAR_AZURE_KEY_VAULT_SECRET_MAPPING")
    else:
        print("⚠️  ALDAR_AZURE_KEY_VAULT_SECRET_MAPPING not set")
        print("\nTesting a few common secrets manually...\n")
        
        # Test some common secrets
        test_secrets = [
            "aiq-db-user",
            "aiq-db-pass",
            "aiq-jwt-secret-key",
            "aiq-redis-password"
        ]
        
        for secret_name in test_secrets:
            success, result = test_secret_retrieval(client, secret_name)
            if success:
                print(f"✅ {secret_name:35} : {result}")
            else:
                print(f"❌ {secret_name:35} : {result}")
    
    print("\n" + "=" * 60)
    print("✅ Key Vault Test Complete!")
    print("=" * 60)
    print("\n💡 Tips:")
    print("   - For local dev: Use Service Principal (set AZURE_CLIENT_ID, etc.)")
    print("   - For AKS/production: Use Managed Identity")
    print("   - Make sure secrets exist in Key Vault with correct names")


if __name__ == "__main__":
    main()
