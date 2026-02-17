#!/bin/bash

# Component-based AKS Deployment Script for AIQ Backend
# This script allows deploying individual components or all components

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
ACR_NAME="aiqbackendacr"

# Component configurations
declare -A COMPONENTS=(
    ["main"]="aldar-middleware-main"
    ["worker"]="aldar-middleware-worker"
    ["beat"]="aldar-middleware-beat"
    ["flower"]="aldar-middleware-flower"
)

declare -A DOCKERFILES=(
    ["main"]="Dockerfile.main"
    ["worker"]="Dockerfile.worker"
    ["beat"]="Dockerfile.beat"
    ["flower"]="Dockerfile.flower"
)

declare -A DEPLOYMENTS=(
    ["main"]="k8s/aldar-middleware-main-deployment.yaml"
    ["worker"]="k8s/aldar-middleware-worker-deployment.yaml"
    ["beat"]="k8s/aldar-middleware-beat-deployment.yaml"
    ["flower"]="k8s/aldar-middleware-flower-deployment.yaml"
)

# Function to show usage
show_usage() {
    echo -e "${BLUE}Usage: $0 [OPTIONS] [COMPONENTS]${NC}"
    echo ""
    echo -e "${YELLOW}Options:${NC}"
    echo "  -h, --help              Show this help message"
    echo "  --build-only           Only build Docker images, don't deploy"
    echo "  --deploy-only          Only deploy, don't build images"
    echo "  --all                  Deploy all components"
    echo ""
    echo -e "${YELLOW}Components:${NC}"
    echo "  main                   Main AIQ Backend application"
    echo "  worker                 Celery worker"
    echo "  beat                   Celery beat scheduler"
    echo "  flower                 Celery flower monitoring"
    echo ""
    echo -e "${YELLOW}Examples:${NC}"
    echo "  $0 --all                                    # Deploy all components"
    echo "  $0 main worker                             # Deploy only main and worker"
    echo "  $0 --build-only main                       # Only build main component"
    echo "  $0 --deploy-only worker beat               # Deploy worker and beat (skip build)"
}

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check prerequisites
check_prerequisites() {
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
}

# Function to login to Azure
azure_login() {
    echo -e "${YELLOW}🔐 Logging into Azure...${NC}"
    az login
}

# Function to check AKS cluster
check_aks_cluster() {
    echo -e "${YELLOW}🔍 Checking AKS cluster...${NC}"
    
    if ! az aks show --resource-group $RESOURCE_GROUP --name $AKS_CLUSTER >/dev/null 2>&1; then
        echo -e "${RED}❌ AKS cluster not found. Please create it first.${NC}"
        exit 1
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
}

# Function to check ACR
check_acr() {
    echo -e "${YELLOW}📦 Checking Azure Container Registry...${NC}"
    
    if ! az acr show --resource-group $RESOURCE_GROUP --name $ACR_NAME >/dev/null 2>&1; then
        echo -e "${RED}❌ ACR not found. Please create it first.${NC}"
        exit 1
    fi
    
    # Login to ACR
    echo -e "${YELLOW}🔑 Logging into ACR...${NC}"
    az acr login --name $ACR_NAME
    echo -e "${GREEN}✅ Successfully logged into ACR${NC}"
}

# Function to build Docker image for a component
build_component() {
    local component=$1
    local image_name=${COMPONENTS[$component]}
    local dockerfile=${DOCKERFILES[$component]}
    
    echo -e "${YELLOW}🐳 Building $component component...${NC}"
    echo -e "${BLUE}Image: $image_name${NC}"
    echo -e "${BLUE}Dockerfile: $dockerfile${NC}"
    
    docker build -f $dockerfile -t $image_name:latest .
    
    # Tag for ACR
    docker tag $image_name:latest $ACR_NAME.azurecr.io/$image_name:latest
    
    # Push to ACR
    echo -e "${YELLOW}📤 Pushing $component to ACR...${NC}"
    docker push $ACR_NAME.azurecr.io/$image_name:latest
    
    echo -e "${GREEN}✅ $component component built and pushed successfully${NC}"
}

# Function to deploy a component
deploy_component() {
    local component=$1
    local deployment_file=${DEPLOYMENTS[$component]}
    
    echo -e "${YELLOW}🚀 Deploying $component component...${NC}"
    
    # Update image in deployment file
    sed -i "s|aiqbackendacr.azurecr.io/aldar-middleware-$component:latest|$ACR_NAME.azurecr.io/aldar-middleware-$component:latest|g" $deployment_file
    
    # Apply deployment
    kubectl apply -f $deployment_file
    
    echo -e "${GREEN}✅ $component component deployed successfully${NC}"
}

