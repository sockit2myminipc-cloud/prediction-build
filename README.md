# Prediction Market Research & Betting Engine

A standalone prediction market research and automated paper-betting engine that watches **Polymarket** and **Kalshi**, ingests live news, detects mispriced markets, and tracks paper bets with full performance analytics. Fully built through **Phases 1–8** of the build guide.

---

## What It Does

| Signal Type | How It Works |
|---|---|
| **News Lag** | News breaks → engine detects markets that haven't repriced yet → alert fired in the lag window |
| **Outlier Detection** | Compares market probability against statistical baselines (Vegas odds, FRED futures, etc.) → flags divergence beyond theme thresholds |
| **Velocity** | Tracks probability momentum over 2-hour windows → flags strong/accelerating movers |
| **Arbitrage** | Cross-platform price spread detection + correlated market math consistency checks |
| **Related Divergence** | Fuzzy-matches market pairs with similar questions; flags when probability gap > 25pp |
| **Scheduled Proximity** | Flags markets mentioning a hardcoded FOMC/election/sports event within 7 days; boosts EV if velocity also fires |
| **Thin Liquidity** | Polymarket markets with spread > 10pp AND liquidity < $5,000 — high-variance, marked low confidence |
| **Cross-Category Momentum** | Finds entities appearing in 3+ markets across 2+ categories — suggests broad repricing event |

All opportunities are ranked by a 3-component composite score and optionally auto-placed as paper bets.

---

## Project Structure

```
Prediction Build/
├── .env.example                        # API key template — copy to .env
├── requirements.txt                    # Python dependencies
├── backtest/                           # Historical data + backtesting engine (Phase 8)
│   ├── __init__.py
│   ├── historical_fetcher.py           # Pulls resolved markets from Polymarket + Kalshi
│   └── engine.py                       # Signal replay, metrics, Kelly sizing, calibration
├── data/
│   ├── prediction_markets.db           # SQLite database (auto-created, 14 tables)
│   ├── kalshi_cache.json               # Kalshi API response cache
│   └── polymarket_cache.json           # Polymarket API response cache
├── engines/
│   └── prediction_markets/
│       ├── main.py                     # Polling orchestrator — runs the full cycle every 10 min
│       ├── runtime_state.py            # Thread-safe in-memory snapshot for the dashboard
│       ├── clients/                    # External API clients (Polymarket, Kalshi, news, sports, macro)
│       ├── filters/                    # Market relevance scoring and deduplication
│       ├── tracking/                   # Probability time-series and DB query layer
│       ├── analysis/                   # Signal detection engines (incl. new_signals.py)
│       ├── betting/                    # Paper betting, opportunity ranking
│       ├── alerts/                     # Alert service (stub — interface defined)
│       └── storage/                    # SQLAlchemy models and session factory
├── logs/                               # Log files from timed runs
├── shared/
│   └── dashboard/
│       └── app.py                      # FastAPI REST dashboard (port 8090)
└── tests/
    └── test_smoke.py                   # 85-test suite (all passing)
```

---

## File-by-File Reference

### Root Files

| File | Purpose |
|---|---|
| `.env.example` | Template for all API keys and config. Copy to `.env` and fill in. |
| `requirements.txt` | All Python package dependencies with minimum versions. |
| `guide_extract.txt` | Text-extracted version of the build guide — the authoritative spec. |

---

### `engines/prediction_markets/main.py`

**The orchestrator.** Runs a full cycle every 10 minutes on a `schedule` loop.

Each cycle does, in order:
1. Seeds statistical baselines if the DB is empty
2. Fetches all active markets from Polymarket (all categories, 22k+ markets) + Kalshi
3. Normalizes and persists each market + a probability reading to the DB
4. Scores and filters markets (relevance ≥ 0.3)
5. Deduplicates cross-platform duplicates
6. Runs outlier detection against stored baselines
7. Runs arbitrage scanner (cross-platform + correlated)
8. Runs velocity scanner for momentum movers
9. Fetches + stores news articles; scores each against open markets for lag
10. **Runs 4 new signal detectors:** related_divergence, scheduled_proximity, thin_liquidity, cross_category_momentum
11. Ranks all opportunity types via `OpportunityRanker` (top 10, now with 8 signal types)
12. If `AUTO_PAPER_BET=true`, auto-places paper bets meeting score/EV/trust thresholds
13. Resolves any pending paper bets whose markets have settled
14. Writes a `HealthLog` row and pushes results to the runtime state snapshot

