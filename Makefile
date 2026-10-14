# ============================
# Makefile
# MT5 Web Quantitative Trading Platform
# ============================

SHELL := /bin/bash
COMPOSE := docker compose -f docker/docker-compose.yml
COMPOSE_DEV := $(COMPOSE) -f docker/docker-compose.dev.yml
BACKEND_CONTAINER := mt5_backend
FRONTEND_CONTAINER := mt5_frontend

.PHONY: help dev build build-dev start stop restart down logs ps \
        test test-backend test-frontend lint lint-backend lint-frontend \
        migrate migrate-create migrate-downgrade shell shell-frontend \
        clean clean-docker clean-all \
        format format-backend format-frontend

# ──────────── Main targets ────────────

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

dev: ## Start development stack with hot-reload
	$(COMPOSE_DEV) up --build

build: ## Build all Docker images
	$(COMPOSE) build --no-cache

build-dev: ## Build development Docker images
	$(COMPOSE_DEV) build

start: ## Start all services in detached mode
	$(COMPOSE) up -d

stop: ## Stop all services
	$(COMPOSE) stop

restart: ## Restart all services
	$(COMPOSE) restart

down: ## Stop and remove containers, networks
	$(COMPOSE) down

logs: ## Tail logs from all services
	$(COMPOSE) logs -f

logs-backend: ## Tail backend logs
	$(COMPOSE) logs -f backend

logs-celery: ## Tail celery worker logs
	$(COMPOSE) logs -f celery_worker

ps: ## List running services
	$(COMPOSE) ps

# ──────────── Testing ────────────

test: test-backend ## Run all tests

test-backend: ## Run backend tests
	docker exec -it $(BACKEND_CONTAINER) pytest -x --cov=app --cov-report=term-missing

test-backend-watch: ## Run backend tests in watch mode
	docker exec -it $(BACKEND_CONTAINER) ptw -- --testmon

test-frontend: ## Run frontend tests
	docker exec -it $(FRONTEND_CONTAINER) npm test

test-e2e: ## Run end-to-end tests
	docker exec -it $(BACKEND_CONTAINER) pytest tests/e2e/ -x

# ──────────── Linting / Formatting ────────────

lint: lint-backend lint-frontend ## Lint all code

lint-backend: ## Lint backend with ruff
	docker exec -it $(BACKEND_CONTAINER) ruff check app/ tests/

lint-frontend: ## Lint frontend with eslint
	docker exec -it $(FRONTEND_CONTAINER) npx eslint src/ --ext .ts,.tsx

format: format-backend format-frontend ## Format all code

format-backend: ## Format backend with ruff
	docker exec -it $(BACKEND_CONTAINER) ruff format app/ tests/

format-frontend: ## Format frontend with prettier
	docker exec -it $(FRONTEND_CONTAINER) npx prettier --write "src/**/*.{ts,tsx,css,scss}"

typecheck: ## Run mypy type checking
	docker exec -it $(BACKEND_CONTAINER) mypy app/

typecheck-frontend: ## Run TypeScript type checking
	docker exec -it $(FRONTEND_CONTAINER) npx tsc --noEmit

# ──────────── Database / Migrations ────────────

migrate: ## Run pending alembic migrations
	docker exec -it $(BACKEND_CONTAINER) alembic upgrade head

migrate-create: ## Create a new migration (usage: make migrate-create msg="description")
	@if [ -z "$(msg)" ]; then \
		echo "ERROR: Provide a migration message: make migrate-create msg='add users table'"; \
		exit 1; \
	fi
	docker exec -it $(BACKEND_CONTAINER) alembic revision --autogenerate -m "$(msg)"

migrate-downgrade: ## Rollback last migration
	docker exec -it $(BACKEND_CONTAINER) alembic downgrade -1

migrate-history: ## Show migration history
	docker exec -it $(BACKEND_CONTAINER) alembic history

db-reset: ## Reset database (drop and recreate)
	$(COMPOSE) down -v postgres
	$(COMPOSE) up -d postgres
	@echo "Waiting for postgres to be ready..."
	@sleep 10
	$(COMPOSE) up -d

# ──────────── Shells ────────────

shell: ## Open bash shell in backend container
	docker exec -it $(BACKEND_CONTAINER) /bin/bash

shell-frontend: ## Open shell in frontend container
	docker exec -it $(FRONTEND_CONTAINER) /bin/sh

shell-postgres: ## Open psql in postgres container
	docker exec -it mt5_postgres psql -U mt5user -d mt5_quant

shell-redis: ## Open redis-cli
	docker exec -it mt5_redis redis-cli

# ──────────── Utilities ────────────

ipython: ## Open IPython shell with app context
	docker exec -it $(BACKEND_CONTAINER) python -c "import IPython; IPython.start_ipython()"

seed: ## Seed database with sample data
	docker exec -it $(BACKEND_CONTAINER) python -m app.scripts.seed

create-user: ## Create a superuser interactively
	docker exec -it $(BACKEND_CONTAINER) python -m app.scripts.create_user

# ──────────── Cleanup ────────────

clean: ## Remove containers, volumes, and temp files
	$(COMPOSE) down -v --remove-orphans
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "node_modules" -exec rm -rf {} + 2>/dev/null || true

clean-docker: ## Remove all Docker resources for this project
	$(COMPOSE) down -v --rmi all --remove-orphans

clean-all: clean clean-docker ## Full cleanup including Docker images

# ──────────── Dependencies ────────────

install-backend: ## Install backend Python dependencies
	docker exec -it $(BACKEND_CONTAINER) pip install -r requirements/dev.txt

install-frontend: ## Install frontend npm dependencies
	docker exec -it $(FRONTEND_CONTAINER) npm install

# ──────────── Documentation ────────────

docs-api: ## Build API documentation (OpenAPI spec)
	docker exec -it $(BACKEND_CONTAINER) python -c "from app.main import app; import json; json.dump(app.openapi(), open('/app/openapi.json', 'w'), indent=2)"

# ──────────── Shortcuts ────────────

.DEFAULT_GOAL := help