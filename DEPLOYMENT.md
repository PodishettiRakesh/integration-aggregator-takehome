# Deployment Guide

This document describes how to deploy the Integration Aggregator to an existing Kubernetes/Minikube infrastructure with an existing OpenBao installation.

## Existing Infrastructure Assumptions

The deployment assumes you have the following existing infrastructure:

- **Kubernetes**: Minikube running with Docker driver
- **OpenBao**: Installed in namespace `openbao`
  - Helm release: `openbao`
  - Pod: `openbao-0`
  - Service: `openbao` on port 8200
  - Version: 2.7.1
  - Running in development mode with root token: `root`
- **OAuth Plugin**: `oauthapp` v3.4.1 installed at `/openbao/plugins/oauthapp`
  - Registered SHA-256: `b9b5b2c752889ffe7d708522d6726752f8bba972769693cf5c84b7ce64a60ec5`
- **Secrets Engine**: `oauth2/` enabled using the `oauthapp` plugin
- **OpenBao Policy**: Policy named `aggregator` exists with permissions for `oauth2/*`
- **Child Token**: A Kubernetes Secret named `aggregator-openbao` exists in namespace `default` containing a child token with the `aggregator` policy and 24-hour TTL

**Important**: The normal deployment workflow never modifies, reinstalls, upgrades, or deletes the existing OpenBao installation.

## Application Namespace

The application deploys to namespace `aggregator` by default. This is configured in the Makefile:

```makefile
NAMESPACE ?= aggregator
```

## Secret Copying

The existing `aggregator-openbao` Secret is in the `default` namespace. You must copy it to the `aggregator` namespace before deploying the application.

### PowerShell Instructions

Run these commands in PowerShell to safely copy the Secret without exposing the token:

```powershell
# Extract the token value without displaying it
$token = kubectl get secret aggregator-openbao -n default -o jsonpath='{.data.OPENBAO_TOKEN}'

# Create the Secret in the aggregator namespace
kubectl create secret generic aggregator-openbao -n aggregator --from-literal=OPENBAO_TOKEN=$token --dry-run=client -o yaml | kubectl apply -n aggregator -f -

# Clean up the variable
Remove-Variable token
```

### Bash Instructions

```bash
# Extract the token value without displaying it
token=$(kubectl get secret aggregator-openbao -n default -o jsonpath='{.data.OPENBAO_TOKEN}')

# Create the Secret in the aggregator namespace
kubectl create secret generic aggregator-openbao -n aggregator --from-literal=OPENBAO_TOKEN=$token --dry-run=client -o yaml | kubectl apply -n aggregator -f -

# Clean up the variable
unset token
```

### Make Helper

The Makefile includes a helper target that displays the PowerShell commands:

```bash
make secret-copy
```

## Replacing an Expired Child Token

If the child token expires (24-hour TTL), you must create a new one without exposing it.

### Step 1: Create a new child token using the OpenBao CLI

Port-forward to OpenBao and create a new token:

```bash
# Port-forward to OpenBao
kubectl port-forward -n openbao svc/openbao 8200:8200 &
PF_PID=$!

# Set environment variables
export VAULT_ADDR="http://127.0.0.1:8200"
export VAULT_TOKEN="root"

# Create a new child token with the aggregator policy and 24-hour TTL
AGGREGATOR_TOKEN=$(vault token create -policy=aggregator -format=json -ttl=24h | jq -r '.auth.client_token')

# Kill the port-forward
kill $PF_PID
```

### Step 2: Update the Kubernetes Secret

**PowerShell**:

```powershell
# Update the Secret without displaying the token
kubectl create secret generic aggregator-openbao -n aggregator --from-literal=OPENBAO_TOKEN=$AGGREGATOR_TOKEN --dry-run=client -o yaml | kubectl apply -n aggregator -f -

# Clean up the variable
Remove-Variable AGGREGATOR_TOKEN
```

**Bash**:

```bash
# Update the Secret without displaying the token
kubectl create secret generic aggregator-openbao -n aggregator --from-literal=OPENBAO_TOKEN=$AGGREGATOR_TOKEN --dry-run=client -o yaml | kubectl apply -n aggregator -f -

# Clean up the variable
unset AGGREGATOR_TOKEN
```

## Normal Deployment Commands

### 1. Check required tools

```bash
make check-tools
```

Verifies that Docker, kubectl, Helm, and minikube are installed.

### 2. Verify Minikube is running

```bash
make minikube-start
```

Starts Minikube if it is not already running.

### 3. Verify existing OpenBao installation

```bash
make openbao-verify
```

Verifies that:
- OpenBao pod `openbao-0` exists in namespace `openbao` and is ready
- OpenBao service `openbao` exists in namespace `openbao`

This target does not modify the existing installation.

### 4. Ensure application namespace exists

```bash
make namespace-create
```

Creates the `aggregator` namespace if it does not exist.

### 5. Verify the Secret exists

```bash
make secret-verify
```

