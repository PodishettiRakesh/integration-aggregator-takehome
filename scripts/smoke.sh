#!/bin/bash
# Smoke test for Integration Aggregator
# Requires: curl, jq, kubectl
# Assumes aggregator is deployed and accessible via port-forward or service
# Note: This script requires a Bash-compatible environment. It does not run natively in PowerShell.

set -o pipefail

# Temp files for HTTP responses (headers/body kept out of the CI log)
TMP_DIR=$(mktemp -d) || { echo "Failed to create temp dir" >&2; exit 1; }
HDR_FILE="$TMP_DIR/headers"
BODY_FILE="$TMP_DIR/body"

# Cleanup: runs on every exit path (success, failure, Ctrl-C, SIGTERM)
cleanup() {
    if [ -n "$PF_PID" ]; then
        kill "$PF_PID" 2>/dev/null || true
    fi
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Configuration
AGGREGATOR_URL="${AGGREGATOR_URL:-http://localhost:8000}"
NAMESPACE="${NAMESPACE:-aggregator}"
RELEASE_NAME="${RELEASE_NAME:-aggregator}"
MOCK_OAUTH_RELEASE="${MOCK_OAUTH_RELEASE:-mock-oauth}"
# Service name of the mock OAuth2 server. CI (.github/workflows/ci.yml) creates a Service named "mock-oauth".
MOCK_OAUTH_SERVICE="${MOCK_OAUTH_SERVICE:-mock-oauth}"
# Where the mock OAuth2 server is reachable from this host (CI port-forwards svc/mock-oauth 8080:8080)
MOCK_OAUTH_LOCAL_URL="${MOCK_OAUTH_LOCAL_URL:-http://localhost:8080}"
# Optional override for the callback URL the OAuth server must redirect to.
# If unset, the redirect_uri that the aggregator itself put into the authorization URL is used
# (the aggregator's REDIRECT_URL setting; chart default http://localhost:8000/callback).
EXPECTED_CALLBACK_URL="${EXPECTED_CALLBACK_URL:-}"
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

# http_request METHOD URL [extra curl args...]
# Sets HTTP_CODE (always 3 digits; "000" on network error/timeout) and CURL_RC.
# Headers go to $HDR_FILE, body to $BODY_FILE. Redirects are NOT followed (no -L).
# Extra args come after --max-time, so a caller can override the timeout.
http_request() {
    local method="$1" url="$2"
    shift 2
    : > "$HDR_FILE"
    : > "$BODY_FILE"
    CURL_RC=0
    HTTP_CODE=$(curl -s --max-time "$CURL_TIMEOUT" -X "$method" \
        -D "$HDR_FILE" -o "$BODY_FILE" -w '%{http_code}' "$@" "$url") || CURL_RC=$?
    if ! [[ "$HTTP_CODE" =~ ^[1-5][0-9][0-9]$ ]]; then
        HTTP_CODE="000"
    fi
    if [ "$HTTP_CODE" = "000" ]; then
        # Exit code only (e.g. 7 = connection refused, 28 = timeout); the URL is never printed
        log_warn "curl could not get an HTTP response (curl exit code $CURL_RC)"
    fi
}

# get_header NAME -> prints the value of header NAME from the LAST response in $HDR_FILE
# (case-insensitive; ignores earlier 1xx blocks; prints nothing if the header is absent)
get_header() {
    tr -d '\r' < "$HDR_FILE" | awk -v n="$1" '
        /^HTTP\// { v = ""; f = 0; next }
        {
            i = index($0, ":")
            if (i > 0 && !f && tolower(substr($0, 1, i - 1)) == tolower(n)) {
                v = substr($0, i + 1)
                sub(/^[ \t]+/, "", v); sub(/[ \t]+$/, "", v)
                f = 1
            }
        }
        END { if (f) print v }'
}

# query_param NAME QUERYSTRING -> prints the (still URL-encoded) value, or fails
query_param() {
    local name="$1" kv
    local -a parts
    IFS='&' read -ra parts <<< "$2"
    for kv in "${parts[@]}"; do
        case "$kv" in
            "$name="*) printf '%s' "${kv#*=}"; return 0 ;;
        esac
    done
    return 1
}

# urldecode STRING -> percent-decoded string ('+' becomes space, as in query strings).
# Only well-formed %XX sequences are decoded; a stray '%' or any backslash is kept literally and
# does not stop later sequences from being decoded. (%00 is dropped: Bash strings cannot hold NUL.)
urldecode() {
    local rest="${1//+/ }" out="" ch
    while [[ "$rest" == *%* ]]; do
        out+="${rest%%%*}"
        rest="${rest#*%}"
        if [[ "$rest" =~ ^([0-9A-Fa-f]{2}) ]]; then
            printf -v ch '\x'"${BASH_REMATCH[1]}"
            out+="$ch"
            rest="${rest:2}"
        else
            out+="%"
        fi
    done
    printf '%s' "$out$rest"
}

