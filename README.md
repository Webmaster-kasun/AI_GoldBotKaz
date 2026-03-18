# GOLD v1.1

**CPR Gold Bot** is an automated XAU/USD breakout trader built on Central Pivot Range levels, running on M15 candles via the OANDA API.

v1.1 — Updated scoring tiers (no small trades), structured SL/TP priority logic, and non-negotiable R:R guard.

---

## Strategy Overview

- **Instrument:** XAU/USD (OANDA: `XAU_USD`)
- **Signal timeframe:** M15
- **Execution cycle:** every 5 minutes
- **Entry model:** CPR breakout or breakdown confirmed by SMA alignment
- **Position size:** determined by signal score ($33 / $66 / $100 risk)
- **SL model:** percentage-based — 0.25% of gold price
- **TP model:** 0.75% of gold price (3× SL, 1:3 RR)

---

## Signal Scoring

Every signal is scored out of **6 points** across three conditions.

### Condition 1 — Main condition (required)

| Scenario | Points |
|---|---|
| Price above CPR top (TC) / PDH / R1 — Bull breakout zone | +2 |
| Price above R2 — overextended Bull entry | +1 |
| Price below CPR bottom (BC) / PDL / S1 — Bear breakdown zone | +2 |
| Price below S2 — overextended Bear entry | +1 |
| Price inside CPR (BC to TC) | 0 — no signal |

If the main condition is not met, the cycle ends with no trade.

### Condition 2 — SMA alignment

| Scenario (Bull) | Points |
|---|---|
| Both SMA20 and SMA50 below price | +2 |
| Only one SMA below price | +1 |
| Both SMAs above price | +0 |

| Scenario (Bear) | Points |
|---|---|
| Both SMA20 and SMA50 above price | +2 |
| Only one SMA above price | +1 |
| Both SMAs below price | +0 |

### Condition 3 — CPR width

| CPR width | Points |
|---|---|
| < 0.5% (narrow) | +2 |
| 0.5% to 1.0% (moderate) | +1 |
| > 1.0% (wide) | +0 |

---

## Score → Position Size

| Score | Position | Description |
|---|---|---|
| 5 – 6 | $100 | Full — all conditions optimal |
| 3 – 4 | $66 | Half — strong but not perfect |
| Below 3 | No trade | Walk away — signal too weak |

> **Rule:** If it's not a great setup, it's no trade. Score below 3 = walk away. Protecting capital is more important than participation.

---

## CPR Levels

All levels are derived from the previous day's High, Low, and Close:

```
Pivot = (PDH + PDL + PDC) / 3
BC    = (PDH + PDL) / 2
TC    = (Pivot − BC) + Pivot
R1    = (2 × Pivot) − PDL
R2    = Pivot + (PDH − PDL)
S1    = (2 × Pivot) − PDH
S2    = Pivot − (PDH − PDL)
```

---

## SL / TP Model

SL and TP follow a **priority-based logic** — structural levels are preferred over fixed percentages where possible.

### SL Priority (per trade)

| Priority | Condition | SL Used |
|---|---|---|
| 1st | CPR level (BC for longs, TC for shorts) is within 0.25% of entry | Below/above CPR |
| 2nd | CPR level is further than 0.25% | Fixed 0.25% |

### TP Priority (per trade)

| Priority | Condition | TP Used |
|---|---|---|
| 1st | R1 (long) or S1 (short) falls 0.50%–0.75% from entry | R1 / S1 level |
| 2nd | R1/S1 is more than 0.75% away | Fixed 0.75% |
| Skip | R1/S1 is less than 0.50% away | Trade skipped — not enough room |

### Non-Negotiable Rule

> **Never take a trade where R:R is less than 1:2.**
> SL 0.25% → TP must be at least 0.50% minimum.
> Ideally always aim for **1:3 (0.25% SL : 0.75% TP)**.
> Any trade where computed R:R < 1:2 is automatically skipped.

### Break-Even

The moment price moves 0.50% in your favour (2× SL), the SL is moved to entry (break-even). This removes all risk from the trade. Triggered at +$5 profit in the bot.

