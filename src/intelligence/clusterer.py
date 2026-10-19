"""
src/intelligence/clusterer.py
──────────────────────────────
Phase 4 — Theme Clustering

Responsibilities
────────────────
1. **LLM Client** — thin wrapper around Gemini (default) or OpenAI, selected
   via ``settings.llm_provider``. Both providers return a plain text response
   that we parse as JSON.

2. **Clustering prompt** — sends batches of scrubbed review texts to the LLM
   and asks it to assign each review to exactly one of ≤ 5 named themes. The
   prompt handles:
     - Mixed-language reviews (19% of Groww reviews are non-ASCII)
     - Theme-name inference from data (no pre-seeded labels)
     - Strict JSON output format for deterministic parsing

3. **Batching** — reviews are sent in chunks of ``settings.llm_batch_size``
   (default 50). Results are merged and themes consolidated across batches.

4. **Theme schema** — output is a list of ``Theme`` objects, sorted by
   ``review_count`` descending. Quotes prefer reviews with ``thumbs_up > 0``.

5. **Retry / hard-cap logic** — if the LLM returns > 5 themes, it retries with
   a stricter prompt (max ``settings.llm_max_retries`` attempts). Raises
   ``ValueError`` if the cap is still exceeded after all retries.

Usage
─────
    from src.intelligence.clusterer import cluster

    themes = cluster(scrubbed_reviews)          # list[Theme]
    for t in themes:
        print(t.name, t.review_count, t.sentiment)

Expected Themes (from real groww_reviews.json analysis)
────────────────────────────────────────────────────────
  1. UI/UX & Ease of Use
  2. Brokerage Charges & Fees
  3. Customer Service & Withdrawals
  4. App Performance & Bugs
  5. Features & Missing Functionality
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

from src.ingestion.parser import Review
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── Exceptions ────────────────────────────────────────────────────────────────

class ThemeCapExceededError(ValueError):
    """Raised when the LLM returns more themes than max_themes even after all retries."""
    pass


# ── Theme Schema ──────────────────────────────────────────────────────────────

class Theme(BaseModel):
    """
    A named cluster of semantically related reviews.

    Produced by ``cluster()`` and consumed by the pulse generator.
    Themes are sorted by ``review_count`` descending before being returned.
    """

    name: str = Field(description="Human-readable theme label (inferred by LLM from review text).")
    review_count: int = Field(ge=0, description="Number of reviews assigned to this theme.")
    reviews: list[Review] = Field(description="All Review objects assigned to this theme.")
    sentiment: str = Field(
        description='Dominant sentiment: "positive" | "neutral" | "negative".',
    )
    top_quotes: list[str] = Field(
        description=(
            "Up to 3 verbatim, PII-free quotes from reviews in this theme. "
            "Selected by prioritising reviews with thumbs_up > 0."
        ),
    )

    @field_validator("sentiment")
    @classmethod
    def validate_sentiment(cls, v: str) -> str:
        allowed = {"positive", "neutral", "negative"}
        v_lower = v.lower().strip()
        if v_lower not in allowed:
            raise ValueError(f"sentiment must be one of {allowed}, got {v!r}")
        return v_lower

    @field_validator("top_quotes")
    @classmethod
    def validate_quotes(cls, v: list[str]) -> list[str]:
        if len(v) > 3:
            raise ValueError(f"top_quotes must contain ≤ 3 items, got {len(v)}")
        return v


# ── LLM Client Protocol & implementations ────────────────────────────────────

class LLMClient(Protocol):
    """Minimal interface that both Gemini and OpenAI adapters satisfy."""

    def generate(self, prompt: str) -> str:
        """Send *prompt* to the LLM; return the raw text response."""
        ...


class GeminiClient:
    """Adapter around google-generativeai."""

    def __init__(self, api_key: str, model: str = "gemini-1.5-flash") -> None:
        try:
            import google.generativeai as genai  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "google-generativeai is required for llm_provider=gemini. "
                "Run: pip install google-generativeai"
            ) from exc
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model)

    def generate(self, prompt: str) -> str:
        response = self._model.generate_content(prompt)
        return response.text


class OpenAIClient:
    """Adapter around openai."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        try:
            from openai import OpenAI  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "openai is required for llm_provider=openai. "
                "Run: pip install openai"
            ) from exc
        self._client = OpenAI(api_key=api_key)
        self._model = model

    def generate(self, prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return response.choices[0].message.content or ""


def _build_llm_client() -> LLMClient:
    """
    Construct the configured LLM client from settings.

    Raises:
        ValueError: If llm_provider is unknown or required API key is missing.
    """
    provider = settings.llm_provider.lower()

    if provider == "gemini":
        if not settings.gemini_api_key:
            raise ValueError(
                "GEMINI_API_KEY is required when llm_provider=gemini. "
                "Set it in .env or as an environment variable."
            )
        return GeminiClient(api_key=settings.gemini_api_key)

    if provider == "openai":
        if not settings.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY is required when llm_provider=openai. "
                "Set it in .env or as an environment variable."
            )
        return OpenAIClient(api_key=settings.openai_api_key)

    raise ValueError(
        f"Unknown llm_provider {provider!r}. Must be 'gemini' or 'openai'."
    )


