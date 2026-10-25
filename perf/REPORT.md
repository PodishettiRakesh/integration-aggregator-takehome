# Performance Test Report

**Test Date:** [Fill in after running]
**Test Duration:** 60 seconds
**Tool:** k6 v0.54.0
**Endpoint:** Token retrieval (async flow with polling)

## Test Configuration

### Workload
- **Target:** `GET /{provider}/{user}` endpoint
- **Flow:** Async token request → poll `/requests/{id}` until completion
- **Stages:**
  - 10s ramp to 10 concurrent users
  - 10s hold at 10 users
  - 10s ramp to 20 concurrent users
  - 10s hold at 20 users
  - 10s ramp to 50 concurrent users
  - 10s hold at 50 users

### Assumptions
- Mock OIDC provider (navikt/mock-oauth2-server) is deployed
- Provider credentials are pre-stored in OpenBao
- Performance test focuses on the token retrieval path, not the full OAuth consent flow

## Results

### Latency

| Metric | Value |
|--------|-------|
| p50 latency | [ms - fill from CI artifact] |
| p95 latency | [ms - fill from CI artifact] |
| p99 latency | [ms - fill from CI artifact] |

### Throughput

| Metric | Value |
|--------|-------|
| Total requests | [fill from CI artifact] |
| Requests per second | [fill from CI artifact] |
| Success rate | [% - fill from CI artifact] |

### Concurrency Levels

| Concurrency | Avg Latency (ms) | Throughput (req/s) | Error Rate (%) |
|-------------|------------------|-------------------|----------------|
| 10 users | [fill from CI artifact] | [fill from CI artifact] | [fill from CI artifact] |
| 20 users | [fill from CI artifact] | [fill from CI artifact] | [fill from CI artifact] |
| 50 users | [fill from CI artifact] | [fill from CI artifact] | [fill from CI artifact] |

## Analysis

[Fill in analysis after running the test]

## Notes

- Performance test runs in CI after successful integration test
- Results are uploaded as GitHub Actions artifacts
- To run locally: `k6 run perf/token_retrieval.js --out json=perf-results.json`
- Requires deployed environment with mock OAuth server
