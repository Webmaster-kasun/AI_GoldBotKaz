"""
AI Reasoning Layer — Claude-powered trade filter
=================================================
Sits between signal scoring and order placement.
Called only when score >= threshold (signal already passed 7-check system).

What it does:
  - Reads recent candle direction, momentum, losses today, price zone history
  - Reasons like a senior trader: "does this trade make sense RIGHT NOW?"
  - Returns: decision (YES/NO/REDUCE), confidence (LOW/MEDIUM/HIGH), reason, lot_multiplier
  - On HIGH confidence: increases lot size (up to 3x)
  - On LOW confidence: blocks the trade entirely
  - Knows you are a day trader expecting 5-8 trades per day — will not over-block

Lot sizing tiers:
  HIGH   confidence + score 7/7 = 3x units
  HIGH   confidence + score 6/7 = 2x units
  MEDIUM confidence             = 1x units (normal)
  LOW    confidence             = BLOCK trade
"""

import os
import json
import logging
import requests
import time

log = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


def _call_claude(prompt: str) -> str:
    """Call Claude API and return the text response."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set — AI reasoning skipped, trade allowed")
        return '{"decision":"YES","confidence":"MEDIUM","reason":"API key not configured","lot_multiplier":1}'

    headers = {
        "x-api-key":         api_key,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }

    body = {
        "model":      "claude-sonnet-4-20250514",
        "max_tokens": 300,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }

    for attempt in range(3):
        try:
            time.sleep(0.5)
            r = requests.post(ANTHROPIC_API_URL, headers=headers, json=body, timeout=20)
            if r.status_code == 200:
                data    = r.json()
                content = data.get("content", [])
                if content and content[0].get("type") == "text":
                    return content[0]["text"].strip()
            log.warning("Claude API attempt " + str(attempt+1) + " failed: " + str(r.status_code))
        except Exception as e:
            log.warning("Claude API error attempt " + str(attempt+1) + ": " + str(e))
        time.sleep(2)

    log.warning("Claude API failed after 3 attempts — trade allowed with normal size")
    return '{"decision":"YES","confidence":"MEDIUM","reason":"API unavailable after retries","lot_multiplier":1}'


def _build_prompt(
    direction:       str,
    score:           int,
    price:           float,
    signal_details:  str,
    wins_today:      int,
    losses_today:    int,
    last_loss_price: float,
    last_loss_dir:   str,
    recent_candles:  list,
    session:         str,
    h4_trend:        str,
    is_asian:        bool,
) -> str:
    """Build the reasoning prompt for Claude."""

    candle_summary = ""
    if recent_candles:
        directions = []
        for i in range(1, min(len(recent_candles), 6)):
            move = recent_candles[i] - recent_candles[i-1]
            directions.append("UP" if move > 0 else "DOWN")
        candle_summary = " -> ".join(directions)

    last_loss_info = "None today"
    if last_loss_price and last_loss_dir:
        price_gap = abs(price - last_loss_price) / 0.01
        last_loss_info = (
            last_loss_dir + " at " + str(last_loss_price) +
            " (current price is " + str(round(price_gap)) + " pips away)"
        )

    prompt = """You are a senior gold (XAU/USD) day trader reviewing a trade signal before it is placed.
You must respond ONLY with a single valid JSON object, no explanation, no markdown.

TRADER PROFILE:
- Day trader targeting 5-8 trades per day on XAU/USD
- Demo account collecting data and learning
- Strategy: CPR breakout with H4 trend filter, EMA, RSI, ATR
- Risk per trade: ~$10-15 USD, lot size 1-3 units

CURRENT SIGNAL:
- Direction: """ + direction + """
- Score: """ + str(score) + """/7
- Entry price: """ + str(price) + """
- Session: """ + session + """
- H4 trend: """ + h4_trend + """
- Is Asian session: """ + str(is_asian) + """
- Signal details: """ + signal_details[:300] + """

TODAY SO FAR:
- Wins: """ + str(wins_today) + """
- Losses: """ + str(losses_today) + """
- Last loss: """ + last_loss_info + """
- Recent H1 candle direction (oldest to newest): """ + (candle_summary if candle_summary else "unavailable") + """

YOUR TASK:
Decide if this trade should be placed. Consider:
1. Is the signal direction aligned with recent candle momentum?
2. Is price re-entering the same zone as a recent loss?
3. Does H4 trend support this direction?
4. Is it reasonable to take another trade given today's win/loss count?
5. Remember: the trader WANTS to trade (5-8 per day). Only block if there is a CLEAR reason.

Respond with ONLY this JSON (no other text):
{
  "decision": "YES" or "NO",
  "confidence": "HIGH" or "MEDIUM" or "LOW",
  "reason": "one sentence explanation max 20 words",
  "lot_multiplier": 1 or 2 or 3
}

Rules for lot_multiplier:
- 3 only if decision=YES and confidence=HIGH and score=7
- 2 only if decision=YES and confidence=HIGH and score>=6
- 1 for all other YES decisions
- 1 for NO decisions (will be ignored anyway)"""

    return prompt


def ai_should_trade(
    direction:       str,
    score:           int,
    price:           float,
    signal_details:  str,
    wins_today:      int,
    losses_today:    int,
    last_loss_price: float,
    last_loss_dir:   str,
    recent_candles:  list,
    session:         str,
    h4_trend:        str,
    is_asian:        bool = False,
) -> dict:
    """
    Main entry point. Returns dict:
    {
        "allow":          True/False,
        "confidence":     "HIGH"/"MEDIUM"/"LOW",
        "reason":         "explanation string",
        "lot_multiplier": 1/2/3
    }
    """
    try:
        prompt = _build_prompt(
            direction       = direction,
            score           = score,
            price           = price,
            signal_details  = signal_details,
            wins_today      = wins_today,
            losses_today    = losses_today,
            last_loss_price = last_loss_price,
            last_loss_dir   = last_loss_dir,
            recent_candles  = recent_candles,
            session         = session,
            h4_trend        = h4_trend,
            is_asian        = is_asian,
        )

        raw = _call_claude(prompt)
        log.info("AI raw response: " + raw[:200])

        # Strip markdown fences if model added them
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        clean = clean.strip()

        result = json.loads(clean)

        decision       = result.get("decision", "YES").upper()
        confidence     = result.get("confidence", "MEDIUM").upper()
        reason         = result.get("reason", "No reason provided")
        lot_multiplier = int(result.get("lot_multiplier", 1))

        # Safety clamp
        lot_multiplier = max(1, min(3, lot_multiplier))

        # LOW confidence always blocks
        if confidence == "LOW":
            decision = "NO"

        allow = (decision == "YES")

        log.info(
            "AI DECISION: " + decision +
            " | confidence=" + confidence +
            " | lot_multiplier=" + str(lot_multiplier) +
            " | reason=" + reason
        )

        return {
            "allow":          allow,
            "confidence":     confidence,
            "reason":         reason,
            "lot_multiplier": lot_multiplier if allow else 1,
        }

    except Exception as e:
        log.warning("AI reasoning error: " + str(e) + " — trade allowed with normal size")
        return {
            "allow":          True,
            "confidence":     "MEDIUM",
            "reason":         "AI error — defaulting to allow",
            "lot_multiplier": 1,
        }
