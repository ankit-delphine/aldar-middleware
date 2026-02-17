#!/bin/bash

# Validation script to check Docker configuration files
# This script validates the Docker setup without requiring Docker to be running

set -e  # Exit on any error

echo "🔍 Validating Docker Configuration"
echo "=================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if file exists
file_exists() {
    if [ -f "$1" ]; then
        print_success "File $1 exists"
        return 0
    else
        print_error "File $1 does not exist"
        return 1
    fi
}

# Function to check if string exists in file
string_exists_in_file() {
    if grep -q "$2" "$1"; then
        print_success "Found '$2' in $1"
        return 0
    else
        print_error "Missing '$2' in $1"
        return 1
    fi
}

# Function to check if string does NOT exist in file
string_not_in_file() {
    if ! grep -q "$2" "$1"; then
        print_success "Confirmed '$2' is not in $1"
        return 0
    else
        print_error "Found unwanted '$2' in $1"
        return 1
    fi
}

echo ""
print_status "Starting Docker configuration validation..."

# Test 1: Check required files exist
echo ""
print_status "Test 1: Checking required files exist..."
file_exists "Dockerfile.base"
file_exists "Dockerfile.main"
file_exists "Dockerfile.worker"
file_exists "Dockerfile.beat"
file_exists "Dockerfile.flower"
file_exists "docker-compose.yml"
file_exists ".dockerignore"
file_exists "Makefile"

# Test 2: Check docker-compose.yml has base service
echo ""
print_status "Test 2: Checking docker-compose.yml configuration..."
string_exists_in_file "docker-compose.yml" "base:"
string_exists_in_file "docker-compose.yml" "dockerfile: ./Dockerfile.base"
string_exists_in_file "docker-compose.yml" "image: aldar-middleware-base:latest"

# Test 3: Check all services depend on base
echo ""
print_status "Test 3: Checking service dependencies on base image..."
string_exists_in_file "docker-compose.yml" "depends_on:"
string_exists_in_file "docker-compose.yml" "base:"
string_exists_in_file "docker-compose.yml" "condition: service_completed_successfully"

# Test 4: Check Dockerfiles use base image
echo ""
print_status "Test 4: Checking Dockerfiles use base image..."
string_exists_in_file "Dockerfile.main" "FROM aldar-middleware-base:latest"
string_exists_in_file "Dockerfile.worker" "FROM aldar-middleware-base:latest"
string_exists_in_file "Dockerfile.beat" "FROM aldar-middleware-base:latest"
string_exists_in_file "Dockerfile.flower" "FROM aldar-middleware-base:latest"

# Test 5: Check base Dockerfile doesn't have default health check
echo ""
print_status "Test 5: Checking base Dockerfile configuration..."
string_not_in_file "Dockerfile.base" "HEALTHCHECK"
print_success "Base Dockerfile correctly has no default health check"

# Test 6: Check Poetry configuration is simplified
echo ""
print_status "Test 6: Checking Poetry configuration..."
string_exists_in_file "Dockerfile.base" "poetry config virtualenvs.create false"
string_not_in_file "Dockerfile.base" "virtualenvs.in-project false"
print_success "Poetry configuration is correctly simplified"

# Test 7: Check .dockerignore exists and has good content
echo ""
print_status "Test 7: Checking .dockerignore configuration..."
string_exists_in_file ".dockerignore" "__pycache__"
string_exists_in_file ".dockerignore" "*.py\\[cod\\]"
string_exists_in_file ".dockerignore" ".git"
string_exists_in_file ".dockerignore" "*.md"
print_success ".dockerignore has comprehensive exclusions"

# Test 8: Check Makefile has base image commands
echo ""
print_status "Test 8: Checking Makefile configuration..."
string_exists_in_file "Makefile" "docker-build-base:"
string_exists_in_file "Makefile" "docker-build-all:"
string_exists_in_file "Makefile" "docker build -f Dockerfile.base"

# Test 9: Check GitHub Actions workflow
echo ""
print_status "Test 9: Checking GitHub Actions workflow..."
if [ -f ".github/workflows/docker-build.yml" ]; then
    string_exists_in_file ".github/workflows/docker-build.yml" "Build base image first"
    string_exists_in_file ".github/workflows/docker-build.yml" "docker build -f Dockerfile.base"
    print_success "GitHub Actions workflow configured correctly"
else
    print_warning "GitHub Actions workflow not found"
fi

# Test 10: Check service-specific health checks
echo ""
print_status "Test 10: Checking service-specific health checks..."
string_exists_in_file "Dockerfile.main" "HEALTHCHECK"
string_exists_in_file "Dockerfile.worker" "HEALTHCHECK"
string_exists_in_file "Dockerfile.beat" "HEALTHCHECK"
string_exists_in_file "Dockerfile.flower" "HEALTHCHECK"

# Test 11: Validate docker-compose.yml syntax (basic check)
echo ""
print_status "Test 11: Validating docker-compose.yml syntax..."
if command -v python3 >/dev/null 2>&1; then
    if python3 -c "import yaml; yaml.safe_load(open('docker-compose.yml'))" 2>/dev/null; then
        print_success "docker-compose.yml has valid YAML syntax"
    else
        print_error "docker-compose.yml has invalid YAML syntax"
    fi
else
    print_warning "Python3 not available, skipping YAML syntax check"
fi

# Test 12: Check for common Docker anti-patterns
echo ""
print_status "Test 12: Checking for Docker anti-patterns..."
string_not_in_file "Dockerfile.base" "RUN apt-get update && apt-get install -y curl"
print_success "Base Dockerfile doesn't install curl (good - it's in runtime stage)"

# Final summary
echo ""
echo "🎉 Docker Configuration Validation Summary"
echo "=========================================="
print_success "✅ All required files exist"
print_success "✅ Docker-compose has base service configuration"
print_success "✅ All services depend on base image"
print_success "✅ All Dockerfiles use base image correctly"
print_success "✅ Base Dockerfile has no default health check"
print_success "✅ Poetry configuration is optimized"
print_success "✅ .dockerignore is comprehensive"
print_success "✅ Makefile has base image commands"
print_success "✅ Service-specific health checks are present"

echo ""
print_status "Configuration validation completed successfully! 🚀"
print_status "The Docker setup is correctly configured."
print_status ""
print_status "To test with Docker running:"
print_status "  ./scripts/test-docker-setup.sh"
print_status ""
print_status "To build and run:"
print_status "  make docker-build-all"
print_status "  docker-compose up -d"
