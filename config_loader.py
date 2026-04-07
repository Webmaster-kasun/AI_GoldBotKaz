from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('DATA_DIR', '/data')).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS_PATH = BASE_DIR / 'settings.json'
SETTINGS_FILE = DATA_DIR / 'settings.json'
SECRETS_JSON_PATH = BASE_DIR / 'secrets.json'


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            with path.open('r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as exc:
        logger.warning('Failed to read %s: %s', path, exc)
    return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


def ensure_persistent_settings() -> Path:
    # Always read the bundled defaults shipped with the code.
    default_settings = _read_json(DEFAULT_SETTINGS_PATH, {})
    if not isinstance(default_settings, dict):
        default_settings = {}

    if SETTINGS_FILE.exists():
        # Merge: inject any keys present in the bundled defaults that are
        # missing from the persistent volume file (e.g. after a deployment
        # that adds new settings keys).
        persistent = _read_json(SETTINGS_FILE, {})
        if not isinstance(persistent, dict):
            persistent = {}
        new_keys = {k: v for k, v in default_settings.items() if k not in persistent}
        changed = dict(new_keys)

        # bot_name is a version indicator — always sync it from the bundled
        # defaults so a redeployment automatically updates the displayed
        # version string without requiring the user to edit the volume file.
        bundled_bot_name = default_settings.get('bot_name')
        if bundled_bot_name and persistent.get('bot_name') != bundled_bot_name:
            changed['bot_name'] = bundled_bot_name

        # signal_threshold is a strategy parameter — always sync from bundled
        # defaults on deploy so Railway persistent volume never lags behind
        # a version upgrade (v3.8 fix: persistent volume had stale value 3). 
        bundled_threshold = default_settings.get('signal_threshold')
        if bundled_threshold is not None and persistent.get('signal_threshold') != bundled_threshold:
            changed['signal_threshold'] = bundled_threshold

        # v4.0 migration — force-sync all keys that changed in v4.0 from the
        # bundled settings.json. setdefault() never overwrites existing volume
        # values, so without this the persistent volume silently keeps old
        # values (e.g. sl_mode=pct_based, loss_streak_cooldown_min=30) even
        # after deploying v4.0-uncapped. Every key listed here is always
        # written from the bundled defaults regardless of what the volume has.
        V4_FORCE_SYNC_KEYS = [
            'sl_mode',
            'atr_sl_multiplier',
            'sl_min_usd',
            'sl_max_usd',
            'rr_ratio',
            'breakeven_trigger_usd',
            'max_losing_trades_day',
            'max_trades_day',
            'max_losing_trades_session',
            'loss_streak_cooldown_min',
            'max_trades_london',
            'max_trades_us',
            'friday_cutoff_hour_sgt',
            'friday_cutoff_minute_sgt',
        ]
        for key in V4_FORCE_SYNC_KEYS:
            if key in default_settings:
                if persistent.get(key) != default_settings[key]:
                    changed[key] = default_settings[key]

        # v4.0 migration — force-sync all keys that changed in v4.0 from the
        # bundled settings.json. setdefault() never overwrites existing volume
        # values, so without this the persistent volume silently keeps old
        # values (e.g. sl_mode=pct_based, loss_streak_cooldown_min=30) even
        # after deploying v4.0-uncapped. Every key listed here is always
        # written from the bundled defaults regardless of what the volume has.
        V4_FORCE_SYNC_KEYS = [
            'sl_mode',
            'atr_sl_multiplier',
            'sl_min_usd',
            'sl_max_usd',
            'rr_ratio',
            'breakeven_trigger_usd',
            'max_losing_trades_day',
            'max_trades_day',
            'max_losing_trades_session',
            'loss_streak_cooldown_min',
            'max_trades_london',
            'max_trades_us',
            'friday_cutoff_hour_sgt',
            'friday_cutoff_minute_sgt',
        ]
        for key in V4_FORCE_SYNC_KEYS:
            if key in default_settings:
                if persistent.get(key) != default_settings[key]:
                    changed[key] = default_settings[key]

        if changed:
            persistent.update(changed)
            _write_json(SETTINGS_FILE, persistent)
            logger.info(
                'Updated %d key(s) in persistent settings: %s',
                len(changed), list(changed.keys()),
            )
        return SETTINGS_FILE

    # First boot — bootstrap the persistent file from bundled defaults.
    default_settings.setdefault('bot_name', 'CPR Gold Bot')
    default_settings.setdefault('max_rr_ratio', 3.0)
    default_settings.setdefault('sl_min_atr_mult', 0.8)
    default_settings.setdefault('h1_trend_filter_enabled', True)
    default_settings.setdefault('h1_ema_period', 21)
    default_settings.setdefault('require_candle_close', True)
    default_settings.setdefault('sl_direction_cooldown_min', 60)
    default_settings.setdefault('asian_session_enabled',  True)
    default_settings.setdefault('london_session_enabled', True)
    default_settings.setdefault('us_session_enabled', True)
    default_settings.setdefault('session_report_hour_sgt', 2)
    default_settings.setdefault('session_report_minute_sgt', 0)
    default_settings.setdefault('version', '4.2')
    default_settings.setdefault('instrument', 'XAU_USD')
    default_settings.setdefault('instrument_display', 'XAU/USD')
    default_settings.setdefault('timeframe', 'M15')
    default_settings.setdefault('cycle_minutes', 5)
    default_settings.setdefault('db_retention_days', 90)
    default_settings.setdefault('db_cleanup_hour_sgt', 0)
    default_settings.setdefault('db_cleanup_minute_sgt', 15)
    default_settings.setdefault('db_vacuum_weekly', True)
    default_settings.setdefault('calendar_fetch_interval_min', 60)
    default_settings.setdefault('calendar_retry_after_min', 15)
    default_settings.setdefault('exhaustion_atr_mult', 2.0)
    _write_json(SETTINGS_FILE, default_settings)
    logger.info('Bootstrapped persistent settings -> %s', SETTINGS_FILE)
    return SETTINGS_FILE


# ── load_settings cache (M-06 fix) ────────────────────────────────────────────
# Avoids re-reading disk on every call. Cache is invalidated when the file's
# modification time changes — so manual edits to settings.json take effect
# on the very next cycle without restarting the bot.
_settings_cache: dict = {}
_settings_mtime: float = 0.0


def load_settings() -> dict:
    global _settings_cache, _settings_mtime
    ensure_persistent_settings()

    try:
        mtime = SETTINGS_FILE.stat().st_mtime
    except OSError:
        mtime = 0.0

    if _settings_cache and mtime == _settings_mtime:
        return _settings_cache  # file unchanged — skip disk read

    settings = _read_json(SETTINGS_FILE, {})
    if not isinstance(settings, dict):
        settings = {}

    original_keys = set(settings.keys())

    settings.setdefault('bot_name', 'CPR Gold Bot')
    settings.setdefault('max_rr_ratio', 3.0)
    settings.setdefault('sl_min_atr_mult', 0.8)
    settings.setdefault('h1_trend_filter_enabled', True)
    settings.setdefault('h1_ema_period', 21)
    settings.setdefault('require_candle_close', True)
    settings.setdefault('sl_direction_cooldown_min', 60)
    settings.setdefault('asian_session_enabled',  True)
    settings.setdefault('london_session_enabled', True)
    settings.setdefault('us_session_enabled', True)
    settings.setdefault('asian_report_hour_sgt',    16)
    settings.setdefault('asian_report_minute_sgt',   5)
    settings.setdefault('london_report_hour_sgt',   21)
    settings.setdefault('london_report_minute_sgt',  5)
    settings.setdefault('us_report_hour_sgt',         1)
    settings.setdefault('us_report_minute_sgt',       5)
    settings.setdefault('session_report_minute_sgt', 0)
    settings.setdefault('version', '4.2')
    settings.setdefault('instrument', 'XAU_USD')
    settings.setdefault('instrument_display', 'XAU/USD')
    settings.setdefault('timeframe', 'M15')
    settings.setdefault('enabled', True)
    settings.setdefault('cycle_minutes', 5)
    settings.setdefault('db_retention_days', 90)
    settings.setdefault('db_cleanup_hour_sgt', 0)
    settings.setdefault('db_cleanup_minute_sgt', 15)
    settings.setdefault('db_vacuum_weekly', True)
    settings.setdefault('calendar_fetch_interval_min', 60)
    settings.setdefault('calendar_retry_after_min', 15)
    settings.setdefault('exhaustion_atr_mult', 2.0)
    settings.setdefault('trading_day_start_hour_sgt', 8)
    settings.setdefault('max_losing_trades_session', 999)   # v4.0-uncapped
    settings.setdefault('max_trades_london', 999)           # v4.0-uncapped
    settings.setdefault('max_trades_asian', 5)
    settings.setdefault('max_trades_us', 999)               # v4.0-uncapped
    settings.setdefault('session_start_hour_sgt', 16)
    settings.setdefault('session_end_hour_sgt', 1)

    if set(settings.keys()) != original_keys:
        _write_json(SETTINGS_FILE, settings)

    _settings_cache = settings
    _settings_mtime = mtime
    return settings


def save_settings(settings: dict) -> None:
    _write_json(SETTINGS_FILE, settings)
    logger.info('Saved settings -> %s', SETTINGS_FILE)


def load_secrets() -> dict:
    """Load secrets with environment variables taking priority over secrets.json."""
    file_secrets: dict = {}
    if SECRETS_JSON_PATH.exists():
        loaded = _read_json(SECRETS_JSON_PATH, {})
        if isinstance(loaded, dict):
            file_secrets = loaded

    return {
        'OANDA_API_KEY':    os.environ.get('OANDA_API_KEY')    or file_secrets.get('OANDA_API_KEY',    ''),
        'OANDA_ACCOUNT_ID': os.environ.get('OANDA_ACCOUNT_ID') or file_secrets.get('OANDA_ACCOUNT_ID', ''),
        'TELEGRAM_TOKEN':   os.environ.get('TELEGRAM_TOKEN')   or file_secrets.get('TELEGRAM_TOKEN',   ''),
        'TELEGRAM_CHAT_ID': os.environ.get('TELEGRAM_CHAT_ID') or file_secrets.get('TELEGRAM_CHAT_ID', ''),
        'DATA_DIR':         str(DATA_DIR),
    }


def get_bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}
