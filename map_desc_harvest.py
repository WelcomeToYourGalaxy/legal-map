#!/usr/bin/env python3
"""
map_desc_harvest.py — harvest real descriptions for stub entries in the legal map.

WHY THIS EXISTS
---------------
An audit of combined_legal_defense_map.html found that ~640 of ~1,376 national
entries (47%) carry a *boilerplate* description that describes the directory they
were scraped from, not the organisation:

    "— Criminal-justice voluntary-sector organisation listed in the Clinks
     directory — works with people in the justice system and their families..."

Every Clinks-sourced entry shares that identical sentence. A user clicking Adfam,
Shelter or Sussex Prisoners' Families reads the same words and learns nothing
about any of them. This script fetches each stub's own website and pulls the
organisation's self-description, so those entries can be filled in for real.

It is READ-ONLY. It never edits the map. It writes a CSV for review.

USAGE
-----
    python3 map_desc_harvest.py --map combined_legal_defense_map.html
    python3 map_desc_harvest.py --map combined_legal_defense_map.html --sub lmap_sub.json
    python3 map_desc_harvest.py --map ... --limit 50          # try a small batch first
    python3 map_desc_harvest.py --map ... --only clinks       # just the Clinks stubs
    python3 map_desc_harvest.py --map ... --resume            # skip rows already in the CSV

Output: map_desc_harvest.csv
    flag, section, location, name, url, http_status,
    current_desc_len, current_desc, harvested_desc, source, title

flag values:
    OK       - got a usable description; review then apply
    THIN     - fetched, but nothing better than what's already there
    BLOCKED  - anti-bot / 403 / robots; NOT dead — see note below
    DEAD     - genuinely unreachable (DNS failure, 404, connection refused)
    SKIP     - no URL, or entry already has a real description

IMPORTANT — "BLOCKED" IS NOT "DEAD"
-----------------------------------
A prior audit flagged 106 rows DEAD; on inspection they were dominated by
anti-bot 403s on sites that were perfectly alive (SNHR, Fair Trials, Viasna...).
This script separates BLOCKED from DEAD so that distinction is never lost again.
Do not delete anything on a BLOCKED flag.

Stdlib only. No dependencies.
"""

import argparse
import csv
import html
import json
import os
import random
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

# Descriptions that describe a directory rather than an organisation.
BOILERPLATE = re.compile(
    r"\u2014\s*(Criminal-justice voluntary-sector organisation listed in"
    r"|Organisation listed in the Prison Reform Trust"
    r"|Prisoner-support organisation listed in"
    r"|National preventive mechanism or torture-prevention body)",
    re.I,
)
SOURCE_HINT = {
    "clinks": "Clinks directory",
    "prt": "Prison Reform Trust directory",
    "parc": "Prison Activist Resource Center",
    "opcat": "APT OPCAT database",
}


# --------------------------------------------------------------------------- #
# map parsing (same brace-matcher the map build uses)
# --------------------------------------------------------------------------- #
def grabspan(h, name):
    m = re.search(r"const " + name + r"\s*=\s*(\{|\[)", h)
    if not m:
        raise ValueError("could not find const %s in the map" % name)
    st = m.end() - 1
    op = h[st]
    cl = "]" if op == "[" else "}"
    depth = 0
    i = st
    instr = False
    q = ""
    while i < len(h):
        c = h[i]
        if instr:
            if c == "\\":
                i += 2
                continue
            if c == q:
                instr = False
        else:
            if c in "\"'`":
                instr = True
                q = c
            elif c == op:
                depth += 1
            elif c == cl:
                depth -= 1
                if depth == 0:
                    break
        i += 1
    return json.loads(h[st:i + 1])


