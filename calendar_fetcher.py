"""
Forex Factory calendar fetcher for the CPR Gold Bot.

Architecture-only improvements:
- Uses /data/runtime_state.json cooldown tracking
- Backs off after HTTP 429 responses
- Avoids noisy warnings for expected next-week 404 responses
- Keeps the existing calendar_cache.json if refresh is skipped or fails

Strategy is unchanged. This only affects how often the news calendar is refreshed.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta

import pytz
import requests

from config_loader import load_settings
from state_utils import CALENDAR_CACHE_FILE, RUNTIME_STATE_FILE, load_json, save_json

log = logging.getLogger(__name__)

SGT = pytz.timezone("Asia/Singapore")
CACHE_PATH = CALENDAR_CACHE_FILE
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEXT_WEEK_URL = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"

GOLD_KEYWORDS = [
    "fomc", "fed", "powell", "cpi", "pce",
    "non-farm", "nfp", "unemployment",
    "core cpi", "core pce", "rate decision",
    "interest rate", "monetary policy",
    "gdp", "retail sales", "durable goods",
    "ism", "pmi",
]


def _now_sgt() -> datetime:
    return datetime.now(SGT)


def _parse_sgt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return SGT.localize(datetime.strptime(value, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return None


def _load_runtime_state() -> dict:
    state = load_json(RUNTIME_STATE_FILE, {})
    return state if isinstance(state, dict) else {}


def _save_runtime_state(state: dict) -> None:
    save_json(RUNTIME_STATE_FILE, state)


def _is_gold_relevant(title: str, country: str, impact: str) -> bool:
    if country.upper() != "USD":
        return False
    if impact.lower() not in {"high", "3", "red", "medium-high"}:
        return False
    title_lower = title.lower()
    return any(kw in title_lower for kw in GOLD_KEYWORDS)


def _parse_ff_event(event: dict) -> dict | None:
    try:
        title = event.get("title", "")
        country = event.get("country", "")
        impact = event.get("impact", "")
        date_str = event.get("date", "")
        time_str = event.get("time", "")

        if not _is_gold_relevant(title, country, impact):
            return None

        et_tz = pytz.timezone("America/New_York")
        dt_date = datetime.strptime(date_str, "%m-%d-%Y")

        if not time_str or time_str.lower() in {"all day", "tentative", ""}:
            dt_naive = dt_date.replace(hour=8, minute=30)
        else:
            time_clean = re.sub(r"([ap]m)", r" \1", time_str, flags=re.IGNORECASE).strip().upper()
            dt_naive = datetime.strptime(f"{date_str} {time_clean}", "%m-%d-%Y %I:%M %p")

        dt_et = et_tz.localize(dt_naive)
        dt_sgt = dt_et.astimezone(SGT)

        return {
            "name": title,
            "currency": country.upper(),
            "impact": "high",
            "time_sgt": dt_sgt.strftime("%Y-%m-%d %H:%M"),
        }
    except Exception as exc:
        log.debug("Could not parse FF event %s: %s", event.get("title"), exc)
        return None


def _fetch_ff_events(url: str, suppress_404: bool = False) -> tuple[list, int | None]:
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "CPRGoldBot/1.0"})
        if r.status_code == 200:
            data = r.json()
            events = data if isinstance(data, list) else []
            usd_events = [e for e in events if e.get("country", "").upper() == "USD"]
            impact_values = sorted({str(e.get("impact", "")) for e in usd_events})
            log.info(
                "FF feed OK: %d total events | %d USD | impact values seen: %s",
                len(events), len(usd_events), impact_values,
            )
            return events, 200
        if r.status_code == 404 and suppress_404:
            log.info("FF next-week feed not yet published (HTTP 404) — keeping current cache.")
            return [], 404
        log.warning("Forex Factory fetch HTTP %s from %s", r.status_code, url)
        return [], r.status_code
    except Exception as exc:
        log.warning("Forex Factory fetch error (%s): %s", url, exc)
        return [], None


def _load_existing_cache() -> list:
    if not CACHE_PATH.exists():
        return []
    try:
        with CACHE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as exc:
        log.warning("Could not read existing calendar_cache.json: %s", exc)
        return []


def _deduplicate(events: list) -> list:
    seen = set()
    out = []
    for e in events:
        key = (e.get("name", "").lower(), e.get("time_sgt", ""))
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _prune_old_events(events: list, days_ahead: int = 14) -> list:
    now = _now_sgt()
    cutoff = now + timedelta(days=days_ahead)
    kept = []
    for e in events:
        try:
            dt = SGT.localize(datetime.strptime(e["time_sgt"], "%Y-%m-%d %H:%M"))
            if now <= dt <= cutoff:
                kept.append(e)
        except Exception:
            pass
    return kept


def _should_skip_fetch(settings: dict, state: dict) -> tuple[bool, str | None]:
    now = _now_sgt()
    next_allowed = _parse_sgt(state.get("calendar_next_allowed_fetch_sgt"))
    if next_allowed and now < next_allowed:
        return True, f"backoff_active_until={next_allowed.strftime('%Y-%m-%d %H:%M:%S')}"

    interval_min = int(settings.get("calendar_fetch_interval_min", 60))
    last_success = _parse_sgt(state.get("calendar_last_success_sgt"))
    if last_success and (now - last_success) < timedelta(minutes=interval_min):
        return True, f"cooldown_active_last_success={last_success.strftime('%Y-%m-%d %H:%M:%S')}"

    return False, None


def run_fetch() -> bool:
    log.info("Fetching economic calendar from Forex Factory...")

    settings = load_settings()
    state = _load_runtime_state()
    now = _now_sgt()
    state["calendar_last_attempt_sgt"] = now.strftime("%Y-%m-%d %H:%M:%S")

    skip, reason = _should_skip_fetch(settings, state)
    if skip:
        state["calendar_last_fetch_result"] = f"skipped:{reason}"
        _save_runtime_state(state)
        log.info("Skipping calendar refresh — %s", reason)
        return False

    today_weekday = now.weekday()
    suppress_nextweek_404 = today_weekday < 3

    this_week, status_this = _fetch_ff_events(FF_URL)
    next_week, status_next = _fetch_ff_events(NEXT_WEEK_URL, suppress_404=suppress_nextweek_404)
    all_raw = this_week + next_week

    if status_this == 429 or status_next == 429:
        retry_after_min = int(settings.get("calendar_retry_after_min", 15))
        next_allowed = now + timedelta(minutes=retry_after_min)
        state["calendar_last_fetch_result"] = "rate_limited_429"
        state["calendar_next_allowed_fetch_sgt"] = next_allowed.strftime("%Y-%m-%d %H:%M:%S")
        _save_runtime_state(state)
        log.warning("Calendar fetch rate-limited (HTTP 429) — backing off until %s SGT.", next_allowed.strftime("%Y-%m-%d %H:%M:%S"))
        return False

    if not all_raw:
        state["calendar_last_fetch_result"] = "no_events_kept_existing_cache"
        _save_runtime_state(state)
        log.warning("No events fetched — keeping existing calendar_cache.json unchanged.")
        return False

    parsed = [e for e in (_parse_ff_event(ev) for ev in all_raw) if e is not None]
    log.info("Parsed %d gold-relevant events from %d total", len(parsed), len(all_raw))

    if not parsed:
        usd_high = [
            e.get("title", "") for e in all_raw
            if e.get("country", "").upper() == "USD"
            and str(e.get("impact", "")).lower() in {"high", "3", "red", "medium-high"}
        ]
        state["calendar_last_fetch_result"] = "no_relevant_events_kept_existing_cache"
        _save_runtime_state(state)
        log.warning("No relevant events matched keywords. USD high-impact titles in feed: %s", usd_high[:20])
        log.warning("No relevant events found in feed — keeping existing cache.")
        return False

    existing = _load_existing_cache()
    merged = _deduplicate(parsed + existing)
    pruned = _prune_old_events(merged, days_ahead=14)
    pruned.sort(key=lambda e: e.get("time_sgt", ""))

    with CACHE_PATH.open("w", encoding="utf-8") as f:
        json.dump(pruned, f, indent=2)

    state["calendar_last_success_sgt"] = now.strftime("%Y-%m-%d %H:%M:%S")
    state["calendar_last_fetch_result"] = f"success:{len(pruned)}_events"
    state.pop("calendar_next_allowed_fetch_sgt", None)
    _save_runtime_state(state)

    log.info("calendar_cache.json updated — %d events saved (next 14 days).", len(pruned))
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    success = run_fetch()
    if not success:
        log.warning("Falling back to existing calendar_cache.json")
