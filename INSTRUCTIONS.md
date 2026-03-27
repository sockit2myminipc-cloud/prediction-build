# How to Use — Prediction Market Engine

Step-by-step guide for **PowerShell on Windows**. All commands below are written for PowerShell unless noted.

---

## PowerShell Quick-Start Checklist

1. Set execution policy (one-time)
2. Create and activate a virtual environment
3. Install dependencies
4. Configure API keys
5. Run the engine (Terminal 1)
6. Run the dashboard (Terminal 2)
7. Open browser at http://localhost:8090
8. *(Optional)* Pull historical data: `python -m backtest.historical_fetcher`
9. *(Optional)* Run backtest engine: `python -m backtest.engine`

---

## Step 0 — PowerShell Execution Policy (one-time, run as Administrator)

By default, PowerShell blocks activation scripts for virtual environments.
Open PowerShell **as Administrator** and run:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

You only need to do this once per machine. Close the admin window when done.

---

## Step 1 — Create a Virtual Environment

Requires **Python 3.11+**. Check your version first:

```powershell
python --version
```

Navigate to the project and create the venv:

```powershell
cd "C:\Users\MiniPC\Projects\Prediction Build"
python -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

Your prompt will change to show `(.venv)` when active. **You must activate the venv in every new terminal window before running any project commands.**

> **Gotcha:** If you get `cannot be loaded because running scripts is disabled`, run Step 0 first.

---

## Step 2 — Install Dependencies

With the venv active:

```powershell
pip install -r requirements.txt
```

This installs all packages including `pytest`, `httpx`, `fastapi`, `uvicorn`, and everything else the engine needs.

If pip itself is outdated:

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

---

## Step 3 — Verify the Installation (Recommended)

Before configuring API keys, confirm everything installed and works:

```powershell
python -m pytest tests/ -v
```

Expected output: **85 passed** in ~2 seconds. No API keys needed — all tests use an isolated SQLite file.

> **Gotcha:** Always run `pytest` as `python -m pytest`, not just `pytest`, to ensure it uses the venv's Python.

> **Gotcha:** If you see `UNIQUE constraint failed` errors, delete the temp test DB:
> ```powershell
> Remove-Item "$env:TEMP\prediction_test_smoke.db" -ErrorAction SilentlyContinue
> ```

---

## Step 4 — Configure API Keys

Copy the example env file:

```powershell
Copy-Item .env.example .env
```

Then open `.env` in any text editor and fill in:

```
NEWSAPI_KEY=your_key_here          # free at newsapi.org
ODDS_API_KEY=your_key_here         # free at the-odds-api.com (500 req/month)
FRED_API_KEY=your_key_here         # free at fred.stlouisfed.org/api/
```

**Minimum viable setup (no keys at all):** The engine still runs using RSS feeds, Reddit, and placeholder baselines. You'll still get velocity signals and cross-platform arb. NewsAPI and Odds API data will be skipped.

**Ollama (optional, for better NLP entity extraction):**
- Install Ollama from [ollama.com](https://ollama.com)
- Pull the required model: `ollama pull qwen2.5:7b`
- The engine **automatically starts Ollama** at startup if it isn't already running — no manual `ollama serve` needed
- If Ollama is not installed or fails to start, it falls back to regex keyword extraction gracefully
- To start manually: `ollama serve`
- Model used: `qwen2.5:7b` (configurable via `OLLAMA_MODEL` in `.env`)

**Check your keys loaded correctly:**

```powershell
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print(os.getenv('NEWSAPI_KEY','NOT SET')[:8]+'...')"
```

---

## Step 5 — Run the Engine and Dashboard

Open **two separate PowerShell windows**. Activate the venv in each.

### Terminal 1 — Polling Engine

```powershell
cd "C:\Users\MiniPC\Projects\Prediction Build"
.\.venv\Scripts\Activate.ps1
python -m engines.prediction_markets.main
```

What happens on startup:
1. `data/prediction_markets.db` is created automatically
2. All database tables are created (14 tables)
3. Default strategy version is seeded
4. Statistical baselines are seeded for key themes
5. First full market fetch begins immediately — all categories (22k+ Polymarket + Kalshi)
6. All 8 signal detectors run on the first cycle
7. Subsequent cycles run every 10 minutes

You will see log output like:
```
INFO  | Prediction market engine starting
INFO  | Kalshi API base: https://trading.kalshi.com/trade-api/v2
INFO  | dedup dropped 12 markets: [...]
INFO  | Outlier alerts: 3
INFO  | Velocity movers: 5
INFO  | New signal related_divergence: 153 alerts
INFO  | New signal scheduled_proximity: 2 alerts
INFO  | New signal thin_liquidity: 14 alerts
INFO  | New signal cross_category_momentum: 1186 alerts
```

### Terminal 2 — Dashboard

```powershell
cd "C:\Users\MiniPC\Projects\Prediction Build"
.\.venv\Scripts\Activate.ps1
python -m uvicorn shared.dashboard.app:app --host 0.0.0.0 --port 8090
```

Then open your browser at: **http://localhost:8090**

The dashboard shows a full HTML UI with all signals, opportunities, bets, markets, and news. It auto-refreshes every 60 seconds.

> **Gotcha:** The dashboard reads from the runtime state snapshot, which is only populated after the first cycle completes. Wait 2–5 minutes after starting the engine, then refresh.

---

## Step 5b — Timed Testing and Log Capture

The engine supports two optional CLI flags for automated testing and log review.

### `--duration MINUTES`

Run the engine for a fixed number of minutes, then exit cleanly with code 0. Use this to verify the engine works end-to-end without babysitting it.

```powershell
python -m engines.prediction_markets.main --duration 10
```

### `--log-file PATH`

Redirect all log output to a file **in addition to** stdout. The directory is created automatically.

```powershell
python -m engines.prediction_markets.main --log-file logs/test.log
```

### Combined — timed test with log capture

```powershell
python -m engines.prediction_markets.main --duration 10 --log-file logs/test.log
```

After it exits, review the log:

```powershell
Get-Content logs\test.log
```

### What to look for in the log

| Pattern | Meaning |
|---|---|
| `Running in timed mode: will stop after X minutes` | Timed mode active ✓ |
| `Timed run complete. Exiting cleanly.` | Clean exit ✓ |
| `Kalshi probe ... failed` | DNS fallback (normal — tries `elections.kalshi.com` next) |
| `Ollama already running at http://localhost:11434` | Ollama was running before startup ✓ |
| `Ollama started automatically (took Xs)` | Engine started Ollama automatically ✓ |
| `Ollama model 'qwen2.5:7b' confirmed available` | Model found and ready ✓ |
| `Ollama not found — using regex fallback` | Ollama not installed; regex extraction used (OK) |
| `Ollama entity response was not a JSON object` | Ollama responded but output wasn't valid JSON; regex fallback used (OK) |
| `Traceback` or `Error` | Something needs fixing |
| `DetachedInstanceError` or `AttributeError` | ORM session bug — report |

