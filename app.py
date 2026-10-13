"""
Cloud-Based Application Deployment and Monitoring System
Flask Backend Application (app.py)

Interactive Cloud Monitoring & Demonstration Architecture:
- In-memory thread-safe metrics storage protected by threading.Lock.
- Real-time performance measurement & status code distribution tracking.
- Dynamic health evaluation engine based on latency and error-rate thresholds.
- Authenticated, rate-bounded demo scenario endpoints for live FT-III examinations.
- Standard HTTP security headers and sanitized error handling.
"""

from collections import deque
from datetime import datetime, timezone
import logging
import os
import threading
import time

from flask import Flask, g, jsonify, render_template, request

# Initialize Flask application
app = Flask(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("app")

# Application Start Time for uptime calculation
START_TIME = time.time()
APP_VERSION = "1.0.0"

# Specific monitoring probes and static assets excluded from user-traffic metrics
# so internal health polling and metrics scraping do not inflate traffic statistics
EXCLUDED_PATHS = (
    "/health",
    "/metrics",
    "/api/activity",
    "/api/demo/status",
    "/api/demo/reset",
)
EXCLUDED_PATH_PREFIXES = (
    "/static",
    "/favicon",
)

# -----------------------------------------------------------------------------
# In-Memory Metrics Telemetry Engine
# -----------------------------------------------------------------------------

# Thread-safe in-memory metrics storage
# Note: In multi-worker WSGI environments (e.g. Gunicorn -w > 1), each worker maintains
# isolated in-memory counters. For persistent distributed metrics, external datastores
# (e.g., Redis, Prometheus) would be used in enterprise deployments.
metrics_lock = threading.Lock()
metrics = {
    "total_requests": 0,
    "successful_requests": 0,  # 2xx and 3xx HTTP status codes
    "client_error_count": 0,   # 4xx Client Errors (e.g., 404, 403)
    "server_error_count": 0,   # 5xx Server Errors (e.g., 500, 503)
    "error_count": 0,          # Alias for server_error_count (5xx)
    "response_time_samples": deque(maxlen=100),  # Max 100 recent response time samples (ms)
    "recent_requests": deque(maxlen=50),         # Max 50 recent requests metadata
    "last_health_check": None                    # ISO8601 string or None
}


def reset_metrics():
    """
    Reset all in-memory metrics counters and sample buffers.
    Used for demo resets and test fixture isolation.
    """
    with metrics_lock:
        metrics["total_requests"] = 0
        metrics["successful_requests"] = 0
        metrics["client_error_count"] = 0
        metrics["server_error_count"] = 0
        metrics["error_count"] = 0
        metrics["response_time_samples"].clear()
        metrics["recent_requests"].clear()
        metrics["last_health_check"] = None


def is_excluded_path(path: str) -> bool:
    """Check if the given request path is an excluded monitoring probe or asset."""
    if path in EXCLUDED_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in EXCLUDED_PATH_PREFIXES)


# -----------------------------------------------------------------------------
# Dynamic Health Evaluation Engine
# -----------------------------------------------------------------------------

def evaluate_health_status():
    """
    Evaluate system health based on actual measured runtime telemetry against defined thresholds.
    Configurable via environment variables:
      - HEALTH_DEGRADED_ERROR_RATE (default 0.10, i.e., 10%)
      - HEALTH_UNHEALTHY_ERROR_RATE (default 0.50, i.e., 50%)
      - HEALTH_DEGRADED_LATENCY_MS (default 500.0 ms)
      - HEALTH_UNHEALTHY_LATENCY_MS (default 2000.0 ms)
    Returns: (status: str, diagnostics: dict)
    """
    with metrics_lock:
        total = metrics["total_requests"]
        server_errs = metrics["server_error_count"]
        samples = list(metrics["response_time_samples"])

    error_rate = (server_errs / total) if total > 0 else 0.0
    avg_latency = (sum(samples) / len(samples)) if samples else 0.0

    degraded_error_th = float(os.environ.get("HEALTH_DEGRADED_ERROR_RATE", 0.10))
    unhealthy_error_th = float(os.environ.get("HEALTH_UNHEALTHY_ERROR_RATE", 0.50))
    degraded_lat_th = float(os.environ.get("HEALTH_DEGRADED_LATENCY_MS", 500.0))
    unhealthy_lat_th = float(os.environ.get("HEALTH_UNHEALTHY_LATENCY_MS", 2000.0))

    status = "healthy"
    reasons = []

    # Evaluate critical / unhealthy conditions
    # Require at least 2 observed requests before marking unhealthy on error-rate to prevent single-request bias
    if total >= 2 and error_rate >= unhealthy_error_th:
        status = "unhealthy"
        reasons.append(
            f"Server error rate ({round(error_rate * 100, 1)}%) exceeds critical threshold ({round(unhealthy_error_th * 100, 1)}%)"
        )
    elif samples and avg_latency >= unhealthy_lat_th:
        status = "unhealthy"
        reasons.append(
            f"Average latency ({round(avg_latency, 1)}ms) exceeds critical threshold ({round(unhealthy_lat_th, 1)}ms)"
        )
    # Evaluate degraded conditions
    elif total >= 2 and error_rate >= degraded_error_th:
        status = "degraded"
        reasons.append(
            f"Server error rate ({round(error_rate * 100, 1)}%) exceeds warning threshold ({round(degraded_error_th * 100, 1)}%)"
        )
    elif samples and avg_latency >= degraded_lat_th:
        status = "degraded"
        reasons.append(
            f"Average latency ({round(avg_latency, 1)}ms) exceeds warning threshold ({round(degraded_lat_th, 1)}ms)"
        )

    diagnostics = {
        "observed_server_error_rate": round(error_rate, 4),
        "observed_avg_latency_ms": round(avg_latency, 2),
        "sample_count": len(samples),
        "thresholds": {
            "degraded_error_rate": degraded_error_th,
            "unhealthy_error_rate": unhealthy_error_th,
            "degraded_latency_ms": degraded_lat_th,
            "unhealthy_latency_ms": unhealthy_lat_th
        },
        "reason": "; ".join(reasons) if reasons else "All operational metrics within standard thresholds"
    }

    return status, diagnostics


