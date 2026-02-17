#!/bin/bash

# Quick Cosmos DB Logging Status Check
# Usage: ./scripts/check_cosmos_status.sh

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Cosmos DB Logging Status Check"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "❌ .env file not found!"
    echo "   Create one from .env.example"
    exit 1
fi

# Check COSMOS_LOGGING_ENABLED
if grep -q "COSMOS_LOGGING_ENABLED=true" .env 2>/dev/null; then
    echo "✅ COSMOS_LOGGING_ENABLED=true"
else
    echo "❌ COSMOS_LOGGING_ENABLED is not true"
    echo "   Add: COSMOS_LOGGING_ENABLED=true"
fi

# Check COSMOS_ENDPOINT exists
if grep -q "COSMOS_ENDPOINT=" .env 2>/dev/null; then
    endpoint=$(grep "COSMOS_ENDPOINT=" .env | head -1 | cut -d'=' -f2-)
    
    if [ -z "$endpoint" ]; then
        echo "❌ COSMOS_ENDPOINT is empty"
    elif echo "$endpoint" | grep -q "AccountEndpoint=.*AccountKey="; then
        echo "✅ COSMOS_ENDPOINT format valid"
        echo "   (Connection string detected)"
    else
        echo "❌ COSMOS_ENDPOINT format invalid"
        echo "   Current: ${endpoint:0:50}..."
        echo ""
        echo "   Expected: AccountEndpoint=https://...;AccountKey=...;"
        echo ""
        echo "   📖 See: docs/HOW_TO_GET_COSMOS_CONNECTION_STRING.md"
    fi
else
    echo "❌ COSMOS_ENDPOINT not set"
    echo "   Add connection string from Azure Portal"
fi

echo ""
echo "───────────────────────────────────────────────────────────────"
echo ""

# Run Python diagnostic
echo "Running detailed diagnostic..."
echo ""
poetry run python scripts/diagnose_cosmos_logging.py

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo ""