---

## Step 6 — Dashboard Endpoints

| URL | What You'll See |
|---|---|
| `/` | Full HTML dashboard — all data in one page, auto-refreshes |
| `/pm/health` | System status: market counts, last poll time, fetch times per source |
| `/pm/overview` | Paper bet summary: win rate, PnL, ROI, best category/signal |
| `/pm/opportunities` | Current top-10 ranked opportunity queue (all signal types combined) |
| `/pm/signals` | Opportunities broken down by all 8 signal types: outlier / velocity / arbitrage / news_lag / related_divergence / scheduled_proximity / thin_liquidity / cross_category_momentum |
| `/pm/backtest` | Backtest results: hit rate, EV, ROI, Sharpe, best Kelly fraction, best entry window, top signal combos |
| `/pm/calibration` | Calibration analysis: probability bucket vs actual resolution rate, most over/underpriced buckets |
| `/pm/arb` | Arbitrage signals only (require manual review — never auto-bet) |
| `/pm/bets?days=30` | Resolved paper bets (filterable by `category` and `days`) |
| `/pm/markets?theme=fed_macro` | All tracked active markets with current probability + 24h delta |
| `/pm/news` | Last 20 news articles with matched markets and relevance scores |
| `/pm/stats` | Per-signal-type and per-category paper bet performance breakdown |
| `/docs` | Auto-generated FastAPI interactive API docs |

---

## Step 7 — Check System Health

Visit **http://localhost:8090/pm/health** or run in PowerShell:

```powershell
Invoke-WebRequest -Uri http://localhost:8090/pm/health | Select-Object -ExpandProperty Content
```

Expected response:
```json
{
  "markets_tracked": 312,
  "ok_markets": 298,
  "stale_markets": 14,
  "last_poll_time": "2026-03-25T14:30:00+00:00",
  "is_ready": true,
  "news_articles_today": 47,
  "open_paper_bets": 0,
  "alerts_last_24h": 8,
  "fetch_times": {
    "polymarket": "14:28:05",
    "kalshi": "14:28:07",
    "news": "14:28:12"
  }
}
```

