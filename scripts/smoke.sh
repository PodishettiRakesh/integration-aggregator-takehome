#!/bin/bash
# Smoke test for Integration Aggregator
# Requires: curl, jq, kubectl
# Assumes aggregator is deployed and accessible via port-forward or service
# Note: This script requires a Bash-compatible environment. It does not run natively in PowerShell.

set -e
set -o pipefail

# Configuration
AGGREGATOR_URL="${AGGREGATOR_URL:-http://localhost:8000}"
NAMESPACE="${NAMESPACE:-aggregator}"
RELEASE_NAME="${RELEASE_NAME:-aggregator}"
MOCK_OAUTH_RELEASE="${MOCK_OAUTH_RELEASE:-mock-oauth}"
MOCK_OAUTH_URL="${MOCK_OAUTH_URL:-http://localhost:8080/default}"
POLL_TIMEOUT="${POLL_TIMEOUT:-30}"
POLL_INTERVAL="${POLL_INTERVAL:-1}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test counters
TESTS_PASSED=0
TESTS_FAILED=0

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    TESTS_PASSED=$((TESTS_PASSED + 1)) || true
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    TESTS_FAILED=$((TESTS_FAILED + 1)) || true
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# Check if required tools are available
check_tools() {
    log_info "Checking required tools..."
    if ! command -v curl &> /dev/null; then
        log_fail "curl not found"
        exit 1
    fi
    if ! command -v jq &> /dev/null; then
        log_fail "jq not found"
        exit 1
    fi
    if ! command -v kubectl &> /dev/null; then
        log_fail "kubectl not found"
        exit 1
    fi
    log_pass "All required tools available"
}

# Check if aggregator is reachable
check_reachability() {
    log_info "Checking aggregator reachability at $AGGREGATOR_URL..."
    
    if curl -sf -o /dev/null -w "%{http_code}" "$AGGREGATOR_URL/healthz" | grep -q "200"; then
        log_pass "Aggregator is reachable"
    else
        log_fail "Aggregator is not reachable at $AGGREGATOR_URL"
        exit 1
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
        log_fail "/healthz unexpected response: $response"
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
        log_warn "/readyz returned 503 - OpenBao may not be ready"
        log_warn "Response: $response"
        return 1
    else
        log_fail "/readyz unexpected response (HTTP $http_code): $response"
        return 1
    fi
}

# Test provider registration
test_provider_registration() {
    log_info "Testing provider registration..."
    
    # Register a mock OIDC provider
    # Note: issuer_url uses Kubernetes DNS because OpenBao runs in-cluster and needs to reach it
    provider_name="test-oidc-$(date +%s)"
    response=$(curl -s -w "\n%{http_code}" -X POST "$AGGREGATOR_URL/providers" \
        -H "Content-Type: application/json" \
        -d "{
            \"name\": \"$provider_name\",
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
            return 1
        fi
        log_pass "Provider registered successfully: $provider_name"
        echo "$provider_name"
    else
        log_fail "Provider registration failed (HTTP $http_code): $body"
        return 1
    fi
}

# Test OAuth connect flow
test_oauth_connect() {
    local provider_name="$1"
    log_info "Testing OAuth connect for provider: $provider_name"

    response=$(curl -s -w "\n%{http_code}" -X POST "$AGGREGATOR_URL/providers/$provider_name/users/test-user/connect")
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n-1)

    if [ "$http_code" = "200" ]; then
        auth_url=$(echo "$body" | jq -r '.authorization_url // empty')
        state=$(echo "$body" | jq -r '.state // empty')

        if [ -n "$auth_url" ] && [ -n "$state" ]; then
            log_pass "OAuth connect successful, state received"
            log_info "Authorization URL: $auth_url"
            # Return both auth_url and state as space-separated values
            echo "$auth_url $state"
        else
            log_fail "OAuth connect response missing authorization_url or state: $body"
            return 1
        fi
    else
        log_fail "OAuth connect failed (HTTP $http_code): $body"
        return 1
    fi
}

