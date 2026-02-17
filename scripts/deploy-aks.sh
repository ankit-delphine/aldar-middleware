#!/bin/bash

# AKS Deployment Script for AIQ Backend
# This script deploys the complete AIQ Backend infrastructure on AKS

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
NAMESPACE="aldar-middleware"
RESOURCE_GROUP="adq"
AKS_CLUSTER="aldar-middleware-aks"
LOCATION="uaenorth"
IMAGE_NAME="aldar-middleware"
IMAGE_TAG="latest"

echo -e "${BLUE}🚀 Starting AKS Deployment for AIQ Backend${NC}"

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check prerequisites
echo -e "${YELLOW}📋 Checking prerequisites...${NC}"

if ! command_exists az; then
    echo -e "${RED}❌ Azure CLI not found. Please install Azure CLI.${NC}"
    exit 1
fi

if ! command_exists kubectl; then
    echo -e "${RED}❌ kubectl not found. Please install kubectl.${NC}"
    exit 1
fi

if ! command_exists docker; then
    echo -e "${RED}❌ Docker not found. Please install Docker.${NC}"
    exit 1
fi

echo -e "${GREEN}✅ All prerequisites found${NC}"

# Login to Azure
echo -e "${YELLOW}🔐 Logging into Azure...${NC}"
az login

# Check if resource group exists
echo -e "${YELLOW}📦 Checking resource group...${NC}"
if az group show --name $RESOURCE_GROUP >/dev/null 2>&1; then
    echo -e "${GREEN}✅ Using existing resource group: $RESOURCE_GROUP${NC}"
else
    echo -e "${RED}❌ Resource group $RESOURCE_GROUP not found${NC}"
    echo -e "${YELLOW}💡 Please create the resource group first or update the script with correct resource group name${NC}"
    exit 1
fi

# Check if AKS cluster exists and its state
echo -e "${YELLOW}🔍 Checking AKS cluster status...${NC}"
if az aks show --resource-group $RESOURCE_GROUP --name $AKS_CLUSTER >/dev/null 2>&1; then
    CLUSTER_STATE=$(az aks show --resource-group $RESOURCE_GROUP --name $AKS_CLUSTER --query 'provisioningState' -o tsv)
    echo -e "${BLUE}Cluster state: $CLUSTER_STATE${NC}"
    
    if [ "$CLUSTER_STATE" = "Failed" ] || [ "$CLUSTER_STATE" = "Deleting" ]; then
        echo -e "${YELLOW}⚠️  Cluster is in $CLUSTER_STATE state. Deleting and recreating...${NC}"
        az aks delete --resource-group $RESOURCE_GROUP --name $AKS_CLUSTER --yes --no-wait
        echo -e "${YELLOW}⏳ Waiting for cluster deletion to complete...${NC}"
        while az aks show --resource-group $RESOURCE_GROUP --name $AKS_CLUSTER >/dev/null 2>&1; do
            echo -e "${BLUE}Still deleting...${NC}"
            sleep 30
        done
        echo -e "${GREEN}✅ Cluster deletion completed${NC}"
    elif [ "$CLUSTER_STATE" = "Succeeded" ]; then
        echo -e "${GREEN}✅ Cluster already exists and is healthy${NC}"
    else
        echo -e "${YELLOW}⏳ Cluster is in $CLUSTER_STATE state. Waiting for completion...${NC}"
        az aks wait --resource-group $RESOURCE_GROUP --name $AKS_CLUSTER --created --interval 30 --timeout 1800
    fi
fi

# Create AKS cluster if it doesn't exist
if ! az aks show --resource-group $RESOURCE_GROUP --name $AKS_CLUSTER >/dev/null 2>&1; then
    echo -e "${YELLOW}🏗️  Creating AKS cluster...${NC}"
    echo -e "${BLUE}Resource Group: $RESOURCE_GROUP${NC}"
    echo -e "${BLUE}Cluster Name: $AKS_CLUSTER${NC}"
    echo -e "${BLUE}Location: $LOCATION${NC}"

    az aks create \
        --resource-group $RESOURCE_GROUP \
        --name $AKS_CLUSTER \
        --location "$LOCATION" \
        --node-count 1 \
        --node-vm-size Standard_B2s \
        --enable-managed-identity \
        --generate-ssh-keys \
        --tags CNAME=Delphi BU=AIQ CREATED_BY=anshukla@delphime.com PURPOSE=AIQ-Backend-Development OWNER=anshukla@delphime.com \
        --no-wait

    echo -e "${YELLOW}⏳ Waiting for cluster creation to complete...${NC}"
    az aks wait --resource-group $RESOURCE_GROUP --name $AKS_CLUSTER --created --interval 30 --timeout 1800
    echo -e "${GREEN}✅ AKS cluster created successfully${NC}"
fi

# Get AKS credentials
echo -e "${YELLOW}🔑 Getting AKS credentials...${NC}"
az aks get-credentials --resource-group $RESOURCE_GROUP --name $AKS_CLUSTER