`is_ready: true` and `markets_tracked > 0` means the engine is working.

---

## Step 8 — View Opportunities

Visit **http://localhost:8090/pm/opportunities** or the HTML dashboard at **http://localhost:8090**

The top-10 ranked opportunities include:

```
signal_score    — raw edge strength (45% of final score)
execution_score — liquidity + spread + freshness (30%)
trust_score     — strategy track record in this category (25%)
final_score     — composite + resolution_boost. >0.6 worth reviewing. >0.7 qualifies for auto-bet.
ev_estimate     — expected value as a fraction (0.12 = 12% edge)
```

### How markets are filtered before ranking

Backtest analysis found that of 41,434 markets in the DB, only 883 (2.1%) have both meaningful liquidity and price movement. The engine applies a 4-rule quality filter in each cycle before signal detection:

| Rule | Threshold | Effect |
|---|---|---|
| Liquidity/volume | `liquidity >= $5k OR volume_24h >= $500` | Drops illiquid markets where fills are unreliable |
| Price movement | `MAX - MIN probability >= 3%` across all readings | Drops flat markets with no historical edge |
| Resolution boost | 7–30 days → +0.15; 0–7 days → +0.05 | Scores markets resolving soon higher (not a hard cutoff) |
| Category inference | Keyword-match on question text | Fixes NULL category for 96% of Polymarket markets |

Markets are also pre-filtered at **fetch time** (before the DB) — only markets with `liquidity >= $1k OR volume >= $1k` are stored. This prevents the DB from bloating with worthless markets that will never pass the ranking filter.

> **What this means for `markets_tracked`:** The number in `/pm/health` will be lower after this filter is applied. That is expected and correct — 300 active, liquid markets is more useful than 22,000 mostly-dead ones.

For signal breakdown by type:
```
GET /pm/signals
```

For arbitrage-only (requires manual execution on two platforms):
```
GET /pm/arb
```

---

## Step 9 — Enable Automatic Paper Betting

In `.env`, set:
```
AUTO_PAPER_BET=true
```

Restart the engine. Auto-betting fires when **all three** conditions are met:
- `final_score > 0.7`
- `trust_score > 0.35`
- `ev_estimate > 0.08` (8% edge minimum)

Arbitrage signals are **never** auto-bet (they require manual execution on two platforms simultaneously).
Paper bets use stake = 1.0 unit. PnL is measured in units, not dollars.

---

## Step 10 — Monitor Paper Bet Performance

Visit `/pm/overview` for totals or `/pm/stats` for per-signal breakdown:

```powershell
Invoke-WebRequest -Uri http://localhost:8090/pm/stats | Select-Object -ExpandProperty Content
```

For detailed bet log:
```powershell
Invoke-WebRequest -Uri "http://localhost:8090/pm/bets?category=fed_macro&days=90" | Select-Object -ExpandProperty Content
```

**When to trust the data:**
- 10+ resolved bets in a category: trust score updates from neutral (0.40)
- 30+ resolved bets: statistically meaningful
- 90+ days: long enough to see through variance

Do not transition to real money until you have 90+ days and positive EV on 2+ signal types.

---

## Step 11 — Inspect the Database Directly

The SQLite database lives at `data\prediction_markets.db`.

In PowerShell (if sqlite3 is installed):
```powershell
sqlite3 data\prediction_markets.db
```

Or use any SQLite GUI (e.g., DB Browser for SQLite).

Useful queries:

```sql
-- How many markets are tracked?
SELECT platform, COUNT(*) FROM markets GROUP BY platform;

-- Latest probability readings
SELECT m.question, pr.probability, pr.timestamp
FROM probability_readings pr
JOIN markets m ON m.id = pr.market_id
ORDER BY pr.timestamp DESC
LIMIT 20;

-- All open paper bets
SELECT question, bet_direction, probability_at_bet, trigger_type, placed_at
FROM paper_bets WHERE outcome = 'PENDING';

-- Win rate by signal type
SELECT trigger_type, COUNT(*) total,
       SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) wins
FROM paper_bets
WHERE outcome IN ('WIN', 'LOSS')
GROUP BY trigger_type;

-- Recent news articles
SELECT headline, source, fetched_at FROM news_articles ORDER BY fetched_at DESC LIMIT 10;

-- Signal events log
SELECT signal_type, final_score, ev_estimate, fired_at
FROM signal_events ORDER BY fired_at DESC LIMIT 20;
```

