"""
HyperClaw Live Feeds — Markets, Intel, Polymarket
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional
import httpx
import feedparser

logger = logging.getLogger("hyperclaw.feeds")

# ── Market Symbols ────────────────────────────────────────────────────────────
MARKET_SYMBOLS = {
    # Crypto
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ethereum",
    "SOL-USD": "Solana",
    "AVAX-USD": "Avalanche",
    "LINK-USD": "Chainlink",
    "MATIC-USD": "Polygon",
    "DOT-USD": "Polkadot",
    "ATOM-USD": "Cosmos",
    "UNI-USD": "Uniswap",
    "AAVE-USD": "Aave",
    # Major Indices
    "^GSPC": "S&P 500",
    "^DJI": "Dow Jones",
    "^IXIC": "Nasdaq",
    "^RUT": "Russell 2000",
    "^VIX": "VIX",
    # Mega Caps
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "GOOGL": "Alphabet",
    "AMZN": "Amazon",
    "NVDA": "NVIDIA",
    "META": "Meta",
    "TSLA": "Tesla",
    "BRK-B": "Berkshire",
    # Tech
    "AMD": "AMD",
    "INTC": "Intel",
    "CRM": "Salesforce",
    "ORCL": "Oracle",
    "ADBE": "Adobe",
    "NFLX": "Netflix",
    "PYPL": "PayPal",
    "SQ": "Block",
    "SHOP": "Shopify",
    "SNOW": "Snowflake",
    "PLTR": "Palantir",
    "COIN": "Coinbase",
    # Finance
    "JPM": "JPMorgan",
    "GS": "Goldman",
    "MS": "Morgan Stanley",
    "BAC": "Bank of America",
    "V": "Visa",
    "MA": "Mastercard",
    # Healthcare
    "UNH": "UnitedHealth",
    "JNJ": "J&J",
    "PFE": "Pfizer",
    "MRNA": "Moderna",
    "LLY": "Eli Lilly",
    # Commodities
    "GC=F": "Gold",
    "SI=F": "Silver",
    "CL=F": "Crude Oil",
    "NG=F": "Natural Gas",
    # Forex
    "DX-Y.NYB": "USD Index",
    "EURUSD=X": "EUR/USD",
    "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY",
    # ETFs
    "SPY": "S&P 500 ETF",
    "QQQ": "Nasdaq ETF",
    "IWM": "Russell 2000 ETF",
    "GLD": "Gold ETF",
    "TLT": "Treasury ETF",
    "XLF": "Financials ETF",
    "XLE": "Energy ETF",
    "ARKK": "ARK Innovation",
    # Space & Defense
    "LMT": "Lockheed",
    "RTX": "Raytheon",
    "BA": "Boeing",
    "RKLB": "Rocket Lab",
    # Travel & Hospitality
    "MAR": "Marriott",
    "HLT": "Hilton",
    "H": "Hyatt",
    "ABNB": "Airbnb",
    "BKNG": "Booking",
    "EXPE": "Expedia",
}

# ── Intel RSS Feeds (60+ sources) ─────────────────────────────────────────────
INTEL_FEEDS = [
    # Tier 1 — Major Wire Services
    ("Reuters", "https://feeds.reuters.com/reuters/topNews"),
    ("AP News", "https://rsshub.app/apnews/topics/apf-topnews"),
    # Tier 2 — Business/Finance
    ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss"),
    ("WSJ", "https://feeds.a]arstechnica.com/arstechnica/index"),
    ("FT", "https://www.ft.com/rss/home"),
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories"),
    ("Barron's", "https://www.barrons.com/feed"),
    # Tier 3 — Tech
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("Wired", "https://www.wired.com/feed/rss"),
    ("MIT Tech Review", "https://www.technologyreview.com/feed/"),
    ("Hacker News", "https://news.ycombinator.com/rss"),
    # Tier 4 — AI/ML
    ("AI News", "https://www.artificialintelligence-news.com/feed/"),
    ("OpenAI Blog", "https://openai.com/blog/rss/"),
    ("DeepMind", "https://deepmind.com/blog/feed/basic/"),
    # Tier 5 — Crypto
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("The Block", "https://www.theblock.co/rss.xml"),
    ("Decrypt", "https://decrypt.co/feed"),
    # Tier 6 — Science/Space
    ("NASA", "https://www.nasa.gov/rss/dyn/breaking_news.rss"),
    ("SpaceNews", "https://spacenews.com/feed/"),
    ("Nature", "https://www.nature.com/nature.rss"),
    ("Science Daily", "https://www.sciencedaily.com/rss/all.xml"),
    # Tier 7 — Geopolitics
    ("Foreign Affairs", "https://www.foreignaffairs.com/rss.xml"),
    ("The Economist", "https://www.economist.com/international/rss.xml"),
    ("Politico", "https://www.politico.com/rss/politicopicks.xml"),
    ("Defense One", "https://www.defenseone.com/rss/all/"),
]


YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}


async def fetch_yahoo_quote(symbol: str, client: httpx.AsyncClient) -> Optional[dict]:
    """Fetch a single quote from Yahoo Finance."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {"interval": "1d", "range": "5d"}
        resp = await client.get(url, params=params, headers=YAHOO_HEADERS, timeout=8.0)
        if resp.status_code == 429:
            # Rate limited - wait and retry once
            await asyncio.sleep(1)
            resp = await client.get(url, params=params, headers=YAHOO_HEADERS, timeout=8.0)
        if resp.status_code != 200:
            return None
        data = resp.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return None
        meta = result[0].get("meta", {})
        price = meta.get("regularMarketPrice", 0)
        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose") or meta.get("regularMarketPreviousClose") or price
        change_pct = ((price - prev_close) / prev_close * 100) if prev_close and prev_close != price else 0

        # Get sparkline data
        indicators = result[0].get("indicators", {}).get("quote", [{}])[0]
        closes = indicators.get("close", [])
        sparkline = [c for c in closes if c is not None][-20:] if closes else []

        return {
            "symbol": symbol.replace("-USD", "").replace("=F", "").replace("^", ""),
            "name": MARKET_SYMBOLS.get(symbol, symbol),
            "price": round(price, 2),
            "change": round(change_pct, 2),
            "sparkline": sparkline,
        }
    except Exception as e:
        logger.debug(f"Yahoo quote error for {symbol}: {e}")
        return None


