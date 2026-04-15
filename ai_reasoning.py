"""AI reasoning layer for CPR Gold Bot — v5.3 FIXED.

KEY FIXES vs v5.2:
  - Timeout / API error now ALLOWS the trade (fail-open) instead of blocking it.
    Previously a 20-second timeout caused silent trade losses because the bot
    blocked every valid signal during network hiccups.
  - Revised system prompt: removed the hard Asian-session block (score >= 5 rule
    already enforced in bot.py session_thresholds), removed overly cautious bias.
  - Added `lot_multiplier` to response schema so HIGH confidence can scale up.
  - Model updated to claude-sonnet-4-5 (latest available in this environment).

Returns a dict:
    {
        "allow":          bool,   # True  -> proceed with trade
        "reason":         str,    # human-readable explanation
        "confidence":     str,    # "high" | "medium" | "low"
        "lot_multiplier": int,    # 1 (normal) | 2 (high confidence) | 3 (very high)
    }

Environment variables required:
    ANTHROPIC_API_KEY  -- your Anthropic API key
"""

import json
import logging
import os

import requests

log = logging.getLogger(__name__)

_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL   = "claude-sonnet-4-5"
_TIMEOUT = 15  # seconds — reduced so a slow response doesn't hold up the cycle


def ai_should_trade(
    *,
    direction: str,
    score: float,
    price: float,
    signal_details: str,
    wins_today: int,
    losses_today: int,
    last_loss_entry: float,
    last_loss_exit: float,
    last_loss_dir: str,
    last_win_exit: float,
    recent_candles: list,
    session: str,
    h4_trend: str,
    is_asian: bool,
) -> dict:
    """Ask Claude whether to allow this trade.

    All keyword arguments are required. Returns allow/reason/confidence dict.
    Falls back to allow=True on any API error (fail-open) so the bot keeps
    running even when the AI layer is unavailable.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set — AI reasoning skipped, allowing trade.")
        return {
            "allow": True,
            "reason": "AI reasoning skipped (no API key)",
            "confidence": "medium",
            "lot_multiplier": 1,
        }

    context = {
        "direction":       direction,
        "score":           score,
        "price":           price,
        "signal_details":  signal_details,
        "wins_today":      wins_today,
        "losses_today":    losses_today,
        "last_loss_entry": last_loss_entry,
        "last_loss_exit":  last_loss_exit,
        "last_loss_dir":   last_loss_dir,
        "last_win_exit":   last_win_exit,
        "recent_candles":  recent_candles,
        "session":         session,
        "h4_trend":        h4_trend,
        "is_asian":        is_asian,
    }

    # v5.3 FIX: Revised system prompt.
    # - Removed hard Asian block (handled by session_thresholds in bot.py)
    # - Removed "fail deny on error" bias — AI is a SECONDARY filter, not primary
    # - Emphasised the H4 trend filter is already applied upstream
    # - Added lot_multiplier to response to allow AI to scale up on conviction
    system_prompt = (
        "You are a secondary risk filter for an algorithmic XAU/USD (gold) CPR breakout bot.\n\n"
        "IMPORTANT: The primary signal engine has ALREADY applied:\n"
        "  - H1 and H4 EMA trend filters (direction must align with macro trend)\n"
        "  - CPR breakout scoring (minimum score threshold already enforced)\n"
        "  - News blackout windows\n"
        "  - Spread and margin checks\n\n"
        "Your role is to BLOCK only clear edge-case risks that the rules miss:\n"
        "  1. BLOCK if this looks like a revenge trade: same direction as the last loss,\n"
        "     AND price is within 0.3% of the last losing entry.\n"
        "  2. BLOCK if losses_today >= 3 AND score < 5 (protect against tilt trading).\n"
        "  3. BLOCK if direction directly contradicts h4_trend with HIGH conviction\n"
        "     (e.g. SELL when h4_trend is strongly BULLISH and price is making new highs).\n"
        "  4. ALLOW if score >= 5 and the setup looks clean — do NOT second-guess.\n"
        "  5. ALLOW on any uncertainty — the upstream filters are conservative enough.\n\n"
        "Set lot_multiplier=2 only if score=6 AND h4_trend strongly aligns AND losses_today=0.\n"
        "Otherwise lot_multiplier=1.\n\n"
        "Respond ONLY with a JSON object — no markdown, no explanation outside JSON:\n"
        '{"allow": true|false, "reason": "...", "confidence": "high"|"medium"|"low", "lot_multiplier": 1}'
    )

    user_message = (
        f"Trade context:\n{json.dumps(context, indent=2)}\n\n"
        "Should the bot take this trade? Reply with JSON only."
    )

    payload = {
        "model":      _MODEL,
        "max_tokens": 256,
        "system":     system_prompt,
        "messages":   [{"role": "user", "content": user_message}],
    }

    headers = {
        "x-api-key":         api_key,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }

    try:
        resp = requests.post(_API_URL, json=payload, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        raw  = data["content"][0]["text"].strip()

        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        result = json.loads(raw)

        if not isinstance(result.get("allow"), bool):
            raise ValueError(f"Unexpected AI response shape: {result}")

        # Ensure lot_multiplier is present and within bounds
        lm = int(result.get("lot_multiplier", 1))
        result["lot_multiplier"] = max(1, min(3, lm))

        log.info(
            "AI reasoning: allow=%s reason=%s confidence=%s lot_multiplier=%dx",
            result["allow"], result.get("reason"),
            result.get("confidence"), result["lot_multiplier"],
        )
        return result

    except requests.exceptions.Timeout:
        # v5.3 FIX: fail-OPEN on timeout (was fail-deny).
        # A slow network should not block a valid trade that passed all upstream filters.
        log.warning("AI reasoning timed out — allowing trade (fail-open).")
        return {
            "allow": True,
            "reason": "AI timeout — fail-open (upstream filters passed)",
            "confidence": "low",
            "lot_multiplier": 1,
        }

    except Exception as exc:
        # v5.3 FIX: fail-OPEN on any error (was fail-deny).
        log.warning("AI reasoning error (%s) — allowing trade (fail-open).", exc)
        return {
            "allow": True,
            "reason": f"AI error: {exc} — fail-open (upstream filters passed)",
            "confidence": "low",
            "lot_multiplier": 1,
        }
