"""
Morning Brief — build_brief.py

Fetches market news and economic data, runs it through Claude,
and renders index.html from template.html.

Runs automatically via GitHub Actions every weekday morning.
Only edit the CONFIG block to customize your brief.
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

# Company/fund names matched to tickers — shown in ticker cards.
TICKER_NAMES = {
    "VOO":  "Vanguard S&P 500 ETF",
    "RDW":  "Redwire Corp",
    "MSFT": "Microsoft Corp",
    "AVGO": "Broadcom Inc",
    "NOW":  "ServiceNow Inc",
}

# Economic indicators — FRED series ID -> display label.
# Find more at https://fred.stlouisfed.org (ID is in the page URL).
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
# claude-haiku-4-5-20251001 → cheapest, fast (~$0.01/run)
# claude-sonnet-4-6         → better synthesis, ~10x cost
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# ─── KEYS ────────────────────────────────────────────────────────────────────

FINNHUB_KEY   = os.environ["FINNHUB_API_KEY"]
FRED_KEY      = os.environ["FRED_API_KEY"]
claude_client = anthropic.Anthropic()

ET = zoneinfo.ZoneInfo("America/New_York")


# ─── DATA FETCHING ────────────────────────────────────────────────────────────

def fetch_company_news(symbol):
    today = datetime.date.today()
    start = today - datetime.timedelta(days=LOOKBACK_DAYS)
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={"symbol": symbol, "from": start.isoformat(),
                    "to": today.isoformat(), "token": FINNHUB_KEY},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()[:MAX_ARTICLES_PER_TICKER]
    except Exception as e:
        print(f"[warn] news fetch failed for {symbol}: {e}")
        return []


def fetch_general_news():
    # FUTURE — X ACCOUNT TRACKING
    # Merge curated X/Twitter posts here before passing to Claude.
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
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": series_id, "api_key": FRED_KEY,
                    "file_type": "json", "sort_order": "desc", "limit": 2},
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
    company_news = {sym: fetch_company_news(sym) for sym in TICKERS}
    general_news = fetch_general_news()
    macro_rows   = []
    for series_id, label in FRED_SERIES.items():
        result = fetch_fred(series_id)
        if result:
            latest, prior, date = result
            macro_rows.append((series_id, label, latest, prior, date))
    return company_news, general_news, macro_rows


# ─── MACRO FORMATTING ─────────────────────────────────────────────────────────

def format_macro_value(series_id, latest, prior):
    if series_id == "GDP":
        return f"${latest / 1000:.1f}T"
    if series_id == "CPIAUCSL":
        if prior and prior > 0:
            return f"{((latest - prior) / prior * 100):.1f}%"
        return f"{latest:.1f}"
    if series_id in ("UNRATE", "FEDFUNDS", "DGS10"):
        return f"{latest:.2f}%"
    return f"{latest:,.3f}"


def is_stale(date_str, threshold_days=45):
    try:
        return (datetime.date.today() - datetime.date.fromisoformat(date_str)).days > threshold_days
    except Exception:
        return False


# ─── HTML COMPONENTS ──────────────────────────────────────────────────────────

def render_macro_grid(macro_rows):
    """Economic indicator cards strip — Python-generated, no Claude needed."""
    if not macro_rows:
        return ""
    cards = []
    for series_id, label, latest, prior, date in macro_rows:
        value_str = format_macro_value(series_id, latest, prior)
        trend = ""
        if prior is not None:
            trend = ('<span class="up">&#9650;</span>' if latest > prior
                     else '<span class="down">&#9660;</span>' if latest < prior else "")
        stale = '<span class="stale-badge">stale</span>' if is_stale(date) else ""
        cards.append(
            f'<div class="macro-card">'
            f'<div class="macro-card-label">{label}</div>'
            f'<div class="macro-card-value">{value_str} {trend}</div>'
            f'<div class="macro-card-date">{date}{stale}</div>'
            f'</div>'
        )
    return '<div class="macro-grid">' + "".join(cards) + "</div>"


def render_news_item(article):
    """Single news article card."""
    headline = article.get("headline", "").strip()
    if not headline:
        return ""
    source  = article.get("source", "")
    summary = article.get("summary", "").strip()
    url     = article.get("url", "")
    hl_html = (f'<a href="{url}" target="_blank" rel="noopener">{headline}</a>'
               if url else headline)
    sm_html = (f'<div class="news-item-summary">{summary[:220]}{"…" if len(summary) > 220 else ""}</div>'
               if summary and summary != headline else "")
    return (f'<div class="news-item">'
            f'<div class="news-item-source">{source}</div>'
            f'<div class="news-item-headline">{hl_html}</div>'
            f'{sm_html}</div>')


def render_ticker_block(symbol, articles):
    """One ticker's raw news as a labeled card group."""
    seen  = set()
    items = []
    for a in articles:
        hl = a.get("headline", "").strip()
        if not hl or hl in seen:
            continue
        seen.add(hl)
        card = render_news_item(a)
        if card:
            items.append(card)
    if not items:
        return ""
    return (f'<div class="ticker-block">'
            f'<div class="ticker-block-header"><span class="ticker">{symbol}</span></div>'
            f'<div class="news-list">{"".join(items)}</div>'
            f'</div>')


