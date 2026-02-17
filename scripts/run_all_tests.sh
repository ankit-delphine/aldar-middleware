#!/bin/bash

# Comprehensive Test Runner for AIQ Backend
# Tests all APIs across the entire application

echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "🧪 AIQ Backend - Comprehensive API Test Suite"
echo "════════════════════════════════════════════════════════════════════"
echo ""

# Check if server is running
if ! curl -s http://localhost:8000/api/v1/health > /dev/null 2>&1; then
    echo "❌ Error: Server is not running on http://localhost:8000"
    echo ""
    echo "Please start the server first:"
    echo "  cd /home/rishabhrivastava/aldar-middleware"
    echo "  ALDAR_ENVIRONMENT=testing poetry run uvicorn aldar_middleware.application:app --host 0.0.0.0 --port 8000"
    echo ""
    exit 1
fi

echo "✅ Server is running"
echo ""

# Set test environment
export ALDAR_ENVIRONMENT=testing
export ALDAR_DB_BASE=aiq_test

# Change to project directory
cd /home/rishabhrivastava/aldar-middleware

echo "════════════════════════════════════════════════════════════════════"
echo "📊 Test Execution Options:"
echo "════════════════════════════════════════════════════════════════════"
echo ""
echo "1. Basic API smoke tests (40 tests - quick validation)"
echo "2. COMPREHENSIVE API tests (184+ tests - ALL endpoints)"
echo "3. RBAC tests only"
echo "4. Individual API module tests"
echo "5. Quick smoke tests (health checks only)"
echo "6. Full test suite (all tests in tests/ folder)"
echo ""
read -p "Select option (1-6) [default: 1]: " option
option=${option:-1}

echo ""
echo "════════════════════════════════════════════════════════════════════"

case $option in
    1)
        echo "Running: Basic API Smoke Tests (40 tests)"
        echo "════════════════════════════════════════════════════════════════════"
        poetry run pytest tests/test_all_apis.py -v --tb=short --color=yes
        ;;
    2)
        echo "Running: COMPREHENSIVE API Tests (184+ endpoints - ALL)"
        echo "════════════════════════════════════════════════════════════════════"
        echo "⚠️  This will test every single endpoint in the application"
        echo "   Estimated time: 2-5 minutes"
        echo ""
        poetry run pytest tests/test_all_apis_comprehensive.py -v --tb=short --color=yes
        ;;
    3)
        echo "Running: RBAC Tests Only"
        echo "════════════════════════════════════════════════════════════════════"
        poetry run pytest tests/test_rbac_pytest.py -v --tb=short --color=yes
        ;;
    4)
        echo "Running: Individual API Module Tests"
        echo "════════════════════════════════════════════════════════════════════"
        echo ""
        echo "Available modules:"
        ls -1 tests/test_*.py | sed 's/tests\//  - /'
        echo ""
        read -p "Enter test file name (e.g., test_all_apis.py): " testfile
        poetry run pytest "tests/$testfile" -v --tb=short --color=yes
        ;;
    5)
        echo "Running: Quick Smoke Tests (Health Checks)"
        echo "════════════════════════════════════════════════════════════════════"
        poetry run pytest tests/test_all_apis.py::TestAllAPIs::test_health_check \
                         tests/test_all_apis.py::TestAllAPIs::test_rbac_health \
                         tests/test_all_apis.py::TestAllAPIs::test_admin_health \
                         -v --tb=short --color=yes
        ;;
    6)
        echo "Running: Full Test Suite (All Tests)"
        echo "════════════════════════════════════════════════════════════════════"
        poetry run pytest tests/ -v --tb=short --color=yes
        ;;
    *)
        echo "Invalid option. Exiting."
        exit 1
        ;;
esac

echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "✅ Test Execution Complete"
echo "════════════════════════════════════════════════════════════════════"
echo ""

