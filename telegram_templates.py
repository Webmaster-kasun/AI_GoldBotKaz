"""Telegram message templates for CPR Gold Bot — v1.0

All message strings live here.
bot.py and scheduler.py import from this module — no inline f-strings elsewhere.

Score breakdown shown in every signal/trade message:
  Main condition  (+2 or +1)
  SMA alignment   (+2 or +1 or +0)
  CPR width       (+2 or +1 or +0)
  Total: X/6 → $Y position

Usage:
    from telegram_templates import (
        msg_signal_update, msg_trade_opened, msg_breakeven,
        msg_trade_closed, msg_news_block, msg_news_penalty,
        msg_cooldown_started, msg_daily_cap, msg_spread_skip,
        msg_error, msg_order_failed, msg_friday_cutoff, msg_startup,
    )
"""

from __future__ import annotations

_DIV = "─" * 22


def _position_label(position_usd: int) -> str:
    if position_usd >= 100:
        return f"${position_usd} 🟢 Full"
    if position_usd >= 66:
        return f"${position_usd} 🟡 Partial"
    if position_usd >= 33:
        return f"${position_usd} 🔴 Small"
    return "No trade"


# ══════════════════════════════════════════════════════════════════════════════
# 1. Signal update  (sent when score or direction changes)
# ══════════════════════════════════════════════════════════════════════════════