async def get_markets(symbols: Optional[list] = None) -> list[dict]:
    """Fetch live market data using parallel v8/chart API calls."""
    if symbols is None:
        symbols = list(MARKET_SYMBOLS.keys())[:25]  # Top 25 for performance

    async with httpx.AsyncClient() as client:
        # Fire all requests concurrently with semaphore to avoid hammering
        sem = asyncio.Semaphore(8)
        async def fetch_one(sym):
            async with sem:
                return await fetch_yahoo_quote(sym, client)

        results = await asyncio.gather(*[fetch_one(s) for s in symbols])
        return [r for r in results if r is not None]


async def fetch_rss_feed(name: str, url: str, client: httpx.AsyncClient) -> list[dict]:
    """Fetch and parse a single RSS feed."""
    try:
        resp = await client.get(url, timeout=8.0, follow_redirects=True)
        if resp.status_code != 200:
            return []
        feed = feedparser.parse(resp.text)
        items = []
        for entry in feed.entries[:5]:  # Top 5 per source
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published:
                pub_time = datetime(*published[:6])
                delta = datetime.utcnow() - pub_time
                if delta.days > 0:
                    time_str = f"{delta.days}D AGO"
                elif delta.seconds >= 3600:
                    time_str = f"{delta.seconds // 3600}H AGO"
                else:
                    time_str = f"{delta.seconds // 60}M AGO"
            else:
                time_str = "RECENT"

            items.append({
                "source": name.upper(),
                "title": entry.get("title", "")[:120],
                "summary": (entry.get("summary") or entry.get("description") or "")[:200].strip(),
                "link": entry.get("link", ""),
                "time": time_str,
                "timestamp": pub_time.isoformat() if published else None,
            })
        return items
    except Exception as e:
        logger.debug(f"RSS error for {name}: {e}")
        return []


