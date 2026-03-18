"""
Telegram connection test — CPR Gold Bot
Run: python test_telegram.py
"""
from telegram_alert import TelegramAlert

if __name__ == "__main__":
    alert = TelegramAlert()
    ok = alert.send(
        "✅ Test message — Telegram is connected and working!\n"
        "CPR Gold Bot is ready to deploy."
    )
    if ok:
        print("✅ Message sent successfully.")
    else:
        print("❌ Failed to send. Check TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in secrets.json.")
