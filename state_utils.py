from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import pytz

logger = logging.getLogger(__name__)

SG_TZ = pytz.timezone('Asia/Singapore')
_data_dir_env = os.environ.get('DATA_DIR', '')
DATA_DIR = Path(_data_dir_env).resolve() if _data_dir_env else Path(__file__).resolve().parent / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)

CALENDAR_CACHE_FILE = DATA_DIR / 'calendar_cache.json'
SCORE_CACHE_FILE = DATA_DIR / 'score_cache.json'
TRADE_HISTORY_FILE = DATA_DIR / 'trade_history.json'
TRADE_HISTORY_ARCHIVE_FILE = DATA_DIR / 'trade_history_archive.json'
LAST_TRADE_CANDLE_FILE = DATA_DIR / 'last_trade_candle.json'
RUNTIME_STATE_FILE = DATA_DIR / 'runtime_state.json'


def load_json(path: Path, default: Any):
    try:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(default, dict) and not isinstance(data, dict):
                    return default.copy()
                if isinstance(default, list) and not isinstance(data, list):
                    return default.copy()
                return data
    except Exception as exc:
        logger.warning('Failed to load %s: %s', path, exc)
    return default.copy() if isinstance(default, (dict, list)) else default


def save_json(path: Path, data: Any):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tmp:
            json.dump(data, tmp, indent=2)
            temp_name = tmp.name
        os.replace(temp_name, path)
    except Exception as exc:
        logger.warning('Failed to save %s: %s', path, exc)


def update_runtime_state(**kwargs) -> None:
    state = load_json(RUNTIME_STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    state.update(kwargs)
    state['updated_at_sgt'] = datetime.now(SG_TZ).strftime('%Y-%m-%d %H:%M:%S')
    save_json(RUNTIME_STATE_FILE, state)
