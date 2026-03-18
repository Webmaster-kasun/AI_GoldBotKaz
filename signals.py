"""Signal engine for CPR breakout detection on XAU/USD — v1.1

Scoring (Bull):
  Main condition  — price above CPR/PDH/R1: +2 | above R2 (extended): +1
  SMA alignment   — both SMA20 & SMA50 below price: +2 | one below: +1
  CPR width       — < 0.5% (narrow): +2 | 0.5%–1.0% (moderate): +1

Scoring (Bear):
  Main condition  — price below CPR/PDL/S1: +2 | below S2 (extended): +1
  SMA alignment   — both SMA20 & SMA50 above price: +2 | one above: +1
  CPR width       — same as Bull

Position size by score:
  score 5–6  →  $100 (full)
  score 3–4  →  $66  (half)
  score < 3  →  no trade — walk away

SL priority (per signal):
  1. Below/above CPR level if CPR is within 0.25% of entry
  2. Fixed 0.25% otherwise

TP priority (per signal):
  1. R1/S1 level if it falls in 0.50%–0.75% range from entry
  2. Fixed 0.75% if R1/S1 is too far (> 0.75%)
  3. Skip trade if R1/S1 is too close (< 0.50%) — not enough room

Non-negotiable rule: R:R must be ≥ 1:2 (TP ≥ 2× SL). Trade is skipped if not met.
"""

import time
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from config_loader import load_secrets

log = logging.getLogger(__name__)

# ── Position size tiers ────────────────────────────────────────────────────────
# score 5–6 → $100 (full)   | score 3–4 → $66 (half)   | score < 3 → no trade
_SIZE_TIERS = [
    (4, 100),   # score >= 5 → $100
    (2, 66),    # score >= 3 → $66
    # score <= 2 → 0 (no trade — walk away)
]

# Minimum score required to trade (scores below this are discarded)
MIN_TRADE_SCORE = 3


def score_to_position_usd(score: int) -> int:
    """Return the risk-dollar position size for a given score.

    Returns 0 (no trade) for any score below MIN_TRADE_SCORE (3).
    """
    for threshold, size in _SIZE_TIERS:
        if score > threshold:
            return size
    return 0


