"""
src/ingestion/scrubber.py
─────────────────────────
Phase 3 — Preprocessing & PII Scrubbing

Responsibilities
────────────────
1. **PII masking** — replace sensitive patterns with safe placeholders:
     - Email addresses          → [email]
     - Indian phone numbers     → [phone]
     - UPI IDs                  → [upi_id]
     - Support/ticket IDs       → [ticket_id]   (observed in real Groww reviews)
     - Device model strings     → removed
     - Excess whitespace        → normalised

2. **Minimum-length filter** — drop reviews below `min_review_length` chars
   after scrubbing. Real data: ~67% of raw Groww reviews are <20 chars.

3. **Multilingual policy** — controlled by `non_english_policy` in settings:
     "keep" (default/recommended) — non-ASCII reviews are passed through;
             Gemini handles Hindi, Tamil, Punjabi, Malayalam natively.
     "drop" — reviews with any non-ASCII character are discarded.
   Real data: ~19.2% of Groww reviews contain non-ASCII text.

4. **Immutability** — the scrubber never mutates Review objects; it creates
   new instances with scrubbed text (Pydantic frozen model).

Usage
─────
    from src.ingestion.scrubber import scrub

    raw_reviews   = fetch_reviews(...)   # list[Review]
    clean_reviews = scrub(raw_reviews)   # list[Review]

Acceptance Criteria (Phase 3)
──────────────────────────────
  - Email, phone, UPI, and ticket ID patterns are masked in output text.
  - Reviews below min_review_length are excluded (~67% in practice).
  - `rating`, `date`, and `thumbs_up` are preserved unchanged.
  - Non-English policy is configurable and enforced.
  - Scrubber is always called before any LLM invocation.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from src.ingestion.parser import Review
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── PII & noise patterns ──────────────────────────────────────────────────────

# Email addresses  (RFC 5321-ish, good enough for review text)
_RE_EMAIL = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

# Indian phone numbers:
#   +91-XXXXX-XXXXX  |  +91 XXXXXXXXXX  |  0XXXXXXXXXX  |  10-digit bare
_RE_PHONE = re.compile(
    r"(?:\+91[\s\-]?)?(?:\(?0\d{2,4}\)?[\s\-]?)?\d{5}[\s\-]?\d{5}"
    r"|(?:\+91[\s\-])?\d{10}",
    re.IGNORECASE,
)

# UPI IDs  (anything@bankhandle — common formats: user@upi, user@okaxis, etc.)
_RE_UPI = re.compile(
    r"[a-zA-Z0-9.\-_]+@(?:upi|okaxis|okhdfcbank|okicici|oksbi|ybl|ibl|axl|"
    r"paytm|apl|fbl|pingpay|naviaxis|rapl|rbl|barodampay|mahb|jsb|utib0000006)",
    re.IGNORECASE,
)

# Support / ticket reference IDs
#   Patterns seen in real data:  "Ticket ID 28133385", "ticket no 12345", "#28133385"
_RE_TICKET = re.compile(
    r"(?:ticket\s*(?:id|no|number|#)?|reference\s*(?:id|no|number)?|"
    r"complaint\s*(?:id|no|number)?|request\s*(?:id|no|number)?|"
    r"case\s*(?:id|no|number)?)\s*[:#]?\s*\d{5,12}"
    r"|\#\d{5,12}",
    re.IGNORECASE,
)

# Device model strings — coarse heuristic; catches patterns like
#   "Samsung Galaxy S21", "Redmi Note 11 Pro", "OnePlus 9R"
_RE_DEVICE = re.compile(
    r"\b(?:Samsung|Redmi|Xiaomi|OnePlus|Realme|Motorola|Vivo|Oppo|Nokia|"
    r"Poco|iQOO|Infinix|Tecno|Itel)\s+[A-Z][a-zA-Z0-9\s]{2,20}",
    re.IGNORECASE,
)

# Multiple consecutive whitespace / invisible Unicode separators → single space
_RE_WHITESPACE = re.compile(r"[ \t\r\n\u00A0\u200B\u200C\u200D\uFEFF]+")


# ── Public helpers ────────────────────────────────────────────────────────────

NonEnglishPolicy = Literal["keep", "drop"]


def _apply_pii_masks(text: str) -> str:
    """
    Apply all PII masking rules to *text* in a fixed, safe order.

    Masking order matters — UPI must come before generic email so that
    the bank-handle suffix is still present when the UPI pattern fires.
    """
    text = _RE_TICKET.sub("[ticket_id]", text)   # before phone (ticket numbers could match phone)
    text = _RE_UPI.sub("[upi_id]", text)          # before email (UPI looks like email)
    text = _RE_EMAIL.sub("[email]", text)
    text = _RE_PHONE.sub("[phone]", text)
    text = _RE_DEVICE.sub("", text)
    return text


def _normalise_whitespace(text: str) -> str:
    """Collapse runs of whitespace / invisible chars to a single space and strip ends."""
    return _RE_WHITESPACE.sub(" ", text).strip()


def _is_non_ascii(text: str) -> bool:
    """Return True if *text* contains any character outside the ASCII range."""
    return not text.isascii()


def scrub_text(text: str) -> str:
    """
    Apply PII masking and whitespace normalisation to a single text string.

    This function is intentionally stateless and side-effect-free so it can
    be used in unit tests independently of the full Review pipeline.

    Args:
        text: Raw review text.

    Returns:
        Scrubbed text with PII replaced and whitespace normalised.
    """
    text = _apply_pii_masks(text)
    text = _normalise_whitespace(text)
    return text


def scrub(
    reviews: list[Review],
    *,
    min_length: int | None = None,
    non_english_policy: NonEnglishPolicy | None = None,
) -> list[Review]:
    """
    Scrub a list of reviews: mask PII, enforce length filter, apply language policy.

    All Review objects returned are freshly constructed (immutable value objects);
    the originals are never modified.

    Args:
        reviews:              Input reviews from the ingestion layer.
        min_length:           Override ``settings.min_review_length``. Reviews
                              whose scrubbed text is shorter than this are dropped.
                              Defaults to ``settings.min_review_length`` (20 chars).
        non_english_policy:   Override ``settings.non_english_policy``.
                              "keep" — retain non-ASCII reviews (recommended).
                              "drop" — discard reviews containing non-ASCII chars.
                              Defaults to ``settings.non_english_policy`` ("keep").

    Returns:
        Filtered and scrubbed list of Review objects. All fields except ``text``
        (and ``title`` when it exists) are preserved exactly as-is.

    Note:
        Based on real Groww data (groww_reviews.json, 400 reviews):
          - ~67% of reviews are <20 chars and will be dropped.
          - ~19% are non-ASCII; dropping them loses substantive regional feedback.
    """
    _min_length = min_length if min_length is not None else settings.min_review_length
    _policy: NonEnglishPolicy = (
        non_english_policy if non_english_policy is not None
        else settings.non_english_policy  # type: ignore[assignment]
    )

    if _policy not in ("keep", "drop"):
        raise ValueError(
            f"non_english_policy must be 'keep' or 'drop', got {_policy!r}"
        )

    result: list[Review] = []
    stats = {
        "input": len(reviews),
        "dropped_short": 0,
        "dropped_non_english": 0,
        "passed": 0,
    }

    for review in reviews:
        # ── 1. Language policy ────────────────────────────────────────────────
        if _policy == "drop" and _is_non_ascii(review.text):
            stats["dropped_non_english"] += 1
            logger.debug(
                "Dropped non-ASCII review",
                extra={"review_id": review.id, "policy": _policy},
            )
            continue

        # ── 2. PII masking ────────────────────────────────────────────────────
        scrubbed_text = scrub_text(review.text)

        # Scrub title too (may contain PII in rare cases)
        scrubbed_title: str | None = None
        if review.title:
            scrubbed_title = _normalise_whitespace(_apply_pii_masks(review.title)) or None

        # ── 3. Minimum-length filter ──────────────────────────────────────────
        if len(scrubbed_text) < _min_length:
            stats["dropped_short"] += 1
            logger.debug(
                "Dropped short review",
                extra={
                    "review_id": review.id,
                    "text_len": len(scrubbed_text),
                    "min_length": _min_length,
                },
            )
            continue

        # ── 4. Emit scrubbed Review ───────────────────────────────────────────
        # Use model_copy to create a new frozen instance with only text/title changed.
        scrubbed_review = review.model_copy(
            update={"text": scrubbed_text, "title": scrubbed_title}
        )
        result.append(scrubbed_review)
        stats["passed"] += 1

    stats_pct_passed = (
        f"{100 * stats['passed'] / stats['input']:.1f}%"
        if stats["input"] > 0
        else "n/a"
    )

    logger.info(
        "Scrubbing complete",
        extra={
            "input":                 stats["input"],
            "passed":                stats["passed"],
            "pct_passed":            stats_pct_passed,
            "dropped_short":         stats["dropped_short"],
            "dropped_non_english":   stats["dropped_non_english"],
            "min_length":            _min_length,
            "non_english_policy":    _policy,
        },
    )

    return result
