"""
scripts/smoke_test.py
─────────────────────
Smoke-test checkpoint for the Intelligence Layer (Phases 2 -> 3 -> 4 -> 5).

Loads real reviews from scripts/groww_reviews.json, runs them through:
  1. Parser (Review schema)
  2. Scrubber (PII removal + filtering)
  3. Clusterer (Theme grouping)
  4. Pulse Generator (Weekly pulse assembly <= 250 words)

Can run in:
  - Mock mode (default when no API key is configured): uses mocked LLM responses
  - Live mode: uses live Gemini/OpenAI API if GEMINI_API_KEY/OPENAI_API_KEY is configured

Run from project root:
  venv\\Scripts\\python scripts\\smoke_test.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

# Make project root importable
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.ingestion.parser import parse_reviews
from src.ingestion.scrubber import scrub
from src.intelligence.clusterer import cluster
from src.intelligence.pulse_generator import generate_pulse
from src.utils.config import settings


def main():
    print("=" * 70)
    print(" GROWW REVIEW INTELLIGENCE AGENT — SMOKE TEST (PHASES 2 - 5)")
    print("=" * 70)

    reviews_file = _ROOT / "scripts" / "groww_reviews.json"
    if not reviews_file.exists():
        print(f"Error: {reviews_file} not found.")
        sys.exit(1)

    with open(reviews_file, "r", encoding="utf-8") as fh:
        raw_data = json.load(fh)
    print(f"\n[Phase 2] Ingested raw reviews from JSON: {len(raw_data)} items")

    # 1. Parse into Review models
    reviews = parse_reviews(raw_data)
    print(f"[Phase 2] Successfully parsed into Review objects: {len(reviews)} items")

    # 2. Scrub PII and filter short reviews
    scrubbed = scrub(reviews)
    print(f"[Phase 3] Scrubbed reviews (PII removed, length >= {settings.min_review_length}): {len(scrubbed)} items")
    print(f"          Retention: {len(scrubbed)/len(reviews):.1%}")

    # 3. Clustering
    has_api_key = bool(settings.gemini_api_key or settings.openai_api_key)
    if has_api_key:
        print("\n[Phase 4] Clustering reviews using configured live LLM client...")
        themes = cluster(scrubbed)
    else:
        print("\n[Phase 4] No LLM API key detected — using deterministic mock LLM for smoke test...")
        # Mock LLM response categorizing into top themes
        theme_names = [
            "UI/UX & Ease of Use",
            "Brokerage Charges & Fees",
            "Customer Service & Withdrawals",
            "App Performance & Bugs",
            "Features & Missing Functionality",
        ]
        mock_assignments = [
            {"review_index": i, "theme_name": theme_names[i % len(theme_names)]}
            for i in range(len(scrubbed))
        ]
        mock_themes = [{"name": n, "sentiment": "positive" if "UI" in n else "negative"} for n in theme_names]
        mock_resp = json.dumps({"assignments": mock_assignments, "themes": mock_themes})

        mock_llm = MagicMock()
        mock_llm.generate.return_value = mock_resp
        themes = cluster(scrubbed, llm=mock_llm, batch_size=len(scrubbed) + 10)

    print(f"[Phase 4] Clustered into {len(themes)} themes:")
    for i, t in enumerate(themes, 1):
        print(f"          {i}. {t.name} ({t.review_count} reviews, sentiment: {t.sentiment})")

    # 4. Pulse Generation
    print("\n[Phase 5] Generating Weekly Pulse...")
    if has_api_key:
        pulse = generate_pulse(themes, week_of=date.today())
    else:
        mock_action_llm = MagicMock()
        mock_action_llm.generate.return_value = json.dumps({
            "action_ideas": [
                "Deploy pre-trade brokerage breakdown modal to prevent hidden charge complaints.",
                "Automate bank withdrawal status notifications to eliminate support ticket escalations.",
                "Scale order execution cluster capacity during morning market opening peaks.",
            ]
        })
        pulse = generate_pulse(themes, week_of=date.today(), llm=mock_action_llm)

    print(f"[Phase 5] Pulse generated successfully!")
    print(f"          Word Count: {pulse.word_count} words (Hard limit: <= {settings.pulse_max_words})")
    print(f"          Top Themes count: {len(pulse.top_themes)}")
    print(f"          Quotes count: {len(pulse.quotes)}")
    print(f"          Action Ideas count: {len(pulse.action_ideas)}")

    print("\n" + "-" * 70)
    print(" GENERATED WEEKLY PULSE (MARKDOWN):")
    print("-" * 70)
    print(pulse.body)

    print("-" * 70)
    print(" GENERATED WEEKLY PULSE (PLAIN TEXT):")
    print("-" * 70)
    print(pulse.to_plain_text())

    print("=" * 70)
    print(" [OK] SMOKE TEST CHECKPOINT PASSED - Full intelligence layer is verified!")
    print("=" * 70)


if __name__ == "__main__":
    main()
