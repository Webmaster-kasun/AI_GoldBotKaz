from __future__ import annotations

import signal
import sys
from datetime import datetime

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from bot import run_bot_cycle
from oanda_trader import OandaTrader
from telegram_alert import TelegramAlert
from telegram_templates import msg_startup
from config_loader import DATA_DIR, load_settings
from database import Database
from logging_utils import configure_logging, get_logger
from startup_checks import run_startup_checks

configure_logging()
logger = get_logger(__name__)
SG_TZ = pytz.timezone('Asia/Singapore')


def run_db_retention_cleanup():
    settings = load_settings()
    retention_days = int(settings.get('db_retention_days', 90))
    vacuum_weekly = bool(settings.get('db_vacuum_weekly', True))
    is_weekly_vacuum_day = datetime.now(SG_TZ).weekday() == 6

    logger.info('Starting DB retention cleanup | retention_days=%s | weekly_vacuum=%s', retention_days, vacuum_weekly)
    try:
        db = Database()
        summary = db.purge_old_data(retention_days=retention_days, vacuum=bool(vacuum_weekly and is_weekly_vacuum_day))
        logger.info('DB retention cleanup complete: %s', summary)
    except Exception as exc:
        logger.exception('DB retention cleanup failed: %s', exc)


def main():
    settings = load_settings()
    cycle_minutes = int(settings.get('cycle_minutes', 5))
    cleanup_hour = int(settings.get('db_cleanup_hour_sgt', 0))
    cleanup_minute = int(settings.get('db_cleanup_minute_sgt', 15))
    retention_days = int(settings.get('db_retention_days', 90))

    logger.info('%s — Scheduler starting', settings.get('bot_name', 'CPR Gold Bot'))
    logger.info('DATA_DIR : %s', DATA_DIR)
    logger.info('Python   : %s', sys.version.split()[0])
    for warning in run_startup_checks():
        logger.warning(warning)

    scheduler = BlockingScheduler(timezone=SG_TZ)
    scheduler.add_job(
        run_bot_cycle,
        IntervalTrigger(minutes=cycle_minutes),
        id='trade_cycle',
        name=f'{cycle_minutes}-min trade cycle',
        max_instances=1,
        coalesce=True,
        misfire_grace_time=max(cycle_minutes * 60, 60),
    )

    scheduler.add_job(
        run_db_retention_cleanup,
        CronTrigger(hour=cleanup_hour, minute=cleanup_minute, timezone=SG_TZ),
        id='db_retention_cleanup',
        name=f'DB retention cleanup ({retention_days}-day rolling)',
        max_instances=1,
        coalesce=True,
    )

    def _graceful_shutdown(signum, frame):
        logger.info('Received signal %s — shutting down scheduler', signum)
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    logger.info('Jobs scheduled:')
    logger.info('  Trade cycle  — every %s minutes', cycle_minutes)
    logger.info('  DB cleanup   — daily at %02d:%02d Asia/Singapore', cleanup_hour, cleanup_minute)
    logger.info('  DB retention — rolling %s days', retention_days)

    logger.info('Running startup cycle...')
    try:
        _trader = OandaTrader(demo=bool(settings.get('demo_mode', True)))
        _balance = _trader.login_with_balance() or 0.0
        _threshold = int(settings.get('signal_threshold', 4))
        _mode = 'DEMO' if settings.get('demo_mode', True) else 'LIVE'
        _version = settings.get('bot_name', 'CPR Gold Bot V2.0')
        TelegramAlert().send(msg_startup(_version, _mode, _balance, _threshold))
    except Exception as _e:
        logger.warning('Could not send startup Telegram alert: %s', _e)
    run_bot_cycle()
    scheduler.start()


if __name__ == '__main__':
    main()
