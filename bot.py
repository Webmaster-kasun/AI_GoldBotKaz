"""Main orchestrator for the CPR Gold Bot — v1.0

Runs the 5-minute trading cycle for XAU/USD, applies session and risk
controls, places orders through OANDA, manages break-even, and persists
runtime state.

Position sizing (v1.0):
  score 5–6  →  $100 risk (full)
  score 3–4  →  $66  risk (half)
  score < 3  →  no trade — walk away

Active trading windows (SGT):
  Asian:    10:00–13:59  (max 2 trades)
  Main:     14:00–01:59  (max 6 trades)
  Dead Zone: 02:00–09:59 (no new entries — existing trades managed)
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytz

from calendar_fetcher import run_fetch as refresh_calendar
from config_loader import DATA_DIR, get_bool_env, load_settings
from database import Database
from logging_utils import configure_logging, get_logger
from news_filter import NewsFilter
from oanda_trader import OandaTrader
from signals import SignalEngine, score_to_position_usd
from startup_checks import run_startup_checks
from state_utils import (
    RUNTIME_STATE_FILE, SCORE_CACHE_FILE,
    TRADE_HISTORY_ARCHIVE_FILE, TRADE_HISTORY_FILE,
    update_runtime_state, load_json, save_json,
)
from telegram_alert import TelegramAlert
from telegram_templates import (
    msg_signal_update, msg_trade_opened, msg_breakeven, msg_trade_closed,
    msg_news_block, msg_news_penalty, msg_cooldown_started, msg_daily_cap,
    msg_spread_skip, msg_order_failed, msg_error, msg_friday_cutoff,
)
from reconcile_state import reconcile_runtime_state

configure_logging()
log = get_logger(__name__)

SGT          = pytz.timezone("Asia/Singapore")
INSTRUMENT   = "XAU_USD"
ASSET        = "XAUUSD"
HISTORY_FILE = TRADE_HISTORY_FILE
ARCHIVE_FILE = TRADE_HISTORY_ARCHIVE_FILE
SCORE_CACHE  = SCORE_CACHE_FILE
HISTORY_DAYS = 90

SESSIONS = [
    ("Asian Window", "Asian",  10, 13, 3),
    ("Main Window",  "London", 14, 19, 3),
    ("Main Window",  "US",     20, 23, 3),
    ("Main Window",  "US",      0,  1, 3),
]

SESSION_BANNERS = {
    "Asian":  "🌏 ASIAN",
    "London": "🇬🇧 LONDON",
    "US":     "🗽 US",
}


# ── Settings ───────────────────────────────────────────────────────────────────

def validate_settings(settings: dict) -> dict:
    required = [
        "spread_limits",
        "max_trades_day",
        "max_losing_trades_day",
        "sl_mode",
        "tp_mode",
        "rr_ratio",
    ]
    missing = [k for k in required if k not in settings]
    if missing:
        raise ValueError(f"Missing required settings keys: {missing}")

    settings.setdefault("signal_threshold",         3)
    settings.setdefault("position_full_usd",        100)
    settings.setdefault("position_partial_usd",     66)
    settings.setdefault("position_partial_usd",     66)
    settings.setdefault("account_balance_override", 0)
    settings.setdefault("atr_sl_multiplier",        0.5)
    settings.setdefault("sl_min_usd",               4.0)
    settings.setdefault("sl_max_usd",               20.0)
    settings.setdefault("fixed_sl_usd",             5.0)
    settings.setdefault("breakeven_trigger_usd",    5.0)
    settings.setdefault("sl_pct",                  0.0025)
    settings.setdefault("tp_pct",                  0.0075)
    settings.setdefault("margin_safety_factor",     0.8)
    settings.setdefault("friday_cutoff_hour_sgt",   23)
    settings.setdefault("friday_cutoff_minute_sgt", 0)
    settings.setdefault("news_lookahead_min",        120)
    return settings


def is_friday_cutoff(now_sgt: datetime, settings: dict) -> bool:
    if now_sgt.weekday() != 4:
        return False
    cutoff_hour   = int(settings.get("friday_cutoff_hour_sgt", 23))
    cutoff_minute = int(settings.get("friday_cutoff_minute_sgt", 0))
    return now_sgt.hour > cutoff_hour or (
        now_sgt.hour == cutoff_hour and now_sgt.minute >= cutoff_minute
    )


# ── Trade history helpers ──────────────────────────────────────────────────────

def load_history() -> list:
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(history: list):
    atomic_json_write(HISTORY_FILE, history)


def atomic_json_write(path: Path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


def archive_old_trades(history: list) -> list:
    cutoff     = datetime.now(SGT) - timedelta(days=HISTORY_DAYS)
    active     = []
    to_archive = []
    for trade in history:
        ts = trade.get("timestamp_sgt", "")
        try:
            dt = SGT.localize(datetime.strptime(ts, "%Y-%m-%d %H:%M:%S"))
            if dt < cutoff:
                to_archive.append(trade)
            else:
                active.append(trade)
        except Exception:
            active.append(trade)

    if to_archive:
        archive = []
        if ARCHIVE_FILE.exists():
            try:
                with ARCHIVE_FILE.open("r", encoding="utf-8") as f:
                    archive = json.load(f)
                if not isinstance(archive, list):
                    archive = []
            except Exception:
                archive = []
        archive.extend(to_archive)
        atomic_json_write(ARCHIVE_FILE, archive)
        log.info("Archived %d old trade(s) | Active: %d", len(to_archive), len(active))

    return active


# ── Session helpers ────────────────────────────────────────────────────────────

def get_session(now: datetime, settings: dict = None):
    h = now.hour
    session_thresholds = (settings or {}).get("session_thresholds", {})
    for name, macro, start, end, fallback_thr in SESSIONS:
        if start <= h <= end:
            thr = int(session_thresholds.get(macro, fallback_thr))
            return name, macro, thr
    return None, None, None


def is_dead_zone_time(now_sgt: datetime) -> bool:
    return 2 <= now_sgt.hour < 10


def get_window_key(session_name: str | None) -> str | None:
    if session_name == "Asian Window":
        return "Asian"
    if session_name == "Main Window":
        return "Main"
    return None


def get_window_trade_cap(window_key: str | None, settings: dict) -> int | None:
    if window_key == "Asian":
        return int(settings.get("max_trades_asian", 2))
    if window_key == "Main":
        return int(settings.get("max_trades_main", 6))
    return None


def window_trade_count(history: list, today_str: str, window_key: str) -> int:
    aliases = {
        "Asian": {"Asian", "Asian Window"},
        "Main":  {"Main", "Main Window", "London", "US"},
    }
    valid = aliases.get(window_key, {window_key})
    count = 0
    for t in history:
        if not t.get("timestamp_sgt", "").startswith(today_str):
            continue
        if t.get("status") != "FILLED":
            continue
        trade_window = t.get("window") or t.get("session") or t.get("macro_session")
        if trade_window in valid:
            count += 1
    return count


# ── Risk / daily cap helpers ───────────────────────────────────────────────────

def daily_totals(history: list, today_str: str, trader=None, instrument: str = INSTRUMENT):
    pnl, count, losses = 0.0, 0, 0
    for t in history:
        if t.get("timestamp_sgt", "").startswith(today_str) and t.get("status") == "FILLED":
            count += 1
            p = t.get("realized_pnl_usd")
            if isinstance(p, (int, float)):
                pnl += p
                if p < 0:
                    losses += 1
    if trader is not None:
        try:
            position = trader.get_position(instrument)
            if position:
                unrealized = trader.check_pnl(position)
                pnl += unrealized
        except Exception as e:
            log.warning("Could not fetch unrealized P&L for daily cap: %s", e)
    return pnl, count, losses


def get_closed_trade_records_today(history: list, today_str: str) -> list:
    closed = []
    for t in history:
        if not t.get("timestamp_sgt", "").startswith(today_str):
            continue
        if t.get("status") != "FILLED":
            continue
        if isinstance(t.get("realized_pnl_usd"), (int, float)):
            closed.append(t)
    closed.sort(key=lambda t: t.get("closed_at_sgt") or t.get("timestamp_sgt") or "")
    return closed


def consecutive_loss_streak_today(history: list, today_str: str) -> int:
    streak = 0
    for t in reversed(get_closed_trade_records_today(history, today_str)):
        pnl = t.get("realized_pnl_usd")
        if not isinstance(pnl, (int, float)):
            continue
        if pnl < 0:
            streak += 1
        else:
            break
    return streak


def _parse_sgt_timestamp(value: str | None):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return SGT.localize(datetime.strptime(value, fmt))
        except Exception:
            pass
    return None


def maybe_start_loss_cooldown(history: list, today_str: str, now_sgt: datetime, settings: dict):
    cooldown_min = int(settings.get("loss_streak_cooldown_min", 30))
    if cooldown_min <= 0:
        return None, None
    streak = consecutive_loss_streak_today(history, today_str)
    if streak < 2:
        return None, None
    closed = get_closed_trade_records_today(history, today_str)
    if len(closed) < 2:
        return None, None
    trigger_trade  = closed[-1]
    trigger_marker = (
        trigger_trade.get("trade_id")
        or trigger_trade.get("closed_at_sgt")
        or trigger_trade.get("timestamp_sgt")
    )
    runtime_state = load_json(RUNTIME_STATE_FILE, {})
    if runtime_state.get("loss_cooldown_trigger") == trigger_marker:
        cooldown_until = _parse_sgt_timestamp(runtime_state.get("cooldown_until_sgt"))
        return cooldown_until, trigger_marker
    cooldown_until = now_sgt + timedelta(minutes=cooldown_min)
    save_json(
        RUNTIME_STATE_FILE,
        {
            **runtime_state,
            "loss_cooldown_trigger": trigger_marker,
            "cooldown_until_sgt":   cooldown_until.strftime("%Y-%m-%d %H:%M:%S"),
            "cooldown_reason":      f"{streak} consecutive losses",
            "updated_at_sgt":       now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
        },
    )
    return cooldown_until, trigger_marker


def active_cooldown_until(now_sgt: datetime):
    runtime_state  = load_json(RUNTIME_STATE_FILE, {})
    cooldown_until = _parse_sgt_timestamp(runtime_state.get("cooldown_until_sgt"))
    if cooldown_until and now_sgt < cooldown_until:
        return cooldown_until
    return None


# ── Position sizing (v1.0) ─────────────────────────────────────────────────────

def compute_sl_usd(levels: dict, settings: dict) -> float:
    """Derive SL in USD based on sl_mode.

    pct_based  (default): SL = entry_price × sl_pct  (e.g. 0.25% of gold price)
    fixed_usd:            SL = fixed_sl_usd flat
    atr_based:            SL = ATR × atr_sl_multiplier, clamped to [sl_min, sl_max]
    """
    sl_mode = str(settings.get("sl_mode", "pct_based")).lower()

    if sl_mode == "fixed_usd":
        return float(settings.get("fixed_sl_usd", 12.50))

    if sl_mode == "pct_based":
        entry  = levels.get("entry") or levels.get("current_price", 0)
        sl_pct = float(settings.get("sl_pct", 0.0025))
        if entry and entry > 0 and sl_pct > 0:
            sl_usd = round(entry * sl_pct, 2)
            log.debug("Pct SL: %.2f × %.4f%% = $%.2f", entry, sl_pct * 100, sl_usd)
            return sl_usd
        fallback = float(settings.get("fixed_sl_usd", 12.50))
        log.warning("pct_based SL: no valid entry price — fallback $%.2f", fallback)
        return fallback

    # atr_based
    current_atr = levels.get("atr")
    if not current_atr or current_atr <= 0:
        fallback = float(settings.get("sl_min_usd", 4.0))
        log.warning("ATR not available — using fallback SL of $%.2f", fallback)
        return fallback
    multiplier = float(settings.get("atr_sl_multiplier", 0.5))
    sl_min     = float(settings.get("sl_min_usd", 4.0))
    sl_max     = float(settings.get("sl_max_usd", 20.0))
    raw_sl     = current_atr * multiplier
    sl_usd     = max(sl_min, min(sl_max, raw_sl))
    log.debug("ATR SL: ATR=%.2f x %.2f = %.2f → clamped $%.2f", current_atr, multiplier, raw_sl, sl_usd)
    return round(sl_usd, 2)


# Keep alias so any external callers still work
compute_atr_sl_usd = compute_sl_usd

def calculate_units_from_position(position_usd: int, sl_usd: float) -> float:
    """Convert score-based position risk to OANDA units.

    units = position_usd / sl_usd
    e.g. $66 risk at $6 SL = 11 units of XAU_USD
    """
    if sl_usd <= 0 or position_usd <= 0:
        return 0.0
    return round(position_usd / sl_usd, 2)


def compute_sl_tp_pips(sl_usd: float, settings: dict):
    pip     = 0.01
    tp_mode = str(settings.get("tp_mode", "rr_multiple")).lower()
    if tp_mode == "fixed_usd":
        tp_usd = float(settings.get("fixed_tp_usd", sl_usd * 3))
    else:
        rr_ratio = float(settings.get("rr_ratio", 3.0))
        tp_usd   = round(sl_usd * rr_ratio, 2)
    return round(sl_usd / pip), round(tp_usd / pip)


def compute_sl_tp_prices(entry: float, direction: str, sl_usd: float, settings: dict):
    tp_mode = str(settings.get("tp_mode", "rr_multiple")).lower()
    if tp_mode == "fixed_usd":
        tp_usd = float(settings.get("fixed_tp_usd", sl_usd * 3))
    else:
        tp_usd = round(sl_usd * float(settings.get("rr_ratio", 3.0)), 2)
    if direction == "BUY":
        return round(entry - sl_usd, 2), round(entry + tp_usd, 2), tp_usd
    return round(entry + sl_usd, 2), round(entry - tp_usd, 2), tp_usd


def get_effective_balance(balance: float | None, settings: dict) -> float:
    override = settings.get("account_balance_override")
    if override is not None:
        try:
            v = float(override)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return float(balance or 0)


# ── Score / cache helpers ─────────────────────────────────────────────────────

def load_score_cache() -> dict:
    if not SCORE_CACHE.exists():
        return {}
    try:
        with open(SCORE_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_score_cache(cache: dict):
    atomic_json_write(SCORE_CACHE, cache)


def send_once_per_state(alert, cache: dict, key: str, value: str, message: str):
    if cache.get(key) != value:
        alert.send(message)
        cache[key] = value
        save_score_cache(cache)


# ── Break-even management ──────────────────────────────────────────────────────

def check_breakeven(history: list, trader, alert, settings: dict):
    demo        = settings.get("demo_mode", True)
    trigger_usd = float(settings.get("breakeven_trigger_usd", 5.0))
    changed     = False

    for trade in history:
        if trade.get("status") != "FILLED":
            continue
        if trade.get("breakeven_moved"):
            continue
        trade_id  = trade.get("trade_id")
        entry     = trade.get("entry")
        direction = trade.get("direction", "")
        if not trade_id or not entry or direction not in ("BUY", "SELL"):
            continue

        open_trade = trader.get_open_trade(str(trade_id))
        if open_trade is None:
            continue

        mid, bid, ask = trader.get_price(INSTRUMENT)
        if mid is None:
            continue

        current_price = bid if direction == "BUY" else ask
        trigger_price = (
            entry + trigger_usd if direction == "BUY" else entry - trigger_usd
        )
        triggered = (
            (direction == "BUY"  and current_price >= trigger_price) or
            (direction == "SELL" and current_price <= trigger_price)
        )
        if not triggered:
            continue

        result = trader.modify_sl(str(trade_id), float(entry))
        if result.get("success"):
            trade["breakeven_moved"] = True
            changed = True
            try:
                unrealized_pnl = float(open_trade.get("unrealizedPL", 0))
            except Exception:
                unrealized_pnl = 0
            alert.send(msg_breakeven(
                trade_id=trade_id,
                direction=direction,
                entry=entry,
                trigger_price=trigger_price,
                trigger_usd=trigger_usd,
                current_price=current_price,
                unrealized_pnl=unrealized_pnl,
                demo=demo,
            ))
        else:
            log.warning("Break-even move failed for trade %s: %s", trade_id, result.get("error"))

    if changed:
        save_history(history)


# ── PnL backfill ───────────────────────────────────────────────────────────────

def backfill_pnl(history: list, trader) -> list:
    changed = False
    for trade in history:
        if trade.get("status") == "FILLED" and trade.get("realized_pnl_usd") is None:
            trade_id = trade.get("trade_id")
            if trade_id:
                pnl = trader.get_trade_pnl(str(trade_id))
                if pnl is not None:
                    trade["realized_pnl_usd"] = pnl
                    changed = True
                    log.info("Back-filled P&L trade %s: $%.2f", trade_id, pnl)
                    try:
                        _cp   = trade.get("tp_price") if pnl > 0 else trade.get("sl_price")
                        _dur  = ""
                        _t1s  = trade.get("timestamp_sgt", "")
                        _t2s  = trade.get("closed_at_sgt", "")
                        if _t1s and _t2s:
                            from datetime import datetime as _dt
                            try:
                                _d = int((_dt.strptime(_t2s, "%Y-%m-%d %H:%M:%S") - _dt.strptime(_t1s, "%Y-%m-%d %H:%M:%S")).total_seconds() // 60)
                                _dur = f"{_d // 60}h {_d % 60}m" if _d >= 60 else f"{_d}m"
                            except Exception:
                                pass
                        alert.send(msg_trade_closed(
                            trade_id=trade_id,
                            direction=trade.get("direction", ""),
                            setup=trade.get("setup", ""),
                            entry=float(trade.get("entry", 0)),
                            close_price=float(_cp or 0),
                            pnl=float(pnl),
                            session=trade.get("session", ""),
                            demo=settings.get("demo_mode", True),
                            duration_str=_dur,
                        ))
                    except Exception as _e:
                        log.warning("Could not send trade_closed alert: %s", _e)
    if changed:
        save_history(history)
    return history


# ── Logging helper ─────────────────────────────────────────────────────────────

def log_event(code: str, message: str, level: str = "info", **extra):
    logger_fn = getattr(log, level, log.info)
    payload   = {"event": code}
    payload.update(extra)
    logger_fn(f"[{code}] {message}", extra=payload)


# ── Main cycle ─────────────────────────────────────────────────────────────────

def run_bot_cycle():
    settings = validate_settings(load_settings())
    db       = Database()
    demo     = settings.get("demo_mode", True)
    alert    = TelegramAlert()
    trader   = OandaTrader(demo=demo)
    history  = load_history()
    now_sgt  = datetime.now(SGT)
    today    = now_sgt.strftime("%Y-%m-%d")

    warnings = run_startup_checks()
    with db.cycle() as run_id:
        try:
            for warning in warnings:
                log.warning(warning, extra={"run_id": run_id})

            log.info(
                "=== %s | %s SGT ===",
                settings.get("bot_name", "CPR Gold Bot"),
                now_sgt.strftime("%Y-%m-%d %H:%M"),
                extra={"run_id": run_id},
            )
            update_runtime_state(
                last_cycle_started=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                last_run_id=run_id,
                status="RUNNING",
            )
            db.upsert_state("last_cycle_started", {
                "run_id": run_id,
                "started_at_sgt": now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
            })

            if not settings.get("enabled", True) or get_bool_env("TRADING_DISABLED", False):
                log.warning("Trading disabled.", extra={"run_id": run_id})
                send_once_per_state(alert, load_score_cache(), "ops_state", "disabled", "⏸️ Trading disabled by configuration.")
                update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="SKIPPED_DISABLED")
                db.finish_cycle(run_id, status="SKIPPED", summary={"stage": "enabled_check", "reason": "disabled"})
                return

            history = archive_old_trades(history)
            save_history(history)

            weekday = now_sgt.weekday()
            if weekday == 5:
                log.info("Saturday — market closed.", extra={"run_id": run_id})
                update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="SKIPPED_MARKET_CLOSED")
                db.finish_cycle(run_id, status="SKIPPED", summary={"stage": "market_guard", "reason": "Saturday"})
                return
            if weekday == 6:
                log.info("Sunday — waiting for Monday open.", extra={"run_id": run_id})
                update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="SKIPPED_MARKET_CLOSED")
                db.finish_cycle(run_id, status="SKIPPED", summary={"stage": "market_guard", "reason": "Sunday"})
                return
            if weekday == 0 and now_sgt.hour < 8:
                log.info("Monday pre-open (before 08:00 SGT) — skipping.", extra={"run_id": run_id})
                update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="SKIPPED_MARKET_CLOSED")
                db.finish_cycle(run_id, status="SKIPPED", summary={"stage": "market_guard", "reason": "Monday pre-open"})
                return

            if settings.get("news_filter_enabled", True):
                try:
                    refresh_calendar()
                except Exception as e:
                    log.warning("Calendar refresh failed (using cached): %s", e, extra={"run_id": run_id})

            history = backfill_pnl(history, trader)
            check_breakeven(history, trader, alert, settings)

            cooldown_started_until, _ = maybe_start_loss_cooldown(history, today, now_sgt, settings)
            cache = load_score_cache()
            if cooldown_started_until and now_sgt < cooldown_started_until:
                send_once_per_state(
                    alert, cache, "ops_state",
                    f"cooldown_started:{cooldown_started_until.strftime('%Y-%m-%d %H:%M:%S')}",
                    msg_cooldown_started(streak=2, cooldown_until_sgt=cooldown_started_until.strftime("%H:%M")),
                )
                log_event("COOLDOWN_STARTED", f"Cooldown until {cooldown_started_until.strftime('%Y-%m-%d %H:%M:%S')} SGT.", run_id=run_id)

            session, macro, threshold = get_session(now_sgt, settings)

            if is_friday_cutoff(now_sgt, settings):
                log_event("FRIDAY_CUTOFF", "Friday cutoff active.", run_id=run_id)
                send_once_per_state(alert, cache, "ops_state",
                    f"friday_cutoff:{now_sgt.strftime('%Y-%m-%d')}",
                    msg_friday_cutoff(int(settings.get("friday_cutoff_hour_sgt", 23))),
                )
                update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="SKIPPED_FRIDAY_CUTOFF")
                db.finish_cycle(run_id, status="SKIPPED", summary={"stage": "friday_cutoff"})
                return

            if settings.get("session_only", True):
                if session is None:
                    if is_dead_zone_time(now_sgt):
                        log_event("DEAD_ZONE_SKIP", "Dead zone — entry blocked, management active.", run_id=run_id)
                    else:
                        log.info("Outside all sessions — skipping.", extra={"run_id": run_id})
                    if cache.get("last_session") is not None:
                        send_once_per_state(alert, cache, "ops_state", "outside_session", "⏸️ Outside active session — no trade.")
                        cache["last_session"] = None
                        save_score_cache(cache)
                    update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="SKIPPED_OUTSIDE_SESSION")
                    db.finish_cycle(run_id, status="SKIPPED", summary={"stage": "session_check", "reason": "outside_session"})
                    return
            else:
                if session is None:
                    session, macro = "All Hours", "London"
                threshold = int(settings.get("signal_threshold", 3))

            threshold = threshold or int(settings.get("signal_threshold", 3))
            banner    = SESSION_BANNERS.get(macro, "📊")
            log.info("Session: %s (%s)", session, macro, extra={"run_id": run_id})

            if cache.get("last_session") != session:
                cache["last_session"] = session
                cache.pop("ops_state", None)
                save_score_cache(cache)

            # ── News filter ────────────────────────────────────────────────
            news_penalty = 0
            news_status  = {}
            if settings.get("news_filter_enabled", True):
                nf = NewsFilter(
                    before_minutes=int(settings.get("news_block_before_min", 30)),
                    after_minutes=int(settings.get("news_block_after_min", 30)),
                    lookahead_minutes=int(settings.get("news_lookahead_min", 120)),
                )
                news_status  = nf.get_status_now()
                blocked      = bool(news_status.get("blocked"))
                reason       = str(news_status.get("reason", "No blocking news"))
                news_penalty = int(news_status.get("penalty", 0))
                lookahead    = news_status.get("lookahead", [])
                if lookahead:
                    la_summary = " | ".join(
                        f"{e['name']} in {e['mins_away']}min ({e['severity']})"
                        for e in lookahead[:3]
                    )
                    log.info("Upcoming news: %s", la_summary, extra={"run_id": run_id})
                if blocked:
                    _evt       = news_status.get("event", {})
                    _block_msg = msg_news_block(
                        event_name=_evt.get("name", reason),
                        event_time_sgt=_evt.get("time_sgt", ""),
                        before_min=int(settings.get("news_block_before_min", 30)),
                        after_min=int(settings.get("news_block_after_min", 30)),
                    )
                    send_once_per_state(alert, cache, "ops_state", f"news:{reason}", _block_msg)
                    db.upsert_state("last_news_block", {"blocked": True, "reason": reason, "checked_at_sgt": now_sgt.strftime("%Y-%m-%d %H:%M:%S")})
                    update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="SKIPPED_NEWS_BLOCK", reason=reason)
                    db.finish_cycle(run_id, status="SKIPPED", summary={"stage": "news_filter", "reason": reason})
                    return
                db.upsert_state("last_news_block", {
                    "blocked": False, "reason": reason if news_penalty else None,
                    "penalty": news_penalty, "checked_at_sgt": now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                })

            # ── OANDA login ────────────────────────────────────────────────
            balance = trader.login_with_balance()
            if balance is None:
                alert.send(msg_error("OANDA login failed", "Check OANDA_API_KEY and OANDA_ACCOUNT_ID"))
                db.finish_cycle(run_id, status="FAILED", summary={"stage": "oanda_login", "reason": "login_failed"})
                update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="FAILED_LOGIN")
                return
            if balance <= 0:
                alert.send(msg_error("Cannot fetch balance", "OANDA account returned $0 or invalid"))
                db.finish_cycle(run_id, status="FAILED", summary={"stage": "oanda_login", "reason": "invalid_balance"})
                update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="FAILED_LOGIN")
                return

            reconcile = reconcile_runtime_state(trader, history, INSTRUMENT, now_sgt, alert=alert)
            if reconcile.get("recovered_trade_ids") or reconcile.get("backfilled_trade_ids"):
                save_history(history)
            db.upsert_state("last_reconciliation", {**reconcile, "checked_at_sgt": now_sgt.strftime("%Y-%m-%d %H:%M:%S")})

            # ── Daily caps ─────────────────────────────────────────────────
            daily_pnl, daily_trades, daily_losses = daily_totals(history, today, trader=trader)
            max_losses = int(settings.get("max_losing_trades_day", 3))
            if daily_losses >= max_losses:
                msg = msg_daily_cap("losing_trades", daily_losses, max_losses)
                log_event("COOLDOWN_ACTIVE", msg, run_id=run_id)
                send_once_per_state(alert, cache, "ops_state", f"loss_cap:{today}", msg)
                update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="SKIPPED_LOSS_CAP")
                db.finish_cycle(run_id, status="SKIPPED", summary={"stage": "daily_caps", "reason": "loss_cap"})
                return

            if daily_trades >= int(settings.get("max_trades_day", 8)):
                msg = msg_daily_cap("total_trades", daily_trades, int(settings.get("max_trades_day", 8)))
                send_once_per_state(alert, cache, "ops_state", f"trade_cap:{today}", msg)
                update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="SKIPPED_TRADE_CAP")
                db.finish_cycle(run_id, status="SKIPPED", summary={"stage": "daily_caps", "reason": "trade_cap"})
                return

            cooldown_until = active_cooldown_until(now_sgt)
            if cooldown_until:
                remaining_min = max(1, int((cooldown_until - now_sgt).total_seconds() // 60))
                msg = f"🧊 Cooldown active — new entries paused for {remaining_min} more minute(s)."
                send_once_per_state(alert, cache, "ops_state", f"cooldown:{cooldown_until.strftime('%Y-%m-%d %H:%M:%S')}", msg)
                update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="SKIPPED_COOLDOWN")
                db.finish_cycle(run_id, status="SKIPPED", summary={"stage": "cooldown_guard"})
                return

            window_key = get_window_key(session)
            window_cap = get_window_trade_cap(window_key, settings)
            if window_key and window_cap is not None:
                trades_in_window = window_trade_count(history, today, window_key)
                if trades_in_window >= window_cap:
                    msg = msg_daily_cap("window", trades_in_window, window_cap, window=window_key)
                    send_once_per_state(alert, cache, "ops_state", f"window_cap:{today}:{window_key}", msg)
                    update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="SKIPPED_WINDOW_CAP")
                    db.finish_cycle(run_id, status="SKIPPED", summary={"stage": "window_guard", "window": window_key})
                    return

            open_count     = trader.get_open_trades_count(INSTRUMENT)
            max_concurrent = int(settings.get("max_concurrent_trades", 1))
            if open_count >= max_concurrent:
                msg = f"⏸️ Max concurrent trades reached ({open_count}/{max_concurrent}) — waiting."
                send_once_per_state(alert, cache, "ops_state", f"open_cap:{open_count}:{max_concurrent}", msg)
                update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="SKIPPED_OPEN_TRADE_CAP")
                db.finish_cycle(run_id, status="SKIPPED", summary={"stage": "open_trade_guard"})
                return

            # ── Signal ────────────────────────────────────────────────────
            engine = SignalEngine(demo=demo)
            score, direction, details, levels, position_usd = engine.analyze(asset=ASSET)

            raw_score        = score
            raw_position_usd = position_usd

            if news_penalty:
                score        = max(score + news_penalty, 0)
                position_usd = score_to_position_usd(score)
                details      = details + f" | ⚠️ News penalty applied ({news_penalty:+d})"
                _nev         = news_status.get("events", [])
                if not _nev and news_status.get("event"):
                    _nev = [news_status["event"]]
                _nev_names = [e.get("name", "") for e in _nev]
                send_once_per_state(
                    alert, cache, "ops_state", f"news_penalty:{news_penalty}:{today}",
                    msg_news_penalty(
                        event_names=_nev_names,
                        penalty=news_penalty,
                        score_after=score,
                        score_before=raw_score,
                        position_after=position_usd,
                        position_before=raw_position_usd,
                    ),
                )

            db.record_signal(
                {"pair": INSTRUMENT, "timeframe": "M15", "side": direction,
                 "score": score, "raw_score": raw_score,
                 "news_penalty": news_penalty, "details": details, "levels": levels},
                timeframe="M15", run_id=run_id,
            )

            # ── Signal update Telegram (on change) ────────────────────────
            cpr_w = levels.get("cpr_width_pct", 0)
            if score != cache.get("score", -1) or direction != cache.get("direction", ""):
                signal_msg = msg_signal_update(
                    banner=banner,
                    session=session,
                    direction=direction,
                    score=score,
                    position_usd=position_usd,
                    cpr_width_pct=cpr_w,
                    detail_lines=details.split(" | "),
                    news_penalty=news_penalty,
                    raw_score=raw_score,
                )
                alert.send(signal_msg)
                cache.update({"score": score, "direction": direction})
                save_score_cache(cache)

            # ── No trade ───────────────────────────────────────────────────
            if direction == "NONE" or position_usd <= 0:
                log.info(
                    "No trade. Score=%s dir=%s position=$%s",
                    score, direction, position_usd,
                    extra={"run_id": run_id},
                )
                update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="COMPLETED_NO_SIGNAL", score=score, direction=direction)
                db.finish_cycle(run_id, status="COMPLETED", summary={"signals": 1, "trades_placed": 0, "score": score, "direction": direction})
                return

            if not settings.get("trade_gold", True):
                update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="SKIPPED_TRADE_GOLD_DISABLED")
                db.finish_cycle(run_id, status="SKIPPED", summary={"stage": "trade_switch"})
                return

            # ── Position sizing ────────────────────────────────────────────
            entry = levels.get("entry", 0)
            if entry <= 0:
                _, _, ask = trader.get_price(INSTRUMENT)
                entry = ask or 0

            effective_balance = get_effective_balance(balance, settings)
            sl_usd  = compute_sl_usd(levels, settings)
            units   = calculate_units_from_position(position_usd, sl_usd)

            if units <= 0:
                alert.send(msg_error("Position size = 0", f"position_usd=${position_usd} sl=${sl_usd:.2f}"))
                db.finish_cycle(run_id, status="SKIPPED", summary={"stage": "position_sizing", "reason": "zero_units"})
                update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="SKIPPED_ZERO_UNITS")
                return

            # ── Margin cap — prevent INSUFFICIENT_MARGIN rejections ────────
            margin_safety = float(settings.get("margin_safety_factor", 0.8))
            try:
                margin_available = trader.get_margin_available() or balance or 0
                specs            = trader.get_instrument_specs(INSTRUMENT)
                margin_rate      = specs.get("marginRate", 0.05)
                price_for_margin = entry if entry > 0 else levels.get("current_price", entry)
                if price_for_margin > 0 and margin_rate > 0:
                    max_units_by_margin = int((margin_available * margin_safety) / (price_for_margin * margin_rate))
                    if max_units_by_margin < 1:
                        log.warning(
                            "Margin cap: marginAvailable=%.2f safety=%.0f%% price=%.2f marginRate=%.4f → max_units=%d — skipping",
                            margin_available, margin_safety * 100, price_for_margin, margin_rate, max_units_by_margin,
                        )
                        alert.send(msg_error(
                            "Insufficient margin — trade skipped",
                            f"marginAvailable=${margin_available:.0f} safety={margin_safety:.0%} "
                            f"price={price_for_margin:.2f} marginRate={margin_rate:.2%} → max_units={max_units_by_margin}",
                        ))
                        db.finish_cycle(run_id, status="SKIPPED", summary={"stage": "margin_cap", "reason": "insufficient_margin"})
                        update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="SKIPPED_MARGIN")
                        return
                    if units > max_units_by_margin:
                        log.warning(
                            "Margin cap applied: requested %.2f units → capped to %d "
                            "(marginAvailable=%.2f safety=%.0f%% price=%.2f marginRate=%.4f)",
                            units, max_units_by_margin, margin_available, margin_safety * 100,
                            price_for_margin, margin_rate,
                        )
                        units = float(max_units_by_margin)
            except Exception as e:
                log.warning("Margin cap check failed (proceeding with original units): %s", e)

            rr_ratio = float(settings.get("rr_ratio", 3.0))
            stop_pips, tp_pips = compute_sl_tp_pips(sl_usd, settings)
            reward_usd = round(units * sl_usd * rr_ratio, 2)

            # ── Spread guard ───────────────────────────────────────────────
            mid, bid, ask = trader.get_price(INSTRUMENT)
            if mid is None:
                alert.send(msg_error("Cannot fetch price", "OANDA pricing endpoint returned None"))
                db.finish_cycle(run_id, status="FAILED", summary={"stage": "pricing"})
                update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="FAILED_PRICING")
                return

            spread_pips  = round(abs(ask - bid) / 0.01)
            spread_limit = int(settings.get("spread_limits", {}).get(macro, settings.get("max_spread_pips", 150)))
            if spread_pips > spread_limit:
                send_once_per_state(alert, cache, "ops_state", f"spread:{macro}:{spread_pips}",
                    msg_spread_skip(banner, session, spread_pips, spread_limit))
                db.finish_cycle(run_id, status="SKIPPED", summary={"stage": "spread_guard"})
                update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="SKIPPED_SPREAD_GUARD")
                return

            # ── Place order ────────────────────────────────────────────────
            result = trader.place_order(
                instrument=INSTRUMENT, direction=direction,
                size=units, stop_distance=stop_pips, limit_distance=tp_pips,
                bid=bid, ask=ask,
            )

            sl_price, tp_price, tp_usd = compute_sl_tp_prices(entry, direction, sl_usd, settings)

            record = {
                "timestamp_sgt":        now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                "mode":                 "DEMO" if demo else "LIVE",
                "instrument":           INSTRUMENT,
                "direction":            direction,
                "setup":                levels.get("setup", ""),
                "session":              session,
                "window":               get_window_key(session),
                "macro_session":        macro,
                "score":                score,
                "raw_score":            raw_score,
                "news_penalty":         news_penalty,
                "position_usd":         position_usd,
                "entry":                round(entry, 2),
                "sl_price":             sl_price,
                "tp_price":             tp_price,
                "size":                 units,
                "cpr_width_pct":        cpr_w,
                "sl_usd":               round(sl_usd, 2),
                "tp_usd":               round(tp_usd, 2),
                "estimated_reward_usd": round(reward_usd, 2),
                "spread_pips":          spread_pips,
                "stop_pips":            stop_pips,
                "tp_pips":              tp_pips,
                "levels":               levels,
                "details":              details,
                "trade_id":             None,
                "status":               "FAILED",
                "realized_pnl_usd":     None,
            }

            if result.get("success"):
                record["trade_id"] = result.get("trade_id")
                record["status"]   = "FILLED"
                fill_price         = result.get("fill_price")
                if fill_price and fill_price > 0:
                    actual_entry           = fill_price
                    record["entry"]        = round(actual_entry, 2)
                    record["signal_entry"] = round(entry, 2)
                    record["sl_price"]     = round(actual_entry - sl_usd if direction == "BUY" else actual_entry + sl_usd, 2)
                    record["tp_price"]     = round(actual_entry + tp_usd if direction == "BUY" else actual_entry - tp_usd, 2)
                else:
                    actual_entry = entry

                trade_msg = msg_trade_opened(
                    banner=banner,
                    direction=direction,
                    setup=levels.get("setup", ""),
                    session=session,
                    fill_price=record["entry"],
                    signal_price=entry,
                    sl_price=record["sl_price"],
                    tp_price=record["tp_price"],
                    sl_usd=sl_usd,
                    tp_usd=tp_usd,
                    units=units,
                    position_usd=position_usd,
                    rr_ratio=rr_ratio,
                    cpr_width_pct=cpr_w,
                    spread_pips=spread_pips,
                    score=score,
                    balance=effective_balance,
                    demo=demo,
                    news_penalty=news_penalty,
                    raw_score=raw_score,
                )
                alert.send(trade_msg)
                log.info("Trade placed: %s", record, extra={"run_id": run_id})
            else:
                err = result.get("error", "Unknown")
                alert.send(msg_order_failed(direction, INSTRUMENT, units, err))
                log.error("Order failed: %s", err, extra={"run_id": run_id})

            history.append(record)
            save_history(history)
            db.record_trade_attempt(
                {"pair": INSTRUMENT, "timeframe": "M15", "side": direction, "score": score, **record},
                ok=bool(result.get("success")), note=result.get("error", "trade placed"),
                broker_trade_id=record.get("trade_id"), run_id=run_id,
            )
            db.upsert_state("last_trade_attempt", {
                "run_id": run_id, "success": bool(result.get("success")),
                "trade_id": record.get("trade_id"), "timestamp_sgt": record["timestamp_sgt"],
            })
            update_runtime_state(
                last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                status="COMPLETED", score=score, direction=direction,
                trade_status=record["status"],
            )
            db.finish_cycle(run_id, status="COMPLETED", summary={
                "signals": 1, "trades_placed": int(bool(result.get("success"))),
                "score": score, "direction": direction, "trade_status": record["status"],
            })

        except Exception as exc:
            update_runtime_state(last_cycle_finished=now_sgt.strftime("%Y-%m-%d %H:%M:%S"), status="FAILED", error=str(exc))
            db.finish_cycle(run_id, status="FAILED", summary={"error": str(exc)})
            raise


def main():
    return run_bot_cycle()


if __name__ == "__main__":
    main()
