"""
Cloud-Based Application Deployment and Monitoring System
Flask Backend Application (app.py)

Note on Metrics Storage:
Metrics are stored in-memory using thread-safe data structures protected by threading.Lock.
All metrics counters, sample buffers, and activity logs reset whenever the application restarts.
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

# Excluded paths that do not count toward normal traffic metrics
EXCLUDED_PATH_PREFIXES = (
    "/health",
    "/metrics",
    "/api/",
    "/static",
    "/favicon"
)

# Thread-safe in-memory metrics storage
# Note: In multi-worker WSGI environments (e.g. Gunicorn -w > 1), each worker maintains
# isolated in-memory counters. For persistent distributed metrics, external datastores
# (e.g., Redis, Prometheus) would be used.
metrics_lock = threading.Lock()
metrics = {
    "total_requests": 0,
    "successful_requests": 0,  # 2xx and 3xx HTTP status codes
    "error_count": 0,          # 5xx Server Errors only (4xx client errors are not counted as server errors)
    "response_time_samples": deque(maxlen=100),  # Max 100 recent response time samples (ms)
    "recent_requests": deque(maxlen=50),         # Max 50 recent requests metadata
    "last_health_check": None                    # ISO8601 string or None
}


def reset_metrics():
    """
    Reset all in-memory metrics counters and buffers.
    Used for test isolation and clean-state resets.
    """
    with metrics_lock:
        metrics["total_requests"] = 0
        metrics["successful_requests"] = 0
        metrics["error_count"] = 0
        metrics["response_time_samples"].clear()
        metrics["recent_requests"].clear()
        metrics["last_health_check"] = None


def is_excluded_path(path: str) -> bool:
    """Check if the given request path starts with any excluded prefix."""
    return any(path.startswith(prefix) for prefix in EXCLUDED_PATH_PREFIXES)


# -----------------------------------------------------------------------------
# Middleware: Request Timing and Metrics Recording
# -----------------------------------------------------------------------------

@app.before_request
def before_request_handler():
    """Start timing the incoming request."""
    g.start_time = time.time()


@app.after_request
def after_request_handler(response):
    """
    Apply security headers and record traffic metrics for non-excluded routes.
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

    # 2. Timing calculation
    start_time = getattr(g, "start_time", None)
    if start_time is not None:
        duration_ms = (time.time() - start_time) * 1000.0
    else:
        duration_ms = 0.0

    # 3. Record metrics for non-excluded paths only
    req_path = request.path
    if not is_excluded_path(req_path):
        status_code = response.status_code
        with metrics_lock:
            metrics["total_requests"] += 1

            if 200 <= status_code < 400:
                metrics["successful_requests"] += 1
            elif 500 <= status_code < 600:
                # If error was not already incremented by error handler
                if not getattr(g, "error_counted", False):
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
    Return safe JSON 500 response without revealing stack traces to users.
    Increment error counter if the request path is not excluded.
    """
    logger.error("Unhandled exception caught: %s", error)

    # Mark as counted so after_request doesn't double-count
    req_path = request.path if request else ""
    if not is_excluded_path(req_path):
        with metrics_lock:
            if not getattr(g, "error_counted", False):
                metrics["error_count"] += 1
                g.error_counted = True

    return jsonify({
        "error": "Internal server error",
        "status": 500
    }), 500


# -----------------------------------------------------------------------------
# Application Routes
# -----------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    """Render the dashboard template."""
    return render_template("dashboard.html")


@app.route("/evaluation", methods=["GET"])
def evaluation():
    """Render the evaluation template."""
    return render_template("evaluation.html")


@app.route("/health", methods=["GET"])
def health():
    """
    JSON health check endpoint.
    Updates the last_health_check timestamp and returns system health status.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    uptime_sec = round(time.time() - START_TIME, 2)

    with metrics_lock:
        metrics["last_health_check"] = now_iso

    return jsonify({
        "status": "healthy",
        "timestamp": now_iso,
        "uptime_seconds": uptime_sec,
        "version": APP_VERSION
    }), 200


@app.route("/metrics", methods=["GET"])
def get_metrics():
    """
    JSON metrics endpoint.
    Returns aggregated metrics, uptime, and notice regarding in-memory persistence.
    """
    uptime_sec = round(time.time() - START_TIME, 2)

    with metrics_lock:
        total_reqs = metrics["total_requests"]
        successful_reqs = metrics["successful_requests"]
        error_cnt = metrics["error_count"]
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

    error_rate = round(error_cnt / total_reqs, 4) if total_reqs > 0 else None

    return jsonify({
        "total_requests": total_reqs,
        "successful_requests": successful_reqs,
        "error_count": error_cnt,
        "average_response_time_ms": avg_resp_time,
        "min_response_time_ms": min_resp_time,
        "max_response_time_ms": max_resp_time,
        "response_time_samples": samples,
        "error_rate": error_rate,
        "uptime_seconds": uptime_sec,
        "last_health_check": last_check,
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


@app.route("/simulate-error", methods=["GET"])
def simulate_error():
    """
    Error simulation endpoint for testing monitoring alerts and 500 handling.
    Disabled by default. Only enabled when ENABLE_ERROR_SIMULATION=true.
    """
    enable_simulation = os.environ.get("ENABLE_ERROR_SIMULATION", "").strip().lower() == "true"
    if not enable_simulation:
        return jsonify({
            "error": "Error simulation is disabled",
            "status": 403,
            "message": "Set ENABLE_ERROR_SIMULATION=true to enable this endpoint."
        }), 403

    raise RuntimeError("Controlled simulated 500 error for testing")


# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
