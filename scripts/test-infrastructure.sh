#!/bin/bash

# Infrastructure Testing Script for AIQ Backend
# This script tests the deployed infrastructure

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
NAMESPACE="aldar-middleware"
SERVICE_NAME="aldar-middleware-service"

echo -e "${BLUE}🧪 Starting Infrastructure Testing for AIQ Backend${NC}"

# Function to test endpoint
test_endpoint() {
    local url=$1
    local expected_status=$2
    local description=$3
    
    echo -e "${YELLOW}Testing: $description${NC}"
    
    response=$(curl -s -o /dev/null -w "%{http_code}" "$url" || echo "000")
    
    if [ "$response" = "$expected_status" ]; then
        echo -e "${GREEN}✅ $description - Status: $response${NC}"
        return 0
    else
        echo -e "${RED}❌ $description - Expected: $expected_status, Got: $response${NC}"
        return 1
    fi
}

# Get service information
echo -e "${YELLOW}📊 Getting service information...${NC}"

# Check if namespace exists
if ! kubectl get namespace $NAMESPACE >/dev/null 2>&1; then
    echo -e "${RED}❌ Namespace $NAMESPACE not found${NC}"
    exit 1
fi

# Get LoadBalancer IP
EXTERNAL_IP=$(kubectl get service $SERVICE_NAME -n $NAMESPACE -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")

if [ -z "$EXTERNAL_IP" ]; then
    echo -e "${YELLOW}⏳ LoadBalancer IP not available yet. Checking pod status...${NC}"
    
    # Check pod status
    echo -e "${BLUE}📋 Pod Status:${NC}"
    kubectl get pods -n $NAMESPACE
    
    # Check service status
    echo -e "${BLUE}🔗 Service Status:${NC}"
    kubectl get services -n $NAMESPACE
    
    echo -e "${YELLOW}⏳ Please wait for LoadBalancer IP to be assigned and run this script again${NC}"
    exit 0
fi

echo -e "${GREEN}🌐 LoadBalancer IP: $EXTERNAL_IP${NC}"

# Test endpoints
echo -e "${YELLOW}🔍 Testing endpoints...${NC}"

BASE_URL="http://$EXTERNAL_IP"
FAILED_TESTS=0

# Test health endpoint
if ! test_endpoint "$BASE_URL/api/v1/health" "200" "Health Check"; then
    FAILED_TESTS=$((FAILED_TESTS + 1))
fi

# Test API documentation
if ! test_endpoint "$BASE_URL/docs" "200" "API Documentation"; then
    FAILED_TESTS=$((FAILED_TESTS + 1))
fi

# Test root endpoint
if ! test_endpoint "$BASE_URL/" "200" "Root Endpoint"; then
    FAILED_TESTS=$((FAILED_TESTS + 1))
fi

# Test Azure Service Bus connection
echo -e "${YELLOW}🔗 Testing Azure Service Bus connection...${NC}"
SERVICE_BUS_TEST=$(kubectl exec -n $NAMESPACE deployment/aldar-middleware -- python -c "
import asyncio
from aldar_middleware.services.azure_service_bus import azure_service_bus
try:
    result = asyncio.run(azure_service_bus.health_check())
    print('SUCCESS:', result)
except Exception as e:
    print('ERROR:', str(e))
" 2>/dev/null || echo "ERROR: Failed to test Azure Service Bus")

if [[ $SERVICE_BUS_TEST == SUCCESS* ]]; then
    echo -e "${GREEN}✅ Azure Service Bus connection successful${NC}"
else
    echo -e "${RED}❌ Azure Service Bus connection failed: $SERVICE_BUS_TEST${NC}"
    FAILED_TESTS=$((FAILED_TESTS + 1))
fi

# Test Celery worker
echo -e "${YELLOW}⚡ Testing Celery worker...${NC}"
CELERY_TEST=$(kubectl exec -n $NAMESPACE deployment/aiq-celery-worker -- python -c "
from aldar_middleware.queue.celery_app import celery_app
try:
    result = celery_app.control.inspect().stats()
    print('SUCCESS: Celery worker is running')
except Exception as e:
    print('ERROR:', str(e))
" 2>/dev/null || echo "ERROR: Failed to test Celery worker")

if [[ $CELERY_TEST == SUCCESS* ]]; then
    echo -e "${GREEN}✅ Celery worker is running${NC}"
else
    echo -e "${RED}❌ Celery worker test failed: $CELERY_TEST${NC}"
    FAILED_TESTS=$((FAILED_TESTS + 1))
fi

# Test database connection
echo -e "${YELLOW}🐘 Testing database connection...${NC}"
DB_TEST=$(kubectl exec -n $NAMESPACE deployment/aldar-middleware -- python -c "
import asyncio
from aldar_middleware.database.base import engine
try:
    async def test_db():
        async with engine.begin() as conn:
            result = await conn.execute('SELECT 1')
            return result.fetchone()
    result = asyncio.run(test_db())
    print('SUCCESS: Database connection successful')
except Exception as e:
    print('ERROR:', str(e))
" 2>/dev/null || echo "ERROR: Failed to test database")

if [[ $DB_TEST == SUCCESS* ]]; then
    echo -e "${GREEN}✅ Database connection successful${NC}"
else
    echo -e "${RED}❌ Database connection failed: $DB_TEST${NC}"
    FAILED_TESTS=$((FAILED_TESTS + 1))
fi

# Test Redis connection
echo -e "${YELLOW}🔴 Testing Redis connection...${NC}"
REDIS_TEST=$(kubectl exec -n $NAMESPACE deployment/aldar-middleware -- python -c "
import redis
from aldar_middleware.settings import settings
try:
    r = redis.from_url(str(settings.redis_url_property))
    r.ping()
    print('SUCCESS: Redis connection successful')
except Exception as e:
    print('ERROR:', str(e))
" 2>/dev/null || echo "ERROR: Failed to test Redis")

if [[ $REDIS_TEST == SUCCESS* ]]; then
    echo -e "${GREEN}✅ Redis connection successful${NC}"
else
    echo -e "${RED}❌ Redis connection failed: $REDIS_TEST${NC}"
    FAILED_TESTS=$((FAILED_TESTS + 1))
fi

# Summary
echo -e "${BLUE}📊 Test Summary:${NC}"

if [ $FAILED_TESTS -eq 0 ]; then
    echo -e "${GREEN}🎉 All tests passed! Infrastructure is ready.${NC}"
    echo -e "${GREEN}🌐 AIQ Backend is available at: $BASE_URL${NC}"
    echo -e "${GREEN}📚 API Documentation: $BASE_URL/docs${NC}"
    echo -e "${GREEN}🏥 Health Check: $BASE_URL/api/v1/health${NC}"
else
    echo -e "${RED}❌ $FAILED_TESTS test(s) failed. Please check the logs.${NC}"
    echo -e "${YELLOW}📋 Debugging commands:${NC}"
    echo -e "kubectl get pods -n $NAMESPACE"
    echo -e "kubectl logs -n $NAMESPACE deployment/aldar-middleware"
    echo -e "kubectl logs -n $NAMESPACE deployment/aiq-celery-worker"
fi

# Show resource usage
echo -e "${BLUE}📈 Resource Usage:${NC}"
kubectl top pods -n $NAMESPACE 2>/dev/null || echo "Metrics not available"

echo -e "${BLUE}🔗 Service Endpoints:${NC}"
kubectl get services -n $NAMESPACE

exit $FAILED_TESTS