**Entry point:** `python -m engines.prediction_markets.main` or run via the module directly.

**CLI flags:**

| Flag | Description |
|---|---|
| `--duration MINUTES` | Run for this many minutes, then exit cleanly with code 0. Omit to run indefinitely. |
| `--log-file PATH` | Log to both stdout and this file simultaneously. Directory is created automatically. |

Example — timed test with log capture:
```bash
python main.py --duration 10 --log-file logs/test.log
```

---

### `engines/prediction_markets/runtime_state.py`

Thread-safe, in-memory store of the latest cycle results. Written by `main.py` after each cycle and read by the FastAPI dashboard without touching the database.

| Function | Purpose |
|---|---|
| `set_cycle_results(opportunities, health, alerts_delta)` | Store the ranked opportunity list + health dict after each cycle |
| `set_fetch_time(source, when)` | Record when each data source was last fetched |
| `snapshot()` | Return a safe copy of all current state for the dashboard |

---

### `engines/prediction_markets/clients/`

#### `polymarket_client.py` — `PolymarketClient`

Connects to the Polymarket Gamma API (`https://gamma-api.polymarket.com`). No authentication required.

| Method | Purpose |
|---|---|
| `get_all_active_markets(limit=500)` | Full paginated sweep across ALL categories (14 tag slugs + volume-sorted general sweep). Deduplicates by market id. Logs per-category counts. Writes local cache. |
| `get_markets_by_category(category_slug)` | Fetch all active markets for one category tag slug with offset pagination. |
| `get_tags()` | Fetches all available tag/category objects from `/tags` endpoint. |
| `get_markets_by_tag(tag_id, limit=100)` | Paginated fetch of active markets for one tag ID. |
| `get_all_markets_with_categories(limit_per_tag=100)` | Combines general sweep + per-tag sweep; attaches `category` from tag label. |
| `get_market_by_id(market_id)` | Fetches one market by ID — used during bet resolution. |
| `get_clob_spread(token_id)` | Fetches CLOB order book bid/ask spread for thin-liquidity detection. |
| `extract_probability(market_dict)` | Reads `outcomePrices[0]` (YES price) as a float. Returns `None` for multi-outcome markets. |

**Coverage:** Fetches 22,000+ active markets per cycle including politics, crypto, sports, science, entertainment, economics, tech, health, world, gaming.

**Cache:** Writes to `data/polymarket_cache.json`. On API failure, serves cache if < 4 hours old. Logs `CRITICAL` if cache is stale.

---

#### `kalshi_client.py` — `KalshiClient`

Connects to Kalshi's public trade API. Tries three base URLs in order (trading, elections, trading-api) and uses the first that responds.

| Method | Purpose |
|---|---|
| `get_all_markets(limit=2000, status='open')` | Full cursor-paginated sweep through all open markets (no artificial cap). Logs per-category counts after fetch. Writes cache. |
| `normalize_market(raw)` | Converts raw Kalshi dict to the standard internal format. Computes `yes_mid = (yes_bid + yes_ask) / 2`, sets `low_confidence=True` when spread > 10%. |

**Cache:** Writes to `data/kalshi_cache.json`. Same 4-hour staleness rules as Polymarket.

---

#### `newsapi_client.py` — `NewsIngester`

Ingests articles from three free news sources.

| Source | Frequency | Detail |
|---|---|---|
| NewsAPI | Every 15 min (via main cycle) | 5 queries across Fed, crypto, elections, sports, macro. Requires `NEWSAPI_KEY`. |
| RSS feeds | Every cycle | Reuters, BBC, NYT, ESPN, WSJ Markets via `feedparser`. |
| Reddit | Every cycle | Top posts from r/politics, r/CryptoCurrency, r/nfl, r/nba via public JSON API. |

| Method | Purpose |
|---|---|
| `run_cycle()` | Polls all three sources, deduplicates by URL, stores new `NewsArticle` rows. Returns `(new_count, new_article_ids)`. |
| `store_article(...)` | Inserts a `NewsArticle` row; skips if URL already in DB. |

---

#### `sports_client.py` — `SportsDataClient`

Fetches live sports betting odds from The Odds API (free tier: 500 req/month).

