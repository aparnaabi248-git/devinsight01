# ---------------------------------------------------------------------------
# DevInsight developer tasks. Windows-friendly: use `make` from any shell with
# make installed, or run the underlying commands directly.
# ---------------------------------------------------------------------------
.DEFAULT_GOAL := help
SHELL := /bin/bash

PY      := python
PIP     := $(PY) -m pip
COMPOSE := docker compose
BACKEND := backend
ML      := ml

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------- setup
.PHONY: install
install: ## Install Python + Node dependencies
	$(PIP) install -r $(BACKEND)/requirements.txt
	cd frontend && npm install

.PHONY: env
env: ## Create .env from the template
	@test -f .env || (cp .env.example .env && \
		echo "generated SECRET_KEY=$$(openssl rand -hex 32)" >> .env && \
		echo "Edit .env and set GITHUB_TOKEN for full API quota.")

# ------------------------------------------------------------------- data
.PHONY: acquire
acquire: ## Clone the real repositories and download the real issue corpus
	$(PY) $(ML)/data/acquire.py

.PHONY: train
train: ## Train all four models (writes ml/artifacts + ml/reports)
	cd $(ML) && $(PY) training/run_all.py

.PHONY: train-defect
train-defect: ## Train only the defect-risk model
	cd $(ML) && $(PY) training/train_defect_risk.py

.PHONY: train-classifier
train-classifier: ## Train only the issue classifier
	cd $(ML) && $(PY) training/train_issue_classifier.py

.PHONY: train-priority
train-priority: ## Train only the priority model
	cd $(ML) && $(PY) training/train_priority.py

.PHONY: train-effort
train-effort: ## Train only the effort regressor
	cd $(ML) && $(PY) training/train_effort.py

.PHONY: validate-artifacts
validate-artifacts: ## Assert every trained model is documented and evaluated
	$(PY) tests/test_artifacts.py

# ----------------------------------------------------------------- backend
.PHONY: migrate
migrate: ## Apply database migrations
	cd $(BACKEND) && alembic upgrade head

.PHONY: check-schema
check-schema: ## Compare the live schema with the SQLAlchemy metadata
	cd $(BACKEND) && $(PY) scripts/check_schema.py

.PHONY: api
api: ## Run the API with autoreload on :8000
	cd $(BACKEND) && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

.PHONY: api-prod
api-prod: ## Run the API without autoreload
	cd $(BACKEND) && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers

# ---------------------------------------------------------------- frontend
.PHONY: web
web: ## Run the Vite dev server on :5173
	cd frontend && npm run dev

.PHONY: build-web
build-web: ## Typecheck and build the frontend
	cd frontend && npm run build

# ----------------------------------------------------------------- quality
.PHONY: lint
lint: ## Lint Python and TypeScript
	ruff check $(BACKEND) $(ML) tests scripts
	cd frontend && npx eslint src --ext .ts,.tsx

.PHONY: format
format: ## Auto-format Python
	ruff format $(BACKEND) $(ML) tests scripts
	ruff check --fix $(BACKEND) $(ML) tests scripts

.PHONY: typecheck
typecheck: ## Typecheck Python and TypeScript
	mypy --ignore-missing-imports --no-strict-optional $(BACKEND)/app
	cd frontend && npx tsc --noEmit

.PHONY: test
test: test-backend test-web ## Run every test suite

.PHONY: test-backend
test-backend: ## Run backend + ML tests
	cd $(BACKEND) && pytest -q --cov=app --cov-report=term-missing

.PHONY: test-web
test-web: ## Run frontend component tests
	cd frontend && npx vitest run

# ----------------------------------------------------------------- docker
.PHONY: up
up: env ## Build and start the whole stack
	$(COMPOSE) up -d --build
	@echo "web    -> http://localhost:8080"
	@echo "api    -> http://localhost:8000/docs"
	@echo "mlflow -> http://localhost:5000"

.PHONY: up-airflow
up-airflow: env ## Start the stack with the Airflow scheduler
	$(COMPOSE) --profile airflow up -d --build

.PHONY: down
down: ## Stop the stack
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop the stack and delete volumes
	$(COMPOSE) down -v --remove-orphans

.PHONY: logs
logs: ## Tail API logs
	$(COMPOSE) logs -f api

.PHONY: shell
shell: ## Open a shell in the API container
	$(COMPOSE) exec api /bin/bash

.PHONY: psql
psql: ## Open psql against the dev database
	$(COMPOSE) exec db psql -U devinsight -d devinsight
