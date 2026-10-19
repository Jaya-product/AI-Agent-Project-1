"""
tests/test_frontend_export.py
──────────────────────────────
Tests for frontend data contract export and web dashboard server.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from http import HTTPStatus
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.parser import Review
from src.intelligence.clusterer import Theme
from src.intelligence.pulse_generator import Pulse, export_dashboard_data
from src.web.server import GrowwPulseHandler, start_server


@pytest.fixture
def sample_themes() -> list[Theme]:
    now = datetime(2026, 10, 19, 12, 0, 0)
    revs = [
        Review(id="r1", rating=5, title="Great UI", text="The user interface is very clean and easy to use.", date=now, thumbs_up=10),
        Review(id="r2", rating=1, title="Hidden fees", text="Too many hidden charges and unexpected brokerage fees.", date=now, thumbs_up=25),
        Review(id="r3", rating=1, title="Withdrawal stuck", text="Customer support is unresponsive and withdrawal takes weeks.", date=now, thumbs_up=40),
    ]
    return [
        Theme(
            name="UI/UX & Ease of Use",
            review_count=35,
            reviews=[revs[0]],
            sentiment="positive",
            top_quotes=["The user interface is very clean and easy to use.", "Smooth and beginner-friendly app."],
        ),
        Theme(
            name="Brokerage Charges & Fees",
            review_count=30,
            reviews=[revs[1]],
            sentiment="negative",
            top_quotes=["Too many hidden charges and unexpected brokerage fees."],
        ),
        Theme(
            name="Customer Service & Withdrawals",
            review_count=28,
            reviews=[revs[2]],
            sentiment="negative",
            top_quotes=["Customer support is unresponsive and withdrawal takes weeks."],
        ),
    ]


@pytest.fixture
def sample_pulse(sample_themes) -> Pulse:
    return Pulse(
        week_of=date(2026, 10, 19),
        top_themes=sample_themes,
        quotes=[
            "The user interface is very clean and easy to use.",
            "Too many hidden charges and unexpected brokerage fees.",
            "Customer support is unresponsive and withdrawal takes weeks.",
        ],
        action_ideas=[
            "Deploy pre-trade brokerage breakdown modal to eliminate fee transparency complaints.",
            "Automate bank withdrawal status notifications to eliminate support ticket escalations.",
            "Improve customer service live chat response times.",
        ],
        body="# Groww Weekly Review Pulse — Week of October 19, 2026\n\nTop Themes...",
        word_count=45,
    )


class TestFrontendDataExport:
    """Test pulse data transformation and export to frontend schema."""

    def test_to_dashboard_dict_structure(self, sample_pulse):
        data = sample_pulse.to_dashboard_dict()

        assert "week" in data
        assert data["week"] == "2026-10-19"

        # Check themes
        assert "themes" in data
        assert len(data["themes"]) == 3
        assert data["themes"][0]["rank"] == 1
        assert data["themes"][0]["theme"] == "UI/UX & Ease of Use"
        assert data["themes"][0]["reviews"] == 35

        # Check quotes
        assert "quotes" in data
        assert len(data["quotes"]) >= 3
        q1 = data["quotes"][0]
        assert q1["id"] == "q1"
        assert q1["theme"] == "UI/UX & Ease of Use"
        assert q1["sentiment"] in ("Positive", "Negative")
        assert len(q1["quote"]) > 0

        # Check actions
        assert "actions" in data
        assert len(data["actions"]) == 3
        a1 = data["actions"][0]
        assert a1["id"] == "a1"
        assert a1["rank"] == 1
        assert "theme" in a1
        assert "action" in a1

    def test_export_dashboard_data_writes_files(self, sample_pulse, tmp_path):
        out_dir = tmp_path / "data"
        exported = export_dashboard_data(sample_pulse, target_dir=out_dir)

        json_file = out_dir / "pulse.json"
        js_file = out_dir / "pulse.js"

        assert json_file.exists()
        assert js_file.exists()

        with open(json_file, "r", encoding="utf-8") as fh:
            loaded_json = json.load(fh)
        assert loaded_json["week"] == "2026-10-19"
        assert len(loaded_json["themes"]) == 3

        with open(js_file, "r", encoding="utf-8") as fh:
            js_content = fh.read()
        assert js_content.startswith("// Auto-generated")
        assert "window.PULSE_DATA =" in js_content


class TestWebServer:
    """Test HTTP server routes and responses."""

    def test_server_status_endpoint(self):
        handler = MagicMock()
        handler.path = "/api/status"
        handler._send_json = MagicMock()

        GrowwPulseHandler.do_GET(handler)
        handler._send_json.assert_called_once()
        status_arg = handler._send_json.call_args[0][0]
        payload = handler._send_json.call_args[0][1]

        assert status_arg == HTTPStatus.OK
        assert payload["status"] == "healthy"
        assert "app" in payload

    def test_server_pulse_endpoint(self, tmp_path):
        handler = MagicMock()
        handler.path = "/api/pulse"
        handler._send_json = MagicMock()

        fake_pulse_file = tmp_path / "pulse.json"
        fake_pulse_file.write_text('{"week": "2026-10-19", "themes": []}', encoding="utf-8")

        with patch("src.web.server._PULSE_JSON_PATH", fake_pulse_file):
            GrowwPulseHandler.do_GET(handler)
            handler._send_json.assert_called_once()
            status_arg = handler._send_json.call_args[0][0]
            data = handler._send_json.call_args[0][1]
            assert status_arg == HTTPStatus.OK
            assert data["week"] == "2026-10-19"

    def test_server_run_pipeline_endpoint(self):
        handler = MagicMock()
        handler.path = "/api/run"
        handler._send_json = MagicMock()

        with patch("src.agent.core.run_pipeline") as mock_run:
            mock_run.return_value = {
                "success": True,
                "dashboard_data": {"week": "2026-10-19", "themes": []},
            }
            GrowwPulseHandler.do_POST(handler)
            handler._send_json.assert_called_once()
            status_arg = handler._send_json.call_args[0][0]
            resp = handler._send_json.call_args[0][1]
            assert status_arg == HTTPStatus.OK
            assert resp["success"] is True
