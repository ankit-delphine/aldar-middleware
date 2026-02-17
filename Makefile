# AIQ Backend Makefile
# Easy commands for development and deployment

.PHONY: help install dev test clean lint format migrate run build docker-up docker-down

# Default target
help: ## Show this help message
	@echo "AIQ Backend - Available Commands:"
	@echo "=================================="
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# Development Setup
install: ## Install dependencies
	@echo "📦 Installing dependencies..."
	poetry install
	@echo "✅ Dependencies installed successfully!"

dev: ## Install development dependencies
	@echo "🔧 Installing development dependencies..."
	poetry install --with dev
	@echo "✅ Development dependencies installed!"

# Database Operations
migrate: ## Run database migrations
	@echo "🗄️  Running database migrations..."
	poetry run alembic upgrade head
	@echo "✅ Migrations completed!"

migrate-create: ## Create a new migration (usage: make migrate-create MESSAGE="your message")
	@echo "📝 Creating new migration: $(MESSAGE)"
	poetry run alembic revision --autogenerate -m "$(MESSAGE)"
	@echo "✅ Migration created!"

migrate-downgrade: ## Downgrade database by one migration
	@echo "⬇️  Downgrading database..."
	poetry run alembic downgrade -1
	@echo "✅ Database downgraded!"

# Application
run: ## Start the development server
	@echo "🚀 Starting AIQ Backend in development mode..."
	@BASE_URL=$$(poetry run python -c 'import os; from urllib.parse import urlparse; \
		aiq_url = os.getenv("AIQ_URL"); \
		if aiq_url: \
			parsed = urlparse(aiq_url); \
			protocol = parsed.scheme or "http"; \
			host = parsed.hostname or (parsed.netloc.split(":")[0] if ":" in parsed.netloc else parsed.netloc) or "0.0.0.0"; \
			port = parsed.port or (443 if protocol == "https" else 80 if protocol == "http" else 8000); \
			print(f"{protocol}://{host}" if ((protocol == "http" and port == 80) or (protocol == "https" and port == 443)) else f"{protocol}://{host}:{port}"); \
		else: \
			try: \
				from aldar_middleware.settings import settings; \
				host = os.getenv("AIQ_HOST", settings.host); \
				port = int(os.getenv("PORT", settings.port)); \
			except: \
				host = os.getenv("AIQ_HOST", "0.0.0.0"); \
				port = int(os.getenv("PORT", 8000)); \
			print(f"http://{host}:{port}" if port != 80 else f"http://{host}"); \
		' 2>/dev/null || echo "http://0.0.0.0:8000"); \
	echo "📍 Server will be available at: $$BASE_URL"; \
	echo "📚 API Documentation: $$BASE_URL/docs"; \
	echo "🔍 Health Check: $$BASE_URL/api/v1/health"; \
	echo "Press Ctrl+C to stop the server"; \
	echo "--------------------------------------------------"
	poetry run python -m aldar_middleware

run-worker: ## Start Celery worker (uses solo pool for async task compatibility)
	@echo "👷 Starting Celery Worker..."
	@echo "⚠️  Using solo pool (single process) for better async task compatibility"
	@echo "This will process background tasks"
	@echo "Press Ctrl+C to stop the worker"
	@echo "--------------------------------------------------"
	poetry run celery -A aldar_middleware.queue.celery_app worker --pool=solo --loglevel=info

run-worker-prefork: ## Start Celery worker with prefork pool (faster but may have issues with async tasks)
	@echo "👷 Starting Celery Worker (Prefork Pool)..."
	@echo "⚠️  Using prefork pool - faster but may have SIGSEGV issues with async tasks on macOS"
	@echo "This will process background tasks"
	@echo "Press Ctrl+C to stop the worker"
	@echo "--------------------------------------------------"
	poetry run celery -A aldar_middleware.queue.celery_app worker --loglevel=info

run-worker-solo: ## Start Celery worker in solo pool (safer for async tasks, single process)
	@echo "👷 Starting Celery Worker (Solo Pool)..."
	@echo "⚠️  Using solo pool - single process, no forking (safer for async tasks)"
	@echo "This will process background tasks"
	@echo "Press Ctrl+C to stop the worker"
	@echo "--------------------------------------------------"
	poetry run celery -A aldar_middleware.queue.celery_app worker --pool=solo --loglevel=info


