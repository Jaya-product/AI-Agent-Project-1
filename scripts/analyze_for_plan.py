import json
from datetime import datetime
from collections import Counter

with open(r'c:\Users\jaya.manocha\Documents\myProjects\AI Agent\scripts\groww_reviews.json', 'r', encoding='utf-8') as f:
    reviews = json.load(f)

total = len(reviews)
ratings = Counter(r['rating'] for r in reviews)
dates = [datetime.fromisoformat(r['date']) for r in reviews]
min_date = min(dates)
max_date = max(dates)
avg_rating = sum(r['rating'] for r in reviews) / total

short_count = sum(1 for r in reviews if len(r['text'].strip()) < 20)
non_english = sum(1 for r in reviews if not r['text'].isascii())

high_thumbs = [(r['text'][:120], r['rating'], r['thumbs_up']) for r in reviews if r['thumbs_up'] >= 5]
high_thumbs.sort(key=lambda x: -x[2])

low_reviews = [(r['text'], r['thumbs_up']) for r in reviews if r['rating'] <= 2 and len(r['text']) > 30]
low_reviews.sort(key=lambda x: -x[1])

three_star = [(r['text'], r['thumbs_up']) for r in reviews if r['rating'] == 3 and len(r['text']) > 20]

with open('review_analysis.txt', 'w', encoding='utf-8') as out:
    out.write(f'Total reviews: {total}\n')
    out.write(f'Date range: {min_date} to {max_date}\n')
    out.write(f'Avg rating: {avg_rating:.2f}\n')
    out.write(f'Rating dist: {dict(sorted(ratings.items()))}\n')
    out.write(f'Short reviews (<20 chars): {short_count} ({100*short_count/total:.1f}%)\n')
    out.write(f'Non-ASCII/multilingual: {non_english} ({100*non_english/total:.1f}%)\n\n')

    out.write('--- TOP THUMBS-UP REVIEWS ---\n')
    for text, rating, thumbs in high_thumbs[:10]:
        out.write(f'[{rating}star | {thumbs} thumbs] {text}\n')

    out.write('\n--- LOW RATING (1-2 star) SUBSTANTIVE REVIEWS ---\n')
    for text, thumbs in low_reviews[:20]:
        out.write(f'[thumbs:{thumbs}] {text[:200]}\n')

    out.write('\n--- 3-STAR REVIEWS ---\n')
    for text, thumbs in three_star:
        out.write(f'[thumbs:{thumbs}] {text[:200]}\n')

print('Done. Written to review_analysis.txt')