# ── Prompt templates ──────────────────────────────────────────────────────────

_CLUSTERING_PROMPT_TEMPLATE = """\
You are a product analyst reviewing user feedback for the Groww investment app (India).

Your task is to cluster the following {n} user reviews into EXACTLY {max_themes} or fewer \
named themes. Infer theme names from the content — do not use generic labels.

RULES:
- Assign each review to exactly ONE theme.
- Return between 1 and {max_themes} themes (never more than {max_themes}).
- Theme names must be concise (2-5 words), descriptive, and specific to Groww's context.
- Determine the dominant sentiment for each theme: "positive", "neutral", or "negative".
- The reviews may be in English, Hindi (Devanagari), Tamil, Punjabi, Malayalam, or mixed \
scripts — handle all languages. Understand the semantic meaning regardless of script.
- Do NOT invent reviews. Only use the review texts provided below.

OUTPUT FORMAT — respond ONLY with a valid JSON object in this exact structure, no markdown fences:
{{
  "assignments": [
    {{"review_index": 0, "theme_name": "Theme Label"}},
    {{"review_index": 1, "theme_name": "Theme Label"}},
    ...
  ],
  "themes": [
    {{"name": "Theme Label", "sentiment": "positive"}},
    ...
  ]
}}

REVIEWS (0-indexed):
{reviews_block}
"""

_STRICT_CLUSTERING_PROMPT_TEMPLATE = """\
You are a product analyst reviewing user feedback for the Groww investment app (India).

CRITICAL CONSTRAINT: You MUST use EXACTLY {max_themes} themes or fewer. \
Do NOT exceed {max_themes} themes under any circumstances.

Cluster the following {n} reviews. Merge similar themes aggressively if needed \
to stay within the {max_themes}-theme limit.

RULES:
- Assign each review to exactly ONE theme.
- Return between 1 and {max_themes} themes (hard limit — exceeding this is a failure).
- Theme names must be concise (2-5 words), descriptive, and specific to Groww.
- Determine dominant sentiment per theme: "positive", "neutral", or "negative".
- Reviews may be in English, Hindi, Tamil, Punjabi, Malayalam, or mixed scripts.

OUTPUT FORMAT — respond ONLY with valid JSON, no markdown fences, no extra text:
{{
  "assignments": [
    {{"review_index": 0, "theme_name": "Theme Label"}},
    ...
  ],
  "themes": [
    {{"name": "Theme Label", "sentiment": "positive"}},
    ...
  ]
}}

REVIEWS (0-indexed):
{reviews_block}
"""