| Method | Purpose |
|---|---|
| `get_game_odds(sport, team_a, team_b)` | Fuzzy-matches team names and returns vig-removed win probabilities for both teams. |
| `convert_odds_to_probability(odds_a, odds_b)` | Converts decimal odds to vig-removed implied probabilities. |

Sports covered: NFL, NBA, MLB, EPL soccer. Requires `ODDS_API_KEY`.

---

#### `macro_client.py` — `MacroBaselineClient`

Fetches macro economic baselines and seeds the `StatisticalBaseline` table.

| Method | Purpose |
|---|---|
| `get_fed_cut_probability()` | Fetches FRED funds rate data and maps level to a soft cut probability heuristic. Requires `FRED_API_KEY`. |
| `seed_theme_baselines()` | On first run, inserts baseline rows for `fed_macro`, `sports_nfl`, `btc_price` themes so outlier detection has something to compare against. |

---

### `engines/prediction_markets/filters/`

#### `market_filter.py` — `MarketFilter`

Assigns a **theme** to each market and scores it for relevance.

**Themes and their keyword fingerprints** (17 total):
`sports_nfl`, `sports_nba`, `sports_mlb`, `sports_soccer`, `sports_combat`, `sports_golf`, `elections_us`, `elections_intl`, `btc_price`, `eth_events`, `other_crypto`, `fed_macro`, `cpi_macro`, `recession`, `geopolitics`, `tech_ai`, `entertainment`

| Method | Purpose |
|---|---|
| `assign_theme(question_text)` | Returns the first matching theme name, or `'other'`. |
| `score_market(market_dict)` | Scores 0–1: +0.3 if themed, +0.2 if volume > $10k, +0.2 if liquidity > $5k, +0.2 if ends ≥ 3 days away, +0.1 if not low-confidence. |
| `filter_markets(markets_list, min_score=0.3)` | Keeps only markets scoring ≥ `min_score`, sorted descending. |

---

#### `dedup.py` — `MarketDeduplicator`

Removes cross-platform duplicate markets so the same event doesn't score twice.

**Two dedup passes:**
1. **Fingerprint dedup:** 5 known recurring events (`fomc_rate`, `cpi_print`, `btc_price`, `us_election`, `recession`). When the same event appears on both platforms, keeps the preferred platform's version.
2. **Fuzzy dedup:** `rapidfuzz.token_set_ratio` at threshold 70 across all remaining cross-platform pairs. Keeps the higher-liquidity version.

`EVENT_FINGERPRINTS` also exported and used by `ArbitrageScanner` to identify cross-platform arb pairs.

---

### `engines/prediction_markets/tracking/`

#### `probability_tracker.py` — `ProbabilityTracker`

Manages the market and reading time-series in the database.

| Method | Purpose |
|---|---|
| `upsert_market_row(...)` | Creates or updates a `Market` row (all metadata fields). |
| `record_reading(market_db_id, probability, ...)` | Inserts a timestamped `ProbabilityReading` row. |
| `latest_probability(market_db_id)` | Returns the most recent probability for a market. |

Also exports `normalize_polymarket_row(raw, pm)` — converts a raw Polymarket API dict to the standard internal dict format.

---

#### `knowledge_base.py` — `KnowledgeBase`

High-level read-only query interface over the database. All write operations go through `ProbabilityTracker` or model inserts.

| Method | Purpose |
|---|---|
| `latest_reading(market_db_id)` | Most recent `ProbabilityReading` for a market. |
| `readings_since(market_db_id, since)` | All readings since a datetime — used by news lag detector. |
| `open_paper_bets()` | All `PaperBet` rows with `outcome='PENDING'`. |
| `count_news_today()` | Count of `NewsArticle` rows fetched since midnight UTC. |
| `recent_news(limit)`, `active_markets()`, `baselines_for_market(...)` | Convenience queries. |

---

### `engines/prediction_markets/analysis/`

#### `news_impact_scorer.py` — `NewsImpactScorer`

Detects news → market price lag windows.

