"""
fetch.py — Data acquisition layer.

Three sources:
  1. RSS feeds           — central banks, blogs, HN filtered
  2. Alpaca News API     — licensed financial news (optional, free tier)
  3. yfinance            — live market snapshot (prices, yields, FX)
"""

import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx
import yfinance as yf

ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; briefing-cli/1.0; personal-use)"}


# ─────────────────────────────────────────────
# RSS
# ─────────────────────────────────────────────


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def parse_pub_date(pub: str) -> datetime | None:
    """Parse RFC 2822 (RSS pubDate) or ISO 8601 (Atom published) to UTC datetime."""
    if not pub:
        return None
    try:
        return parsedate_to_datetime(pub.strip()).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(pub.strip().replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except Exception:
        pass
    return None


def fetch_feed(
    url: str,
    seen: set,
    max_per_feed: int,
    max_chars: int,
    cutoff: datetime | None = None,
) -> list[dict]:
    """Fetch one RSS feed. Filters articles older than cutoff. Deduplicates against seen set."""
    try:
        resp = httpx.get(url, timeout=12, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        content = resp.content
        root = ET.fromstring(content)
    except Exception:
        return []

    items = root.findall(".//item") or root.findall(
        ".//{http://www.w3.org/2005/Atom}entry"
    )

    articles = []
    for item in items:
        ns = "{http://www.w3.org/2005/Atom}"
        title = strip_html(
            item.findtext("title") or item.findtext(f"{ns}title") or ""
        ).strip()

        if not title:
            continue

        key = " ".join(title.lower().split()[:5])
        if key in seen:
            continue

        pub_raw = (
            item.findtext("pubDate")
            or item.findtext(f"{ns}published")
            or item.findtext(f"{ns}updated")
            or ""
        ).strip()

        pub_dt = parse_pub_date(pub_raw)

        # Skip stale articles only when we have a parseable date; undated articles pass through
        if cutoff and pub_dt and pub_dt < cutoff:
            continue

        seen.add(key)

        desc = strip_html(
            item.findtext("description")
            or item.findtext(f"{ns}summary")
            or item.findtext(f"{ns}content")
            or ""
        )[:max_chars]

        atom_link = item.find(f"{ns}link")
        link = (
            item.findtext("link")
            or item.findtext(f"{ns}link")
            or (atom_link is not None and atom_link.get("href"))
            or ""
        ).strip()

        pub_display = pub_dt.strftime("%Y-%m-%d %H:%M UTC") if pub_dt else pub_raw[:22]

        articles.append(
            {
                "title": title,
                "description": desc,
                "url": link,
                "published": pub_display,
                "_pub_dt": pub_dt,
                "source": "rss",
            }
        )

    # Newest first; undated articles go last
    _epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    articles.sort(key=lambda a: a["_pub_dt"] or _epoch, reverse=True)
    return articles[:max_per_feed]


def fetch_section_rss(
    urls: list[str],
    seen: set,
    n: int,
    max_chars: int,
    cutoff: datetime | None = None,
) -> list[dict]:
    """Fetch all feeds for a section in parallel, merge, sort by recency, and cap."""
    results = []
    with ThreadPoolExecutor(max_workers=min(len(urls), 6)) as ex:
        futures = {
            ex.submit(fetch_feed, url, seen, n, max_chars, cutoff): url for url in urls
        }
        for f in as_completed(futures):
            results.extend(f.result())
    _epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    results.sort(key=lambda a: a.get("_pub_dt") or _epoch, reverse=True)
    return results[:n]


# ─────────────────────────────────────────────
# ALPACA NEWS API
# ─────────────────────────────────────────────


def fetch_alpaca_section(
    topics: list[str],
    seen: set,
    n: int,
    max_chars: int,
    key_id: str,
    secret_key: str,
    cutoff: datetime | None = None,
) -> list[dict]:
    """
    Fetch from Alpaca Markets News API.
    Free tier: https://alpaca.markets — no credit card, 200 req/min.
    Returns [] gracefully if keys absent or API unreachable.
    """
    if not key_id or not secret_key:
        return []

    query = " OR ".join(f'"{t}"' for t in topics[:4])
    params: dict = {"q": query, "limit": n, "sort": "desc"}
    if cutoff:
        params["start"] = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        resp = httpx.get(
            ALPACA_NEWS_URL,
            params=params,
            headers={
                "Apca-Api-Key-Id": key_id,
                "Apca-Api-Secret-Key": secret_key,
            },
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("news", [])
    except Exception:
        return []

    articles = []
    for item in items:
        title = (item.get("headline") or "").strip()
        if not title:
            continue
        key = " ".join(title.lower().split()[:5])
        if key in seen:
            continue
        seen.add(key)
        pub_dt = parse_pub_date(item.get("created_at") or "")
        articles.append(
            {
                "title": title,
                "description": (item.get("summary") or "")[:max_chars].strip(),
                "url": item.get("url", ""),
                "published": (item.get("created_at") or "")[:10],
                "_pub_dt": pub_dt,
                "source": "alpaca",
            }
        )
    return articles


# ─────────────────────────────────────────────
# MARKET DATA (yfinance)
# ─────────────────────────────────────────────


def fetch_market_snapshot(tickers: dict[str, str]) -> list[dict]:
    """
    Fetch current price + day change for a set of tickers via yfinance.
    Returns list of dicts: {name, price, change_pct, direction}
    Falls back gracefully per-ticker if data unavailable.
    """
    results = []
    # Batch download is faster than individual calls
    symbols = list(tickers.values())

    try:
        data = yf.download(
            symbols,
            period="2d",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
    except Exception:
        return []

    if data is None:
        return []

    for name, symbol in tickers.items():
        try:
            closes = data["Close"][symbol].dropna()  # type: ignore[index]
            if len(closes) < 2:
                continue
            prev_close = float(closes.iloc[-2])
            curr_close = float(closes.iloc[-1])
            change_pct = ((curr_close - prev_close) / prev_close) * 100
            direction = "▲" if change_pct >= 0 else "▼"

            # Format price sensibly
            if curr_close > 1000:
                price_str = f"{curr_close:,.0f}"
            elif curr_close > 10:
                price_str = f"{curr_close:,.2f}"
            else:
                price_str = f"{curr_close:.4f}"

            results.append(
                {
                    "name": name,
                    "price": price_str,
                    "change_pct": change_pct,
                    "direction": direction,
                    "colour": "green" if change_pct >= 0 else "red",
                }
            )
        except Exception:
            continue

    return results


# ─────────────────────────────────────────────
# ORCHESTRATION
# ─────────────────────────────────────────────


def fetch_all_sections(
    sources: dict,
    alpaca_config: dict,
    articles_per_section: int,
    max_chars: int,
    max_age_days: int = 0,
) -> dict[str, list[dict]]:
    """
    Fetch all sections. For each section:
      1. Try Alpaca if configured and section has topic mapping
      2. Supplement with RSS (always runs)
      3. Merge, deduplicate, sort by recency, cap
    """
    alpaca_key = os.getenv("ALPACA_KEY_ID", "")
    alpaca_secret = os.getenv("ALPACA_SECRET_KEY", "")
    alpaca_on = alpaca_config.get("enabled", False) and bool(alpaca_key)
    alpaca_topics = alpaca_config.get("topics", {})

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=max_age_days)
        if max_age_days > 0
        else None
    )

    seen: set = set()
    sections: dict = {}
    _epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

    for section, urls in sources.items():
        results = []

        if alpaca_on and section in alpaca_topics:
            results = fetch_alpaca_section(
                topics=alpaca_topics[section],
                seen=seen,
                n=articles_per_section,
                max_chars=max_chars,
                key_id=alpaca_key,
                secret_key=alpaca_secret,
                cutoff=cutoff,
            )

        rss = fetch_section_rss(urls, seen, articles_per_section, max_chars, cutoff)
        results.extend(rss)

        results.sort(key=lambda a: a.get("_pub_dt") or _epoch, reverse=True)
        capped = results[:articles_per_section]

        # Strip internal sorting field before handing off to downstream code
        for a in capped:
            a.pop("_pub_dt", None)

        sections[section] = capped

    return sections
