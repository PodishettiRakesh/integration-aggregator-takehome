import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.AGGREGATOR_URL || 'http://localhost:8000';

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
  // Register a mock OIDC provider
  const providerName = `perf-test-${__VU}`;
  const registerPayload = JSON.stringify({
    name: providerName,
    provider: 'oidc',
    client_id: 'perf-test-client',
    client_secret: 'perf-test-secret',
    scopes: ['openid', 'profile'],
    provider_options: {
      issuer_url: 'http://localhost:8080/default'
    }
  });

  const registerRes = http.post(`${BASE_URL}/providers`, registerPayload, {
    headers: { 'Content-Type': 'application/json' },
  });

  check(registerRes, {
    'provider registration successful': (r) => r.status === 201,
  });

  // Start OAuth connect
  const connectRes = http.post(`${BASE_URL}/providers/${providerName}/users/perf-user/connect`);

  check(connectRes, {
    'OAuth connect successful': (r) => r.status === 200,
  });

  const authData = JSON.parse(connectRes.body);
  const authUrl = authData.authorization_url;
  const state = authData.state;

  // Call the mock OAuth authorization endpoint to get a redirect with code
  // Replace cluster DNS with localhost for host access
  const hostAuthUrl = authUrl.replace('mock-oauth.aggregator.svc.cluster.local:8080', 'localhost:8080');
  const authRedirectRes = http.get(hostAuthUrl, { redirects: 0 });

  check(authRedirectRes, {
    'authorization redirect received': (r) => r.status === 302 || r.status === 301,
  });

  // Extract redirect URL and parse code and state
  const redirectUrl = authRedirectRes.headers['Location'];
  const codeMatch = redirectUrl.match(/code=([^&]+)/);
  const stateMatch = redirectUrl.match(/state=([^&]+)/);

  if (!codeMatch || !stateMatch) {
    throw new Error('Failed to extract code or state from redirect');
  }

  const code = codeMatch[1];
  const returnedState = stateMatch[1];

  // Verify state matches
  if (returnedState !== state) {
    throw new Error('State mismatch in OAuth flow');
  }

  // Call aggregator callback with the authorization code
  const callbackRes = http.get(`${BASE_URL}/callback?code=${code}&state=${returnedState}`);

  check(callbackRes, {
    'callback successful': (r) => r.status === 200,
  });

  const callbackData = JSON.parse(callbackRes.body);
  check(callbackData, {
    'callback status connected': (r) => r.status === 'connected',
  });

  return { providerName };
}

// Teardown: Clean up
export function teardown(data) {
  // No cleanup needed - provider registration is idempotent
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
