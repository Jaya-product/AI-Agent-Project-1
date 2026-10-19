"""
src/ingestion/fetcher.py
────────────────────────
Fetches public Groww Play Store reviews using google-play-scraper.

Design principles:
  - No authentication, no store login, no ToS-violating automation.
    google-play-scraper uses only the public Play Store web endpoint.
  - Reviews are fetched in pages (200/page) until max_reviews is reached
    or no more results are available.
  - Only reviews within the configured time window are returned.
  - Exponential backoff is applied between pages to respect rate limits.

Usage:
    from src.ingestion.fetcher import fetch_reviews
    from src.utils.config import settings

    reviews = fetch_reviews(
        app_id=settings.app_id,
        weeks=settings.review_window_weeks,
        max_reviews=settings.max_reviews,
    )
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from src.ingestion.parser import Review, parse_reviews
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
_PAGE_SIZE     = 200    # reviews per API call (library maximum)
_BACKOFF_BASE  = 1.0    # seconds — base for exponential backoff between pages
_BACKOFF_MAX   = 16.0   # seconds — cap for backoff


def _cutoff_date(weeks: int) -> datetime:
    """Return a UTC-aware datetime *weeks* ago (start of the ingestion window)."""
    return datetime.now(tz=timezone.utc) - timedelta(weeks=weeks)


def _fetch_page(
    app_id: str,
    lang: str,
    country: str,
    count: int,
    continuation_token: Any | None,
) -> tuple[list[dict], Any | None]:
    """
    Fetch one page of reviews from google-play-scraper.

    Returns:
        (raw_review_list, next_continuation_token)
        next_continuation_token is None when no more pages are available.
    """
    from google_play_scraper import reviews as gps_reviews  # type: ignore[import]
    from google_play_scraper.constants.element import ElementSpecs  # noqa: F401

    result, token = gps_reviews(
        app_id,
        lang=lang,
        country=country,
        count=count,
        continuation_token=continuation_token,
    )
    return result, token


def fetch_reviews(
    app_id: str | None = None,
    weeks: int | None = None,
    lang: str | None = None,
    country: str | None = None,
    max_reviews: int | None = None,
) -> list[Review]:
    """
    Fetch and return Play Store reviews for *app_id* within the last *weeks* weeks.

    Args:
        app_id:      Play Store app identifier. Defaults to ``settings.app_id``.
        weeks:       How many weeks of history to include. Defaults to
                     ``settings.review_window_weeks``.
        lang:        Language code (e.g. ``"en"``). Defaults to ``settings.lang``.
        country:     Country code (e.g. ``"in"``). Defaults to ``settings.country``.
        max_reviews: Hard cap on the number of reviews returned. Defaults to
                     ``settings.max_reviews``.

    Returns:
        List of :class:`~src.ingestion.parser.Review` objects, sorted newest first.

    Notes:
        - Uses public Play Store data only — no authentication.
        - Applies exponential backoff between pages to avoid rate-limiting.
        - Reviews outside the time window are excluded even if fetched.
    """
    # ── Resolve defaults ──────────────────────────────────────────────────────
    app_id      = app_id      or settings.app_id
    weeks       = weeks       if weeks       is not None else settings.review_window_weeks
    lang        = lang        or settings.lang
    country     = country     or settings.country
    max_reviews = max_reviews if max_reviews is not None else settings.max_reviews

    cutoff   = _cutoff_date(weeks)
    all_raw:  list[dict] = []
    token:    Any | None = None
    page_num: int        = 0
    stop_fetching = False

    logger.info(
        "Starting review fetch",
        extra={
            "app_id":      app_id,
            "weeks":       weeks,
            "cutoff":      cutoff.isoformat(),
            "max_reviews": max_reviews,
            "lang":        lang,
            "country":     country,
        },
    )

    while not stop_fetching:
        page_count = min(_PAGE_SIZE, max_reviews - len(all_raw))
        if page_count <= 0:
            logger.info("max_reviews cap reached", extra={"fetched": len(all_raw)})
            break

        # ── Backoff between pages ─────────────────────────────────────────────
        if page_num > 0:
            delay = min(_BACKOFF_BASE * (2 ** (page_num - 1)), _BACKOFF_MAX)
            logger.debug("Backoff before next page", extra={"delay_s": delay, "page": page_num})
            time.sleep(delay)

        try:
            raw_page, token = _fetch_page(app_id, lang, country, page_count, token)
        except Exception as exc:
            logger.error(
                "Play Store fetch failed",
                extra={"page": page_num, "error": str(exc)},
            )
            break

        page_num += 1

        if not raw_page:
            logger.info("No more reviews available from Play Store")
            break

        # ── Filter to time window ─────────────────────────────────────────────
        for raw in raw_page:
            review_date: datetime | None = raw.get("at")
            if review_date is None:
                continue
            # Ensure UTC-aware for comparison
            if review_date.tzinfo is None:
                review_date = review_date.replace(tzinfo=timezone.utc)

            if review_date < cutoff:
                # Reviews come back newest-first; once we hit old reviews we can stop
                stop_fetching = True
                logger.info(
                    "Reached cutoff date — stopping fetch",
                    extra={"cutoff": cutoff.isoformat(), "review_date": review_date.isoformat()},
                )
                break

            all_raw.append(raw)

        # Stop if the library signals no more pages
        if token is None:
            logger.info("Play Store returned no continuation token — all pages exhausted")
            break

    logger.info(
        "Fetch complete — parsing reviews",
        extra={"raw_count": len(all_raw), "pages_fetched": page_num},
    )

    reviews = parse_reviews(all_raw)

    # Hard-enforce the cap on the final list (parse_reviews may return
    # slightly more if the last page slightly overshot the limit)
    if len(reviews) > max_reviews:
        reviews = reviews[:max_reviews]

    logger.info(
        "Ingestion complete",
        extra={
            "total_reviews": len(reviews),
            "weeks":         weeks,
            "app_id":        app_id,
        },
    )

    return reviews
