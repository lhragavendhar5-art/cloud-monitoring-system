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
    # Reset any demo/threshold environment variables that might leak across tests
    os.environ.pop("ENABLE_DEMO_MODE", None)
    os.environ.pop("ENABLE_ERROR_SIMULATION", None)
    os.environ.pop("DEMO_ACCESS_TOKEN", None)
    os.environ.pop("HEALTH_DEGRADED_ERROR_RATE", None)
    os.environ.pop("HEALTH_UNHEALTHY_ERROR_RATE", None)
    os.environ.pop("HEALTH_DEGRADED_LATENCY_MS", None)
    os.environ.pop("HEALTH_UNHEALTHY_LATENCY_MS", None)
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
    assert "diagnostics" in data
    assert isinstance(data["uptime_seconds"], (int, float))


def test_health_updates_last_health_check(client):
    client.get("/health")
    metrics_res = client.get("/metrics")
    data = json.loads(metrics_res.data)
    assert data["last_health_check"] is not None


def test_health_status_degraded_on_error_rate(client):
    os.environ["ENABLE_DEMO_MODE"] = "true"
    os.environ["HEALTH_DEGRADED_ERROR_RATE"] = "0.10"
    os.environ["HEALTH_UNHEALTHY_ERROR_RATE"] = "0.50"

    # Generate 8 successful requests and 2 server errors -> error rate = 2/10 = 20% (>= 10% degraded threshold)
    for _ in range(8):
        client.get("/")
    for _ in range(2):
        client.get("/api/demo/server-error")

    health_res = client.get("/health")
    assert health_res.status_code == 200
    data = json.loads(health_res.data)
    assert data["status"] == "degraded"
    assert data["diagnostics"]["observed_server_error_rate"] == 0.2


def test_health_status_unhealthy_on_critical_error_rate(client):
    os.environ["ENABLE_DEMO_MODE"] = "true"
    os.environ["HEALTH_UNHEALTHY_ERROR_RATE"] = "0.50"

    # Generate 1 successful request and 3 server errors -> error rate = 3/4 = 75% (>= 50% critical threshold)
    client.get("/")
    for _ in range(3):
        client.get("/api/demo/server-error")

    health_res = client.get("/health")
    assert health_res.status_code == 503
    data = json.loads(health_res.data)
    assert data["status"] == "unhealthy"
    assert data["diagnostics"]["observed_server_error_rate"] == 0.75


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
        "client_error_count",
        "server_error_count",
        "error_count",
        "average_response_time_ms",
        "min_response_time_ms",
        "max_response_time_ms",
        "response_time_samples",
        "error_rate",
        "uptime_seconds",
        "last_health_check",
        "health_status",
        "in_memory_notice"
    ]
    for key in expected_keys:
        assert key in data, f"Missing key in metrics: {key}"


def test_metrics_initial_empty_state(client):
    response = client.get("/metrics")
    data = json.loads(response.data)
    assert data["total_requests"] == 0
    assert data["successful_requests"] == 0
    assert data["client_error_count"] == 0
    assert data["server_error_count"] == 0
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
    client.get("/")
    metrics_res = client.get("/metrics")
    data = json.loads(metrics_res.data)
    assert data["total_requests"] == 1
    assert data["successful_requests"] == 1
    assert data["error_count"] == 0
    assert len(data["response_time_samples"]) == 1
    assert data["average_response_time_ms"] is not None


def test_excluded_paths_do_not_inflate_metrics(client):
    client.get("/health")
    client.get("/health")
    client.get("/api/activity")
    client.get("/api/demo/status")

    metrics_res = client.get("/metrics")
    data = json.loads(metrics_res.data)
    assert data["total_requests"] == 0
    assert data["successful_requests"] == 0


def test_response_time_statistics_calculation(client):
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
    os.environ.pop("ENABLE_DEMO_MODE", None)
    response = client.get("/simulate-error")
    assert response.status_code == 403
    data = json.loads(response.data)
    assert data["status"] == 403
    assert "disabled" in data["error"].lower()


def test_simulate_error_when_enabled(client):
    os.environ["ENABLE_ERROR_SIMULATION"] = "true"
    response = client.get("/simulate-error")
    assert response.status_code == 500
    data = json.loads(response.data)
    assert data["status"] == 500
    assert data["error"] == "Internal server error"
    assert "Traceback" not in response.data.decode("utf-8")

    metrics_res = client.get("/metrics")
    m_data = json.loads(metrics_res.data)
    assert m_data["error_count"] == 1
    assert m_data["server_error_count"] == 1
    assert m_data["total_requests"] == 1
    assert m_data["error_rate"] == 1.0


def test_404_handler_structure(client):
    response = client.get("/route-that-does-not-exist-404")
    assert response.status_code == 404
    data = json.loads(response.data)
    assert data["status"] == 404
    assert data["error"] == "Not found"
    assert "Traceback" not in response.data.decode("utf-8")


def test_404_not_counted_as_server_error(client):
    client.get("/nonexistent-page")
    metrics_res = client.get("/metrics")
    data = json.loads(metrics_res.data)
    assert data["total_requests"] == 1
    assert data["successful_requests"] == 0
    assert data["client_error_count"] == 1
    assert data["server_error_count"] == 0
    assert data["error_count"] == 0


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


# -----------------------------------------------------------------------------
# 9. Interactive Demo Scenarios & Security Controls Tests
# -----------------------------------------------------------------------------

def test_demo_status_endpoint(client):
    res = client.get("/api/demo/status")
    assert res.status_code == 200
    data = json.loads(res.data)
    assert "demo_mode_enabled" in data
    assert "token_required" in data