| Method | Purpose |
|---|---|
| `ensure_ollama_running()` | Called at engine startup. Pings `localhost:11434`; if Ollama isn't running, launches `ollama serve` as a background process and waits up to 10s. Also verifies the configured model is available. Logs `"Ollama already running"`, `"Ollama started automatically (took Xs)"`, or `"Ollama not found — using regex fallback"`. |
| `extract_entities(article_text, headline)` | Calls Ollama (`qwen2.5:7b`) to extract structured entities (teams, politicians, events, numeric targets). Falls back to regex keyword extraction if Ollama unavailable. |
| `match_markets(entities, all_markets)` | Fuzzy-matches flattened entity strings against market questions. Returns markets with `relevance_score > 0.4`. |
| `detect_news_lag(article, matched_markets, readings_loader)` | For each matched market: checks if probability moved < 2% in last 30 min AND article is < 45 min old. If so, creates a lag alert. |
| `expected_direction(headline, question)` | Asks Ollama: "does this headline make the market outcome more likely?" → `YES/NO/unknown`. |
| `process_article(article_id, all_markets, readings_loader)` | Full pipeline: extract entities → match markets → save `NewsMarketLink` rows → detect lag. |

---

#### `outlier_detector.py` — `OutlierDetector`

Finds markets where the crowd probability diverges from external baselines beyond theme-specific thresholds.

**Thresholds by theme:**
- `sports_*`: 8% · `elections_*`: 10% · `fed_macro`: 5% · `cpi_macro`: 7% · `btc_price`: 10% · `recession`: 12% · `other`: 15%

| Method | Purpose |
|---|---|
| `find_outliers(session, markets)` | Compares each market against its best matching `StatisticalBaseline`. Creates `OutlierAlert` if divergence ≥ threshold AND EV > 5%. |
| `calculate_ev(market_prob, baseline_prob, bet_yes)` | EV formula using baseline as "true" probability. Returns edge as a fraction (e.g. 0.12 = 12% edge). |
| `theme_threshold(theme)` | Returns the divergence threshold for a theme. |

---

#### `velocity_scanner.py` — `VelocityScanner`

Detects probability momentum and acceleration.

| Method | Purpose |
|---|---|
| `get_velocity(market_db_id, window_hours=2)` | Computes `delta`, `velocity` (pts/hr), and `acceleration` from DB readings. Returns `quality='stale'` if < 2 readings. |
| `find_movers(markets, min_velocity=0.04, min_liquidity=5000)` | Filters to fast-moving markets above liquidity floor. Classifies each as `strong_bullish_move`, `mild_bullish_move`, `strong_bearish_move`, or `mild_bearish_move`. |
| `detect_velocity_opportunities(movers)` | Generates `VelocityAlert` dicts. EV based on historical 62% continuation rate for strong movers. Notes whether move is news-linked. |

---

#### `new_signals.py` — Four Additional Live Signal Detectors

| Function | Signal Type | How It Works |
|---|---|---|
| `find_related_divergence(markets, min_gap=0.25)` | `related_divergence` | rapidfuzz `token_set_ratio > 70` on question pairs. If abs(prob_a − prob_b) > 25pp, the underdog market is an opportunity. `ev_estimate = (gap − 0.05) × 0.5`. Caps search at 200 markets. |
| `find_scheduled_proximity(markets, velocity_market_ids, days_window=7)` | `scheduled_proximity` | 32 hardcoded FOMC/CPI/election/sports events. Keyword-matches market questions. Fires within 7 days of event. `ev_estimate = 0.14` if velocity also fires, `0.08` otherwise. |
| `find_thin_liquidity(markets)` | `thin_liquidity` | Polymarket only: liquidity < $5,000 AND (spread > 10% OR low_confidence). `ev_estimate = 0.05`, `confidence = "low"`. Never auto-bet. |
| `find_cross_category_momentum(markets)` | `cross_category_momentum` | Extracts title-cased words ≥ 4 chars (minus stop words) from all questions. If an entity appears in 3+ markets across 2+ categories, the most extreme-probability market fires. `ev_estimate = 0.06`. |

Also exports `SCHEDULED_EVENTS` — the full 32-entry list (FOMC 2025–2026, Super Bowl LIX/LX, NBA Finals, FIFA World Cup, CPI releases, US Midterms).

---

#### `arbitrage_scanner.py` — `ArbitrageScanner`

Three types of mathematical inconsistency detection.

| Type | Method | Description |
|---|---|---|
| Cross-platform | `find_cross_platform_arb(pm, kalshi)` | Same event on both platforms; flags spread > 5%. Uses `EVENT_FINGERPRINTS` from dedup. |
| Correlated | `find_correlated_arb(markets)` | BTC price ladder (P($120k) must not exceed P($100k)) + 3-way mutually exclusive election cluster sum > 1.0. |
| Implied chains | Built into `find_correlated_arb` | Structural math consistency across related markets. |

