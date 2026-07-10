#!/usr/bin/env python3
"""
rss_probe.py  —  discover working RSS feeds for the Global Wire.

Feed the map's news/investigative source homepages (one URL per line, or pass a
CSV exported from the map) and it will try common feed paths, validate that each
parses as a real feed with recent entries, and print a ready-to-paste
[name, url] list for wire_harvest.py / WIRE_FEEDS.

  pip install feedparser requests
  python3 rss_probe.py sites.txt            # one homepage per line
  python3 rss_probe.py --from-map map.html  # pull news-kind source URLs from a map
"""
import sys, re, json, argparse
try:
    import feedparser, requests
except ImportError:
    sys.exit("pip install feedparser requests")

CANDIDATES = ['/feed', '/feed/', '/rss', '/rss/', '/rss.xml', '/feed.xml',
              '/index.xml', '/atom.xml', '/feeds/all.rss', '/?feed=rss2',
              '/en/feed', '/en/rss', '/blog-feed.xml']
HDRS = {'User-Agent': 'Mozilla/5.0 (rss-probe)'}

def base(u):
    m = re.match(r'(https?://[^/]+)', u.strip())
    return m.group(1) if m else None

def try_feed(url):
    try:
        r = requests.get(url, headers=HDRS, timeout=12)
        if r.status_code != 200:
            return None
        d = feedparser.parse(r.content)
        if d.entries and (d.feed.get('title') or len(d.entries) >= 3):
            return d.feed.get('title') or url
    except Exception:
        pass
    return None

def discover(home):
    # 1) explicit <link rel=alternate type=rss/atom>
    try:
        r = requests.get(home, headers=HDRS, timeout=12)
        for m in re.finditer(r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*>', r.text, re.I):
            hm = re.search(r'href=["\']([^"\']+)["\']', m.group(0))
            if hm:
                u = hm.group(1)
                if u.startswith('/'):
                    u = base(home) + u
                t = try_feed(u)
                if t:
                    return t, u
    except Exception:
        pass
    # 2) common paths
    b = base(home)
    if not b:
        return None
    for c in CANDIDATES:
        t = try_feed(b + c)
        if t:
            return t, b + c
    return None

def urls_from_map(path):
    h = open(path, encoding='utf-8').read()
    out = set()
    for m in re.finditer(r'"kind":"(news|investigative|blog|media)"[^}]*?"url":"([^"]+)"', h):
        out.add(m.group(2))
    for m in re.finditer(r'"url":"([^"]+)"[^}]*?"kind":"(news|investigative|blog|media)"', h):
        out.add(m.group(1))
    return sorted(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input', nargs='?')
    ap.add_argument('--from-map')
    a = ap.parse_args()
    if a.from_map:
        homes = urls_from_map(a.from_map)
    elif a.input:
        homes = [l.strip() for l in open(a.input) if l.strip()]
    else:
        sys.exit("give a file of homepages or --from-map map.html")
    found = []
    for h in homes:
        res = discover(h)
        if res:
            name, url = res
            found.append((name.strip()[:60], url))
            print("  OK   %-40s %s" % (name.strip()[:40], url), file=sys.stderr)
        else:
            print("  miss %s" % h, file=sys.stderr)
    print("\n// %d working feeds:\n" % len(found))
    for name, url in sorted(found):
        print('  [%s, %s],' % (json.dumps(name), json.dumps(url)))

if __name__ == '__main__':
    main()
