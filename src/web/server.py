"""
src/web/server.py
─────────────────
Lightweight HTTP server for the Groww Weekly Review Pulse Dashboard.

Serves the interactive frontend from ``Designs/groww-pulse`` and provides
API endpoints to read pulse data and trigger pipeline runs.

Endpoints:
  - GET  /              -> Serves index.html
  - GET  /api/pulse     -> Returns latest pulse JSON
  - GET  /api/status    -> Health check & server status
  - POST /api/run       -> Runs the agent pipeline and refreshes pulse data
  - GET  /*             -> Static assets (styles.css, app.js, data/...)

Usage:
  python -m src.web.server [--port 8000] [--host 127.0.0.1]
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_DIR = _PROJECT_ROOT / "Designs" / "groww-pulse"
_PULSE_JSON_PATH = _FRONTEND_DIR / "data" / "pulse.json"


class GrowwPulseHandler(SimpleHTTPRequestHandler):
    """Custom HTTP request handler serving Groww Pulse frontend and API."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(_FRONTEND_DIR), **kwargs)

    def end_headers(self) -> None:
        """Add standard security and CORS headers."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        """Handle CORS pre-flight requests."""
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self) -> None:
        """Handle GET requests for static files and API routes."""
        req_path = self.path.split("?")[0].rstrip("/")

        if req_path in ("", "/index.html"):
            self.path = "/index.html"
            return super().do_GET()

        if req_path == "/api/status":
            self._send_json(HTTPStatus.OK, {
                "status": "healthy",
                "app": "Groww Weekly Review Pulse",
                "frontend_dir": str(_FRONTEND_DIR),
                "pulse_exists": _PULSE_JSON_PATH.exists(),
            })
            return

        if req_path == "/api/pulse":
            if not _PULSE_JSON_PATH.exists():
                self._send_json(HTTPStatus.NOT_FOUND, {
                    "error": "No pulse data generated yet. Run pipeline or POST /api/run to generate.",
                })
                return
            try:
                with open(_PULSE_JSON_PATH, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self._send_json(HTTPStatus.OK, data)
            except Exception as exc:
                logger.error("Failed to read pulse.json", extra={"error": str(exc)})
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return

        # Serve static assets from frontend directory
        return super().do_GET()

    def do_POST(self) -> None:
        """Handle POST requests, such as triggering a new pipeline run."""
        req_path = self.path.split("?")[0].rstrip("/")

        if req_path in ("/api/run", "/api/refresh"):
            try:
                from src.agent.core import run_pipeline

                # Run dry run with sample data or live data
                result = run_pipeline(dry_run=True, sample_data=True)
                dashboard_data = result.get("dashboard_data")

                self._send_json(HTTPStatus.OK, {
                    "success": True,
                    "message": "Pipeline executed and dashboard data refreshed.",
                    "data": dashboard_data,
                })
            except Exception as exc:
                logger.error("Failed to execute pipeline via POST /api/run", extra={"error": str(exc)})
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                    "success": False,
                    "error": str(exc),
                })
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Endpoint not found: {self.path}"})

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        """Helper to serialize and write JSON response."""
        body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Route HTTP server access log to logger."""
        logger.debug(f"{self.address_string()} - {format % args}")


def start_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    """
    Start the Groww Pulse HTTP server on host:port.
    """
    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, GrowwPulseHandler)
    logger.info(
        "Groww Pulse Dashboard server started",
        extra={"host": host, "port": port, "frontend_path": str(_FRONTEND_DIR)},
    )
    return httpd


def main():
    """CLI Entry point for the Groww Pulse web server."""
    parser = argparse.ArgumentParser(description="Start the Groww Review Pulse web dashboard server.")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port number (default: 8000)")

    args = parser.parse_args()

    print("=" * 70)
    print(" GROWW WEEKLY REVIEW PULSE — DASHBOARD SERVER")
    print("=" * 70)
    print(f" Serving dashboard from : {_FRONTEND_DIR}")
    print(f" Web UI URL             : http://{args.host}:{args.port}/")
    print(f" API Pulse Endpoint     : http://{args.host}:{args.port}/api/pulse")
    print(f" API Refresh Endpoint   : http://{args.host}:{args.port}/api/run (POST)")
    print("=" * 70)
    print(" Press Ctrl+C to stop the server.\n")

    httpd = start_server(host=args.host, port=args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        httpd.server_close()
        print("Server stopped cleanly.")


if __name__ == "__main__":
    main()
