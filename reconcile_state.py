"""
Startup/runtime reconciliation helpers for the CPR Gold Bot.
Broker state is treated as the source of truth for open positions/trades.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def reconcile_runtime_state(trader, history: list, instrument: str, now_sgt, alert=None) -> dict:
    """
    Reconcile local history with broker truth.

    What it does:
    - detects currently open trades at the broker
    - inserts a recovered FILLED record into history if an open broker trade
      exists but local history does not know about it
    - back-fills realized P&L on local FILLED trades that are now closed
    - returns a summary for logging/decision-making
    """
    summary = {
        "open_trade_ids": [],
        "open_trade_count": 0,
        "recovered_trade_ids": [],
        "backfilled_trade_ids": [],
        "recent_closed_count": 0,
    }

    try:
        open_trades = trader.get_open_trades(instrument)
    except Exception as exc:
        log.warning("Could not fetch open trades during reconciliation: %s", exc)
        open_trades = []

    summary["open_trade_ids"] = [str(t.get("id")) for t in open_trades if t.get("id")]
    summary["open_trade_count"] = len(summary["open_trade_ids"])

    local_trade_ids = {
        str(t.get("trade_id")) for t in history
        if t.get("status") == "FILLED" and t.get("trade_id") is not None
    }

    for trade in open_trades:
        trade_id = str(trade.get("id", "")).strip()
        if not trade_id or trade_id in local_trade_ids:
            continue

        current_units = _safe_float(trade.get("currentUnits"))
        direction = "BUY" if current_units > 0 else "SELL"
        entry = _safe_float(trade.get("price"))
        recovered = {
            "timestamp_sgt": now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "RECOVERED",
            "instrument": instrument,
            "direction": direction,
            "setup": "startup_reconciled",
            "session": "Recovered",
            "macro_session": "Recovered",
            "score": None,
            "threshold": None,
            "entry": round(entry, 2) if entry > 0 else None,
            "sl_price": None,
            "tp_price": None,
            "size": abs(current_units),
            "cpr_width_pct": None,
            "estimated_risk_usd": None,
            "estimated_reward_usd": None,
            "spread_pips": None,
            "stop_pips": None,
            "tp_pips": None,
            "levels": {"source": "broker_reconciliation"},
            "details": "Recovered from broker openTrades during startup/runtime reconciliation.",
            "trade_id": trade_id,
            "status": "FILLED",
            "realized_pnl_usd": None,
            "breakeven_moved": False,
        }
        history.append(recovered)
        summary["recovered_trade_ids"].append(trade_id)
        local_trade_ids.add(trade_id)
        log.warning("Recovered open broker trade into local history: %s", trade_id)

    try:
        recent_closed = trader.get_recent_closed_trades(instrument, count=25)
        summary["recent_closed_count"] = len(recent_closed)
    except Exception as exc:
        log.warning("Could not fetch recent closed trades during reconciliation: %s", exc)
        recent_closed = []

    pnl_by_trade_id = {}
    for trade in recent_closed:
        trade_id = str(trade.get("id", "")).strip()
        if not trade_id:
            continue
        pnl = trade.get("realizedPL")
        if pnl is not None:
            pnl_by_trade_id[trade_id] = _safe_float(pnl)

    open_trade_ids = set(summary["open_trade_ids"])
    for item in history:
        if item.get("status") != "FILLED":
            continue
        trade_id = str(item.get("trade_id", "")).strip()
        if not trade_id or trade_id in open_trade_ids:
            continue
        if item.get("realized_pnl_usd") is not None:
            continue

        pnl = pnl_by_trade_id.get(trade_id)
        if pnl is None:
            pnl = trader.get_trade_pnl(trade_id)
        if pnl is not None:
            item["realized_pnl_usd"] = pnl
            summary["backfilled_trade_ids"].append(trade_id)
            log.info("Reconciled closed trade %s with realized P&L $%.2f", trade_id, pnl)

    if alert and summary["recovered_trade_ids"]:
        alert.send(
            "♻️ Startup reconciliation recovered open broker trade(s): "
            + ", ".join(summary["recovered_trade_ids"])
        )

    return summary
