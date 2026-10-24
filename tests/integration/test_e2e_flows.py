"""End-to-end integration tests covering the complete OAuth flow."""

from fastapi.testclient import TestClient


def test_full_oauth_flow_happy_path(client: TestClient) -> None:
    """Test the complete happy path: register → connect → callback → token request → poll."""
    # 1. Register a GitHub provider
    provider_response = client.post(
        "/providers",
        json={
            "name": "github",
            "provider": "github",
            "client_id": "test_github_client_id",
            "client_secret": "test_github_secret",
            "scopes": ["read:user", "repo"],
        },
    )
    assert provider_response.status_code == 201
    provider_data = provider_response.json()
    assert provider_data["name"] == "github"
    assert provider_data["client_id"] == "test_github_client_id"
    assert "client_secret" not in provider_data  # Secret never exposed

    # 2. Connect user to the provider
    connect_response = client.post("/providers/github/users/alice/connect")
    assert connect_response.status_code == 200
    connect_data = connect_response.json()
    assert "authorization_url" in connect_data
    assert "state" in connect_data
    state = connect_data["state"]
    assert len(state) > 0

    # 3. Simulate IdP callback with authorization code
    callback_response = client.get(
        "/callback", params={"code": "mock_auth_code_123", "state": state}
    )
    assert callback_response.status_code == 200
    callback_data = callback_response.json()
    assert callback_data["status"] == "connected"
    assert callback_data["provider"] == "github"
    assert callback_data["user_id"] == "alice"

    # 4. Request token (async, returns 202 immediately)
    token_request_response = client.get("/github/alice")
    assert token_request_response.status_code == 202
    assert "Location" in token_request_response.headers
    location = token_request_response.headers["Location"]
    assert location.startswith("/requests/")
    request_id = location.split("/")[-1]

    request_data = token_request_response.json()
    assert request_data["request_id"] == request_id

    # 5. Poll for token result
    poll_response = client.get(f"/requests/{request_id}")
    assert poll_response.status_code == 200
    poll_data = poll_response.json()
    assert poll_data["request_id"] == request_id
    assert poll_data["status"] == "succeeded"
    assert poll_data.get("access_token") == "fake-access-github_alice"
    assert poll_data.get("token_type") == "Bearer"
    assert poll_data.get("expire_time") is not None
    assert poll_data.get("error") is None


def test_full_oauth_flow_with_oidc_provider(client: TestClient) -> None:
    """Test complete flow with an OIDC provider (requires issuer_url)."""
    # 1. Register OIDC provider
    provider_response = client.post(
        "/providers",
        json={
            "name": "my-oidc",
            "provider": "oidc",
            "client_id": "oidc_client_id",
            "client_secret": "oidc_secret",
            "scopes": ["openid", "profile"],
            "provider_options": {"issuer_url": "https://accounts.example.com"},
        },
    )
    assert provider_response.status_code == 201
    provider_data = provider_response.json()
    assert provider_data["name"] == "my-oidc"
    assert provider_data["provider_options"]["issuer_url"] == "https://accounts.example.com"

    # 2. Connect and complete flow
    connect_response = client.post("/providers/my-oidc/users/bob/connect")
    assert connect_response.status_code == 200
    state = connect_response.json()["state"]

    callback_response = client.get(
        "/callback", params={"code": "oidc_auth_code", "state": state}
    )
    assert callback_response.status_code == 200
    assert callback_response.json()["user_id"] == "bob"

    # 3. Request and poll token
    token_request_response = client.get("/my-oidc/bob")
    assert token_request_response.status_code == 202
    request_id = token_request_response.headers["Location"].split("/")[-1]

    poll_response = client.get(f"/requests/{request_id}")
    assert poll_response.status_code == 200
    poll_data = poll_response.json()
    assert poll_data["status"] == "succeeded"
    assert poll_data["access_token"] == "fake-access-my-oidc_bob"


def test_state_replay_prevention(client: TestClient) -> None:
    """Test that OAuth state cannot be reused (replay attack prevention)."""
    # Register provider
    client.post(
        "/providers",
        json={
            "name": "github",
            "provider": "github",
            "client_id": "client_id",
            "client_secret": "secret",
        },
    )

    # Connect and get state
    connect_response = client.post("/providers/github/users/eve/connect")
    state = connect_response.json()["state"]

    # First callback succeeds
    first_callback = client.get(
        "/callback", params={"code": "auth_code", "state": state}
    )
    assert first_callback.status_code == 200

    # Second callback with same state should fail
    second_callback = client.get(
        "/callback", params={"code": "auth_code_2", "state": state}
    )
    assert second_callback.status_code == 400


def test_callback_with_invalid_state(client: TestClient) -> None:
    """Test callback with a state that was never generated."""
    client.post(
        "/providers",
        json={
            "name": "github",
            "provider": "github",
            "client_id": "client_id",
            "client_secret": "secret",
        },
    )

    # Callback with fake state
    response = client.get(
        "/callback", params={"code": "auth_code", "state": "fake_state_xyz"}
    )
    assert response.status_code == 400


def test_callback_missing_code_parameter(client: TestClient) -> None:
    """Test callback fails when code parameter is missing."""
    client.post(
        "/providers",
        json={
            "name": "github",
            "provider": "github",
            "client_id": "client_id",
            "client_secret": "secret",
        },
    )

    connect_response = client.post("/providers/github/users/charlie/connect")
    state = connect_response.json()["state"]

    # Callback without code
    response = client.get("/callback", params={"state": state})
    assert response.status_code == 400


