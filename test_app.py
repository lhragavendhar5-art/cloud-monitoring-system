import pytest
import json
import os
from app import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

# Test health endpoint
def test_health_endpoint_status(client):
    response = client.get('/health')
    assert response.status_code == 200

def test_health_endpoint_json_format(client):
    response = client.get('/health')
    data = json.loads(response.data)
    assert 'status' in data
    assert data['status'] == 'healthy'
    assert 'timestamp' in data
    assert 'uptime_seconds' in data
    assert 'version' in data

def test_health_endpoint_content_type(client):
    response = client.get('/health')
    assert response.content_type == 'application/json'

# Test metrics endpoint
def test_metrics_endpoint_status(client):
    response = client.get('/metrics')
    assert response.status_code == 200

def test_metrics_endpoint_json_format(client):
    response = client.get('/metrics')
    data = json.loads(response.data)
    assert 'total_requests' in data
    assert 'successful_requests' in data
    assert 'error_count' in data
    assert 'average_response_time_ms' in data
    assert 'min_response_time_ms' in data
    assert 'max_response_time_ms' in data
    assert 'uptime_seconds' in data
    assert 'in_memory_notice' in data

def test_metrics_initial_values(client):
    response = client.get('/metrics')
    data = json.loads(response.data)
    assert isinstance(data['total_requests'], int)
    assert isinstance(data['error_count'], int)

# Test dashboard
def test_dashboard_renders(client):
    response = client.get('/')
    assert response.status_code == 200

def test_dashboard_content_type(client):
    response = client.get('/')
    assert 'text/html' in response.content_type

def test_dashboard_contains_key_elements(client):
    response = client.get('/')
    html = response.data.decode()
    assert 'CloudMonitor' in html
    assert 'Dashboard' in html

# Test activity API
def test_activity_endpoint_status(client):
    response = client.get('/api/activity')
    assert response.status_code == 200

def test_activity_endpoint_json_format(client):
    response = client.get('/api/activity')
    data = json.loads(response.data)
    assert 'recent_requests' in data
    assert 'response_time_samples' in data
    assert 'sample_count' in data

def test_activity_recent_requests_is_list(client):
    response = client.get('/api/activity')
    data = json.loads(response.data)
    assert isinstance(data['recent_requests'], list)
    assert isinstance(data['response_time_samples'], list)

# Test error simulation disabled by default
def test_simulate_error_disabled_by_default(client):
    # Ensure env var is not set
    os.environ.pop('ENABLE_ERROR_SIMULATION', None)
    response = client.get('/simulate-error')
    assert response.status_code == 403

def test_simulate_error_returns_json_when_disabled(client):
    os.environ.pop('ENABLE_ERROR_SIMULATION', None)
    response = client.get('/simulate-error')
    data = json.loads(response.data)
    assert 'error' in data or 'message' in data

# Test evaluation page
def test_evaluation_page_renders(client):
    response = client.get('/evaluation')
    assert response.status_code == 200

def test_evaluation_page_content(client):
    response = client.get('/')
    html = response.data.decode()
    assert 'text/html' in response.content_type

# Test 404 handler
def test_404_handler(client):
    response = client.get('/nonexistent-page')
    assert response.status_code == 404

def test_404_returns_json(client):
    response = client.get('/nonexistent-page')
    data = json.loads(response.data)
    assert data['status'] == 404

# Test metrics collection
def test_metrics_increment_after_request(client):
    # Make a request to dashboard (tracked endpoint)
    client.get('/')
    response = client.get('/metrics')
    data = json.loads(response.data)
    assert data['total_requests'] >= 1

# Test security headers
def test_security_headers(client):
    response = client.get('/health')
    assert 'X-Content-Type-Options' in response.headers
    assert 'X-Frame-Options' in response.headers
