"""AI reasoning layer for CPR Gold Bot.

Calls Claude (claude-sonnet-4-20250514) via the Anthropic Messages API to
decide whether the bot should enter a trade given current market context.

Returns a dict:
    {
        "allow":      bool,   # True  -> proceed with trade
        "reason":     str,    # human-readable explanation
        "confidence": str,    # "high" | "medium" | "low"
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
_MODEL   = "claude-sonnet-4-20250514"
_TIMEOUT = 20  # seconds


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
    Falls back to allow=True on any API error so the bot keeps running.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set -- AI reasoning skipped, allowing trade.")
        return {"allow": True, "reason": "AI reasoning skipped (no API key)", "confidence": "low"}

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

    system_prompt = (
        "You are a professional XAU/USD (gold) trading risk manager. "
        "You receive structured trade context from an algorithmic bot and decide "
        "whether the trade should be allowed.\n\n"
        "Rules:\n"
        "- Block the trade if direction contradicts the H4 trend strongly.\n"
        "- Block if losses_today >= 3 (daily loss limit).\n"
        "- Block if the last loss and this trade share the same direction and "
        "  the price is very close to the last losing entry (revenge trade risk).\n"
        "- Block during Asian session (is_asian=true) unless score >= 5.\n"
        "- Allow if score >= 5 and context looks clean.\n\n"
        "Respond ONLY with a JSON object -- no markdown, no explanation outside JSON:\n"
        '{"allow": true|false, "reason": "...", "confidence": "high"|"medium"|"low"}'
    )

    user_message = (
        f"Trade context:\n{json.dumps(context, indent=2)}\n\n"
        "Should the bot take this trade?"
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

        log.info(
            "AI reasoning: allow=%s reason=%s confidence=%s",
            result["allow"], result.get("reason"), result.get("confidence"),
        )
        return result

    except requests.exceptions.Timeout:
        log.warning("AI reasoning timed out -- allowing trade as fallback.")
        return {"allow": True, "reason": "AI timeout -- fallback allow", "confidence": "low"}

    except Exception as exc:
        log.warning("AI reasoning error (%s) -- allowing trade as fallback.", exc)
        return {"allow": True, "reason": f"AI error: {exc}", "confidence": "low"}
