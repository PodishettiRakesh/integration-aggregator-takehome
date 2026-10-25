# Usage Documentation

## Prerequisites

### Local Development

- **Python 3.11+**: Required for running the application
- **Docker**: For building and running containers
- **kubectl**: Kubernetes CLI for cluster interaction
- **Helm 3.x**: For deploying OpenBao and the application
- **Minikube**: Local Kubernetes cluster (or another k8s cluster)
- **Make**: For running deployment targets (GNU Make on Windows via WSL, Chocolatey, or Scoop)

### OAuth Providers

- **GitHub OAuth App**: Create at https://github.com/settings/developers (free)
- **Google OAuth Client**: Create at Google Cloud Console (free)
- **Or**: Use the mock OAuth2 server for testing (navikt/mock-oauth2-server)

## Local Setup and Deployment

### 1. Clone and Install Dependencies

```bash
# Clone the repository
git clone <your-fork-url>
cd integration-aggregator-takehome

# Install Python dependencies
pip install -e .[dev]
```

### 2. Start Minikube

```bash
minikube start
```

### 3. Deploy OpenBao with oauthapp Plugin

If you have an existing OpenBao installation, skip to step 4.

```bash
# Deploy OpenBao with the oauthapp plugin
make openbao-install-fresh
```

This will:
- Install OpenBao via Helm
- Download and register the oauthapp v3.4.1 plugin
- Enable the oauth2/ secrets engine
- Create the aggregator policy
- Create a child token for the service

**Note**: The child token is saved to `/tmp/openbao-aggregator-token.txt`. Do not commit this file.

### 4. Deploy the Application

```bash
# Copy the OpenBao token Secret to the aggregator namespace
make secret-copy
# Follow the displayed PowerShell or Bash commands

# Full deployment (builds image, deploys app and mock OAuth)
make up
```

This will:
- Verify Minikube is running
- Verify OpenBao installation
- Create the aggregator namespace
- Build and load the Docker image
- Deploy the mock OAuth server
- Deploy the aggregator service

### 5. Access the Application

```bash
# Port-forward to the service
kubectl port-forward -n aggregator svc/aggregator 8000:80
```

The application will be available at `http://localhost:8000`.

## Required Environment Variables

The application uses environment variables for configuration. Create a `.env` file (never commit it) or use Kubernetes Secrets.

### Local Development (.env)

```bash
# OpenBao configuration
OPENBAO_ADDR=http://127.0.0.1:8200
OPENBAO_TOKEN=<your-openbao-token>
OPENBAO_MOUNT=oauth2

# OAuth callback URL
REDIRECT_URL=http://localhost:8000/callback

# Application settings
LOG_LEVEL=INFO
HTTP_TIMEOUT_SECONDS=5
OAUTH_STATE_TTL_SECONDS=600
REQUEST_TTL_SECONDS=3600
```

### Kubernetes (Helm values)

The Helm chart uses `values.yaml` for configuration. Sensitive values should be supplied via Kubernetes Secrets:

```yaml
openbao:
  address: http://openbao.openbao.svc.cluster.local:8200
  existingSecret: aggregator-openbao  # Secret containing OPENBAO_TOKEN key
```

**Important**: Never commit actual token values to Git. Use `.env.example` as a template only.

## API Usage

### Register a Provider

Register an OAuth provider with its client ID and secret. The client secret is stored in OpenBao only.

```bash
curl -X POST http://localhost:8000/providers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "github",
    "provider": "github",
    "client_id": "<your-github-client-id>",
    "client_secret": "<your-github-client-secret>",
    "scopes": ["read:user"]
  }'
```

Response (201 Created):
```json
{
  "name": "github",
  "provider": "github",
  "client_id": "<your-github-client-id>",
  "scopes": ["read:user"]
}
```

**Note**: The response does not include the client_secret.

### Initiate OAuth Authorization

Start a connection for a user. Returns the authorization URL and a state value.

```bash
curl -X POST http://localhost:8000/providers/github/users/alice/connect
```

Response (200 OK):
```json
{
  "authorization_url": "https://github.com/login/oauth/authorize?client_id=...&state=...&redirect_uri=...",
  "state": "abc123..."
}
```

### Complete OAuth Flow (Browser)

1. Open the `authorization_url` in a browser
2. Log in to the OAuth provider and authorize the application
3. The provider redirects to the callback URL with `code` and `state`
4. The service handles the callback automatically

### Request Token Retrieval (Async)

Request the current access token for a user. Returns immediately with a request ID.

```bash
curl -i http://localhost:8000/github/alice
```

