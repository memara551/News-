#!/usr/bin/env python3
"""
Daily news aggregator (English + Arabic) from well-known outlets per country.

Install:   pip install feedparser requests
Run once:  python news_aggregator.py
Every 6h:  python news_aggregator.py --loop 6
Options:   --hours 24   (how far back)   --max 15 (items per feed)
           --keywords iran,hormuz,ukraine   (optional filter, EN/AR)

Output: news_output/latest.html  (+ a dated copy)
Feeds fail/move sometimes: failures are printed and listed in the report.
Edit FEEDS below to add/remove sources. tag = "state" marks state-affiliated
outlets so you can weigh them accordingly.
"""
import argparse
import html
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import mktime

import feedparser
import requests

# (country, source, url, lang, tag)
FEEDS = [
    # --- USA ---
    ("USA", "NYT World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "en", ""),
    ("USA", "NPR World", "https://feeds.npr.org/1004/rss.xml", "en", ""),
    ("USA", "CBS News World", "https://www.cbsnews.com/latest/rss/world", "en", ""),
    ("USA", "Washington Post World", "https://feeds.washingtonpost.com/rss/world", "en", ""),
    # --- Egypt ---
    ("Egypt", "Egypt Independent", "https://www.egyptindependent.com/feed/", "en", ""),
    ("Egypt", "Daily News Egypt", "https://www.dailynewsegypt.com/feed/", "en", ""),
    ("Egypt", "اليوم السابع", "https://www.youm7.com/rss/SectionRss?SectionID=65", "ar", ""),
    ("Egypt", "المصري اليوم", "https://www.almasryalyoum.com/rss/rssfeeds", "ar", ""),
    # --- Russia ---
    ("Russia", "The Moscow Times", "https://www.themoscowtimes.com/rss/news", "en", ""),
    ("Russia", "TASS", "https://tass.com/rss/v2.xml", "en", "state"),
    ("Russia", "Sputnik عربي", "https://sarabic.ae/export/rss2/archive/index.xml", "ar", "state"),
    # --- Ukraine ---
    ("Ukraine", "Kyiv Independent", "https://kyivindependent.com/feed/", "en", ""),
    ("Ukraine", "Ukrinform", "https://www.ukrinform.net/rss/block-lastnews", "en", "state"),
    # --- Israel ---
    ("Israel", "Times of Israel", "https://www.timesofisrael.com/feed/", "en", ""),
    ("Israel", "Haaretz", "https://www.haaretz.com/srv/haaretz-latest-headlines", "en", ""),
    ("Israel", "Jerusalem Post", "https://www.jpost.com/rss/rssfeedsheadlines.aspx", "en", ""),
    # --- Iran ---
    ("Iran", "Tehran Times", "https://www.tehrantimes.com/rss", "en", "state"),
    ("Iran", "Press TV", "https://www.presstv.ir/rss.xml", "en", "state"),
    ("Iran", "IRNA English", "https://en.irna.ir/rss", "en", "state"),
    # --- China ---
    ("China", "Xinhua", "http://www.xinhuanet.com/english/rss/worldrss.xml", "en", "state"),
    ("China", "China Daily", "https://www.chinadaily.com.cn/rss/world_rss.xml", "en", "state"),
    ("China", "SCMP", "https://www.scmp.com/rss/91/feed", "en", ""),
    # --- International (EN + AR) ---
    ("International", "Al Jazeera English", "https://www.aljazeera.com/xml/rss/all.xml", "en", ""),
    ("International", "BBC World", "http://feeds.bbci.co.uk/news/world/rss.xml", "en", ""),
    ("International", "BBC عربي", "https://feeds.bbci.co.uk/arabic/rss.xml", "ar", ""),
    ("International", "DW عربية", "https://rss.dw.com/xml/rss-ar-all", "ar", ""),
    ("International", "France 24 عربي", "https://www.france24.com/ar/rss", "ar", ""),
    ("International", "سكاي نيوز عربية", "https://www.skynewsarabia.com/web/rss", "ar", ""),
    # --- OSINT / analysis (not news wires: treat as unverified analysis) ---
    ("OSINT", "Bellingcat", "https://www.bellingcat.com/feed/", "en", "osint"),
    ("OSINT", "ISW (Institute for the Study of War)", "https://understandingwar.org/feed/", "en", "osint"),
    ("OSINT", "Critical Threats (Iran updates)", "https://www.criticalthreats.org/feed", "en", "osint"),
    ("OSINT", "Oryx (equipment losses)", "https://www.oryxspioenkop.com/feeds/posts/default", "en", "osint"),
    ("OSINT", "The War Zone", "https://www.twz.com/feed", "en", "osint"),
    ("OSINT", "War on the Rocks", "https://warontherocks.com/feed/", "en", "osint"),
    ("OSINT", "r/CredibleDefense", "https://www.reddit.com/r/CredibleDefense/.rss", "en", "osint"),
    ("OSINT", "US CENTCOM releases", "https://www.centcom.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=808&max=10", "en", "state"),
]

# GDELT: free global news-event index (multi-language). ~1 request / 5s allowed.
GDELT_QUERIES = [
    ('"Ukraine war"', "english"), ('"Iran war"', "english"), ('"Israel Gaza"', "english"),
    ('"Strait of Hormuz"', "english"),
    ("أوكرانيا", "arabic"), ("إيران", "arabic"), ("إسرائيل", "arabic"), ("هرمز", "arabic"),
]

HEADERS = {"User-Agent": "Mozilla/5.0 (NewsAggregator/1.0)"}
TAG_RE = re.compile(r"<[^>]+>")


def clean(text, limit=220):
    text = html.unescape(TAG_RE.sub("", text or "")).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def fetch(feed, max_items):
    country, name, url, lang, tag = feed
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        d = feedparser.parse(r.content)
        items = []
        for e in d.entries[:max_items]:
            ts = e.get("published_parsed") or e.get("updated_parsed")
            dt = datetime.fromtimestamp(mktime(ts), tz=timezone.utc) if ts else None
            items.append({
                "country": country, "source": name, "lang": lang, "tag": tag,
                "title": clean(e.get("title", ""), 200),
                "link": e.get("link", ""),
                "summary": clean(e.get("summary", "")),
                "time": dt,
            })
        if not items:
            return [], f"{name}: no entries"
        return items, None
    except Exception as ex:  # noqa: BLE001
        return [], f"{name}: {ex}"


def fetch_gdelt(hours, max_items):
    items, errs = [], []
    for q, lang in GDELT_QUERIES:
        try:
            r = requests.get(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params={"query": f"{q} sourcelang:{lang}", "mode": "artlist",
                        "maxrecords": max_items, "format": "json",
                        "timespan": f"{int(hours)}h", "sort": "datedesc"},
                headers=HEADERS, timeout=30)
            r.raise_for_status()
            for a in r.json().get("articles", []):
                dt = None
                try:
                    dt = datetime.strptime(a["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                except Exception:  # noqa: BLE001
                    pass
                items.append({
                    "country": "OSINT", "source": f"GDELT · {a.get('domain', '')}",
                    "lang": "ar" if lang == "arabic" else "en", "tag": "osint",
                    "title": clean(a.get("title", ""), 200), "link": a.get("url", ""),
                    "summary": f"{q} · {a.get('sourcecountry', '')}", "time": dt,
                })
        except Exception as ex:  # noqa: BLE001
            errs.append(f"GDELT {q}: {ex}")
        time.sleep(5.5)  # respect GDELT rate limit
    return items, ("; ".join(errs) if errs else None)


def collect(hours, max_items, keywords, gdelt=True):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda f: fetch(f, max_items), FEEDS))
    if gdelt:
        results.append(fetch_gdelt(hours, max_items))
    items, errors, seen = [], [], set()
    for res, err in results:
        if err:
            errors.append(err)
        for it in res:
            if it["time"] and it["time"] < cutoff:
                continue
            key = it["link"] or it["title"]
            if key in seen or not it["title"]:
                continue
            if keywords:
                blob = (it["title"] + " " + it["summary"]).lower()
                if not any(k in blob for k in keywords):
                    continue
            seen.add(key)
            items.append(it)
    items.sort(key=lambda i: i["time"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items, errors


def render(items, errors, hours):
    esc = html.escape
    countries = []
    for it in items:
        if it["country"] not in countries:
            countries.append(it["country"])
    sections = []
    for c in countries:
        cards = []
        for it in (i for i in items if i["country"] == c):
            rtl = ' dir="rtl"' if it["lang"] == "ar" else ""
            t = it["time"].strftime("%Y-%m-%d %H:%M UTC") if it["time"] else ""
            tag = {"state": ' <span class="tag">state-affiliated</span>',
                   "osint": ' <span class="tag osint">OSINT / unverified</span>'}.get(it["tag"], "")
            cards.append(
                f'<article class="card" data-lang="{it["lang"]}"{rtl}>'
                f'<a href="{esc(it["link"])}" target="_blank" rel="noopener">{esc(it["title"])}</a>'
                f'<p>{esc(it["summary"])}</p>'
                f'<small>{esc(it["source"])} · {t}{tag}</small></article>'
            )
        sections.append(f'<section><h2>{esc(c)} ({len(cards)})</h2>{"".join(cards)}</section>')
    err_html = ""
    if errors:
        err_html = "<details><summary>Feeds that failed</summary><ul>" + "".join(
            f"<li>{esc(e)}</li>" for e in errors) + "</ul></details>"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>World News Digest</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--card:#f4f5f7;--mut:#666;--ac:#0b5fff}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111;--fg:#eee;--card:#1d1f23;--mut:#9aa;--ac:#6ea8ff}}}}
body{{font-family:system-ui,sans-serif;background:var(--bg);color:var(--fg);max-width:900px;margin:auto;padding:12px}}
h1{{font-size:1.4rem}} h2{{border-bottom:2px solid var(--ac);padding-bottom:4px;margin-top:28px}}
.card{{background:var(--card);border-radius:10px;padding:10px 14px;margin:8px 0}}
.card a{{color:var(--ac);font-weight:600;text-decoration:none}} .card p{{margin:6px 0}}
small{{color:var(--mut)}} .tag{{background:#c62828;color:#fff;border-radius:4px;padding:0 5px;margin-inline-start:6px}}
.tag.osint{{background:#e68a00}}
button{{margin:2px;padding:6px 12px;border-radius:8px;border:1px solid var(--ac);background:none;color:var(--fg)}}
</style></head><body>
<h1>World News Digest — last {hours}h · generated {now}</h1>
<div><button onclick="f('all')">All</button><button onclick="f('en')">English</button><button onclick="f('ar')">العربية</button></div>
{"".join(sections)}{err_html}
<script>function f(l){{document.querySelectorAll('.card').forEach(c=>c.style.display=(l==='all'||c.dataset.lang===l)?'':'none')}}</script>
</body></html>"""


def run(args):
    kw = [k.strip().lower() for k in args.keywords.split(",") if k.strip()] if args.keywords else []
    items, errors = collect(args.hours, args.max, kw, gdelt=not args.no_gdelt)
    out = Path("news_output")
    out.mkdir(exist_ok=True)
    page = render(items, errors, args.hours)
    (out / "latest.html").write_text(page, encoding="utf-8")
    (out / f"news_{datetime.now():%Y-%m-%d}.html").write_text(page, encoding="utf-8")
    print(f"{len(items)} items | {len(errors)} failed feeds -> {out / 'latest.html'}")
    for e in errors:
        print("  FAILED:", e)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--max", type=int, default=15)
    ap.add_argument("--keywords", default="")
    ap.add_argument("--no-gdelt", action="store_true", help="skip GDELT (faster, ~45s saved)")
    ap.add_argument("--loop", type=float, default=0, help="repeat every N hours")
    a = ap.parse_args()
    run(a)
    while a.loop:
        time.sleep(a.loop * 3600)
        run(a)
