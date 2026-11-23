#!/usr/bin/env python3
"""
Somalia–Türkiye cooperation scraper
===================================
Collects recent news and government releases about Somalia–Türkiye relations,
tags each item by domain and impact, and writes `events.json` for the
Somalia–Türkiye Relations Simulator ("Load refreshed events.json" button).

Sources
  1. Google News RSS searches in English, Turkish and Somali, including
     searches restricted to government sites (mfa.gov.tr, msb.gov.tr,
     enerji.gov.tr, iletisim.gov.tr, sonna.so, mfa.gov.so, villasomalia.gov.so).
  2. GDELT DOC 2.0 API: article list plus a daily average-tone timeline.
  3. SONNA (Somali National News Agency) RSS feed.

Usage
  pip install requests            # only dependency
  python somalia_turkey_scraper.py                 # last 90 days
  python somalia_turkey_scraper.py --days 30 --out data
  python somalia_turkey_scraper.py --min-abs-impact 1   # drop neutral items

Outputs (in --out, default ./scraped)
  events.json       array of {date, domain, impact, title, url, source}
  events.csv        same, for spreadsheets
  gdelt_tone.csv    daily news volume and average tone for Somalia+Turkey
  raw_items.json    everything collected before filtering, for auditing

Impact scores are a keyword heuristic (-3..+3). Review events.csv and edit the
`impact` or `domain` columns where the heuristic gets it wrong, then re-export
with --from-csv events.csv before loading into the simulator.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import email.utils
import html
import json
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("Please install requests first:  pip install requests")

UA = {"User-Agent": "Mozilla/5.0 (research scraper; Somalia-Turkey relations simulator)"}

# ---------------------------------------------------------------- sources
GN_QUERIES = [
    # (query, hl, gl, ceid)
    ('Somalia Turkey', "en-US", "US", "US:en"),
    ('Somalia Türkiye', "en-US", "US", "US:en"),
    ('Mogadishu Ankara', "en-US", "US", "US:en"),
    ('Somali Türkiye', "tr", "TR", "TR:tr"),
    ('Somali Türk askeri OR TPAO OR Çağrı Bey', "tr", "TR", "TR:tr"),
    ('Soomaaliya Turkiga', "so", "SO", "SO:so"),
    # government-site searches
    ('Somalia site:mfa.gov.tr', "en-US", "US", "US:en"),
    ('Somali site:msb.gov.tr', "tr", "TR", "TR:tr"),
    ('Somali site:enerji.gov.tr', "tr", "TR", "TR:tr"),
    ('Somalia site:iletisim.gov.tr', "en-US", "US", "US:en"),
    ('Türkiye site:sonna.so', "en-US", "US", "US:en"),
    ('Turkey site:mfa.gov.so', "en-US", "US", "US:en"),
    ('Turkey site:villasomalia.gov.so', "en-US", "US", "US:en"),
]
EXTRA_RSS = [
    "https://sonna.so/en/feed/",
]
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_QUERY = '(Somalia OR Somali OR Mogadishu) (Turkey OR Türkiye OR Turkish OR Ankara OR Erdogan)'

# ---------------------------------------------------------------- tagging
DOMAIN_KEYWORDS = {
    "energy": ["oil", "gas", "drill", "tpao", "çağrı bey", "cagri bey", "oruç reis", "oruc reis", "curad",
               "petroleum", "hydrocarbon", "offshore block", "seismic", "petrol", "sondaj", "enerji"],
    "space": ["spaceport", "space port", "rocket", "launch site", "satellite", "uzay", "roket", "missile test"],
    "security": ["military", "army", "navy", "naval", "frigate", "troop", "turksom", "drone", "f-16", "al-shabaab",
                 "shabaab", "defence", "defense", "gorgor", "haramcad", "warship", "counterterror", "air strike",
                 "airstrike", "asker", "savunma", "deniz kuvvet", "msb", "piracy", "coast guard"],
    "economy": ["trade", "export", "import", "investment", "port", "airport", "hospital", "aid", "tika",
                "humanitarian", "fishing", "fisheries", "loan", "debt", "business", "forum", "scholarship",
                "turkish airlines", "infrastructure", "road", "ticaret", "yatırım", "yardım"],
    "politics": ["opposition", "election", "mandate", "constitution", "puntland", "jubaland", "protest",
                 "mediat", "farmaajo", "parliament", "federal member", "talks between", "seçim", "muhalefet"],
    "diplomacy": ["minister", "visit", "ambassador", "embassy", "summit", "president", "erdogan", "erdoğan",
                  "hassan sheikh", "fidan", "somaliland", "israel", "ethiopia", "uae", "bilateral", "agreement",
                  "memorandum", "cumhurbaşkanı", "büyükelçi"],
}
POS = {3: ["historic", "landmark", "treaty", "ratif", "discovery", "found oil", "strategic partnership"],
       2: ["sign", "agreement", "deploy", "inaugurat", "opens", "launched", "extend", "deliver", "handover",
           "hand over", "commission", "memorandum", "complete", "construction", "begins", "starts", "pledge", "donat", "imzala", "açılış"],
       1: ["meet", "talks", "visit", "discuss", "cooperat", "support", "train", "welcome", "praise", "thank",
           "boost", "strengthen", "progress", "arriv", "send", "sends", "sent", "escort"]}
NEG = {3: ["expel", "sever", "cut ties", "terminate", "suspend", "withdraw from", "killed turkish",
           "turkish soldiers killed", "attack on turkish"],
       2: ["protest", "accuse", "reject", "cancel", "clash", "dispute", "condemn turkey", "dry hole",
           "halt", "scandal", "backlash", "violation"],
       1: ["concern", "criticis", "criticiz", "tension", "deny", "denies", "delay", "stall", "question",
           "warn", "fail"]}
RELEVANCE_A = ["somalia", "somali", "mogadishu", "soomaaliya", "somali̇"]
RELEVANCE_B = ["turkey", "türkiye", "turkiye", "turkish", "ankara", "erdogan", "erdoğan", "türk", "turkiga", "tpao"]


def classify(title: str) -> tuple[str, int]:
    t = title.lower()
    scores = {d: sum(1 for k in kws if k in t) for d, kws in DOMAIN_KEYWORDS.items()}
    # tie-break order: more specific domains win
    order = ["energy", "space", "politics", "security", "economy", "diplomacy"]
    domain = max(order, key=lambda d: (scores[d], -order.index(d)))
    if scores[domain] == 0:
        domain = "diplomacy"
    pos = max([w for w, kws in POS.items() if any(k in t for k in kws)], default=0)
    neg = max([w for w, kws in NEG.items() if any(k in t for k in kws)], default=0)
    impact = pos - neg
    # Somali domestic politics items are mostly friction unless mediation succeeds
    if domain == "politics" and pos and not neg and "agree" not in t and "deal" not in t:
        impact = min(impact, 1)
    return domain, max(-3, min(3, impact))


def relevant(title: str) -> bool:
    t = title.lower()
    return any(a in t for a in RELEVANCE_A) and any(b in t for b in RELEVANCE_B)


# ---------------------------------------------------------------- fetching
def get(url: str, params: dict | None = None, tries: int = 3) -> requests.Response | None:
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (i + 1)); continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if i == tries - 1:
                print(f"  ! {url[:80]}… failed: {e}", file=sys.stderr)
            time.sleep(2 * (i + 1))
    return None


def parse_rss(xml_text: str, source: str) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate") or ""
        src = it.find("source")
        outlet = src.text.strip() if src is not None and src.text else source
        # Google News appends " - Outlet" to titles
        if " - " in title and outlet and title.endswith(outlet):
            title = title[: -len(outlet) - 3].strip()
        try:
            date = email.utils.parsedate_to_datetime(pub).date().isoformat()
        except (TypeError, ValueError):
            date = ""
        items.append({"date": date, "title": title, "url": link, "source": outlet})
    return items


def google_news(days: int) -> list[dict]:
    out = []
    for q, hl, gl, ceid in GN_QUERIES:
        query = f"{q} when:{days}d"
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": query, "hl": hl, "gl": gl, "ceid": ceid})
        r = get(url)
        got = parse_rss(r.text, "Google News") if r else []
        print(f"  Google News [{q}]: {len(got)}")
        out += got
        time.sleep(1)
    return out


def extra_feeds() -> list[dict]:
    out = []
    for url in EXTRA_RSS:
        r = get(url)
        got = parse_rss(r.text, urllib.parse.urlparse(url).netloc) if r else []
        print(f"  RSS {url}: {len(got)}")
        out += got
    return out


def gdelt_articles(days: int) -> list[dict]:
    params = {"query": GDELT_QUERY, "mode": "artlist", "format": "json", "maxrecords": 250,
              "sort": "datedesc", "timespan": f"{min(days, 90)}d"}
    r = get(GDELT, params)
    out = []
    if r:
        try:
            for a in r.json().get("articles", []):
                d = a.get("seendate", "")[:8]
                out.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else "",
                            "title": a.get("title", "").strip(), "url": a.get("url", ""),
                            "source": a.get("domain", "GDELT")})
        except ValueError:
            pass
    print(f"  GDELT articles: {len(out)}")
    return out


def gdelt_tone(days: int, out_dir: Path) -> None:
    rows = {}
    for mode in ("timelinevolraw", "timelinetone"):
        r = get(GDELT, {"query": GDELT_QUERY, "mode": mode, "format": "json", "timespan": f"{min(days, 365)}d"})
        if not r:
            continue
        try:
            series = r.json()["timeline"][0]["data"]
        except (ValueError, KeyError, IndexError):
            continue
        for p in series:
            day = p["date"][:8]
            rows.setdefault(day, {})["volume" if mode == "timelinevolraw" else "tone"] = p["value"]
        time.sleep(1)
    if rows:
        with open(out_dir / "gdelt_tone.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["date", "articles", "avg_tone"])
            for day in sorted(rows):
                w.writerow([f"{day[:4]}-{day[4:6]}-{day[6:8]}", rows[day].get("volume", ""), rows[day].get("tone", "")])
        print(f"  GDELT tone timeline: {len(rows)} days → gdelt_tone.csv")


# ---------------------------------------------------------------- pipeline
def norm(title: str) -> str:
    return re.sub(r"[^a-z0-9ğüşöçı ]", "", title.lower())[:90]


def build_events(raw: list[dict], min_abs: int, since: dt.date) -> list[dict]:
    seen, events = set(), []
    for it in sorted(raw, key=lambda x: x.get("date") or "", reverse=True):
        t = it.get("title") or ""
        if not t or not it.get("date") or not relevant(t):
            continue
        if dt.date.fromisoformat(it["date"]) < since:
            continue
        key = norm(t)
        if key in seen:
            continue
        seen.add(key)
        domain, impact = classify(t)
        if abs(impact) < min_abs:
            continue
        events.append({"date": it["date"], "domain": domain, "impact": impact,
                       "title": t, "url": it.get("url", ""), "source": it.get("source", "")})
    return events


def write_outputs(events: list[dict], out_dir: Path) -> None:
    (out_dir / "events.json").write_text(json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(out_dir / "events.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "domain", "impact", "title", "url", "source"])
        w.writeheader(); w.writerows(events)
    by = {}
    for e in events:
        by.setdefault(e["domain"], []).append(e["impact"])
    print(f"\nWrote {len(events)} events to {out_dir/'events.json'}")
    for d, v in sorted(by.items()):
        print(f"  {d:<10} {len(v):>4} items, net impact {sum(v):+d}")


def from_csv(path: Path, out_dir: Path) -> None:
    with open(path, encoding="utf-8") as f:
        events = [{**r, "impact": int(r["impact"])} for r in csv.DictReader(f)]
    write_outputs(events, out_dir)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=90, help="look-back window in days (default 90)")
    ap.add_argument("--out", default="scraped", help="output folder (default ./scraped)")
    ap.add_argument("--min-abs-impact", type=int, default=0, help="drop items scored with |impact| below this")
    ap.add_argument("--no-gdelt", action="store_true", help="skip the GDELT API")
    ap.add_argument("--from-csv", help="re-export a hand-edited events.csv to events.json and exit")
    a = ap.parse_args()
    out_dir = Path(a.out); out_dir.mkdir(parents=True, exist_ok=True)
    if a.from_csv:
        from_csv(Path(a.from_csv), out_dir); return

    print(f"Collecting Somalia–Türkiye items from the last {a.days} days…")
    raw = google_news(a.days) + extra_feeds()
    if not a.no_gdelt:
        raw += gdelt_articles(a.days)
        gdelt_tone(a.days, out_dir)
    (out_dir / "raw_items.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    since = dt.date.today() - dt.timedelta(days=a.days)
    events = build_events(raw, a.min_abs_impact, since)
    write_outputs(events, out_dir)
    print("\nNext: open the simulator and click 'Load refreshed events.json from the scraper'.")


if __name__ == "__main__":
    main()