async def get_intel(limit: int = 30) -> list[dict]:
    """Aggregate intel from all RSS feeds."""
    async with httpx.AsyncClient() as client:
        tasks = [fetch_rss_feed(name, url, client) for name, url in INTEL_FEEDS]
        results = await asyncio.gather(*tasks)

    all_items = []
    for items in results:
        all_items.extend(items)

    # Sort by timestamp (most recent first), filter out items without timestamps
    with_ts = [i for i in all_items if i.get("timestamp")]
    with_ts.sort(key=lambda x: x["timestamp"], reverse=True)

    return with_ts[:limit]


async def get_polymarket(limit: int = 20) -> list[dict]:
    """Fetch live Polymarket prediction markets."""
    try:
        async with httpx.AsyncClient() as client:
            # Polymarket Gamma API (better for browsing markets)
            url = "https://gamma-api.polymarket.com/markets"
            params = {"limit": 100, "active": "true", "closed": "false", "order": "volume", "ascending": "false"}
            resp = await client.get(url, params=params, timeout=10.0)
            if resp.status_code != 200:
                # Fallback to CLOB API
                url = "https://clob.polymarket.com/markets"
                params = {"limit": 50, "active": "true"}
                resp = await client.get(url, params=params, timeout=10.0)
                if resp.status_code != 200:
                    return []

            raw = resp.json()
            # Handle both API structures
            data = raw.get("data", raw) if isinstance(raw, dict) else raw
            if not isinstance(data, list):
                data = [data] if data else []

            markets = []
            for m in data:
                if m.get("closed") or m.get("archived"):
                    continue
                question = m.get("question", "") or m.get("title", "")
                if not question or len(question) < 10:
                    continue

                # Get probability from various possible fields
                prob = 50
                if "outcomePrices" in m:
                    try:
                        prices = m["outcomePrices"]
                        if isinstance(prices, str):
                            import json
                            prices = json.loads(prices)
                        if prices and len(prices) > 0:
                            prob = int(float(prices[0]) * 100)
                    except:
                        pass
                elif "tokens" in m and m["tokens"]:
                    try:
                        prob = int(float(m["tokens"][0].get("price", 0.5)) * 100)
                    except:
                        pass

                vol = m.get("volume", 0) or m.get("volumeNum", 0) or 0
                try:
                    vol = float(vol)
                except:
                    vol = 0
                vol_str = f"${vol/1e6:.1f}M" if vol >= 1e6 else f"${vol/1e3:.0f}K" if vol >= 1000 else f"${vol:.0f}"

                markets.append({
                    "question": question[:150],
                    "prob": max(1, min(99, prob)),
                    "volume": vol_str,
                    "end_date": m.get("endDate") or m.get("end_date_iso"),
                    "slug": m.get("slug") or m.get("condition_id", ""),
                })

            # Sort by volume (already sorted but ensure)
            markets.sort(key=lambda x: float(x["volume"].replace("$","").replace("M","000000").replace("K","000")), reverse=True)
            return markets[:limit]
    except Exception as e:
        logger.error(f"Polymarket API error: {e}")
        return []


# ── Summit Calendar ───────────────────────────────────────────────────────────
SUMMITS = [
    {"month": "JUN", "day": "12", "name": "BILDERBERG 2026", "location": "Madrid, Spain", "date": "2026-06-12"},
    {"month": "JUL", "day": "09", "name": "SUN VALLEY CONFERENCE", "location": "Sun Valley, Idaho", "date": "2026-07-09"},
    {"month": "SEP", "day": "17", "name": "UN GENERAL ASSEMBLY", "location": "New York, NY", "date": "2026-09-17"},
    {"month": "NOV", "day": "15", "name": "G20 SUMMIT", "location": "South Africa", "date": "2026-11-15"},
    {"month": "JAN", "day": "20", "name": "WEF DAVOS 2027", "location": "Davos, Switzerland", "date": "2027-01-20"},
    {"month": "FEB", "day": "17", "name": "MUNICH SECURITY CONF", "location": "Munich, Germany", "date": "2027-02-17"},
]


def get_summits() -> list[dict]:
    """Return upcoming summits."""
    today = datetime.now().strftime("%Y-%m-%d")
    return [s for s in SUMMITS if s["date"] >= today]