def _build_reviews_block(reviews: list[Review], offset: int = 0) -> str:
    """Format reviews as a numbered block for inclusion in a prompt."""
    lines: list[str] = []
    for i, review in enumerate(reviews):
        idx = i + offset
        lines.append(f"[{idx}] (rating: {review.rating}/5) {review.text}")
    return "\n".join(lines)


# ── JSON parsing ──────────────────────────────────────────────────────────────

def _extract_json(raw: str) -> dict[str, Any]:
    """
    Extract and parse the first JSON object from an LLM response string.

    The LLM sometimes wraps JSON in markdown fences or adds prose before/after.
    This function strips common wrappers and finds the first ``{...}`` block.

    Raises:
        ValueError: If no valid JSON object can be found or parsed.
    """
    # Strip common markdown fences
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()

    # Find the outermost JSON object
    brace_start = cleaned.find("{")
    brace_end = cleaned.rfind("}")
    if brace_start == -1 or brace_end == -1:
        raise ValueError(f"No JSON object found in LLM response: {raw[:200]!r}")

    json_str = cleaned[brace_start : brace_end + 1]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse JSON from LLM response: {exc}") from exc


# ── Quote selection ───────────────────────────────────────────────────────────

def _select_top_quotes(reviews: list[Review], max_quotes: int = 3) -> list[str]:
    """
    Pick up to *max_quotes* verbatim quotes from *reviews*.

    Selection criteria (in order of priority):
      1. Reviews with ``thumbs_up > 0`` (community-validated sentiment)
      2. Longer reviews (more informative)
      3. Stable sort preserves original order for equal-priority reviews

    Returns only non-empty text strings of meaningful length (≥30 chars).
    """
    # Filter to reviews with meaningful text for quoting
    candidates = [r for r in reviews if len(r.text.strip()) >= 30]

    if not candidates:
        # Fall back to any review in the theme
        candidates = [r for r in reviews if r.text.strip()]

    # Sort: thumbs_up descending, then text length descending
    candidates.sort(key=lambda r: (-r.thumbs_up, -len(r.text)))

    return [r.text.strip() for r in candidates[:max_quotes]]


# ── Batch clustering ──────────────────────────────────────────────────────────

def _cluster_batch(
    reviews: list[Review],
    llm: LLMClient,
    max_themes: int,
    strict: bool = False,
    offset: int = 0,
) -> dict[str, Any]:
    """
    Send a single batch of *reviews* to the LLM and return the parsed response.

    Args:
        reviews:    Batch of scrubbed Review objects.
        llm:        Configured LLM client.
        max_themes: Hard cap on number of themes.
        strict:     If True, use the stricter prompt variant.
        offset:     Index offset for review numbering (multi-batch scenarios).

    Returns:
        Parsed dict with "assignments" and "themes" keys.

    Raises:
        ValueError: If the LLM response cannot be parsed or has no assignments.
    """
    reviews_block = _build_reviews_block(reviews, offset=offset)
    template = _STRICT_CLUSTERING_PROMPT_TEMPLATE if strict else _CLUSTERING_PROMPT_TEMPLATE
    prompt = template.format(
        n=len(reviews),
        max_themes=max_themes,
        reviews_block=reviews_block,
    )

    raw_response = llm.generate(prompt)
    logger.debug(
        "LLM clustering response received",
        extra={"batch_size": len(reviews), "response_chars": len(raw_response)},
    )

    result = _extract_json(raw_response)

    if "assignments" not in result:
        raise ValueError(
            f"LLM response missing 'assignments' key. Got keys: {list(result.keys())}"
        )

    return result


