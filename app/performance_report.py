"""Performance report generation module for k6 test results."""

import json
from datetime import UTC, datetime
from math import ceil
from pathlib import Path


def parse_k6_json(input_file: str) -> dict[str, list[float]]:
    """Parse k6 JSON output and extract metric values.

    Args:
        input_file: Path to the k6 JSON output file.

    Returns:
        Dictionary mapping metric names to lists of values.
    """
    metrics: dict[str, list[float]] = {}
    parse_errors = 0

    with open(input_file, encoding="utf-8") as f:
        for _line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue

            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                parse_errors += 1
                continue

            if not isinstance(item, dict) or item.get("type") != "Point":
                continue

            # k6 commonly represents "metric" as a string, such as
            # "http_req_duration". Also support a dictionary representation.
            metric = item.get("metric")

            if isinstance(metric, str):
                metric_name = metric
            elif isinstance(metric, dict):
                metric_name = metric.get("name", "")
            else:
                continue

            point_data = item.get("data")
            if not isinstance(point_data, dict):
                continue

            value = point_data.get("value")
            if isinstance(value, int | float):
                metrics.setdefault(metric_name, []).append(value)

    return metrics


def percentile(values: list[float], p: float) -> float | None:
    """Calculate a percentile using the nearest-rank method.

    Args:
        values: List of numeric values.
        p: Percentile to calculate (0.0 to 1.0).

    Returns:
        The percentile value, or None if values is empty.
    """
    if not values:
        return None

    ordered = sorted(values)
    index = max(0, ceil(p * len(ordered)) - 1)
    return ordered[index]


def generate_report(
    input_file: str,
    output_file: str,
) -> None:
    """Generate a performance report from k6 JSON output.

    Args:
        input_file: Path to the k6 JSON output file.
        output_file: Path where the report will be written.
    """
    metrics = {}
    request_timestamps = []
    parse_errors = 0

    # Read the k6 JSON output.
    with open(input_file, encoding="utf-8") as f:
        for _line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue

            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                parse_errors += 1
                continue

            if not isinstance(item, dict) or item.get("type") != "Point":
                continue

            # k6 commonly represents "metric" as a string, such as
            # "http_req_duration". Also support a dictionary representation.
            metric = item.get("metric")

            if isinstance(metric, str):
                metric_name = metric
            elif isinstance(metric, dict):
                metric_name = metric.get("name", "")
            else:
                continue

            point_data = item.get("data")
            if not isinstance(point_data, dict):
                continue

            value = point_data.get("value")
            if isinstance(value, int | float):
                metrics.setdefault(metric_name, []).append(value)

            # Capture timestamps of HTTP requests for throughput estimation.
            if metric_name == "http_reqs":
                timestamp = point_data.get("time")
                if isinstance(timestamp, str):
                    try:
                        parsed_time = datetime.fromisoformat(
                            timestamp.replace("Z", "+00:00")
                        )
                        request_timestamps.append(parsed_time)
                    except ValueError:
                        pass

    report = [
        "# Performance Test Report\n\n",
        f"Generated: {datetime.now(UTC).isoformat()}\n\n",
        "## Token Retrieval Performance\n\n",
    ]

    # Latency: k6 reports http_req_duration in milliseconds already.
    durations = metrics.get("http_req_duration", [])

    report.append("### Latency\n\n")

    if durations:
        report.append(f"- Samples: {len(durations)}\n")
        report.append(f"- Average: {sum(durations) / len(durations):.2f} ms\n")
        report.append(f"- p50: {percentile(durations, 0.50):.2f} ms\n")
        report.append(f"- p95: {percentile(durations, 0.95):.2f} ms\n")
        report.append(f"- Maximum: {max(durations):.2f} ms\n\n")
    else:
        report.append("- No HTTP request duration samples were found.\n\n")

    # Throughput
    report.append("### Throughput\n\n")

    request_points = metrics.get("http_reqs", [])
    total_requests = sum(request_points)

    report.append(f"- Total HTTP requests: {total_requests:.0f}\n")

    if len(request_timestamps) >= 2:
        elapsed_seconds = (
            max(request_timestamps) - min(request_timestamps)
        ).total_seconds()

        if elapsed_seconds > 0:
            report.append(
                f"- Approximate throughput: "
                f"{total_requests / elapsed_seconds:.2f} requests/second\n\n"
            )
        else:
            report.append(
                "- Throughput unavailable: request timestamps have no measurable span.\n\n"
            )
    else:
        report.append(
            "- Throughput unavailable: insufficient request timestamps.\n\n"
        )

    # HTTP failures
    failed_requests = metrics.get("http_req_failed", [])

    report.append("### HTTP Reliability\n\n")

    if failed_requests:
        failed_count = sum(failed_requests)
        total_requests = len(failed_requests)
        if total_requests > 0:
            report.append(f"- Failed-request rate: {failed_count / total_requests * 100:.2f}%\n")
        else:
            report.append("- Failed-request rate: N/A (no requests)\n")
        report.append(f"- Failure samples: {failed_count}\n\n")
    else:
        report.append("- No HTTP failure-rate samples were found.\n\n")

    if parse_errors:
        report.append("### Data Quality\n\n")
        report.append(f"- Invalid JSON lines skipped: {parse_errors}\n\n")

    if not metrics:
        report.append(
            "**WARNING:** No k6 metric points were found. "
            "Check whether the performance test completed successfully.\n"
        )

    Path(output_file).write_text("".join(report), encoding="utf-8")

    print(f"Report written to {output_file}")
    print("Metrics found:", ", ".join(sorted(metrics.keys())) or "none")
    print("HTTP request points:", len(request_points))
    print("Latency samples:", len(durations))
    print("Invalid JSON lines skipped:", parse_errors)