# normalize_url URL -> scheme://host[:port]/path
# Drops query/fragment, lowercases scheme and host, removes default ports. Fails if URL is not absolute.
normalize_url() {
    local u="${1%%#*}" scheme authority path
    u="${u%%\?*}"
    [[ "$u" =~ ^([A-Za-z][A-Za-z0-9+.-]*)://([^/]*)(/.*)?$ ]] || return 1
    scheme=$(printf '%s' "${BASH_REMATCH[1]}" | tr '[:upper:]' '[:lower:]')
    authority=$(printf '%s' "${BASH_REMATCH[2]}" | tr '[:upper:]' '[:lower:]')
    path="${BASH_REMATCH[3]}"
    case "$scheme" in
        http)  authority="${authority%:80}" ;;
        https) authority="${authority%:443}" ;;
    esac
    printf '%s://%s%s' "$scheme" "$authority" "${path:-/}"
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
    
    http_request GET "$AGGREGATOR_URL/healthz"
    if [ "$HTTP_CODE" = "200" ]; then
        log_pass "Aggregator is reachable"
    else
        log_fail "Aggregator is not reachable at $AGGREGATOR_URL (HTTP $HTTP_CODE)"
        return 1
    fi
}

# Test healthz endpoint
test_healthz() {
    log_info "Testing /healthz endpoint..."
    
    http_request GET "$AGGREGATOR_URL/healthz"
    status=$(jq -r '.status // empty' "$BODY_FILE" 2>/dev/null)
    
    if [ "$HTTP_CODE" = "200" ] && [ "$status" = "ok" ]; then
        log_pass "/healthz returned status: ok"
    else
        log_fail "/healthz unexpected response (HTTP $HTTP_CODE, status: $status)"
        return 1
    fi
}

# Test readyz endpoint
test_readyz() {
    log_info "Testing /readyz endpoint..."
    
    http_request GET "$AGGREGATOR_URL/readyz"
    http_code="$HTTP_CODE"
    status=$(jq -r '.status // empty' "$BODY_FILE" 2>/dev/null)
    openbao_status=$(jq -r '.openbao // empty' "$BODY_FILE" 2>/dev/null)
    
    if [ "$http_code" = "200" ] && [ "$status" = "ready" ] && [ "$openbao_status" = "up" ]; then
        log_pass "/readyz returned status: ready, openbao: up"
    elif [ "$http_code" = "503" ]; then
        log_fail "/readyz returned 503 - OpenBao dependency failure (status: $status, openbao: $openbao_status)"
        return 1
    else
        log_fail "/readyz unexpected response (HTTP $http_code, status: $status, openbao: $openbao_status)"
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
    http_request POST "$AGGREGATOR_URL/providers" \
        -H "Content-Type: application/json" \
        -d "{
            \"name\": \"$PROVIDER_NAME\",
            \"provider\": \"oidc\",
            \"client_id\": \"test-client-id\",
            \"client_secret\": \"test-client-secret\",
            \"scopes\": [\"openid\", \"profile\"],
            \"provider_options\": {
                \"issuer_url\": \"http://$MOCK_OAUTH_SERVICE.$NAMESPACE.svc.cluster.local:8080/default\"
            }
        }"
    
    if [ "$HTTP_CODE" = "201" ]; then
        if ! jq -e . "$BODY_FILE" > /dev/null 2>&1; then
            log_fail "Provider registration returned invalid JSON"
            PROVIDER_NAME=""
            return 1
        fi
        # Verify response doesn't contain client_secret
        if jq -e 'has("client_secret")' "$BODY_FILE" > /dev/null 2>&1; then
            log_fail "Provider registration response contains client_secret (should be redacted)"
            PROVIDER_NAME=""
            return 1
        fi
        log_pass "Provider registered successfully: $PROVIDER_NAME"
    else
        log_fail "Provider registration failed (HTTP $HTTP_CODE)"
        PROVIDER_NAME=""
        return 1
    fi
}