All arb alerts are passed to `OpportunityRanker` but **never auto-paper-bet** (manual execution only).

---

### `engines/prediction_markets/betting/`

#### `opportunity_ranker.py` — `OpportunityRanker` + `filter_markets`

Combines all signal types and ranks them by a **3-component composite score**.

**Score components:**
| Component | Weight | What it measures |
|---|---|---|
| `signal_score` | 45% | Raw edge strength: EV × 0.5 + signal-type bonus (divergence/relevance/velocity) |
| `execution_score` | 30% | Can we capture it: liquidity (log-normalized) + spread tightness + reading freshness |
| `trust_score` | 25% | Strategy track record in this category × signal type. Neutral 0.40 until 10+ resolved bets. |

**`final_score` = signal × 0.45 + execution × 0.30 + trust × 0.25 + resolution_boost**

Returns top 10 opportunities. Every opportunity is logged as a `SignalEvent` row (training data for future strategy improvement).

Also exports:
- `filter_markets(markets, session)` — see **Market Filtering Rules** section below
- `news_lag_to_opportunities(alerts)` — converts news lag alert dicts to standard opportunity format
- `time_sensitivity_score(opp)` — decays from 1.0 at 0 minutes to 0.0 at 60 minutes for news lag signals

---

#### `paper_bettor.py` — `PaperBettor`

Records, manages, and resolves paper bets.

| Method | Purpose |
|---|---|
| `place_paper_bet(market_db_id, direction, trigger_type, trigger_detail, stake=1.0)` | Looks up latest probability reading, computes implied odds, inserts a `PaperBet` row with `outcome='PENDING'`. |
| `check_resolutions()` | For all PENDING bets whose `end_date` has passed: fetches final probability via API. Resolves as WIN/LOSS/VOID (VOID if final prob is between 5%–95%). |
| `get_stats(category, signal_type, days=90)` | Returns win rate, total PnL, ROI%, max drawdown, best category, best signal type over the specified window. |

**Auto-bet rule** (when `AUTO_PAPER_BET=true` in `.env`):
- `final_score > 0.7` AND `trust_score > 0.35` AND `ev_estimate > 0.08`
- Never auto-bets arbitrage signals (manual review required)
- Never auto-bets low-confidence markets

---

#### `bet_executor.py` — `BetExecutor`

**Phase 7 placeholder.** Raises `NotImplementedError` for all live execution attempts. Do not implement until 90+ days of paper data with positive EV across ≥ 2 signal types.

---

### `engines/prediction_markets/alerts/alert_service.py` — `PMAlertService`

**Phase 6 stub.** All methods log debug messages only. Will be wired to Telegram in Phase 6. The interface is already defined:
- `send_opportunity_alert(opportunity)` — for high-scoring opportunities
- `send_news_lag_alert(news_lag)` — for active lag windows
- `send_daily_digest()` — 8am daily summary

---

### `engines/prediction_markets/storage/`

#### `models.py`

All SQLAlchemy ORM models. Tables auto-created by `init_db()`.

| Table | Model | Purpose |
|---|---|---|
| `markets` | `Market` | One row per platform × market_id. Stores question, theme, liquidity, volume, end_date, low_confidence flag. |
| `probability_readings` | `ProbabilityReading` | Time-series of probability snapshots. Indexed on `(market_id, timestamp)` and `(market_id, quality, timestamp)`. |
| `news_articles` | `NewsArticle` | Deduplicated news articles from all sources. Stores headline, body, URL (unique), entities JSON. |
| `news_market_links` | `NewsMarketLink` | Many-to-many link between articles and markets with `relevance_score`. |
| `paper_bets` | `PaperBet` | Full record of every paper bet placed, including trigger type, odds, outcome, PnL. |
| `statistical_baselines` | `StatisticalBaseline` | External probability baselines per market or theme. |
| `health_logs` | `HealthLog` | Per-cycle health snapshot: markets tracked, stale count, open bets, last signal score. |
| `strategy_versions` | `StrategyVersion` | Versioned strategy configs (for Phase 6.5 governance). Seeded with `default` champion on first run. |
| `strategy_performance_snapshots` | `StrategyPerformanceSnapshot` | Per-category × signal-type performance stats per strategy version. |
| `signal_events` | `SignalEvent` | Every opportunity that reached the ranker, with all 3 component scores — the training dataset. |