def _merge_batch_results(
    batch_results: list[tuple[list[Review], dict[str, Any]]],
    max_themes: int | None = None,
) -> dict[str, list[Review]]:
    """
    Merge assignments from multiple batches into a single theme → reviews mapping.

    Theme names from different batches may differ slightly (LLM is non-deterministic).
    We use exact-match first. If max_themes is provided, consolidate the smallest
    themes to respect the limit.

    Returns:
        Dict mapping theme_name → list[Review].
    """
    theme_map: dict[str, list[Review]] = {}

    for reviews_in_batch, result in batch_results:
        assignments: list[dict] = result.get("assignments", [])
        for assignment in assignments:
            idx_in_batch = assignment.get("review_index", 0)
            # review_index is batch-local (0-based within this batch)
            local_idx = idx_in_batch % len(reviews_in_batch)
            review = reviews_in_batch[local_idx]
            theme_name: str = str(assignment.get("theme_name", "Uncategorized")).strip()

            if theme_name not in theme_map:
                theme_map[theme_name] = []
            theme_map[theme_name].append(review)

    # If max_themes is specified and exceeded, consolidate smallest ones
    if max_themes is not None:
        while len(theme_map) > max_themes:
            # Merge the two smallest themes into the smaller one's bucket
            sorted_themes = sorted(theme_map.items(), key=lambda kv: len(kv[1]))
            smallest_name, smallest_reviews = sorted_themes[0]
            second_name, _ = sorted_themes[1]

            theme_map[second_name].extend(smallest_reviews)
            del theme_map[smallest_name]
            logger.debug(
                "Merged smallest theme to stay within cap",
                extra={"merged": smallest_name, "into": second_name, "current_count": len(theme_map)},
            )

    return theme_map



# ── Sentiment inference ───────────────────────────────────────────────────────

def _infer_sentiment_from_ratings(reviews: list[Review]) -> str:
    """
    Infer dominant sentiment from average star rating of a theme's reviews.

    Fallback used when the LLM doesn't return a valid sentiment.
    """
    if not reviews:
        return "neutral"
    avg = sum(r.rating for r in reviews) / len(reviews)
    if avg >= 4.0:
        return "positive"
    if avg >= 2.5:
        return "neutral"
    return "negative"


def _get_theme_sentiment(
    theme_name: str,
    themes_meta: list[dict],
    reviews: list[Review],
) -> str:
    """
    Look up the LLM-provided sentiment for *theme_name*, falling back to rating-based inference.
    """
    for meta in themes_meta:
        if meta.get("name", "").strip() == theme_name:
            raw_sentiment = meta.get("sentiment", "").lower().strip()
            if raw_sentiment in {"positive", "neutral", "negative"}:
                return raw_sentiment
    return _infer_sentiment_from_ratings(reviews)


# ── Public entry point ────────────────────────────────────────────────────────

