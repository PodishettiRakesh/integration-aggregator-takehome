# GitHub OAuth Flow Transcript

This document demonstrates the manual OAuth authorization-code flow with GitHub using the Integration Aggregator. All sensitive values are placeholders - replace them with your actual values when testing.

**Important**: Never commit real client IDs, secrets, authorization codes, states, or access tokens to Git.

## Prerequisites

1. **GitHub OAuth App**: Create at https://github.com/settings/developers
   - Application name: Your app name
   - Homepage URL: `http://localhost:8000`
   - Authorization callback URL: `http://localhost:8000/callback`
   - Note the Client ID and generate a Client Secret

2. **Integration Aggregator**: Deployed and accessible at `http://localhost:8000`

3. **OpenBao**: Running with oauthapp plugin enabled

## Step 1: Register GitHub Provider

Register GitHub as an OAuth provider with the aggregator. The client secret is stored in OpenBao only.

### curl

```bash
curl -X POST http://localhost:8000/providers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "github",
    "provider": "github",
    "client_id": "<YOUR_GITHUB_CLIENT_ID>",
    "client_secret": "<YOUR_GITHUB_CLIENT_SECRET>",
    "scopes": ["read:user"]
  }'
```

### PowerShell

```powershell
$body = @{
    name = "github"
    provider = "github"
    client_id = "<YOUR_GITHUB_CLIENT_ID>"
    client_secret = "<YOUR_GITHUB_CLIENT_SECRET>"
    scopes = @("read:user")
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/providers" `
    -Method Post `
    -ContentType "application/json" `
    -Body $body
```

**Expected Response** (201 Created):
```json
{
  "name": "github",
  "provider": "github",
  "client_id": "<YOUR_GITHUB_CLIENT_ID>",
  "scopes": ["read:user"]
}
```

**Note**: The response does not include the `client_secret`.

## Step 2: Initiate OAuth Authorization

Start a connection for a user. This returns the authorization URL and a state value.

### curl

```bash
curl -X POST http://localhost:8000/providers/github/users/alice/connect
```

### PowerShell

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/providers/github/users/alice/connect" `
    -Method Post
```

**Expected Response** (200 OK):
```json
{
  "authorization_url": "https://github.com/login/oauth/authorize?client_id=<YOUR_GITHUB_CLIENT_ID>&state=<RANDOM_STATE>&redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fcallback&scope=read%3Auser",
  "state": "<RANDOM_STATE>"
}
```

**Save the `state` value** - you will need it to verify the callback.

## Step 3: Complete Authorization in Browser

This step requires opening a browser - it cannot be done with curl or PowerShell alone.

1. Copy the `authorization_url` from the previous response
2. Open it in a web browser
3. You will be redirected to GitHub's authorization page
4. Log in to GitHub (if not already logged in)
5. Review the permissions requested by the application
6. Click "Authorize" to grant access

After authorizing, GitHub will redirect to your callback URL:
```
http://localhost:8000/callback?code=<AUTHORIZATION_CODE>&state=<RANDOM_STATE>
```

**Note**: The aggregator service handles this callback automatically. You should see a success response in the browser.

## Step 4: Verify Callback Success

The callback endpoint returns a success response. You can verify this manually.

### curl

```bash
# The callback is handled automatically when GitHub redirects
# To test manually, you would need the actual code and state from the redirect
curl "http://localhost:8000/callback?code=<AUTHORIZATION_CODE>&state=<RANDOM_STATE>"
```

### PowerShell

```powershell
# The callback is handled automatically when GitHub redirects
# To test manually, you would need the actual code and state from the redirect
Invoke-RestMethod -Uri "http://localhost:8000/callback?code=<AUTHORIZATION_CODE>&state=<RANDOM_STATE>"
```

**Expected Response** (200 OK):
```json
{
  "status": "connected",
  "provider": "github",
  "user_id": "alice"
}
```

## Step 5: Request Token Retrieval (Async)

Request the current access token for the user. This returns immediately with a request ID.

### curl

```bash
curl -i http://localhost:8000/github/alice
```

### PowerShell

```powershell
$response = Invoke-WebRequest -Uri "http://localhost:8000/github/alice" `
    -Method Get
$response.Headers["Location"]
$response.Content | ConvertFrom-Json
```

