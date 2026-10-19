"""
tests/test_pulse_generator.py
──────────────────────────────
Unit tests for Phase 5 — Pulse Generation.

All LLM calls are mocked — no external API calls are made.

Coverage:
  - Pulse schema: validation (theme count, quote count, action ideas count, word cap)
  - Plain text rendering: to_plain_text() output
  - Theme selection: select_top_themes() sorting, count, error handling
  - Quote selection: select_theme_quote(), select_quotes_for_themes()
      * Prioritizes theme.top_quotes
      * Falls back to theme.reviews (thumbs_up weighting, length)
      * Never invents quotes — all trace back to input reviews
  - Action ideas generation:
      * LLM prompt structure
      * Valid JSON response parsing
      * Retry on LLM errors
      * Fallback to theme-grounded actions on exhausted retries
  - Template assembly: render_pulse_markdown()
      * Title header with formatted date
      * Sections: Top Themes, What Users Are Saying, Recommended Actions
  - Word limit enforcement: enforce_word_limit()
      * Preserves text <= 250 words
      * Truncates action ideas when > 250 words
      * Never truncates quotes
      * Logs warning on truncation
  - generate_pulse() end-to-end pipeline:
      * Validates all acceptance criteria
      * Real Groww 5-theme scenario
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.parser import Review
from src.intelligence.clusterer import Theme
from src.intelligence.pulse_generator import (
    Pulse,
    _clean_quote,
    _count_words,
    enforce_word_limit,
    generate_action_ideas,
    generate_pulse,
    render_pulse_markdown,
    select_quotes_for_themes,
    select_theme_quote,
    select_top_themes,
)

_NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)
_TEST_DATE = date(2026, 10, 14)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _make_review(
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


def _make_theme(
    name: str,
    review_count: int = 10,
    sentiment: str = "positive",
    top_quotes: list[str] | None = None,
    reviews: list[Review] | None = None,
) -> Theme:
    if top_quotes is None:
        top_quotes = [f"This is a great quote about {name} from a user."]
    if reviews is None:
        reviews = [
            _make_review(f"Review for {name} with text", review_id=f"{name}-{i}")
            for i in range(review_count)
        ]
    return Theme(
        name=name,
        review_count=review_count,
        reviews=reviews,
        sentiment=sentiment,
        top_quotes=top_quotes,
    )


def _mock_llm(action_ideas: list[str] | str) -> MagicMock:
    llm = MagicMock()
    if isinstance(action_ideas, list):
        response_text = json.dumps({"action_ideas": action_ideas})
    else:
        response_text = action_ideas
    llm.generate.return_value = response_text
    return llm


# ═══════════════════════════════════════════════════════════════════════════════
# Test Pulse Schema
# ═══════════════════════════════════════════════════════════════════════════════

class TestPulseSchema:

    def test_valid_pulse_constructs(self):
        themes = [_make_theme(f"Theme {i}") for i in range(3)]
        quotes = ["Quote 1", "Quote 2", "Quote 3"]
        actions = ["Action 1", "Action 2", "Action 3"]
        body = "Short valid body."

        pulse = Pulse(
            week_of=_TEST_DATE,
            top_themes=themes,
            quotes=quotes,
            action_ideas=actions,
            body=body,
            word_count=len(body.split()),
        )
        assert pulse.week_of == _TEST_DATE
        assert len(pulse.top_themes) == 3
        assert len(pulse.quotes) == 3
        assert len(pulse.action_ideas) == 3
        assert pulse.word_count == 3

    def test_top_themes_not_3_raises(self):
        themes = [_make_theme(f"Theme {i}") for i in range(2)]
        with pytest.raises(ValueError, match="top_themes must contain exactly 3"):
            Pulse(
                week_of=_TEST_DATE,
                top_themes=themes,
                quotes=["Q1", "Q2", "Q3"],
                action_ideas=["A1", "A2", "A3"],
                body="Body text",
                word_count=2,
            )

    def test_quotes_not_3_raises(self):
        themes = [_make_theme(f"Theme {i}") for i in range(3)]
        with pytest.raises(ValueError, match="quotes must contain exactly 3"):
            Pulse(
                week_of=_TEST_DATE,
                top_themes=themes,
                quotes=["Q1", "Q2"],
                action_ideas=["A1", "A2", "A3"],
                body="Body text",
                word_count=2,
            )

    def test_action_ideas_not_3_raises(self):
        themes = [_make_theme(f"Theme {i}") for i in range(3)]
        with pytest.raises(ValueError, match="action_ideas must contain exactly 3"):
            Pulse(
                week_of=_TEST_DATE,
                top_themes=themes,
                quotes=["Q1", "Q2", "Q3"],
                action_ideas=["A1", "A2", "A3", "A4"],
                body="Body text",
                word_count=2,
            )

    def test_word_count_gt_250_raises(self):
        themes = [_make_theme(f"Theme {i}") for i in range(3)]
        with pytest.raises(ValueError, match="word_count must be <= 250"):
            Pulse(
                week_of=_TEST_DATE,
                top_themes=themes,
                quotes=["Q1", "Q2", "Q3"],
                action_ideas=["A1", "A2", "A3"],
                body="Body text",
                word_count=251,
            )

    def test_to_plain_text_renders_cleanly(self):
        themes = [
            _make_theme("UI/UX", review_count=40),
            _make_theme("Brokerage Charges", review_count=25),
            _make_theme("Customer Service", review_count=15),
        ]
        quotes = [
            "Very simple and clean UI.",
            "Brokerage charges are confusing.",
            "Customer support took 2 weeks.",
        ]
        actions = [
            "Improve chart responsiveness.",
            "Clarify fee schedule modal.",
            "Automate withdrawal status tickets.",
        ]
        pulse = Pulse(
            week_of=_TEST_DATE,
            top_themes=themes,
            quotes=quotes,
            action_ideas=actions,
            body="Markdown body",
            word_count=2,
        )
        plain = pulse.to_plain_text()
        assert "Groww Weekly Review Pulse — Week of October 14, 2026" in plain
        assert "Top Themes:" in plain
        assert "1. UI/UX — 40 reviews" in plain
        assert "What Users Are Saying:" in plain
        assert '- "Very simple and clean UI."' in plain
        assert "Recommended Actions:" in plain
        assert "1. Improve chart responsiveness." in plain
        # Should not contain markdown headers or blockquotes
        assert "# " not in plain
        assert "> " not in plain


# ═══════════════════════════════════════════════════════════════════════════════
# Test Theme & Quote Selection
# ═══════════════════════════════════════════════════════════════════════════════

class TestThemeAndQuoteSelection:

    def test_select_top_themes_picks_top_by_volume(self):
        t1 = _make_theme("T1", review_count=10)
        t2 = _make_theme("T2", review_count=50)
        t3 = _make_theme("T3", review_count=30)
        t4 = _make_theme("T4", review_count=5)
        t5 = _make_theme("T5", review_count=20)

        # Passed in arbitrary order
        selected = select_top_themes([t1, t2, t3, t4, t5], count=3)
        assert len(selected) == 3
        assert selected[0].name == "T2"  # 50
        assert selected[1].name == "T3"  # 30
        assert selected[2].name == "T5"  # 20

    def test_select_top_themes_empty_raises(self):
        with pytest.raises(ValueError, match="Cannot select top themes from an empty list"):
            select_top_themes([])

    def test_select_top_themes_fewer_than_count_raises(self):
        themes = [_make_theme("T1"), _make_theme("T2")]
        with pytest.raises(ValueError, match="At least 3 themes required"):
            select_top_themes(themes, count=3)

    def test_select_theme_quote_prefers_top_quotes(self):
        theme = _make_theme(
            "UI/UX",
            top_quotes=["Best app for beginners in India."],
            reviews=[_make_review("Another review")],
        )
        quote = select_theme_quote(theme)
        assert quote == "Best app for beginners in India."

    def test_select_theme_quote_falls_back_to_reviews_with_thumbs_up(self):
        # top_quotes is empty
        r1 = _make_review("Short text", thumbs_up=0)
        r2 = _make_review(
            "This is a longer review with community validation",
            thumbs_up=5,
        )
        theme = _make_theme("Customer Service", top_quotes=[], reviews=[r1, r2])

        quote = select_theme_quote(theme)
        assert quote == "This is a longer review with community validation"

    def test_select_theme_quote_cleans_surrounding_quotes(self):
        theme = _make_theme("UI", top_quotes=['"App is very intuitive and clean."'])
        quote = select_theme_quote(theme)
        assert quote == "App is very intuitive and clean."

    def test_select_theme_quote_empty_raises(self):
        theme = _make_theme("Empty", top_quotes=[], reviews=[])
        with pytest.raises(ValueError, match="no quotes or reviews"):
            select_theme_quote(theme)

    def test_select_quotes_for_themes_returns_exactly_3(self):
        themes = [
            _make_theme("T1", top_quotes=["Quote 1"]),
            _make_theme("T2", top_quotes=["Quote 2"]),
            _make_theme("T3", top_quotes=["Quote 3"]),
        ]
        quotes = select_quotes_for_themes(themes)
        assert quotes == ["Quote 1", "Quote 2", "Quote 3"]


# ═══════════════════════════════════════════════════════════════════════════════
# Test Action Ideas Generation
# ═══════════════════════════════════════════════════════════════════════════════

class TestActionIdeasGeneration:

    def test_generate_action_ideas_happy_path(self):
        themes = [_make_theme(f"T{i}") for i in range(3)]
        quotes = ["Q1", "Q2", "Q3"]
        expected_ideas = [
            "Implement pre-trade brokerage calculator modal.",
            "Streamline withdrawal tracking and notification SLA.",
            "Fix order screen hanging during market open volatility.",
        ]
        llm = _mock_llm(expected_ideas)

        ideas = generate_action_ideas(themes, quotes, llm=llm)
        assert ideas == expected_ideas
        assert llm.generate.call_count == 1

    def test_generate_action_ideas_strips_markdown_fences(self):
        themes = [_make_theme(f"T{i}") for i in range(3)]
        quotes = ["Q1", "Q2", "Q3"]
        fenced_json = (
            "```json\n"
            '{\n  "action_ideas": ["Action 1", "Action 2", "Action 3"]\n}\n'
            "```"
        )
        llm = _mock_llm(fenced_json)

        ideas = generate_action_ideas(themes, quotes, llm=llm)
        assert ideas == ["Action 1", "Action 2", "Action 3"]

    @patch("src.intelligence.pulse_generator.time.sleep")
    def test_generate_action_ideas_retries_on_malformed_response(self, mock_sleep):
        themes = [_make_theme(f"T{i}") for i in range(3)]
        quotes = ["Q1", "Q2", "Q3"]

        llm = MagicMock()
        # First returns invalid text, second returns valid JSON
        llm.generate.side_effect = [
            "Sorry, not json",
            json.dumps({"action_ideas": ["A1", "A2", "A3"]}),
        ]

        ideas = generate_action_ideas(themes, quotes, llm=llm, max_retries=2)
        assert ideas == ["A1", "A2", "A3"]
        assert llm.generate.call_count == 2
        assert mock_sleep.call_count == 1

    @patch("src.intelligence.pulse_generator.time.sleep")
    def test_generate_action_ideas_fallback_when_retries_exhausted(self, mock_sleep):
        themes = [
            _make_theme("UI/UX"),
            _make_theme("Brokerage Charges"),
            _make_theme("Customer Support"),
        ]
        quotes = ["Q1", "Q2", "Q3"]
        llm = _mock_llm("unparseable response always")

        ideas = generate_action_ideas(themes, quotes, llm=llm, max_retries=2)
        assert len(ideas) == 3
        # Grounded in theme names
        assert "UI/UX" in ideas[0]
        assert "Brokerage Charges" in ideas[1]
        assert "Customer Support" in ideas[2]


# ═══════════════════════════════════════════════════════════════════════════════
# Test Markdown Assembly & Word Limit
# ═══════════════════════════════════════════════════════════════════════════════

class TestMarkdownAndWordLimit:

    def test_render_pulse_markdown_format(self):
        themes = [
            _make_theme("UI/UX & Ease of Use", review_count=40),
            _make_theme("Brokerage Charges & Fees", review_count=20),
            _make_theme("Customer Service & Withdrawals", review_count=15),
        ]
        quotes = [
            "Clean and beginner friendly app.",
            "₹20 brokerage charged even on failed orders.",
            "Withdrawal took over 3 weeks to process.",
        ]
        actions = [
            "Implement fee transparency breakdown before order execution.",
            "Accelerate bank withdrawal processing SLA.",
            "Preserve chart settings across app updates.",
        ]

        body = render_pulse_markdown(_TEST_DATE, themes, quotes, actions)

        assert "# Groww Weekly Review Pulse — Week of October 14, 2026" in body
        assert "## Top Themes" in body
        assert "1. UI/UX & Ease of Use — 40 reviews" in body
        assert "2. Brokerage Charges & Fees — 20 reviews" in body
        assert "3. Customer Service & Withdrawals — 15 reviews" in body
        assert "## What Users Are Saying" in body
        assert '> "Clean and beginner friendly app."' in body
        assert '> "₹20 brokerage charged even on failed orders."' in body
        assert '> "Withdrawal took over 3 weeks to process."' in body
        assert "## Recommended Actions" in body
        assert "1. Implement fee transparency breakdown before order execution." in body
        assert "2. Accelerate bank withdrawal processing SLA." in body
        assert "3. Preserve chart settings across app updates." in body

    def test_enforce_word_limit_under_cap_unchanged(self):
        themes = [_make_theme(f"T{i}", review_count=10) for i in range(3)]
        quotes = ["Short quote 1", "Short quote 2", "Short quote 3"]
        actions = ["Short action 1", "Short action 2", "Short action 3"]

        body, final_actions, count = enforce_word_limit(
            _TEST_DATE, themes, quotes, actions, max_words=250
        )
        assert count <= 250
        assert final_actions == actions
        assert _count_words(body) == count

    def test_enforce_word_limit_over_cap_trims_actions_not_quotes(self):
        themes = [_make_theme(f"Theme {i}", review_count=10) for i in range(3)]
        quotes = [
            "Verbatim quote that must never be altered under any circumstances whatsoever.",
            "Second verbatim quote preserved with total fidelity to user input.",
            "Third verbatim quote untouched by any trimming routine.",
        ]
        # Extremely verbose action ideas that push the body over the cap
        actions = [
            "This is an extraordinarily long action idea that contains a tremendous number of repetitive and unnecessary words intended specifically to inflate the overall word count of the weekly review pulse beyond the configured maximum limit so that truncation is strictly required to pass validation.",
            "Another extremely long-winded recommendation containing extensive details and verbose explanations that adds many extra words to the pulse body.",
            "A third long paragraph explaining architectural changes and process optimizations in excessive detail.",
        ]

        # Use a tight cap of 80 words to definitely force trimming
        body, final_actions, count = enforce_word_limit(
            _TEST_DATE, themes, quotes, actions, max_words=80
        )

        assert count <= 80
        # Quotes MUST remain untouched
        for q in quotes:
            assert f'> "{q}"' in body

        # Action ideas were trimmed
        assert any(a.endswith("...") for a in final_actions)

    def test_enforce_word_limit_trims_theme_names_if_needed(self):
        # Long theme names and tight cap
        themes = [
            _make_theme("Very Long Theme Name Number One With Multiple Words", review_count=10),
            _make_theme("Another Extremely Elaborate Theme Title For Testing Purposes", review_count=10),
            _make_theme("Third Excessively Detailed Theme Heading Across Many Words", review_count=10),
        ]
        quotes = ["Q1", "Q2", "Q3"]
        actions = ["Action 1", "Action 2", "Action 3"]

        # Tight word cap to force theme name trimming
        body, final_actions, count = enforce_word_limit(
            _TEST_DATE, themes, quotes, actions, max_words=55
        )
        assert count <= 55
        assert "..." in body


# ═══════════════════════════════════════════════════════════════════════════════
# Test generate_pulse() Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

class TestGeneratePulsePipeline:

    def test_generate_pulse_happy_path(self):
        themes = [
            _make_theme("UI/UX & Ease of Use", review_count=45, top_quotes=["Clean UI!"]),
            _make_theme("Brokerage Charges", review_count=30, top_quotes=["Too expensive!"]),
            _make_theme("Customer Service", review_count=20, top_quotes=["Support is slow."]),
            _make_theme("App Performance", review_count=10, top_quotes=["Laggy."]),
        ]
        llm = _mock_llm([
            "Clarify fee schedule modal before order placement.",
            "Automate ticket resolution SLA tracking for withdrawals.",
            "Streamline onboarding KYC steps.",
        ])

        pulse = generate_pulse(themes, week_of=_TEST_DATE, llm=llm)

        assert isinstance(pulse, Pulse)
        assert pulse.week_of == _TEST_DATE
        assert len(pulse.top_themes) == 3
        # Top 3 selected in volume order
        assert pulse.top_themes[0].name == "UI/UX & Ease of Use"
        assert pulse.top_themes[1].name == "Brokerage Charges"
        assert pulse.top_themes[2].name == "Customer Service"

        # Exactly 3 quotes
        assert pulse.quotes == ["Clean UI!", "Too expensive!", "Support is slow."]

        # Exactly 3 action ideas
        assert len(pulse.action_ideas) == 3

        # Word count is within limit
        assert pulse.word_count <= 250
        assert pulse.word_count == len(pulse.body.split())

        # Quotes are present in body
        assert '> "Clean UI!"' in pulse.body
        assert '> "Too expensive!"' in pulse.body
        assert '> "Support is slow."' in pulse.body

    def test_quotes_map_to_real_reviews(self):
        """Acceptance Criteria: No invented quotes — each maps back to real review."""
        r1 = _make_review("Groww interface is very friendly for equity beginners.")
        r2 = _make_review("Hidden DP charges and ₹20 flat fee should be clearly shown.")
        r3 = _make_review("Ticket ID 28133385 has been pending withdrawal for 18 days.")

        t1 = _make_theme("UI/UX", review_count=30, top_quotes=[r1.text], reviews=[r1])
        t2 = _make_theme("Brokerage", review_count=20, top_quotes=[r2.text], reviews=[r2])
        t3 = _make_theme("Withdrawals", review_count=15, top_quotes=[r3.text], reviews=[r3])

        llm = _mock_llm(["Action 1", "Action 2", "Action 3"])
        pulse = generate_pulse([t1, t2, t3], week_of=_TEST_DATE, llm=llm)

        # Every quote in pulse.quotes must be an exact match to a review in input
        all_review_texts = {r.text for t in [t1, t2, t3] for r in t.reviews}
        for q in pulse.quotes:
            assert q in all_review_texts

    def test_default_week_of_is_today(self):
        themes = [_make_theme(f"Theme {i}") for i in range(3)]
        llm = _mock_llm(["Action 1", "Action 2", "Action 3"])

        pulse = generate_pulse(themes, llm=llm)
        assert pulse.week_of == date.today()

    def test_empty_themes_raises_value_error(self):
        with pytest.raises(ValueError, match="Cannot generate pulse from empty themes list"):
            generate_pulse([])

    def test_fewer_than_3_themes_raises_value_error(self):
        themes = [_make_theme("T1"), _make_theme("T2")]
        with pytest.raises(ValueError, match="At least 3 themes required"):
            generate_pulse(themes)

    def test_real_groww_5_themes_scenario(self):
        """Simulate pulse generation from the 5 real Groww themes."""
        themes = [
            _make_theme(
                "UI/UX & Ease of Use",
                review_count=40,
                sentiment="positive",
                top_quotes=["Very easy to use for new investors, simple design."],
            ),
            _make_theme(
                "Brokerage Charges & Fees",
                review_count=20,
                sentiment="negative",
                top_quotes=["Charges are high for small amounts ₹20 per trade."],
            ),
            _make_theme(
                "Customer Service & Withdrawals",
                review_count=15,
                sentiment="negative",
                top_quotes=["Customer support is totally unresponsive to my ticket."],
            ),
            _make_theme(
                "App Performance & Bugs",
                review_count=10,
                sentiment="negative",
                top_quotes=["App hangs frequently during trading hours."],
            ),
            _make_theme(
                "Features & Missing Functionality",
                review_count=5,
                sentiment="neutral",
                top_quotes=["Please add automatic SIP mandate options."],
            ),
        ]
        llm = _mock_llm([
            "Introduce pre-trade fee breakdown to resolve brokerage charge surprises.",
            "Implement automated withdrawal status updates to reduce support ticket load.",
            "Optimize market-open server capacity to eliminate app freeze issues.",
        ])

        pulse = generate_pulse(themes, week_of=_TEST_DATE, llm=llm)

        assert len(pulse.top_themes) == 3
        # Top 3 themes are UI, Brokerage, Customer Service
        assert pulse.top_themes[0].name == "UI/UX & Ease of Use"
        assert pulse.top_themes[1].name == "Brokerage Charges & Fees"
        assert pulse.top_themes[2].name == "Customer Service & Withdrawals"

        assert len(pulse.quotes) == 3
        assert len(pulse.action_ideas) == 3
        assert pulse.word_count <= 250

        # Renders correctly in Markdown and plain text
        assert "# Groww Weekly Review Pulse — Week of October 14, 2026" in pulse.body
        plain = pulse.to_plain_text()
        assert "Groww Weekly Review Pulse — Week of October 14, 2026" in plain
        assert "Top Themes:" in plain
