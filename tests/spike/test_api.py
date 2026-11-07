"""Integration tests for FastAPI endpoints."""

import pytest
from starlette.testclient import TestClient
from app.spike.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data
    assert "environment" in data
    assert "server_ytdlp_enabled" in data


def test_validate_url_valid(client):
    response = client.post(
        "/spike/validate",
        json={"url": "https://www.instagram.com/reel/C1234567890/?igsh=test"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["is_valid"] is True
    assert data["shortcode"] == "C1234567890"
    assert data["path_type"] == "reel"
    assert data["normalized_url"] == "https://www.instagram.com/reel/C1234567890/"


def test_validate_url_invalid(client):
    response = client.post(
        "/spike/validate",
        json={"url": "https://twitter.com/post/123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["is_valid"] is False
    assert data["error_detail"] is not None


def test_get_metrics_endpoint(client):
    response = client.get("/spike/metrics")
    assert response.status_code == 200
    data = response.json()
    assert "total_attempts" in data
    assert "url_resolution_success_rate" in data
    assert "end_to_end_processing_success_rate" in data


def test_get_nonexistent_result_returns_404_rfc7807(client):
    response = client.get("/spike/results/exp_nonexistent_9999999")
    assert response.status_code == 404
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "HTTP_ERROR"
    assert data["error"]["status_code"] == 404