# Test callback with mock OAuth2
test_callback() {
    local provider_name="$1"
    local original_auth_url="$2"
    local original_state="$3"
    log_info "Testing OAuth callback..."

    # Use the authorization URL passed in (reused from connect)
    auth_url="$original_auth_url"
    
    if [ -z "$auth_url" ]; then
        log_fail "Failed to get authorization URL"
        return 1
    fi

    log_info "Requesting authorization code from mock OAuth2 server..."
    # Call authorize endpoint without following redirects, capture Location header
    redirect_url=$(curl -s -w "%{redirect_url}" -o /dev/null "$auth_url")
    
    if [ -z "$redirect_url" ]; then
        log_fail "No redirect URL received from authorize endpoint"
        return 1
    fi

    log_info "Redirect URL received: $redirect_url"
    
    # Extract authorization code from redirect URL using portable utilities
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
    if [ "$returned_state" != "$original_state" ]; then
        log_fail "State mismatch: expected $original_state, got $returned_state"
        return 1
    fi
    
    log_pass "State verified, authorization code received"
    
    # Call aggregator callback with the authorization code
    log_info "Calling aggregator callback..."
    response=$(curl -s -w "\n%{http_code}" "$AGGREGATOR_URL/callback?code=$code&state=$returned_state")
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
    local provider_name="$1"
    log_info "Testing async token request for provider: $provider_name"
    
    response=$(curl -s -w "\n%{http_code}" "$AGGREGATOR_URL/$provider_name/test-user")
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "202" ]; then
        request_id=$(echo "$body" | jq -r '.request_id // empty')
        
        if [ -n "$request_id" ]; then
            log_pass "Token request accepted, request_id: $request_id"
            echo "$request_id"
        else
            log_fail "Token request response missing request_id: $body"
            return 1
        fi
    else
        log_fail "Token request failed (HTTP $http_code): $body"
        return 1
    fi
}

# Poll request status
poll_request_status() {
    local request_id="$1"
    log_info "Polling request status for request_id: $request_id (timeout: ${POLL_TIMEOUT}s)..."
    
    elapsed=0
    while [ $elapsed -lt $POLL_TIMEOUT ]; do
        response=$(curl -s "$AGGREGATOR_URL/requests/$request_id")
        status=$(echo "$response" | jq -r '.status // empty')
        
        if [ "$status" = "succeeded" ] || [ "$status" = "failed" ]; then
            if [ "$status" = "succeeded" ]; then
                # Verify token is present but don't print it
                has_token=$(echo "$response" | jq -r '.access_token // empty')
                if [ -n "$has_token" ]; then
                    log_pass "Request succeeded, token received (redacted)"
                else
                    log_fail "Request succeeded but no token in response"
                    return 1
                fi
            else
                error=$(echo "$response" | jq -r '.error // empty')
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
    
    check_tools
    check_reachability
    test_healthz
    test_readyz
    
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
    provider_name=$(test_provider_registration)
    if [ $? -ne 0 ]; then
        print_summary
        exit 1
    fi
    
    # OAuth connect
    connect_result=$(test_oauth_connect "$provider_name")
    if [ $? -ne 0 ]; then
        print_summary
        exit 1
    fi
    
    # Parse auth_url and state from connect result
    auth_url=$(echo "$connect_result" | awk '{print $1}')
    state=$(echo "$connect_result" | awk '{print $2}')
    
    # OAuth callback
    test_callback "$provider_name" "$auth_url" "$state"
    if [ $? -ne 0 ]; then
        log_warn "Callback test failed - skipping token request test"
        print_summary
        exit 1
    fi
    
    # Token request
    request_id=$(test_token_request "$provider_name")
    if [ $? -ne 0 ]; then
        print_summary
        exit 1
    fi
    
    # Poll request status
    poll_request_status "$request_id"
    if [ $? -ne 0 ]; then
        print_summary
        exit 1
    fi
    
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