---

#### `db.py`

Session factory and database initialization.

| Function | Purpose |
|---|---|
| `init_db()` | Creates all tables + seeds the default champion `StrategyVersion` row. Safe to call multiple times. |
| `get_session()` | Returns a new SQLAlchemy `Session`. Caller must close it. |
| `database_url()` | Returns `DATABASE_URL` from env, or defaults to `data/prediction_markets.db`. |
| `get_engine()` | Creates a `create_engine()` instance with `check_same_thread=False` for SQLite. |

---

### `shared/dashboard/app.py`

FastAPI application running on port **8090**. Reads from the DB and the runtime state snapshot.

| Endpoint | Returns |
|---|---|
| `GET /` | **Full HTML dashboard** — all data in one readable page, auto-refreshes every 60 seconds |
| `GET /pm/health` | Markets tracked, ok/stale counts, last poll time, is_ready, open bets, alerts last 24h, per-source fetch times |
| `GET /pm/overview` | Total paper bets, overall win rate, best category, best signal type, total PnL, ROI% |
| `GET /pm/opportunities` | Current top-10 ranked opportunity queue with all 3 component scores (all signal types combined) |
| `GET /pm/signals` | Opportunities grouped by all 8 signal types: `outlier`, `velocity`, `arbitrage`, `news_lag`, `related_divergence`, `scheduled_proximity`, `thin_liquidity`, `cross_category_momentum` |
| `GET /pm/arb` | Arbitrage signals only — with note that these require manual review and are never auto-bet |
| `GET /pm/bets?category=X&days=30` | Resolved paper bets with direction, outcome, PnL — filterable by category and time window |
| `GET /pm/markets?theme=X` | All tracked active markets with current probability and 24h delta — filterable by theme |
| `GET /pm/news` | Last 20 news articles with their matched markets and relevance scores |
| `GET /pm/stats` | Per-signal-type and per-category paper bet performance breakdown (win rate, PnL, ROI) |
| `GET /pm/backtest` | Backtest results summary: hit rate, EV, ROI, Sharpe, top signal combinations, best Kelly fraction, best entry window |
| `GET /pm/calibration` | Calibration analysis: market probability bucket vs actual resolution rate, most over/underpriced buckets |
| `GET /docs` | Auto-generated FastAPI interactive API documentation |

The HTML dashboard at `/` displays all the above in a dark-themed, table-based UI with color-coded signal types, score bars, and status indicators. No external dependencies — served entirely inline.

---

### `backtest/`

#### `historical_fetcher.py`

Pulls resolved markets and their full price histories from both platforms. Idempotent — skips markets already in the DB.

| Function | Purpose |
|---|---|
| `pull_history(days_back=90) -> dict` | Main entry point. Fetches Polymarket resolved markets + CLOB price history, Kalshi finalized markets + candlestick history. Returns `{"polymarket": N, "kalshi": M, "total": N+M}`. |

**Polymarket flow:** Queries Gamma API for resolved markets. For each, fetches minute-level price history from CLOB (`/prices-history`). Stores as `HistoricalMarket` rows with `price_history` JSON.

**Kalshi flow:** Queries trade API for finalized markets in the 90-day window. For each, fetches OHLC candlestick data. Stores same `HistoricalMarket` shape.

**Idempotency:** Skips `condition_id`/`ticker` values already in the DB (`UniqueConstraint` on `source + external_id`). Safe to run repeatedly.

**Usage:**
```bash
python -m backtest.historical_fetcher
```

---

#### `engine.py`

Replays all 8 signal detection strategies against `HistoricalMarket` rows and computes full performance metrics.

| Function | Purpose |
|---|---|
| `run_backtest() -> dict` | Loads all historical markets, runs all strategies, persists results to DB, returns summary dict. |

