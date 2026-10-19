"""
tests/test_clusterer.py
────────────────────────
Unit tests for Phase 4 — Theme Clustering.

All LLM calls are fully mocked — no real API calls are made.

Coverage
────────
  - Theme schema: validation (sentiment, quote count, review_count)
  - LLM client construction: Gemini, OpenAI, invalid provider
  - _extract_json: clean JSON, markdown-fenced JSON, missing JSON, malformed JSON
  - _select_top_quotes: thumbs_up prioritisation, length fallback, short-text filter
  - _infer_sentiment_from_ratings: positive / neutral / negative thresholds
  - _build_reviews_block: correct index offset formatting
  - _cluster_batch: normal response, strict mode, missing 'assignments' key
  - _merge_batch_results: single batch, multi-batch merge, over-cap consolidation
  - cluster() entry point:
      * Single batch, happy path → returns sorted Theme list
      * Multi-batch → themes merged correctly
      * Every review assigned to exactly one theme
      * Themes sorted by review_count descending
      * LLM returns > 5 themes → retry with strict prompt; cap enforced
      * LLM returns > 5 themes after all retries → ValueError raised
      * Empty review list → ValueError raised
      * LLM parse error → retries then raises RuntimeError
      * Fewer-than-max themes returned → valid output
      * thumbs_up-weighted quotes appear in top_quotes
      * All reviews assigned (no orphans)
      * Quote count ≤ 3 per theme
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest

from src.ingestion.parser import Review
from src.intelligence.clusterer import (
    Theme,
    GeminiClient,
    OpenAIClient,
    _build_llm_client,
    _extract_json,
    _select_top_quotes,
    _infer_sentiment_from_ratings,
    _build_reviews_block,
    _cluster_batch,
    _merge_batch_results,
    cluster,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures & helpers
# ═══════════════════════════════════════════════════════════════════════════════

_NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)


def _review(
    text: str,
    *,
    rating: int = 4,
    thumbs_up: int = 0,
    review_id: str = "r-001",
) -> Review:
    return Review(
        id=review_id,
        rating=rating,
        title=None,
        text=text,
        date=_NOW,
        thumbs_up=thumbs_up,
    )


def _make_reviews(n: int, rating: int = 4, thumbs_up: int = 0) -> list[Review]:
    """Create *n* unique dummy reviews."""
    return [
        _review(
            f"This is review number {i} about the Groww investment app experience",
            rating=rating,
            thumbs_up=thumbs_up,
            review_id=f"r-{i:03d}",
        )
        for i in range(n)
    ]


def _llm_response(assignments: list[dict], themes: list[dict]) -> str:
    """Build a clean JSON LLM response string."""
    import json
    return json.dumps({"assignments": assignments, "themes": themes})


def _mock_llm(response: str | list[str]) -> MagicMock:
    """Return a mock LLM client whose generate() returns *response*."""
    mock = MagicMock()
    if isinstance(response, str):
        mock.generate.return_value = response
    else:
        mock.generate.side_effect = response
    return mock


# ═══════════════════════════════════════════════════════════════════════════════
# Theme schema tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestThemeSchema:

    def test_valid_theme_constructs(self):
        reviews = _make_reviews(3)
        t = Theme(
            name="UI/UX & Ease of Use",
            review_count=3,
            reviews=reviews,
            sentiment="positive",
            top_quotes=["Great app", "Very smooth"],
        )
        assert t.name == "UI/UX & Ease of Use"
        assert t.review_count == 3
        assert t.sentiment == "positive"

    def test_sentiment_normalised_to_lowercase(self):
        t = Theme(
            name="Test", review_count=1,
            reviews=_make_reviews(1), sentiment="POSITIVE", top_quotes=[],
        )
        assert t.sentiment == "positive"

    def test_invalid_sentiment_raises(self):
        with pytest.raises(Exception, match="sentiment"):
            Theme(
                name="Test", review_count=1,
                reviews=_make_reviews(1), sentiment="happy", top_quotes=[],
            )

    def test_more_than_3_quotes_raises(self):
        with pytest.raises(Exception):
            Theme(
                name="Test", review_count=1,
                reviews=_make_reviews(1), sentiment="positive",
                top_quotes=["q1", "q2", "q3", "q4"],  # 4 — too many
            )

    def test_zero_review_count_valid(self):
        t = Theme(name="Empty", review_count=0, reviews=[], sentiment="neutral", top_quotes=[])
        assert t.review_count == 0


# ═══════════════════════════════════════════════════════════════════════════════
# _extract_json tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractJson:

    def test_clean_json_parsed(self):
        raw = '{"assignments": [], "themes": []}'
        result = _extract_json(raw)
        assert result == {"assignments": [], "themes": []}

    def test_json_with_markdown_fence(self):
        raw = "```json\n{\"assignments\": [], \"themes\": []}\n```"
        result = _extract_json(raw)
        assert "assignments" in result

    def test_json_with_prose_prefix(self):
        raw = "Sure, here is the result:\n{\"assignments\": [], \"themes\": []}"
        result = _extract_json(raw)
        assert "assignments" in result

    def test_json_with_prose_suffix(self):
        raw = '{\"assignments\": [], \"themes\": []}\n\nLet me know if you need changes.'
        result = _extract_json(raw)
        assert "themes" in result

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            _extract_json("This response has no JSON at all.")

    def test_malformed_json_raises(self):
        with pytest.raises(ValueError):
            _extract_json('{"assignments": [INVALID]}')

    def test_markdown_fence_without_language_tag(self):
        raw = "```\n{\"assignments\": [], \"themes\": []}\n```"
        result = _extract_json(raw)
        assert "assignments" in result


# ═══════════════════════════════════════════════════════════════════════════════
# _select_top_quotes tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSelectTopQuotes:

    def test_prefers_high_thumbs_up_reviews(self):
        low  = _review("This is a decent review of the app experience today", thumbs_up=0)
        high = _review("I love the updates coming from Groww team hard work shows", thumbs_up=504)
        quotes = _select_top_quotes([low, high])
        assert quotes[0] == high.text

    def test_falls_back_to_length_when_thumbs_up_equal(self):
        short = _review("Good app for trading daily", thumbs_up=0, review_id="r-short")
        long  = _review(
            "The brokerage charges are too high for small retail investors who trade frequently",
            thumbs_up=0, review_id="r-long",
        )
        quotes = _select_top_quotes([short, long])
        assert quotes[0] == long.text

    def test_max_3_quotes_returned(self):
        reviews = _make_reviews(10, thumbs_up=1)
        quotes = _select_top_quotes(reviews)
        assert len(quotes) <= 3

    def test_empty_reviews_returns_empty(self):
        assert _select_top_quotes([]) == []

    def test_very_short_reviews_skipped(self):
        """Reviews < 30 chars should be skipped in favour of longer ones."""
        short = _review("good", review_id="r-short")
        meaningful = _review(
            "The app is smooth and very beginner friendly I recommend it",
            review_id="r-long",
        )
        quotes = _select_top_quotes([short, meaningful])
        assert meaningful.text in quotes

    def test_all_short_reviews_fallback(self):
        """If all reviews are <30 chars, still return quotes (fallback path)."""
        reviews = [_review("ok", review_id=f"r-{i}") for i in range(3)]
        quotes = _select_top_quotes(reviews)
        assert len(quotes) <= 3   # may be shorter but shouldn't crash

    def test_thumbs_up_zero_still_returned_when_no_alternatives(self):
        r = _review("The UI is clean and easy to navigate for new investors", thumbs_up=0)
        quotes = _select_top_quotes([r])
        assert quotes == [r.text]


# ═══════════════════════════════════════════════════════════════════════════════
# _infer_sentiment_from_ratings tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestInferSentiment:

    def test_high_rating_positive(self):
        reviews = _make_reviews(3, rating=5)
        assert _infer_sentiment_from_ratings(reviews) == "positive"

    def test_medium_rating_neutral(self):
        reviews = _make_reviews(3, rating=3)
        assert _infer_sentiment_from_ratings(reviews) == "neutral"

    def test_low_rating_negative(self):
        reviews = _make_reviews(3, rating=1)
        assert _infer_sentiment_from_ratings(reviews) == "negative"

    def test_empty_returns_neutral(self):
        assert _infer_sentiment_from_ratings([]) == "neutral"

    def test_boundary_4_positive(self):
        reviews = _make_reviews(1, rating=4)
        assert _infer_sentiment_from_ratings(reviews) == "positive"

    def test_boundary_2_negative(self):
        reviews = _make_reviews(1, rating=2)
        assert _infer_sentiment_from_ratings(reviews) == "negative"


# ═══════════════════════════════════════════════════════════════════════════════
# _build_reviews_block tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildReviewsBlock:

    def test_zero_offset(self):
        reviews = _make_reviews(2)
        block = _build_reviews_block(reviews, offset=0)
        assert "[0]" in block
        assert "[1]" in block

    def test_non_zero_offset(self):
        reviews = _make_reviews(2)
        block = _build_reviews_block(reviews, offset=50)
        assert "[50]" in block
        assert "[51]" in block

    def test_rating_included(self):
        reviews = [_review("Test review text here", rating=1)]
        block = _build_reviews_block(reviews)
        assert "rating: 1/5" in block

    def test_text_included(self):
        reviews = [_review("Very smooth and user friendly application")]
        block = _build_reviews_block(reviews)
        assert "Very smooth and user friendly application" in block


# ═══════════════════════════════════════════════════════════════════════════════
# _cluster_batch tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestClusterBatch:

    def _make_valid_response(self, reviews: list[Review]) -> str:
        assignments = [{"review_index": i, "theme_name": "UI/UX"} for i in range(len(reviews))]
        themes = [{"name": "UI/UX", "sentiment": "positive"}]
        return _llm_response(assignments, themes)

    def test_valid_batch_response_parsed(self):
        reviews = _make_reviews(3)
        llm = _mock_llm(self._make_valid_response(reviews))
        result = _cluster_batch(reviews, llm, max_themes=5)
        assert "assignments" in result
        assert len(result["assignments"]) == 3

    def test_strict_mode_uses_different_prompt(self):
        reviews = _make_reviews(2)
        llm = _mock_llm(self._make_valid_response(reviews))
        _cluster_batch(reviews, llm, max_themes=5, strict=True)
        prompt_used = llm.generate.call_args[0][0]
        assert "CRITICAL CONSTRAINT" in prompt_used

    def test_normal_mode_prompt_content(self):
        reviews = _make_reviews(2)
        llm = _mock_llm(self._make_valid_response(reviews))
        _cluster_batch(reviews, llm, max_themes=5, strict=False)
        prompt_used = llm.generate.call_args[0][0]
        assert "product analyst" in prompt_used
        assert "CRITICAL CONSTRAINT" not in prompt_used

    def test_missing_assignments_key_raises(self):
        reviews = _make_reviews(2)
        llm = _mock_llm('{"themes": [{"name": "UI", "sentiment": "positive"}]}')
        with pytest.raises(ValueError, match="assignments"):
            _cluster_batch(reviews, llm, max_themes=5)

    def test_malformed_json_raises(self):
        reviews = _make_reviews(2)
        llm = _mock_llm("This is not JSON at all")
        with pytest.raises(ValueError):
            _cluster_batch(reviews, llm, max_themes=5)

    def test_offset_in_prompt(self):
        reviews = _make_reviews(3)
        llm = _mock_llm(self._make_valid_response(reviews))
        _cluster_batch(reviews, llm, max_themes=5, offset=50)
        prompt_used = llm.generate.call_args[0][0]
        assert "[50]" in prompt_used


# ═══════════════════════════════════════════════════════════════════════════════
# _merge_batch_results tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestMergeBatchResults:

    def test_single_batch_merged_correctly(self):
        reviews = _make_reviews(4)
        result = {
            "assignments": [
                {"review_index": 0, "theme_name": "UI/UX"},
                {"review_index": 1, "theme_name": "UI/UX"},
                {"review_index": 2, "theme_name": "Brokerage Fees"},
                {"review_index": 3, "theme_name": "Customer Service"},
            ],
            "themes": [],
        }
        theme_map = _merge_batch_results([(reviews, result)], max_themes=5)
        assert "UI/UX" in theme_map
        assert len(theme_map["UI/UX"]) == 2
        assert len(theme_map["Brokerage Fees"]) == 1

    def test_multi_batch_merged(self):
        batch1 = _make_reviews(2, review_id_offset=0)
        batch2 = _make_reviews(2, review_id_offset=2)
        result1 = {
            "assignments": [
                {"review_index": 0, "theme_name": "UI/UX"},
                {"review_index": 1, "theme_name": "UI/UX"},
            ],
            "themes": [],
        }
        result2 = {
            "assignments": [
                {"review_index": 0, "theme_name": "UI/UX"},
                {"review_index": 1, "theme_name": "Brokerage"},
            ],
            "themes": [],
        }
        theme_map = _merge_batch_results([(batch1, result1), (batch2, result2)], max_themes=5)
        assert "UI/UX" in theme_map
        assert len(theme_map["UI/UX"]) == 3

    def test_over_cap_consolidates(self):
        """When batches produce more themes than max_themes, smallest are merged."""
        reviews = _make_reviews(6)
        result = {
            "assignments": [
                {"review_index": i, "theme_name": f"Theme{i}"} for i in range(6)
            ],
            "themes": [],
        }
        theme_map = _merge_batch_results([(reviews, result)], max_themes=3)
        assert len(theme_map) <= 3

    def test_empty_batches_returns_empty(self):
        theme_map = _merge_batch_results([], max_themes=5)
        assert theme_map == {}


# _make_reviews override for multi-batch test
def _make_reviews(n: int, rating: int = 4, thumbs_up: int = 0, review_id_offset: int = 0) -> list[Review]:
    return [
        Review(
            id=f"r-{i + review_id_offset:03d}",
            rating=rating,
            title=None,
            text=f"This is review number {i + review_id_offset} about the Groww app experience",
            date=_NOW,
            thumbs_up=thumbs_up,
        )
        for i in range(n)
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# cluster() — main entry point tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCluster:
    """
    All LLM calls are mocked. Tests exercise the full cluster() pipeline.
    """

    def _make_llm_response(
        self,
        reviews: list[Review],
        theme_assignments: dict[int, str],
        themes_meta: list[dict] | None = None,
    ) -> str:
        """Build a JSON LLM response assigning reviews to themes."""
        assignments = [
            {"review_index": i, "theme_name": theme_assignments.get(i, "General")}
            for i in range(len(reviews))
        ]
        if themes_meta is None:
            theme_names = list(set(theme_assignments.values()))
            themes_meta = [
                {"name": name, "sentiment": "positive"} for name in theme_names
            ]
        return _llm_response(assignments, themes_meta)

    # ── Happy path ────────────────────────────────────────────────────────────

    @patch("src.intelligence.clusterer.time.sleep")
    def test_cluster_returns_sorted_themes(self, mock_sleep):
        reviews = _make_reviews(5)
        # Theme A gets 3 reviews, Theme B gets 2 — A should come first
        assignments = {0: "Theme A", 1: "Theme A", 2: "Theme A", 3: "Theme B", 4: "Theme B"}
        llm = _mock_llm(self._make_llm_response(reviews, assignments))

        themes = cluster(reviews, llm=llm, max_themes=5, batch_size=10, max_retries=3)

        assert themes[0].name == "Theme A"
        assert themes[0].review_count == 3
        assert themes[1].name == "Theme B"
        assert themes[1].review_count == 2

    @patch("src.intelligence.clusterer.time.sleep")
    def test_every_review_assigned_to_exactly_one_theme(self, mock_sleep):
        reviews = _make_reviews(6)
        assignments = {0: "A", 1: "A", 2: "B", 3: "B", 4: "C", 5: "C"}
        llm = _mock_llm(self._make_llm_response(reviews, assignments))

        themes = cluster(reviews, llm=llm, max_themes=5, batch_size=10, max_retries=3)

        total_assigned = sum(t.review_count for t in themes)
        assert total_assigned == len(reviews)

    @patch("src.intelligence.clusterer.time.sleep")
    def test_cluster_count_lte_max_themes(self, mock_sleep):
        reviews = _make_reviews(10)
        assignments = {i: f"Theme{i % 3}" for i in range(10)}
        llm = _mock_llm(self._make_llm_response(reviews, assignments))

        themes = cluster(reviews, llm=llm, max_themes=5, batch_size=10, max_retries=3)

        assert len(themes) <= 5

    @patch("src.intelligence.clusterer.time.sleep")
    def test_single_theme_returned(self, mock_sleep):
        reviews = _make_reviews(4)
        assignments = {i: "UI/UX" for i in range(4)}
        llm = _mock_llm(self._make_llm_response(reviews, assignments))

        themes = cluster(reviews, llm=llm, max_themes=5, batch_size=10, max_retries=3)

        assert len(themes) == 1
        assert themes[0].name == "UI/UX"
        assert themes[0].review_count == 4

    # ── Quotes ────────────────────────────────────────────────────────────────

    @patch("src.intelligence.clusterer.time.sleep")
    def test_top_quotes_present_and_capped(self, mock_sleep):
        reviews = _make_reviews(10, thumbs_up=1)
        assignments = {i: "Brokerage Charges" for i in range(10)}
        llm = _mock_llm(self._make_llm_response(reviews, assignments))

        themes = cluster(reviews, llm=llm, max_themes=5, batch_size=10, max_retries=3)

        assert all(len(t.top_quotes) <= 3 for t in themes)

    @patch("src.intelligence.clusterer.time.sleep")
    def test_high_thumbs_up_review_preferred_in_quotes(self, mock_sleep):
        """The review with thumbs_up=500 should appear as the first quote."""
        viral = Review(
            id="r-viral",
            rating=5,
            title=None,
            text="I love the updates from Groww Team your hard work really shows so much",
            date=_NOW,
            thumbs_up=500,
        )
        regular = _review(
            "The app works fine for daily trading activities I recommend it",
            thumbs_up=0,
            review_id="r-regular",
        )
        reviews = [regular, viral]
        assignments = {0: "Positive Experience", 1: "Positive Experience"}
        llm = _mock_llm(self._make_llm_response(reviews, assignments))

        themes = cluster(reviews, llm=llm, max_themes=5, batch_size=10, max_retries=3)

        assert len(themes) == 1
        assert themes[0].top_quotes[0] == viral.text

    # ── Sentiment ─────────────────────────────────────────────────────────────

    @patch("src.intelligence.clusterer.time.sleep")
    def test_sentiment_from_llm_used(self, mock_sleep):
        reviews = _make_reviews(3)
        assignments = {0: "Brokerage Fees", 1: "Brokerage Fees", 2: "Brokerage Fees"}
        themes_meta = [{"name": "Brokerage Fees", "sentiment": "negative"}]
        llm = _mock_llm(self._make_llm_response(reviews, assignments, themes_meta))

        themes = cluster(reviews, llm=llm, max_themes=5, batch_size=10, max_retries=3)

        assert themes[0].sentiment == "negative"

    @patch("src.intelligence.clusterer.time.sleep")
    def test_sentiment_fallback_from_ratings(self, mock_sleep):
        """When LLM returns invalid sentiment, fallback to rating-based inference."""
        reviews = _make_reviews(3, rating=5)
        assignments = {i: "Happy Users" for i in range(3)}
        # LLM returns invalid sentiment → should fall back to rating inference
        themes_meta = [{"name": "Happy Users", "sentiment": "ecstatic"}]
        llm = _mock_llm(self._make_llm_response(reviews, assignments, themes_meta))

        themes = cluster(reviews, llm=llm, max_themes=5, batch_size=10, max_retries=3)

        # rating=5 → positive (rating-based fallback)
        assert themes[0].sentiment == "positive"

    # ── Retry & error handling ────────────────────────────────────────────────

    @patch("src.intelligence.clusterer.time.sleep")
    def test_empty_reviews_raises_value_error(self, mock_sleep):
        llm = _mock_llm("{}")
        with pytest.raises(ValueError, match="empty review list"):
            cluster([], llm=llm, max_themes=5, batch_size=10, max_retries=3)

    @patch("src.intelligence.clusterer.time.sleep")
    def test_llm_parse_error_retries_then_raises(self, mock_sleep):
        """All attempts return malformed JSON → RuntimeError after max_retries."""
        reviews = _make_reviews(3)
        llm = _mock_llm("not json at all, sorry")

        with pytest.raises(RuntimeError, match="Clustering failed"):
            cluster(reviews, llm=llm, max_themes=5, batch_size=10, max_retries=2)

        assert llm.generate.call_count == 2

    @patch("src.intelligence.clusterer.time.sleep")
    def test_too_many_themes_retries_with_strict_prompt(self, mock_sleep):
        """
        First attempt returns 6 themes (over cap of 5).
        Second attempt (strict) returns 4 themes → success.
        """
        reviews = _make_reviews(6)

        # First response: 6 themes (over cap)
        over_cap_assignments = {i: f"Theme{i}" for i in range(6)}
        over_cap_themes = [{"name": f"Theme{i}", "sentiment": "positive"} for i in range(6)]
        first_response = _llm_response(
            [{"review_index": i, "theme_name": f"Theme{i}"} for i in range(6)],
            over_cap_themes,
        )

        # Second response (strict): 3 themes (under cap)
        ok_assignments = {0: "A", 1: "A", 2: "B", 3: "B", 4: "C", 5: "C"}
        second_response = self._make_llm_response(reviews, ok_assignments)

        llm = _mock_llm([first_response, second_response])

        themes = cluster(reviews, llm=llm, max_themes=5, batch_size=10, max_retries=3)

        assert len(themes) <= 5
        assert llm.generate.call_count == 2

        # Second call must use strict prompt
        second_prompt = llm.generate.call_args_list[1][0][0]
        assert "CRITICAL CONSTRAINT" in second_prompt

    def _make_llm_response(
        self,
        reviews,
        theme_assignments,
        themes_meta=None,
    ):
        assignments = [
            {"review_index": i, "theme_name": theme_assignments.get(i, "General")}
            for i in range(len(reviews))
        ]
        if themes_meta is None:
            theme_names = list(set(theme_assignments.values()))
            themes_meta = [{"name": n, "sentiment": "positive"} for n in theme_names]
        return _llm_response(assignments, themes_meta)

    @patch("src.intelligence.clusterer.time.sleep")
    def test_too_many_themes_all_retries_exhausted_raises(self, mock_sleep):
        """All retries still return > max_themes → ValueError raised."""
        reviews = _make_reviews(6)
        # Each batch returns 6 themes, all over the cap of 3
        over_cap_response = _llm_response(
            [{"review_index": i, "theme_name": f"T{i}"} for i in range(6)],
            [{"name": f"T{i}", "sentiment": "positive"} for i in range(6)],
        )
        llm = _mock_llm(over_cap_response)

        with pytest.raises(ValueError, match="themes after"):
            cluster(reviews, llm=llm, max_themes=2, batch_size=10, max_retries=2)

    # ── Multi-batch ───────────────────────────────────────────────────────────

    @patch("src.intelligence.clusterer.time.sleep")
    def test_multi_batch_all_reviews_assigned(self, mock_sleep):
        """With batch_size=3, 6 reviews → 2 batches; all 6 assigned."""
        reviews = _make_reviews(6)

        def side_effect(prompt: str) -> str:
            # Both batches return same theme
            return _llm_response(
                [{"review_index": i, "theme_name": "UI/UX"} for i in range(3)],
                [{"name": "UI/UX", "sentiment": "positive"}],
            )

        llm = MagicMock()
        llm.generate.side_effect = side_effect

        themes = cluster(reviews, llm=llm, max_themes=5, batch_size=3, max_retries=3)

        total = sum(t.review_count for t in themes)
        assert total == 6
        assert llm.generate.call_count == 2   # 2 batches

    @patch("src.intelligence.clusterer.time.sleep")
    def test_sleep_called_between_batches(self, mock_sleep):
        """A short sleep is added between batches to respect rate limits."""
        reviews = _make_reviews(6)

        def side_effect(prompt: str) -> str:
            return _llm_response(
                [{"review_index": i, "theme_name": "UI"} for i in range(3)],
                [{"name": "UI", "sentiment": "positive"}],
            )

        llm = MagicMock()
        llm.generate.side_effect = side_effect

        cluster(reviews, llm=llm, max_themes=5, batch_size=3, max_retries=3)

        # sleep called once (between 2 batches)
        assert mock_sleep.call_count >= 1

    # ── Real Groww data scenarios ─────────────────────────────────────────────

    @patch("src.intelligence.clusterer.time.sleep")
    def test_groww_expected_themes(self, mock_sleep):
        """
        Simulate the 5 expected theme clusters from real groww_reviews.json analysis.
        Verify Theme objects have correct names, sentiments, and counts.
        """
        theme_names = [
            "UI/UX & Ease of Use",
            "Brokerage Charges & Fees",
            "Customer Service & Withdrawals",
            "App Performance & Bugs",
            "Features & Missing Functionality",
        ]
        # Create reviews: 40 for UI, 20 for Brokerage, 15 for CS, 10 for Perf, 5 for Features
        counts = [40, 20, 15, 10, 5]
        all_reviews = []
        assignments = {}
        themes_meta = []
        sentiments = ["positive", "negative", "negative", "negative", "neutral"]

        for name, count, sentiment in zip(theme_names, counts, sentiments):
            for _ in range(count):
                idx = len(all_reviews)
                assignments[idx] = name
                all_reviews.append(_review(
                    f"Review about {name} number {idx}",
                    review_id=f"r-{idx:03d}",
                ))
            themes_meta.append({"name": name, "sentiment": sentiment})

        llm_resp = _llm_response(
            [{"review_index": i, "theme_name": assignments[i]} for i in range(len(all_reviews))],
            themes_meta,
        )
        llm = _mock_llm(llm_resp)

        themes = cluster(all_reviews, llm=llm, max_themes=5, batch_size=200, max_retries=3)

        assert len(themes) == 5
        assert themes[0].name == "UI/UX & Ease of Use"
        assert themes[0].review_count == 40
        assert themes[0].sentiment == "positive"
        assert themes[1].name == "Brokerage Charges & Fees"
        assert themes[-1].name == "Features & Missing Functionality"
        assert themes[-1].review_count == 5

    @patch("src.intelligence.clusterer.time.sleep")
    def test_all_reviews_one_theme(self, mock_sleep):
        """Edge case: all reviews fall into one theme — valid output."""
        reviews = _make_reviews(10)
        assignments = {i: "General App Feedback" for i in range(10)}
        llm = _mock_llm(self._make_llm_response(reviews, assignments))

        themes = cluster(reviews, llm=llm, max_themes=5, batch_size=10, max_retries=3)

        assert len(themes) == 1
        assert themes[0].review_count == 10

    @patch("src.intelligence.clusterer.time.sleep")
    def test_mixed_language_reviews_handled(self, mock_sleep):
        """Cluster must work even when some reviews contain non-ASCII (Hindi) text."""
        reviews = [
            _review("The app is smooth and very beginner friendly highly recommended"),
            _review("बहुत अच्छा ऐप है, आसानी से निवेश कर सकते हैं"),  # Hindi
            _review("Brokerage charges are way too high for small retail investors"),
            _review("ഗ്രോ ആപ്പ് നല്ലതാണ് but customer service is very slow"),  # Malayalam + English
        ]
        assignments = {
            0: "UI/UX",
            1: "UI/UX",
            2: "Brokerage Fees",
            3: "Customer Service",
        }
        llm = _mock_llm(self._make_llm_response(reviews, assignments))

        themes = cluster(reviews, llm=llm, max_themes=5, batch_size=10, max_retries=3)

        assert len(themes) == 3
        total = sum(t.review_count for t in themes)
        assert total == 4


# ═══════════════════════════════════════════════════════════════════════════════
# LLM client construction tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildLLMClient:

    def test_unknown_provider_raises(self):
        with patch("src.intelligence.clusterer.settings") as mock_settings:
            mock_settings.llm_provider = "unknown_provider"
            with pytest.raises(ValueError, match="Unknown llm_provider"):
                _build_llm_client()

    def test_gemini_missing_api_key_raises(self):
        with patch("src.intelligence.clusterer.settings") as mock_settings:
            mock_settings.llm_provider = "gemini"
            mock_settings.gemini_api_key = ""
            with pytest.raises(ValueError, match="GEMINI_API_KEY"):
                _build_llm_client()

    def test_openai_missing_api_key_raises(self):
        with patch("src.intelligence.clusterer.settings") as mock_settings:
            mock_settings.llm_provider = "openai"
            mock_settings.openai_api_key = ""
            with pytest.raises(ValueError, match="OPENAI_API_KEY"):
                _build_llm_client()