run-beat: ## Start Celery Beat scheduler (for periodic tasks)
	@echo "⏰ Starting Celery Beat Scheduler..."
	@echo "This will schedule periodic tasks (e.g., agent health checks every 30 minutes)"
	@echo "Press Ctrl+C to stop the scheduler"
	@echo "--------------------------------------------------"
	poetry run celery -A aldar_middleware.queue.celery_app beat --loglevel=info

run-all: ## Start all services (main + worker + beat) - requires 3 terminals
	@echo "🚀 Starting all services..."
	@echo "⚠️  This requires 3 separate terminals:"
	@echo "   Terminal 1: make run (main application)"
	@echo "   Terminal 2: make run-worker (Celery worker)"
	@echo "   Terminal 3: make run-beat (Celery beat scheduler)"
	@echo ""
	@echo "Or use Docker: make docker-dev"

run-prod: ## Start the production server
	@echo "🚀 Starting AIQ Backend in production mode..."
	poetry run gunicorn aldar_middleware:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000

# Testing
test: ## Run all tests
	@echo "🧪 Running tests..."
	poetry run pytest
	@echo "✅ Tests completed!"

test-cov: ## Run tests with coverage
	@echo "🧪 Running tests with coverage..."
	poetry run pytest --cov=aldar_middleware --cov-report=html --cov-report=term
	@echo "✅ Tests with coverage completed!"

test-watch: ## Run tests in watch mode
	@echo "👀 Running tests in watch mode..."
	poetry run pytest-watch

# Code Quality
lint: ## Run linting
	@echo "🔍 Running linting..."
	poetry run ruff check aldar_middleware/
	@echo "✅ Linting completed!"

format: ## Format code
	@echo "✨ Formatting code..."
	poetry run ruff format aldar_middleware/
	@echo "✅ Code formatted!"

format-check: ## Check code formatting
	@echo "🔍 Checking code formatting..."
	poetry run ruff format --check aldar_middleware/
	@echo "✅ Format check completed!"

# Database Management
db-reset: ## Reset database (WARNING: This will delete all data!)
	@echo "⚠️  WARNING: This will delete all data!"
	@read -p "Are you sure? (y/N): " confirm && [ "$$confirm" = "y" ]
	@echo "🗑️  Resetting database..."
	poetry run alembic downgrade base
	poetry run alembic upgrade head
	@echo "✅ Database reset completed!"

db-status: ## Show database migration status
	@echo "📊 Database migration status:"
	poetry run alembic current
	poetry run alembic history

# Environment
env-check: ## Check environment variables
	@echo "🔍 Checking environment variables..."
	@echo "Database URL: $$(poetry run python -c 'from aldar_middleware.settings import settings; print(settings.db_url_property)')"
	@echo "Redis URL: $$(poetry run python -c 'from aldar_middleware.settings import settings; print(settings.redis_url_property)')"
	@echo "Environment: $$(poetry run python -c 'from aldar_middleware.settings import settings; print(settings.environment)')"

# Docker
docker-up: ## Start Docker services
	@echo "🐳 Starting Docker services..."
	docker compose up -d
	@echo "✅ Docker services started!"

docker-down: ## Stop Docker services
	@echo "🐳 Stopping Docker services..."
	docker compose down
	@echo "✅ Docker services stopped!"

docker-logs: ## Show Docker logs
	@echo "📋 Showing Docker logs..."
	docker compose logs -f

docker-build: ## Build Docker images
	@echo "🔨 Building Docker images..."
	docker compose build
	@echo "✅ Docker images built!"

docker-restart: ## Restart Docker services
	@echo "🔄 Restarting Docker services..."
	docker compose restart
	@echo "✅ Docker services restarted!"

# Component-based Docker Commands
docker-dev: ## Start development environment (main + worker + db + redis)
	@echo "🚀 Starting development environment..."
	docker compose up -d api celery-worker db redis
	@echo "✅ Development environment started!"
	@echo "📍 Main Application: http://localhost:8000"
	@echo "📚 API Documentation: http://localhost:8000/docs"

docker-full: ## Start full environment (all services)
	@echo "🚀 Starting full environment..."
	docker compose up -d
	@echo "✅ Full environment started!"
	@echo "📍 Main Application: http://localhost:8000"
	@echo "🌸 Celery Flower: http://localhost:5555"
	@echo "📊 Prometheus: http://localhost:9090"
	@echo "📈 Grafana: Using Azure Grafana (external)"