def cluster(
    reviews: list[Review],
    *,
    llm: LLMClient | None = None,
    max_themes: int | None = None,
    batch_size: int | None = None,
    max_retries: int | None = None,
) -> list[Theme]:
    """
    Cluster scrubbed reviews into ≤ ``max_themes`` named, ranked Theme objects.

    The LLM assigns each review to exactly one theme; results from multiple
    batches are merged. Themes are sorted by ``review_count`` descending.

    Args:
        reviews:     Scrubbed Review objects (output of Phase 3 scrubber).
                     **Must not be called with unscrubbed reviews.**
        llm:         Optional pre-built LLM client (used for testing). If None,
                     a client is constructed from ``settings``.
        max_themes:  Override ``settings.max_themes`` (default 5).
        batch_size:  Override ``settings.llm_batch_size`` (default 50).
        max_retries: Override ``settings.llm_max_retries`` (default 3).

    Returns:
        List of ``Theme`` objects sorted by ``review_count`` descending.
        Always contains ≤ ``max_themes`` items.

    Raises:
        ValueError: If no reviews are provided, or if the LLM still returns
                    > max_themes themes after all retries.
    """
    if not reviews:
        raise ValueError("Cannot cluster an empty review list.")

    _max_themes = max_themes if max_themes is not None else settings.max_themes
    _batch_size = batch_size if batch_size is not None else settings.llm_batch_size
    _max_retries = max_retries if max_retries is not None else settings.llm_max_retries

    _llm = llm if llm is not None else _build_llm_client()

    logger.info(
        "Starting theme clustering",
        extra={
            "review_count": len(reviews),
            "max_themes": _max_themes,
            "batch_size": _batch_size,
            "max_retries": _max_retries,
            "llm_provider": settings.llm_provider,
        },
    )

    # ── Batch the reviews ─────────────────────────────────────────────────────
    batches = [reviews[i : i + _batch_size] for i in range(0, len(reviews), _batch_size)]
    logger.info("Review batches prepared", extra={"batch_count": len(batches)})

    # ── Run clustering with retry loop ────────────────────────────────────────
    for attempt in range(1, _max_retries + 1):
        strict_mode = attempt > 1   # use stricter prompt after first failure
        try:
            batch_results: list[tuple[list[Review], dict[str, Any]]] = []
            all_themes_meta: list[dict] = []

            for batch_idx, batch in enumerate(batches):
                logger.debug(
                    "Processing batch",
                    extra={
                        "batch_idx": batch_idx + 1,
                        "of": len(batches),
                        "size": len(batch),
                        "attempt": attempt,
                        "strict": strict_mode,
                    },
                )

                result = _cluster_batch(
                    reviews=batch,
                    llm=_llm,
                    max_themes=_max_themes,
                    strict=strict_mode,
                    offset=batch_idx * _batch_size,
                )
                batch_results.append((batch, result))
                all_themes_meta.extend(result.get("themes", []))

                # Polite pause between batches to avoid rate limits
                if batch_idx < len(batches) - 1:
                    time.sleep(0.5)

            # ── Merge batch assignments ───────────────────────────────────────
            theme_map = _merge_batch_results(batch_results)

            # ── Validate cap ──────────────────────────────────────────────────
            if len(theme_map) > _max_themes:
                logger.warning(
                    "Theme cap exceeded after merge — will retry",
                    extra={
                        "theme_count": len(theme_map),
                        "max_themes": _max_themes,
                        "attempt": attempt,
                    },
                )
                if attempt == _max_retries:
                    raise ThemeCapExceededError(
                        f"LLM returned {len(theme_map)} themes after {_max_retries} retries "
                        f"(max allowed: {_max_themes}). "
                        "Check prompt and consider raising max_themes."
                    )
                backoff = 2 ** attempt
                logger.info("Retrying after backoff", extra={"seconds": backoff})
                time.sleep(backoff)
                continue   # retry

            # ── Build Theme objects ───────────────────────────────────────────
            themes: list[Theme] = []
            for theme_name, theme_reviews in theme_map.items():
                sentiment = _get_theme_sentiment(theme_name, all_themes_meta, theme_reviews)
                top_quotes = _select_top_quotes(theme_reviews)

                themes.append(
                    Theme(
                        name=theme_name,
                        review_count=len(theme_reviews),
                        reviews=theme_reviews,
                        sentiment=sentiment,
                        top_quotes=top_quotes,
                    )
                )

            # ── Sort by review_count descending ───────────────────────────────
            themes.sort(key=lambda t: -t.review_count)

            logger.info(
                "Clustering complete",
                extra={
                    "theme_count": len(themes),
                    "themes": [
                        {"name": t.name, "count": t.review_count, "sentiment": t.sentiment}
                        for t in themes
                    ],
                    "attempt": attempt,
                },
            )

            return themes

        except ThemeCapExceededError:
            raise   # propagate cap-exceeded error from final attempt

        except Exception as exc:
            logger.warning(
                "Clustering attempt failed",
                extra={"attempt": attempt, "error": str(exc)},
            )
            if attempt == _max_retries:
                raise RuntimeError(
                    f"Clustering failed after {_max_retries} attempts. Last error: {exc}"
                ) from exc

            # Exponential backoff before retry
            backoff = 2 ** attempt
            logger.info("Retrying after backoff", extra={"seconds": backoff})
            time.sleep(backoff)

    # Should not reach here, but satisfy type checker
    raise RuntimeError("Clustering loop exited unexpectedly.")