# -----------------------------------------------------------------------------
# Demo Authorization & Control Guard
# -----------------------------------------------------------------------------

def is_demo_enabled() -> bool:
    """
    Check if interactive demo scenarios or error simulation are explicitly enabled.
    Disabled by default for production security.
    """
    return (
        os.environ.get("ENABLE_DEMO_MODE", "").strip().lower() == "true" or
        os.environ.get("ENABLE_ERROR_SIMULATION", "").strip().lower() == "true"
    )


def verify_demo_auth():
    """
    Validate that demo mode is enabled and check DEMO_ACCESS_TOKEN if configured.
    Returns None if authorized, otherwise returns (jsonify_response, status_code).
    """
    if not is_demo_enabled():
        return jsonify({
            "error": "Demo mode is disabled",
            "status": 403,
            "message": "Demo scenario execution is disabled. Set ENABLE_DEMO_MODE=true in your environment to enable."
        }), 403

    configured_token = os.environ.get("DEMO_ACCESS_TOKEN", "").strip()
    if configured_token:
        # Check X-Demo-Token header, Bearer header, or token query param
        provided_token = request.headers.get("X-Demo-Token")
        if not provided_token and "Authorization" in request.headers:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                provided_token = auth_header[7:].strip()
        if not provided_token:
            provided_token = request.args.get("token")
            if not provided_token and request.is_json and request.json:
                provided_token = request.json.get("token")

        if not provided_token or provided_token != configured_token:
            return jsonify({
                "error": "Unauthorized demo request",
                "status": 401,
                "message": "A valid demo access token is required to execute scenario controls."
            }), 401

    return None


# -----------------------------------------------------------------------------
# Middleware: Request Timing, Security Headers, and Metrics Recording
# -----------------------------------------------------------------------------

@app.before_request
def before_request_handler():
    """Record request arrival timestamp for accurate latency measurement."""
    g.start_time = time.time()


@app.after_request
def after_request_handler(response):
    """
    Apply defense-in-depth security headers and record telemetry for non-excluded routes.
    """
    # 1. Apply Security Headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    )

    # 2. Latency calculation
    start_time = getattr(g, "start_time", None)
    if start_time is not None:
        duration_ms = (time.time() - start_time) * 1000.0
    else:
        duration_ms = 0.0

    # 3. Record metrics for monitored paths only
    req_path = request.path
    if not is_excluded_path(req_path):
        status_code = response.status_code
        with metrics_lock:
            metrics["total_requests"] += 1

            if 200 <= status_code < 400:
                metrics["successful_requests"] += 1
            elif 400 <= status_code < 500:
                metrics["client_error_count"] += 1
            elif 500 <= status_code < 600:
                # Increment only if not already counted by handle_internal_error
                if not getattr(g, "error_counted", False):
                    metrics["server_error_count"] += 1
                    metrics["error_count"] += 1
                    g.error_counted = True

            # Record response time sample
            rounded_duration = round(duration_ms, 2)
            metrics["response_time_samples"].append(rounded_duration)

            # Record recent request activity
            metrics["recent_requests"].append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "path": req_path,
                "method": request.method,
                "status": status_code,
                "response_time": rounded_duration
            })

    return response


# -----------------------------------------------------------------------------
# Error Handlers
# -----------------------------------------------------------------------------

@app.errorhandler(404)
def handle_not_found(error):
    """Return safe JSON 404 response without revealing stack traces."""
    return jsonify({
        "error": "Not found",
        "status": 404
    }), 404


