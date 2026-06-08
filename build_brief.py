"""
Morning Brief — build_brief.py

Fetches market news and economic data, runs it through Claude,
and renders index.html from template.html.

Runs automatically via GitHub Actions every weekday morning.
Only edit the CONFIG block below to customize your brief.
"""

import os
import re
import datetime
import zoneinfo
import requests
import anthropic

# ─── CONFIG ──────────────────────────────────────────────────────────────────

# Watchlist — ticker symbols to pull news for.
TICKERS = ["VOO", "RDW", "MSFT", "AVGO", "NOW"]

# Economic indicators — FRED series ID -> display label.
# Find more series at https://fred.stlouisfed.org (ID is in the page URL).
FRED_SERIES = {
    "UNRATE":   "Unemployment Rate",
    "FEDFUNDS": "Fed Funds Rate",
    "DGS10":    "10-Yr Treasury",
    "CPIAUCSL": "CPI (YoY %)",
    "GDP":      "GDP",
}

# How many days back to look for news, and article limits per section.
LOOKBACK_DAYS           = 2
MAX_ARTICLES_PER_TICKER = 8
MAX_GENERAL_ARTICLES    = 15

# Claude model to use for the AI summary.
# claude-haiku-4-5-20251001  → cheapest, fast (~$0.01/run)
# claude-sonnet-4-6          → better synthesis, ~10x cost
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# ─── KEYS ────────────────────────────────────────────────────────────────────

FINNHUB_KEY = os.environ["FINNHUB_API_KEY"]
FRED_KEY    = os.environ["FRED_API_KEY"]
claude_client = anthropic.Anthropic()

# US Eastern timezone (handles EST/EDT automatically)
ET = zoneinfo.ZoneInfo("America/New_York")


# ─── MARKET STATUS ────────────────────────────────────────────────────────────

# US market holidays 2026 (add years as needed)
MARKET_HOLIDAYS = {
    datetime.date(2026, 1, 1),   # New Year's Day
    datetime.date(2026, 1, 19),  # MLK Day
    datetime.date(2026, 2, 16),  # Presidents Day
    datetime.date(2026, 4, 3),   # Good Friday
    datetime.date(2026, 5, 25),  # Memorial Day
    datetime.date(2026, 7, 3),   # Independence Day (observed)
    datetime.date(2026, 9, 7),   # Labor Day
    datetime.date(2026, 11, 26), # Thanksgiving
    datetime.date(2026, 11, 27), # Black Friday (early close — treated as full close)
    datetime.date(2026, 12, 25), # Christmas
}

def get_market_status():
    """
    Return a dict with market status info for the masthead.
    Keys: status ('open'|'premarket'|'closed'|'holiday'|'weekend'),
          label (display string), color ('green'|'yellow'|'red')
    """
    now_et   = datetime.datetime.now(ET)
    today    = now_et.date()
    weekday  = today.weekday()  # 0=Mon, 6=Sun
    t        = now_et.time()
    open_t   = datetime.time(9, 30)
    close_t  = datetime.time(16, 0)
    pre_t    = datetime.time(4, 0)

    if today in MARKET_HOLIDAYS:
        return {"status": "holiday", "label": "Market Holiday", "color": "red"}
    if weekday >= 5:
        # Find next Monday (or skip holiday)
        days_ahead = 7 - weekday  # days until Monday
        next_open  = today + datetime.timedelta(days=days_ahead)
        while next_open in MARKET_HOLIDAYS or next_open.weekday() >= 5:
            next_open += datetime.timedelta(days=1)
        return {"status": "weekend", "label": f"Weekend — opens {next_open.strftime('%a %b %-d')}", "color": "red"}
    if open_t <= t < close_t:
        closes_at = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        diff      = closes_at - now_et
        h, rem    = divmod(int(diff.total_seconds()), 3600)
        m         = rem // 60
        return {"status": "open", "label": f"Market Open — closes in {h}h {m:02d}m ET", "color": "green"}
    if pre_t <= t < open_t:
        opens_at = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        diff     = opens_at - now_et
        h, rem   = divmod(int(diff.total_seconds()), 3600)
        m        = rem // 60
        return {"status": "premarket", "label": f"Pre-Market — opens in {h}h {m:02d}m ET", "color": "yellow"}
    # After close
    return {"status": "closed", "label": "Market Closed", "color": "red"}


# ─── DATA FETCHING ────────────────────────────────────────────────────────────

