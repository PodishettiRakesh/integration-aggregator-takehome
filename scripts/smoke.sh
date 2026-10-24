#!/bin/bash
# Smoke test for Integration Aggregator
# Requires: curl, jq, kubectl
# Assumes aggregator is deployed and accessible via port-forward or service
# Note: This script requires a Bash-compatible environment. It does not run natively in PowerShell.

set -o pipefail

# Cleanup trap
cleanup() {
    if [ -n "$PF_PID" ]; then
        kill "$PF_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# Configuration
AGGREGATOR_URL="${AGGREGATOR_URL:-http://localhost:8000}"
NAMESPACE="${NAMESPACE:-aggregator}"
RELEASE_NAME="${RELEASE_NAME:-aggregator}"
MOCK_OAUTH_RELEASE="${MOCK_OAUTH_RELEASE:-mock-oauth}"
# Service name for mock-oauth2-server chart (Helm 3 defaults to release name)
MOCK_OAUTH_SERVICE="${MOCK_OAUTH_SERVICE:-mock-oauth}"
POLL_TIMEOUT="${POLL_TIMEOUT:-30}"
POLL_INTERVAL="${POLL_INTERVAL:-1}"
CURL_TIMEOUT="${CURL_TIMEOUT:-10}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test counters
TESTS_PASSED=0
TESTS_FAILED=0

# Helper functions (all logging to stderr to avoid mixing with return values)
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1" >&2
}

log_pass() {
    echo -e "${GREEN}[PASS]${NC} $1" >&2
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1" >&2
    TESTS_FAILED=$((TESTS_FAILED + 1))
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1" >&2
}

# Check if required tools are available
check_tools() {
    log_info "Checking required tools..."
    if ! command -v curl &> /dev/null; then
        log_fail "curl not found"
        return 1
    fi
    if ! command -v jq &> /dev/null; then
        log_fail "jq not found"
        return 1
    fi
    if ! command -v kubectl &> /dev/null; then
        log_fail "kubectl not found"
        return 1
    fi
    log_pass "All required tools available"
}

# Check if aggregator is reachable
check_reachability() {
    log_info "Checking aggregator reachability at $AGGREGATOR_URL..."
    
    http_code=$(curl -sf -o /dev/null -w "%{http_code}" "$AGGREGATOR_URL/healthz")
    if [ "$http_code" = "200" ]; then
        log_pass "Aggregator is reachable"
    else
        log_fail "Aggregator is not reachable at $AGGREGATOR_URL (HTTP $http_code)"
        return 1
    fi
}

# Test healthz endpoint
test_healthz() {
    log_info "Testing /healthz endpoint..."
    
    response=$(curl -s "$AGGREGATOR_URL/healthz")
    status=$(echo "$response" | jq -r '.status // empty')
    
    if [ "$status" = "ok" ]; then
        log_pass "/healthz returned status: ok"
    else
        log_fail "/healthz unexpected response (status: $status): $response"
        return 1
    fi
}

# Test readyz endpoint
test_readyz() {
    log_info "Testing /readyz endpoint..."
    
    response=$(curl -s -w "\n%{http_code}" "$AGGREGATOR_URL/readyz")
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n-1)
    status=$(echo "$body" | jq -r '.status // empty')
    openbao_status=$(echo "$body" | jq -r '.openbao // empty')
    
    if [ "$http_code" = "200" ] && [ "$status" = "ready" ] && [ "$openbao_status" = "up" ]; then
        log_pass "/readyz returned status: ready, openbao: up"
    elif [ "$http_code" = "503" ]; then
        log_fail "/readyz returned 503 - OpenBao dependency failure (status: $status, openbao: $openbao_status)"
        return 1
    else
        log_fail "/readyz unexpected response (HTTP $http_code, status: $status, openbao: $openbao_status): $body"
        return 1
    fi
}

# Global variables for test results
PROVIDER_NAME=""
AUTH_URL=""
STATE=""
REQUEST_ID=""
PF_PID=""

# Test provider registration
test_provider_registration() {
    log_info "Testing provider registration..."
    
    # Register a mock OIDC provider
    # Note: issuer_url uses Kubernetes DNS because OpenBao runs in-cluster and needs to reach it
    PROVIDER_NAME="test-oidc-$(date +%s)"
    response=$(curl -s -w "\n%{http_code}" -X POST "$AGGREGATOR_URL/providers" \
        -H "Content-Type: application/json" \
        -d "{
            \"name\": \"$PROVIDER_NAME\",
            \"provider\": \"oidc\",
            \"client_id\": \"test-client-id\",
            \"client_secret\": \"test-client-secret\",
            \"scopes\": [\"openid\", \"profile\"],
            \"provider_options\": {
                \"issuer_url\": \"http://mock-oauth.$NAMESPACE.svc.cluster.local:8080/default\"
            }
        }")
    
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "201" ]; then
        # Verify response doesn't contain client_secret
        if echo "$body" | jq -e '.client_secret' &> /dev/null; then
            log_fail "Provider registration response contains client_secret (should be redacted)"
            PROVIDER_NAME=""
            return 1
        fi
        log_pass "Provider registered successfully: $PROVIDER_NAME"
    else
        log_fail "Provider registration failed (HTTP $http_code): $body"
        PROVIDER_NAME=""
        return 1
    fi
}

