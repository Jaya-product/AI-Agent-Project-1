"""
tests/test_agent_core.py
────────────────────────
Unit tests for Phase 7 — Agent Core Orchestration.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from src.agent.core import run_pipeline
from src.mcp.client import MCPClientError


@pytest.fixture
def mock_clean_reviews():
    from src.ingestion.parser import Review
    from datetime import datetime, timezone
    return [
        Review(id=f"r{i}", rating=5 if i % 2 == 0 else 1, title="Review", text=f"This is meaningful review number {i} with sufficient text length.", date=datetime.now(timezone.utc), thumbs_up=i)
        for i in range(10)
    ]


class TestAgentCore:
    @patch("src.agent.core.fetch_reviews")
    def test_run_pipeline_dry_run(self, mock_fetch: MagicMock, mock_clean_reviews: list) -> None:
        mock_fetch.return_value = mock_clean_reviews
        result = run_pipeline(dry_run=True)

        assert result["success"] is True
        assert result["pulse"] is not None
        assert result["doc_result"] is None
        assert result["email_result"] is None
        assert result["total_reviews"] == 10

    @patch("src.agent.core._load_sample_reviews")
    def test_run_pipeline_sample_data_with_mcp(self, mock_load: MagicMock, mock_clean_reviews: list) -> None:
        mock_load.return_value = mock_clean_reviews
        mock_client = MagicMock()
        mock_client.call_tool_sync.side_effect = [
            {"success": True, "characters_appended": 500, "status": "Text appended"},
            {"success": True, "message_id": "msg-xyz", "status": "Email sent"},
        ]

        result = run_pipeline(
            sample_data=True,
            doc_id="https://docs.google.com/document/d/123/edit",
            recipient="test@example.com",
            client=mock_client,
        )

        assert result["success"] is True
        assert result["doc_result"]["success"] is True
        assert result["email_result"]["success"] is True
        assert result["email_result"]["message_id"] == "msg-xyz"
        assert mock_client.call_tool_sync.call_count == 2

    @patch("src.agent.core.fetch_reviews")
    def test_run_pipeline_insufficient_reviews_raises(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = []
        with patch("src.agent.core._SAMPLE_REVIEWS_PATH") as mock_path:
            mock_path.exists.return_value = False
            with pytest.raises(ValueError) as exc:
                run_pipeline(weeks=1)
            assert "No reviews found" in str(exc.value)

    @patch("src.agent.core._load_sample_reviews")
    def test_run_pipeline_mcp_doc_failure_graceful_fallback(self, mock_load: MagicMock, mock_clean_reviews: list) -> None:
        mock_load.return_value = mock_clean_reviews
        mock_client = MagicMock()
        mock_client.call_tool_sync.side_effect = [
            MCPClientError("Docs service temporarily down"),
            {"success": True, "message_id": "fallback-inline-email-id", "status": "Email sent"},
        ]

        result = run_pipeline(
            sample_data=True,
            doc_id="https://docs.google.com/document/d/123/edit",
            recipient="test@example.com",
            client=mock_client,
        )

        assert result["success"] is True
        assert result["doc_result"]["success"] is False
        assert "Docs service temporarily down" in result["doc_result"]["error"]
        assert result["email_result"]["success"] is True
        assert result["email_result"]["message_id"] == "fallback-inline-email-id"
        assert mock_client.call_tool_sync.call_count == 2

    @patch("src.agent.core._load_sample_reviews")
    def test_run_pipeline_fewer_than_3_reviews_raises(self, mock_load: MagicMock, mock_clean_reviews: list) -> None:
        mock_load.return_value = mock_clean_reviews[:2]
        with pytest.raises(ValueError) as exc:
            run_pipeline(sample_data=True)
        assert "Fewer than 3 reviews" in str(exc.value)

    @patch("src.agent.core.cluster")
    @patch("src.agent.core._load_sample_reviews")
    def test_run_pipeline_zero_themes_raises(self, mock_load: MagicMock, mock_cluster: MagicMock, mock_clean_reviews: list) -> None:
        mock_load.return_value = mock_clean_reviews
        mock_cluster.return_value = []
        with patch("src.agent.core.settings.gemini_api_key", "mock-key"):
            with pytest.raises(ValueError) as exc:
                run_pipeline(sample_data=True)
            assert "0 themes" in str(exc.value)

    @patch("src.agent.core.run_pipeline")
    def test_cli_main_dry_run(self, mock_pipeline: MagicMock) -> None:
        from src.agent.core import main
        from src.intelligence.pulse_generator import Pulse
        from src.intelligence.clusterer import Theme
        from datetime import date

        mock_theme = Theme(name="UI", review_count=5, reviews=[], sentiment="positive", top_quotes=["Quote"])
        mock_pulse = Pulse(
            week_of=date.today(),
            top_themes=[mock_theme, mock_theme, mock_theme],
            quotes=["q1", "q2", "q3"],
            action_ideas=["a1", "a2", "a3"],
            body="Short test pulse",
            word_count=3,
        )
        mock_pipeline.return_value = {
            "success": True,
            "pulse": mock_pulse,
            "dashboard_data": {"themes": [1, 2, 3], "quotes": [1, 2, 3], "actions": [1, 2, 3]},
            "total_reviews": 10,
            "clean_reviews": 8,
            "doc_result": None,
            "email_result": None,
        }

        with patch("sys.argv", ["core.py", "--dry-run", "--sample-data"]):
            main()

        mock_pipeline.assert_called_once_with(
            weeks=None,
            dry_run=True,
            doc_id=None,
            recipient=None,
            sample_data=True,
        )
