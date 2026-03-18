from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent

# DATA_DIR: use env var DATA_DIR if set (GitHub Actions injects it via workflow),
# otherwise fall back to ./data next to the repo files.
# This avoids requiring /data (Railway-only path) when running on GitHub Actions.
_data_dir_env = os.environ.get('DATA_DIR', '')
DATA_DIR = Path(_data_dir_env).resolve() if _data_dir_env else BASE_DIR / 'data'
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
    if SETTINGS_FILE.exists():
        return SETTINGS_FILE

    default_settings = _read_json(DEFAULT_SETTINGS_PATH, {})
    if not isinstance(default_settings, dict):
        default_settings = {}
    # Helix-style runtime defaults without touching CPR strategy values.
    default_settings.setdefault('bot_name', 'CPR Gold Bot')
    default_settings.setdefault('cycle_minutes', 5)
    default_settings.setdefault('db_retention_days', 90)
    default_settings.setdefault('db_cleanup_hour_sgt', 0)
    default_settings.setdefault('db_cleanup_minute_sgt', 15)
    default_settings.setdefault('db_vacuum_weekly', True)
    default_settings.setdefault('calendar_fetch_interval_min', 60)
    default_settings.setdefault('calendar_retry_after_min', 15)
    _write_json(SETTINGS_FILE, default_settings)
    logger.info('Bootstrapped persistent settings -> %s', SETTINGS_FILE)
    return SETTINGS_FILE



def load_settings() -> dict:
    ensure_persistent_settings()
    settings = _read_json(SETTINGS_FILE, {})
    if not isinstance(settings, dict):
        settings = {}
    settings.setdefault('bot_name', 'CPR Gold Bot')
    settings.setdefault('cycle_minutes', 5)
    settings.setdefault('db_retention_days', 90)
    settings.setdefault('db_cleanup_hour_sgt', 0)
    settings.setdefault('db_cleanup_minute_sgt', 15)
    settings.setdefault('db_vacuum_weekly', True)
    settings.setdefault('calendar_fetch_interval_min', 60)
    settings.setdefault('calendar_retry_after_min', 15)
    _write_json(SETTINGS_FILE, settings)
    return settings



def save_settings(settings: dict) -> None:
    _write_json(SETTINGS_FILE, settings)
    logger.info('Saved settings -> %s', SETTINGS_FILE)



def load_secrets() -> dict:
    if SECRETS_JSON_PATH.exists():
        loaded = _read_json(SECRETS_JSON_PATH, {})
        if isinstance(loaded, dict):
            return loaded

    return {
        'OANDA_API_KEY': os.environ.get('OANDA_API_KEY', ''),
        'OANDA_ACCOUNT_ID': os.environ.get('OANDA_ACCOUNT_ID', ''),
        'TELEGRAM_TOKEN': os.environ.get('TELEGRAM_TOKEN', ''),
        'TELEGRAM_CHAT_ID': os.environ.get('TELEGRAM_CHAT_ID', ''),
        'DATA_DIR': str(DATA_DIR),
    }



def get_bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}
