# GOLD v1.1 — Confluence Ready

**Date:** 2026-03-17  
**Version:** v1.1  
**Status:** Score tiers refined, SL/TP priority logic added, R:R guard enforced

---

## 1. Executive Summary

GOLD v1.1 refines the scoring and risk model from v1.0. The small trade tier ($33, score 2–3) has been removed — any score below 3 is a no-trade. SL and TP now follow a structured priority logic: structural CPR and R1/S1 levels are used where they fall within the valid range, with fixed percentages as fallback. A non-negotiable R:R guard (minimum 1:2) automatically skips any trade that does not meet the threshold, regardless of score.

---

## 2. Design Goal

> Take larger positions only when all three conditions agree. Reduce size — not direction — when conditions are mixed. Walk away entirely when the setup is below the minimum quality threshold (score < 3).

A trade is only taken at score ≥ 3. This filters out weak setups and preserves capital for high-quality entries. The $33 small tier has been removed — if the setup isn't worth at least $66, it isn't worth taking.

---

## 3. Strategy Logic

### 3.1 CPR Levels

All key levels derive from the previous day's High (PDH), Low (PDL), and Close (PDC):

| Level | Formula |
|---|---|
| Pivot | (PDH + PDL + PDC) / 3 |
| BC (Bottom CPR) | (PDH + PDL) / 2 |
| TC (Top CPR) | (Pivot − BC) + Pivot |
| R1 | (2 × Pivot) − PDL |
| R2 | Pivot + (PDH − PDL) |
| S1 | (2 × Pivot) − PDH |
| S2 | Pivot − (PDH − PDL) |

The CPR band (BC to TC) acts as the signal gate. Price must break out of this band to generate any signal.

### 3.2 Scoring System

Each signal is scored out of 6 across three independent conditions.

#### Condition 1 — Main condition

The price position relative to key CPR levels determines signal direction and base score:

**Bull:**
- Price > TC and price ≤ R2 → direction = BUY, +2 (standard breakout)
- Price > R2 → direction = BUY, +1 (extended entry — premium reduced)

**Bear:**
- Price < BC and price ≥ S2 → direction = SELL, +2 (standard breakdown)
- Price < S2 → direction = SELL, +1 (extended entry — premium reduced)

The R2/S2 extended-entry penalty reflects the risk of entering after price has already run far from the CPR. The breakout is still valid, but the potential move to TP is smaller.

#### Condition 2 — SMA alignment

Uses SMA20 and SMA50 of completed M15 candles.

**Bull:**
- Both SMAs below price → +2
- One SMA below price → +1
- Both SMAs above price → +0

**Bear:**
- Both SMAs above price → +2
- One SMA above price → +1
- Both SMAs below price → +0

SMA alignment measures the intermediate trend. A bull breakout with both SMAs below is structurally sound. A breakout with both SMAs above means price is breaking up against the trend — still tradeable at reduced size.

#### Condition 3 — CPR width

The CPR width as a percentage of the pivot price reflects how well-defined the prior day's range was.

| Width | Score | Interpretation |
|---|---|---|
| < 0.5% | +2 | Narrow CPR — strong pivot zones, clean breakouts |
| 0.5% to 1.0% | +1 | Moderate — acceptable |
| > 1.0% | +0 | Wide — noisy pivot, reduced confidence |

---

## 4. Score → Position Size

| Score | Position | Tier |
|---|---|---|
| 5 – 6 | $100 | Full — all conditions optimal |
| 3 – 4 | $66 | Half — strong but not perfect |
| Below 3 | No trade | Walk away |

Score of 1–2 can only occur from an R2/S2 extended entry with weak SMA contribution and moderate/wide CPR. These setups are not traded in v1.1.

**Position size = risk amount.** Units = `position_usd / sl_usd`.

---

## 5. SL / TP Model

SL and TP follow a **priority-based logic** — structural levels are preferred over fixed percentages where they fall within the valid range.

### SL Priority (evaluated each trade)

| Priority | Condition | SL Used |
|---|---|---|
| 1st | CPR level (BC for longs, TC for shorts) is within 0.25% of entry | Below/above CPR structural level |
| 2nd | CPR level is further than 0.25% from entry | Fixed 0.25% of entry price |

The CPR structural SL is tighter and more meaningful — it places the stop below the level that, if broken, invalidates the setup entirely.

### TP Priority (evaluated each trade)

| Priority | Condition | TP Used |
|---|---|---|
| 1st | R1 (long) or S1 (short) falls 0.50%–0.75% from entry | R1 / S1 structural level |
| 2nd | R1/S1 is more than 0.75% away | Fixed 0.75% of entry price |
| Skip | R1/S1 is less than 0.50% away | Trade skipped — not enough room to target |

### Non-Negotiable R:R Guard

> **Minimum R:R is 1:2.** TP must be at least 2× the SL distance.
> Any trade where computed R:R < 1:2 is automatically rejected, regardless of score.
> Ideal target is always 1:3 (0.25% SL : 0.75% TP).

### Break-Even Rule

When price moves 0.50% in favour (2× SL distance), the SL is moved to entry. This locks in a risk-free trade. In bot terms, this triggers at +$5 profit.

