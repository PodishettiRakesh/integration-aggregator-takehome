# Design Document

## Overview

The Integration Aggregator is a Python FastAPI service that orchestrates OAuth token retrieval using OpenBao's oauthapp plugin. The service implements no OAuth logic itself and persists no tokens. It provides a unified API for registering OAuth providers, managing user consent flows, and retrieving access tokens asynchronously.

## Architecture and Request Flow

### Components

- **FastAPI Application** (`app/main.py`): HTTP API with async endpoints and dependency injection
- **Provider Registry** (`app/services/provider_registry.py`): In-memory provider metadata
- **OAuth State Store** (`app/services/oauth_state.py`): In-memory OAuth state with TTL and consume-once semantics
- **OAuth Service** (`app/services/oauth_service.py`): Orchestrates connect and callback flows
- **Request Manager** (`app/services/request_manager.py`): Async request lifecycle with background worker
- **Token Service** (`app/services/token_service.py`): Background worker for token retrieval from OpenBao
- **OpenBao Adapter** (`app/integrations/openbao_oauth.py`): HTTP client for oauthapp plugin v3 API

### Request Flow

1. **Provider Registration**: Operator calls `POST /providers` with client ID and secret. Service writes server config to OpenBao via `PUT oauth2/servers/{name}`. Client secret never leaves OpenBao. Provider metadata (name, client_id, scopes) stored in-memory.

2. **Connect Initiation**: Caller calls `POST /providers/{provider}/users/{user}/connect`. Service generates cryptographically random state, stores `(state → provider, user_id, expiry)` in memory, calls OpenBao `PUT oauth2/auth-code-url` to get authorization URL. Returns URL and state to caller.

3. **User Consent**: User opens authorization URL in browser, completes consent with OAuth provider. Provider redirects to callback with `code` and `state`.

4. **Callback**: Service receives `GET /callback?code&state`. Validates state (exists, not expired, not replayed), calls OpenBao `PUT oauth2/creds/{provider}_{user}` with code. Plugin exchanges code for tokens and stores them in OpenBao. Returns success to user.

5. **Token Retrieval (Async)**: Caller requests `GET /{provider}/{user}`. Service returns `202 Accepted` with request ID and `Location: /requests/{id}`. Background worker calls OpenBao `GET oauth2/creds/{provider}_{user}` (plugin refreshes if expired), holds token in memory request record. Caller polls `GET /requests/{id}` until status is `succeeded` or `failed`. Token returned only on success.

### Real vs Mock Provider

The service supports GitHub, Google, GitLab, and generic OIDC providers via the oauthapp plugin. For automated CI testing, navikt/mock-oauth2-server is used as a programmatically driven local OIDC provider that can complete consent without browser interaction. Real GitHub OAuth is documented separately in `docs/GITHUB_OAUTH_TRANSCRIPT.md`.

## What Lives Where and Why

| Component/Data | Location | Reason |
|----------------|----------|--------|
| Provider metadata (name, client_id, scopes) | Service memory (ProviderRegistry) | Fast lookup, no secrets, acceptable to lose on restart |
| OAuth state values (state → provider, user_id, expiry) | Service memory (OAuthStateStore) | CSRF protection, consume-once validation, short-lived (10 min TTL) |
| Async request records (status, tokens) | Service memory (RequestManager) | Polling interface, tokens only held transiently, TTL cleanup (1 hour) |
| Client secrets | OpenBao (`oauth2/servers/{name}`) | Persistent secrets store, never exposed to application |
| OAuth access/refresh tokens | OpenBao (`oauth2/creds/{provider}_{user}`) | Plugin handles refresh and expiry, never in application memory or logs |
| OpenBao plugin binary | OpenBao pod filesystem (`/vault/plugins/oauthapp`) | Plugin registration required for oauthapp secrets engine |
| OpenBao policy | OpenBao (`aggregator` policy) | Least-privilege access to `oauth2/*` paths only |
| Application configuration | Environment variables + Helm values | Configurable per environment, secrets via Kubernetes Secrets |

**Consequences of in-memory storage**: Provider metadata, OAuth state, and async request records are not shared between replicas. If a replica restarts, all pending OAuth states and async requests are lost. Callback may hit a different replica than the one that generated the state. Token request may poll a different replica than the one processing the request. This limits deployment to a single replica.

