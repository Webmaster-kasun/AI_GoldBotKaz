# CPR Gold Bot — v5.0

Automated XAU/USD trading bot built on a Central Pivot Range (CPR) breakout
strategy. Runs every 5 minutes, applies layered execution guards, places orders
through the OANDA REST API, and reports every decision to Telegram.

---

## Table of contents

1. [Architecture overview](#1-architecture-overview)
2. [Project structure](#2-project-structure)
3. [Strategy design](#3-strategy-design)
4. [Signal scoring model](#4-signal-scoring-model)
5. [Position sizing](#5-position-sizing)
6. [Stop loss and take profit](#6-stop-loss-and-take-profit)
7. [Execution guard pipeline](#7-execution-guard-pipeline)
8. [Risk controls](#8-risk-controls)
9. [News filter](#9-news-filter)
10. [Trading sessions](#10-trading-sessions)
11. [State management and reconciliation](#11-state-management-and-reconciliation)
12. [Database and observability](#12-database-and-observability)
13. [Configuration reference](#13-configuration-reference)
14. [Secrets and environment variables](#14-secrets-and-environment-variables)
15. [Deployment (Railway)](#15-deployment-railway)
16. [Running locally](#16-running-locally)
17. [Performance analysis tool](#17-performance-analysis-tool)
18. [Changelog](#18-changelog)

---

## 1. Architecture overview

```
scheduler.py          <- APScheduler: fires run_bot_cycle() every 5 min
    └── bot.py        <- Main cycle orchestrator
          ├── signals.py            <- CPR scoring engine (reads OANDA candles)
          ├── oanda_trader.py       <- OANDA REST API layer
          ├── news_filter.py        <- Economic calendar guard
          ├── calendar_fetcher.py   <- Forex Factory calendar sync
          ├── reconcile_state.py    <- Broker-state reconciliation
          ├── database.py           <- SQLite observability store
          ├── telegram_alert.py     <- Telegram sender
          └── telegram_templates.py <- All message strings
```

Key design decisions:

- **Broker is source of truth.** On every cycle `reconcile_state` checks open trades at OANDA and recovers any missing from local history.
- **Atomic file writes.** All JSON state files are written via a temp-file rename to prevent corruption on crash.
- **One concurrent position.** `max_concurrent_trades = 1` by default.
- **No TA-Lib.** Signal generation uses only the OANDA candles API and pure Python math.
- **Fixed SL/TP.** No trailing stop — clean, predictable exits.

---

## 2. Project structure

```
RF v5.0/
├── bot.py                  # Main cycle orchestrator
├── scheduler.py            # APScheduler entry point
├── signals.py              # CPR signal engine
├── oanda_trader.py         # OANDA execution layer
├── news_filter.py          # News block / penalty logic
├── calendar_fetcher.py     # Forex Factory calendar sync
├── reconcile_state.py      # Broker <-> local state reconciliation
├── database.py             # SQLite persistence (observability)
├── state_utils.py          # JSON file helpers, path constants
├── config_loader.py        # Settings + secrets loader
├── telegram_alert.py       # Telegram HTTP sender
├── telegram_templates.py   # All Telegram message templates
├── logging_utils.py        # Structured logging + secret redaction
├── startup_checks.py       # Config validation on startup
├── analyze_trades.py       # CLI performance dashboard
├── settings.json           # Default config (copied to /data on first run)
├── Procfile                # Railway process definition
├── railway.json            # Railway deployment config
└── requirements.txt        # Python dependencies
```

Runtime data (written to `DATA_DIR`, default `/data`):

```
/data/
├── settings.json                # Live settings (editable at runtime)
├── trade_history.json           # Active trade records (rolling 90 days)
├── runtime_state.json           # Cycle state + cooldowns
├── calendar_cache.json          # Parsed news events
├── cpr_gold.db                  # SQLite observability database
└── logs/
    └── cpr_gold_bot.log         # Rotating log (5 × 1 MB)
```

---

## 3. Strategy design

### 3.1 Central Pivot Range (CPR)

Calculated from the **previous day's** OHLC:

| Level | Formula |
|-------|---------|
| Pivot | (H + L + C) / 3 |
| BC (Bottom Central) | (H + L) / 2 |
| TC (Top Central) | (Pivot − BC) + Pivot |
| R1 | (2 × Pivot) − L |
| R2 | Pivot + (H − L) |
| S1 | (2 × Pivot) − H |
| S2 | Pivot − (H − L) |

Market bias:
- Price **above TC** → bullish bias → look for BUY
- Price **below BC** → bearish bias → look for SELL
- Price **inside CPR** → no trade zone

### 3.2 Breakout conditions

| Condition | Score | Setup label |
|-----------|------:|-------------|
| Price > R2 | +1 | R2 Extended Breakout |
| TC < Price ≤ R2, Price > R1 | +2 | R1 Breakout |
| TC < Price ≤ R1, Price > PDH | +2 | PDH Breakout |
| Price > TC (other) | +2 | CPR Bull Breakout |
| Price < S2 | +1 | S2 Extended Breakdown |
| S2 ≤ Price < BC, Price < S1 | +2 | S1 Breakdown |
| S2 ≤ Price < S1, Price < PDL | +2 | PDL Breakdown |
| Price < BC (other) | +2 | CPR Bear Breakdown |

### 3.3 SMA alignment (M15, last 50 completed candles)

| Condition | Score |
|-----------|------:|
| Both SMA20 and SMA50 confirm direction | +2 |
| One SMA confirms direction | +1 |
| Neither SMA confirms | +0 |

### 3.4 CPR width filter

`CPR width % = abs(TC − BC) / Pivot × 100`

| Width | Interpretation | Score |
|-------|---------------|------:|
| < 0.5% | Narrow — breakout likely | +2 |
| 0.5%–1.0% | Moderate | +1 |
| > 1.0% | Wide — range-bound | +0 |

### 3.5 H1 trend filter (v4.8+)

H1 EMA21 computed on 26 hourly candles every cycle.
- BUY blocked if H1 price < H1 EMA21 (bearish H1 trend)
- SELL blocked if H1 price > H1 EMA21 (bullish H1 trend)
- Disabled by setting `h1_trend_filter_enabled: false`

### 3.6 Candle-close confirmation (v4.8+)

When `require_candle_close: true`, signal uses the **last completed M15 candle** `[-2]`
rather than the forming candle `[-1]`. Eliminates fakeout entries where price
crosses a level intracandle then reverses before the bar closes.

---

## 4. Signal scoring model

| Component | Max score |
|-----------|----------:|
| Breakout strength | 2 |
| SMA alignment | 2 |
| CPR width | 2 |
| **Total** | **6** |

Minimum score to trade: **4**

Decision label: `WATCHING` | `BLOCKED` | `READY`

---

## 5. Position sizing

| Score | Risk | Default |
|-------|------|---------|
| 5–6 | `position_full_usd` | $100 |
| 4 | `position_partial_usd` | $66 |
| 0–3 | No trade | $0 |

```
units = position_risk_usd / sl_usd
```

---

## 6. Stop loss and take profit

### SL — ATR-based adaptive (v4.8+)

```
atr_floor = ATR × sl_min_atr_mult        (e.g. 0.8)
sl_min    = max(sl_min_usd, atr_floor)   (adaptive floor)
sl_usd    = clamp(ATR × atr_sl_multiplier, sl_min, sl_max_usd)
```

| Setting | Default | Description |
|---------|---------|-------------|
| `sl_mode` | `atr_based` | Sizing mode |
| `atr_sl_multiplier` | `1.0` | ATR multiplier |
| `sl_min_usd` | `25.0` | Absolute floor |
| `sl_min_atr_mult` | `0.8` | Adaptive floor multiplier |
| `sl_max_usd` | `60.0` | Ceiling |

### TP — fixed RR (v5.0)

```
tp_usd = sl_usd × rr_ratio
```

Both `rr_ratio` and `max_rr_ratio` are set to **1.5** — TP is always exactly
1.5× the SL. No structural S1/S2 overshoot possible.

| Setting | v4.8 | v5.0 |
|---------|------|------|
| `rr_ratio` | 2.65 | **1.5** |
| `max_rr_ratio` | 3.0 | **1.5** |

**Why 1.5?** Gold's available intraday move from a CPR entry point on an
average session is $35–50. At 1.5× RR with a $40 SL, TP is $60 (6,000 pips) —
achievable on average and volatile days without requiring an exceptional trend.
Breakeven win rate: **40%**.

| ATR | SL | TP | TP pips |
|-----|----|----|---------|
| $35 | $35 | $52.50 | 5,250p |
| $40 | $40 | $60.00 | 6,000p |
| $45 | $45 | $67.50 | 6,750p |
| $50 | $50 | $75.00 | 7,500p |

### Price offsets

| Direction | SL price | TP price |
|-----------|----------|----------|
| BUY | entry − sl_usd | entry + tp_usd |
| SELL | entry + sl_usd | entry − tp_usd |

Gold pip = $0.01

---

## 7. Execution guard pipeline

Guards run in this exact sequence:

```
 1. Market open?             Skip Saturday, Sunday
 2. Session active?          Asian 08:00–15:59 / London 16:00–20:59 / US 21:00–00:59 SGT
 3. Friday cutoff?           No entries after 23:00 SGT Friday
 4. News hard block?         ±30 min around FOMC, NFP, Powell, Rate Decision
 5. OANDA login OK?          Balance > 0
 6. Daily loss cap?          max_losing_trades_day (default 8)
 7. Session loss cap?        max_losing_trades_session (default 4)
 8. Loss cooldown?           30 min after 2 consecutive losses
 9. Session trade cap?       Asian ≤ 5, London ≤ 10, US ≤ 10
10. Concurrent trade cap?    max_concurrent_trades = 1
11. Same-setup cooldown?     min_reentry_wait_min (default 10 min)
12. H1 trend filter?         Block if H1 EMA21 opposes direction (v4.8+)
13. Signal score ≥ 4?        CPR engine evaluation
14. Candle-close check?      Use completed M15 candle (v4.8+)
15. Exhaustion hard block?   S2/R2 extended blocked when overextended
16. RR gate?                 Actual R:R ≥ rr_ratio (1.5)
17. Direction guard?         After 2 consecutive SL same direction → elevated score required
18. Direction cooldown?      60-min time block after direction guard fires (v4.8+)
19. Spread guard?            Session-specific pip limits
20. Margin guard?            Pre-trade size cap
 → Place order (fixed SL + fixed TP)
```

---

## 8. Risk controls

| Rule | Default |
|------|---------|
| Max concurrent trades | 1 |
| Max losing trades per day | 8 |
| Max losing trades per session | 4 |
| Max Asian window trades | 5 |
| Max London window trades | 10 |
| Max US window trades | 10 |
| Consecutive-loss cooldown | 30 min |
| Same-setup cooldown | 10 min |
| Direction guard | After 2 SL hits same direction, require score 5+ |
| Direction time cooldown | 60 min block after direction guard fires |
| Friday cutoff | 23:00 SGT |

---

## 9. News filter

### Hard block — major events
Trades fully blocked ±30 min around: FOMC, Non-Farm Payrolls, Powell, Rate Decision, Fed Chair, Federal Reserve

### Soft penalty — medium events
Score reduced by 1 near: CPI, Core CPI, PCE, Core PCE, Unemployment, Jobless Claims

---

## 10. Trading sessions

All times **Singapore Time (SGT, UTC+8)**.

| Session | Hours (SGT) | Max trades | Report time |
|---------|-------------|-----------|-------------|
| 🌏 Asian | 08:00–15:59 | 5 | 16:05 SGT |
| 🇬🇧 London | 16:00–20:59 | 10 | 21:05 SGT |
| 🗽 US | 21:00–00:59 | 10 | 01:05 SGT |
| 💤 Dead zone | 01:00–07:59 | None | — |

---

## 11. State management and reconciliation

### JSON state files

| File | Purpose |
|------|---------|
| `trade_history.json` | Active trade records (rolling 90 days) |
| `runtime_state.json` | Cycle metadata, cooldowns, direction blocks |
| `calendar_cache.json` | Parsed news events |

All writes are atomic (temp file + rename).

### Reconciliation
Every cycle: fetch all open trades at broker, insert any missing from local history, backfill P&L on closed trades.

---

## 12. Database and observability

SQLite at `DATA_DIR/cpr_gold.db` (WAL mode).

| Table | Contents |
|-------|---------|
| `cycle_runs` | Every cycle: start, finish, status, summary JSON |
| `signals_log` | Every signal: score, direction, full payload |
| `trades` | Every order attempt: result, broker ID, note |

---

## 13. Configuration reference

### Strategy

| Key | Default | Description |
|-----|---------|-------------|
| `signal_threshold` | 4 | Minimum score to trade |
| `position_full_usd` | 100 | Risk USD for score 5–6 |
| `position_partial_usd` | 66 | Risk USD for score 4 |
| `sl_mode` | `atr_based` | SL sizing mode |
| `atr_sl_multiplier` | 1.0 | ATR multiplier |
| `sl_min_usd` | 25.0 | Absolute SL floor |
| `sl_min_atr_mult` | 0.8 | Adaptive SL floor (fraction of ATR) |
| `sl_max_usd` | 60.0 | SL ceiling |
| `rr_ratio` | 1.5 | TP = SL × rr_ratio |
| `max_rr_ratio` | 1.5 | TP hard ceiling (same as rr_ratio = fixed RR) |
| `exhaustion_atr_mult` | 2.5 | Extended setup block threshold |
| `h1_trend_filter_enabled` | true | H1 EMA trend filter |
| `h1_ema_period` | 21 | H1 EMA period |
| `require_candle_close` | true | Use completed M15 candle for signal |
| `trailing_stop_atr_mult` | 0 | Disabled — fixed SL/TP only |

### Risk

| Key | Default | Description |
|-----|---------|-------------|
| `max_concurrent_trades` | 1 | Max simultaneous positions |
| `max_losing_trades_day` | 8 | Daily loss cap |
| `max_losing_trades_session` | 4 | Per-session loss cap |
| `max_trades_asian` | 5 | Asian window cap |
| `max_trades_london` | 10 | London window cap |
| `max_trades_us` | 10 | US window cap |
| `loss_streak_cooldown_min` | 30 | Cooldown after 2 consecutive losses |
| `consecutive_sl_guard` | 2 | SL streak threshold for direction guard |
| `sl_direction_cooldown_min` | 60 | Time block after direction guard fires |
| `min_reentry_wait_min` | 10 | Same-setup re-entry cooldown |

### Sessions

| Key | Default | Description |
|-----|---------|-------------|
| `asian_session_enabled` | true | Enable Asian session |
| `london_session_enabled` | true | Enable London session |
| `us_session_enabled` | true | Enable US session |
| `friday_cutoff_hour_sgt` | 23 | No entries after this hour on Fridays |
| `trading_day_start_hour_sgt` | 8 | Day boundary (SGT) |

### Infrastructure

| Key | Default | Description |
|-----|---------|-------------|
| `bot_name` | `CPR Gold Bot v5.0` | Display name |
| `demo_mode` | true | `true` = practice, `false` = live |
| `cycle_minutes` | 5 | Bot cycle interval |
| `db_retention_days` | 90 | Data retention window |

---

## 14. Secrets and environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OANDA_API_KEY` | Yes | OANDA v20 API token |
| `OANDA_ACCOUNT_ID` | Yes | OANDA account ID |
| `TELEGRAM_TOKEN` | Yes | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Yes | Telegram chat / group ID |
| `DATA_DIR` | No | Persistent data path (default `/data`) |
| `TRADING_DISABLED` | No | Set `true` to pause without restart |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` (default `INFO`) |

---

## 15. Deployment (Railway)

```
Procfile:     web: python scheduler.py
railway.json: startCommand: python scheduler.py
```

Required environment variables in Railway dashboard:
```
OANDA_API_KEY
OANDA_ACCOUNT_ID
TELEGRAM_TOKEN
TELEGRAM_CHAT_ID
DATA_DIR=/data
```

Mount a Railway volume at `/data` to persist state across deploys.

---

## 16. Running locally

```bash
pip install -r requirements.txt

export OANDA_API_KEY=your_key
export OANDA_ACCOUNT_ID=your_account
export TELEGRAM_TOKEN=your_token
export TELEGRAM_CHAT_ID=your_chat_id
export DATA_DIR=./data

python scheduler.py
```

---

## 17. Performance analysis tool

```bash
python analyze_trades.py              # All-time closed trades
python analyze_trades.py --last 30    # Last 30 days
```

---

## 18. Changelog

### v5.1 — 2026-03-31
**Feature: Signal candle debug log for timing verification**

Added a log line every cycle showing exactly which candle the bot acted on:

```
Signal candle | close=4462.35 (candle [-2]) | current_tick=4465.80 (candle [-1]) | ATR=42.15
```

| Field | Meaning |
|-------|---------|
| `close=` | Completed M15 candle used for signal — match this on your OANDA chart |
| `current_tick=` | Live forming candle price at cycle time |
| `ATR=` | Current ATR — SL will be approximately this value |

**Timing note:** All log timestamps inside the message are SGT (UTC+8). Railway log prefix is UTC.

**Files changed:** `signals.py`, `version.py`, `settings.json`, `bot.py`, `README.md`, `CONFLUENCE_READY.md`

---

### v5.0 — 2026-03-30
**Fix: TP ratio reduced to 1.5× for average-day reachability**

**Problem:** Trades were reaching $100–125 unrealised profit twice and reversing
before TP. At rr_ratio 2.65 and max_rr_ratio 3.0, TP was placed $92–170 away —
requiring an exceptional trending day (10,000+ pips of movement) to hit.
Gold's average available move from entry on a normal session is $35–50,
meaning TP was consistently out of reach on regular days.

**Fix:** Both `rr_ratio` and `max_rr_ratio` reduced to **1.5**. Setting both
the same removes all structural S1/S2 overshoot — TP is always exactly
`SL × 1.5`, no variation.

| Setting | v4.8 | v5.0 |
|---------|------|------|
| `rr_ratio` | 2.65 | **1.5** |
| `max_rr_ratio` | 3.0 | **1.5** |

**Impact at typical ATR $40–45:**

| | v4.8 | v5.0 |
|---|---|---|
| TP | $106–119 (10,600–11,925p) | **$60–67 (6,000–6,750p)** |
| Breakeven WR | 27% | **40%** |
| Reachable on average day | ❌ No | **✅ Yes** |

**Files changed:** `settings.json`, `version.py`, `bot.py`, `signals.py`, `README.md`, `CONFLUENCE_READY.md`

---

### v4.8 — 2026-03-28
**Feature: Adaptive SL floor + H1 trend filter + candle-close confirmation + direction time cooldown**

Four improvements addressing the root causes of Mar 25–27 losses.

| Fix | Setting | Effect |
|-----|---------|--------|
| Adaptive SL floor | `sl_min_atr_mult: 0.8` | SL floor scales with ATR — quiet days get proportionally smaller floor |
| H1 trend filter | `h1_trend_filter_enabled: true`, `h1_ema_period: 21` | Blocks trades against H1 EMA21 macro trend |
| Candle-close confirmation | `require_candle_close: true` | Uses last completed M15 candle — eliminates fakeout entries |
| Direction time cooldown | `sl_direction_cooldown_min: 60` | 60-min block after direction guard fires |

**Root cause of Mar 25–27 failures:**
1. Gold in strong macro bull run (tariff news) — bot kept selling into rising market → fixed by H1 filter
2. SL $20 (~20 pips) too tight for 40–50 pip ATR → fixed by adaptive floor
3. Candle-close fakeouts triggering entries on intracandle spikes → fixed by `require_candle_close`

---

### v4.7 — 2026-03-28
**Update: Telegram templates cleaned and updated to gold bot content**

Replaced forex-specific Telegram templates (GBP/USD pairs, Tokyo session, ORB references)
with gold-specific content. New cleaner message format: WATCHING is 5 lines,
BLOCKED is 3 lines, READY shows only execution-critical fields.

---

### v4.6 — 2026-03-28
**Fix: TP hard ceiling + SL floor raised**

| Fix | Setting | Effect |
|-----|---------|--------|
| TP ceiling | `max_rr_ratio: 3.0` | TP never exceeds SL × 3.0 — fixes 1:5.1 TP bug |
| SL floor | `sl_min_usd: 35.0` | Raised from $20 — gives room beyond ATR noise |

---

### v4.5 — 2026-03-24
**Fix: Trailing stop disabled — pure fixed SL/TP**

`trailing_stop_atr_mult: 0` — removes trailing stop entirely. 10 of 14 trail exits
were losses at avg 9-minute hold. Gold reverses 20–30 pips routinely before
continuing. Fixed SL/TP gives cleaner data and eliminates trail noise.

---

### v4.4 — 2026-03-23
**Feature: Asian session + per-session reports**

| Change | Detail |
|--------|--------|
| Asian session | 08:00–15:59 SGT, cap 5 trades |
| Dead zone | Reduced to 01:00–07:59 SGT only |
| Session reports | Asian 16:05 / London 21:05 / US 01:05 SGT |

---

### v4.1 — v4.3
Initial ATR-based SL/TP, structural TP from S1/R1, direction guard, RR gate,
server-side trailing stop, partial close, parameterisation, spread limits.

---

### v4.0
ATR-based SL replaces fixed 0.25% SL. Extended setup hard block. Breakeven re-enabled.

---

### v3.x
CPR breakout strategy initial development. Session guards, news filter,
Telegram reporting, Railway deployment, pyramid trading, signal threshold fixes.
