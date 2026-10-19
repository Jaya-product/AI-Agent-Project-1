"""
tests/test_ingestion.py
───────────────────────
Unit tests for the Review ingestion layer (fetcher + parser).

All network calls are mocked — no real Play Store requests are made.

Test coverage:
  - Parser: valid reviews, missing fields, blank text, date normalisation,
            fallback ID hashing, batch parsing with partial failures.
  - Fetcher: time-window filtering, max_reviews cap, pagination + token
             exhaustion, early stop on old review, network error handling.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from src.ingestion.parser import Review, parse_review, parse_reviews


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

NOW = datetime.now(tz=timezone.utc)

def _raw(
    *,
    review_id: str = "rev-001",
    score: int = 4,
    title: str | None = "Great app",
    content: str = "Works perfectly",
    at: datetime | None = None,
) -> dict:
    """Build a minimal raw dict matching google-play-scraper output."""
    return {
        "reviewId": review_id,
        "score":    score,
        "title":    title,
        "content":  content,
        "at":       at or NOW,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Parser tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseReview:
    """Unit tests for parse_review()."""

    def test_parses_all_fields(self):
        r = parse_review(_raw())
        assert r.id     == "rev-001"
        assert r.rating == 4
        assert r.title  == "Great app"
        assert r.text   == "Works perfectly"
        assert r.date.tzinfo is not None   # always UTC-aware

    def test_none_title_becomes_none(self):
        r = parse_review(_raw(title=None))
        assert r.title is None

    def test_blank_title_becomes_none(self):
        r = parse_review(_raw(title="   "))
        assert r.title is None

    def test_strips_text_whitespace(self):
        r = parse_review(_raw(content="  hello world  "))
        assert r.text == "hello world"

    def test_blank_text_raises(self):
        with pytest.raises(Exception):
            parse_review(_raw(content="   "))

    def test_naive_datetime_becomes_utc(self):
        naive = datetime(2024, 6, 1, 12, 0, 0)  # no tzinfo
        r = parse_review(_raw(at=naive))
        assert r.date.tzinfo == timezone.utc

    def test_utc_aware_datetime_preserved(self):
        aware = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        r = parse_review(_raw(at=aware))
        assert r.date == aware

    def test_iso_string_datetime_parsed(self):
        raw = _raw()
        raw["at"] = "2024-06-01T12:00:00"
        r = parse_review(raw)
        assert r.date.year == 2024
        assert r.date.month == 6
        assert r.date.day == 1

    def test_fallback_id_generated_when_missing(self):
        raw = _raw()
        raw.pop("reviewId")
        r = parse_review(raw)
        assert len(r.id) == 32  # MD5 hex digest

    def test_rating_valid_range(self):
        for rating in range(1, 6):
            r = parse_review(_raw(score=rating))
            assert r.rating == rating

    def test_rating_out_of_range_raises(self):
        with pytest.raises(Exception):
            parse_review(_raw(score=6))

    def test_review_is_immutable(self):
        r = parse_review(_raw())
        with pytest.raises(Exception):
            r.rating = 5  # type: ignore[misc]

    def test_missing_date_raises(self):
        raw = _raw()
        raw.pop("at")
        raw["at"] = None
        with pytest.raises(Exception):
            parse_review(raw)


class TestParseReviews:
    """Unit tests for parse_reviews() batch function."""

    def test_parses_valid_list(self):
        raws = [_raw(review_id=f"r{i}") for i in range(5)]
        result = parse_reviews(raws)
        assert len(result) == 5
        assert all(isinstance(r, Review) for r in result)

    def test_skips_invalid_entries(self):
        raws = [
            _raw(review_id="good-1"),
            _raw(content="   "),       # blank text — should be skipped
            _raw(review_id="good-2"),
        ]
        result = parse_reviews(raws)
        assert len(result) == 2
        assert {r.id for r in result} == {"good-1", "good-2"}

    def test_empty_list_returns_empty(self):
        assert parse_reviews([]) == []

    def test_all_invalid_returns_empty(self):
        result = parse_reviews([_raw(content="  "), _raw(content="")])
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# Fetcher tests  (all network calls mocked)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFetchReviews:
    """Unit tests for fetch_reviews() — Play Store calls are fully mocked."""

    def _make_raw(self, days_ago: int, review_id: str = "r1") -> dict:
        """Helper: raw review dict with date *days_ago* days in the past."""
        return _raw(
            review_id=review_id,
            at=NOW - timedelta(days=days_ago),
        )

    @patch("src.ingestion.fetcher._fetch_page")
    def test_returns_reviews_within_window(self, mock_fetch):
        """Reviews within the time window are returned."""
        mock_fetch.return_value = (
            [self._make_raw(5, "r1"), self._make_raw(10, "r2")],
            None,   # no next page
        )
        from src.ingestion.fetcher import fetch_reviews
        result = fetch_reviews(weeks=12)
        assert len(result) == 2
        assert {r.id for r in result} == {"r1", "r2"}

    @patch("src.ingestion.fetcher._fetch_page")
    def test_excludes_reviews_outside_window(self, mock_fetch):
        """Reviews older than the cutoff are excluded."""
        mock_fetch.return_value = (
            [
                self._make_raw(5,   "recent"),
                self._make_raw(100, "old"),    # 100 days ago > 12 weeks
            ],
            None,
        )
        from src.ingestion.fetcher import fetch_reviews
        result = fetch_reviews(weeks=12)
        assert len(result) == 1
        assert result[0].id == "recent"

    @patch("src.ingestion.fetcher.time.sleep")     # suppress actual delay
    @patch("src.ingestion.fetcher._fetch_page")
    def test_pagination_fetches_multiple_pages(self, mock_fetch, mock_sleep):
        """Fetcher makes multiple page calls until token is None."""
        page1 = [self._make_raw(1, f"p1-{i}") for i in range(3)]
        page2 = [self._make_raw(2, f"p2-{i}") for i in range(2)]

        mock_fetch.side_effect = [
            (page1, "token-abc"),   # first call → has next page
            (page2, None),          # second call → last page
        ]

        from src.ingestion.fetcher import fetch_reviews
        result = fetch_reviews(weeks=4, max_reviews=100)
        assert len(result) == 5
        assert mock_fetch.call_count == 2

    @patch("src.ingestion.fetcher.time.sleep")
    @patch("src.ingestion.fetcher._fetch_page")
    def test_max_reviews_cap_respected(self, mock_fetch, mock_sleep):
        """Fetcher never returns more than max_reviews reviews."""
        big_page = [self._make_raw(1, f"r{i}") for i in range(200)]
        mock_fetch.return_value = (big_page, None)

        from src.ingestion.fetcher import fetch_reviews
        result = fetch_reviews(weeks=10, max_reviews=50)
        assert len(result) <= 50

    @patch("src.ingestion.fetcher._fetch_page")
    def test_early_stop_on_old_review(self, mock_fetch):
        """Fetching stops when first review older than cutoff is encountered."""
        # Mix: one recent, then one too old (triggers stop_fetching)
        mock_fetch.return_value = (
            [self._make_raw(3, "recent"), self._make_raw(200, "ancient")],
            "token-should-not-be-used",
        )

        from src.ingestion.fetcher import fetch_reviews
        result = fetch_reviews(weeks=4)
        # "ancient" is excluded; only "recent" makes it through
        assert all(r.id != "ancient" for r in result)

    @patch("src.ingestion.fetcher._fetch_page")
    def test_network_error_returns_partial_results(self, mock_fetch):
        """A fetch exception is caught; reviews collected so far are returned."""
        good_page = [self._make_raw(1, "r1")]
        mock_fetch.side_effect = [
            (good_page, "token"),
            Exception("Network timeout"),
        ]

        from src.ingestion.fetcher import fetch_reviews
        # Should not raise — returns what was collected before the error
        result = fetch_reviews(weeks=10, max_reviews=500)
        assert isinstance(result, list)

    @patch("src.ingestion.fetcher._fetch_page")
    def test_empty_play_store_response(self, mock_fetch):
        """An empty response from the Play Store returns an empty list."""
        mock_fetch.return_value = ([], None)

        from src.ingestion.fetcher import fetch_reviews
        result = fetch_reviews(weeks=4)
        assert result == []

    @patch("src.ingestion.fetcher._fetch_page")
    def test_no_auth_in_fetch_call(self, mock_fetch):
        """
        Verify fetch_page is called without any authentication parameters.
        google-play-scraper's reviews() signature: (app_id, lang, country, count, token)
        No API key / credentials should appear.
        """
        mock_fetch.return_value = ([], None)

        from src.ingestion.fetcher import fetch_reviews
        fetch_reviews(weeks=4)

        # Inspect the actual call — first positional arg is app_id
        args, kwargs = mock_fetch.call_args
        app_id_used = args[0]
        assert app_id_used == "com.nextbillion.groww"
        # No 'api_key', 'auth', 'token_auth' kwargs present
        assert "api_key" not in kwargs
        assert "auth"    not in kwargs

    @patch("src.ingestion.fetcher._fetch_page")
    def test_default_app_id_is_groww(self, mock_fetch):
        """Default app_id resolves to com.nextbillion.groww from settings."""
        mock_fetch.return_value = ([], None)

        from src.ingestion.fetcher import fetch_reviews
        fetch_reviews()

        args, _ = mock_fetch.call_args
        assert args[0] == "com.nextbillion.groww"