---

## Running Both Processes Together (PowerShell shortcut)

You can start both in one step using PowerShell's `Start-Process` to open a second window:

**Window 1 — Engine:**
```powershell
cd "C:\Users\MiniPC\Projects\Prediction Build"
.\.venv\Scripts\Activate.ps1
python -m engines.prediction_markets.main
```

**Window 2 — Dashboard (open separately):**
```powershell
cd "C:\Users\MiniPC\Projects\Prediction Build"
.\.venv\Scripts\Activate.ps1
python -m uvicorn shared.dashboard.app:app --host 0.0.0.0 --port 8090
```

Or launch the dashboard in a new window automatically from Window 1:
```powershell
Start-Process powershell -ArgumentList '-NoExit', '-Command', 'cd "C:\Users\MiniPC\Projects\Prediction Build"; .\.venv\Scripts\Activate.ps1; python -m uvicorn shared.dashboard.app:app --host 0.0.0.0 --port 8090'
```

---

## Common PowerShell Gotchas

| Problem | Fix |
|---|---|
| `cannot be loaded because running scripts is disabled` | Run `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` as Administrator (Step 0) |
| `python` not found | Install Python from python.org; check "Add to PATH" during install |
| `ModuleNotFoundError` | Make sure the venv is activated (`.\.venv\Scripts\Activate.ps1`). The prompt must show `(.venv)`. |
| `python -m pytest` not found | Run `pip install pytest httpx` inside the activated venv |
| `Address already in use` on port 8090 | Kill the old process: `Stop-Process -Id (Get-NetTCPConnection -LocalPort 8090).OwningProcess` |
| Path separators | PowerShell accepts both `/` and `\`. Use `\` for Windows paths in most contexts. |
| `pip` upgrading itself | Run `python -m pip install --upgrade pip` if you see pip upgrade warnings |
| Dashboard shows empty data | The engine must run first. Wait 2–5 minutes for the first cycle to complete. |
| `.env` not loading | Make sure `.env` is in the project root, not inside `engines/`. Run `dir .env` to confirm. |
| `UNIQUE constraint failed` in tests | Delete the stale test DB: `Remove-Item "$env:TEMP\prediction_test_smoke.db"` |

---

## Folder Layout After First Run

```
Prediction Build/
├── .env                          ← your keys (created from .env.example)
├── .venv/                        ← virtual environment (created in Step 1)
├── data/
│   ├── prediction_markets.db     ← SQLite database (auto-created)
│   ├── kalshi_cache.json         ← Kalshi API cache (auto-created)
│   └── polymarket_cache.json     ← Polymarket API cache (auto-created)
└── ... (all source files unchanged)
```

---

## Troubleshooting

### Smoke tests fail

- Run `pip install -r requirements.txt` inside the activated venv
- Tests must run from the **project root**, not from inside `tests/`
- Use `python -m pytest tests/ -v`, not just `pytest tests/ -v`

### Engine starts but no markets appear

- Check internet connection — Polymarket and Kalshi APIs must be reachable
- Look for `WARNING` or `CRITICAL` lines in log output
- If Kalshi is down, markets still come from Polymarket
- Cache files in `data/` cover up to 4 hours during outages

### No opportunities showing

- Opportunities only appear after the first cycle completes (1–3 min)
- Velocity signals need ≥ 2 readings per market (available after the 2nd cycle, ~10 min)
- News lag signals need both new articles AND markets that haven't moved

### Dashboard shows empty data

Opportunities are only populated after `run_cycle()` completes. Wait 2–5 minutes, then refresh.

### Ollama entity extraction not working

- The engine attempts to start Ollama automatically; check the log for `Ollama started automatically` or `Ollama not found`
- If Ollama isn't installed, install it from [ollama.com](https://ollama.com)
- Pull the required model if missing: `ollama pull qwen2.5:7b`
- To start Ollama manually: `ollama serve`
- Verify Ollama is running: `ollama list`
- Check `OLLAMA_BASE_URL` in `.env` — default is `http://localhost:11434`
- Engine always falls back to regex keyword extraction if Ollama is unavailable

### FRED API returns null fed baseline

Add your free key to `.env` as `FRED_API_KEY`. Without it, fed macro baseline defaults to 0.50 (neutral).

---

## What NOT to Do