docker-monitoring: ## Start monitoring services (flower + prometheus)
	@echo "📊 Starting monitoring services..."
	docker compose up -d celery-flower prometheus
	@echo "✅ Monitoring services started!"
	@echo "🌸 Celery Flower: http://localhost:5555"
	@echo "📊 Prometheus: http://localhost:9090"
	@echo "📈 Grafana: Using Azure Grafana (external)"

# Component-specific build commands
docker-build-main: ## Build main application image
	@echo "🔨 Building main application image..."
	docker compose build api
	@echo "✅ Main application image built!"

docker-build-worker: ## Build worker image
	@echo "🔨 Building worker image..."
	docker compose build celery-worker
	@echo "✅ Worker image built!"

docker-build-beat: ## Build beat image
	@echo "🔨 Building beat image..."
	docker compose build celery-beat
	@echo "✅ Beat image built!"

docker-build-flower: ## Build flower image
	@echo "🔨 Building flower image..."
	docker compose build celery-flower
	@echo "✅ Flower image built!"

docker-build-all: ## Build all component images
	@echo "🔨 Building all component images..."
	docker compose build api celery-worker celery-beat celery-flower
	@echo "✅ All component images built!"

# Component-specific logs
docker-logs-main: ## Show main application logs
	@echo "📋 Showing main application logs..."
	docker compose logs -f api

docker-logs-worker: ## Show worker logs
	@echo "📋 Showing worker logs..."
	docker compose logs -f celery-worker

docker-logs-beat: ## Show beat logs
	@echo "📋 Showing beat logs..."
	docker compose logs -f celery-beat

docker-logs-flower: ## Show flower logs
	@echo "📋 Showing flower logs..."
	docker compose logs -f celery-flower

docker-logs-db: ## Show database logs
	@echo "📋 Showing database logs..."
	docker compose logs -f db

docker-logs-redis: ## Show Redis logs
	@echo "📋 Showing Redis logs..."
	docker compose logs -f redis

# Environment Setup
env-setup: ## Setup environment from example
	@echo "⚙️  Setting up environment..."
	@if [ ! -f .env ]; then \
		cp env.example .env; \
		echo "✅ Created .env file from env.example"; \
		echo "📝 Please update .env file with your values"; \
	else \
		echo "⚠️  .env file already exists"; \
	fi

# Cleanup
clean: ## Clean up temporary files
	@echo "🧹 Cleaning up temporary files..."
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf .pytest_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	@echo "✅ Cleanup completed!"

# Development Workflow
setup: install migrate ## Complete development setup
	@echo "🎉 Development setup completed!"
	@echo "Run 'make run' to start the server"

# Query Architecture Testing
test-query: ## Test the query handling architecture
	@echo "🧪 Testing query handling architecture..."
	poetry run python scripts/test_query_architecture.py
	@echo "✅ Query architecture test completed!"

# Quick Commands
start: run ## Alias for run
stop: ## Stop the server (Ctrl+C)
	@echo "🛑 Use Ctrl+C to stop the server"

# Production Deployment
deploy-check: ## Check if ready for deployment
	@echo "🔍 Checking deployment readiness..."
	@echo "✅ Environment variables:"
	@make env-check
	@echo "✅ Database migrations:"
	@make db-status
	@echo "✅ Code quality:"
	@make lint
	@echo "✅ Tests:"
	@make test
	@echo "🎉 Ready for deployment!"

# Monitoring
health: ## Check application health
	@echo "🏥 Checking application health..."
	@curl -s http://localhost:8000/api/v1/health | python -m json.tool || echo "❌ Application not running"

logs: ## Show application logs
	@echo "📋 Showing application logs..."
	@tail -f logs/app.log 2>/dev/null || echo "No log file found"

# Documentation
docs: ## Generate API documentation
	@echo "📚 API Documentation available at: http://localhost:8000/docs"
	@echo "📖 ReDoc available at: http://localhost:8000/redoc"

# Security
security-check: ## Run security checks
	@echo "🔒 Running security checks..."
	poetry run safety check
	@echo "✅ Security check completed!"

# Performance
benchmark: ## Run performance benchmarks
	@echo "⚡ Running performance benchmarks..."
	poetry run python -m pytest tests/benchmark/ -v
	@echo "✅ Benchmark completed!"

# Backup
backup-db: ## Backup database
	@echo "💾 Creating database backup..."
	@mkdir -p backups
	@timestamp=$$(date +%Y%m%d_%H%M%S) && \
	poetry run python -c "from aldar_middleware.settings import settings; print(settings.db_url_property)" | \
	cut -d'@' -f2 | cut -d'/' -f1 | \
	xargs -I {} pg_dump -h {} -U $$(poetry run python -c "from aldar_middleware.settings import settings; print(settings.db_user)") \
	-d $$(poetry run python -c "from aldar_middleware.settings import settings; print(settings.db_base)") > backups/backup_$$timestamp.sql
	@echo "✅ Database backup created!"