def render_news_feed(company_news, general_news):
    """
    Full raw news feed — watchlist articles + general market headlines.
    Rendered separately from the Claude brief so the two don't mix.

    FUTURE — ADDITIONAL SOURCES
    Add earnings calendar, options flow, or X post blocks here.
    """
    parts = []

    watchlist = [render_ticker_block(sym, arts)
                 for sym, arts in company_news.items() if arts]
    if any(watchlist):
        parts.append('<div class="section-label">Watchlist News</div>')
        parts.extend(w for w in watchlist if w)

    if general_news:
        seen  = set()
        items = []
        for a in general_news:
            hl = a.get("headline", "").strip()
            if not hl or hl in seen:
                continue
            seen.add(hl)
            card = render_news_item(a)
            if card:
                items.append(card)
        if items:
            parts.append('<div class="section-label" style="margin-top:24px">General Market News</div>')
            parts.append('<div class="news-list">' + "".join(items) + "</div>")

    return "\n".join(parts)


# ─── SOURCE PLACEHOLDER HELPER ────────────────────────────────────────────────

def source_placeholder(name="", url="", date="", fresh=""):
    """
    Render a source attribution block.
    All fields are optional — fill them in when real data is connected.

    FUTURE — SOURCE CONNECTIONS
    When a live news API provides article metadata, pass the values here:
      source_placeholder(name="WSJ", url="https://...", date="2026-06-08", fresh="today")
    """
    value = name if name else "— pending"
    return (f'<div class="source-placeholder">'
            f'<span class="source-label">Source</span>'
            f'<span class="source-value">{value}</span>'
            f'</div>')


# ─── CLAUDE AI CONTENT ────────────────────────────────────────────────────────

def build_source_text(company_news, general_news, macro_rows):
    """Flatten all fetched data into plain text for Claude's prompt."""
    chunks = ["=== COMPANY NEWS (watchlist) ==="]
    for sym, articles in company_news.items():
        if not articles:
            continue
        chunks.append(f"\n## {sym} — {TICKER_NAMES.get(sym, '')}")
        for a in articles:
            hl  = a.get("headline", "").strip()
            sm  = a.get("summary", "").strip()
            src = a.get("source", "")
            chunks.append(f"- [{src}] {hl}\n  {sm}")

    chunks.append("\n=== GENERAL MARKET NEWS ===")
    for a in general_news:
        chunks.append(f"- [{a.get('source','')}] {a.get('headline','').strip()}")

    chunks.append("\n=== ECONOMIC INDICATORS ===")
    for series_id, label, latest, prior, date in macro_rows:
        delta = ""
        if prior is not None:
            diff  = latest - prior
            arrow = "up" if diff > 0 else ("down" if diff < 0 else "flat")
            delta = f" ({arrow} {abs(diff):.2f} from prior)"
        chunks.append(f"- {label}: {latest}{delta}  [{date}]")

    return "\n".join(chunks)