### Example at $4,000 gold

| Parameter | Calculation | Value |
|---|---|---|
| SL | 0.25% × 4000 | $10 (100 pips) at $3,990 |
| TP | 0.75% × 4000 | $30 (300 pips) at $4,030 |
| R:R | 1:3 | always |
| Break-even trigger | 0.50% × 4000 | Move SL to $4,000 when price hits $4,020 |

---

## Sessions (SGT)

| Window | Session | Hours | Trade Cap |
|---|---|---|---|
| Dead Zone | — | 02:00–09:59 | No entries |
| Asian Window | Asian | 10:00–13:59 | 2 trades max |
| Main Window | London | 14:00–19:59 | 6 combined |
| Main Window | US | 20:00–01:59 | 6 combined |
| Market Closed | — | Saturday | Skipped |
| Market Closed | — | Sunday | Skipped |
| Market Closed | — | Monday before 08:00 | Skipped |

---

## Risk Controls

| Control | Value |
|---|---|
| Max concurrent trades | 1 |
| Max trades / day | 8 |
| Max losing trades / day | 3 |
| Loss streak cooldown | 30 min after 2 consecutive losses |
| Spread guard | Enabled per session |
| Margin cap | 80% of marginAvailable — auto-floors units |
| Friday cutoff | No new entries after 23:00 SGT |

---

## News Filter

### Hard block (no new entries)
- FOMC / FOMC Minutes
- NFP / Non-Farm Payrolls
- Fed Chair remarks (Powell)
- Federal Reserve rate decisions

### Soft penalty (score reduced by −1 per medium event)
- CPI / Core CPI, PCE / Core PCE
- GDP, Retail Sales, ISM, Durable Goods
- Unemployment / Jobless Claims
- Consumer Confidence, Michigan Sentiment
- SNB rate decisions
- Dollar Index / DXY, Gold reserves

**Block window:** 30 min before + 30 min after  
**Lookahead:** events in next 120 min logged each cycle

---

## File Map

| File | Purpose |
|---|---|
| `signals.py` | CPR scoring engine |
| `bot.py` | Main trading cycle orchestrator |
| `telegram_templates.py` | All Telegram message strings |
| `oanda_trader.py` | OANDA API wrapper |
| `news_filter.py` | Economic calendar filter |
| `scheduler.py` | APScheduler entry point |
| `settings.json` | Runtime configuration |
| `database.py` | SQLite cycle and trade log |
| `state_utils.py` | Runtime state persistence |
| `reconcile_state.py` | OANDA ↔ local state sync |

---

## Running the Bot

```bash
python scheduler.py
```

---

## Key Settings

| Setting | Default | Purpose |
|---|---|---|
| `sl_mode` | `pct_based` | `pct_based`, `atr_based`, or `fixed_usd` |
| `sl_pct` | 0.0025 | SL = price × 0.25% (fallback if CPR is far) |
| `tp_pct` | 0.0075 | TP = price × 0.75% (fallback if R1/S1 is too far) |
| `rr_ratio` | 3.0 | TP = SL × 3 |
| `signal_threshold` | 3 | Minimum score to trade (below 3 = no trade) |
| `position_full_usd` | 100 | Risk amount for score 5–6 |
| `position_partial_usd` | 66 | Risk amount for score 3–4 |
| `breakeven_trigger_usd` | 5.0 | Move SL to entry when +$5 |
| `margin_safety_factor` | 0.8 | Max % of marginAvailable used per trade |
| `demo_mode` | true | Use OANDA practice account |

---

## Notes

Run on OANDA demo mode first. At $5,000 gold the $66 partial tier requires ~$1,325 margin, which fits a $1,500 account comfortably. The $100 full tier (~$2,000 margin) may be floored by the margin cap on small accounts — this is by design.

To switch to ATR-based stops, set `"sl_mode": "atr_based"` in `settings.json`. The `atr_sl_multiplier`, `sl_min_usd`, and `sl_max_usd` settings are all still respected in that mode.
