"""Tests for performance report generation."""

import tempfile
from pathlib import Path

from app.performance_report import generate_report, parse_k6_json, percentile


def test_percentile():
    """Test percentile calculation."""
    assert percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert percentile([1, 2, 3, 4, 5], 0.95) == 5
    assert percentile([10, 20, 30], 0.5) == 20
    assert percentile([], 0.5) is None


def test_parse_k6_json_simple():
    """Test parsing simple k6 JSON output."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write('{"type":"Point","metric":"http_req_duration","data":{"value":100}}\n')
        f.write('{"type":"Point","metric":"http_req_duration","data":{"value":200}}\n')
        f.write('{"type":"Point","metric":"http_req_failed","data":{"value":0}}\n')
        f.write('{"type":"Point","metric":"http_req_failed","data":{"value":1}}\n')
        temp_file = f.name

    try:
        metrics = parse_k6_json(temp_file)
        assert metrics["http_req_duration"] == [100, 200]
        assert metrics["http_req_failed"] == [0, 1]
    finally:
        Path(temp_file).unlink()


def test_parse_k6_json_with_invalid_lines():
    """Test parsing k6 JSON with invalid lines."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write('{"type":"Point","metric":"http_req_duration","data":{"value":100}}\n')
        f.write('invalid json\n')
        f.write('{"type":"Point","metric":"http_req_duration","data":{"value":200}}\n')
        temp_file = f.name

    try:
        metrics = parse_k6_json(temp_file)
        assert metrics["http_req_duration"] == [100, 200]
    finally:
        Path(temp_file).unlink()


def test_generate_report_all_success():
    """Test report generation with all successful requests."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        # 100 successful requests (http_req_failed = 0)
        for _ in range(100):
            f.write('{"type":"Point","metric":"http_req_duration","data":{"value":100,"time":"2024-01-01T00:00:00Z"}}\n')
            f.write('{"type":"Point","metric":"http_reqs","data":{"value":1,"time":"2024-01-01T00:00:00Z"}}\n')
            f.write('{"type":"Point","metric":"http_req_failed","data":{"value":0}}\n')
        temp_input = f.name

    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
        temp_output = f.name

    try:
        generate_report(temp_input, temp_output)
        report = Path(temp_output).read_text()

        # Verify all requests succeeded
        assert "Failed-request rate: 0.00%" in report
        assert "Failure samples: 0" in report
        assert "Total HTTP requests: 100" in report
    finally:
        Path(temp_input).unlink()
        Path(temp_output).unlink()


def test_generate_report_mixed_success_failure():
    """Test report generation with mixed success and failure."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        # 100 total requests: 97 success, 3 failure
        for _ in range(97):
            f.write('{"type":"Point","metric":"http_req_duration","data":{"value":100,"time":"2024-01-01T00:00:00Z"}}\n')
            f.write('{"type":"Point","metric":"http_reqs","data":{"value":1,"time":"2024-01-01T00:00:00Z"}}\n')
            f.write('{"type":"Point","metric":"http_req_failed","data":{"value":0}}\n')
        for _ in range(3):
            f.write('{"type":"Point","metric":"http_req_duration","data":{"value":100,"time":"2024-01-01T00:00:00Z"}}\n')
            f.write('{"type":"Point","metric":"http_reqs","data":{"value":1,"time":"2024-01-01T00:00:00Z"}}\n')
            f.write('{"type":"Point","metric":"http_req_failed","data":{"value":1}}\n')
        temp_input = f.name

    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
        temp_output = f.name

    try:
        generate_report(temp_input, temp_output)
        report = Path(temp_output).read_text()

        # Verify failure count is correct
        assert "Failed-request rate: 3.00%" in report
        assert "Failure samples: 3" in report
        assert "Total HTTP requests: 100" in report
    finally:
        Path(temp_input).unlink()
        Path(temp_output).unlink()


def test_generate_report_all_failure():
    """Test report generation with all failed requests."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        # 10 failed requests (http_req_failed = 1)
        for _ in range(10):
            f.write('{"type":"Point","metric":"http_req_duration","data":{"value":100,"time":"2024-01-01T00:00:00Z"}}\n')
            f.write('{"type":"Point","metric":"http_reqs","data":{"value":1,"time":"2024-01-01T00:00:00Z"}}\n')
            f.write('{"type":"Point","metric":"http_req_failed","data":{"value":1}}\n')
        temp_input = f.name

    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
        temp_output = f.name

    try:
        generate_report(temp_input, temp_output)
        report = Path(temp_output).read_text()

        # Verify all requests failed
        assert "Failed-request rate: 100.00%" in report
        assert "Failure samples: 10" in report
        assert "Total HTTP requests: 10" in report
    finally:
        Path(temp_input).unlink()
        Path(temp_output).unlink()


def test_generate_report_empty_data():
    """Test report generation with no data."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_input = f.name

    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
        temp_output = f.name

    try:
        generate_report(temp_input, temp_output)
        report = Path(temp_output).read_text()

        # Verify warning is shown
        assert "WARNING" in report
        assert "No k6 metric points were found" in report
    finally:
        Path(temp_input).unlink()
        Path(temp_output).unlink()