def fetch_company_news(symbol):
    """Pull recent news headlines for a single ticker from Finnhub."""
    today = datetime.date.today()
    start = today - datetime.timedelta(days=LOOKBACK_DAYS)
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={
                "symbol": symbol,
                "from":   start.isoformat(),
                "to":     today.isoformat(),
                "token":  FINNHUB_KEY,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()[:MAX_ARTICLES_PER_TICKER]
    except Exception as e:
        print(f"[warn] news fetch failed for {symbol}: {e}")
        return []


def fetch_general_news():
    """Pull general market headlines from Finnhub."""
    # FUTURE — X ACCOUNT TRACKING
    # Add a second source here (e.g. curated X/Twitter accounts via a
    # social API) and merge results before passing to Claude.
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": "general", "token": FINNHUB_KEY},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()[:MAX_GENERAL_ARTICLES]
    except Exception as e:
        print(f"[warn] general news fetch failed: {e}")
        return []


def fetch_fred(series_id):
    """Return (latest_value, prior_value, date) for a FRED series, or None."""
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id":  series_id,
                "api_key":    FRED_KEY,
                "file_type":  "json",
                "sort_order": "desc",
                "limit":      2,
            },
            timeout=30,
        )
        r.raise_for_status()
        obs = [o for o in r.json().get("observations", []) if o["value"] != "."]
        if not obs:
            return None
        latest = float(obs[0]["value"])
        prior  = float(obs[1]["value"]) if len(obs) > 1 else None
        return latest, prior, obs[0]["date"]
    except Exception as e:
        print(f"[warn] FRED fetch failed for {series_id}: {e}")
        return None


def fetch_all():
    """Fetch all data sources and return structured results."""
    company_news = {sym: fetch_company_news(sym) for sym in TICKERS}
    general_news = fetch_general_news()

    macro_rows = []
    for series_id, label in FRED_SERIES.items():
        result = fetch_fred(series_id)
        if result:
            latest, prior, date = result
            macro_rows.append((series_id, label, latest, prior, date))

    return company_news, general_news, macro_rows


# ─── MACRO FORMATTING ─────────────────────────────────────────────────────────

def format_macro_value(series_id, label, latest, prior):
    """
    Format a raw FRED value into a human-readable string with units.
    CPI is converted to YoY % change vs prior reading.
    GDP is shown in trillions.
    """
    if series_id == "GDP":
        return f"${latest / 1000:.1f}T"
    if series_id == "CPIAUCSL":
        if prior and prior > 0:
            yoy = ((latest - prior) / prior) * 100
            return f"{yoy:.1f}%"
        return f"{latest:.1f}"
    if series_id in ("UNRATE", "FEDFUNDS", "DGS10"):
        return f"{latest:.2f}%"
    return f"{latest:,.3f}"


def is_stale(date_str, threshold_days=45):
    """Return True if the data date is older than threshold_days."""
    try:
        data_date = datetime.date.fromisoformat(date_str)
        return (datetime.date.today() - data_date).days > threshold_days
    except Exception:
        return False


# ─── HTML COMPONENTS ──────────────────────────────────────────────────────────

def render_macro_grid(macro_rows):
    """Render the economic indicator cards strip."""
    if not macro_rows:
        return ""
    cards = []
    for series_id, label, latest, prior, date in macro_rows:
        value_str = format_macro_value(series_id, label, latest, prior)

        trend = ""
        if prior is not None:
            if latest > prior:
                trend = '<span class="up">&#9650;</span>'
            elif latest < prior:
                trend = '<span class="down">&#9660;</span>'

        stale_badge = (
            '<span class="stale-badge">stale</span>'
            if is_stale(date) else ""
        )

        cards.append(
            f'<div class="macro-card">'
            f'  <div class="macro-card-label">{label}</div>'
            f'  <div class="macro-card-value">{value_str} {trend}</div>'
            f'  <div class="macro-card-date">{date} {stale_badge}</div>'
            f'</div>'
        )
    return '<div class="macro-grid">' + "".join(cards) + "</div>"


def render_news_item(article):
    """Render a single news article as a card."""
    headline = article.get("headline", "").strip()
    if not headline:
        return ""
    source  = article.get("source", "")
    summary = article.get("summary", "").strip()
    url     = article.get("url", "")

    headline_html = (
        f'<a href="{url}" target="_blank" rel="noopener">{headline}</a>'
        if url else headline
    )
    summary_html = (
        f'<div class="news-item-summary">'
        f'{summary[:220]}{"…" if len(summary) > 220 else ""}'
        f'</div>'
        if summary and summary != headline else ""
    )
    return (
        f'<div class="news-item">'
        f'  <div class="news-item-source">{source}</div>'
        f'  <div class="news-item-headline">{headline_html}</div>'
        f'  {summary_html}'
        f'</div>'
    )


def render_ticker_block(symbol, articles):
    """Render one ticker's news as a labeled card group."""
    seen  = set()
    items = []
    for a in articles:
        headline = a.get("headline", "").strip()
        if not headline or headline in seen:
            continue
        seen.add(headline)
        card = render_news_item(a)
        if card:
            items.append(card)
    if not items:
        return ""
    return (
        f'<div class="ticker-block">'
        f'  <div class="ticker-block-header">'
        f'    <span class="ticker">{symbol}</span>'
        f'  </div>'
        f'  <div class="news-list">{"".join(items)}</div>'
        f'</div>'
    )


