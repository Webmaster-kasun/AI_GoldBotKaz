## v5.1 Release — Signal Candle Debug Log

**Date:** 2026-03-31

Added timing verification log to signals.py — fires every cycle:

```
Signal candle | close=4462.35 (candle [-2]) | current_tick=4465.80 (candle [-1]) | ATR=42.15
```

`close=` is the completed M15 candle the bot acted on — match this on your OANDA chart (M15, UTC+8).
`current_tick=` is live price at cycle time. Gap between the two = price moved since signal candle closed.

All bot log timestamps are SGT. Railway prefix timestamps are UTC (8 hours behind).

---

## v5.0 Release — AtomicFX-Style Telegram Templates

**Date:** 2026-03-30

### Changes

Adopted the RF MP Scalp v2.6 "AtomicFX" Telegram template style — compact, clean, state-change only.

| Card | Change |
|------|--------|
| Signal WATCHING | Pair + direction icon + score inline. H1 trend shown with 🟢/🔴 icon |
| Signal BLOCKED | Single reason line. H1 counter-trend flagged with ⚠️ |
| Signal READY | Window + CPR width inline. H1 alignment confirmed |
| Trade opened | Structured block: Entry → TP1 (bot) → TP2 (reference) → SL with pips |
| Trade closed | Outcome inline: `📉 SELL SL ✗` / `📈 BUY TP ✅`. Peak pips shown |
| Cooldown | 🧊 icon. Remaining loss count visible |
| Session open | Icon-first format. CPR scan label instead of ORB |
| Startup | XAU/USD (M15), CPR Breakout strategy, H1 filter status line |

### New in telegram_alert.py

`send_document()` method added — sends a file as Telegram document attachment.
Used for scheduled trade history exports.

### Deployment checklist

1. Deploy v5.0
2. Confirm startup Telegram shows `🚀 CPR Gold Bot v5.0 started`
3. Confirm H1 filter line shows in startup: `H1 filter: ✅ HARD`
4. Confirm log shows `Updated 1 key(s): ['bot_name']`
5. First signal card should show `XAU/USD 📉 SELL Score 4/6 👁 Watching` format

---

# CPR Gold Bot — Release Notes

---

## v5.0 Release — TP Ratio Reduced to 1.5× for Average-Day Reachability

**Date:** 2026-03-30
**Triggered by:** Observation that trades reached $100–125 unrealised profit twice and reversed before TP hit.

### Root cause

| Issue | Detail |
|-------|--------|
| TP too far | At rr_ratio 2.65, TP was $92–170 away — requiring 9,000–17,000 pips of movement |
| Gold average move | Available move from a CPR entry on a normal session is $35–50 |
| Result | TP consistently out of reach on regular days; only hit on exceptional trending days |

### Fix

Both `rr_ratio` and `max_rr_ratio` set to **1.5**. Setting both identical removes
all structural S1/S2 overshoot — TP is always exactly `SL × 1.5`, no variation.

### Settings change

| Key | v4.8 | v5.0 |
|-----|------|------|
| `rr_ratio` | 2.65 | **1.5** |
| `max_rr_ratio` | 3.0 | **1.5** |

### Impact

| Metric | v4.8 | v5.0 |
|--------|------|------|
| TP at SL=$40 | $106 (10,600p) | **$60 (6,000p)** |
| TP at SL=$45 | $119 (11,925p) | **$67.50 (6,750p)** |
| Breakeven WR | 27% | **40%** |
| Hits on average day | ❌ No | **✅ Yes** |

### Expected behaviour after deploy

- Telegram startup shows `CPR Gold Bot v5.0`
- Trades close faster — TP at $60–75 rather than $100–170
- Win rate should increase as TP is within normal session range
- Startup log shows `Updated 2 key(s): ['rr_ratio', 'max_rr_ratio']`

### Deployment checklist

1. Deploy v5.0 on Railway
2. Confirm Telegram startup message shows `v5.0`
3. Confirm log shows `Updated 2 key(s): ['rr_ratio', 'max_rr_ratio']`
4. Monitor first trade — confirm TP is ~6,000–7,500 pips (not 10,000+)

### Files changed

`settings.json`, `version.py`, `bot.py`, `signals.py`, `README.md`, `CONFLUENCE_READY.md`

---

## v4.8 Release — Adaptive SL + H1 Filter + Candle-Close + Direction Cooldown

**Date:** 2026-03-28
**Triggered by:** Analysis of Mar 25–27 failures — all 5 trades SL hits, all in wrong direction.

### Root cause summary

| # | Root cause | Effect |
|---|---|---|
| 1 | Gold in macro bull run (tariff news) | Bot kept selling into rising market |
| 2 | SL $20 (~20 pips) — inside 1× ATR candle noise | All trades stopped by normal volatility |
| 3 | Candle-close fakeouts | Entries triggered on intracandle spikes that reversed |
| 4 | No time-based direction block | Bot re-entered same losing direction every 5 min |

### Changes

| # | Fix | Setting | File |
|---|-----|---------|------|
| 1 | H1 trend filter | `h1_trend_filter_enabled: true`, `h1_ema_period: 21` | `signals.py` |
| 2 | Adaptive SL floor | `sl_min_atr_mult: 0.8`, `sl_min_usd: 25` | `bot.py` |
| 3 | Candle-close confirmation | `require_candle_close: true` | `signals.py` |
| 4 | Direction time cooldown | `sl_direction_cooldown_min: 60` | `bot.py` |