# Test OAuth connect flow
test_oauth_connect() {
    log_info "Testing OAuth connect for provider: $PROVIDER_NAME"

    response=$(curl -s -w "\n%{http_code}" -X POST "$AGGREGATOR_URL/providers/$PROVIDER_NAME/users/test-user/connect")
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n-1)

    if [ "$http_code" = "200" ]; then
        AUTH_URL=$(echo "$body" | jq -r '.authorization_url // empty')
        STATE=$(echo "$body" | jq -r '.state // empty')

        if [ -n "$AUTH_URL" ] && [ -n "$STATE" ]; then
            log_pass "OAuth connect successful, state received"
            log_info "Authorization URL: $AUTH_URL"
        else
            log_fail "OAuth connect response missing authorization_url or state: $body"
            AUTH_URL=""
            STATE=""
            return 1
        fi
    else
        log_fail "OAuth connect failed (HTTP $http_code): $body"
        AUTH_URL=""
        STATE=""
        return 1
    fi
}

# Test callback with mock OAuth2
test_callback() {
    log_info "Testing OAuth callback..."

    if [ -z "$AUTH_URL" ]; then
        log_fail "Failed to get authorization URL"
        return 1
    fi

    # Port-forward mock OAuth service to make it reachable from host
    log_info "Setting up port-forward to mock OAuth service..."
    kubectl port-forward -n "$NAMESPACE" svc/"$MOCK_OAUTH_SERVICE" 8080:8080 > /dev/null 2>&1 &
    PF_PID=$!
    sleep 2

    # Replace cluster DNS with localhost in auth_url for host access
    host_auth_url=$(echo "$AUTH_URL" | sed "s|mock-oauth.$NAMESPACE.svc.cluster.local:8080|localhost:8080|g")
    log_info "Host-accessible authorization URL: $host_auth_url"

    log_info "Requesting authorization code from mock OAuth2 server..."
    # Call authorize endpoint, capture HTTP status and redirect URL
    response=$(curl -s -w "\n%{http_code}" --max-time "$CURL_TIMEOUT" "$host_auth_url")
    http_code=$(echo "$response" | tail -n1)
    redirect_url=$(echo "$response" | head -n-1)
    
    if [ "$http_code" != "302" ] && [ "$http_code" != "301" ]; then
        log_fail "Authorization endpoint returned unexpected HTTP status: $http_code (expected redirect)"
        return 1
    fi
    
    if [ -z "$redirect_url" ]; then
        log_fail "No redirect URL received from authorize endpoint"
        return 1
    fi

    log_info "Redirect URL received: $redirect_url"
    
    # Verify redirect points to the configured callback
    if ! echo "$redirect_url" | grep -q "callback"; then
        log_fail "Redirect does not point to callback endpoint: $redirect_url"
        return 1
    fi
    
    # Extract authorization code and state from redirect URL
    # URL format: http://localhost:8000/callback?code=xxx&state=yyy
    code=$(echo "$redirect_url" | sed 's/.*code=\([^&]*\).*/\1/')
    returned_state=$(echo "$redirect_url" | sed 's/.*state=\([^&]*\).*/\1/')
    
    if [ -z "$code" ]; then
        log_fail "Failed to extract authorization code from redirect URL"
        return 1
    fi
    
    if [ -z "$returned_state" ]; then
        log_fail "Failed to extract state from redirect URL"
        return 1
    fi

    # Verify state matches
    if [ "$returned_state" != "$STATE" ]; then
        log_fail "State mismatch: expected $STATE, got $returned_state"
        return 1
    fi
    
    log_pass "State verified, authorization code received"
    
    # Call aggregator callback with the authorization code
    log_info "Calling aggregator callback..."
    # URL-encode parameters for safety
    encoded_code=$(printf '%s' "$code" | jq -sRr @uri)
    encoded_state=$(printf '%s' "$returned_state" | jq -sRr @uri)
    response=$(curl -s -w "\n%{http_code}" --max-time "$CURL_TIMEOUT" "$AGGREGATOR_URL/callback?code=$encoded_code&state=$encoded_state")
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "200" ]; then
        callback_status=$(echo "$body" | jq -r '.status // empty')
        if [ "$callback_status" = "connected" ]; then
            log_pass "OAuth callback successful"
        else
            log_fail "OAuth callback returned unexpected status: $callback_status"
            log_fail "Response: $body"
            return 1
        fi
    else
        log_fail "OAuth callback failed (HTTP $http_code): $body"
        return 1
    fi
}