def render_news_feed(company_news, general_news):
    """
    Render the full raw news feed section below the Claude summary.

    FUTURE — ADDITIONAL NEWS SOURCES
    Add more blocks here (earnings calendar, options flow, X posts).
    Each block is just a render_ticker_block() call or a new component.
    """
    parts = []

    watchlist_blocks = [
        render_ticker_block(sym, articles)
        for sym, articles in company_news.items()
        if articles
    ]
    if watchlist_blocks:
        parts.append('<div class="section-label">Watchlist News</div>')
        parts.extend(watchlist_blocks)

    if general_news:
        seen  = set()
        items = []
        for a in general_news:
            headline = a.get("headline", "").strip()
            if not headline or headline in seen:
                continue
            seen.add(headline)
            card = render_news_item(a)
            if card:
                items.append(card)
        if items:
            parts.append('<div class="section-label" style="margin-top:32px">General Market News</div>')
            parts.append('<div class="news-list">' + "".join(items) + "</div>")

    return "\n".join(parts)


# ─── CLAUDE AI SUMMARY ────────────────────────────────────────────────────────

def build_source_text(company_news, general_news, macro_rows):
    """Flatten all fetched data into plain text for Claude's prompt."""
    chunks = ["=== COMPANY NEWS (watchlist) ==="]
    for sym, articles in company_news.items():
        if not articles:
            continue
        chunks.append(f"\n## {sym}")
        for a in articles:
            headline = a.get("headline", "").strip()
            summary  = a.get("summary", "").strip()
            source   = a.get("source", "")
            chunks.append(f"- [{source}] {headline}\n  {summary}")

    chunks.append("\n=== GENERAL MARKET NEWS ===")
    for a in general_news:
        headline = a.get("headline", "").strip()
        source   = a.get("source", "")
        chunks.append(f"- [{source}] {headline}")

    chunks.append("\n=== ECONOMIC INDICATORS ===")
    for series_id, label, latest, prior, date in macro_rows:
        delta = ""
        if prior is not None:
            diff  = latest - prior
            arrow = "up" if diff > 0 else ("down" if diff < 0 else "flat")
            delta = f" ({arrow} {abs(diff):.2f} from prior)"
        chunks.append(f"- {label}: {latest}{delta}  [{date}]")

    return "\n".join(chunks)


def write_claude_brief(company_news, general_news, macro_rows):
    """
    Ask Claude to write the analyst summary section.

    FUTURE — PROMPT TUNING
    Edit the prompt to change tone, focus, or output format.
    Add extra context here (e.g. portfolio size, risk tolerance, sector focus).
    """
    source_text = build_source_text(company_news, general_news, macro_rows)
    prompt = f"""You are a sharp markets analyst writing a private morning brief for an active retail investor who trades equities and options.

Write a tight, no-fluff brief from the raw data below. Rules:
- Lead with "What matters most today" — 3 to 5 bullets of genuinely market-moving items only.
- Then a per-ticker section. ONLY include tickers with something material and actionable. If a ticker has no meaningful news, do NOT include it at all — not even a "nothing to report" line.
- End with a "Macro read" — 2 to 3 sentences tying the economic indicators together.
- Note sentiment (bullish/bearish) in plain terms where it adds value.
- Be direct. No filler. No promotional content. No duplicate stories.

Output ONLY clean HTML using: <h2>, <h3>, <p>, <ul>, <li>, <strong>.
Wrap ticker symbols in <span class="ticker">SYMBOL</span>.
Do not include <html>, <head>, <body>, or markdown code fences.

RAW DATA:
{source_text}
"""
    resp = claude_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}],
    )
    html = "".join(b.text for b in resp.content if b.type == "text").strip()
    html = re.sub(r"^```(?:html)?\s*|\s*```$", "", html).strip()
    return html


# ─── PAGE RENDERER ────────────────────────────────────────────────────────────

def render_page(macro_rows, brief_html, news_html):
    """
    Slot all components into template.html and return the final page string.

    template.html placeholders:
      {{DATE}}      — formatted date (ET)
      {{GENERATED}} — generation time (ET)
      {{MACRO}}     — macro indicator cards
      {{BRIEF}}     — Claude summary + raw news feed
    """
    now_et   = datetime.datetime.now(ET)
    date_str = now_et.strftime("%A, %B %-d, %Y")
    gen_str  = now_et.strftime("%-I:%M %p ET")

    with open("template.html", "r", encoding="utf-8") as f:
        template = f.read()

    combined_brief = "\n".join(filter(None, [brief_html, news_html]))

    return (
        template
        .replace("{{DATE}}",      date_str)
        .replace("{{GENERATED}}", gen_str)
        .replace("{{MACRO}}",     render_macro_grid(macro_rows))
        .replace("{{BRIEF}}",     combined_brief)
    )


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print("Gathering sources...")
    company_news, general_news, macro_rows = fetch_all()

    # FUTURE — X ACCOUNT TRACKING
    # Fetch curated X posts here and merge into company_news / general_news
    # before passing to write_claude_brief().

    print("Asking Claude to write the brief...")
    brief_html = write_claude_brief(company_news, general_news, macro_rows)

    print("Rendering news feed...")
    news_html = render_news_feed(company_news, general_news)

    print("Rendering page...")
    page = render_page(macro_rows, brief_html, news_html)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(page)

    print("Done → index.html")


if __name__ == "__main__":
    main()
