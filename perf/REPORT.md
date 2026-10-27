# Performance Test Report

**Test Date:** October 27, 2026
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

*These are aggregate results from the GitHub Actions performance report generated on October 27, 2026.*

### Latency

| Metric | Value |
|--------|-------|
| Average latency | 1.35 ms |
| p50 latency | 1.08 ms |
| p95 latency | 2.33 ms |
| Maximum latency | 74.87 ms |

### Throughput

| Metric | Value |
|--------|-------|
| Total requests | 1,831 |
| Requests per second | 30.25 |

### Reliability

| Metric | Value |
|--------|-------|
| Failed-request rate | 0.00% |
| Failure samples | 0 |

## Analysis

The performance test completed successfully with zero failures across 1,831 HTTP requests. The failed-request rate was 0.00%, indicating that all token retrieval operations completed without errors during the test run. The p95 latency of 2.33 ms demonstrates that 95% of requests completed within approximately 2.33 milliseconds, which is well below the 5-second threshold configured in the k6 test script. The maximum observed latency of 74.87 ms represents an outlier; the average latency of 1.35 ms and p50 of 1.08 ms show that typical response times are significantly lower.

## Notes

- Performance test runs in CI after successful integration test on main branch pushes
- Results are uploaded as GitHub Actions artifacts as `performance-report`
- To run locally: `k6 run perf/token_retrieval.js --out json=perf-results.json`
- Requires deployed environment with mock OAuth server
- The test simulates token retrieval from pre-stored credentials; it does not exercise the full OAuth consent flow
