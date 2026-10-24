# Makefile for Integration Aggregator
# Requires: GNU Make, Docker, kubectl, Helm, minikube
# On Windows: Use WSL or install GNU Make via chocolatey/scoop

# Configuration (override with environment variables)
IMAGE_NAME ?= integration-aggregator
IMAGE_TAG ?= latest
CHART_PATH ?= charts/aggregator
RELEASE_NAME ?= aggregator
NAMESPACE ?= default
OPENBAO_RELEASE ?= openbao
OPENBAO_NAMESPACE ?= openbao
OPENBAO_CHART ?= openbao/openbao
OPENBAO_ADDR ?= http://openbao.openbao.svc.cluster.local:8200
OAUTHAPP_VERSION ?= v3.0.0
MOCK_OAUTH_RELEASE ?= mock-oauth
MOCK_OAUTH_CHART ?= oci://ghcr.io/navikt/mock-oauth2-server

# Colors for output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

.PHONY: help
help: ## Show this help message
	@echo "$(BLUE)Integration Aggregator Makefile$(NC)"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "$(GREEN)Development targets:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'

.PHONY: check-tools
check-tools: ## Check for required tools
	@echo "$(BLUE)Checking required tools...$(NC)"
	@command -v docker >/dev/null 2>&1 || { echo "$(RED)Error: docker not found$(NC)"; exit 1; }
	@command -v kubectl >/dev/null 2>&1 || { echo "$(RED)Error: kubectl not found$(NC)"; exit 1; }
	@command -v helm >/dev/null 2>&1 || { echo "$(RED)Error: helm not found$(NC)"; exit 1; }
	@command -v minikube >/dev/null 2>&1 || { echo "$(RED)Error: minikube not found$(NC)"; exit 1; }
	@echo "$(GREEN)All required tools found$(NC)"

.PHONY: test
test: ## Run pytest
	@echo "$(BLUE)Running tests...$(NC)"
	pytest tests/ -v

.PHONY: lint
lint: ## Run Ruff linter
	@echo "$(BLUE)Running Ruff linter...$(NC)"
	ruff check app tests

.PHONY: helm-lint
helm-lint: ## Run helm lint on the chart
	@echo "$(BLUE)Linting Helm chart...$(NC)"
	helm lint $(CHART_PATH)

.PHONY: helm-template
helm-template: ## Render Helm chart without deploying
	@echo "$(BLUE)Rendering Helm chart...$(NC)"
	helm template $(RELEASE_NAME) $(CHART_PATH)

.PHONY: build
build: ## Build Docker image
	@echo "$(BLUE)Building Docker image $(IMAGE_NAME):$(IMAGE_TAG)...$(NC)"
	docker build -t $(IMAGE_NAME):$(IMAGE_TAG) .

.PHONY: minikube-start
minikube-start: check-tools ## Start minikube cluster if not running
	@echo "$(BLUE)Checking minikube status...$(NC)"
	@minikube status >/dev/null 2>&1 || \
		(echo "$(YELLOW)Starting minikube...$(NC)" && minikube start)
	@echo "$(GREEN)Minikube is running$(NC)"

