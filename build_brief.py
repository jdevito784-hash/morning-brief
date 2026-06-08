"""
Morning Brief builder.

Pulls news for your watchlist + key economic indicators and writes
index.html (your web page).

Runs automatically via GitHub Actions. You only edit the CONFIG block below.
"""

import os
import datetime
import requests

# ----------------------------------------------------------------------
# CONFIG  --  this is the only part you need to edit
# ----------------------------------------------------------------------

# The stocks you want covered. Use ticker symbols.
TICKERS = ["NVDA", "AMD", "MSFT", "AVGO", "EXE"]

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
# Format raw data into HTML without an AI API
# ----------------------------------------------------------------------
def write_brief(company_news, general_news, macro_rows):
    parts = []

    # Per-ticker sections
    tickers_with_news = [(sym, articles) for sym, articles in company_news.items() if articles]
    if tickers_with_news:
        parts.append("<h2>Watchlist News</h2>")
        for sym, articles in tickers_with_news:
            parts.append(f'<h3><span class="ticker">{sym}</span></h3><ul>')
            seen = set()
            for a in articles:
                headline = a.get("headline", "").strip()
                if not headline or headline in seen:
                    continue
                seen.add(headline)
                source = a.get("source", "")
                summary = a.get("summary", "").strip()
                url = a.get("url", "")
                line = f'<strong>[{source}]</strong> '
                line += f'<a href="{url}" target="_blank">{headline}</a>' if url else headline
                if summary and summary != headline:
                    line += f"<br><small>{summary[:200]}{'…' if len(summary) > 200 else ''}</small>"
                parts.append(f"<li>{line}</li>")
            parts.append("</ul>")

    # General market news
    if general_news:
        parts.append("<h2>General Market News</h2><ul>")
        seen = set()
        for a in general_news:
            headline = a.get("headline", "").strip()
            if not headline or headline in seen:
                continue
            seen.add(headline)
            source = a.get("source", "")
            url = a.get("url", "")
            line = f'<strong>[{source}]</strong> '
            line += f'<a href="{url}" target="_blank">{headline}</a>' if url else headline
            parts.append(f"<li>{line}</li>")
        parts.append("</ul>")

    return "\n".join(parts)


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
    print("Building brief...")
    brief_html = write_brief(company_news, general_news, macro_rows)
    print("Rendering page...")
    page = render_page(brief_html, macro_rows)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(page)
    print("Done -> index.html")


if __name__ == "__main__":
    main()