@app.errorhandler(500)
@app.errorhandler(Exception)
def handle_internal_error(error):
    """
    Return safe JSON 500 response without revealing internal stack traces.
    Increment server error counters if request path is not excluded.
    """
    logger.error("Unhandled exception caught: %s", error)

    req_path = request.path if request else ""
    if not is_excluded_path(req_path):
        with metrics_lock:
            if not getattr(g, "error_counted", False):
                metrics["server_error_count"] += 1
                metrics["error_count"] += 1
                g.error_counted = True

    return jsonify({
        "error": "Internal server error",
        "status": 500
    }), 500


# -----------------------------------------------------------------------------
# Core Application Routes
# -----------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    """Render the monitoring dashboard template."""
    return render_template("dashboard.html")


@app.route("/evaluation", methods=["GET"])
def evaluation():
    """Render the FT-III evaluation benchmark template."""
    return render_template("evaluation.html")


@app.route("/health", methods=["GET"])
def health():
    """
    JSON health check endpoint.
    Evaluates runtime telemetry against latency and error-rate thresholds.
    Returns HTTP 200 when healthy or degraded, and HTTP 503 when unhealthy.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    uptime_sec = round(time.time() - START_TIME, 2)

    with metrics_lock:
        metrics["last_health_check"] = now_iso

    status, diagnostics = evaluate_health_status()
    http_code = 503 if status == "unhealthy" else 200

    return jsonify({
        "status": status,
        "timestamp": now_iso,
        "uptime_seconds": uptime_sec,
        "version": APP_VERSION,
        "diagnostics": diagnostics
    }), http_code


@app.route("/metrics", methods=["GET"])
def get_metrics():
    """
    JSON metrics endpoint.
    Returns aggregated metrics, error categories, uptime, and in-memory persistence notice.
    """
    uptime_sec = round(time.time() - START_TIME, 2)

    with metrics_lock:
        total_reqs = metrics["total_requests"]
        successful_reqs = metrics["successful_requests"]
        client_err_cnt = metrics["client_error_count"]
        server_err_cnt = metrics["server_error_count"]
        samples = list(metrics["response_time_samples"])
        last_check = metrics["last_health_check"]

    if samples:
        avg_resp_time = round(sum(samples) / len(samples), 2)
        min_resp_time = round(min(samples), 2)
        max_resp_time = round(max(samples), 2)
    else:
        avg_resp_time = None
        min_resp_time = None
        max_resp_time = None

    # Error rate defined as: server_error_count / total_requests
    error_rate = round(server_err_cnt / total_reqs, 4) if total_reqs > 0 else None
    health_status, _ = evaluate_health_status()

    return jsonify({
        "total_requests": total_reqs,
        "successful_requests": successful_reqs,
        "client_error_count": client_err_cnt,
        "server_error_count": server_err_cnt,
        "error_count": server_err_cnt,  # Backwards compatibility alias
        "average_response_time_ms": avg_resp_time,
        "min_response_time_ms": min_resp_time,
        "max_response_time_ms": max_resp_time,
        "response_time_samples": samples,
        "error_rate": error_rate,
        "uptime_seconds": uptime_sec,
        "last_health_check": last_check,
        "health_status": health_status,
        "in_memory_notice": (
            "Metrics are stored in process memory and reset when the application restarts. "
            "In multi-worker environments (e.g. Gunicorn), metrics are worker-isolated."
        )
    }), 200


@app.route("/api/activity", methods=["GET"])
def get_activity():
    """
    JSON recent activity endpoint.
    Returns recent requests and recent response time samples.
    """
    with metrics_lock:
        recent_reqs = list(metrics["recent_requests"])
        samples = list(metrics["response_time_samples"])

    return jsonify({
        "recent_requests": recent_reqs,
        "response_time_samples": samples,
        "sample_count": len(samples)
    }), 200


# -----------------------------------------------------------------------------
# Interactive Demo Scenarios API (Protected & Rate-Bounded)
# -----------------------------------------------------------------------------

@app.route("/api/demo/status", methods=["GET"])
def demo_status():
    """
    Returns public status of demo mode availability and whether a demo token is required.
    Does not disclose token content.
    """
    return jsonify({
        "demo_mode_enabled": is_demo_enabled(),
        "token_required": bool(os.environ.get("DEMO_ACCESS_TOKEN", "").strip())
    }), 200


@app.route("/api/demo/traffic", methods=["GET", "POST"])
def demo_traffic():
    """
    Scenario A: Normal Traffic
    Generates actual successful HTTP traffic. Updates request counters, latency samples, and history.
    """
    auth_err = verify_demo_auth()
    if auth_err:
        return auth_err

    count = 1
    if request.is_json and request.json:
        try:
            count = int(request.json.get("count", 1))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid count parameter; must be an integer", "status": 400}), 400
    elif request.args.get("count"):
        try:
            count = int(request.args.get("count"))
        except ValueError:
            return jsonify({"error": "Invalid count parameter; must be an integer", "status": 400}), 400

    if count < 1 or count > 25:
        return jsonify({
            "error": "Count out of bounds",
            "status": 400,
            "message": "Request count must be between 1 and 25 to prevent denial of service."
        }), 400

    # Simulate realistic microsecond-level application computation
    time.sleep(0.003)

    return jsonify({
        "scenario": "normal_traffic",
        "status": "success",
        "requests_processed": count,
        "message": f"Processed {count} normal application request(s) successfully."
    }), 200


@app.route("/api/demo/server-error", methods=["GET", "POST"])
def demo_server_error():
    """
    Scenario B: Simulate Server Error
    Triggers controlled HTTP 500 response. Updates server-error counter and error rate.
    """
    auth_err = verify_demo_auth()
    if auth_err:
        return auth_err

    # Triggers controlled simulated 500 error handled cleanly by handle_internal_error
    raise RuntimeError("Controlled simulated 500 server error triggered via demo scenario")


@app.route("/api/demo/slow-response", methods=["GET", "POST"])
def demo_slow_response():
    """
    Scenario C: Simulate Slow Response
    Applies a configurable, bounded delay to demonstrate latency spike and degradation monitoring.
    Never blocks health checks or concurrent requests.
    """
    auth_err = verify_demo_auth()
    if auth_err:
        return auth_err

    delay_ms = 400
    if request.is_json and request.json:
        try:
            delay_ms = int(request.json.get("delay_ms", 400))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid delay_ms parameter", "status": 400}), 400
    elif request.args.get("delay_ms"):
        try:
            delay_ms = int(request.args.get("delay_ms"))
        except ValueError:
            return jsonify({"error": "Invalid delay_ms parameter", "status": 400}), 400

    if delay_ms < 50 or delay_ms > 2000:
        return jsonify({
            "error": "Delay out of bounds",
            "status": 400,
            "message": "Delay must be between 50ms and 2000ms."
        }), 400

    # Inject bounded delay to measure real elapsed time
    time.sleep(delay_ms / 1000.0)

    return jsonify({
        "scenario": "slow_response",
        "status": "success",
        "delay_ms": delay_ms,
        "message": f"Simulated slow response with {delay_ms}ms injected latency."
    }), 200


@app.route("/api/demo/client-error", methods=["GET", "POST"])
def demo_client_error():
    """
    Scenario D: Simulate Client Error (404)
    Generates controlled 404 response. Tracked in client_error_count separately from server errors.
    """
    auth_err = verify_demo_auth()
    if auth_err:
        return auth_err

    return jsonify({
        "scenario": "client_error",
        "status": 404,
        "error": "Not found",
        "message": "Simulated client 404 error (resource not found). Tracked separately from 5xx server errors."
    }), 404


@app.route("/api/demo/recovery", methods=["POST"])
def demo_recovery():
    """
    Scenario E: Recovery Test
    Executes successful traffic to demonstrate error-rate reduction and health status recovery.
    """
    auth_err = verify_demo_auth()
    if auth_err:
        return auth_err

    count = 5
    if request.is_json and request.json:
        try:
            count = int(request.json.get("count", 5))
        except (TypeError, ValueError):
            count = 5

    if count < 1 or count > 25:
        count = 5

    time.sleep(0.003)

    return jsonify({
        "scenario": "recovery",
        "status": "success",
        "requests_processed": count,
        "message": f"Recovery batch processed: {count} successful requests generated. Cumulative errors remain in history, but active health metrics reflect recovery."
    }), 200


@app.route("/api/demo/reset", methods=["POST"])
def demo_reset():
    """
    Scenario F: Reset Demo Metrics
    Safely resets in-memory telemetry counters and buffers to initial empty state.
    """
    auth_err = verify_demo_auth()
    if auth_err:
        return auth_err

    reset_metrics()
    return jsonify({
        "status": "success",
        "message": "In-memory telemetry counters and sample buffers have been reset to initial state."
    }), 200


@app.route("/simulate-error", methods=["GET"])
def simulate_error():
    """
    Legacy error simulation endpoint preserved for test suite backwards compatibility.
    Disabled by default. Enabled when ENABLE_ERROR_SIMULATION=true or ENABLE_DEMO_MODE=true.
    """
    if not is_demo_enabled():
        return jsonify({
            "error": "Error simulation is disabled",
            "status": 403,
            "message": "Set ENABLE_ERROR_SIMULATION=true or ENABLE_DEMO_MODE=true to enable this endpoint."
        }), 403

    raise RuntimeError("Controlled simulated 500 error for testing")


# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