**Expected Response** (202 Accepted):
```
HTTP/1.1 202 Accepted
Location: /requests/<REQUEST_ID>

{
  "request_id": "<REQUEST_ID>"
}
```

**Save the `request_id`** - you will need it to poll for the result.

## Step 6: Poll Request Status

Poll the request status endpoint until the request is complete. The background worker retrieves the token from OpenBao.

### curl

```bash
curl http://localhost:8000/requests/<REQUEST_ID>
```

### PowerShell

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/requests/<REQUEST_ID>" `
    -Method Get
```

**Response while pending** (200 OK):
```json
{
  "request_id": "<REQUEST_ID>",
  "status": "pending"
}
```

**Response while in progress** (200 OK):
```json
{
  "request_id": "<REQUEST_ID>",
  "status": "in_progress"
}
```

**Response on success** (200 OK):
```json
{
  "request_id": "<REQUEST_ID>",
  "status": "succeeded",
  "access_token": "gho_<ACCESS_TOKEN>",
  "token_type": "Bearer",
  "expire_time": "2024-01-01T00:00:00Z"
}
```

**Response on failure** (200 OK):
```json
{
  "request_id": "<REQUEST_ID>",
  "status": "failed",
  "error": "Credentials not found in OpenBao"
}
```

**Note**: The access token is only returned when `status` is `succeeded`. The oauthapp plugin handles token refresh automatically.

## Step 7: Use the Access Token

Use the retrieved access token to authenticate with GitHub's API.

### curl

```bash
curl -H "Authorization: Bearer <ACCESS_TOKEN>" \
  https://api.github.com/user
```

### PowerShell

```powershell
$headers = @{
    Authorization = "Bearer <ACCESS_TOKEN>"
}
Invoke-RestMethod -Uri "https://api.github.com/user" `
    -Headers $headers
```

**Expected Response** (200 OK):
```json
{
  "login": "your-github-username",
  "id": 12345678,
  "node_id": "MDQ6VXNlcjEyMzQ1Njc4",
  "avatar_url": "https://avatars.githubusercontent.com/u/12345678?v=4",
  ...
}
```

## Complete Flow Summary

1. **Register provider**: `POST /providers` with client ID and secret
2. **Initiate authorization**: `POST /providers/{provider}/users/{user}/connect` → get auth URL and state
3. **Complete consent**: Open auth URL in browser, authorize with GitHub
4. **Callback**: GitHub redirects to `/callback?code&state` (handled automatically)
5. **Request token**: `GET /{provider}/{user}` → returns 202 with request ID
6. **Poll status**: `GET /requests/{id}` until status is `succeeded` or `failed`
7. **Use token**: Access token returned in successful response

## Security Notes

- **State validation**: The service validates the state parameter to prevent CSRF attacks
- **Consume-once**: Each state value can only be used once
- **TTL**: State values expire after 10 minutes (configurable)
- **Secret storage**: Client secrets are stored in OpenBao, never in the application
- **Token storage**: Access tokens are stored in OpenBao, the service only retrieves them on demand
- **No logging**: Tokens, secrets, and authorization codes are never logged

## Troubleshooting

### Invalid State Error

If the callback returns an error about invalid state:
- The state may have expired (TTL 10 minutes)
- The state may have been replayed (consume-once protection)
- Ensure you're using the state value from the most recent connect request

### Credentials Not Found

If token retrieval fails with "Credentials not found":
- Ensure the OAuth callback completed successfully
- Check that the callback returned status "connected"
- Verify the provider name and user ID match

### Token Expired

If the access token is expired:
- The oauthapp plugin automatically refreshes tokens
- Request a new token retrieval - the plugin will refresh if needed
- Check the `expire_time` in the token response

## Additional Resources

- **GitHub OAuth Docs**: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps
- **Integration Aggregator API**: See `docs/USAGE.md` for complete API documentation
- **DESIGN.md**: Architecture and security decisions