# Function to wait for deployment
wait_for_deployment() {
    local component=$1
    local deployment_name=${COMPONENTS[$component]}
    
    echo -e "${YELLOW}⏳ Waiting for $component deployment to be ready...${NC}"
    kubectl wait --for=condition=available --timeout=300s deployment/$deployment_name -n $NAMESPACE
    echo -e "${GREEN}✅ $component deployment is ready${NC}"
}

# Function to show deployment status
show_status() {
    echo -e "${GREEN}🎉 Deployment completed!${NC}"
    echo -e "${BLUE}📊 Component Status:${NC}"
    
    for component in "${!COMPONENTS[@]}"; do
        local deployment_name=${COMPONENTS[$component]}
        echo -e "${BLUE}$component:${NC}"
        kubectl get deployment $deployment_name -n $NAMESPACE
        echo ""
    done
    
    echo -e "${BLUE}📋 All Pods:${NC}"
    kubectl get pods -n $NAMESPACE
    
    echo -e "${BLUE}🔗 Services:${NC}"
    kubectl get services -n $NAMESPACE
}

# Parse command line arguments
BUILD_ONLY=false
DEPLOY_ONLY=false
COMPONENTS_TO_DEPLOY=()

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_usage
            exit 0
            ;;
        --build-only)
            BUILD_ONLY=true
            shift
            ;;
        --deploy-only)
            DEPLOY_ONLY=true
            shift
            ;;
        --all)
            COMPONENTS_TO_DEPLOY=("main" "worker" "beat" "flower")
            shift
            ;;
        main|worker|beat|flower)
            COMPONENTS_TO_DEPLOY+=("$1")
            shift
            ;;
        *)
            echo -e "${RED}❌ Unknown option: $1${NC}"
            show_usage
            exit 1
            ;;
    esac
done

# If no components specified, show usage
if [ ${#COMPONENTS_TO_DEPLOY[@]} -eq 0 ]; then
    echo -e "${RED}❌ No components specified${NC}"
    show_usage
    exit 1
fi

# Main execution
echo -e "${BLUE}🚀 Starting Component-based AKS Deployment for AIQ Backend${NC}"

# Check prerequisites
check_prerequisites

# Login to Azure
azure_login

# Check AKS cluster
check_aks_cluster

# Check ACR
check_acr

# Create namespace if it doesn't exist
echo -e "${YELLOW}📁 Creating namespace...${NC}"
kubectl apply -f k8s/namespace.yaml

# Apply configurations
echo -e "${YELLOW}⚙️  Applying configurations...${NC}"
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secrets.yaml

# Deploy databases first
echo -e "${YELLOW}🐘 Deploying PostgreSQL...${NC}"
kubectl apply -f k8s/postgres-deployment.yaml

echo -e "${YELLOW}🔴 Deploying Redis...${NC}"
kubectl apply -f k8s/redis-deployment.yaml

# Wait for databases to be ready
echo -e "${YELLOW}⏳ Waiting for databases to be ready...${NC}"
kubectl wait --for=condition=available --timeout=300s deployment/aiq-postgres -n $NAMESPACE
kubectl wait --for=condition=available --timeout=300s deployment/aiq-redis -n $NAMESPACE

# Process each component
for component in "${COMPONENTS_TO_DEPLOY[@]}"; do
    echo -e "${BLUE}🔄 Processing $component component...${NC}"
    
    if [ "$DEPLOY_ONLY" = false ]; then
        build_component $component
    fi
    
    if [ "$BUILD_ONLY" = false ]; then
        deploy_component $component
        wait_for_deployment $component
    fi
done

# Deploy Azure Identity (if using managed identity)
if [ "$BUILD_ONLY" = false ]; then
    echo -e "${YELLOW}🔐 Deploying Azure Identity...${NC}"
    kubectl apply -f k8s/azure-identity.yaml
    
    # Deploy Ingress
    echo -e "${YELLOW}🌐 Deploying Ingress...${NC}"
    kubectl apply -f k8s/ingress.yaml
fi

# Show final status
if [ "$BUILD_ONLY" = false ]; then
    show_status
fi

echo -e "${GREEN}✅ Component deployment completed successfully!${NC}"