# Test OAuth connect flow
test_oauth_connect() {
    log_info "Testing OAuth connect for provider: $PROVIDER_NAME"

    http_request POST "$AGGREGATOR_URL/providers/$PROVIDER_NAME/users/test-user/connect"

    if [ "$HTTP_CODE" = "200" ]; then
        AUTH_URL=$(jq -r '.authorization_url // empty' "$BODY_FILE" 2>/dev/null)
        STATE=$(jq -r '.state // empty' "$BODY_FILE" 2>/dev/null)

        if [ -n "$AUTH_URL" ] && [ -n "$STATE" ]; then
            log_pass "OAuth connect successful, state received"
            # The authorization URL contains the state, so it is intentionally not logged
        else
            log_fail "OAuth connect response missing authorization_url or state"
            AUTH_URL=""
            STATE=""
            return 1
        fi
    else
        log_fail "OAuth connect failed (HTTP $HTTP_CODE)"
        AUTH_URL=""
        STATE=""
        return 1
    fi
}

# mock_oauth_is_ours: succeeds only if $MOCK_OAUTH_LOCAL_URL really is the mock OAuth2 server
# serving the "default" issuer (an /isalive 200 alone could come from any unrelated process).
mock_oauth_is_ours() {
    http_request GET "$MOCK_OAUTH_LOCAL_URL/default/.well-known/openid-configuration" --max-time 3 2>/dev/null
    [ "$HTTP_CODE" = "200" ] || return 1
    jq -e '(.issuer | type == "string") and (.issuer | endswith("/default"))
           and (.authorization_endpoint | type == "string")
           and (.authorization_endpoint | endswith("/default/authorize"))' \
        "$BODY_FILE" > /dev/null 2>&1
}

# Make the mock OAuth server reachable on localhost:8080.
# CI already starts a port-forward; reuse it only if it is verifiably the mock server,
# otherwise start our own. Never reuse an unrelated listener.
ensure_mock_oauth_reachable() {
    if mock_oauth_is_ours; then
        log_info "Mock OAuth server verified on $MOCK_OAUTH_LOCAL_URL (reusing existing port-forward)"
        return 0
    fi

    log_info "Setting up port-forward to svc/$MOCK_OAUTH_SERVICE in namespace $NAMESPACE..."
    kubectl port-forward -n "$NAMESPACE" svc/"$MOCK_OAUTH_SERVICE" 8080:8080 > "$TMP_DIR/pf.log" 2>&1 &
    PF_PID=$!

    local i
    for i in $(seq 1 15); do
        if mock_oauth_is_ours; then
            return 0
        fi
        if ! kill -0 "$PF_PID" 2>/dev/null; then
            PF_PID=""
            # Our port-forward died (e.g. port 8080 already taken). CI's own forward may have
            # come up in the meantime, so check once more before giving up.
            if mock_oauth_is_ours; then
                log_info "Mock OAuth server verified on $MOCK_OAUTH_LOCAL_URL (existing port-forward)"
                return 0
            fi
            log_fail "kubectl port-forward exited and localhost:8080 is not the mock OAuth server: $(head -n1 "$TMP_DIR/pf.log" | head -c 200)"
            return 1
        fi
        sleep 1
    done
    log_fail "localhost:8080 did not respond as the mock OAuth server (default issuer) after 15s"
    return 1
}

