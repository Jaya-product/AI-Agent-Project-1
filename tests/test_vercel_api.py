"""
tests/test_vercel_api.py
────────────────────────
Unit tests for Vercel Serverless Function handler in api/index.py.
"""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from api.index import handler


class DummyVercelHandler(handler):
    """Subclass of handler that mocks socket connections for unit testing."""

    def __init__(self, method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None):
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.command = method
        self.path = path
        self.request_version = "HTTP/1.1"
        self.requestline = f"{method} {path} HTTP/1.1"
        self.headers = headers or {}
        self.close_connection = True

        # Run the handler method
        if method == "GET":
            self.do_GET()
        elif method == "POST":
            self.do_POST()
        elif method == "OPTIONS":
            self.do_OPTIONS()

    def log_message(self, format: str, *args):
        pass

    def get_response(self) -> tuple[int, dict, str]:
        """Parse the raw response from wfile."""
        self.wfile.seek(0)
        raw = self.wfile.read().decode("utf-8")
        lines = raw.split("\r\n")
        status_line = lines[0]
        status_code = int(status_line.split(" ")[1])

        # Find empty line separating headers from body
        body_idx = lines.index("")
        body_str = "\r\n".join(lines[body_idx + 1:])
        try:
            body_json = json.loads(body_str)
        except Exception:
            body_json = {}
        return status_code, body_json, body_str


def test_vercel_status_endpoint():
    """GET /api/status returns healthy status and service info."""
    h = DummyVercelHandler("GET", "/api/status")
    status, body, _ = h.get_response()
    assert status == 200
    assert body["status"] == "healthy"
    assert body["service"] == "Groww Review Intelligence Agent"
    assert "pulse_exists" in body


def test_vercel_pulse_endpoint():
    """GET /api/pulse returns valid pulse JSON data."""
    h = DummyVercelHandler("GET", "/api/pulse")
    status, body, _ = h.get_response()
    assert status == 200
    assert "week" in body
    assert "themes" in body
    assert "quotes" in body
    assert "actions" in body


def test_vercel_cors_options():
    """OPTIONS request returns 204 with CORS headers."""
    h = DummyVercelHandler("OPTIONS", "/api/pulse")
    status, _, _ = h.get_response()
    assert status == 204


def test_vercel_not_found():
    """Unknown endpoint returns 404."""
    h = DummyVercelHandler("GET", "/api/unknown-endpoint")
    status, body, _ = h.get_response()
    assert status == 404
    assert "error" in body


def test_vercel_cron_unauthorized_when_secret_set(monkeypatch):
    """GET /api/cron rejects request without valid Bearer token when CRON_SECRET is configured."""
    monkeypatch.setenv("CRON_SECRET", "super-secret-token")
    h = DummyVercelHandler("GET", "/api/cron")
    status, body, _ = h.get_response()
    assert status == 401
    assert "Unauthorized" in body["error"]


def test_vercel_post_run_mocked():
    """POST /api/run executes pipeline and returns refreshed dashboard data."""
    mock_result = {
        "dashboard_data": {
            "week": "2026-10-19",
            "themes": [{"rank": 1, "theme": "UI/UX", "reviews": 10}],
            "quotes": [],
            "actions": [],
        }
    }
    with patch("src.agent.core.run_pipeline", return_value=mock_result):
        h = DummyVercelHandler("POST", "/api/run")
        status, body, _ = h.get_response()
        assert status == 200
        assert body["success"] is True
        assert body["data"]["week"] == "2026-10-19"