def test_callback_with_idp_error(client: TestClient) -> None:
    """Test callback when IdP returns an error."""
    client.post(
        "/providers",
        json={
            "name": "github",
            "provider": "github",
            "client_id": "client_id",
            "client_secret": "secret",
        },
    )

    connect_response = client.post("/providers/github/users/dave/connect")
    state = connect_response.json()["state"]

    # IdP returns error
    response = client.get(
        "/callback", params={"error": "access_denied", "state": state}
    )
    assert response.status_code == 400


def test_unknown_provider_404(client: TestClient) -> None:
    """Test that unknown provider returns 404."""
    # Connect to non-existent provider
    response = client.post("/providers/nonexistent/users/user/connect")
    assert response.status_code == 404

    # Request token for non-existent provider
    response = client.get("/nonexistent/user")
    assert response.status_code == 404


def test_duplicate_provider_registration_409(client: TestClient) -> None:
    """Test that registering the same provider twice returns 409."""
    # First registration
    first_response = client.post(
        "/providers",
        json={
            "name": "github",
            "provider": "github",
            "client_id": "client_id",
            "client_secret": "secret",
        },
    )
    assert first_response.status_code == 201

    # Duplicate registration
    second_response = client.post(
        "/providers",
        json={
            "name": "github",
            "provider": "github",
            "client_id": "another_id",
            "client_secret": "another_secret",
        },
    )
    assert second_response.status_code == 409


def test_oidc_without_issuer_url_validation(client: TestClient) -> None:
    """Test that OIDC provider requires issuer_url in provider_options."""
    response = client.post(
        "/providers",
        json={
            "name": "bad-oidc",
            "provider": "oidc",
            "client_id": "client_id",
            "client_secret": "secret",
            # Missing issuer_url
        },
    )
    assert response.status_code == 422


def test_token_request_before_callback(client: TestClient) -> None:
    """Test requesting token before completing OAuth flow returns failed status."""
    client.post(
        "/providers",
        json={
            "name": "github",
            "provider": "github",
            "client_id": "client_id",
            "client_secret": "secret",
        },
    )

    # Request token without connecting first
    token_request_response = client.get("/github/frank")
    assert token_request_response.status_code == 202
    request_id = token_request_response.headers["Location"].split("/")[-1]

    # Poll should show failed status
    poll_response = client.get(f"/requests/{request_id}")
    assert poll_response.status_code == 200
    poll_data = poll_response.json()
    assert poll_data["status"] == "failed"
    assert poll_data.get("error") is not None
    assert poll_data.get("access_token") is None


def test_unknown_request_id_404(client: TestClient) -> None:
    """Test that polling non-existent request returns 404."""
    response = client.get("/requests/nonexistent-request-id")
    assert response.status_code == 404


def test_multiple_users_same_provider(client: TestClient) -> None:
    """Test that multiple users can connect to the same provider independently."""
    client.post(
        "/providers",
        json={
            "name": "github",
            "provider": "github",
            "client_id": "client_id",
            "client_secret": "secret",
        },
    )

    # User 1 connects
    connect1 = client.post("/providers/github/users/user1/connect")
    state1 = connect1.json()["state"]
    client.get("/callback", params={"code": "code1", "state": state1})

    # User 2 connects
    connect2 = client.post("/providers/github/users/user2/connect")
    state2 = connect2.json()["state"]
    assert state2 != state1  # States should be different
    client.get("/callback", params={"code": "code2", "state": state2})

    # Both users can get tokens
    req1 = client.get("/github/user1")
    req2 = client.get("/github/user2")
    assert req1.status_code == 202
    assert req2.status_code == 202

    id1 = req1.headers["Location"].split("/")[-1]
    id2 = req2.headers["Location"].split("/")[-1]
    assert id1 != id2

    # Poll both
    poll1 = client.get(f"/requests/{id1}")
    poll2 = client.get(f"/requests/{id2}")
    assert poll1.json()["access_token"] == "fake-access-github_user1"
    assert poll2.json()["access_token"] == "fake-access-github_user2"


def test_multiple_providers_same_user(client: TestClient) -> None:
    """Test that one user can connect to multiple providers."""
    # Register two providers
    client.post(
        "/providers",
        json={
            "name": "github",
            "provider": "github",
            "client_id": "github_id",
            "client_secret": "github_secret",
        },
    )
    client.post(
        "/providers",
        json={
            "name": "gitlab",
            "provider": "gitlab",
            "client_id": "gitlab_id",
            "client_secret": "gitlab_secret",
        },
    )

    # Connect to GitHub
    gh_connect = client.post("/providers/github/users/shared_user/connect")
    gh_state = gh_connect.json()["state"]
    client.get("/callback", params={"code": "gh_code", "state": gh_state})

    # Connect to GitLab
    gl_connect = client.post("/providers/gitlab/users/shared_user/connect")
    gl_state = gl_connect.json()["state"]
    client.get("/callback", params={"code": "gl_code", "state": gl_state})

    # Get tokens from both
    gh_req = client.get("/github/shared_user")
    gl_req = client.get("/gitlab/shared_user")
    assert gh_req.status_code == 202
    assert gl_req.status_code == 202

    gh_id = gh_req.headers["Location"].split("/")[-1]
    gl_id = gl_req.headers["Location"].split("/")[-1]

    gh_poll = client.get(f"/requests/{gh_id}")
    gl_poll = client.get(f"/requests/{gl_id}")
    assert gh_poll.json()["access_token"] == "fake-access-github_shared_user"
    assert gl_poll.json()["access_token"] == "fake-access-gitlab_shared_user"


def test_health_endpoints(client: TestClient) -> None:
    """Test health and readiness endpoints."""
    # Healthz should always return 200
    health_response = client.get("/healthz")
    assert health_response.status_code == 200

    # Readyz should return 200 (fake OpenBao is healthy)
    ready_response = client.get("/readyz")
    assert ready_response.status_code == 200
