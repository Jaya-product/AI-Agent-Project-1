import json, re
from pathlib import Path
from collections import Counter

data = json.loads(Path('scripts/groww_reviews.json').read_text(encoding='utf-8'))

texts   = [r['text'] for r in data]
lengths = [len(t) for t in texts]
ratings = [r['rating'] for r in data]

def is_english(text):
    if not text: return False
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return ascii_chars / len(text) > 0.8

non_english = [r for r in data if not is_english(r['text'])]
very_short  = [r for r in data if len(r['text'].strip()) < 20]
blank_title = [r for r in data if not r['title']]
emoji_only  = [r for r in data if r['text'].strip() and not re.search(r'[a-zA-Z0-9]', r['text'])]

email_re = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')
phone_re = re.compile(r'(\+91|91|0)?[6-9]\d{9}')
upi_re   = re.compile(r'\w+@(oksbi|okhdfcbank|ybl|paytm|upi|icici|axl|okaxis|okicici)')

pii_email = [r for r in data if email_re.search(r['text'])]
pii_phone = [r for r in data if phone_re.search(r['text'])]
pii_upi   = [r for r in data if upi_re.search(r['text'])]

one_star = [r for r in data if r['rating'] == 1]
one_star_words = Counter()
for r in one_star:
    for w in r['text'].lower().split():
        if len(w) > 3:
            one_star_words[w] += 1

buckets = {'<20 chars': 0, '20-50': 0, '50-150': 0, '150-300': 0, '>300': 0}
for t in texts:
    l = len(t)
    if l < 20:    buckets['<20 chars'] += 1
    elif l < 50:  buckets['20-50'] += 1
    elif l < 150: buckets['50-150'] += 1
    elif l < 300: buckets['150-300'] += 1
    else:         buckets['>300'] += 1

rating_dist = dict(sorted(Counter(ratings).items()))

print('=== GROWW REVIEW DATA ANALYSIS ===')
print(f'Total reviews    : {len(data)}')
print(f'Avg rating       : {sum(ratings)/len(ratings):.2f} / 5')
print(f'Rating dist      : {rating_dist}')
print()
print('== Text Length Distribution ==')
for k, v in buckets.items():
    print(f'  {k:<12} : {v:>4}  ({v/len(data)*100:.1f}%)')
print(f'  Avg length   : {sum(lengths)//len(lengths)} chars')
print(f'  Max length   : {max(lengths)} chars')
print()
print('== Content Quality Issues ==')
print(f'  Non-English (>20% non-ASCII) : {len(non_english)} ({len(non_english)/len(data)*100:.1f}%)')
print(f'  Very short  (<20 chars)      : {len(very_short)} ({len(very_short)/len(data)*100:.1f}%)')
print(f'  Emoji-only  (no a-z/0-9)     : {len(emoji_only)} ({len(emoji_only)/len(data)*100:.1f}%)')
print(f'  Blank title field            : {len(blank_title)} ({len(blank_title)/len(data)*100:.1f}%)')
print()
print('== PII Scan ==')
print(f'  Reviews with email addresses : {len(pii_email)}')
print(f'  Reviews with phone numbers   : {len(pii_phone)}')
print(f'  Reviews with UPI IDs         : {len(pii_upi)}')
if pii_email:
    sample = pii_email[0]['text'][:100].encode('ascii','replace').decode()
    print(f'  Sample email review          : {sample}')
if pii_phone:
    sample = pii_phone[0]['text'][:100].encode('ascii','replace').decode()
    print(f'  Sample phone review          : {sample}')
print()
print('== Top 15 Keywords in 1-star Reviews ==')
for word, count in one_star_words.most_common(15):
    print(f'  {word:<22} : {count}')
print()
print('== Sample 1-star Reviews ==')
for r in one_star[:6]:
    t = r['text'][:120].encode('ascii','replace').decode()
    print(f'  {t}')
print()
print('== Sample Non-English Reviews ==')
for r in non_english[:4]:
    t = r['text'][:80].encode('ascii','replace').decode()
    print(f'  [rating={r["rating"]}] {t}')
