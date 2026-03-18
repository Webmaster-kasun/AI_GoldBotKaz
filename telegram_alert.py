"""
Telegram Alert System — CPR Gold Bot
"""
import logging
import requests
from config_loader import load_secrets

log = logging.getLogger(__name__)


class TelegramAlert:
    def __init__(self):
        secrets       = load_secrets()
        self.token    = secrets.get("TELEGRAM_TOKEN", "")
        self.chat_id  = secrets.get("TELEGRAM_CHAT_ID", "")

    def send(self, message: str) -> bool:
        if not self.token or not self.chat_id:
            log.warning("Telegram not configured.")
            return False
        try:
            url  = f"https://api.telegram.org/bot{self.token}/sendMessage"
            text = f"🤖 CPR Gold Bot\n{'─'*22}\n{message}"
            r    = requests.post(url, data={"chat_id": self.chat_id, "text": text}, timeout=10)
            if r.status_code == 200:
                log.info("Telegram sent!")
                return True
            log.warning("Telegram error: %s", r.text)
            return False
        except Exception as e:
            log.error("Telegram error: %s", e)
            return False
