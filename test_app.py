import json
import os
import pytest
from app import app, reset_metrics, metrics, metrics_lock


@pytest.fixture(autouse=True)
def clean_metrics_state():
    """
    Ensure every test runs with an isolated, fresh in-memory metrics state.
    """
    reset_metrics()
    yield
    reset_metrics()


@pytest.fixture
def client():
    """
    Flask test client fixture with TESTING mode enabled.
    """
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


# -----------------------------------------------------------------------------
# 1. Health Endpoint Tests
# -----------------------------------------------------------------------------

def test_health_endpoint_status(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.content_type == "application/json"


def test_health_endpoint_json_format(client):
    response = client.get("/health")
    data = json.loads(response.data)
    assert data.get("status") == "healthy"
    assert "timestamp" in data
    assert "uptime_seconds" in data
    assert data.get("version") == "1.0.0"
    assert isinstance(data["uptime_seconds"], (int, float))


def test_health_updates_last_health_check(client):
    client.get("/health")
    metrics_res = client.get("/metrics")
    data = json.loads(metrics_res.data)
    assert data["last_health_check"] is not None


# -----------------------------------------------------------------------------
# 2. Metrics Endpoint Tests
# -----------------------------------------------------------------------------

def test_metrics_endpoint_status(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.content_type == "application/json"


def test_metrics_endpoint_json_format(client):
    response = client.get("/metrics")
    data = json.loads(response.data)
    expected_keys = [
        "total_requests",
        "successful_requests",
        "error_count",
        "average_response_time_ms",
        "min_response_time_ms",
        "max_response_time_ms",
        "response_time_samples",
        "error_rate",
        "uptime_seconds",
        "last_health_check",
        "in_memory_notice"
    ]
    for key in expected_keys:
        assert key in data, f"Missing key in metrics: {key}"


def test_metrics_initial_empty_state(client):
    response = client.get("/metrics")
    data = json.loads(response.data)
    assert data["total_requests"] == 0
    assert data["successful_requests"] == 0
    assert data["error_count"] == 0
    assert data["average_response_time_ms"] is None
    assert data["min_response_time_ms"] is None
    assert data["max_response_time_ms"] is None
    assert data["error_rate"] is None
    assert data["response_time_samples"] == []


# -----------------------------------------------------------------------------
# 3. Dashboard Route Tests
# -----------------------------------------------------------------------------

def test_dashboard_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.content_type


def test_dashboard_contains_key_elements(client):
    response = client.get("/")
    html = response.data.decode("utf-8")
    assert "CloudMonitor" in html
    assert "Application Dashboard" in html
    assert "Total Requests" in html
    assert "Successful Requests" in html
    assert "Server Errors" in html
    assert "responseTimeChart" in html
    assert "activityChart" in html
    assert "In-Memory Metrics" in html


# -----------------------------------------------------------------------------
# 4. Evaluation Page Tests
# -----------------------------------------------------------------------------

def test_evaluation_page_renders(client):
    response = client.get("/evaluation")
    assert response.status_code == 200
    assert "text/html" in response.content_type


def test_evaluation_page_content(client):
    # Tests that /evaluation specifically renders actual evaluation content
    response = client.get("/evaluation")
    assert response.status_code == 200
    html = response.data.decode("utf-8")
    assert "System Evaluation & Assessment" in html
    assert "1. Functional Test Results" in html
    assert "2. Live Performance Metrics" in html
    assert "3. Performance Test Interface" in html
    assert "4. Deployment Status" in html
    assert "5. Scalability Analysis" in html
    assert "6. Cost Analysis" in html
    assert "pytest -v" in html


# -----------------------------------------------------------------------------
# 5. Activity API Tests
# -----------------------------------------------------------------------------

def test_activity_endpoint_status(client):
    response = client.get("/api/activity")
    assert response.status_code == 200
    assert response.content_type == "application/json"


def test_activity_endpoint_json_format(client):
    response = client.get("/api/activity")
    data = json.loads(response.data)
    assert "recent_requests" in data
    assert "response_time_samples" in data
    assert "sample_count" in data
    assert isinstance(data["recent_requests"], list)
    assert isinstance(data["response_time_samples"], list)
    assert isinstance(data["sample_count"], int)


# -----------------------------------------------------------------------------
# 6. Metrics Recording & Exclusion Filtering Tests
# -----------------------------------------------------------------------------

def test_metrics_increment_on_monitored_traffic(client):
    # Visiting dashboard (monitored route)
    client.get("/")
    metrics_res = client.get("/metrics")
    data = json.loads(metrics_res.data)
    assert data["total_requests"] == 1
    assert data["successful_requests"] == 1
    assert data["error_count"] == 0
    assert len(data["response_time_samples"]) == 1
    assert data["average_response_time_ms"] is not None


def test_excluded_paths_do_not_inflate_metrics(client):
    # Calling internal monitoring endpoints should NOT increase total_requests
    client.get("/health")
    client.get("/health")
    client.get("/api/activity")

    metrics_res = client.get("/metrics")
    data = json.loads(metrics_res.data)
    # total_requests should still be 0 because internal monitoring paths are excluded
    assert data["total_requests"] == 0
    assert data["successful_requests"] == 0


def test_response_time_statistics_calculation(client):
    # Issue multiple requests to generate response time samples
    client.get("/")
    client.get("/evaluation")

    metrics_res = client.get("/metrics")
    data = json.loads(metrics_res.data)
    assert data["total_requests"] == 2
    assert len(data["response_time_samples"]) == 2
    assert data["min_response_time_ms"] <= data["average_response_time_ms"] <= data["max_response_time_ms"]


# -----------------------------------------------------------------------------
# 7. Error Handling & Simulate-Error Tests
# -----------------------------------------------------------------------------

def test_simulate_error_disabled_by_default(client):
    os.environ.pop("ENABLE_ERROR_SIMULATION", None)
    response = client.get("/simulate-error")
    assert response.status_code == 403
    data = json.loads(response.data)
    assert data["status"] == 403
    assert "disabled" in data["error"].lower()


def test_simulate_error_when_enabled(client):
    os.environ["ENABLE_ERROR_SIMULATION"] = "true"
    try:
        response = client.get("/simulate-error")
        assert response.status_code == 500
        data = json.loads(response.data)
        assert data["status"] == 500
        assert data["error"] == "Internal server error"
        # Verify no traceback leaks to user
        assert "Traceback" not in response.data.decode("utf-8")

        # Verify error counted in metrics
        metrics_res = client.get("/metrics")
        m_data = json.loads(metrics_res.data)
        assert m_data["error_count"] == 1
        assert m_data["total_requests"] == 1
        assert m_data["error_rate"] == 1.0
    finally:
        os.environ.pop("ENABLE_ERROR_SIMULATION", None)


def test_404_handler_structure(client):
    response = client.get("/route-that-does-not-exist-404")
    assert response.status_code == 404
    data = json.loads(response.data)
    assert data["status"] == 404
    assert data["error"] == "Not found"
    # Ensure no stack trace leaks
    assert "Traceback" not in response.data.decode("utf-8")


def test_404_not_counted_as_server_error(client):
    # 404 is a client error; it should increment total_requests but NOT error_count (which tracks 5xx)
    client.get("/nonexistent-page")
    metrics_res = client.get("/metrics")
    data = json.loads(metrics_res.data)
    assert data["total_requests"] == 1
    assert data["successful_requests"] == 0
    assert data["error_count"] == 0  # 4xx is not a 5xx server error


# -----------------------------------------------------------------------------
# 8. Security Headers Tests
# -----------------------------------------------------------------------------

def test_security_headers_present(client):
    response = client.get("/health")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert response.headers.get("X-XSS-Protection") == "1; mode=block"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "Content-Security-Policy" in response.headers
    csp = response.headers.get("Content-Security-Policy")
    assert "default-src 'self'" in csp
