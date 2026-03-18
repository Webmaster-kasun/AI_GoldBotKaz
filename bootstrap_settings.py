"""
Manual bootstrap helper (optional).

Normally you do NOT need to run this manually because the bot automatically
bootstraps /data/settings.json on first start.

But if you want to force the check yourself:

    python bootstrap_settings.py
"""

from config_loader import ensure_persistent_settings

if __name__ == "__main__":
    path = ensure_persistent_settings()
    print(f"Persistent settings ready at: {path}")