class SignalEngine:
    def __init__(self, demo: bool = True):
        secrets = load_secrets()
        self.api_key = secrets.get("OANDA_API_KEY", "")
        self.account_id = secrets.get("OANDA_ACCOUNT_ID", "")
        self.base_url = (
            "https://api-fxpractice.oanda.com" if demo else "https://api-fxtrade.oanda.com"
        )
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self.session = requests.Session()
        retries = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def analyze(self, asset: str = "XAUUSD"):
        """Run the v1.0 CPR scoring engine.

        Returns
        -------
        (score, direction, details, levels, position_usd)
        """
        if asset != "XAUUSD":
            return 0, "NONE", "Only XAUUSD supported in this version", {}, 0

        instrument = "XAU_USD"

        # ── Daily candles → CPR levels ─────────────────────────────────────
        daily_closes, daily_highs, daily_lows = self._fetch_candles(instrument, "D", 3)
        if len(daily_closes) < 2:
            return 0, "NONE", "Not enough daily data for CPR", {}, 0

        prev_high  = daily_highs[-2]
        prev_low   = daily_lows[-2]
        prev_close = daily_closes[-2]

        pivot      = (prev_high + prev_low + prev_close) / 3
        bc         = (prev_high + prev_low) / 2
        tc         = (pivot - bc) + pivot
        daily_range = prev_high - prev_low
        r1         = (2 * pivot) - prev_low
        r2         = pivot + daily_range
        s1         = (2 * pivot) - prev_high
        s2         = pivot - daily_range
        pdh        = prev_high
        pdl        = prev_low
        cpr_width_pct = abs(tc - bc) / pivot * 100

        levels = {
            "pivot":         round(pivot, 2),
            "tc":            round(tc, 2),
            "bc":            round(bc, 2),
            "r1":            round(r1, 2),
            "r2":            round(r2, 2),
            "s1":            round(s1, 2),
            "s2":            round(s2, 2),
            "pdh":           round(pdh, 2),
            "pdl":           round(pdl, 2),
            "cpr_width_pct": round(cpr_width_pct, 3),
        }

        # ── M15 candles → price, SMA, ATR ─────────────────────────────────
        m15_closes, m15_highs, m15_lows = self._fetch_candles(instrument, "M15", 65)
        if len(m15_closes) < 52:
            return 0, "NONE", "Not enough M15 data (need 52 candles for SMA50)", levels, 0

        current_close = m15_closes[-1]

        # SMA20 and SMA50 use the last 20/50 completed candles (exclude current)
        sma20 = sum(m15_closes[-21:-1]) / 20
        sma50 = sum(m15_closes[-51:-1]) / 50

        # ATR(14) — used by bot.py for SL sizing, not for scoring
        atr_val = self._atr(m15_highs, m15_lows, m15_closes, 14)
        levels["atr"]          = round(atr_val, 2) if atr_val else None
        levels["current_price"] = round(current_close, 2)
        levels["sma20"]         = round(sma20, 2)
        levels["sma50"]         = round(sma50, 2)

        # ── Scoring ────────────────────────────────────────────────────────
        score     = 0
        direction = "NONE"
        reasons   = []

        reasons.append(
            f"CPR TC={tc:.2f} BC={bc:.2f} width={cpr_width_pct:.2f}% | "
            f"R1={r1:.2f} R2={r2:.2f} S1={s1:.2f} S2={s2:.2f} | "
            f"PDH={pdh:.2f} PDL={pdl:.2f}"
        )

        # ── 1. Main condition ──────────────────────────────────────────────
        if current_close > tc:
            direction = "BUY"
            if current_close > r2:
                score += 1
                setup = "R2 Extended Breakout"
                reasons.append(
                    f"⚠️ Price {current_close:.2f} > R2={r2:.2f} — extended entry (+1, main condition)"
                )
            else:
                score += 2
                if current_close > r1:
                    setup = "R1 Breakout"
                elif current_close > pdh:
                    setup = "PDH Breakout"
                else:
                    setup = "CPR Bull Breakout"
                reasons.append(
                    f"✅ Price {current_close:.2f} above CPR/PDH/R1 zone [{setup}] (+2, main condition)"
                )
        elif current_close < bc:
            direction = "SELL"
            if current_close < s2:
                score += 1
                setup = "S2 Extended Breakdown"
                reasons.append(
                    f"⚠️ Price {current_close:.2f} < S2={s2:.2f} — extended entry (+1, main condition)"
                )
            else:
                score += 2
                if current_close < s1:
                    setup = "S1 Breakdown"
                elif current_close < pdl:
                    setup = "PDL Breakdown"
                else:
                    setup = "CPR Bear Breakdown"
                reasons.append(
                    f"✅ Price {current_close:.2f} below CPR/PDL/S1 zone [{setup}] (+2, main condition)"
                )
        else:
            reasons.append(
                f"❌ Price {current_close:.2f} inside CPR (TC={tc:.2f} BC={bc:.2f}) — no signal"
            )
            return 0, "NONE", " | ".join(reasons), levels, 0

        # ── 2. SMA alignment ───────────────────────────────────────────────
        if direction == "BUY":
            both_below = sma20 < current_close and sma50 < current_close
            one_below  = (sma20 < current_close) != (sma50 < current_close)
            if both_below:
                score += 2
                reasons.append(
                    f"✅ Both SMAs below price — SMA20={sma20:.2f} SMA50={sma50:.2f} (+2)"
                )
            elif one_below:
                score += 1
                which = "SMA20" if sma20 < current_close else "SMA50"
                reasons.append(
                    f"⚠️ Only {which} below price — SMA20={sma20:.2f} SMA50={sma50:.2f} (+1)"
                )
            else:
                reasons.append(
                    f"❌ Both SMAs above price — SMA20={sma20:.2f} SMA50={sma50:.2f} (+0)"
                )
        else:  # SELL
            both_above = sma20 > current_close and sma50 > current_close
            one_above  = (sma20 > current_close) != (sma50 > current_close)
            if both_above:
                score += 2
                reasons.append(
                    f"✅ Both SMAs above price — SMA20={sma20:.2f} SMA50={sma50:.2f} (+2)"
                )
            elif one_above:
                score += 1
                which = "SMA20" if sma20 > current_close else "SMA50"
                reasons.append(
                    f"⚠️ Only {which} above price — SMA20={sma20:.2f} SMA50={sma50:.2f} (+1)"
                )
            else:
                reasons.append(
                    f"❌ Both SMAs below price — SMA20={sma20:.2f} SMA50={sma50:.2f} (+0)"
                )

        # ── 3. CPR width ───────────────────────────────────────────────────
        if cpr_width_pct < 0.5:
            score += 2
            reasons.append(f"✅ Narrow CPR ({cpr_width_pct:.2f}% < 0.5%) (+2)")
        elif cpr_width_pct <= 1.0:
            score += 1
            reasons.append(f"⚠️ Moderate CPR ({cpr_width_pct:.2f}% in 0.5–1.0%) (+1)")
        else:
            reasons.append(f"❌ Wide CPR ({cpr_width_pct:.2f}% > 1.0%) (+0)")

        # ── Position size ──────────────────────────────────────────────────
        position_usd = score_to_position_usd(score)

        # ── SL recommendation (priority order) ────────────────────────────
        # 1. Use CPR structural level if it is within 0.25% of entry
        # 2. Fall back to fixed 0.25% percentage SL
        entry = current_close
        if direction == "BUY":
            cpr_sl_candidate = bc          # below the bottom CPR for longs
            cpr_dist_pct = (entry - cpr_sl_candidate) / entry * 100
        else:
            cpr_sl_candidate = tc          # above the top CPR for shorts
            cpr_dist_pct = (cpr_sl_candidate - entry) / entry * 100

        fixed_sl_pct  = 0.25
        if cpr_dist_pct <= fixed_sl_pct:
            sl_pct_used  = round(cpr_dist_pct, 4)
            sl_source    = "below_cpr" if direction == "BUY" else "above_cpr"
        else:
            sl_pct_used  = fixed_sl_pct
            sl_source    = "fixed_pct"
        sl_usd_rec = round(entry * sl_pct_used / 100, 2)

        # ── TP recommendation (priority order) ────────────────────────────
        # 1. Use R1/S1 if it falls in 0.50%–0.75% from entry
        # 2. Use fixed 0.75% if R1/S1 is too far (> 0.75%)
        # 3. Skip trade if R1/S1 is too close (< 0.50%) — not enough room
        target_level = r1 if direction == "BUY" else s1
        if direction == "BUY":
            level_dist_pct = (target_level - entry) / entry * 100
        else:
            level_dist_pct = (entry - target_level) / entry * 100

        tp_skip = False
        if 0.50 <= level_dist_pct <= 0.75:
            tp_pct_used = round(level_dist_pct, 4)
            tp_source   = "r1_level" if direction == "BUY" else "s1_level"
        elif level_dist_pct > 0.75:
            tp_pct_used = 0.75
            tp_source   = "fixed_pct"
        else:
            # R1/S1 too close — skip trade
            tp_pct_used = level_dist_pct
            tp_source   = "too_close_skip"
            tp_skip     = True
        tp_usd_rec = round(entry * tp_pct_used / 100, 2)

        # ── R:R guard — skip if R:R < 1:2 ────────────────────────────────
        rr_ratio = (tp_usd_rec / sl_usd_rec) if sl_usd_rec > 0 else 0
        rr_skip  = rr_ratio < 2.0

        if tp_skip or rr_skip:
            skip_reason = (
                f"R1/S1 too close ({level_dist_pct:.2f}% from entry — min 0.50% required)"
                if tp_skip
                else f"R:R {rr_ratio:.2f} < 1:2 — skip trade"
            )
            reasons.append(f"🚫 {skip_reason}")
            details = " | ".join(reasons)
            log.info(
                "CPR signal SKIPPED | setup=%s | dir=%s | score=%s | reason=%s",
                setup, direction, score, skip_reason,
            )
            return 0, "NONE", details, levels, 0

        levels["score"]        = score
        levels["position_usd"] = position_usd
        levels["entry"]        = round(entry, 2)
        levels["setup"]        = setup
        levels["sl_usd_rec"]   = sl_usd_rec
        levels["sl_source"]    = sl_source
        levels["sl_pct_used"]  = sl_pct_used
        levels["tp_usd_rec"]   = tp_usd_rec
        levels["tp_source"]    = tp_source
        levels["tp_pct_used"]  = tp_pct_used
        levels["rr_ratio"]     = round(rr_ratio, 2)

        reasons.append(
            f"📐 SL={sl_usd_rec} ({sl_source} {sl_pct_used:.3f}%) | "
            f"TP={tp_usd_rec} ({tp_source} {tp_pct_used:.3f}%) | R:R 1:{rr_ratio:.1f}"
        )

        details = " | ".join(reasons)
        log.info(
            "CPR signal | setup=%s | dir=%s | score=%s/6 | position=$%s",
            setup, direction, score, position_usd,
        )
        return score, direction, details, levels, position_usd

    # ── Data helpers ───────────────────────────────────────────────────────────

    def _fetch_candles(self, instrument: str, granularity: str, count: int = 60):
        url    = f"{self.base_url}/v3/instruments/{instrument}/candles"
        params = {"count": str(count), "granularity": granularity, "price": "M"}
        for attempt in range(3):
            try:
                r = self.session.get(url, headers=self.headers, params=params, timeout=15)
                if r.status_code == 200:
                    candles  = r.json().get("candles", [])
                    complete = [c for c in candles if c.get("complete")]
                    closes   = [float(c["mid"]["c"]) for c in complete]
                    highs    = [float(c["mid"]["h"]) for c in complete]
                    lows     = [float(c["mid"]["l"]) for c in complete]
                    return closes, highs, lows
                log.warning("Fetch candles %s %s: HTTP %s", instrument, granularity, r.status_code)
            except Exception as e:
                log.warning(
                    "Fetch candles error (%s %s) attempt %s: %s",
                    instrument, granularity, attempt + 1, e,
                )
            time.sleep(1)
        return [], [], []

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> float | None:
        """Return the most recent ATR value, or None if insufficient data."""
        n = len(closes)
        if n < period + 2 or len(highs) < n or len(lows) < n:
            return None
        trs = [
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(1, n)
        ]
        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr
