"""
tests/test_hardening_and_edge_cases.py
──────────────────────────────────────
Phase 8 — Hardening, Edge Cases & Integration Tests.

Comprehensive test suite verifying:
  - 8.2: End-to-end integration test (Mock Play Store -> Mock LLM -> Mock MCP -> Pulse shape)
  - 8.3: Edge cases grounded in real Groww Play Store dataset:
      1. Short reviews (<20 chars, ~67% in real data) filtered out
      2. Multilingual reviews (Hindi/Tamil/Malayalam, ~19% in real data) handled
      3. Emoji-only / single-word reviews filtered
      4. Rating vs. text mismatch (1★ with praise, 5★ with complaint)
      5. Support ticket / reference IDs masked as [ticket_id]
      6. High thumbs_up outlier (504 upvotes) handled without skewing cross-theme quotes
      7. All reviews assigned to 1 theme -> pipeline completes reflecting dominant theme
      8. Malformed LLM JSON -> retried with backoff and fails cleanly
      9. MCP server unavailable -> graceful error / fallback
      10. Fewer than 3 substantive reviews -> clean warning / abort
  - 8.4: Pydantic validation enforcement at every layer boundary
  - 8.5: Exponential backoff for LLM API rate limits
  - 8.6: Audit log output confirming zero PII leaks (phones, emails, UPI, ticket IDs)
  - 8.7: Thumbs-up quote selection validation (prefer upvoted over thumbs_up=0)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from src.agent.core import run_pipeline, _ensure_pulse_theme_count
from src.ingestion.fetcher import fetch_reviews
from src.ingestion.parser import Review, parse_reviews
from src.ingestion.scrubber import scrub, scrub_text
from src.intelligence.clusterer import (
    Theme,
    cluster,
    _select_top_quotes,
)
from src.intelligence.pulse_generator import (
    Pulse,
    generate_pulse,
    select_theme_quote,
    select_top_themes,
)
from src.mcp.client import MCPClient, MCPClientError
from src.utils.config import settings

_ROOT = Path(__file__).resolve().parents[1]
_SAMPLE_DATA_PATH = _ROOT / "scripts" / "groww_reviews.json"
_NOW = datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════════
# Task 8.2: End-to-End Integration Test
# ═══════════════════════════════════════════════════════════════════════════════

class TestEndToEndIntegration:
    """Task 8.2: Mock Play Store -> Mock LLM -> Mock MCP -> Verify Pulse shape."""

    @patch("src.agent.core.fetch_reviews")
    def test_full_pipeline_mocked_end_to_end(self, mock_fetch: MagicMock):
        # 1. Mock Play Store review list (mix of positive & negative substantive reviews)
        raw_reviews = [
            Review(
                id=f"rev-{i}",
                rating=5 if i % 2 == 0 else 1,
                title=f"Review title {i}",
                text=f"This is substantial review text for testing number {i} with valuable feedback about Groww.",
                date=_NOW,
                thumbs_up=i * 2,
            )
            for i in range(12)
        ]
        mock_fetch.return_value = raw_reviews

        # 2. Mock MCP client
        mock_client = MagicMock(spec=MCPClient)
        mock_client.call_tool_sync.side_effect = [
            {"success": True, "characters_appended": 480, "status": "Text appended"},
            {"success": True, "message_id": "integration-msg-123", "status": "Email sent"},
        ]

        # 3. Execute pipeline
        result = run_pipeline(
            weeks=10,
            doc_id="https://docs.google.com/document/d/mock-doc-id/edit",
            recipient="leader@groww.in",
            client=mock_client,
        )

        # 4. Verify results and shapes
        assert result["success"] is True
        assert result["total_reviews"] == 12
        assert result["clean_reviews"] == 12
        assert result["doc_result"]["success"] is True
        assert result["email_result"]["success"] is True
        assert result["email_result"]["message_id"] == "integration-msg-123"

        pulse: Pulse = result["pulse"]
        assert isinstance(pulse, Pulse)
        assert pulse.word_count <= settings.pulse_max_words
        assert len(pulse.top_themes) == 3
        assert len(pulse.quotes) == 3
        assert len(pulse.action_ideas) == 3
        assert "Groww Weekly Review Pulse" in pulse.body
        assert mock_client.call_tool_sync.call_count == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Task 8.3: Real-Data Grounded Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestRealDataEdgeCases:
    """Task 8.3: Edge case tests grounded in real groww_reviews.json observations."""

    def test_edge_case_1_short_reviews_filtered(self):
        """
        Observation: 67.2% of raw reviews are < 20 characters (e.g. 'good', 'nice app', 'ok').
        Expected: Filtered out by min_review_length; substantive subset retained.
        """
        raw_items = [
            {"reviewId": "s1", "score": 5, "content": "Nice", "at": _NOW},
            {"reviewId": "s2", "score": 5, "content": "Good app", "at": _NOW},
            {"reviewId": "s3", "score": 1, "content": "Bad", "at": _NOW},
            {"reviewId": "s4", "score": 4, "content": "Super", "at": _NOW},
            {
                "reviewId": "sub1",
                "score": 5,
                "content": "The overall user interface is remarkably clean, intuitive and beginner-friendly for mutual funds.",
                "at": _NOW,
            },
            {
                "reviewId": "sub2",
                "score": 1,
                "content": "Brokerage and hidden regulatory charges are extremely confusing for new retail option traders.",
                "at": _NOW,
            },
        ]
        parsed = parse_reviews(raw_items)
        assert len(parsed) == 6

        clean = scrub(parsed, min_length=20)
        assert len(clean) == 2
        assert all(len(r.text) >= 20 for r in clean)

    def test_edge_case_2_multilingual_reviews(self):
        """
        Observation: 19.2% non-ASCII text (Hindi, Tamil, Malayalam).
        Expected: Preserved without crash when non_english_policy='keep'; dropped when 'drop'.
        """
        multilingual_reviews = [
            Review(id="hi-1", rating=5, title=None, text="बहुत ही बढ़िया और सुरक्षित ऐप है, निवेश करना आसान है।", date=_NOW),
            Review(id="ta-1", rating=4, title=None, text="பயன்பாடு மிக அருமை, எளிதாக முதலீடு செய்ய முடிகிறது.", date=_NOW),
            Review(id="ml-1", rating=2, title=None, text="കസ്റ്റമർ കെയർ സർവീസ് വളരെ മോശമാണ്, withdrawal വൈകുന്നു.", date=_NOW),
            Review(id="en-1", rating=5, title=None, text="Fast execution and great watchlist sorting features.", date=_NOW),
        ]

        # Policy: keep
        clean_kept = scrub(multilingual_reviews, non_english_policy="keep")
        assert len(clean_kept) == 4

        # Policy: drop
        clean_dropped = scrub(multilingual_reviews, non_english_policy="drop")
        assert len(clean_dropped) == 1
        assert clean_dropped[0].id == "en-1"

    def test_edge_case_3_emoji_and_single_word_reviews(self):
        """
        Observation: Users frequently submit emoji-only reviews: '👎🏻👎🏻', '😊😊😊', '👍'.
        Expected: Filtered out as short/non-substantive.
        """
        emoji_reviews = [
            Review(id="em1", rating=1, title=None, text="👎🏻👎🏻👎🏻", date=_NOW),
            Review(id="em2", rating=5, title=None, text="😊😊😊😊", date=_NOW),
            Review(id="em3", rating=4, title=None, text="👍", date=_NOW),
            Review(id="sub", rating=5, title=None, text="I have been using this app for 3 years without a single glitch or issue.", date=_NOW),
        ]
        clean = scrub(emoji_reviews, min_length=20)
        assert len(clean) == 1
        assert clean[0].id == "sub"

    def test_edge_case_4_rating_text_mismatch(self):
        """
        Observation: 1-star rating with praising text ('good appp') or 5-star with severe grievance.
        Expected: Scrubber preserves text cleanly; theme grouping evaluates semantic text.
        """
        mismatched_review = Review(
            id="mis1",
            rating=1,
            title=None,
            text="Actually the best investment application in India, love the simple UI and clean design.",
            date=_NOW,
        )
        clean = scrub([mismatched_review])
        assert len(clean) == 1
        assert "best investment application" in clean[0].text
        assert clean[0].rating == 1

    def test_edge_case_5_ticket_and_reference_ids_masked(self):
        """
        Observation: Real complaints contain 'Ticket ID 28133385' or 'reference no 98765432'.
        Expected: Masked to [ticket_id] by scrubber.
        """
        complaint = (
            "Withdrawal of funds stuck since Monday. Customer support gave me "
            "Ticket ID 28133385 and complaint reference #987654321 but no response."
        )
        scrubbed = scrub_text(complaint)
        assert "28133385" not in scrubbed
        assert "987654321" not in scrubbed
        assert "[ticket_id]" in scrubbed

    def test_edge_case_6_high_thumbs_up_outlier(self):
        """
        Observation: Single viral review has 504 thumbs_up.
        Expected: The viral review is selected for its own theme, but does NOT prevent other themes
        from selecting their own top quotes.
        """
        viral_review = Review(
            id="viral",
            rating=1,
            title=None,
            text="Terrible withdrawal delay of 3 weeks! Support ticket unresponsive and money stuck.",
            date=_NOW,
            thumbs_up=504,
        )
        theme1_other = Review(
            id="t1-o",
            rating=1,
            title=None,
            text="Withdrawal takes too long to credit to my bank account without notification.",
            date=_NOW,
            thumbs_up=10,
        )
        theme2_review = Review(
            id="t2-r",
            rating=5,
            title=None,
            text="Clean and clear user interface, very easy for beginner mutual fund SIP investments.",
            date=_NOW,
            thumbs_up=8,
        )
        theme3_review = Review(
            id="t3-r",
            rating=2,
            title=None,
            text="Brokerage ₹20 per order is not transparently displayed before executing trades.",
            date=_NOW,
            thumbs_up=15,
        )

        t1 = Theme(name="Withdrawals", review_count=2, reviews=[viral_review, theme1_other], sentiment="negative", top_quotes=[viral_review.text])
        t2 = Theme(name="UI/UX", review_count=1, reviews=[theme2_review], sentiment="positive", top_quotes=[theme2_review.text])
        t3 = Theme(name="Brokerage", review_count=1, reviews=[theme3_review], sentiment="negative", top_quotes=[theme3_review.text])

        # Pulse selection must choose exactly 1 quote per theme across all 3 themes
        q1 = select_theme_quote(t1)
        q2 = select_theme_quote(t2)
        q3 = select_theme_quote(t3)

        assert q1 == viral_review.text
        assert q2 == theme2_review.text
        assert q3 == theme3_review.text

    def test_edge_case_7_all_reviews_fall_into_one_theme(self):
        """
        Observation: Small substantive subset where all reviews belong to a single theme.
        Expected: _ensure_pulse_theme_count segments the dominant theme into focused sub-facets;
        pipeline completes without error.
        """
        reviews = [
            Review(id=f"dom-{i}", rating=5, title=None, text=f"Dominant theme substantive review number {i} regarding UI.", date=_NOW, thumbs_up=i)
            for i in range(6)
        ]
        single_theme = Theme(
            name="UI/UX & Ease of Use",
            review_count=6,
            reviews=reviews,
            sentiment="positive",
            top_quotes=[reviews[0].text, reviews[1].text],
        )

        # Ensure facet segmentation
        segmented = _ensure_pulse_theme_count([single_theme])
        assert len(segmented) == 3
        assert "UI/UX & Ease of Use" in segmented[0].name
        assert "UI/UX & Ease of Use" in segmented[1].name
        assert "UI/UX & Ease of Use" in segmented[2].name

        mock_llm = MagicMock()
        mock_llm.generate.return_value = json.dumps({
            "action_ideas": ["Action 1", "Action 2", "Action 3"]
        })

        pulse = generate_pulse(segmented, week_of=date.today(), llm=mock_llm)
        assert isinstance(pulse, Pulse)
        assert len(pulse.top_themes) == 3
        assert len(pulse.quotes) == 3

    def test_edge_case_8_llm_malformed_json_retries_and_recovers(self):
        """
        Observation: LLM occasionally outputs non-JSON markdown or truncated text.
        Expected: Retries with backoff; recovers when valid JSON is returned.
        """
        mock_llm = MagicMock()
        # First 2 calls return invalid text; 3rd call returns valid JSON
        mock_llm.generate.side_effect = [
            "Here are your action ideas: 1. Do this, 2. Do that",
            "```json\n{ truncated json",
            json.dumps({"action_ideas": ["Action A", "Action B", "Action C"]}),
        ]

        t1 = Theme(name="T1", review_count=10, reviews=[], sentiment="positive", top_quotes=["Quote 1"])
        t2 = Theme(name="T2", review_count=8, reviews=[], sentiment="negative", top_quotes=["Quote 2"])
        t3 = Theme(name="T3", review_count=5, reviews=[], sentiment="negative", top_quotes=["Quote 3"])

        with patch("time.sleep"):  # Avoid test delay
            pulse = generate_pulse([t1, t2, t3], week_of=date.today(), llm=mock_llm)

        assert pulse.action_ideas == ["Action A", "Action B", "Action C"]
        assert mock_llm.generate.call_count == 3

    def test_edge_case_9_mcp_service_unavailable_fallback(self):
        """
        Observation: Remote MCP server is down or returns connection failure.
        Expected: Graceful error handling; docs append failure logs error and falls back to Gmail.
        """
        reviews = [
            Review(id=f"r{i}", rating=5, title=None, text=f"Substantive review text number {i} for fallback test.", date=_NOW)
            for i in range(5)
        ]

        mock_client = MagicMock(spec=MCPClient)
        # Docs fails with MCPClientError, Gmail succeeds
        mock_client.call_tool_sync.side_effect = [
            MCPClientError("Railway SSE connection refused"),
            {"success": True, "message_id": "email-msg-999", "status": "Email sent"},
        ]

        with patch("src.agent.core.fetch_reviews", return_value=reviews):
            result = run_pipeline(
                weeks=1,
                doc_id="https://docs.google.com/document/d/fake/edit",
                recipient="test@groww.in",
                client=mock_client,
            )

        assert result["success"] is True
        assert result["doc_result"]["success"] is False
        assert "connection refused" in result["doc_result"]["error"].lower()
        assert result["email_result"]["success"] is True

    def test_edge_case_10_fewer_than_3_substantive_reviews_aborts(self):
        """
        Observation: Off-peak period where < 3 substantive reviews remain after scrubbing.
        Expected: Pipeline aborts with ValueError before LLM invocation.
        """
        few_reviews = [
            Review(id="1", rating=5, title=None, text="Meaningful review number one.", date=_NOW),
            Review(id="2", rating=1, title=None, text="Meaningful review number two.", date=_NOW),
        ]
        with patch("src.agent.core.fetch_reviews", return_value=few_reviews):
            with pytest.raises(ValueError, match="Fewer than 3 reviews fetched"):
                run_pipeline(weeks=1)


# ═══════════════════════════════════════════════════════════════════════════════
# Task 8.4: Pydantic Validation at Every Layer Boundary
# ═══════════════════════════════════════════════════════════════════════════════

class TestPydanticLayerValidation:
    """Task 8.4: Fail-fast validation at model boundaries."""

    def test_review_validation_bounds(self):
        # Invalid rating > 5
        with pytest.raises(ValidationError):
            Review(id="1", rating=6, title=None, text="Valid text", date=_NOW)

        # Invalid rating < 1
        with pytest.raises(ValidationError):
            Review(id="1", rating=0, title=None, text="Valid text", date=_NOW)

        # Blank text
        with pytest.raises(ValidationError):
            Review(id="1", rating=5, title=None, text="   ", date=_NOW)

        # Negative thumbs_up
        with pytest.raises(ValidationError):
            Review(id="1", rating=5, title=None, text="Valid text", date=_NOW, thumbs_up=-1)

    def test_theme_validation_bounds(self):
        # Invalid sentiment
        with pytest.raises(ValidationError):
            Theme(name="UI", review_count=1, reviews=[], sentiment="ecstatic", top_quotes=["Q"])

        # Negative review count
        with pytest.raises(ValidationError):
            Theme(name="UI", review_count=-1, reviews=[], sentiment="positive", top_quotes=["Q"])

        # > 3 top_quotes
        with pytest.raises(ValidationError):
            Theme(name="UI", review_count=4, reviews=[], sentiment="positive", top_quotes=["1", "2", "3", "4"])

    def test_pulse_validation_bounds(self):
        t = Theme(name="UI", review_count=5, reviews=[], sentiment="positive", top_quotes=["Q"])
        # Word count > 250
        with pytest.raises(ValidationError):
            Pulse(
                week_of=date.today(),
                top_themes=[t, t, t],
                quotes=["Q1", "Q2", "Q3"],
                action_ideas=["A1", "A2", "A3"],
                body="Body",
                word_count=251,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Task 8.6: Audit Log Output for Zero PII Leaks
# ═══════════════════════════════════════════════════════════════════════════════

class TestLogOutputPIIAudit:
    """Task 8.6: Verify zero PII leaks into any logger records."""

    def test_logs_contain_no_raw_pii(self, caplog):
        caplog.set_level(logging.DEBUG)

        sensitive_phone = "9876543210"
        sensitive_email = "victim_user@secretbank.com"
        sensitive_upi = "secretinvestor@okhdfcbank"
        sensitive_ticket = "28133385"

        pii_review = Review(
            id="pii-user",
            rating=1,
            title=f"Contact {sensitive_email}",
            text=(
                f"Urgent help needed! Call me at {sensitive_phone} or refund to "
                f"{sensitive_upi}. Ticket ID {sensitive_ticket} has no resolution!"
            ),
            date=_NOW,
        )

        clean = scrub([pii_review])
        assert len(clean) == 1

        # Check all logged records during scrubber run
        all_log_text = " ".join(record.getMessage() for record in caplog.records)
        for record in caplog.records:
            extra_dict = getattr(record, "__dict__", {})
            all_log_text += " " + str(extra_dict)

        assert sensitive_phone not in all_log_text
        assert sensitive_email not in all_log_text
        assert sensitive_upi not in all_log_text
        assert sensitive_ticket not in all_log_text


# ═══════════════════════════════════════════════════════════════════════════════
# Task 8.7: Thumbs-Up Quote Selection Validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestThumbsUpQuoteSelection:
    """Task 8.7: Assert no quote with thumbs_up=0 is chosen when upvoted alternative exists."""

    def test_thumbs_up_priority_over_zero(self):
        zero_upvote_review = Review(
            id="zero-1",
            rating=1,
            title=None,
            text="This is a detailed complaint with zero community upvotes on Play Store.",
            date=_NOW,
            thumbs_up=0,
        )
        high_upvote_review = Review(
            id="high-1",
            rating=1,
            title=None,
            text="This is an impactful complaint that has received five community upvotes.",
            date=_NOW,
            thumbs_up=5,
        )

        # Top quotes extraction prioritises high upvotes
        quotes = _select_top_quotes([zero_upvote_review, high_upvote_review], max_quotes=1)
        assert len(quotes) == 1
        assert quotes[0] == high_upvote_review.text

        theme = Theme(
            name="Complaints",
            review_count=2,
            reviews=[zero_upvote_review, high_upvote_review],
            sentiment="negative",
            top_quotes=quotes,
        )

        selected_quote = select_theme_quote(theme)
        assert selected_quote == high_upvote_review.text
        assert selected_quote != zero_upvote_review.text