def collect(map_path, sub_path=None):
    """Yield (section, location, entry) for every entry in the map."""
    h = open(map_path, encoding="utf-8").read()
    for iso, cc in grabspan(h, "trackerData").items():
        for t in cc.get("trackers", []):
            yield "national", iso, t
    for grp in grabspan(h, "internationalBodies"):
        for t in grp.get("trackers", []):
            yield "international", grp.get("name", ""), t
    if sub_path and os.path.exists(sub_path):
        for cc, regions in json.load(open(sub_path, encoding="utf-8")).items():
            for rg, rec in regions.items():
                for t in rec.get("trackers", []):
                    yield "subnational", "%s/%s" % (cc, rg), t


# --------------------------------------------------------------------------- #
# description extraction
# --------------------------------------------------------------------------- #
class Extract(HTMLParser):
    """Pull og:description, meta description, <title>, and body paragraphs."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta_desc = ""
        self.og_desc = ""
        self.title = ""
        self.paras = []
        self._in_title = False
        self._in_p = False
        self._buf = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            content = (a.get("content") or "").strip()
            if key == "og:description" and not self.og_desc:
                self.og_desc = content
            elif key == "description" and not self.meta_desc:
                self.meta_desc = content
        elif tag == "title":
            self._in_title = True
        elif tag in ("script", "style", "nav", "footer", "header"):
            self._skip += 1
        elif tag == "p" and not self._skip:
            self._in_p = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in ("script", "style", "nav", "footer", "header"):
            self._skip = max(0, self._skip - 1)
        elif tag == "p" and self._in_p:
            txt = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if len(txt) >= 80:
                self.paras.append(txt)
            self._in_p = False

    def handle_data(self, d):
        if self._in_title:
            self.title += d
        elif self._in_p and not self._skip:
            self._buf.append(d)


JUNKY = re.compile(
    r"cookie|javascript|enable js|privacy policy|all rights reserved|"
    r"subscribe|newsletter|skip to (main )?content|© ?\d{4}|please log ?in",
    re.I,
)


def best_description(page_bytes):
    """Return (description, source, title) — the org's own words if we can find them."""
    try:
        text = page_bytes.decode("utf-8", errors="replace")
    except Exception:
        text = str(page_bytes)
    p = Extract()
    try:
        p.feed(text)
    except Exception:
        pass
    title = re.sub(r"\s+", " ", html.unescape(p.title or "")).strip()[:160]

    for cand, src in ((p.og_desc, "og:description"), (p.meta_desc, "meta description")):
        cand = re.sub(r"\s+", " ", html.unescape(cand or "")).strip()
        if len(cand) >= 60 and not JUNKY.search(cand):
            return cand[:600], src, title

    # First-paragraph fallback is risky: on news-led sites it grabs the top story.
    # (Real example: Prison Legal News returned "For 30 minutes, Brian Tracey lay
    # naked and unable to breathe on the floor of the medical ward...")
    # So only accept a paragraph that reads like self-description, and never one
    # that reads like reporting.
    SELF = re.compile(r"\b(we|our|us)\b|\bis a\b|\bis an\b|\bfounded\b|\bprovides?\b|"
                      r"\bworks? (to|with|for)\b|\bmission\b|\bdedicated to\b", re.I)
    NEWSY = re.compile(
        r"^[A-Z][A-Z .,'-]{3,30}\s*[\u2013\u2014-]{1,2}\s|"     # dateline: "BLYTHE, CA -- "
        r"\b(said|told|announced|reported|according to)\b|"     # reported speech
        r"\b(on|in)\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}|"
        r"\b(19|20)\d{2}\s*[,.]|"                              # "It was Dec. 15, 2023,"
        r"\bread (more|our story)\b|\bribbon cutting\b|\blearn more\b",
        re.I | re.M)
    for para in p.paras:
        para = html.unescape(para).strip()
        if len(para) < 120 or JUNKY.search(para):
            continue
        if NEWSY.search(para) or not SELF.search(para):
            continue
        return para[:600], "first paragraph", title

    return "", "", title


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #
def fetch(url, timeout=20):
    """Return (status, body). status: int, or a string label for failures."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # many NGO sites have imperfect chains
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en,*;q=0.5",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.getcode(), r.read(400_000)
    except urllib.error.HTTPError as e:
        return e.code, b""
    except urllib.error.URLError as e:
        return "URLError: %s" % (e.reason,), b""
    except Exception as e:
        return "%s: %s" % (type(e).__name__, e), b""


def classify(status):
    if status == 200:
        return None
    if status in (401, 403, 406, 429, 503):
        return "BLOCKED"           # anti-bot, near-certainly alive
    if isinstance(status, int):
        return "DEAD" if status in (404, 410) else "BLOCKED"
    s = str(status).lower()
    if "timed out" in s or "timeout" in s or "reset" in s:
        return "BLOCKED"           # transient, not dead
    return "DEAD"


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", required=True, help="combined_legal_defense_map.html")
    ap.add_argument("--sub", default="lmap_sub.json", help="lmap_sub.json (optional)")
    ap.add_argument("--out", default="map_desc_harvest.csv")
    ap.add_argument("--limit", type=int, default=0, help="stop after N fetches")
    ap.add_argument("--only", default="", help="filter: clinks | prt | parc | opcat")
    ap.add_argument("--minlen", type=int, default=200,
                    help="treat descriptions shorter than this as stubs (default 200)")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between fetches")
    ap.add_argument("--resume", action="store_true", help="skip URLs already in --out")
    args = ap.parse_args()

    done = set()
    if args.resume and os.path.exists(args.out):
        with open(args.out, encoding="utf-8-sig", newline="") as f:
            done = {r["url"] for r in csv.DictReader(f) if r.get("url")}
        print("resume: %d rows already harvested" % len(done))

    targets = []
    for section, loc, t in collect(args.map, args.sub):
        desc = t.get("desc", "") or ""
        url = (t.get("url") or "").strip()
        is_stub = bool(BOILERPLATE.search(desc)) or len(desc) < args.minlen
        if not is_stub:
            continue
        if args.only and args.only.lower() not in desc.lower():
            continue
        if not url or t.get("nodomain"):
            continue            # contact-only entries: nothing to fetch, by design
        if url in done:
            continue
        targets.append((section, loc, t, url))

    print("%d stub entries to harvest%s" % (len(targets),
          (" (limited to %d)" % args.limit) if args.limit else ""))
    if args.limit:
        targets = targets[:args.limit]

    mode = "a" if (args.resume and os.path.exists(args.out)) else "w"
    with open(args.out, mode, encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if mode == "w":
            w.writerow(["flag", "section", "location", "name", "url", "http_status",
                        "current_desc_len", "current_desc", "harvested_desc",
                        "source", "title"])
        counts = {}
        for i, (section, loc, t, url) in enumerate(targets, 1):
            status, body = fetch(url)
            bad = classify(status)
            desc = t.get("desc", "") or ""
            if bad:
                flag, got, src, title = bad, "", "", ""
            else:
                got, src, title = best_description(body)
                if not got:
                    flag = "THIN"
                elif len(got) <= len(desc):
                    flag = "THIN"
                else:
                    flag = "OK"
            counts[flag] = counts.get(flag, 0) + 1
            w.writerow([flag, section, loc, t.get("name", ""), url, status,
                        len(desc), desc[:300], got, src, title])
            f.flush()
            print("  [%4d/%d] %-8s %-42s %s" % (i, len(targets), flag,
                                                t.get("name", "")[:42], status))
            time.sleep(args.delay + random.uniform(0, 0.4))   # be polite

    print("\ndone -> %s" % args.out)
    print("summary: %s" % counts)
    print("\nNEXT: review the OK rows, then hand the CSV back to be applied.")
    print("Do NOT delete anything flagged BLOCKED — that means anti-bot, not dead.")


if __name__ == "__main__":
    main()