# Show all available commands
list: help ## List all available commands

# Azure Service Bus Commands
azure-service-bus-test: ## Test Azure Service Bus connection
	@echo "🔗 Testing Azure Service Bus connection..."
	poetry run python -c "import asyncio; from aldar_middleware.orchestration.azure_service_bus import azure_service_bus; result = asyncio.run(azure_service_bus.health_check()); print(f'Result: {result}')"

azure-service-bus-send: ## Send test message to Azure Service Bus (usage: make azure-service-bus-send MESSAGE="test message")
	@echo "📤 Sending test message to Azure Service Bus..."
	poetry run python -c "import asyncio; from aldar_middleware.orchestration.azure_service_bus import azure_service_bus; message = {'message': '$(MESSAGE)', 'timestamp': '$(shell date -u +%Y-%m-%dT%H:%M:%SZ)'}; result = asyncio.run(azure_service_bus.send_message(message, 'test')); print(f'Message sent: {result}')"

azure-service-bus-monitor: ## Monitor Azure Service Bus queue
	@echo "👀 Monitoring Azure Service Bus queue..."
	@echo "Use Azure Portal to monitor your Service Bus queue"
	@echo "Queue Name: aiq-queue"
	@echo "Check for messages and processing status"

# AKS Deployment Commands
aks-deploy: ## Deploy to AKS (Azure Kubernetes Service)
	@echo "🚀 Deploying to AKS..."
	@echo "This will create a complete AKS infrastructure"
	@echo "Make sure you have Azure CLI and kubectl installed"
	@echo "Running deployment script..."
	./scripts/deploy-aks.sh

aks-test: ## Test AKS deployment
	@echo "🧪 Testing AKS deployment..."
	@echo "This will test all deployed components"
	./scripts/test-infrastructure.sh

aks-cleanup: ## Clean up AKS resources
	@echo "🧹 Cleaning up AKS resources..."
	@echo "⚠️  This will delete all AKS resources!"
	@echo "Are you sure? This action cannot be undone."
	@read -p "Type 'yes' to continue: " confirm && [ "$$confirm" = "yes" ] || exit 1
	./scripts/cleanup-aks.sh

aks-status: ## Check AKS deployment status
	@echo "📊 Checking AKS deployment status..."
	@kubectl get pods -n aldar-middleware 2>/dev/null || echo "Namespace not found. Run 'make aks-deploy' first"
	@kubectl get services -n aldar-middleware 2>/dev/null || echo "Services not found"
	@kubectl get ingress -n aldar-middleware 2>/dev/null || echo "Ingress not found"

aks-logs: ## Show AKS deployment logs
	@echo "📋 Showing AKS deployment logs..."
	@kubectl logs -n aldar-middleware deployment/aldar-middleware --tail=50
	@echo "--- Celery Worker Logs ---"
	@kubectl logs -n aldar-middleware deployment/aiq-celery-worker --tail=50

aks-scale: ## Scale AKS deployments (usage: make aks-scale BACKEND=3 WORKERS=4)
	@echo "📈 Scaling AKS deployments..."
	@kubectl scale deployment aldar-middleware --replicas=$(BACKEND) -n aldar-middleware
	@kubectl scale deployment aiq-celery-worker --replicas=$(WORKERS) -n aldar-middleware
	@echo "✅ Scaled to $(BACKEND) backend pods and $(WORKERS) worker pods"

# Azure Permissions Commands
azure-permissions-check: ## Check Azure permissions for Container Apps (default: adq, usage: make azure-permissions-check RG=adq)
	@echo "🔍 Checking Azure permissions..."
	@if [ -z "$(RG)" ]; then \
		./scripts/check-azure-permissions.sh adq; \
	else \
		./scripts/check-azure-permissions.sh $(RG); \
	fi

azure-grant-contributor: ## Grant Contributor role to a user (default: adq, usage: make azure-grant-contributor RG=adq USER=user@example.com)
	@echo "🔐 Granting Contributor role..."
	@if [ -z "$(RG)" ]; then \
		./scripts/grant-contributor-role.sh adq $(USER); \
	else \
		./scripts/grant-contributor-role.sh $(RG) $(USER); \
	fi