Verifies that the `aggregator-openbao` Secret exists in the `aggregator` namespace and contains the `OPENBAO_TOKEN` key.

If the Secret is missing, run `make secret-copy` for instructions on copying it from the `default` namespace.

### 6. Build and load the Docker image

```bash
make image-load
```

Builds the Docker image and loads it into Minikube.

### 7. Deploy the mock OAuth server (required for smoke test)

```bash
make mock-oauth-up
```

Deploys the mock OAuth2 server to the `aggregator` namespace for integration testing.

### 8. Deploy the application

```bash
make deploy
```

Deploys the Integration Aggregator using the Helm chart to the `aggregator` namespace.

### Full deployment (all steps)

```bash
make up
```

Runs all the above steps in order:
1. check-tools
2. minikube-start
3. openbao-verify
4. namespace-create
5. secret-verify
6. image-load
7. mock-oauth-up
8. deploy

## Smoke Test Execution

The smoke test verifies the end-to-end OAuth flow with the mock OAuth server.

```bash
make smoke
```

The smoke test:
- Requires a Bash-compatible environment (does not run natively in PowerShell)
- Uses the `aggregator` namespace by default
- Tests health endpoints
- Registers a mock OIDC provider
- Tests OAuth connect flow
- Tests callback with authorization code
- Tests async token request
- Polls for request status

**Note**: The smoke test is separate from deployment. Run it after `make up` completes successfully.

To run the smoke test with a custom aggregator URL:

```bash
AGGREGATOR_URL=http://localhost:8000 make smoke
```

## Accessing the Application

To access the application locally via port-forward:

```bash
kubectl port-forward -n aggregator svc/aggregator 8000:80
```

The application will be available at `http://localhost:8000`.

## Safe Application Cleanup

To remove the application and mock OAuth server while preserving OpenBao:

```bash
make down
```

This removes:
- The `aggregator` Helm release
- The `mock-oauth` Helm release
- The `aggregator-openbao` Secret in the `aggregator` namespace

This **preserves**:
- The `aggregator` namespace
- The `openbao` namespace
- The OpenBao Helm release
- The OpenBao plugin, policy, and secrets engine

### Deleting the namespace

To delete the entire `aggregator` namespace (WARNING: deletes all resources in the namespace):

```bash
make namespace-delete
```

This requires confirmation before proceeding.

## OpenBao Preservation

The normal deployment workflow never modifies the existing OpenBao installation. The following are preserved:

- OpenBao namespace: `openbao`
- OpenBao Helm release: `openbao`
- OpenBao pod: `openbao-0`
- OpenBao service: `openbao`
- OAuth plugin: `oauthapp` v3.4.1
- Plugin registration and SHA-256
- Secrets engine: `oauth2/`
- OpenBao policy: `aggregator`

### Fresh Environment Installation

If you need to install OpenBao from scratch (for a fresh environment without an existing installation), use the optional target:

```bash
make openbao-install-fresh
```

This target:
- Requires explicit confirmation before proceeding
- Installs OpenBao with the oauthapp plugin v3.4.1
- Creates the aggregator policy
- Creates a child token

**Do not run this if you have an existing OpenBao installation you want to preserve.**

### Dangerous OpenBao Teardown

To completely remove the OpenBao installation (DANGEROUS):

```bash
make infra-down
```

This:
- Requires explicit confirmation before proceeding
- Uninstalls the OpenBao Helm release
- Deletes the `openbao` namespace
- Removes local token files

**Do not run this if you have an existing OpenBao installation you want to preserve.**

## Troubleshooting

### Secret verification fails

If `make secret-verify` fails:

1. Check if the Secret exists in the `default` namespace:
   ```bash
   kubectl get secret aggregator-openbao -n default
   ```

2. Copy the Secret to the `aggregator` namespace using the instructions in the "Secret Copying" section.

3. Run `make secret-verify` again.

### OpenBao verification fails

If `make openbao-verify` fails:

1. Check if OpenBao is running:
   ```bash
   kubectl get pod openbao-0 -n openbao
   kubectl get svc openbao -n openbao
   ```

2. If OpenBao is not installed, you have two options:
   - Install it manually following the OpenBao documentation
   - Use `make openbao-install-fresh` for a fresh installation (only if no existing installation exists)

### Smoke test fails

If `make smoke` fails:

1. Ensure the application is deployed:
   ```bash
   kubectl get pod -n aggregator
   kubectl get svc -n aggregator
   ```

2. Ensure the mock OAuth server is deployed:
   ```bash
   kubectl get pod -n aggregator -l app.kubernetes.io/name=mock-oauth2-server
   ```

3. Check application logs:
   ```bash
   kubectl logs -n aggregator deployment/aggregator
   ```

4. Port-forward to the application and test endpoints manually:
   ```bash
   kubectl port-forward -n aggregator svc/aggregator 8000:80
   curl http://localhost:8000/healthz
   curl http://localhost:8000/readyz
   ```