### New settings

| Key | v4.7 | v4.8 |
|-----|------|------|
| `sl_min_usd` | 35.0 | **25.0** |
| `sl_min_atr_mult` | absent | **0.8** |
| `h1_trend_filter_enabled` | absent | **true** |
| `h1_ema_period` | absent | **21** |
| `require_candle_close` | absent | **true** |
| `sl_direction_cooldown_min` | absent | **60** |

### How each fix works

**H1 trend filter:** Fetches 26 H1 candles every cycle, computes EMA21.
BUY blocked if H1 price < EMA21. SELL blocked if H1 price > EMA21.
Mar 27 bull run would have blocked all SELL entries — the core failure prevented.

**Adaptive SL floor:** `sl_min = max(sl_min_usd, ATR × 0.8)`. On a $20 ATR day
floor adapts to $16 instead of locking at $35. On a $50 ATR day floor is $40.
Proportional to volatility rather than fixed.

**Candle-close:** `current_close = m15_closes[-2]` (completed bar, not forming).
Eliminates entries triggered by intracandle spikes that reverse before bar close.

**Direction cooldown:** When direction guard fires (2 SL hits same direction),
timestamps `direction_block_{buy/sell}` in runtime_state.json. Next cycle checks
timestamp before allowing same-direction entry. Persists across container restarts.

### Deployment checklist

1. Deploy v4.8
2. Confirm `Updated 7 key(s)` in startup log
3. Monitor first blocked trade — confirm H1 filter appears in BLOCKED reason when applicable
4. After 2 SL hits same direction — confirm 60-min cooldown activates

---

## v4.7 Release — Telegram Templates Gold Bot Clean-Up

**Date:** 2026-03-28

Replaced uploaded forex-specific templates (GBP/USD, Tokyo session, ORB/EMA references)
with gold-specific content throughout.

| Change | Detail |
|--------|--------|
| Startup message | Now shows XAU/USD (M15), Asian/London/US sessions, correct position sizes |
| Session icons | Tokyo 🗼 → Asian 🌏 |
| Signal WATCHING | Reduced from full check panel to 5-line compact card |
| Signal BLOCKED | Reduced to 3 lines: direction, score, reason |
| Signal READY | Compact: position, spread, margin, signal notes only |
| Session open | "Scanning for EMA + ORB scalp setups" → "Scanning for CPR breakout setups" |
| `telegram_alert.py` | Bot name always read from settings.json — header auto-updates on version change |

---

## v4.6 Release — TP Hard Ceiling + SL Floor Raised

**Date:** 2026-03-28

### Problem 1 — TP too far (1:5.1 RR bug)
Structural S1/S2 TP passed the `stp >= sl_usd * min_rr` check but was placed
far beyond the intended RR. Live trade showed TP at 7,979 pips vs SL at 1,566 pips = 1:5.1 RR.

**Fix:** New `max_rr_ratio: 3.0`. All TP sources capped at `SL × max_rr_ratio`
inside `compute_tp_usd()`. Impossible to place TP beyond 3× SL regardless of structural level.

### Problem 2 — SL too tight
`sl_min_usd: 20` (~20 pips). Gold ATR 40–50 pips. All 5 Mar 25–27 losses
hit SL within 20–21 pips — normal volatility, not adverse moves.

**Fix:** `sl_min_usd: 35`. Raised from $20. Gives minimum 1× ATR of breathing room.

| Key | v4.5 | v4.6 |
|-----|------|------|
| `sl_min_usd` | 20.0 | **35.0** |
| `sl_max_usd` | 55.0 | **60.0** |
| `max_rr_ratio` | absent | **3.0** |

---

## v4.5 Release — Trailing Stop Disabled

**Date:** 2026-03-24

`trailing_stop_atr_mult: 0` — trailing stop removed entirely.

**Analysis of trail exits (14 total):**
- Profitable: 4 (avg +$31)
- Losing: 10 (avg −$30)
- Avg hold time: 9 minutes
- Total trail P&L: −$174

Gold reverses 20–30 pips routinely before continuing the trend. Trail at 0.5× ATR
fires before meaningful profit, turning neutral positions into small losses.
Fixed SL/TP gives cleaner data and simpler analysis.

---

## v4.4 Release — Asian Session + Per-Session Reports

**Date:** 2026-03-23

| Change | Detail |
|--------|--------|
| Asian session added | 08:00–15:59 SGT, cap 5 trades, spread limit 150 pips |
| Dead zone reduced | Was 01:00–15:59 SGT, now 01:00–07:59 SGT only |
| Per-session reports | Asian 16:05, London 21:05, US 01:05 SGT |
| `msg_session_report()` | New template: trades, P&L, WR, avg hold per session |

All 3 sessions enabled with per-session enable flags, caps, and reports.

---

## v4.1–v4.3 — Foundation Releases

Core architecture: ATR-based SL, structural TP from S1/R1, RR gate post-SL computation,
server-side trailing stop, direction guard, full parameterisation, spread limits per session.

---

## v4.0 — ATR-Based SL/TP

Root cause fix for 2026-03-19 losses. ATR-based SL replaces fixed 0.25%.
Extended setup hard block. Breakeven re-enabled.

---

## v3.x — Initial Development

CPR strategy, scoring model, session guards, news filter, Telegram reporting,
Railway deployment, pyramid trading (disabled by default).
