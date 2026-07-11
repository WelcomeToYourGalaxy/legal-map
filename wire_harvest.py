#!/usr/bin/env python3
"""
wire_harvest.py  --  Global Wire archive builder (LEGAL DEFENSE & PRISONER SUPPORT edition)

Runs server-side (GitHub Actions, every 6h). Pulls the defense/prisoner-support RSS
feeds, keeps only on-subject, big-picture items, dedupes into wire_archive.json
(capped at 2000, newest first). No API key, no account, no cost.

The relevance gate MIRRORS the in-map filter exactly (FIN_CORE required with a word
boundary; FIN_EXCLUDE rejected) so the harvested archive and the live browser layer
agree on what counts as on-subject. This keeps finance/electoral/court-generic stories
(e.g. "money laundering: court remands ...", generic "voting rights" pieces) out.

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
UA = "Mozilla/5.0 (compatible; legal-map-wire/1.0)"

# --- feed pool: the map's own on-subject live feeds (edit freely) ---
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

# --- relevance gate: an EXACT mirror of the in-map _hasFin() gate ---
FIN_CORE = ['prison','prisoner','prisoners','jail','incarcerat','incarceration','detention',
 'detainee','detained','public defender','public defenders','legal aid','right to counsel',
 'defense lawyer','defence lawyer','indigent defense','wrongful conviction','wrongfully convicted',
 'exonerated','exoneration','innocence project','miscarriage of justice','death penalty','death row',
 'execution','capital punishment','clemency','commutation','parole','probation','reentry','re-entry',
 'solitary confinement','bail fund','cash bail','bail reform','pretrial detention','pre-trial detention',
 'remanded in custody','habeas corpus','prisoners rights','sentencing reform','mass incarceration',
 'expungement','immigration detention','custody death','died in custody']

FIN_EXCLUDE = ['money laundering','laundering','sanctioned','sanctions','timber','oligarch','embezzle',
 'graft','bribery','kickback','procurement','tender','audit office','tax evasion','tax avoidance',
 'tax haven','offshore','shell company','crypto','bitcoin','stock market','earnings','inflation',
 'interest rate','central bank','bailout','sovereign debt','tariff','ukraine war','gaza','israel',
 'hamas','hezbollah','airstrike','missile','drone strike','world cup','olympic','football','soccer',
 'celebrity','recipe','box office','fashion','album','film review','video game','horoscope',
 'weather forecast','webinar','register now','apply now','fellowship','job opening',
 'call for applications','book launch']

STOP = ['football','soccer','celebrity','royal wedding','recipe','horoscope','box office','fashion',
 'weather forecast','sports','webinar','register now','join us','save the date','rsvp',
 'upcoming event','panel discussion','sign up','watch live','apply now','fellowship','job opening',
 'call for applications','call for papers','book launch','newsletter']

BIG = ['landmark','sweeping','historic','ruling','supreme court','overturn','overturned','exonerat',
 'released','freed','wrongful','death penalty','execution','abolish','moratorium','solitary',
 'overcrowding','torture','investigation','inquiry','class action','lawsuit','settlement','reform',
 'legislation','commutation','clemency','pardon','decarceration','monitoring','report finds',
 'death in custody','consent decree','died in custody']
SMALL = ['councillor','local council','resign','quit','stepped down','apolog','affair','wedding',
 'tweeted','gaffe','insult','feud','gossip','obituary','by-election','hospital',' dies']

CATS = [
 ('international',['united nations','mandela rules','bangkok rules','human rights court','european court','inter-american','special rapporteur','optcat','cpt','international']),
 ('defense',['public defender','legal aid','right to counsel','defense lawyer','defence lawyer','indigent defense','access to justice','pro bono','caseload']),
 ('pretrial',['bail','bond','pretrial detention','remand','pre-trial','cash bail','bail fund','awaiting trial']),
 ('conditions',['prison conditions','overcrowding','solitary confinement','inspection','prison death','abuse in custody','ill-treatment','torture','prison health']),
 ('innocence',['wrongful conviction','exonerat','innocence project','overturned conviction','dna evidence','miscarriage of justice','post-conviction','false confession']),
 ('capital',['death penalty','death row','execution','capital punishment','life without parole','juvenile life','clemency','commutation']),
 ('prisoners',['prisoner support','families of prisoners','visitation','books to prisoners','children of prisoners','commissary']),
 ('rights',['prisoners rights','strategic litigation','class action','lawsuit','consent decree','ombudsman','human rights commission']),
 ('reentry',['reentry','re-entry','expungement','record relief','collateral consequences','restoration of voting rights','parole','probation','release']),
 ('incarceration',['mass incarceration','prison population','incarceration rate','sentencing reform','decarceration','prison closure','criminal justice reform','jail']),
 ('immigration',['immigration detention','detention centre','detention center','deportation','asylum detention','migrant detention']),
]

def _word_hit(hay, term):
    i = hay.find(term)
    while i >= 0:
        a = hay[i-1] if i > 0 else ' '
        b = hay[i+len(term)] if i+len(term) < len(hay) else ' '
        if (not a.isalnum()) and (not b.isalnum()):
            return True
        i = hay.find(term, i+1)
    return False

def has_topic(t):
    s = ' ' + (t or '').lower() + ' '
    if any(w in s for w in FIN_EXCLUDE):
        return False
    return any(_word_hit(s, w) for w in FIN_CORE)

def sig(t):
    s = ' ' + (t or '').lower() + ' '
    sc = 0
    for w in BIG:      sc += 2 if w in s else 0
    for w in SMALL:    sc -= 3 if w in s else 0
    for w in FIN_CORE: sc += 1 if w in s else 0
    return sc

def cat(t):
    s = ' ' + (t or '').lower() + ' '
    best, bo = 'other', 0
    for cid, terms in CATS:
        n = sum(1 for w in terms if w in s)
        if n > bo:
            bo, best = n, cid
    return best

def load_archive():
    try:
        with open(ARCHIVE, encoding='utf-8') as f:
            a = json.load(f)
            return a if isinstance(a, list) else []
    except Exception:
        return []

def main():
    feedparser.USER_AGENT = UA
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
            if not title or not link or link in seen:
                continue
            blob = title + ' ' + desc
            low = blob.lower()
            if any(w in low for w in STOP):
                continue
            if not has_topic(blob):
                continue
            s = sig(blob)
            if s < 2:
                continue
            ts = None
            for k in ('published_parsed', 'updated_parsed'):
                v = getattr(it, k, None)
                if v:
                    ts = int(time.mktime(v)) * 1000
                    break
            if ts is None:
                ts = int(time.time() * 1000)
            archive.append({'name': name, 'title': title[:300], 'link': link,
                            'date': ts, 'cat': cat(blob), 'sig': s, 'desc': desc[:180]})
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
