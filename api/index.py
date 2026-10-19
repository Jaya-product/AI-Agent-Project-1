"""
api/index.py
────────────
Vercel Serverless Function entry point for Groww Review Intelligence Agent.

Handles:
  - GET  /api/status  -> Health check & metadata
  - GET  /api/pulse   -> Latest weekly pulse JSON dataset
  - GET  /api/cron    -> Automated weekly pulse trigger (Vercel Cron)
  - POST /api/run     -> Trigger agent pipeline run
"""

from __future__ import annotations

import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

# Ensure repository root is on sys.path so src.* modules can be imported
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_FRONTEND_DIR = _PROJECT_ROOT / "Designs" / "groww-pulse"


def _get_pulse_json_path() -> Path:
    p1 = _FRONTEND_DIR / "data" / "pulse.json"
    if p1.exists():
        return p1
    p2 = _PROJECT_ROOT / "public" / "data" / "pulse.json"
    if p2.exists():
        return p2
    return p1


class handler(BaseHTTPRequestHandler):
    """Vercel Python serverless HTTP request handler."""

    def do_OPTIONS(self) -> None:
        """Handle CORS pre-flight requests."""
        self.send_response(HTTPStatus.NO_CONTENT)
        self._add_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        """Handle GET requests for pulse data, status, and cron trigger."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        pulse_path = _get_pulse_json_path()

        if path in ("", "/api", "/api/status", "/api/index"):
            self._send_json(HTTPStatus.OK, {
                "status": "healthy",
                "service": "Groww Review Intelligence Agent",
                "version": "1.0.0",
                "pulse_exists": pulse_path.exists(),
                "endpoints": [
                    "GET  /api/status",
                    "GET  /api/pulse",
                    "GET  /api/cron",
                    "POST /api/run",
                ],
            })
            return

        if path == "/api/pulse":
            if not pulse_path.exists():
                self._send_json(HTTPStatus.NOT_FOUND, {
                    "error": "No pulse data generated yet. Run pipeline or POST /api/run to generate.",
                })
                return
            try:
                with open(pulse_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self._send_json(HTTPStatus.OK, data)
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return

        if path == "/api/cron":
            # Authenticate cron request via CRON_SECRET if configured
            cron_secret = os.getenv("CRON_SECRET", "").strip()
            auth_header = self.headers.get("Authorization", "").strip()
            if cron_secret and auth_header != f"Bearer {cron_secret}":
                self._send_json(HTTPStatus.UNAUTHORIZED, {
                    "error": "Unauthorized: invalid or missing CRON_SECRET authorization header",
                })
                return

            self._run_agent(dry_run=False, sample_data=False)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Endpoint not found: {self.path}"})

    def do_POST(self) -> None:
        """Handle POST requests to trigger pipeline execution."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path in ("/api/run", "/api/refresh"):
            # Optional API key protection
            api_key = os.getenv("AGENT_API_KEY", "").strip()
            if api_key:
                auth_header = self.headers.get("Authorization", "").strip()
                if auth_header != f"Bearer {api_key}":
                    self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized: invalid API key"})
                    return

            self._run_agent(dry_run=True, sample_data=True)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Endpoint not found: {self.path}"})

    def _run_agent(self, dry_run: bool = True, sample_data: bool = True) -> None:
        """Execute the agent pipeline and return synthesized dashboard data."""
        try:
            from src.agent.core import run_pipeline

            result = run_pipeline(dry_run=dry_run, sample_data=sample_data)
            self._send_json(HTTPStatus.OK, {
                "success": True,
                "message": "Pipeline executed successfully and pulse data refreshed.",
                "data": result.get("dashboard_data"),
            })
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "success": False,
                "error": str(exc),
            })

    def _add_cors_headers(self) -> None:
        """Inject CORS and caching headers."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")

    def _send_json(self, status: int, payload: dict) -> None:
        """Helper to serialize and transmit JSON responses."""
        body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(body)