.PHONY: openbao-up
openbao-up: minikube-start ## Deploy OpenBao with oauthapp plugin
	@echo "$(BLUE)Deploying OpenBao...$(NC)"
	@kubectl create namespace $(OPENBAO_NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	@helm upgrade --install $(OPENBAO_RELEASE) $(OPENBAO_CHART) \
		--namespace $(OPENBAO_NAMESPACE) \
		--set injector.enabled=false \
		--set server.standalone.enabled=true \
		--set server.dev.enabled=true \
		--set server.dev.rootToken="dev-only-token" \
		--wait
	@echo "$(GREEN)OpenBao deployed$(NC)"
	@echo "$(BLUE)Configuring oauthapp plugin...$(NC)"
	@# Note: Plugin registration steps would go here
	@# This is a placeholder - actual implementation requires:
	@# - Download oauthapp binary
	@# - Register plugin in OpenBao catalog
	@# - Enable secrets engine
	@# - Create policy and token
	@echo "$(YELLOW)Plugin registration not yet implemented$(NC)"

.PHONY: secret-create
secret-create: ## Create Kubernetes Secret for OpenBao token
	@echo "$(BLUE)Creating OpenBao token Secret...$(NC)"
	@if [ -z "$$OPENBAO_TOKEN" ]; then \
		echo "$(RED)Error: OPENBAO_TOKEN environment variable not set$(NC)"; \
		exit 1; \
	fi
	@kubectl create secret generic $(RELEASE_NAME)-openbao \
		--namespace $(NAMESPACE) \
		--from-literal=OPENBAO_TOKEN=$$OPENBAO_TOKEN \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "$(GREEN)Secret created$(NC)"

.PHONY: image-load
image-load: build ## Load Docker image into minikube
	@echo "$(BLUE)Loading image into minikube...$(NC)"
	minikube image load $(IMAGE_NAME):$(IMAGE_TAG)
	@echo "$(GREEN)Image loaded$(NC)"

.PHONY: deploy
deploy: image-load secret-create ## Deploy aggregator using Helm chart
	@echo "$(BLUE)Deploying aggregator...$(NC)"
	@kubectl create namespace $(NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	@helm upgrade --install $(RELEASE_NAME) $(CHART_PATH) \
		--namespace $(NAMESPACE) \
		--set image.repository=$(IMAGE_NAME) \
		--set image.tag=$(IMAGE_TAG) \
		--set openbao.address=$(OPENBAO_ADDR) \
		--set openbao.existingSecret=$(RELEASE_NAME)-openbao \
		--wait
	@echo "$(GREEN)Aggregator deployed$(NC)"

.PHONY: mock-oauth-up
mock-oauth-up: minikube-start ## Deploy mock OAuth2 server for testing
	@echo "$(BLUE)Deploying mock OAuth2 server...$(NC)"
	@helm upgrade --install $(MOCK_OAUTH_RELEASE) $(MOCK_OAUTH_CHART) \
		--namespace $(NAMESPACE) \
		--set httpPort=8080 \
		--wait
	@echo "$(GREEN)Mock OAuth2 server deployed$(NC)"

.PHONY: up
up: check-tools openbao-up deploy mock-oauth-up ## Full deployment: minikube, OpenBao, aggregator, mock OAuth
	@echo "$(GREEN)========================================$(NC)"
	@echo "$(GREEN)Deployment complete!$(NC)"
	@echo "$(GREEN)========================================$(NC)"
	@echo "$(BLUE)Aggregator service:$(NC)"
	@kubectl get svc $(RELEASE_NAME) -n $(NAMESPACE)
	@echo "$(BLUE)To access locally, run:$(NC)"
	@echo "kubectl port-forward -n $(NAMESPACE) svc/$(RELEASE_NAME) 8000:80"

.PHONY: mock-oauth-down
mock-oauth-down: ## Remove mock OAuth2 server
	@echo "$(BLUE)Removing mock OAuth2 server...$(NC)"
	@helm uninstall $(MOCK_OAUTH_RELEASE) --namespace $(NAMESPACE) || true
	@echo "$(GREEN)Mock OAuth2 server removed$(NC)"

.PHONY: down
down: ## Tear down all deployed resources
	@echo "$(BLUE)Tearing down deployment...$(NC)"
	@helm uninstall $(RELEASE_NAME) --namespace $(NAMESPACE) || true
	@helm uninstall $(MOCK_OAUTH_RELEASE) --namespace $(NAMESPACE) || true
	@helm uninstall $(OPENBAO_RELEASE) --namespace $(OPENBAO_NAMESPACE) || true
	@kubectl delete namespace $(NAMESPACE) --ignore-not-found=true || true
	@kubectl delete namespace $(OPENBAO_NAMESPACE) --ignore-not-found=true || true
	@echo "$(GREEN)Teardown complete$(NC)"
	@echo "$(YELLOW)Note: minikube cluster is still running. Stop it with: minikube stop$(NC)"

.PHONY: smoke
smoke: ## Run smoke test (requires deployed environment)
	@echo "$(BLUE)Running smoke test...$(NC)"
	@if [ ! -f scripts/smoke.sh ]; then \
		echo "$(RED)Error: scripts/smoke.sh not found$(NC)"; \
		exit 1; \
	fi
	@bash scripts/smoke.sh

.PHONY: clean
clean: ## Clean build artifacts
	@echo "$(BLUE)Cleaning build artifacts...$(NC)"
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "$(GREEN)Clean complete$(NC)"
