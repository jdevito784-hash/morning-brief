"""
Morning Brief — build_brief.py

Fetches market news and economic data, optionally runs it through Claude,
and renders index.html from template.html.

Runs automatically via GitHub Actions every weekday morning.
Only edit the CONFIG block below to customize your brief.
"""

import os
import re
import datetime
import requests
import anthropic

# ─── CONFIG ──────────────────────────────────────────────────────────────────
# Edit this section to personalize your brief.

# Watchlist — ticker symbols to pull news for.
TICKERS = ["VOO", "RDW", "MSFT", "AVGO", "NOW"]

# Economic indicators — FRED series ID -> display label.
# Find more series at https://fred.stlouisfed.org (ID is in the page URL).
FRED_SERIES = {
    "UNRATE":   "Unemployment Rate (%)",
    "FEDFUNDS": "Fed Funds Rate (%)",
    "DGS10":    "10-Yr Treasury (%)",
    "CPIAUCSL": "CPI Index",
    "GDP":      "GDP ($B)",
}

# How many days back to look for news, and article limits per section.
LOOKBACK_DAYS          = 2
MAX_ARTICLES_PER_TICKER = 8
MAX_GENERAL_ARTICLES   = 15

# Claude model to use for the AI summary.
# claude-haiku-4-5-20251001  → cheapest, fast (~$0.01/run)
# claude-sonnet-4-6          → better synthesis, ~10x cost
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# ─── KEYS ────────────────────────────────────────────────────────────────────
# All secrets are read from environment variables set as GitHub repo Secrets.
# Never paste keys directly into this file.

FINNHUB_KEY = os.environ["FINNHUB_API_KEY"]
FRED_KEY    = os.environ["FRED_API_KEY"]
# Anthropic SDK reads ANTHROPIC_API_KEY automatically from the environment.
claude = anthropic.Anthropic()


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
    # social API) and merge the results before passing to Claude.
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
            macro_rows.append((label, latest, prior, date))

    return company_news, general_news, macro_rows


# ─── HTML COMPONENTS ──────────────────────────────────────────────────────────
# Each function returns an HTML string for one UI component.
# Add new components here and reference them in render_page().

def render_macro_grid(macro_rows):
    """Render the row of economic indicator cards."""
    if not macro_rows:
        return ""
    cards = []
    for label, latest, prior, date in macro_rows:
        trend = ""
        if prior is not None:
            if latest > prior:
                trend = '<span class="up">&#9650;</span>'
            elif latest < prior:
                trend = '<span class="down">&#9660;</span>'
        cards.append(
            f'<div class="macro-card">'
            f'  <div class="macro-card-label">{label}</div>'
            f'  <div class="macro-card-value">{latest} {trend}</div>'
            f'  <div class="macro-card-date">{date}</div>'
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
    Render the full news feed section (watchlist + general market news).

    FUTURE — ADDITIONAL NEWS SOURCES
    Add more blocks here (e.g. earnings calendar, options flow, X posts).
    Each block is just a render_ticker_block() or a new component function.
    """
    parts = []

    # Watchlist news
    watchlist_blocks = [
        render_ticker_block(sym, articles)
        for sym, articles in company_news.items()
        if articles
    ]
    if watchlist_blocks:
        parts.append('<div class="section-label">Watchlist News<span style="flex:1;height:1px;background:var(--border);margin-left:10px;display:inline-block;vertical-align:middle"></span></div>')
        parts.extend(watchlist_blocks)

    # General market news
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
            parts.append('<div class="section-label" style="margin-top:32px">General Market News<span style="flex:1;height:1px;background:var(--border);margin-left:10px;display:inline-block;vertical-align:middle"></span></div>')
            parts.append('<div class="news-list">' + "".join(items) + "</div>")

    return "\n".join(parts)


# ─── CLAUDE AI SUMMARY ────────────────────────────────────────────────────────

def build_source_text(company_news, general_news, macro_rows):
    """Flatten all fetched data into a plain-text block for Claude's prompt."""
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
    for label, latest, prior, date in macro_rows:
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
    Edit the prompt below to change Claude's tone, focus areas, or output format.
    You can also add extra context (e.g. user's portfolio size, risk tolerance).
    """
    source_text = build_source_text(company_news, general_news, macro_rows)
    prompt = f"""You are a sharp markets analyst writing a private morning brief for an active equities and options trader.

Write a tight, no-fluff brief from the raw data below. Rules:
- Lead with "What matters most today" — 3 to 5 bullets, only genuinely market-moving items.
- Then a short per-ticker section. ONLY include tickers with something material. Skip PR noise and duplicate headlines.
- End with a "Macro read" — 2-3 sentences tying the economic data together.
- Note sentiment (bullish/bearish) in plain terms where useful.
- Be direct. No filler.

Output ONLY clean HTML using: <h2>, <h3>, <p>, <ul>, <li>, <strong>.
Wrap ticker symbols in <span class="ticker">SYMBOL</span>.
Do not include <html>, <head>, <body>, or markdown code fences.

RAW DATA:
{source_text}
"""
    resp = claude.messages.create(
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
      {{DATE}}      — formatted date string
      {{GENERATED}} — UTC generation time
      {{MACRO}}     — macro indicator cards
      {{BRIEF}}     — Claude AI summary (or empty string)
    """
    now      = datetime.datetime.now(datetime.timezone.utc)
    date_str = now.strftime("%A, %B %-d, %Y")
    gen_str  = now.strftime("%H:%M UTC")

    with open("template.html", "r", encoding="utf-8") as f:
        template = f.read()

    # Combine Claude summary + raw news feed into the {{BRIEF}} slot.
    # When Claude is disabled, brief_html is empty and only news_html shows.
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
    # Fetch curated X posts here and pass them into write_claude_brief() as
    # an additional data source alongside company_news and general_news.

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
