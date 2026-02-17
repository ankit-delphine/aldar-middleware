#!/bin/bash

# Grant Contributor Role Script
# This script grants Contributor role to a user on a resource group
# Requires Owner or User Access Administrator permissions

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
RESOURCE_GROUP="${1:-adq}"
USER_EMAIL="${2:-}"

if [ -z "$USER_EMAIL" ]; then
    echo -e "${RED}❌ Usage: $0 <resource-group> <user-email>${NC}"
    echo -e "${YELLOW}Example: $0 adq user@example.com${NC}"
    exit 1
fi

echo -e "${BLUE}🔐 Granting Contributor Role${NC}"
echo -e "${BLUE}Resource Group: $RESOURCE_GROUP${NC}"
echo -e "${BLUE}User: $USER_EMAIL${NC}"
echo ""

# Check if Azure CLI is installed
if ! command -v az &> /dev/null; then
    echo -e "${RED}❌ Azure CLI not found. Please install Azure CLI.${NC}"
    exit 1
fi

# Check if logged in
echo -e "${YELLOW}🔐 Checking Azure login status...${NC}"
CURRENT_USER=$(az account show --query user.name -o tsv 2>/dev/null || echo "")
if [ -z "$CURRENT_USER" ]; then
    echo -e "${YELLOW}⚠️  Not logged in. Logging in...${NC}"
    az login
    CURRENT_USER=$(az account show --query user.name -o tsv)
fi
echo -e "${GREEN}✅ Logged in as: $CURRENT_USER${NC}"

# Get subscription ID
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
echo -e "${BLUE}Subscription ID: $SUBSCRIPTION_ID${NC}"
echo ""

# Check if resource group exists
echo -e "${YELLOW}📦 Checking resource group...${NC}"
if ! az group show --name $RESOURCE_GROUP >/dev/null 2>&1; then
    echo -e "${RED}❌ Resource group '$RESOURCE_GROUP' not found${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Resource group exists${NC}"

# Get user object ID
echo -e "${YELLOW}👤 Looking up user...${NC}"
USER_OBJECT_ID=$(az ad user show --id "$USER_EMAIL" --query id -o tsv 2>/dev/null || echo "")
if [ -z "$USER_OBJECT_ID" ]; then
    echo -e "${RED}❌ User '$USER_EMAIL' not found in Azure AD${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Found user: $USER_EMAIL (Object ID: $USER_OBJECT_ID)${NC}"
echo ""

# Check if role already assigned
echo -e "${YELLOW}🔍 Checking existing role assignments...${NC}"
EXISTING_ROLE=$(az role assignment list \
    --resource-group $RESOURCE_GROUP \
    --assignee $USER_OBJECT_ID \
    --query "[?roleDefinitionName=='Contributor']" \
    -o tsv 2>/dev/null || echo "")

if [ -n "$EXISTING_ROLE" ]; then
    echo -e "${YELLOW}⚠️  User already has 'Contributor' role on this resource group${NC}"
    echo -e "${GREEN}✅ No action needed${NC}"
    exit 0
fi

# Grant Contributor role
echo -e "${YELLOW}🔐 Granting Contributor role...${NC}"
az role assignment create \
    --role "Contributor" \
    --assignee "$USER_OBJECT_ID" \
    --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP" \
    --output none

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Successfully granted 'Contributor' role to $USER_EMAIL${NC}"
    echo -e "${GREEN}✅ User can now create Container App Environments${NC}"
else
    echo -e "${RED}❌ Failed to grant role. You may not have sufficient permissions.${NC}"
    echo -e "${YELLOW}💡 You need 'Owner' or 'User Access Administrator' role to grant permissions${NC}"
    exit 1
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✅ Role assignment completed successfully!${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