def write_claude_content(company_news, general_news, macro_rows):
    """
    Single Claude call that generates both the Market Mood card and the
    main brief (signal cards + ticker cards + macro read).

    Returns (mood_html, brief_html). Falls back to ("", "") on failure.

    FUTURE — PROMPT TUNING
    Edit the prompt below to adjust tone, add portfolio context,
    risk tolerance, sector focus, or time horizon.
    """
    source_text  = build_source_text(company_news, general_news, macro_rows)
    ticker_names = "\n".join(f"- {sym}: {name}" for sym, name in TICKER_NAMES.items()
                             if sym in TICKERS)

    prompt = f"""You are a private markets research assistant generating a structured daily brief for a retail investor. Use only research-focused language. Never use "buy", "sell", or direct trading instructions. Instead use phrases like: "research candidate", "worth monitoring", "possible setup", "risk to verify", "needs confirmation", "worth watching".

OUTPUT STRUCTURE — output exactly two XML sections, no other text:

<mood>
[MOOD CARD HTML — see format below]
</mood>

<brief>
[BRIEF HTML — see format below]
</brief>

━━━ MOOD CARD FORMAT ━━━
One div with class "mood-card" plus ONE of: mood-bullish, mood-cautious, mood-mixed, mood-risk-off, mood-neutral.
Inside it:
<div class="mood-card mood-[label]">
  <div class="mood-top">
    <div class="mood-label-group">
      <span class="mood-eyebrow">Market Mood</span>
      <span class="mood-indicator">[Bullish|Cautious|Mixed|Risk-Off|Neutral]</span>
    </div>
    <span class="mood-disclaimer">Research only — not investment advice</span>
  </div>
  <div class="mood-summary">[One sentence capturing today's overall tone]</div>
  <ul class="mood-drivers">
    <li class="mood-driver"><span class="driver-dot"></span>[Driver 1]</li>
    <li class="mood-driver"><span class="driver-dot"></span>[Driver 2]</li>
    <li class="mood-driver"><span class="driver-dot"></span>[Driver 3]</li>
  </ul>
</div>

━━━ BRIEF FORMAT ━━━
Section 1 — Signal cards. Use this exact wrapper and card structure:
<div class="section-label">What Matters Most Today</div>
<div class="signal-grid">
  [3–5 signal-card divs, one per major development]
</div>

Each signal card:
<div class="signal-card">
  <div class="signal-meta">
    <span class="impact-badge impact-[high|medium|low]">[High|Medium|Low] Impact</span>
    <div class="signal-cats">
      <span class="signal-cat">[Category]</span>
      [more signal-cat spans as needed — use: Rates, Tech, Semis, Oil, Economy, Watchlist, Macro, IPO]
    </div>
  </div>
  <div class="signal-headline">[Short punchy headline]</div>
  <div class="signal-explanation">[2–3 sentences. Research language only. No trading instructions.]</div>
  <div class="source-placeholder"><span class="source-label">Source</span><span class="source-value">— pending</span></div>
</div>

Section 2 — Ticker analysis cards. ONLY include tickers with material, actionable information. Skip tickers with no relevant news — do not include a "nothing to report" card.
<div class="section-label">Ticker Watch</div>
<div class="ticker-analysis-grid">
  [ticker-card divs]
</div>

Each ticker card — use tone-bullish, tone-bearish, tone-mixed, tone-neutral, or tone-watch:
<div class="ticker-card tone-[label]">
  <div class="ticker-card-header">
    <div class="ticker-card-id">
      <span class="ticker">[SYMBOL]</span>
      <span class="ticker-fullname">[Full company name]</span>
    </div>
    <span class="tone-badge tone-[label]">[Bullish|Bearish|Mixed|Neutral|Watch]</span>
  </div>
  <div class="ticker-card-body">
    <div class="ticker-why">[1 sentence: why this ticker matters today]</div>
    <div class="ticker-factors">
      <div class="ticker-factor">
        <span class="factor-label bull-label">Possible Setup</span>
        <span class="factor-text">[Bullish angle or potential upside factor]</span>
      </div>
      <div class="ticker-factor">
        <span class="factor-label bear-label">Risk to Verify</span>
        <span class="factor-text">[Bearish angle or downside risk]</span>
      </div>
      <div class="ticker-factor">
        <span class="factor-label verify-label">Worth Monitoring</span>
        <span class="factor-text">[What to watch next — catalyst, confirmation, data point]</span>
      </div>
    </div>
    <div class="ticker-source"><div class="source-placeholder"><span class="source-label">Source</span><span class="source-value">— pending</span></div></div>
  </div>
</div>

Section 3 — Macro read:
<div class="section-label">Macro Read</div>
<div class="macro-read"><p>[2–3 sentences tying economic indicators together. Research tone only.]</p></div>

━━━ TICKER NAMES ━━━
{ticker_names}

━━━ RAW DATA ━━━
{source_text}
"""

    resp = claude_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=3500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(b.text for b in resp.content if b.type == "text").strip()
    raw = re.sub(r"^```(?:html)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()

    mood_match  = re.search(r'<mood>(.*?)</mood>',   raw, re.DOTALL)
    brief_match = re.search(r'<brief>(.*?)</brief>', raw, re.DOTALL)

    mood_html  = mood_match.group(1).strip()  if mood_match  else ""
    brief_html = brief_match.group(1).strip() if brief_match else raw

    return mood_html, brief_html


# ─── PAGE RENDERER ────────────────────────────────────────────────────────────

def render_page(macro_rows, mood_html, brief_html, news_html):
    """
    Slot all components into template.html.

    Placeholders:
      {{DATE}}      — formatted date (ET)
      {{GENERATED}} — generation time (ET)
      {{MOOD}}      — Market Mood card (Claude)
      {{MACRO}}     — Economic indicator cards (Python)
      {{BRIEF}}     — Signal cards + Ticker cards + Macro read (Claude)
      {{NEWS}}      — Raw news feed (Python)
    """
    now_et   = datetime.datetime.now(ET)
    date_str = now_et.strftime("%A, %B %-d, %Y")
    gen_str  = now_et.strftime("%-I:%M %p ET")

    with open("template.html", "r", encoding="utf-8") as f:
        template = f.read()

    return (
        template
        .replace("{{DATE}}",      date_str)
        .replace("{{GENERATED}}", gen_str)
        .replace("{{MOOD}}",      mood_html)
        .replace("{{MACRO}}",     render_macro_grid(macro_rows))
        .replace("{{BRIEF}}",     brief_html)
        .replace("{{NEWS}}",      news_html)
    )


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print("Gathering sources...")
    company_news, general_news, macro_rows = fetch_all()

    # FUTURE — X ACCOUNT TRACKING
    # Fetch curated X posts and merge into company_news / general_news
    # before calling write_claude_content().

    # FUTURE — LIVE PRICE DATA
    # Call a price API here and pass prices into write_claude_content()
    # so Claude can reference actual % moves in its analysis.

    print("Asking Claude to write the brief...")
    mood_html, brief_html = write_claude_content(company_news, general_news, macro_rows)

    print("Rendering news feed...")
    news_html = render_news_feed(company_news, general_news)

    print("Rendering page...")
    page = render_page(macro_rows, mood_html, brief_html, news_html)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(page)

    print("Done → index.html")


if __name__ == "__main__":
    main()
