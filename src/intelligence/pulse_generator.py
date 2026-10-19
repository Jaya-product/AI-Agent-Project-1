"""
src/intelligence/pulse_generator.py
───────────────────────────────────
Phase 5 — Pulse Generation

Responsibilities
────────────────
1. **Top Theme Selection** — picks the top 3 themes by review count volume.
2. **Quote Selection** — selects exactly 1 verbatim, PII-free quote per top theme
   (3 total) directly from scrubbed reviews. Quotes are never invented.
3. **Action Idea Generation** — uses the configured LLM (Gemini or OpenAI) to
   generate 3 concrete, high-impact action recommendations grounded in the top
   themes and user quotes.
4. **Markdown & Plain Text Assembly** — formats the one-page weekly pulse note
   following the exact standard template.
5. **Hard Word Limit Cap** — enforces the strict <= 250-word cap post-generation
   by trimming action ideas if necessary. Quotes are never trimmed.
6. **Pulse Schema** — returns a validated Pydantic ``Pulse`` object.

Usage
─────
    from src.intelligence.pulse_generator import generate_pulse

    pulse = generate_pulse(themes)
    print(pulse.body)
    print(pulse.word_count)
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import time
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.intelligence.clusterer import LLMClient, Theme, _build_llm_client, _extract_json
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── Pulse Schema ──────────────────────────────────────────────────────────────

class Pulse(BaseModel):
    """
    A structured, executive weekly review pulse note.

    Contains top 3 themes, 3 verbatim quotes, 3 concrete action ideas,
    and a formatted body strictly <= 250 words.
    """

    week_of: date = Field(description="The date of the week represented by this pulse.")
    top_themes: list[Theme] = Field(description="Top 3 themes by review volume.")
    quotes: list[str] = Field(description="3 verbatim quotes (one from each top theme).")
    action_ideas: list[str] = Field(description="3 concrete action ideas grounded in top themes.")
    body: str = Field(description="Formatted pulse text (Markdown) <= 250 words.")
    word_count: int = Field(ge=0, description="Word count of the body string.")

    @field_validator("top_themes")
    @classmethod
    def validate_top_themes(cls, v: list[Theme]) -> list[Theme]:
        if len(v) != 3:
            raise ValueError(f"top_themes must contain exactly 3 themes, got {len(v)}")
        return v

    @field_validator("quotes")
    @classmethod
    def validate_quotes(cls, v: list[str]) -> list[str]:
        if len(v) != 3:
            raise ValueError(f"quotes must contain exactly 3 items, got {len(v)}")
        return v

    @field_validator("action_ideas")
    @classmethod
    def validate_action_ideas(cls, v: list[str]) -> list[str]:
        if len(v) != 3:
            raise ValueError(f"action_ideas must contain exactly 3 items, got {len(v)}")
        return v

    @field_validator("word_count")
    @classmethod
    def validate_word_count(cls, v: int) -> int:
        if v > 250:
            raise ValueError(f"word_count must be <= 250 words, got {v}")
        return v

    def to_plain_text(self) -> str:
        """
        Render the pulse in clean plain-text format (suitable for plain-text email or terminal).
        """
        date_str = self.week_of.strftime("%B %d, %Y")
        title = f"Groww Weekly Review Pulse — Week of {date_str}"
        underline = "=" * len(title)

        themes_text = "\n".join(
            f"{i}. {t.name} — {t.review_count} reviews"
            for i, t in enumerate(self.top_themes, 1)
        )

        quotes_text = "\n".join(
            f'- "{_clean_quote(q)}"'
            for q in self.quotes
        )

        actions_text = "\n".join(
            f"{i}. {action.strip()}"
            for i, action in enumerate(self.action_ideas, 1)
        )

        return (
            f"{title}\n{underline}\n\n"
            f"Top Themes:\n{themes_text}\n\n"
            f"What Users Are Saying:\n{quotes_text}\n\n"
            f"Recommended Actions:\n{actions_text}\n"
        )

    def to_dashboard_dict(self) -> dict[str, Any]:
        """
        Convert the pulse object to the exact frontend dashboard schema
        expected by Designs/groww-pulse.
        """
        themes_data = [
            {
                "rank": i,
                "theme": theme.name,
                "reviews": theme.review_count,
            }
            for i, theme in enumerate(self.top_themes, 1)
        ]

        quotes_data = []
        # Add primary 3 pulse quotes
        for i, quote_text in enumerate(self.quotes, 1):
            theme_obj = self.top_themes[i - 1] if i - 1 < len(self.top_themes) else self.top_themes[0]
            clean_q = _clean_quote(quote_text)
            sentiment = _detect_sentiment(clean_q, theme_obj.sentiment)
            quotes_data.append({
                "id": f"q{i}",
                "theme": theme_obj.name,
                "sentiment": sentiment,
                "quote": clean_q,
            })

        # Also add any distinct extra top quotes from the top themes if available
        seen = {_clean_quote(q).lower() for q in self.quotes}
        q_counter = len(quotes_data) + 1
        for theme_obj in self.top_themes:
            for extra in theme_obj.top_quotes:
                clean_extra = _clean_quote(extra)
                if clean_extra.lower() not in seen and len(clean_extra) >= 20:
                    seen.add(clean_extra.lower())
                    sentiment = _detect_sentiment(clean_extra, theme_obj.sentiment)
                    quotes_data.append({
                        "id": f"q{q_counter}",
                        "theme": theme_obj.name,
                        "sentiment": sentiment,
                        "quote": clean_extra,
                    })
                    q_counter += 1

        actions_data = []
        for i, action_text in enumerate(self.action_ideas, 1):
            theme_obj = self.top_themes[i - 1] if i - 1 < len(self.top_themes) else self.top_themes[0]
            matched_theme = theme_obj.name
            for t in self.top_themes:
                if t.name.lower() in action_text.lower():
                    matched_theme = t.name
                    break
            actions_data.append({
                "id": f"a{i}",
                "rank": i,
                "theme": matched_theme,
                "action": action_text.strip(),
            })

        return {
            "week": self.week_of.isoformat(),
            "themes": themes_data,
            "quotes": quotes_data,
            "actions": actions_data,
        }


# ── Helper: Quote Cleaning ────────────────────────────────────────────────────

def _clean_quote(quote: str) -> str:
    """Normalize internal whitespace and strip existing enclosing quotes."""
    text = " ".join(quote.split())
    text = text.strip("\"' \t\r\n")
    return text


def _detect_sentiment(text: str, default_sentiment: str = "Negative") -> str:
    """Determine whether a quote text conveys Positive or Negative sentiment."""
    lower = text.lower()
    negative_signals = [
        "poor", "worst", "bad", "delay", "issue", "problem", "disappoint", "pathetic",
        "stuck", "error", "horrible", "fraud", "loot", "waste", "cheat", "scam",
        "slow", "fail", "not working", "unable", "complaint", "चोर", "लूटा", "घटिया"
    ]
    positive_signals = [
        "good", "great", "excellent", "best", "love", "awesome", "smooth", "kudos",
        "superb", "helpful", "easy", "nice", "fast", "wonderful", "perfect", "बढ़िया", "शानदार"
    ]
    neg_score = sum(1 for w in negative_signals if w in lower)
    pos_score = sum(1 for w in positive_signals if w in lower)
    if neg_score > pos_score:
        return "Negative"
    elif pos_score > neg_score:
        return "Positive"
    return "Positive" if default_sentiment.lower() == "positive" else "Negative"


# ── Theme & Quote Selection ───────────────────────────────────────────────────

def select_top_themes(themes: list[Theme], count: int = 3) -> list[Theme]:
    """
    Select the top *count* themes sorted by review count descending.

    Raises:
        ValueError: If themes list is empty or has fewer than *count* themes.
    """
    if not themes:
        raise ValueError("Cannot select top themes from an empty list.")
    if len(themes) < count:
        raise ValueError(
            f"At least {count} themes required to generate pulse, got {len(themes)}."
        )

    # Sort descending by review_count; stable sort preserves order on ties
    sorted_themes = sorted(themes, key=lambda t: -t.review_count)
    return sorted_themes[:count]


def select_theme_quote(theme: Theme) -> str:
    """
    Select exactly 1 verbatim quote for *theme*.

    Selection priority:
      1. First quote in ``theme.top_quotes`` (which is already sorted by thumbs_up > 0 and length).
      2. If ``top_quotes`` is empty, pick the best candidate from ``theme.reviews``.

    Raises:
        ValueError: If the theme has no quotes or reviews available.
    """
    if theme.top_quotes:
        return _clean_quote(theme.top_quotes[0])

    if theme.reviews:
        # Filter for meaningful length
        candidates = [r for r in theme.reviews if len(r.text.strip()) >= 30]
        if not candidates:
            candidates = [r for r in theme.reviews if r.text.strip()]
        if candidates:
            candidates.sort(key=lambda r: (-r.thumbs_up, -len(r.text)))
            return _clean_quote(candidates[0].text)

    raise ValueError(f"Theme {theme.name!r} has no quotes or reviews to select from.")


def select_quotes_for_themes(themes: list[Theme]) -> list[str]:
    """Select 1 verbatim quote per theme in *themes*."""
    return [select_theme_quote(t) for t in themes]


# ── Action Ideas Prompt & Generation ──────────────────────────────────────────

_ACTION_IDEAS_PROMPT_TEMPLATE = """\
You are a senior product and engineering leader at Groww (India's leading investment and trading app).

Based on the top 3 user review themes and verbatim user quotes from the past week, \
generate EXACTLY 3 concrete, high-impact product or engineering action recommendations.

Top Themes:
{themes_summary}

User Quotes:
{quotes_summary}

RULES:
- Provide EXACTLY 3 action ideas.
- Each action idea must be concrete, specific to Groww, and directly address user pain points.
- Keep each action idea concise (1 sentence, 15-25 words) to ensure scannability.
- Do NOT use generic advice like "improve customer support" or "fix bugs". Be specific \
(e.g., "Implement pre-trade brokerage breakdown modal to eliminate fee transparency complaints").

OUTPUT FORMAT — respond ONLY with a valid JSON object in this exact format, with NO markdown fences:
{{
  "action_ideas": [
    "Action idea 1...",
    "Action idea 2...",
    "Action idea 3..."
  ]
}}
"""


def _format_themes_summary(themes: list[Theme]) -> str:
    lines = []
    for i, t in enumerate(themes, 1):
        lines.append(f"{i}. {t.name} ({t.review_count} reviews, sentiment: {t.sentiment})")
    return "\n".join(lines)


def _format_quotes_summary(quotes: list[str]) -> str:
    lines = []
    for i, q in enumerate(quotes, 1):
        lines.append(f'{i}. "{q}"')
    return "\n".join(lines)


def _generate_fallback_action_ideas(themes: list[Theme]) -> list[str]:
    """Deterministic fallback action ideas grounded in theme names when LLM fails."""
    actions = [
        f"Prioritize workflow optimizations and address core complaints in {themes[0].name}.",
        f"Improve self-service transparency and communication regarding {themes[1].name}.",
        f"Enhance platform monitoring and diagnostic tooling for {themes[2].name}.",
    ]
    return actions


def generate_action_ideas(
    themes: list[Theme],
    quotes: list[str],
    llm: LLMClient,
    max_retries: int | None = None,
) -> list[str]:
    """
    Generate 3 concrete action ideas using the configured LLM.

    Args:
        themes: Top 3 Theme objects.
        quotes: 3 verbatim user quotes.
        llm: Configured LLMClient instance.
        max_retries: Max retry attempts on parse or API failure.

    Returns:
        List of 3 concrete action idea strings.
    """
    _max_retries = max_retries if max_retries is not None else settings.llm_max_retries
    prompt = _ACTION_IDEAS_PROMPT_TEMPLATE.format(
        themes_summary=_format_themes_summary(themes),
        quotes_summary=_format_quotes_summary(quotes),
    )

    for attempt in range(1, _max_retries + 1):
        try:
            logger.debug(
                "Requesting action ideas from LLM",
                extra={"attempt": attempt, "theme_count": len(themes)},
            )
            raw_response = llm.generate(prompt)
            data = _extract_json(raw_response)

            ideas = data.get("action_ideas", [])
            if isinstance(ideas, list) and len(ideas) >= 3:
                cleaned_ideas = [str(idea).strip() for idea in ideas[:3] if str(idea).strip()]
                if len(cleaned_ideas) == 3:
                    return cleaned_ideas

            logger.warning(
                "LLM returned invalid action_ideas count or format",
                extra={"attempt": attempt, "received_count": len(ideas) if isinstance(ideas, list) else 0},
            )

        except Exception as exc:
            logger.warning(
                "Action idea generation attempt failed",
                extra={"attempt": attempt, "error": str(exc)},
            )

        if attempt < _max_retries:
            time.sleep(2 ** attempt)

    logger.warning("All LLM attempts failed for action ideas; using theme-grounded fallback")
    return _generate_fallback_action_ideas(themes)


# ── Markdown Assembly & Word Limit Enforcement ────────────────────────────────

def render_pulse_markdown(
    week_of: date,
    top_themes: list[Theme],
    quotes: list[str],
    action_ideas: list[str],
) -> str:
    """
    Assemble the Markdown pulse body according to the standard template.
    """
    date_str = week_of.strftime("%B %d, %Y")

    themes_block = "\n".join(
        f"{i}. {t.name} — {t.review_count} reviews"
        for i, t in enumerate(top_themes, 1)
    )

    quotes_block = "\n".join(
        f'> "{_clean_quote(q)}"'
        for q in quotes
    )

    actions_block = "\n".join(
        f"{i}. {action.strip()}"
        for i, action in enumerate(action_ideas, 1)
    )

    body = (
        f"# Groww Weekly Review Pulse — Week of {date_str}\n\n"
        f"## Top Themes\n"
        f"{themes_block}\n\n"
        f"## What Users Are Saying\n"
        f"{quotes_block}\n\n"
        f"## Recommended Actions\n"
        f"{actions_block}\n"
    )
    return body


def _count_words(text: str) -> int:
    """Count words in text using whitespace splitting."""
    return len(text.split())


def enforce_word_limit(
    week_of: date,
    top_themes: list[Theme],
    quotes: list[str],
    action_ideas: list[str],
    max_words: int = 250,
) -> tuple[str, list[str], int]:
    """
    Ensure the assembled pulse body does not exceed *max_words*.

    If over limit:
      - Trims action ideas progressively.
      - Quotes are strictly NEVER trimmed (maintained verbatim).
      - Logs a warning if truncation occurs.

    Returns:
        Tuple of (final_body, final_action_ideas, final_word_count).
    """
    current_actions = list(action_ideas)
    body = render_pulse_markdown(week_of, top_themes, quotes, current_actions)
    word_count = _count_words(body)

    if word_count <= max_words:
        return body, current_actions, word_count

    logger.warning(
        "Pulse body exceeded max word limit — trimming action ideas to enforce limit",
        extra={"initial_word_count": word_count, "max_words": max_words},
    )

    # Iteratively trim words from the longest action ideas
    while word_count > max_words:
        # Find index of longest action idea
        longest_idx = max(range(len(current_actions)), key=lambda i: len(current_actions[i].split()))
        words = current_actions[longest_idx].split()

        if len(words) <= 1:
            # Cannot trim action ideas any further
            break

        # Remove trailing word and ensure ellipsis
        trimmed_words = words[:-1]
        trimmed_text = " ".join(trimmed_words)
        if not trimmed_text.endswith("..."):
            trimmed_text = trimmed_text.rstrip(".,;:!-") + "..."
        current_actions[longest_idx] = trimmed_text

        body = render_pulse_markdown(week_of, top_themes, quotes, current_actions)
        word_count = _count_words(body)

    # If still over limit, trim theme descriptions/names (never quotes)
    if word_count > max_words:
        trimmed_themes = []
        for t in top_themes:
            t_words = t.name.split()
            if len(t_words) > 2:
                short_name = " ".join(t_words[:2]) + "..."
                trimmed_themes.append(
                    Theme(
                        name=short_name,
                        review_count=t.review_count,
                        reviews=t.reviews,
                        sentiment=t.sentiment,
                        top_quotes=t.top_quotes,
                    )
                )
            else:
                trimmed_themes.append(t)
        body = render_pulse_markdown(week_of, trimmed_themes, quotes, current_actions)
        word_count = _count_words(body)

    return body, current_actions, word_count


# ── Public Entry Point ────────────────────────────────────────────────────────

def generate_pulse(
    themes: list[Theme],
    *,
    week_of: date | None = None,
    llm: LLMClient | None = None,
    max_words: int | None = None,
) -> Pulse:
    """
    Generate an executive weekly review pulse from clustered themes.

    Args:
        themes:     List of Theme objects produced by ``cluster()``.
                    Must contain at least 3 themes.
        week_of:    Date representing the pulse week. Defaults to ``date.today()``.
        llm:        Optional LLMClient instance. Defaults to configured provider client.
        max_words:  Override ``settings.pulse_max_words`` (default 250).

    Returns:
        A validated ``Pulse`` object with top 3 themes, 3 verbatim quotes,
        3 action ideas, and formatted Markdown body strictly <= 250 words.

    Raises:
        ValueError: If themes list is empty or contains fewer than 3 themes.
    """
    if not themes:
        raise ValueError("Cannot generate pulse from empty themes list.")

    # 1. Select top 3 themes (validates theme count before LLM setup)
    top_3_themes = select_top_themes(themes, count=settings.pulse_themes)

    _week_of = week_of if week_of is not None else date.today()
    if isinstance(_week_of, datetime):
        _week_of = _week_of.date()

    _max_words = max_words if max_words is not None else settings.pulse_max_words
    _llm = llm if llm is not None else _build_llm_client()

    logger.info(
        "Starting pulse generation",
        extra={
            "theme_count": len(themes),
            "week_of": _week_of.isoformat(),
            "max_words": _max_words,
        },
    )

    # 2. Select 1 verbatim quote per top theme (3 quotes total)
    quotes = select_quotes_for_themes(top_3_themes)

    # 3. Generate 3 concrete action ideas via LLM
    action_ideas = generate_action_ideas(top_3_themes, quotes, llm=_llm)

    # 4. Assemble Markdown body and enforce word limit
    body, final_action_ideas, word_count = enforce_word_limit(
        week_of=_week_of,
        top_themes=top_3_themes,
        quotes=quotes,
        action_ideas=action_ideas,
        max_words=_max_words,
    )

    logger.info(
        "Pulse generation complete",
        extra={
            "word_count": word_count,
            "themes": [t.name for t in top_3_themes],
        },
    )

    return Pulse(
        week_of=_week_of,
        top_themes=top_3_themes,
        quotes=quotes,
        action_ideas=final_action_ideas,
        body=body,
        word_count=word_count,
    )


# ── Frontend Dashboard Data Export ───────────────────────────────────────────

def export_dashboard_data(
    pulse: Pulse,
    target_dir: Path | str | None = None,
) -> dict[str, Any]:
    """
    Export pulse data to pulse.json and pulse.js in Designs/groww-pulse/data/
    for the interactive frontend dashboard.

    Args:
        pulse: The generated Pulse object.
        target_dir: Directory where pulse.json and pulse.js will be written.
                    Defaults to Designs/groww-pulse/data.

    Returns:
        Structured dashboard dictionary conforming to the frontend schema.
    """
    data = pulse.to_dashboard_dict()

    if target_dir is None:
        target_path = Path(__file__).resolve().parents[2] / "Designs" / "groww-pulse" / "data"
    else:
        target_path = Path(target_dir)

    target_path.mkdir(parents=True, exist_ok=True)

    json_path = target_path / "pulse.json"
    js_path = target_path / "pulse.js"

    json_content = json.dumps(data, indent=2, ensure_ascii=False)
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(json_content + "\n")

    js_content = f"// Auto-generated by Groww Review Intelligence Agent\nwindow.PULSE_DATA = {json_content};\n"
    with open(js_path, "w", encoding="utf-8") as f:
        f.write(js_content)

    # Sync to public/data if public folder exists (for Vercel root deployment)
    if target_dir is None:
        public_dir = Path(__file__).resolve().parents[2] / "public" / "data"
        if public_dir.parent.exists():
            public_dir.mkdir(parents=True, exist_ok=True)
            with open(public_dir / "pulse.json", "w", encoding="utf-8") as f:
                f.write(json_content + "\n")
            with open(public_dir / "pulse.js", "w", encoding="utf-8") as f:
                f.write(js_content)

    logger.info(
        "Exported dashboard data for frontend",
        extra={"json_path": str(json_path), "js_path": str(js_path), "week": data["week"]},
    )
    return data