- **Do not edit `data/prediction_markets.db` manually** while the engine is running — SQLite lock conflicts.
- **Do not delete `kalshi_cache.json` or `polymarket_cache.json`** during an API outage — they are the fallback.
- **Do not set `AUTO_PAPER_BET=true` on day one** — wait until the trust score has data behind it.
- **Do not enable live bet execution** (Phase 7) until you have 90+ days paper data with demonstrated positive EV.

---

## Step 12 — Pull Historical Data (Optional — for Backtesting)

Fetches resolved markets and price histories from both platforms (90-day window). Idempotent — safe to run repeatedly.

```powershell
python -m backtest.historical_fetcher
```

Expected output:
```
INFO  | Fetching Polymarket resolved markets (last 90 days)...
INFO  | Stored 142 Polymarket historical markets
INFO  | Fetching Kalshi finalized markets...
INFO  | Stored 67 Kalshi historical markets
INFO  | pull_history complete: {"polymarket": 142, "kalshi": 67, "total": 209}
```

---

## Step 13 — Run the Backtest Engine (Optional)

Replays signals against historical data and computes full strategy performance metrics.

```powershell
python -m backtest.engine
```

This runs all 7 backtest strategies (signal stacking, Kelly sizing, entry windows, calibration, momentum, portfolio cap, time-of-day) and writes results to the database. Results are then visible at `GET /pm/backtest` and `GET /pm/calibration`.

Expected output:
```
INFO  | Running backtest on N historical markets
INFO  | Backtest complete — stored X BacktestResult rows, Y CalibrationResult rows, Z MomentumResult rows
```

After running, visit `http://localhost:8090/pm/backtest` to see the results.

---

## Next Phases (Not Yet Built)

| Phase | What It Adds |
|---|---|
| **Phase 9** | Telegram alerts wired to `alert_service.py`. Daily digest at 8am. Rate-limited to max 10 alerts/day. |
| **Phase 9.5** | Strategy governance: champion/challenger framework, promotion decision tables. |
| **Phase 10** | Live bet execution via Polymarket and Kalshi APIs. Requires 90+ days validated paper data first. |

---

## Market Filtering Rules

The engine applies 4 rules before ranking opportunities, cutting 41,000+ tracked markets down to ~500–1,000 high-quality targets.

| Rule | Value | Effect |
|---|---|---|
| Minimum liquidity | $5,000 | Removes illiquid markets |
| Minimum 24h volume | $500 | Removes stale markets |
| Price movement required | ≥ 3% range in readings | Only tracks actively traded markets |
| Resolution window boost | 7–30 days → +0.15 score | Prioritizes near-term resolution |

### Why these numbers?

Backtesting on real data showed:
- Markets with < $5k liquidity produce noise signals with no tradeable edge
- Only 883 of 41,434 tracked markets pass the liquidity + price movement filters
- The **7–30 day entry window** produced the best Sharpe ratio (0.19) vs 0–7 days (0.14)
- **Politics markets** showed the worst ROI (-0.013) vs entertainment (ROI +3.26) and sports (ROI +1.77)

### Tuning the filters

Edit constants at the top of `engines/prediction_markets/betting/opportunity_ranker.py`:

```python
MIN_LIQUIDITY = 5_000       # $5k minimum liquidity
MIN_VOLUME_24H = 500        # $500 minimum 24h volume
MIN_PRICE_MOVEMENT = 0.03   # 3% price range required
```

### Keyword-based auto-tagging

Markets with no category are auto-tagged based on question text. See `engines/prediction_markets/filters/market_filter.py` for the full keyword map. Categories assigned:

- **crypto**: bitcoin, ethereum, solana, btc, eth, xrp, ripple, altcoin
- **sports**: nfl, nba, mlb, soccer, tennis, ufc, golf, championship, finals
- **politics/elections**: president, senate, election, vote, parliament, referendum
- **macro/finance**: fed, inflation, cpi, interest rate, gdp, recession, fomc
- **geopolitics**: war, ukraine, russia, china, taiwan, sanctions
- **entertainment**: oscar, grammy, award, box office

### Checking filter performance

Query the DB to see how many markets pass each stage:

```sql
-- Markets passing liquidity filter
SELECT COUNT(*) FROM markets WHERE liquidity >= 5000 OR volume_24h >= 500;

-- Markets with price movement
SELECT COUNT(DISTINCT market_id) FROM probability_readings
GROUP BY market_id HAVING MAX(probability) - MIN(probability) >= 0.03;
```
