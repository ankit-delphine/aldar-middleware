#!/bin/bash
# Simple wrapper script to sync .env to Azure Key Vault

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

# Default Key Vault URL (can be overridden)
KEY_VAULT_URL="${ALDAR_AZURE_KEY_VAULT_URL:-https://aldar-middleware-vault.vault.azure.net/}"

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "❌ Error: .env file not found in $PROJECT_ROOT"
    exit 1
fi

echo "🔐 Syncing .env to Azure Key Vault..."
echo ""

# Run the Python script
python3 "$SCRIPT_DIR/sync-env-to-keyvault.py" \
    --key-vault-url "$KEY_VAULT_URL" \
    --generate-mapping \
    "$@"