# Test async token request
test_token_request() {
    log_info "Testing async token request for provider: $PROVIDER_NAME"
    
    response=$(curl -s -w "\n%{http_code}\n%{header_location}" --max-time "$CURL_TIMEOUT" "$AGGREGATOR_URL/$PROVIDER_NAME/test-user")
    http_code=$(echo "$response" | head -n1)
    location=$(echo "$response" | head -n2 | tail -n1)
    body=$(echo "$response" | tail -n+3)
    
    if [ "$http_code" = "202" ]; then
        REQUEST_ID=$(echo "$body" | jq -r '.request_id // empty')
        
        if [ -n "$REQUEST_ID" ]; then
            # Validate Location header points to expected polling endpoint
            expected_location="/requests/$REQUEST_ID"
            if [ "$location" != "$expected_location" ]; then
                log_fail "Location header mismatch: expected $expected_location, got $location"
                REQUEST_ID=""
                return 1
            fi
            log_pass "Token request accepted, request_id: $REQUEST_ID"
        else
            log_fail "Token request response missing request_id: $body"
            REQUEST_ID=""
            return 1
        fi
    else
        log_fail "Token request failed (HTTP $http_code): $body"
        REQUEST_ID=""
        return 1
    fi
}

# Poll request status
poll_request_status() {
    log_info "Polling request status for request_id: $REQUEST_ID (timeout: ${POLL_TIMEOUT}s)..."
    
    elapsed=0
    while [ $elapsed -lt $POLL_TIMEOUT ]; do
        response=$(curl -s -w "\n%{http_code}" --max-time "$CURL_TIMEOUT" "$AGGREGATOR_URL/requests/$REQUEST_ID")
        http_code=$(echo "$response" | tail -n1)
        body=$(echo "$response" | head -n-1)
        
        if [ "$http_code" != "200" ]; then
            log_fail "Poll request returned HTTP $http_code"
            return 1
        fi
        
        status=$(echo "$body" | jq -r '.status // empty')
        
        if [ "$status" = "succeeded" ] || [ "$status" = "failed" ]; then
            if [ "$status" = "succeeded" ]; then
                # Verify token is present but don't print it
                has_token=$(echo "$body" | jq -r '.access_token // empty')
                if [ -n "$has_token" ]; then
                    log_pass "Request succeeded, token received (redacted)"
                else
                    log_fail "Request succeeded but no token in response"
                    return 1
                fi
            else
                error=$(echo "$body" | jq -r '.error // empty')
                log_fail "Request failed with error: $error"
                return 1
            fi
            return 0
        fi
        
        sleep $POLL_INTERVAL
        elapsed=$((elapsed + POLL_INTERVAL))
    done
    
    log_fail "Request polling timed out after ${POLL_TIMEOUT}s"
    return 1
}

# Main test flow
main() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}Integration Aggregator Smoke Test${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    
    check_tools || { print_summary; exit 1; }
    check_reachability || { print_summary; exit 1; }
    test_healthz || { print_summary; exit 1; }
    test_readyz || { print_summary; exit 1; }
    
    # If readyz fails, we can't proceed with integration tests
    if [ $TESTS_FAILED -gt 0 ]; then
        log_warn "Skipping integration tests due to readiness check failure"
        print_summary
        exit 1
    fi
    
    echo ""
    log_info "Starting integration tests..."
    echo ""
    
    # Provider registration
    test_provider_registration || { print_summary; exit 1; }
    
    # OAuth connect
    test_oauth_connect || { print_summary; exit 1; }
    
    # OAuth callback
    test_callback || { log_warn "Callback test failed - skipping token request test"; print_summary; exit 1; }
    
    # Token request
    test_token_request || { print_summary; exit 1; }
    
    # Poll request status
    poll_request_status || { print_summary; exit 1; }
    
    print_summary
}

print_summary() {
    echo ""
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}Test Summary${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
    echo -e "${RED}Failed: $TESTS_FAILED${NC}"
    echo -e "${BLUE}========================================${NC}"
    
    if [ $TESTS_FAILED -eq 0 ]; then
        exit 0
    else
        exit 1
    fi
}

# Run main function
main
