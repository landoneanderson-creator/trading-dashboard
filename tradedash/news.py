"""Recent headlines per ticker from Yahoo Finance's free RSS feed (shown for context, not scored)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import requests

FEED = "https://feeds.finance.yahoo.com/rss/2.0/headline"


def headlines(ticker: str, limit: int = 3, session: requests.Session | None = None) -> list[dict]:
    s = session or requests
    try:
        resp = s.get(FEED, params={"s": ticker, "region": "US", "lang": "en-US"},
                     headers={"User-Agent": "Mozilla/5.0 (trading-dashboard)"}, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as exc:
        print(f"[news] {ticker}: {exc}")
        return []
    items = []
    for item in root.iter("item"):
        title, link = item.findtext("title"), item.findtext("link")
        if title and link and link.startswith("https://"):
            items.append({"title": title.strip(), "link": link.strip(), "date": item.findtext("pubDate") or ""})
        if len(items) >= limit:
            break
    return items