# Test callback with mock OAuth2
test_callback() {
    log_info "Testing OAuth callback..."

    if [ -z "$AUTH_URL" ]; then
        log_fail "Failed to get authorization URL"
        return 1
    fi

    ensure_mock_oauth_reachable || return 1

    # Replace cluster DNS with localhost in auth_url for host access
    host_auth_url=$(echo "$AUTH_URL" | sed "s|$MOCK_OAUTH_SERVICE.$NAMESPACE.svc.cluster.local:8080|localhost:8080|g")

    log_info "Requesting authorization code from mock OAuth2 server..."
    # No -L: we want the 302 itself, not the page it points to.
    http_request GET "$host_auth_url"

    if [ "$HTTP_CODE" != "302" ] && [ "$HTTP_CODE" != "301" ]; then
        log_fail "Authorization endpoint returned unexpected HTTP status: $HTTP_CODE (expected 301/302 redirect)"
        return 1
    fi

    redirect_url=$(get_header "location")
    if [ -z "$redirect_url" ]; then
        log_fail "No Location header received from authorize endpoint (HTTP $HTTP_CODE)"
        return 1
    fi

    # Split the redirect into path and query (drop any #fragment). Not logged: it holds code/state.
    redirect_no_frag="${redirect_url%%#*}"
    redirect_base="${redirect_no_frag%%\?*}"
    redirect_query=""
    case "$redirect_no_frag" in
        *\?*) redirect_query="${redirect_no_frag#*\?}" ;;
    esac

    # Verify the redirect goes to the callback URL the aggregator was configured with
    # (scheme, host, port and path). The query string is dropped, so nothing sensitive is printed.
    expected_callback="$EXPECTED_CALLBACK_URL"
    if [ -z "$expected_callback" ]; then
        case "$AUTH_URL" in
            *\?*) expected_callback=$(urldecode "$(query_param "redirect_uri" "${AUTH_URL#*\?}")") ;;
        esac
    fi
    if ! expected_norm=$(normalize_url "$expected_callback"); then
        log_fail "Cannot determine the expected callback URL (no redirect_uri in authorization URL; set EXPECTED_CALLBACK_URL)"
        return 1
    fi
    if ! redirect_norm=$(normalize_url "$redirect_base"); then
        log_fail "Redirect Location is not an absolute URL"
        return 1
    fi
    if [ "$redirect_norm" != "$expected_norm" ]; then
        log_fail "Redirect target mismatch: expected $expected_norm, got $redirect_norm"
        return 1
    fi
    # Exact path check: redirect_norm is scheme://authority/path, so strip scheme://authority
    redirect_path="/${redirect_norm#*://*/}"
    if [ "$redirect_path" != "/callback" ]; then
        log_fail "Redirect path is not exactly /callback (got: $redirect_path)"
        return 1
    fi

    # Extract authorization code and state (kept URL-encoded for forwarding)
    code=$(query_param "code" "$redirect_query")
    returned_state=$(query_param "state" "$redirect_query")

    if [ -z "$code" ]; then
        log_fail "Authorization code missing from redirect"
        return 1
    fi

    if [ -z "$returned_state" ]; then
        log_fail "State missing from redirect"
        return 1
    fi

    # Verify state matches (decode first so encoding differences don't cause false failures)
    if [ "$(urldecode "$returned_state")" != "$STATE" ]; then
        log_fail "State mismatch between connect response and authorize redirect"
        return 1
    fi
    
    log_pass "State verified, authorization code received"
    
    # Call aggregator callback with the authorization code.
    # code/state are already URL-encoded as received, so they are forwarded unchanged.
    log_info "Calling aggregator callback..."
    http_request GET "$AGGREGATOR_URL/callback?code=$code&state=$returned_state"
    
    if [ "$HTTP_CODE" = "200" ]; then
        callback_status=$(jq -r '.status // empty' "$BODY_FILE" 2>/dev/null)
        if [ "$callback_status" = "connected" ]; then
            log_pass "OAuth callback successful"
        else
            log_fail "OAuth callback returned unexpected status: ${callback_status:-<none>}"
            return 1
        fi
    else
        log_fail "OAuth callback failed (HTTP $HTTP_CODE)"
        return 1
    fi
}

# Test async token request
test_token_request() {
    log_info "Testing async token request for provider: $PROVIDER_NAME"
    
    http_request GET "$AGGREGATOR_URL/$PROVIDER_NAME/test-user"
    
    if [ "$HTTP_CODE" != "202" ]; then
        log_fail "Token request failed (HTTP $HTTP_CODE, expected 202)"
        REQUEST_ID=""
        return 1
    fi

    REQUEST_ID=$(jq -r '.request_id // empty' "$BODY_FILE" 2>/dev/null)
    if [ -z "$REQUEST_ID" ]; then
        log_fail "Token request response missing request_id"
        return 1
    fi

    # Validate Location header points to expected polling endpoint
    location=$(get_header "location")
    expected_location="/requests/$REQUEST_ID"
    if [ "$location" != "$expected_location" ]; then
        log_fail "Location header mismatch: expected $expected_location, got ${location:-<missing>}"
        REQUEST_ID=""
        return 1
    fi
    log_pass "Token request accepted (HTTP 202), request_id: $REQUEST_ID"
}

# Poll request status
poll_request_status() {
    log_info "Polling request status for request_id: $REQUEST_ID (timeout: ${POLL_TIMEOUT}s)..."
    
    elapsed=0
    while [ $elapsed -lt $POLL_TIMEOUT ]; do
        http_request GET "$AGGREGATOR_URL/requests/$REQUEST_ID"
        
        if [ "$HTTP_CODE" != "200" ]; then
            log_fail "Poll request returned HTTP $HTTP_CODE"
            return 1
        fi
        
        status=$(jq -r '.status // empty' "$BODY_FILE" 2>/dev/null)
        
        if [ "$status" = "succeeded" ]; then
            # Verify a non-empty token is present without ever printing it
            if jq -e '(.access_token | type == "string") and (.access_token | length > 0)' "$BODY_FILE" > /dev/null 2>&1; then
                log_pass "Request succeeded, token received (redacted)"
                return 0
            fi
            log_fail "Request succeeded but no token in response"
            return 1
        elif [ "$status" = "failed" ]; then
            error=$(jq -r '.error // empty' "$BODY_FILE" 2>/dev/null | head -c 200)
            log_fail "Request failed with error: $error"
            return 1
        fi
        
        sleep "$POLL_INTERVAL"
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