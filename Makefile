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
OAUTHAPP_BINARY_URL ?= https://github.com/openbao/openbao-plugin-secrets-oauthapp/releases/download/$(OAUTHAPP_VERSION)/openbao-plugin-secrets-oauthapp_$(OAUTHAPP_VERSION)_linux_amd64.zip
OAUTHAPP_SHA256 ?= # Set this to the actual SHA-256 of the binary
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
		--set ui.enabled=true \
		--wait
	@echo "$(GREEN)OpenBao deployed$(NC)"
	@$(MAKE) openbao-plugin-register

.PHONY: openbao-plugin-register
openbao-plugin-register: ## Register oauthapp plugin and configure secrets engine
	@echo "$(BLUE)Configuring oauthapp plugin...$(NC)"
	@# Wait for OpenBao to be ready
	@kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=openbao -n $(OPENBAO_NAMESPACE) --timeout=120s
	@# Port-forward to access OpenBao CLI
	@kubectl port-forward -n $(OPENBAO_NAMESPACE) svc/$(OPENBAO_RELEASE) 8200:8200 > /dev/null 2>&1 &
	@PF_PID=$$!; \
	sleep 5; \
	export VAULT_ADDR="http://127.0.0.1:8200"; \
	export VAULT_TOKEN="dev-only-token"; \
	\
	# Download oauthapp binary if not present in plugin directory
	echo "$(BLUE)Downloading oauthapp binary...$(NC)"; \
	kubectl exec -n $(OPENBAO_NAMESPACE) deployment/$(OPENBAO_RELEASE) -- sh -c ' \
		if [ ! -f /vault/plugins/oauthapp ]; then \
			cd /tmp && \
			curl -L -o oauthapp.zip "$(OAUTHAPP_BINARY_URL)" && \
			unzip -o oauthapp.zip && \
			mv openbao-plugin-secrets-oauthapp /vault/plugins/oauthapp && \
			chmod +x /vault/plugins/oauthapp && \
			rm -f oauthapp.zip; \
		fi'; \
	\
	# Calculate SHA-256 and register plugin
	echo "$(BLUE)Registering plugin in catalog...$(NC)"; \
	SHA256=$$(kubectl exec -n $(OPENBAO_NAMESPACE) deployment/$(OPENBAO_RELEASE) -- sha256sum /vault/plugins/oauthapp | awk '{print $$1}'); \
	vault plugin register -sha256="$$SHA256" secret oauthapp; \
	\
	# Enable secrets engine if not already enabled
	echo "$(BLUE)Enabling oauthapp secrets engine...$(NC)"; \
	vault secrets enable -path=oauth2 oauthapp 2>/dev/null || echo "Secrets engine already enabled"; \
	\
	# Create least-privilege policy for aggregator
	echo "$(BLUE)Creating aggregator policy...$(NC)"; \
	vault policy write aggregator - <<EOF
	path "oauth2/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
EOF
	\
	# Create child token for aggregator
	echo "$(BLUE)Creating aggregator token...$(NC)"; \
	AGGREGATOR_TOKEN=$$(vault token create -policy=aggregator -format=json -ttl=24h | jq -r '.auth.client_token'); \
	echo "$$AGGREGATOR_TOKEN" > /tmp/openbao-aggregator-token.txt; \
	echo "$(GREEN)Plugin configuration complete$(NC)"; \
	echo "$(YELLOW)Aggregator token saved to /tmp/openbao-aggregator-token.txt$(NC)"; \
	\
	kill $$PF_PID 2>/dev/null || true

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
down: ## Tear down aggregator and mock OAuth2 only (preserves OpenBao)
	@echo "$(BLUE)Tearing down aggregator deployment...$(NC)"
	@helm uninstall $(RELEASE_NAME) --namespace $(NAMESPACE) || true
	@helm uninstall $(MOCK_OAUTH_RELEASE) --namespace $(NAMESPACE) || true
	@kubectl delete secret $(RELEASE_NAME)-openbao --namespace $(NAMESPACE) --ignore-not-found=true || true
	@kubectl delete namespace $(NAMESPACE) --ignore-not-found=true || true
	@echo "$(GREEN)Aggregator teardown complete$(NC)"
	@echo "$(YELLOW)Note: OpenBao is preserved. Remove it with: make infra-down$(NC)"
	@echo "$(YELLOW)Note: minikube cluster is still running. Stop it with: minikube stop$(NC)"

.PHONY: infra-down
infra-down: ## Tear down OpenBao infrastructure (namespace, release, configuration)
	@echo "$(BLUE)Tearing down OpenBao infrastructure...$(NC)"
	@helm uninstall $(OPENBAO_RELEASE) --namespace $(OPENBAO_NAMESPACE) || true
	@kubectl delete namespace $(OPENBAO_NAMESPACE) --ignore-not-found=true || true
	@rm -f /tmp/openbao-aggregator-token.txt
	@echo "$(GREEN)OpenBao infrastructure teardown complete$(NC)"
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