**Strategies (5a–5g):**
| Strategy | Description |
|---|---|
| **5a Signal Stacking** | All combinations of signals firing within 24h of each other. Ranks combos by hit rate × count. |
| **5b Kelly Criterion** | Simulates `flat` / `full` / `half` / `quarter` Kelly fractions against historical P&L. Returns final bankroll and ROI for each. |
| **5c Entry Windows** | Groups resolved bets by days-before-resolution into 6 buckets: `30+`, `14-30`, `7-14`, `3-7`, `1-3`, `same-day`. |
| **5d Calibration** | Compares market probability at signal time vs actual outcome. Buckets 0–10%, 10–20%, …, 90–100%. Stored as `CalibrationResult` rows. |
| **5e Momentum** | Tracks price continuation vs mean reversion at 24h / 48h / 168h after a velocity spike. Stored as `MomentumResult` rows. |
| **5f Portfolio Cap** | (Wired into ranker) Limits correlated positions. |
| **5g Time-of-Day** | Per-hour (0–23) and per-weekday (0–6) hit rate breakdown stored in `BacktestResult` rows. |

**Core metrics computed:** `hit_rate`, `avg_edge`, `ev`, `roi`, `sharpe` (mean/std), `max_drawdown` (running peak).

**Usage:**
```bash
python -m backtest.engine
```

---

## Database

SQLite file at `data/prediction_markets.db`. Created automatically on first run. No setup required.

**To inspect:** `sqlite3 data/prediction_markets.db` then `.tables` to list all tables.

---

## Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `NEWSAPI_KEY` | Recommended | NewsAPI queries (100 free calls/day). Without it, only RSS + Reddit used. |
| `ODDS_API_KEY` | Optional | The Odds API for sports baselines (500 free req/month). Without it, sports outlier detection uses seeded placeholder baselines. |
| `FRED_API_KEY` | Optional | FRED macro data for Fed cut probability baseline. Without it, uses neutral 0.5. |
| `OLLAMA_BASE_URL` | Optional | Local Ollama endpoint for NLP entity extraction. Default: `http://localhost:11434`. |
| `OLLAMA_MODEL` | Optional | Ollama model for entity extraction and direction detection. Default: `qwen2.5:7b`. The engine starts Ollama automatically at startup and verifies this model is available. Pull it with `ollama pull qwen2.5:7b`. To start manually: `ollama serve`. |
| `AUTO_PAPER_BET` | Optional | Set to `true` to enable automatic paper betting. Default: `false`. |
| `DATABASE_URL` | Optional | Override default SQLite path. Example: `sqlite:///data/prediction_markets.db`. |

---

## Market Filtering Rules

Derived from backtest analysis of 41,434 markets — only 883 (2.1%) had meaningful liquidity **and** price movement. The filter runs in `run_cycle()` after deduplication, before all signal detectors.

### Why filtering matters
- **Category field is NULL for 96% of liquid markets** — API-provided categories cannot be trusted for filtering. Keyword inference is used instead.
- **41k markets in DB, 883 actionable** — without filtering, signal detectors waste computation on illiquid, static markets that never resolve in tradeable windows.
- The **3 filters that actually work** (confirmed by backtesting): liquidity threshold, price movement range, and time-to-resolution.

### Filter constants (`opportunity_ranker.py`)

| Constant | Value | Purpose |
|---|---|---|
| `MIN_LIQUIDITY` | `$5,000` | Minimum market liquidity — below this, fills are unreliable |
| `MIN_PRICE_MOVEMENT` | `3%` | MIN/MAX probability range across all stored readings — flat markets have no tradeable edge |
| `MIN_VOLUME_24H` | `$500` | Minimum 24h volume — alternative to liquidity (either threshold qualifies a market) |
| `MAX_DAYS_TO_RESOLUTION` | `30` | Reference for resolution-window boost (not a hard cutoff) |

### `filter_markets(markets, session)` — 4 rules applied in order

1. **Liquidity/volume gate:** Drop markets where `liquidity < $5k AND volume_24h < $500`. Both must fail to drop — a high-volume low-liquidity market still passes.
2. **Price movement gate:** Query `SELECT MAX(probability) - MIN(probability) FROM probability_readings WHERE market_id = ?`. Drop markets with < 3% range (no historical movement = no edge opportunity).
3. **Resolution-window boost:** Add to `final_score` — not a hard cutoff, so markets outside the window still rank, just lower:
   - 7–30 days to resolution → **+0.15**
   - 0–7 days to resolution → **+0.05**
   - >30 days or no end date → **+0.00**
4. **Keyword category inference:** When `category` is NULL/empty (96% of Polymarket markets), assign from question text:
   - `crypto` — bitcoin, ethereum, solana, crypto, btc, eth, sol, xrp, bnb
   - `sports` — win, match, game, player, team, nfl, nba, mlb, nhl, soccer, tennis, football
   - `politics` — president, election, congress, senate, vote, trump, biden, israel, iran, russia, ukraine
   - `finance` — stock, market, s&p, dow, nasdaq, fed, interest rate, gdp, recession
   - `weather` — temperature, °c, °f, weather, rain, snow, celsius
   - `other` — everything else

