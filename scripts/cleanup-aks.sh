#!/bin/bash

# AKS Cleanup Script for AIQ Backend
# This script cleans up all resources created for AIQ Backend

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
NAMESPACE="aldar-middleware"
RESOURCE_GROUP="aldar-middleware-rg"
AKS_CLUSTER="aldar-middleware-aks"
ACR_NAME="aiqbackendacr"

echo -e "${BLUE}🧹 Starting AKS Cleanup for AIQ Backend${NC}"

# Confirmation prompt
echo -e "${YELLOW}⚠️  This will delete all AIQ Backend resources. Are you sure? (y/N)${NC}"
read -r response
if [[ ! "$response" =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}❌ Cleanup cancelled.${NC}"
    exit 0
fi

# Delete Kubernetes resources
echo -e "${YELLOW}🗑️  Deleting Kubernetes resources...${NC}"

# Delete namespace (this will delete all resources in the namespace)
if kubectl get namespace $NAMESPACE >/dev/null 2>&1; then
    echo -e "${YELLOW}Deleting namespace $NAMESPACE...${NC}"
    kubectl delete namespace $NAMESPACE
    echo -e "${GREEN}✅ Namespace deleted${NC}"
else
    echo -e "${YELLOW}⚠️  Namespace $NAMESPACE not found${NC}"
fi

# Delete AKS cluster
echo -e "${YELLOW}🏗️  Deleting AKS cluster...${NC}"
if az aks show --resource-group $RESOURCE_GROUP --name $AKS_CLUSTER >/dev/null 2>&1; then
    echo -e "${YELLOW}Deleting AKS cluster $AKS_CLUSTER...${NC}"
    az aks delete --resource-group $RESOURCE_GROUP --name $AKS_CLUSTER --yes
    echo -e "${GREEN}✅ AKS cluster deleted${NC}"
else
    echo -e "${YELLOW}⚠️  AKS cluster $AKS_CLUSTER not found${NC}"
fi

# Delete Azure Container Registry
echo -e "${YELLOW}📦 Deleting Azure Container Registry...${NC}"
if az acr show --resource-group $RESOURCE_GROUP --name $ACR_NAME >/dev/null 2>&1; then
    echo -e "${YELLOW}Deleting ACR $ACR_NAME...${NC}"
    az acr delete --resource-group $RESOURCE_GROUP --name $ACR_NAME --yes
    echo -e "${GREEN}✅ ACR deleted${NC}"
else
    echo -e "${YELLOW}⚠️  ACR $ACR_NAME not found${NC}"
fi

# Delete resource group
echo -e "${YELLOW}📦 Deleting resource group...${NC}"
if az group show --name $RESOURCE_GROUP >/dev/null 2>&1; then
    echo -e "${YELLOW}Deleting resource group $RESOURCE_GROUP...${NC}"
    az group delete --name $RESOURCE_GROUP --yes
    echo -e "${GREEN}✅ Resource group deleted${NC}"
else
    echo -e "${YELLOW}⚠️  Resource group $RESOURCE_GROUP not found${NC}"
fi

# Clean up local Docker images
echo -e "${YELLOW}🐳 Cleaning up local Docker images...${NC}"
docker rmi aldar-middleware:latest 2>/dev/null || echo "Image not found locally"
docker rmi $ACR_NAME.azurecr.io/aldar-middleware:latest 2>/dev/null || echo "Image not found locally"

# Clean up kubectl context
echo -e "${YELLOW}🔑 Cleaning up kubectl context...${NC}"
kubectl config delete-context $AKS_CLUSTER 2>/dev/null || echo "Context not found"
kubectl config delete-cluster $AKS_CLUSTER 2>/dev/null || echo "Cluster not found"

echo -e "${GREEN}🎉 Cleanup completed successfully!${NC}"
echo -e "${BLUE}📋 Summary of deleted resources:${NC}"
echo -e "• AKS Cluster: $AKS_CLUSTER"
echo -e "• Resource Group: $RESOURCE_GROUP"
echo -e "• Container Registry: $ACR_NAME"
echo -e "• Kubernetes Namespace: $NAMESPACE"
echo -e "• All associated resources (VMs, disks, networking, etc.)"

echo -e "${YELLOW}💡 Note: Some resources may take a few minutes to be completely deleted.${NC}"
