# Morning Brief

A self-updating web page that pulls news for your watchlist plus key economic
indicators every weekday morning, has Claude distill it into a no-fluff brief,
and publishes it — all for free (aside from a few cents/day in API usage).

You don't run anything on your computer. GitHub stores the code, runs it on a
timer, and hosts the page.

---

## What you'll set up (about 30 minutes, once)

You need three free API keys and a GitHub account. **Claude Code can walk you
through every step below — just point it at this folder and say "help me set
this up."**

### Step 1 — Get your three keys

| Service | What it's for | Where | Free tier |
|---|---|---|---|
| **Finnhub** | Stock news | finnhub.io | Yes — generous free tier |
| **FRED** | Economic data | fred.stlouisfed.org/docs/api/api_key.html | Yes — fully free |
| **Anthropic** | Claude (writes the brief) | console.anthropic.com | Pay-as-you-go; ~pennies/day at one brief/day |

Sign up for each, generate an API key, and keep the three keys handy.
(You sign up for these yourself — never share keys with anyone, and never paste
them into the code.)

### Step 2 — Put the project on GitHub

1. Create a free account at github.com.
2. Create a **new repository** (name it whatever — e.g. `morning-brief`).
3. Upload these files to it (Claude Code can do this with `git`):
   `build_brief.py`, `template.html`, `requirements.txt`,
   and the `.github/workflows/daily-brief.yml` file.

### Step 3 — Add your keys as Secrets (keeps them private)

In your repo: **Settings → Secrets and variables → Actions → New repository
secret.** Add three secrets with these exact names:

- `FINNHUB_API_KEY`
- `FRED_API_KEY`
- `ANTHROPIC_API_KEY`

Secrets are encrypted. They never appear in the code or on the page.

### Step 4 — Set your watchlist

Open `build_brief.py` and edit the `TICKERS` list near the top to the stocks you
follow. Optionally tweak `FRED_SERIES` for the economic numbers you care about.

### Step 5 — Turn on the web page

In your repo: **Settings → Pages → Build and deployment → Source: Deploy from a
branch → Branch: `main` / root.** Your page will live at
`https://YOUR-USERNAME.github.io/morning-brief/`.

### Step 6 — Test it now

Go to the **Actions** tab → **Daily Brief** → **Run workflow**. Wait ~1 minute,
then open your Pages URL. You should see today's brief. From now on it rebuilds
itself every weekday morning.

---

## Notes

- **Timing:** the schedule in `daily-brief.yml` is in UTC. `0 11 * * 1-5` is
  ~6–7am Eastern. Change the `11` to shift it. (GitHub's free scheduler can be a
  few minutes late during busy periods — fine for a morning read.)

- **Privacy:** a free GitHub Pages site is public at its URL. The page content
  and your ticker list would be viewable by anyone who has the link. Your API
  keys stay private regardless (they're encrypted secrets). If you want the page
  itself private, make the repo private and use GitHub Pro (~$4/mo), or we can
  switch to a private delivery method later.

- **Cost control:** the brief is one Claude API call per day. To cut cost,
  change `MODEL` in `build_brief.py` to `claude-haiku-4-5-20251001`. For sharper
  synthesis, use `claude-opus-4-8`.

- **This is v1.** Easy upgrades once it's running: add sentiment scoring, pull
  from more news sources, add a price snapshot, or email yourself a copy.