Response (202 Accepted):
```
HTTP/1.1 202 Accepted
Location: /requests/req-abc123...

{
  "request_id": "req-abc123..."
}
```

### Poll Request Status

Poll the request status endpoint until the request is complete.

```bash
curl http://localhost:8000/requests/req-abc123...
```

Response (pending):
```json
{
  "request_id": "req-abc123...",
  "status": "pending"
}
```

Response (succeeded):
```json
{
  "request_id": "req-abc123...",
  "status": "succeeded",
  "access_token": "gho_...",
  "token_type": "Bearer",
  "expire_time": "2024-01-01T00:00:00Z"
}
```

Response (failed):
```json
{
  "request_id": "req-abc123...",
  "status": "failed",
  "error": "Credentials not found in OpenBao"
}
```

## Running Tests

### Unit Tests

```bash
# Run all tests
pytest tests/ -v

# Run only unit tests
pytest tests/unit/ -v

# Run with coverage
pytest tests/ --cov=app --cov-report=html
```

### Integration Tests

```bash
# Run integration tests (uses Fake OpenBao)
pytest tests/integration/ -v
```

### Linting

```bash
# Run Ruff linter
ruff check app tests

# Auto-fix issues
ruff check --fix app tests
```

## Docker

### Build Image

```bash
docker build -t integration-aggregator:latest .
```

### Run Container

```bash
docker run -p 8000:8000 \
  -e OPENBAO_ADDR=http://host.docker.internal:8200 \
  -e OPENBAO_TOKEN=<token> \
  integration-aggregator:latest
```

## Helm

### Lint Chart

```bash
make helm-lint
# or
helm lint charts/aggregator
```

### Render Chart (Dry Run)

```bash
make helm-template
# or
helm template aggregator charts/aggregator
```

### Install/Upgrade

```bash
helm upgrade --install aggregator charts/aggregator \
  --namespace aggregator \
  --set image.repository=integration-aggregator \
  --set image.tag=latest \
  --set openbao.address=http://openbao.openbao.svc.cluster.local:8200
```

## Smoke Test

The smoke test verifies the end-to-end OAuth flow with the mock OAuth server.

```bash
# Ensure the application and mock OAuth are deployed
make up

# Run the smoke test
make smoke
```

The smoke test:
- Checks health endpoints
- Registers a mock OIDC provider
- Tests OAuth connect flow
- Tests callback with authorization code
- Tests async token request
- Polls for request status

**Note**: The smoke test requires a Bash-compatible environment. It does not run natively in PowerShell.

## Cleanup

### Remove Application Only

```bash
make down
```

This removes the aggregator and mock OAuth server but preserves OpenBao.

### Remove Everything

```bash
# Delete the aggregator namespace
make namespace-delete

# Remove OpenBao (DANGEROUS - only if you want to delete it)
make infra-down
```

## Troubleshooting

### Health Check Fails

If `/readyz` returns 503:
- Check OpenBao is running: `kubectl get pod -n openbao`
- Check the OpenBao token Secret exists: `kubectl get secret aggregator-openbao -n aggregator`
- Check application logs: `kubectl logs -n aggregator deployment/aggregator`

### OAuth State Invalid

If callback fails with "Invalid state":
- The state may have expired (default TTL 10 minutes)
- The state may have been replayed (consume-once protection)
- Check the `OAUTH_STATE_TTL_SECONDS` configuration

### Token Request Fails

If `/requests/{id}` returns "failed":
- Check that the OAuth callback completed successfully
- Verify credentials are stored in OpenBao: `kubectl exec -n openbao openbao-0 -- vault list oauth2/creds`
- Check OpenBao token has the correct policy

### Port-Forward Issues

If port-forward fails:
- Check the service exists: `kubectl get svc -n aggregator`
- Check the pod is ready: `kubectl get pod -n aggregator`
- Try a different port if 8000 is in use

### Minikube Issues

If Minikube fails to start:
- Check Docker is running: `docker ps`
- Try deleting and recreating Minikube: `minikube delete && minikube start`
- Check available resources: `minikube status`

### Secret Verification Fails

If `make secret-verify` fails:
- Check if the Secret exists in the default namespace: `kubectl get secret aggregator-openbao -n default`
- Copy the Secret using `make secret-copy` instructions
- Verify the token hasn't expired (24-hour TTL)

## Additional Documentation

- **DESIGN.md**: Architecture, data placement, and security decisions
- **DEPLOYMENT.md**: Detailed deployment guide for existing OpenBao infrastructure
- **docs/GITHUB_OAUTH_TRANSCRIPT.md**: Manual GitHub OAuth flow with curl/PowerShell
- **README.md**: Assignment requirements and evaluation criteria