def test_demo_controls_disabled_by_default(client):
    # Verify all demo endpoints return 403 when demo mode is not enabled
    os.environ.pop("ENABLE_DEMO_MODE", None)
    os.environ.pop("ENABLE_ERROR_SIMULATION", None)

    for route, method in [
        ("/api/demo/traffic", "POST"),
        ("/api/demo/server-error", "POST"),
        ("/api/demo/slow-response", "POST"),
        ("/api/demo/client-error", "POST"),
        ("/api/demo/recovery", "POST"),
        ("/api/demo/reset", "POST"),
    ]:
        if method == "POST":
            res = client.post(route)
        else:
            res = client.get(route)
        assert res.status_code == 403, f"{route} did not return 403 when disabled"


def test_demo_controls_token_authorization(client):
    os.environ["ENABLE_DEMO_MODE"] = "true"
    os.environ["DEMO_ACCESS_TOKEN"] = "test-secret-key-123"

    # 1. Missing token -> 401
    res1 = client.post("/api/demo/traffic", json={"count": 1})
    assert res1.status_code == 401

    # 2. Invalid token -> 401
    res2 = client.post("/api/demo/traffic", json={"count": 1}, headers={"X-Demo-Token": "wrong-key"})
    assert res2.status_code == 401

    # 3. Valid token via header -> 200
    res3 = client.post("/api/demo/traffic", json={"count": 1}, headers={"X-Demo-Token": "test-secret-key-123"})
    assert res3.status_code == 200


def test_demo_input_bounds_validation(client):
    os.environ["ENABLE_DEMO_MODE"] = "true"

    # Out of bounds request count (min 1, max 25)
    bad_count_res = client.post("/api/demo/traffic", json={"count": 999})
    assert bad_count_res.status_code == 400

    zero_count_res = client.post("/api/demo/traffic", json={"count": 0})
    assert zero_count_res.status_code == 400

    # Non-integer count
    invalid_count_res = client.post("/api/demo/traffic", json={"count": "abc"})
    assert invalid_count_res.status_code == 400

    # Out of bounds delay (min 50ms, max 2000ms)
    bad_delay_res = client.post("/api/demo/slow-response", json={"delay_ms": 10000})
    assert bad_delay_res.status_code == 400

    low_delay_res = client.post("/api/demo/slow-response", json={"delay_ms": 10})
    assert low_delay_res.status_code == 400


def test_demo_normal_traffic_scenario(client):
    os.environ["ENABLE_DEMO_MODE"] = "true"

    res = client.post("/api/demo/traffic", json={"count": 3})
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data["scenario"] == "normal_traffic"

    # Verify metrics updated
    m_res = client.get("/metrics")
    m_data = json.loads(m_res.data)
    assert m_data["total_requests"] == 1
    assert m_data["successful_requests"] == 1
    assert m_data["error_count"] == 0


def test_demo_server_error_scenario(client):
    os.environ["ENABLE_DEMO_MODE"] = "true"

    res = client.post("/api/demo/server-error")
    assert res.status_code == 500
    err_data = json.loads(res.data)
    assert err_data["status"] == 500

    # Verify server error counted
    m_res = client.get("/metrics")
    m_data = json.loads(m_res.data)
    assert m_data["server_error_count"] == 1
    assert m_data["error_count"] == 1
    assert m_data["error_rate"] == 1.0


def test_demo_client_error_scenario(client):
    os.environ["ENABLE_DEMO_MODE"] = "true"

    res = client.post("/api/demo/client-error")
    assert res.status_code == 404
    data = json.loads(res.data)
    assert data["status"] == 404

    # Verify client error counted separately from server error
    m_res = client.get("/metrics")
    m_data = json.loads(m_res.data)
    assert m_data["total_requests"] == 1
    assert m_data["client_error_count"] == 1
    assert m_data["server_error_count"] == 0
    assert m_data["error_count"] == 0


def test_demo_slow_response_scenario(client):
    os.environ["ENABLE_DEMO_MODE"] = "true"

    res = client.post("/api/demo/slow-response", json={"delay_ms": 100})
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data["delay_ms"] == 100

    # Verify measured latency was at least 90ms
    m_res = client.get("/metrics")
    m_data = json.loads(m_res.data)
    assert m_data["total_requests"] == 1
    assert m_data["average_response_time_ms"] >= 90.0


def test_demo_recovery_scenario(client):
    os.environ["ENABLE_DEMO_MODE"] = "true"

    # Trigger 1 server error -> error rate 100%
    client.post("/api/demo/server-error")
    m1 = json.loads(client.get("/metrics").data)
    assert m1["error_rate"] == 1.0

    # Execute recovery batch
    rec_res = client.post("/api/demo/recovery", json={"count": 5})
    assert rec_res.status_code == 200

    # Error rate drops to 1/2 = 50%
    m2 = json.loads(client.get("/metrics").data)
    assert m2["total_requests"] == 2
    assert m2["error_rate"] == 0.5


def test_demo_reset_scenario(client):
    os.environ["ENABLE_DEMO_MODE"] = "true"

    # Generate some traffic and errors
    client.get("/")
    client.post("/api/demo/server-error")
    m1 = json.loads(client.get("/metrics").data)
    assert m1["total_requests"] == 2
    assert m1["server_error_count"] == 1

    # Reset metrics
    reset_res = client.post("/api/demo/reset")
    assert reset_res.status_code == 200

    # Verify everything returned to clean 0 state
    m2 = json.loads(client.get("/metrics").data)
    assert m2["total_requests"] == 0
    assert m2["successful_requests"] == 0
    assert m2["client_error_count"] == 0
    assert m2["server_error_count"] == 0
    assert m2["error_count"] == 0
    assert m2["average_response_time_ms"] is None
    assert m2["response_time_samples"] == []
