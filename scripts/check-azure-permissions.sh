#!/bin/bash

# Azure Permissions Check Script for Container Apps
# This script checks your current permissions and provides guidance

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
RESOURCE_GROUP="${1:-adq}"
SUBSCRIPTION_ID="${2:-}"

echo -e "${BLUE}🔍 Checking Azure Permissions for Container Apps${NC}"
echo -e "${BLUE}Resource Group: $RESOURCE_GROUP${NC}"
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

# Get current subscription
if [ -z "$SUBSCRIPTION_ID" ]; then
    SUBSCRIPTION_ID=$(az account show --query id -o tsv)
fi
echo -e "${BLUE}Subscription ID: $SUBSCRIPTION_ID${NC}"
echo ""

# Check if resource group exists
echo -e "${YELLOW}📦 Checking resource group...${NC}"
if az group show --name $RESOURCE_GROUP >/dev/null 2>&1; then
    echo -e "${GREEN}✅ Resource group '$RESOURCE_GROUP' exists${NC}"
else
    echo -e "${RED}❌ Resource group '$RESOURCE_GROUP' not found${NC}"
    echo -e "${YELLOW}💡 Please create the resource group first or check the name${NC}"
    exit 1
fi

# Get current user's object ID or principal name
echo -e "${YELLOW}👤 Getting your user information...${NC}"
USER_OBJECT_ID=$(az ad signed-in-user show --query id -o tsv 2>/dev/null || echo "")
if [ -z "$USER_OBJECT_ID" ]; then
    # Fallback to using principal name (email) if object ID lookup fails
    echo -e "${YELLOW}⚠️  Could not get user object ID, using principal name instead...${NC}"
    ASSIGNEE="$CURRENT_USER"
else
    ASSIGNEE="$USER_OBJECT_ID"
    echo -e "${GREEN}✅ User Object ID: $USER_OBJECT_ID${NC}"
fi
echo ""

# Check current role assignments
echo -e "${YELLOW}🔍 Checking your role assignments on resource group...${NC}"
ROLE_ASSIGNMENTS=$(az role assignment list \
    --resource-group $RESOURCE_GROUP \
    --assignee "$ASSIGNEE" \
    --query "[].{Role:roleDefinitionName, Scope:scope}" \
    -o table 2>/dev/null || echo "")

if [ -z "$ROLE_ASSIGNMENTS" ] || [ "$ROLE_ASSIGNMENTS" = "Role    Scope" ]; then
    echo -e "${RED}❌ No role assignments found${NC}"
    echo -e "${YELLOW}⚠️  You don't have any permissions on this resource group${NC}"
else
    echo -e "${GREEN}✅ Current role assignments:${NC}"
    echo "$ROLE_ASSIGNMENTS"
fi
echo ""

# Check for Contributor role specifically
HAS_CONTRIBUTOR=$(az role assignment list \
    --resource-group $RESOURCE_GROUP \
    --assignee "$ASSIGNEE" \
    --query "[?roleDefinitionName=='Contributor']" \
    -o tsv 2>/dev/null || echo "")

if [ -z "$HAS_CONTRIBUTOR" ]; then
    echo -e "${RED}❌ Missing 'Contributor' role on resource group '$RESOURCE_GROUP'${NC}"
    echo ""
    echo -e "${YELLOW}📋 To fix this, you need to:${NC}"
    echo ""
    echo -e "${BLUE}Option 1: Request permissions from an admin${NC}"
    echo -e "   Ask someone with 'Owner' or 'User Access Administrator' role to run:"
    echo ""
    echo -e "   ${GREEN}az role assignment create \\${NC}"
    echo -e "   ${GREEN}  --role 'Contributor' \\${NC}"
    if [ -n "$USER_OBJECT_ID" ]; then
        echo -e "   ${GREEN}  --assignee '$USER_OBJECT_ID' \\${NC}"
    else
        echo -e "   ${GREEN}  --assignee '$CURRENT_USER' \\${NC}"
    fi
    echo -e "   ${GREEN}  --scope /subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP${NC}"
    echo ""
    echo -e "${BLUE}Option 2: If you have Owner/User Access Administrator role${NC}"
    echo -e "   Run the command above yourself"
    echo ""
    echo -e "${BLUE}Option 3: Use a different resource group where you have permissions${NC}"
    echo ""
    exit 1
else
    echo -e "${GREEN}✅ You have 'Contributor' role on resource group '$RESOURCE_GROUP'${NC}"
    echo ""
    echo -e "${GREEN}✅ You should be able to create Container App Environments${NC}"
fi

# Check for Container Apps permissions
echo -e "${YELLOW}🔍 Checking Container Apps specific permissions...${NC}"
echo -e "${BLUE}Note: Container Apps require 'Contributor' role at minimum${NC}"
echo ""

# Summary
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}📊 Summary${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "User: $CURRENT_USER"
echo -e "Resource Group: $RESOURCE_GROUP"
echo -e "Subscription: $SUBSCRIPTION_ID"
if [ -z "$HAS_CONTRIBUTOR" ]; then
    echo -e "Status: ${RED}❌ Missing Contributor role${NC}"
else
    echo -e "Status: ${GREEN}✅ Has Contributor role${NC}"
fi
echo ""

