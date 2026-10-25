# Design Document

## Architecture

The Integration Aggregator is a Python FastAPI service that orchestrates OAuth token retrieval using OpenBao's oauthapp plugin. The service implements no OAuth logic itself and persists no tokens.

### Components

- **FastAPI Application** (`app/main.py`): HTTP API with async endpoints
- **Provider Registry** (`app/services/provider_registry.py`): In-memory provider metadata
- **OAuth State Store** (`app/services/oauth_state.py`): In-memory OAuth state with TTL and consume-once semantics
- **OAuth Service** (`app/services/oauth_service.py`): Orchestrates connect and callback flows
- **Request Manager** (`app/services/request_manager.py`): Async request lifecycle with background worker
- **Token Service** (`app/services/token_service.py`): Background worker for token retrieval from OpenBao
- **OpenBao Adapter** (`app/integrations/openbao_oauth.py`): HTTP client for oauthapp plugin v3 API
- **Fake OpenBao** (`app/integrations/fake_openbao.py`): In-memory stand-in for testing

### Data Placement

**OpenBao (persistent secrets store):**
- Client secrets (`oauth2/servers/{name}`)
- OAuth tokens (`oauth2/creds/{provider}_{user}`)
- Plugin handles token refresh and expiry

**Service memory (ephemeral, never persisted):**
- Provider names and metadata (no secrets)
- OAuth state values with TTL and consume-once protection
- Async request records (status, tokens held only in memory)

### OAuth Authorization-Code Flow

1. **Provider Registration**: Operator calls `POST /providers` with client ID/secret. Service writes server config to OpenBao via `PUT oauth2/servers/{name}`. Client secret never leaves OpenBao.

2. **Connect Initiation**: Caller calls `POST /providers/{provider}/users/{user}/connect`. Service generates cryptographically random state, stores `(state → provider, user_id, expiry)` in memory, calls OpenBao `PUT oauth2/auth-code-url` to get authorization URL. Returns URL and state to caller.

3. **User Consent**: User opens authorization URL in browser, completes consent with OAuth provider. Provider redirects to callback with `code` and `state`.

4. **Callback**: Service receives `GET /callback?code&state`. Validates state (exists, not expired, not replayed), calls OpenBao `PUT oauth2/creds/{provider}_{user}` with code. Plugin exchanges code for tokens and stores them in OpenBao. Returns success to user.

5. **Token Retrieval (Async)**: Caller requests `GET /{provider}/{user}`. Service returns `202 Accepted` with request ID and `Location: /requests/{id}`. Background worker calls OpenBao `GET oauth2/creds/{provider}_{user}` (plugin refreshes if expired), holds token in memory request record. Caller polls `GET /requests/{id}` until status is `succeeded` or `failed`. Token returned only on success.

### OpenBao Plugin Integration

The service uses the oauthapp plugin v3 API:
- `PUT servers/{name}`: Register provider with client secret
- `PUT auth-code-url`: Generate authorization URL
- `PUT creds/{name}`: Exchange authorization code for tokens
- `GET creds/{name}?minimum_seconds=10`: Retrieve token (plugin refreshes if needed)

All OpenBao communication uses the child token with least-privilege policy (`oauth2/*` capabilities: create, read, update, delete, list).

### Async Token Retrieval Lifecycle

Request states: `pending` → `in_progress` → `succeeded` | `failed`

1. Caller requests token → Service creates request record with `pending` status, queues to worker
2. Worker picks up request → Status changes to `in_progress`
3. Worker calls OpenBao `GET creds/{name}` → On success, stores token in request record, status `succeeded`
4. On error, stores sanitized error message, status `failed`
5. Caller polls `/requests/{id}` → Returns current status and token/error when terminal
6. Completed records have TTL cleanup (default 1 hour)

### Security Decisions

- **State validation**: Cryptographically random state (secrets.token_urlsafe), bound to (provider, user_id), TTL (default 10 min), consume-once under asyncio.Lock
- **Secret redaction**: Logger redacts tokens, secrets, codes from all output. Fake OpenBao never logs secrets.
- **No token leakage**: Token only returned via `/requests/{id}` on success. Never in logs, other HTTP responses, or Git.
- **Least-privilege OpenBao policy**: Service token limited to `oauth2/*` only, no root or admin access
- **Non-root container**: Dockerfile runs as non-root user (appuser:1000)
- **Read-only root filesystem**: Container securityContext sets readOnlyRootFilesystem=true

### Known Limitations

**Single-replica deployment:**
- In-memory state (OAuth state, async requests) is not shared across replicas
- If a replica restarts, all pending OAuth states and async requests are lost
- Callback may hit a different replica than the one that generated the state
- Token request may poll a different replica than the one processing the request

**Production fix:** Use Redis or similar distributed cache for OAuth state and async request records. Use a proper message queue (RabbitMQ, Kafka) for async token retrieval instead of in-memory queue.

**OpenBao dev mode:**
- No persistence (tokens lost on restart)
- No unsealing process (dev mode auto-unseals)
- No TLS (HTTP only)
- Single replica only (no HA)

**Production fix:** Use OpenBao production mode with Raft storage, proper unsealing, TLS, and HA configuration.

### Operational Considerations

- **Token refresh**: Handled entirely by oauthapp plugin. Service does not implement refresh logic.
- **State TTL**: Default 10 minutes for OAuth state, 1 hour for async request records. Configurable via environment variables.
- **Health checks**: `/healthz` (liveness, no credentials), `/readyz` (OpenBao connectivity check)
- **Logging**: Structured logging with secret redaction. Log level configurable (INFO default).
- **Error handling**: Sanitized errors returned to clients. Detailed errors logged without secrets.
- **Idempotent deployment**: Makefile `make up` is idempotent. Helm chart uses `upgrade --install`.

### Providers

The service supports GitHub, Google, GitLab, and generic OIDC providers via the oauthapp plugin. For CI/automation, navikt/mock-oauth2-server is used as a programmatically driven local OIDC provider. Real GitHub OAuth is documented separately in `docs/GITHUB_OAUTH_TRANSCRIPT.md`.