**Long example at $4,000:**
- Entry: $4,000
- SL: $3,990 (−0.25% / 100 pips)
- TP: $4,030 (+0.75% / 300 pips)
- Break-even trigger: $4,020 (+0.50%) → SL moves to $4,000

**Short example at $4,000:**
- Entry: $4,000
- SL: $4,010 (+0.25%)
- TP: $3,970 (−0.75%)
- Break-even trigger: $3,980 (−0.50%) → SL moves to $4,000

### Units and Margin at $5,000 gold (5% OANDA margin rate)

| Tier | Risk | SL | Units | Approx. Margin |
|---|---|---|---|---|
| Half | $66 | $12.50 | 5.3 | ~$1,325 |
| Full | $100 | $12.50 | 8.0 | ~$2,000 |

As gold price rises, SL widens in dollar terms and units decrease — keeping margin usage proportional automatically.

### Margin Safety Cap

Before every order the bot checks `marginAvailable` from OANDA and caps units to `marginAvailable × 0.8 / (price × marginRate)`. This prevents `INSUFFICIENT_MARGIN` rejections on small accounts.

---

## 6. Session Structure

Trading is restricted to active gold liquidity windows (SGT):

| Window | Hours | Max Trades |
|---|---|---|
| Dead Zone | 02:00–09:59 | No entries |
| Asian | 10:00–13:59 | 2 |
| London | 14:00–19:59 | 6 (shared with US) |
| US | 20:00–01:59 | 6 (shared with London) |

Dead Zone: existing open trades are managed normally (SL/TP/break-even). Only new entries are blocked.

---

## 7. Risk Controls

| Control | Value | Purpose |
|---|---|---|
| Max concurrent trades | 1 | No pyramiding |
| Max trades / day | 8 | Daily exposure cap |
| Max losing trades / day | 3 | Drawdown protection |
| Loss streak cooldown | 30 min after 2 consecutive losses | Circuit breaker |
| Spread guard | Per-session limits | Avoid wide-spread fills |
| Margin cap | 80% of marginAvailable | Prevent margin rejection |
| Friday cutoff | 23:00 SGT | Avoid low-liquidity close |
| Saturday | Skip | Market closed |
| Sunday | Skip | Market closed |
| Monday pre-08:00 SGT | Skip | Tokyo open alignment |

---

## 8. News Filter

Two modes:

**Hard block:** No new entries during the event window (−30 min / +30 min). Triggered by high-impact USD events: FOMC, NFP, Fed rate decisions, Fed Chair speeches.

**Soft penalty:** Score reduced by −1 per qualifying medium event. Affects: CPI, PCE, GDP, Retail Sales, ISM, Jobless Claims, SNB, DXY events. After penalty, position size recalculates from the adjusted score.

Lookahead: upcoming events in the next 120 minutes are logged each cycle for informational awareness.

---

## 9. Telegram Alert Structure

Every alert falls into one of these categories:

| Alert | Trigger |
|---|---|
| Signal Update | Score or direction changes |
| Trade Opened | Order filled |
| Break-Even | SL moved to entry |
| Trade Closed | P&L backfilled |
| News Block | Hard block activated |
| News Penalty | Soft penalty reduces score/size |
| Cooldown | 2 consecutive losses |
| Daily Cap | Losing trade or total trade limit |
| Spread Skip | Spread exceeds session limit |
| Friday Cutoff | Post-23:00 SGT Friday |
| System Error | OANDA login or pricing failure |

The **Signal Update** message includes a full score breakdown showing the points contributed by each condition and the resulting position size tier.

---

## 10. Architecture

```
scheduler.py          — APScheduler, runs every 5 min
    └── bot.py        — cycle orchestrator, risk controls, order placement
         ├── signals.py          — CPR scoring engine
         ├── oanda_trader.py     — OANDA REST API
         ├── news_filter.py      — economic calendar
         ├── telegram_alert.py   — message delivery
         ├── telegram_templates.py — message strings
         ├── database.py         — SQLite log
         └── reconcile_state.py  — state sync
```

---

## 11. Configuration Reference

Key `settings.json` parameters:

| Key | Default | Notes |
|---|---|---|
| `sl_mode` | `pct_based` | `pct_based`, `atr_based`, or `fixed_usd` |
| `sl_pct` | 0.0025 | SL fallback = entry price × 0.25% |
| `tp_pct` | 0.0075 | TP fallback = entry price × 0.75% |
| `rr_ratio` | 3.0 | TP = SL × 3 (ideal target) |
| `signal_threshold` | 3 | Minimum score to trade — below 3 = no trade |
| `position_full_usd` | 100 | Risk for score 5–6 |
| `position_partial_usd` | 66 | Risk for score 3–4 |
| `breakeven_trigger_usd` | 5.0 | Move SL to entry at +$5 |
| `margin_safety_factor` | 0.8 | Fraction of marginAvailable to use |
| `news_filter_enabled` | true | Enable/disable news filter |
| `demo_mode` | true | OANDA practice vs live |
| `max_losing_trades_day` | 3 | Daily loss cap |
| `max_trades_day` | 8 | Daily trade cap |

---

## 12. Deployment

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
OANDA_API_KEY=...
OANDA_ACCOUNT_ID=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Run
python scheduler.py
```

Railway / Heroku: uses `Procfile` with `worker: python scheduler.py`.
