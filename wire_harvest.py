#!/usr/bin/env python3
"""
wire_harvest.py  —  Global Wire archive builder (legal-accountability edition)

Runs server-side (GitHub Actions, every 6h). Pulls the accountability RSS feeds,
keeps only legal-relevant, big-picture items, dedupes into wire_archive.json
(capped at 2000, newest first). No API key, no account, no cost.

  pip install feedparser
  python3 wire_harvest.py
"""
import json, re, time, sys, os
from datetime import datetime, timezone
try:
    import feedparser
except ImportError:
    sys.exit("pip install feedparser")

ARCHIVE = os.path.join(os.path.dirname(__file__), "wire_archive.json")
CAP = 2000

# --- feed pool (rss_probe-verified; edit freely) ---
FEEDS = [
    ('Prison Insider', 'https://www.prison-insider.com/en/rss'),
    ('Penal Reform International', 'https://www.penalreform.org/feed/'),
    ('Fair Trials', 'https://www.fairtrials.org/feed/'),
    ('The Marshall Project', 'https://www.themarshallproject.org/rss/recent.rss'),
    ('Prison Policy Initiative', 'https://www.prisonpolicy.org/rss.xml'),
    ('The Sentencing Project', 'https://www.sentencingproject.org/feed/'),
    ('Innocence Project', 'https://innocenceproject.org/feed/'),
    ('Equal Justice Initiative', 'https://eji.org/feed/'),
    ('Prison Legal News', 'https://www.prisonlegalnews.org/rss/news/'),
    ('Death Penalty Information Center', 'https://deathpenaltyinfo.org/feed'),
    ('Vera Institute of Justice', 'https://www.vera.org/feed'),
    ('Bolts', 'https://boltsmag.org/feed'),
    ('Open Society Justice Initiative', 'https://www.justiceinitiative.org/feed'),
    ('Reprieve', 'https://reprieve.org/feed/'),
    ('Solitary Watch', 'https://solitarywatch.org/feed/'),
    ('Prison Journalism Project', 'https://prisonjournalismproject.org/feed/'),
    ('Appeal', 'https://theappeal.org/feed/'),
]

# --- legal relevance gate (mirror of the in-map filter) ---
JUD_TERMS = ['court','legal','judiciary',' judge',' judges','justice','ruling','ruled',
 'verdict','judgment','tribunal','prosecut','supreme court','constitutional court','appeal',
 'appellate','cassation','magistrat','litigation','lawsuit','indict','conviction','acquit',
 'sentenced','impeach','disbar','contempt of court','injunction','habeas','plea','docket',
 'precedent','jurisprudence','bar association','attorney general','war crimes',
 'human rights court','rule of law','legal aid','right to counsel']

STOP = ['football','soccer','celebrity','royal wedding','recipe','horoscope','box office',
 'fashion','weather forecast','sports','webinar','register now','join us','save the date',
 'rsvp','upcoming event','panel discussion','sign up','watch live','apply now','fellowship',
 'job opening','call for applications','call for papers','book launch','newsletter']

BIG = ['supreme court','constitutional court','landmark','ruling','ruled','verdict','judgment',
 'struck down','upheld','overturn','unconstitutional','precedent','indict','convicted',
 'acquitted','sentenced','impeach','tribunal','prosecution','attorney general','injunction',
 'class action','appeals court','court of appeal','cassation','legal independence',
 'court packing','chief justice','war crimes','human rights court']
SMALL = ['councillor','local court','traffic','resign','quit','stepped down','apolog','affair',
 'wedding','tweeted','gaffe','insult','feud','hospital',' dies','obituary','health scare',
 'personal','celebrity']

def has_jud(t):
    s = ' ' + (t or '').lower() + ' '
    return any(w in s for w in JUD_TERMS)

def sig(t):
    s = ' ' + (t or '').lower() + ' '
    sc = 0
    for w in BIG:   sc += 2 if w in s else 0
    for w in SMALL: sc -= 3 if w in s else 0
    for w in JUD_TERMS: sc += 1 if w in s else 0
    return sc

def load_archive():
    try:
        with open(ARCHIVE, encoding='utf-8') as f:
            a = json.load(f)
            return a if isinstance(a, list) else []
    except Exception:
        return []

def main():
    archive = load_archive()
    seen = {x.get('link') for x in archive if x.get('link')}
    added = 0
    for name, url in FEEDS:
        try:
            d = feedparser.parse(url)
        except Exception:
            continue
        for it in d.entries[:40]:
            title = (getattr(it, 'title', '') or '').strip()
            desc  = re.sub('<[^>]+>', '', getattr(it, 'summary', '') or '')[:300]
            link  = getattr(it, 'link', '') or ''
            if not link or link in seen:
                continue
            blob = (title + ' ' + desc)
            low = blob.lower()
            if any(s in low for s in STOP):
                continue
            if not has_jud(blob):
                continue
            s = sig(blob)
            if s < 3:                      # drop small/scattered incidents
                continue
            # timestamp
            ts = None
            for k in ('published_parsed', 'updated_parsed'):
                v = getattr(it, k, None)
                if v:
                    ts = int(time.mktime(v)) * 1000
                    break
            if ts is None:
                ts = int(time.time() * 1000)
            archive.append({'name': name, 'title': title, 'link': link,
                            'date': ts, 'sig': s, 'desc': desc[:180]})
            seen.add(link)
            added += 1
    archive.sort(key=lambda x: x.get('date', 0), reverse=True)
    archive = archive[:CAP]
    with open(ARCHIVE, 'w', encoding='utf-8') as f:
        json.dump(archive, f, ensure_ascii=False, separators=(',', ':'))
    print("added %d | archive now %d | %s" % (added, len(archive),
          datetime.now(timezone.utc).isoformat()))

if __name__ == '__main__':
    main()
