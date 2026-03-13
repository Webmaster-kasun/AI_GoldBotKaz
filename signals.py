"""
Gold Signal Engine — CPR + EMA + RSI + CPR Width + H4 Trend
=============================================================
Scoring (5 pts max):
  Check 1 — CPR Position  (0–2 pts): Price above TC=BUY, below BC=SELL
  Check 2 — EMA Alignment (0–1 pt):  H1 EMA20/EMA50 agree with direction
  Check 3 — RSI Momentum  (0–1 pt):  RSI > 55 BUY / RSI < 45 SELL
  Check 4 — CPR Width     (0–1 pt):  Width < 0.3% = trending day bonus

  H4 TREND FILTER (hard block):
    H4 EMA20 > EMA50 = only BUY signals allowed
    H4 EMA20 < EMA50 = only SELL signals allowed
    Prevents trading against the higher timeframe trend
"""

import os
import requests
import logging
from cpr import CPRCalculator

log = logging.getLogger(__name__)


class SignalEngine:
    def __init__(self):
        self.api_key  = os.environ.get("OANDA_API_KEY", "")
        self.base_url = "https://api-fxpractice.oanda.com"
        self.headers  = {"Authorization": "Bearer " + self.api_key}
        self.cpr      = CPRCalculator()

    def _fetch_candles(self, instrument, granularity, count=100):
        url    = self.base_url + "/v3/instruments/" + instrument + "/candles"
        params = {"count": str(count), "granularity": granularity, "price": "M"}
        for attempt in range(3):
            try:
                r = requests.get(url, headers=self.headers, params=params, timeout=10)
                if r.status_code == 200:
                    candles = r.json()["candles"]
                    c       = [x for x in candles if x["complete"]]
                    closes  = [float(x["mid"]["c"]) for x in c]
                    highs   = [float(x["mid"]["h"]) for x in c]
                    lows    = [float(x["mid"]["l"]) for x in c]
                    opens   = [float(x["mid"]["o"]) for x in c]
                    volumes = [int(x.get("volume", 0)) for x in c]
                    return closes, highs, lows, opens, volumes
                log.warning("Candle fetch attempt " + str(attempt+1) + " failed: " + str(r.status_code))
            except Exception as e:
                log.warning("Candle fetch error: " + str(e))
        return [], [], [], [], []

    def _ema(self, data, period):
        if not data or len(data) < period:
            avg = sum(data) / len(data) if data else 0
            return [avg] * max(len(data), 1)
        seed = sum(data[:period]) / period
        emas = [seed] * period
        mult = 2 / (period + 1)
        for p in data[period:]:
            emas.append((p - emas[-1]) * mult + emas[-1])
        return emas

    def _calc_rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        deltas   = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains    = [d if d > 0 else 0 for d in deltas[-period:]]
        losses   = [-d if d < 0 else 0 for d in deltas[-period:]]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 1)

    def analyze(self, asset="XAUUSD"):
        if asset == "XAUUSD_ASIAN":
            return self._analyze_gold_asian()
        return self._analyze_gold()

    # ══════════════════════════════════════════════════════════
    # MAIN GOLD ANALYSIS — London / NY sessions
    # ══════════════════════════════════════════════════════════
    def _analyze_gold(self):
        reasons   = []
        score     = 0
        direction = "NONE"

        h4_closes, _, _, _, _ = self._fetch_candles("XAU_USD", "H4", 60)
        h1_closes, _, _, _, _ = self._fetch_candles("XAU_USD", "H1", 60)

        if not h1_closes:
            return 0, "NONE", "No price data"

        price = h1_closes[-1]

        # ── H4 TREND FILTER (hard block) ─────────────────────
        h4_direction = "NONE"
        if len(h4_closes) >= 50:
            h4_ema20 = self._ema(h4_closes, 20)[-1]
            h4_ema50 = self._ema(h4_closes, 50)[-1]
            if h4_ema20 > h4_ema50:
                h4_direction = "BUY"
                log.info("H4 trend: BULLISH EMA20=" + str(round(h4_ema20,2)) + " > EMA50=" + str(round(h4_ema50,2)))
            elif h4_ema20 < h4_ema50:
                h4_direction = "SELL"
                log.info("H4 trend: BEARISH EMA20=" + str(round(h4_ema20,2)) + " < EMA50=" + str(round(h4_ema50,2)))
        else:
            log.warning("H4: not enough candles — trend filter skipped")

        # ── CHECK 1: CPR POSITION (0–2 pts) ──────────────────
        cpr = self.cpr.get_levels("XAU_USD")
        if not cpr:
            return 0, "NONE", "CPR levels unavailable"

        tc = cpr["tc"]
        bc = cpr["bc"]
        r1 = cpr["r1"]
        s1 = cpr["s1"]

        log.info("Gold CPR TC=" + str(tc) + " BC=" + str(bc) + " price=" + str(round(price,2)))

        if price > tc:
            direction = "BUY"
            score    += 2
            reasons.append("✅ Price " + str(round(price,2)) + " above TC=" + str(tc) + " (2 pts)")
        elif price < bc:
            direction = "SELL"
            score    += 2
            reasons.append("✅ Price " + str(round(price,2)) + " below BC=" + str(bc) + " (2 pts)")
        else:
            reasons.append("❌ Price inside CPR (" + str(bc) + "–" + str(tc) + ") — no trade")
            return 0, "NONE", " | ".join(reasons)

        # ── H4 HARD BLOCK — apply after direction is set ─────
        if h4_direction != "NONE" and direction != h4_direction:
            reasons.append(
                "🚫 H4 trend is " + h4_direction + " but CPR says " + direction +
                " — blocked (trading against trend)"
            )
            return score, "NONE", " | ".join(reasons)
        elif h4_direction != "NONE":
            reasons.append("✅ H4 trend " + h4_direction + " matches signal direction")

        # ── CHECK 2: EMA ALIGNMENT (0–1 pt) ──────────────────
        if len(h1_closes) >= 50:
            ema20 = self._ema(h1_closes, 20)[-1]
            ema50 = self._ema(h1_closes, 50)[-1]
            log.info("Gold EMA20=" + str(round(ema20,2)) + " EMA50=" + str(round(ema50,2)))

            if direction == "BUY" and price > ema20 and ema20 > ema50:
                score += 1
                reasons.append(
                    "✅ EMA OK: price > EMA20=" + str(round(ema20,2)) +
                    " > EMA50=" + str(round(ema50,2)) + " (1 pt)"
                )
            elif direction == "SELL" and price < ema20 and ema20 < ema50:
                score += 1
                reasons.append(
                    "✅ EMA OK: price < EMA20=" + str(round(ema20,2)) +
                    " < EMA50=" + str(round(ema50,2)) + " (1 pt)"
                )
            else:
                reasons.append(
                    "❌ EMA conflict: EMA20=" + str(round(ema20,2)) +
                    " EMA50=" + str(round(ema50,2)) + " (0 pts)"
                )
        else:
            reasons.append("❌ EMA: not enough H1 data (0 pts)")

        # ── CHECK 3: RSI MOMENTUM (0–1 pt) ───────────────────
        rsi_val = self._calc_rsi(h1_closes, 14)
        if rsi_val is not None:
            log.info("Gold RSI=" + str(rsi_val))
            if direction == "BUY" and rsi_val > 55:
                score += 1
                reasons.append("✅ RSI=" + str(rsi_val) + " > 55 — bullish momentum (1 pt)")
            elif direction == "SELL" and rsi_val < 45:
                score += 1
                reasons.append("✅ RSI=" + str(rsi_val) + " < 45 — bearish momentum (1 pt)")
            else:
                reasons.append("❌ RSI=" + str(rsi_val) + " — no momentum confirmation (0 pts)")
        else:
            reasons.append("❌ RSI: not enough data (0 pts)")

        # ── CHECK 4: CPR WIDTH BONUS (0–1 pt) ─────────────────
        cpr_width = float(cpr.get("width_pct", 999))
        if cpr_width < 0.3:
            score += 1
            reasons.append("✅ Narrow CPR=" + str(cpr_width) + "% < 0.3% — trending day (1 pt)")
        elif cpr_width > 0.6:
            reasons.append("❌ Wide CPR=" + str(cpr_width) + "% > 0.6% — choppy (0 pts)")
        else:
            reasons.append("❌ Normal CPR=" + str(cpr_width) + "% — no bonus (0 pts)")

        reasons.append("R1=" + str(r1) + " S1=" + str(s1))
        log.info("Gold score=" + str(score) + " direction=" + direction)
        return score, direction, " | ".join(reasons)

    # ══════════════════════════════════════════════════════════
    # ASIAN SESSION — same checks, lower threshold
    # ══════════════════════════════════════════════════════════
    def _analyze_gold_asian(self):
        reasons   = []
        score     = 0
        direction = "NONE"

        h4_closes, _, _, _, _ = self._fetch_candles("XAU_USD", "H4", 60)
        h1_closes, _, _, _, _ = self._fetch_candles("XAU_USD", "H1", 60)

        if not h1_closes:
            return 0, "NONE", "No price data"

        price = h1_closes[-1]

        # H4 trend direction
        h4_direction = "NONE"
        if len(h4_closes) >= 50:
            h4_ema20 = self._ema(h4_closes, 20)[-1]
            h4_ema50 = self._ema(h4_closes, 50)[-1]
            h4_direction = "BUY" if h4_ema20 > h4_ema50 else "SELL"

        # CHECK 1: CPR
        cpr = self.cpr.get_levels("XAU_USD")
        if not cpr:
            return 0, "NONE", "CPR unavailable"

        tc = cpr["tc"]
        bc = cpr["bc"]

        if price > tc:
            direction = "BUY"
            score    += 2
            reasons.append("✅ Price " + str(round(price,2)) + " above TC=" + str(tc) + " (2 pts)")
        elif price < bc:
            direction = "SELL"
            score    += 2
            reasons.append("✅ Price " + str(round(price,2)) + " below BC=" + str(bc) + " (2 pts)")
        else:
            reasons.append("❌ Price inside CPR — no direction")
            return 0, "NONE", " | ".join(reasons)

        # H4 hard block
        if h4_direction != "NONE" and direction != h4_direction:
            reasons.append("🚫 H4 trend " + h4_direction + " blocks " + direction + " signal")
            return score, "NONE", " | ".join(reasons)
        elif h4_direction != "NONE":
            reasons.append("✅ H4 trend " + h4_direction + " matches")

        # CHECK 2: EMA
        if len(h1_closes) >= 50:
            ema20 = self._ema(h1_closes, 20)[-1]
            ema50 = self._ema(h1_closes, 50)[-1]
            between = min(ema20, ema50) < price < max(ema20, ema50)
            if between:
                reasons.append("❌ Price between EMA20/EMA50 — conflict (0 pts)")
            elif direction == "BUY" and ema20 > ema50:
                score += 1
                reasons.append("✅ EMA uptrend (1 pt)")
            elif direction == "SELL" and ema20 < ema50:
                score += 1
                reasons.append("✅ EMA downtrend (1 pt)")
            else:
                reasons.append("❌ EMA mismatch (0 pts)")

        # CHECK 3: RSI
        rsi_val = self._calc_rsi(h1_closes, 14)
        if rsi_val is not None:
            if direction == "BUY" and rsi_val > 52:
                score += 1
                reasons.append("✅ RSI=" + str(rsi_val) + " > 52 — bullish (1 pt)")
            elif direction == "SELL" and rsi_val < 48:
                score += 1
                reasons.append("✅ RSI=" + str(rsi_val) + " < 48 — bearish (1 pt)")
            else:
                reasons.append("❌ RSI=" + str(rsi_val) + " — neutral (0 pts)")
        else:
            reasons.append("❌ RSI: not enough data (0 pts)")

        # CHECK 4: CPR Width bonus
        cpr_width = float(cpr.get("width_pct", 999))
        if cpr_width < 0.3:
            score += 1
            reasons.append("✅ Narrow CPR=" + str(cpr_width) + "% — trending bonus (1 pt)")
        else:
            reasons.append("❌ CPR=" + str(cpr_width) + "% — no bonus (0 pts)")

        reasons.append("R1=" + str(cpr["r1"]) + " S1=" + str(cpr["s1"]))
        log.info("Gold Asian score=" + str(score) + " direction=" + direction)
        return score, direction, " | ".join(reasons)