def msg_signal_update(
    banner: str,
    session: str,
    direction: str,
    score: int,
    position_usd: int,
    cpr_width_pct: float,
    detail_lines: list[str],
    news_penalty: int = 0,
    raw_score: int | None = None,
) -> str:
    tradeable = direction != "NONE" and position_usd > 0
    status_line = "🎯 Signal confirmed — placing trade..." if tradeable else "⏳ Watching for breakout..."
    news_line   = f"📰 News penalty active ({news_penalty})\n" if news_penalty else ""
    score_str   = f"{score}/6"
    if raw_score is not None and news_penalty:
        score_str += f"  (raw {raw_score}, news {news_penalty:+d})"

    details = "\n".join(f"  {r}" for r in detail_lines)
    return (
        f"{banner} SESSION\n"
        f"📊 CPR Signal Update\n{_DIV}\n"
        f"Window:    {session}\n"
        f"Bias:      {direction}\n"
        f"Score:     {score_str}\n"
        f"Position:  {_position_label(position_usd)}\n"
        f"CPR Width: {cpr_width_pct:.2f}%\n"
        f"{_DIV}\n"
        f"{details}\n"
        f"{_DIV}\n"
        f"{news_line}"
        f"{status_line}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 2. New trade opened
# ══════════════════════════════════════════════════════════════════════════════

def msg_trade_opened(
    banner: str,
    direction: str,
    setup: str,
    session: str,
    fill_price: float,
    signal_price: float,
    sl_price: float,
    tp_price: float,
    sl_usd: float,
    tp_usd: float,
    units: float,
    position_usd: int,
    rr_ratio: float,
    cpr_width_pct: float,
    spread_pips: int,
    score: int,
    balance: float,
    demo: bool,
    news_penalty: int = 0,
    raw_score: int | None = None,
) -> str:
    slip     = fill_price - signal_price
    slip_str = f"  (signal ${signal_price:.2f}, slip ${slip:+.2f})" if abs(slip) > 0.005 else ""
    score_str = f"{score}/6"
    if raw_score is not None and news_penalty:
        score_str += f"  (raw {raw_score}, news {news_penalty:+d})"
    mode = "DEMO" if demo else "LIVE"
    return (
        f"{banner} 🥇 New Trade — {direction}\n{_DIV}\n"
        f"Setup:    {setup}\n"
        f"Window:   {session}\n"
        f"Fill:     ${fill_price:.2f}{slip_str}\n"
        f"SL:       ${sl_price:.2f}  (-${sl_usd:.2f})\n"
        f"TP:       ${tp_price:.2f}  (+${tp_usd:.2f})\n"
        f"Units:    {units}\n"
        f"Position: {_position_label(position_usd)}  (1:{rr_ratio:.0f})\n"
        f"CPR:      {cpr_width_pct:.2f}% width | Spread: {spread_pips} pips\n"
        f"Score:    {score_str}\n"
        f"Balance:  ${balance:.2f}\n"
        f"Mode:     {mode}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 3. Break-even activated
# ══════════════════════════════════════════════════════════════════════════════

def msg_breakeven(
    trade_id: str | int,
    direction: str,
    entry: float,
    trigger_price: float,
    trigger_usd: float,
    current_price: float,
    unrealized_pnl: float,
    demo: bool,
) -> str:
    mode = "DEMO" if demo else "LIVE"
    return (
        f"🔒 Break-Even Activated\n{_DIV}\n"
        f"Trade ID:  {trade_id}\n"
        f"Direction: {direction}\n"
        f"Entry:     ${entry:.2f}\n"
        f"Trigger:   ${trigger_price:.2f} (+${trigger_usd:.2f} move)\n"
        f"Price now: ${current_price:.2f}\n"
        f"PnL now:   ${unrealized_pnl:+.2f}\n"
        f"SL moved → entry (${entry:.2f})\n"
        f"Mode:      {mode}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 4. Trade closed
# ══════════════════════════════════════════════════════════════════════════════

def msg_trade_closed(
    trade_id: str | int,
    direction: str,
    setup: str,
    entry: float,
    close_price: float,
    pnl: float,
    session: str,
    demo: bool,
    duration_str: str = "",
) -> str:
    outcome = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")
    icon    = "✅" if pnl > 0 else ("❌" if pnl < 0 else "➡️")
    duration_line = f"Duration:  {duration_str}\n" if duration_str else ""
    mode    = "DEMO" if demo else "LIVE"
    return (
        f"{icon} Trade Closed — {outcome}\n{_DIV}\n"
        f"Trade ID:  {trade_id}\n"
        f"Direction: {direction}\n"
        f"Setup:     {setup}\n"
        f"Entry:     ${entry:.2f}\n"
        f"Close:     ${close_price:.2f}\n"
        f"PnL:       ${pnl:+.2f}\n"
        f"{duration_line}"
        f"Session:   {session}\n"
        f"Mode:      {mode}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 5. News hard block
# ══════════════════════════════════════════════════════════════════════════════

def msg_news_block(
    event_name: str,
    event_time_sgt: str,
    before_min: int,
    after_min: int,
) -> str:
    return (
        f"📰 News Block Active\n{_DIV}\n"
        f"Event:   {event_name}\n"
        f"Time:    {event_time_sgt} SGT\n"
        f"Window:  -{before_min}min → +{after_min}min\n"
        f"Action:  Hard block — no new entries\n"
        f"{_DIV}\n"
        f"⏳ Resuming {after_min} min after event"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 6. News soft penalty
# ══════════════════════════════════════════════════════════════════════════════

def msg_news_penalty(
    event_names: list[str],
    penalty: int,
    score_after: int,
    score_before: int,
    position_after: int,
    position_before: int,
) -> str:
    names = ", ".join(event_names) if event_names else "Medium event"
    count = len(event_names) if event_names else 1
    pos_change = (
        f"${position_before} → ${position_after}"
        if position_before != position_after
        else f"${position_after} (unchanged)"
    )
    return (
        f"📰 Soft News Penalty Active\n{_DIV}\n"
        f"Events:   {names}\n"
        f"Count:    {count} medium event(s)\n"
        f"Penalty:  {penalty} applied to score\n"
        f"Score:    {score_before}/6 → {score_after}/6\n"
        f"Position: {pos_change}\n"
        f"{_DIV}\n"
        f"{'⚠️ Trading continues with reduced size' if position_after > 0 else '⏳ Score below minimum — watching'}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 7. Loss cooldown started
# ══════════════════════════════════════════════════════════════════════════════

def msg_cooldown_started(
    streak: int,
    cooldown_until_sgt: str,
) -> str:
    return (
        f"🧊 Cooldown Started\n{_DIV}\n"
        f"Reason:   {streak} consecutive losses\n"
        f"Paused:   New entries only\n"
        f"Resumes:  {cooldown_until_sgt} SGT\n"
        f"{_DIV}\n"
        f"Existing trades continue to be managed"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 8. Daily / window cap reached
# ══════════════════════════════════════════════════════════════════════════════

def msg_daily_cap(
    cap_type: str,      # "losing_trades" | "total_trades" | "window"
    count: int,
    limit: int,
    window: str = "",
) -> str:
    if cap_type == "losing_trades":
        label  = "Max losing trades"
        action = "No new entries today"
        footer = "Bot resumes next trading day"
    elif cap_type == "total_trades":
        label  = "Max trades/day"
        action = "No new entries today"
        footer = "Bot resumes next trading day"
    else:
        label  = f"{window} window cap"
        action = f"No new entries in {window} window"
        footer = "Entries resume next window"

    return (
        f"🛑 Daily Cap Reached\n{_DIV}\n"
        f"Type:    {label}\n"
        f"Count:   {count}/{limit}\n"
        f"Action:  {action}\n"
        f"{_DIV}\n"
        f"{footer}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 9. Spread too wide
# ══════════════════════════════════════════════════════════════════════════════

def msg_spread_skip(
    banner: str,
    session_label: str,
    spread_pips: int,
    limit_pips: int,
) -> str:
    excess = spread_pips - limit_pips
    return (
        f"⚠️ Spread Too Wide — Skipping\n{_DIV}\n"
        f"Session:  {session_label}\n"
        f"Spread:   {spread_pips} pips\n"
        f"Limit:    {limit_pips} pips  (+{excess} over)\n"
        f"{_DIV}\n"
        f"Waiting for spread to normalise"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 10. Order placement failed
# ══════════════════════════════════════════════════════════════════════════════

def msg_order_failed(
    direction: str,
    instrument: str,
    units: float,
    error: str,
) -> str:
    return (
        f"❌ Order Failed\n{_DIV}\n"
        f"Direction: {direction}\n"
        f"Pair:      {instrument}\n"
        f"Units:     {units}\n"
        f"Error:     {error}\n"
        f"{_DIV}\n"
        f"Check OANDA account and logs"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 11. System errors
# ══════════════════════════════════════════════════════════════════════════════

def msg_error(
    error_type: str,
    detail: str = "",
) -> str:
    detail_line = f"Detail:  {detail}\n" if detail else ""
    return (
        f"❌ System Error\n{_DIV}\n"
        f"Type:    {error_type}\n"
        f"{detail_line}"
        f"{_DIV}\n"
        f"Check logs for full trace"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 12. Friday cutoff
# ══════════════════════════════════════════════════════════════════════════════

def msg_friday_cutoff(cutoff_hour_sgt: int) -> str:
    return (
        f"📅 Friday Cutoff Active\n{_DIV}\n"
        f"Time:    After {cutoff_hour_sgt:02d}:00 SGT Friday\n"
        f"Action:  No new entries\n"
        f"Reason:  Low gold liquidity end-of-week\n"
        f"{_DIV}\n"
        f"Bot resumes Monday 08:00 SGT"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 13. Bot startup
# ══════════════════════════════════════════════════════════════════════════════

def msg_startup(
    version: str,
    mode: str,
    balance: float,
    min_score: int,
) -> str:
    return (
        f"🚀 Bot Started — {version}\n{_DIV}\n"
        f"Mode:      {mode}\n"
        f"Balance:   ${balance:.2f}\n"
        f"Min score: {min_score}/6 to trade\n"
        f"Pair:      XAU/USD (M15)\n"
        f"Sizes:     $33 (score 2–3) | $66 (score 4–5) | $100 (score 6)\n"
        f"{_DIV}\n"
        f"Cycle: every 5 min ✅"
    )
