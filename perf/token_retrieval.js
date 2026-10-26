import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.AGGREGATOR_URL || 'http://localhost:8000';

// Mock OAuth server. OpenBao runs in its own pod and fetches the OIDC discovery document from
// issuer_url, so the issuer must use the in-cluster DNS name (same as scripts/smoke.sh).
// "localhost" would point at the OpenBao pod itself and registration would fail.
const NAMESPACE = __ENV.NAMESPACE || 'aggregator';
const MOCK_OAUTH_SERVICE = __ENV.MOCK_OAUTH_SERVICE || 'mock-oauth';
const MOCK_OAUTH_CLUSTER_HOST = `${MOCK_OAUTH_SERVICE}.${NAMESPACE}.svc.cluster.local:8080`;
// Where the CI host reaches the same server (kubectl port-forward svc/mock-oauth 8080:8080)
const MOCK_OAUTH_LOCAL_HOST = __ENV.MOCK_OAUTH_LOCAL_HOST || 'localhost:8080';
const ISSUER_URL = `http://${MOCK_OAUTH_CLUSTER_HOST}/default`;

// Remove anything that could carry OAuth secrets before text is printed:
// query strings (authorization code, state) and key/value or JSON pairs for sensitive keys.
function scrub(text) {
  return String(text)
    .replace(/\?[^\s"'}]*/g, '?[REDACTED]')
    .replace(/(client_secret|access_token|refresh_token|token|code|state)(["']?\s*[:=]\s*["']?)[^&\s"',}]+/gi, '$1$2[REDACTED]')
    .slice(0, 300);
}

// Safe one-line description of a failed response: status plus only the API's own
// error/message/validation fields (never the raw body, never validation "input" values).
function describeResponse(res) {
  const parts = [`HTTP ${res.status}`];
  if (res.error) {
    parts.push(`network_error=${scrub(res.error)} (code ${res.error_code})`);
  }
  try {
    const b = JSON.parse(res.body);
    if (b && typeof b === 'object') {
      if (typeof b.error === 'string') parts.push(`error=${scrub(b.error)}`);
      if (typeof b.message === 'string') parts.push(`message=${scrub(b.message)}`);
      if (Array.isArray(b.detail)) {
        const items = b.detail.slice(0, 5).map((d) => `${(d.loc || []).join('.')}: ${scrub(d.msg || '')}`);
        parts.push(`detail=${items.join('; ')}`);
      }
    }
  } catch (e) {
    parts.push(`non-JSON body (${res.body ? res.body.length : 0} bytes)`);
  }
  return parts.join(' ');
}

function setupFail(step, res, hint) {
  throw new Error(`setup failed at "${step}": ${describeResponse(res)}${hint ? '. ' + hint : ''}`);
}

// Test configuration
export const options = {
  stages: [
    { duration: '10s', target: 10 },   // Ramp up to 10 users
    { duration: '10s', target: 10 },   // Stay at 10 users
    { duration: '10s', target: 20 },   // Ramp up to 20 users
    { duration: '10s', target: 20 },   // Stay at 20 users
    { duration: '10s', target: 50 },   // Ramp up to 50 users
    { duration: '10s', target: 50 },   // Stay at 50 users
  ],
  thresholds: {
    http_req_duration: ['p(95)<5000'], // 95% of requests should complete within 5s
    http_req_failed: ['rate<0.1'],      // Error rate should be less than 10%
  },
};

// Setup: Register a provider and complete OAuth flow once
export function setup() {
  // Preflight: make sure the mock OAuth server is reachable from this host BEFORE any URL carrying
  // an OAuth state is requested. (k6 itself prints the full URL of a network-failed request, so a
  // failure on the authorize URL below would put the state into the CI log.)
  const mockRes = http.get(`http://${MOCK_OAUTH_LOCAL_HOST}/default/.well-known/openid-configuration`, { redirects: 0 });
  const mockReachable = check(mockRes, {
    'mock OAuth server reachable': (r) => r.status === 200,
  });
  if (!mockReachable) {
    setupFail('mock OAuth reachability', mockRes,
      `expected the mock OAuth server on ${MOCK_OAUTH_LOCAL_HOST} (kubectl port-forward svc/${MOCK_OAUTH_SERVICE} 8080:8080)`);
  }

  // Register a mock OIDC provider
  // Unique per run: the backend rejects a repeated provider name with HTTP 409 (registration is
  // NOT idempotent), so a fixed name would break any second run against the same deployment.
  const providerName = `perf-test-${Date.now()}`;
  const registerPayload = JSON.stringify({
    name: providerName,
    provider: 'oidc',
    client_id: 'perf-test-client',
    client_secret: 'perf-test-secret',
    scopes: ['openid', 'profile'],
    provider_options: {
      issuer_url: ISSUER_URL
    }
  });

  const registerRes = http.post(`${BASE_URL}/providers`, registerPayload, {
    headers: { 'Content-Type': 'application/json' },
  });

  const registered = check(registerRes, {
    'provider registration successful': (r) => r.status === 201,
  });
  if (!registered) {
    // 5xx = the backend could not write the provider to OpenBao (e.g. OIDC discovery of issuer_url failed)
    setupFail('provider registration', registerRes,
      registerRes.status >= 500
        ? `issuer_url must be reachable from the OpenBao pod (using ${ISSUER_URL})`
        : undefined);
  }

  // Start OAuth connect
  const connectRes = http.post(`${BASE_URL}/providers/${providerName}/users/perf-user/connect`);

  const connected = check(connectRes, {
    'OAuth connect successful': (r) => r.status === 200,
  });
  if (!connected) {
    setupFail('OAuth connect', connectRes);
  }

  let authData;
  try {
    authData = JSON.parse(connectRes.body);
  } catch (e) {
    setupFail('OAuth connect (response is not JSON)', connectRes);
  }
  const authUrl = authData && authData.authorization_url;
  const state = authData && authData.state;
  if (typeof authUrl !== 'string' || !authUrl || typeof state !== 'string' || !state) {
    setupFail('OAuth connect (response missing authorization_url or state)', connectRes);
  }

  // Call the mock OAuth authorization endpoint to get a redirect with code
  // Replace cluster DNS with localhost for host access
  const hostAuthUrl = authUrl.replace(MOCK_OAUTH_CLUSTER_HOST, MOCK_OAUTH_LOCAL_HOST);
  const authRedirectRes = http.get(hostAuthUrl, { redirects: 0 });

  const redirected = check(authRedirectRes, {
    'authorization redirect received': (r) => r.status === 302 || r.status === 301,
  });
  if (!redirected) {
    setupFail('authorization redirect', authRedirectRes);
  }

  // Extract redirect URL and parse code and state
  const redirectUrl = authRedirectRes.headers['Location'];
  if (typeof redirectUrl !== 'string' || !redirectUrl) {
    throw new Error(`setup failed at "authorization redirect": HTTP ${authRedirectRes.status} without a Location header`);
  }
  // Anchored to a real query parameter (not e.g. "session_state="); values stay URL-encoded as received
  const codeMatch = redirectUrl.match(/[?&]code=([^&#]+)/);
  const stateMatch = redirectUrl.match(/[?&]state=([^&#]+)/);

  if (!codeMatch || !stateMatch) {
    throw new Error('Failed to extract code or state from redirect');
  }

  const code = codeMatch[1];
  const returnedState = stateMatch[1];

  // Verify state matches (decode first: the redirect carries it URL-encoded, the connect response does not)
  let decodedState = null;
  try {
    decodedState = decodeURIComponent(returnedState.replace(/\+/g, ' '));
  } catch (e) {
    // malformed escape: treated as a mismatch below, without printing the value
  }
  if (decodedState !== state) {
    throw new Error('State mismatch in OAuth flow');
  }

  // Call aggregator callback with the authorization code
  const callbackRes = http.get(`${BASE_URL}/callback?code=${code}&state=${returnedState}`);

  const callbackOk = check(callbackRes, {
    'callback successful': (r) => r.status === 200,
  });
  if (!callbackOk) {
    setupFail('OAuth callback', callbackRes);
  }

  let callbackData;
  try {
    callbackData = JSON.parse(callbackRes.body);
  } catch (e) {
    setupFail('OAuth callback (response is not JSON)', callbackRes);
  }
  const callbackConnected = check(callbackData, {
    'callback status connected': (r) => r.status === 'connected',
  });
  if (!callbackConnected) {
    setupFail('OAuth callback (status is not "connected")', callbackRes);
  }

  return { providerName };
}

// Teardown: Clean up
export function teardown(data) {
  // No cleanup needed - the provider registry is in memory and every run uses a unique provider name
}

// Main VU logic
export default function (data) {
  const { providerName } = data;

  // Request token retrieval (async)
  const tokenRes = http.get(`${BASE_URL}/${providerName}/perf-user`);

  check(tokenRes, {
    'token request accepted': (r) => r.status === 202,
    'Location header present': (r) => r.headers['Location'] !== undefined,
  });

  if (tokenRes.status === 202) {
    const location = tokenRes.headers['Location'];
    const requestId = location.split('/').pop();

    // Poll for result
    let maxPolls = 10;
    let pollCount = 0;
    let succeeded = false;

    while (pollCount < maxPolls && !succeeded) {
      sleep(0.5); // Wait 500ms between polls
      const statusRes = http.get(`${BASE_URL}/requests/${requestId}`);

      check(statusRes, {
        'status request successful': (r) => r.status === 200,
      });

      if (statusRes.status === 200) {
        const statusData = JSON.parse(statusRes.body);
        if (statusData.status === 'succeeded') {
          succeeded = true;
          check(statusData, {
            'token present in successful response': (r) => r.access_token !== undefined,
          });
        } else if (statusData.status === 'failed') {
          // Token not pre-stored - this is expected for perf test without full OAuth flow
          break;
        }
      }
      pollCount++;
    }
  }

  sleep(1); // Small pause between iterations
}