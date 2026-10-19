"""
Scratch script: download real Groww Play Store reviews and save to JSON.
Run from project root: venv\Scripts\python scripts\download_reviews.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make src/ importable from the project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google_play_scraper import reviews as gps_reviews, Sort

APP_ID      = "com.nextbillion.groww"
LANG        = "en"
COUNTRY     = "in"
MAX_REVIEWS = 300          # fetch up to 300 reviews
OUT_FILE    = Path(__file__).resolve().parent / "groww_reviews.json"


def fetch_all() -> list[dict]:
    all_reviews = []
    token = None
    page  = 0

    while len(all_reviews) < MAX_REVIEWS:
        count = min(200, MAX_REVIEWS - len(all_reviews))
        print(f"  Fetching page {page + 1} ({count} reviews)...", flush=True)

        try:
            result, token = gps_reviews(
                APP_ID,
                lang=LANG,
                country=COUNTRY,
                sort=Sort.NEWEST,
                count=count,
                continuation_token=token,
            )
        except Exception as e:
            print(f"  ERROR on page {page + 1}: {e}")
            break

        if not result:
            print("  No more reviews returned by Play Store.")
            break

        all_reviews.extend(result)
        page += 1

        if token is None:
            print("  No continuation token — all pages exhausted.")
            break

    return all_reviews


def serialise(reviews: list[dict]) -> list[dict]:
    """Convert datetime objects to ISO strings for JSON output."""
    out = []
    for r in reviews:
        out.append({
            "id":     r.get("reviewId", ""),
            "rating": r.get("score", 0),
            "title":  r.get("title") or "",
            "text":   r.get("content", ""),
            "date":   r["at"].isoformat() if isinstance(r.get("at"), datetime) else str(r.get("at", "")),
            "thumbs_up": r.get("thumbsUpCount", 0),
        })
    return out


def main():
    print(f"\nDownloading Groww reviews from Play Store...")
    print(f"  App ID  : {APP_ID}")
    print(f"  Language: {LANG}  Country: {COUNTRY}")
    print(f"  Target  : {MAX_REVIEWS} reviews\n")

    raw = fetch_all()
    serialised = serialise(raw)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(serialised, f, ensure_ascii=False, indent=2)

    print(f"\nDone! {len(serialised)} reviews saved to:")
    print(f"  {OUT_FILE}\n")

    # ── Quick summary ─────────────────────────────────────────────────────────
    if serialised:
        ratings = [r["rating"] for r in serialised]
        avg     = sum(ratings) / len(ratings)
        dist    = {i: ratings.count(i) for i in range(1, 6)}
        oldest  = min(r["date"] for r in serialised)
        newest  = max(r["date"] for r in serialised)

        print("== Quick Summary ==================================")
        print(f"  Total reviews   : {len(serialised)}")
        print(f"  Average rating  : {avg:.2f} / 5")
        print(f"  Date range      : {oldest[:10]}  ->  {newest[:10]}")
        print(f"  Rating breakdown:")
        for star in range(5, 0, -1):
            bar = "#" * dist[star]
            print(f"    {star}*  {bar:<40}  ({dist[star]})")
        print("==================================================\n")

        print("== Sample Reviews (first 3) =======================")
        for i, r in enumerate(serialised[:3], 1):
            # Encode to ASCII safely for Windows terminals (reviews may contain Hindi/emoji)
            safe_title = r['title'].encode('ascii', 'replace').decode() if r['title'] else '(no title)'
            safe_text  = r['text'][:200].encode('ascii', 'replace').decode()
            ellipsis   = '...' if len(r['text']) > 200 else ''
            print(f"\n  [{i}] Rating: {r['rating']}/5   Date: {r['date'][:10]}")
            print(f"      Title : {safe_title}")
            print(f"      Text  : {safe_text}{ellipsis}")
        print("\n==================================================\n")


if __name__ == "__main__":
    main()
