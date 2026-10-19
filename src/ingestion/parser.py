"""
src/ingestion/parser.py
───────────────────────
Defines the canonical Review schema and maps raw google-play-scraper
output dictionaries into validated Review objects.

The raw output from google-play-scraper varies slightly across versions,
so all field access is defensive (uses .get() with sensible defaults).

Usage:
    from src.ingestion.parser import parse_review, parse_reviews

    raw_list = [...]          # list of dicts from google_play_scraper
    reviews  = parse_reviews(raw_list)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pydantic import ConfigDict
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ── Canonical Review Schema ───────────────────────────────────────────────────

class Review(BaseModel):
    """
    Normalised representation of a single Play Store review.

    All downstream components (scrubber, clusterer, pulse generator) operate
    on this schema exclusively — no raw library dicts leak further.
    """

    id: str = Field(description="Unique review identifier from the Play Store.")
    rating: int = Field(ge=1, le=5, description="Star rating 1–5.")
    title: str | None = Field(default=None, description="Review headline (may be absent).")
    text: str = Field(description="Full review body text.")
    date: datetime = Field(description="Submission timestamp (UTC-aware).")
    thumbs_up: int = Field(
        default=0,
        ge=0,
        description="Community upvote count. Used to prioritise resonant quotes in clustering & pulse.",
    )

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Review text must not be blank.")
        return v.strip()

    @field_validator("title", mode="before")
    @classmethod
    def normalise_title(cls, v: Any) -> str | None:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return str(v).strip()

    @field_validator("date", mode="before")
    @classmethod
    def ensure_utc(cls, v: Any) -> datetime:
        if isinstance(v, datetime):
            # Attach UTC if naive
            if v.tzinfo is None:
                return v.replace(tzinfo=timezone.utc)
            return v
        raise ValueError(f"Expected datetime, got {type(v).__name__}")

    model_config = ConfigDict(frozen=True)   # Reviews are immutable value objects


# ── Raw → Review mapping ──────────────────────────────────────────────────────

# Keys emitted by google-play-scraper (may change across library versions)
_ID_KEYS         = ("reviewId", "review_id", "id")
_RATING_KEY      = "score"
_TITLE_KEYS      = ("title",)
_TEXT_KEYS       = ("content", "text", "body")
_DATE_KEYS       = ("at", "date", "datetime")
_THUMBS_UP_KEYS  = ("thumbsUpCount", "thumbs_up", "thumbsup")


def _get_first(d: dict, keys: tuple[str, ...], default: Any = None) -> Any:
    """Return the value of the first matching key found in *d*."""
    for k in keys:
        if k in d:
            return d[k]
    return default


def parse_review(raw: dict[str, Any]) -> Review:
    """
    Convert a single raw google-play-scraper dict into a Review.

    Args:
        raw: Dict as returned by ``google_play_scraper.reviews()``.

    Returns:
        A validated :class:`Review` instance.

    Raises:
        ValueError: If required fields are missing or invalid.
    """
    review_id = str(_get_first(raw, _ID_KEYS, "") or "")
    if not review_id:
        # Fall back to a deterministic hash when the library omits the ID
        import hashlib
        review_id = hashlib.md5(
            (str(raw.get(_RATING_KEY, "")) + str(_get_first(raw, _TEXT_KEYS, ""))).encode()
        ).hexdigest()

    rating    = int(raw.get(_RATING_KEY, 3))
    title     = _get_first(raw, _TITLE_KEYS)
    text      = str(_get_first(raw, _TEXT_KEYS, "") or "")
    date      = _get_first(raw, _DATE_KEYS)
    thumbs_up = int(_get_first(raw, _THUMBS_UP_KEYS, 0) or 0)

    if isinstance(date, str):
        try:
            date = datetime.fromisoformat(date)
        except Exception:
            pass

    if not isinstance(date, datetime):
        raise ValueError(f"Cannot parse date field from raw review: {raw!r}")

    return Review(
        id=review_id,
        rating=rating,
        title=title,
        text=text,
        date=date,
        thumbs_up=thumbs_up,
    )


def parse_reviews(raw_list: list[dict[str, Any]]) -> list[Review]:
    """
    Parse a list of raw review dicts, skipping any that fail validation.

    Validation errors are logged as warnings rather than crashing the pipeline.

    Args:
        raw_list: List of raw dicts from google-play-scraper.

    Returns:
        List of valid :class:`Review` objects (may be shorter than input).
    """
    from src.utils.logger import get_logger  # lazy import to avoid circular deps
    logger = get_logger(__name__)

    reviews: list[Review] = []
    skipped = 0

    for raw in raw_list:
        try:
            reviews.append(parse_review(raw))
        except Exception as exc:
            skipped += 1
            logger.warning(
                "Skipping unparseable review",
                extra={"error": str(exc), "raw_keys": list(raw.keys())},
            )

    if skipped:
        logger.warning("Parsing complete with skipped rows", extra={"skipped": skipped, "parsed": len(reviews)})
    else:
        logger.info("Parsing complete", extra={"parsed": len(reviews)})

    return reviews