# Verify cluster is ready
echo -e "${YELLOW}🔍 Verifying cluster is ready...${NC}"
kubectl cluster-info
if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Failed to connect to AKS cluster. Please check cluster status.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Successfully connected to AKS cluster${NC}"

# Create namespace
echo -e "${YELLOW}📁 Creating namespace...${NC}"
kubectl apply -f k8s/namespace.yaml

# Apply configurations
echo -e "${YELLOW}⚙️  Applying configurations...${NC}"
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secrets.yaml

# Deploy PostgreSQL
echo -e "${YELLOW}🐘 Deploying PostgreSQL...${NC}"
kubectl apply -f k8s/postgres-deployment.yaml

# Deploy Redis
echo -e "${YELLOW}🔴 Deploying Redis...${NC}"
kubectl apply -f k8s/redis-deployment.yaml

# Wait for databases to be ready
echo -e "${YELLOW}⏳ Waiting for databases to be ready...${NC}"
kubectl wait --for=condition=available --timeout=300s deployment/aiq-postgres -n $NAMESPACE
kubectl wait --for=condition=available --timeout=300s deployment/aiq-redis -n $NAMESPACE

# Build and push Docker image
echo -e "${YELLOW}🐳 Building Docker image...${NC}"
docker build -t $IMAGE_NAME:$IMAGE_TAG .

# Get ACR login server (if using ACR)
ACR_NAME="aiqbackendacr"
echo -e "${YELLOW}📦 Creating Azure Container Registry...${NC}"
az acr create --resource-group $RESOURCE_GROUP --name $ACR_NAME --sku Basic

# Login to ACR
az acr login --name $ACR_NAME

# Tag and push image
docker tag $IMAGE_NAME:$IMAGE_TAG $ACR_NAME.azurecr.io/$IMAGE_NAME:$IMAGE_TAG
docker push $ACR_NAME.azurecr.io/$IMAGE_NAME:$IMAGE_TAG

# Update image in deployment files
sed -i "s|aldar-middleware:latest|$ACR_NAME.azurecr.io/$IMAGE_NAME:$IMAGE_TAG|g" k8s/aldar-middleware-deployment.yaml
sed -i "s|aldar-middleware:latest|$ACR_NAME.azurecr.io/$IMAGE_NAME:$IMAGE_TAG|g" k8s/celery-worker-deployment.yaml

# Deploy AIQ Backend
echo -e "${YELLOW}🚀 Deploying AIQ Backend...${NC}"
kubectl apply -f k8s/aldar-middleware-deployment.yaml

# Deploy Celery Workers
echo -e "${YELLOW}⚡ Deploying Celery Workers...${NC}"
kubectl apply -f k8s/celery-worker-deployment.yaml

# Deploy Azure Identity (if using managed identity)
echo -e "${YELLOW}🔐 Deploying Azure Identity...${NC}"
kubectl apply -f k8s/azure-identity.yaml

# Deploy Ingress
echo -e "${YELLOW}🌐 Deploying Ingress...${NC}"
kubectl apply -f k8s/ingress.yaml

# Wait for deployments
echo -e "${YELLOW}⏳ Waiting for deployments to be ready...${NC}"
kubectl wait --for=condition=available --timeout=300s deployment/aldar-middleware -n $NAMESPACE
kubectl wait --for=condition=available --timeout=300s deployment/aiq-celery-worker -n $NAMESPACE

# Get service information
echo -e "${GREEN}🎉 Deployment completed!${NC}"
echo -e "${BLUE}📊 Service Information:${NC}"

# Get LoadBalancer IP
EXTERNAL_IP=$(kubectl get service aldar-middleware-service -n $NAMESPACE -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
if [ -z "$EXTERNAL_IP" ]; then
    echo -e "${YELLOW}⏳ LoadBalancer IP is still being assigned...${NC}"
    echo -e "${BLUE}Run 'kubectl get service aldar-middleware-service -n $NAMESPACE' to check status${NC}"
else
    echo -e "${GREEN}🌐 AIQ Backend is available at: http://$EXTERNAL_IP${NC}"
    echo -e "${GREEN}📚 API Documentation: http://$EXTERNAL_IP/docs${NC}"
    echo -e "${GREEN}🏥 Health Check: http://$EXTERNAL_IP/api/v1/health${NC}"
fi

# Show pod status
echo -e "${BLUE}📋 Pod Status:${NC}"
kubectl get pods -n $NAMESPACE

# Show services
echo -e "${BLUE}🔗 Services:${NC}"
kubectl get services -n $NAMESPACE

echo -e "${GREEN}✅ AKS deployment completed successfully!${NC}"
echo -e "${YELLOW}📝 Next steps:${NC}"
echo -e "1. Update your DNS to point to the LoadBalancer IP"
echo -e "2. Configure SSL certificates if needed"
echo -e "3. Update Azure Service Bus connection string in secrets"
echo -e "4. Test the application endpoints"
