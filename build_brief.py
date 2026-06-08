"""
Morning Brief builder.

Pulls news for your watchlist + key economic indicators and writes
index.html (your web page).

Runs automatically via GitHub Actions. You only edit the CONFIG block below.
"""

import os
import re
import datetime
import requests
import anthropic

# ----------------------------------------------------------------------
# CONFIG  --  this is the only part you need to edit
# ----------------------------------------------------------------------

# The stocks you want covered. Use ticker symbols.
TICKERS = ["VOO", "RDW", "MSFT", "AVGO", "NOW"]

# Economic indicators (FRED series IDs -> friendly label).
# Find more at https://fred.stlouisfed.org  (the ID is in the page URL)
FRED_SERIES = {
    "UNRATE":   "Unemployment Rate (%)",
    "FEDFUNDS": "Fed Funds Rate (%)",
    "DGS10":    "10-Yr Treasury (%)",
    "CPIAUCSL": "CPI Index",
    "GDP":      "GDP ($B)",
}

# How far back to look for company news, and how many articles per ticker.
LOOKBACK_DAYS = 2
MAX_ARTICLES_PER_TICKER = 8
MAX_GENERAL_ARTICLES = 15

# ----------------------------------------------------------------------
# Keys are read from environment variables (set as GitHub repo Secrets).
# Never paste your keys directly into this file.
# ----------------------------------------------------------------------
FINNHUB_KEY = os.environ["FINNHUB_API_KEY"]
FRED_KEY = os.environ["FRED_API_KEY"]
# Anthropic SDK reads ANTHROPIC_API_KEY automatically
claude = anthropic.Anthropic()


# ----------------------------------------------------------------------
# Data fetching
# ----------------------------------------------------------------------
def fetch_company_news(symbol):
    today = datetime.date.today()
    start = today - datetime.timedelta(days=LOOKBACK_DAYS)
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={
                "symbol": symbol,
                "from": start.isoformat(),
                "to": today.isoformat(),
                "token": FINNHUB_KEY,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()[:MAX_ARTICLES_PER_TICKER]
    except Exception as e:
        print(f"[warn] news fetch failed for {symbol}: {e}")
        return []


def fetch_general_news():
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
    """Return (latest_value, prior_value, date) for a series, or None."""
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key": FRED_KEY,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 2,
            },
            timeout=30,
        )
        r.raise_for_status()
        obs = [o for o in r.json().get("observations", []) if o["value"] != "."]
        if not obs:
            return None
        latest = float(obs[0]["value"])
        prior = float(obs[1]["value"]) if len(obs) > 1 else None
        return latest, prior, obs[0]["date"]
    except Exception as e:
        print(f"[warn] FRED fetch failed for {series_id}: {e}")
        return None


# ----------------------------------------------------------------------
# Fetch all data
# ----------------------------------------------------------------------
def fetch_all():
    company_news = {sym: fetch_company_news(sym) for sym in TICKERS}
    general_news = fetch_general_news()
    macro_rows = []
    for series_id, label in FRED_SERIES.items():
        result = fetch_fred(series_id)
        if not result:
            continue
        latest, prior, date = result
        macro_rows.append((label, latest, prior, date))
    return company_news, general_news, macro_rows


# ----------------------------------------------------------------------
# Build source text for Claude
# ----------------------------------------------------------------------
def build_source_text(company_news, general_news, macro_rows):
    chunks = ["=== COMPANY NEWS (watchlist) ==="]
    for sym, articles in company_news.items():
        if not articles:
            continue
        chunks.append(f"\n## {sym}")
        for a in articles:
            headline = a.get("headline", "").strip()
            summary = a.get("summary", "").strip()
            source = a.get("source", "")
            chunks.append(f"- [{source}] {headline}\n  {summary}")

    chunks.append("\n=== GENERAL MARKET NEWS ===")
    for a in general_news:
        headline = a.get("headline", "").strip()
        source = a.get("source", "")
        chunks.append(f"- [{source}] {headline}")

    chunks.append("\n=== ECONOMIC INDICATORS (latest) ===")
    for label, latest, prior, date in macro_rows:
        delta = ""
        if prior is not None:
            diff = latest - prior
            arrow = "up" if diff > 0 else ("down" if diff < 0 else "flat")
            delta = f" ({arrow} {abs(diff):.2f} from prior)"
        chunks.append(f"- {label}: {latest}{delta}  [{date}]")

    return "\n".join(chunks)


# ----------------------------------------------------------------------
# Ask Claude to write the brief
# ----------------------------------------------------------------------
def write_brief(company_news, general_news, macro_rows):
    source_text = build_source_text(company_news, general_news, macro_rows)
    prompt = f"""You are a sharp markets analyst writing a private morning brief for an active equities and options trader. Below is today's raw news for their watchlist, general market news, and the latest economic indicators.

Write a tight, no-fluff brief. Rules:
- Lead with a section "What matters most today" — 3 to 5 bullets, only genuinely market-moving items.
- Then a short per-ticker section, but ONLY include tickers that have something material. Skip tickers with nothing but PR noise or repeated headlines.
- End with a brief "Macro read" — 2-3 sentences tying the economic data together.
- Strip promotional content, clickbait, and duplicate stories. Be direct.
- Where useful, note sentiment (bullish/bearish) in plain terms.

Output ONLY clean HTML using these tags: <h2>, <h3>, <p>, <ul>, <li>, <strong>. Wrap ticker symbols in <span class="ticker">SYMBOL</span>. Do not include <html>, <head>, <body>, or markdown code fences.

RAW MATERIAL:
{source_text}
"""
    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}],
    )
    html = "".join(b.text for b in resp.content if b.type == "text").strip()
    html = re.sub(r"^```(?:html)?\s*|\s*```$", "", html).strip()
    return html


# ----------------------------------------------------------------------
# Render the page
# ----------------------------------------------------------------------
def render_macro_table(macro_rows):
    if not macro_rows:
        return ""
    cells = []
    for label, latest, prior, date in macro_rows:
        trend = ""
        if prior is not None:
            if latest > prior:
                trend = '<span class="up">&#9650;</span>'
            elif latest < prior:
                trend = '<span class="down">&#9660;</span>'
        cells.append(
            f'<div class="macro-card"><div class="macro-label">{label}</div>'
            f'<div class="macro-value">{latest} {trend}</div>'
            f'<div class="macro-date">{date}</div></div>'
        )
    return '<div class="macro-grid">' + "".join(cells) + "</div>"


def render_page(brief_html, macro_rows):
    now = datetime.datetime.now(datetime.timezone.utc)
    date_str = now.strftime("%A, %B %-d, %Y")
    gen_str = now.strftime("%H:%M UTC")
    macro_html = render_macro_table(macro_rows)

    with open("template.html", "r", encoding="utf-8") as f:
        template = f.read()

    return (
        template
        .replace("{{DATE}}", date_str)
        .replace("{{GENERATED}}", gen_str)
        .replace("{{MACRO}}", macro_html)
        .replace("{{BRIEF}}", brief_html)
    )


def main():
    print("Gathering sources...")
    company_news, general_news, macro_rows = fetch_all()
    print("Asking Claude to write the brief...")
    brief_html = write_brief(company_news, general_news, macro_rows)
    print("Rendering page...")
    page = render_page(brief_html, macro_rows)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(page)
    print("Done -> index.html")


if __name__ == "__main__":
    main()
