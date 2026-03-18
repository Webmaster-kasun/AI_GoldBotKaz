from __future__ import annotations

from pathlib import Path

from config_loader import DATA_DIR, SETTINGS_FILE, load_secrets, load_settings


def run_startup_checks() -> list[str]:
    settings = load_settings()
    secrets = load_secrets()
    warnings: list[str] = []

    if not Path(DATA_DIR).exists():
        warnings.append(f'DATA_DIR missing: {DATA_DIR}')
    if not Path(SETTINGS_FILE).exists():
        warnings.append(f'settings file missing: {SETTINGS_FILE}')
    if not secrets.get('OANDA_ACCOUNT_ID'):
        warnings.append('OANDA_ACCOUNT_ID not set; broker calls will fail until configured')
    if not secrets.get('OANDA_API_KEY'):
        warnings.append('OANDA_API_KEY not set; broker calls will fail until configured')
    if not secrets.get('TELEGRAM_TOKEN') or not secrets.get('TELEGRAM_CHAT_ID'):
        warnings.append('Telegram not fully configured; alerts will be skipped')
    if int(settings.get('cycle_minutes', 5)) <= 0:
        warnings.append('cycle_minutes must be > 0')
    return warnings