## API and Failure Handling

### Endpoints

- `POST /providers`: Register OAuth provider with client ID and secret
- `POST /providers/{provider}/users/{user}/connect`: Start OAuth flow, return authorization URL and state
- `GET /callback`: Handle OAuth provider redirect, exchange code for tokens
- `GET /{provider}/{user}`: Request access token (async, returns 202)
- `GET /requests/{id}`: Poll async request status and retrieve token
- `GET /healthz`: Liveness check (no credentials required)
- `GET /readyz`: Readiness check (verifies OpenBao connectivity)

### Error Handling and Security

- **State validation**: Cryptographically random state (`secrets.token_urlsafe`), bound to (provider, user_id), TTL (default 10 min), consume-once under `asyncio.Lock`
- **Secret redaction**: Logger redacts tokens, secrets, codes from all output via regex pattern matching
- **No token leakage**: Token only returned via `/requests/{id}` on success. Never in logs, other HTTP responses, or Git
- **Least-privilege OpenBao policy**: Service token limited to `oauth2/*` capabilities (create, read, update, delete, list)
- **HTTP timeouts**: Configurable timeout (default 5 seconds) for OpenBao requests
- **Sanitized error responses**: Error messages redacted before returning to clients
- **Request validation**: Pydantic models validate all request payloads
- **Concurrent request protection**: Async request status terminal states (`succeeded`, `failed`) are idempotent

## Deployment and Testing

### Deployment

- **Docker**: Multi-stage build from Python 3.11 slim, runs as non-root user (appuser:1000), read-only root filesystem, health check via `/healthz`
- **Helm Chart**: Configurable values for image, OpenBao address, replica count, resource limits, probes. Uses `upgrade --install` for idempotent deployment
- **Makefile**: `make up` is idempotent. Verifies existing OpenBao installation, downloads and registers oauthapp plugin v3.4.1, creates aggregator policy and token, deploys app and mock OAuth server
- **OpenBao Configuration**: Plugin registered in catalog, oauthapp secrets engine enabled at `oauth2/` path, least-privilege policy for aggregator service
- **Kubernetes**: Deployment with liveness/readiness probes, security context (non-root, read-only filesystem), resource limits (500m CPU, 512Mi memory limit)

### Testing

- **Unit Tests**: pytest covering individual services and integrations (fake OpenBao for isolated testing)
- **CI Workflow**: Runs on every push to main/master. Steps: lint (Ruff), unit tests, build/push Docker image and Helm chart to GHCR, integration test
- **Integration Test**: Deploys minikube with OpenBao and mock OAuth server, runs end-to-end smoke test, runs k6 performance test on token retrieval endpoint
- **Smoke Test**: Bash script that registers mock OAuth provider, initiates connect, completes callback programmatically, submits async token request, polls for success
- **Performance Test**: k6 script loads token retrieval endpoint, generates report with latency (p50, p95, average, max) and throughput metrics

## Production Improvements

**Current limitations**:
- Single-replica deployment due to in-memory state
- OpenBao dev mode (no persistence, TLS, unsealing, HA)
- No retry logic for transient OpenBao failures
- No rate limiting or circuit breaking
- Limited observability (structured logging only, no metrics)
- No horizontal scaling safeguards

**Prioritized improvements**:

1. **Shared durable storage**: Move OAuth state and async request records from process memory to Redis or similar distributed cache. Enables horizontal scaling and survives replica restarts.

2. **Message queue architecture**: Replace in-memory asyncio task queue with proper message queue (RabbitMQ, Kafka, or AWS SQS) for reliable async token retrieval with retries and dead-letter queues.

3. **Production OpenBao**: Deploy OpenBao in production mode with Raft storage, proper unsealing process, TLS for all communication, and HA configuration with multiple replicas.

4. **Observability**: Add Prometheus metrics for request latency, error rates, token refresh counts, and queue depth. Add distributed tracing for request flow across services.

5. **Resilience**: Implement exponential backoff retries for OpenBao requests, circuit breaking for downstream failures, and graceful degradation during outages.

6. **Security hardening**: Add rate limiting to prevent abuse, stricter callback validation (PKCE), token revocation support, and regular secret rotation policies.

7. **Operational tooling**: Add administrative endpoints for health checks, configuration validation, and manual state cleanup. Add readiness checks that verify queue health and storage connectivity.