### Pre-filter at fetch time (DB bloat prevention)

Both clients apply a **broad pre-filter** before inserting into the DB — wider than the ranking filter to allow marginal markets to accumulate readings over time:

| Client | Pre-filter condition |
|---|---|
| `PolymarketClient.get_all_active_markets()` | `liquidity >= $1,000 OR volume_24h >= $1,000` |
| `KalshiClient.get_all_markets()` | `liquidity >= $1,000 OR volume >= $1,000` |

Both log how many markets were dropped: `"pre-filter: dropped N markets below $1k liquidity/volume"`.

---

## Phase Completion Status

| Phase | Description | Status |
|---|---|---|
| Phase 1 | Foundation & Data Layer | ✅ Complete |
| Phase 2 | News Ingestion & NLP | ✅ Complete |
| Phase 3 | Statistical Baselines & Outlier Detection | ✅ Complete |
| Phase 4 | Paper Betting System & Performance Tracking | ✅ Complete |
| Phase 5 | Velocity, Arbitrage & Correlation Scanners | ✅ Complete |
| Phase 6 | Full Market Category Coverage (all categories, 22k+ markets) | ✅ Complete |
| Phase 7 | Four New Live Signals (related_divergence, scheduled_proximity, thin_liquidity, cross_category_momentum) | ✅ Complete |
| Phase 8 | Historical Data Puller + Backtesting Engine (7 strategies: signal stacking, Kelly sizing, entry windows, calibration, momentum, portfolio cap, time-of-day) | ✅ Complete |
| Phase 9 | Telegram Alerts | Stub only — interface defined, not wired |
| Phase 9.5 | Strategy Governance & Learning Loop | Governance tables exist, logic not built |
| Phase 10 | Live Bet Execution | Placeholder only — requires 90+ days paper data |

---

## Testing

A full smoke-test suite lives in `tests/test_smoke.py` and covers:

- **Import checks** — all 21 modules import cleanly
- **DB initialization** — all 10 tables created, default strategy seeded
- **Filters** — `MarketFilter` theme assignment, scoring, filtering
- **Deduplication** — `MarketDeduplicator` fingerprint grouping and fuzz dedup
- **Outlier detection** — EV calculation, theme thresholds, DB-backed outlier search
- **Velocity scanner** — stale/fresh classification, move classification
- **Arbitrage scanner** — cross-platform spread detection, `arb_to_opportunity` shape
- **Opportunity ranker** — empty input, full scoring pipeline, rank assignment
- **Paper bettor** — place bet, get stats (empty/populated)
- **Knowledge base** — all query helpers
- **Probability tracker** — upsert + record reading + update path
- **Client unit tests** — Kalshi/Polymarket normalization, Odds probability conversion
- **FastAPI dashboard** — all endpoints via TestClient including `/pm/backtest` and `/pm/calibration`
- **New signal types** — `find_related_divergence`, `find_scheduled_proximity`, `find_thin_liquidity`, `find_cross_category_momentum` logic and output shape
- **Backtest module** — `pull_history` and `run_backtest` return shapes and DB writes
- **OpportunityRanker with extra signals** — `extra_alerts` parameter wiring and scoring

### Running the tests

```bash
# From the project root
pytest tests/ -v
```

Tests use an isolated SQLite file (`prediction_test_smoke.db` in the system temp dir) — **the production database is never touched**. No API keys are required.

**Prerequisites** (already in `requirements.txt`):
```bash
pip install pytest httpx
```

---

## Dependencies

See `requirements.txt`. Install with:

```bash
pip install -r requirements.txt
```

Key packages:
- `sqlalchemy` — ORM and database layer
- `fastapi` + `uvicorn` — REST dashboard
- `requests` + `feedparser` — HTTP + RSS ingestion
- `newsapi-python` — NewsAPI client
- `rapidfuzz` — Fuzzy string matching for dedup and market matching
- `loguru` — Structured logging
- `schedule` — Polling loop timing
- `pytest` + `httpx` — Test runner and FastAPI TestClient transport
- `python-dotenv` — `.env` file loading
