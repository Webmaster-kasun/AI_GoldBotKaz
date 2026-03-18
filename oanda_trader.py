"""OANDA execution layer for the CPR Gold Bot.

Handles pricing, order placement, trade lookup, stop updates, and retry-safe
HTTP communication with OANDA.
"""
from __future__ import annotations

import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config_loader import load_secrets

log = logging.getLogger(__name__)


class OandaTrader:
    def __init__(self, demo: bool = True):
        secrets = load_secrets()
        self.api_key = secrets.get("OANDA_API_KEY", "")
        self.account_id = secrets.get("OANDA_ACCOUNT_ID", "")
        self.base_url = "https://api-fxpractice.oanda.com" if demo else "https://api-fxtrade.oanda.com"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self.session = requests.Session()
        retries = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT"],
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        masked = f"{self.api_key[:8]}****" if self.api_key else "(missing)"
        log.info("OANDA | Mode: %s", "DEMO" if demo else "LIVE")
        log.info("Account: %s | API Key: %s", self.account_id, masked)

    def _request(self, method: str, path: str, **kwargs):
        return self.session.request(
            method,
            f"{self.base_url}{path}",
            headers=self.headers,
            timeout=kwargs.pop("timeout", 15),
            **kwargs,
        )

    def login_with_balance(self) -> float | None:
        try:
            r = self._request("GET", f"/v3/accounts/{self.account_id}")
            if r.status_code == 200:
                bal = float(r.json()["account"]["balance"])
                log.info("Login success! Balance: $%.2f", bal)
                return bal
            log.error("Login failed: %s %s", r.status_code, r.text[:200])
            return None
        except Exception as e:
            log.error("Login error: %s", e)
            return None

    def get_margin_available(self) -> float | None:
        """Return the account's current marginAvailable in account currency."""
        try:
            r = self._request("GET", f"/v3/accounts/{self.account_id}")
            if r.status_code == 200:
                return float(r.json()["account"]["marginAvailable"])
            log.warning("get_margin_available: HTTP %s", r.status_code)
            return None
        except Exception as e:
            log.warning("get_margin_available error: %s", e)
            return None

    def get_price(self, instrument):
        try:
            r = self._request(
                "GET", f"/v3/accounts/{self.account_id}/pricing", params={"instruments": instrument}, timeout=10
            )
            if r.status_code != 200:
                log.error("get_price failed: %s %s", r.status_code, r.text[:200])
                return None, None, None
            price = r.json()["prices"][0]
            bid = float(price["bids"][0]["price"])
            ask = float(price["asks"][0]["price"])
            mid = (bid + ask) / 2
            return mid, bid, ask
        except Exception as e:
            log.error("get_price error: %s", e)
            return None, None, None

    def get_instrument_specs(self, instrument):
        if not hasattr(self, "_specs_cache"):
            self._specs_cache = {}
        if instrument in self._specs_cache:
            return self._specs_cache[instrument]

        defaults = {
            "name": instrument,
            "tradeUnitsPrecision": 0,
            "minimumTradeSize": 1,
            "pipLocation": -2 if instrument == "XAU_USD" else -4,
            "displayPrecision": 2 if instrument == "XAU_USD" else 5,
            "marginRate": 0.05,  # 5% = 20:1 leverage (conservative default for XAU_USD)
        }
        try:
            r = self._request(
                "GET", f"/v3/accounts/{self.account_id}/instruments", params={"instruments": instrument}, timeout=10
            )
            if r.status_code != 200:
                self._specs_cache[instrument] = defaults
                return defaults
            instruments = r.json().get("instruments", [])
            if not instruments:
                self._specs_cache[instrument] = defaults
                return defaults
            d = instruments[0]
            result = {
                "name": d.get("name", instrument),
                "tradeUnitsPrecision": int(d.get("tradeUnitsPrecision", defaults["tradeUnitsPrecision"])),
                "minimumTradeSize": float(d.get("minimumTradeSize", defaults["minimumTradeSize"])),
                "pipLocation": int(d.get("pipLocation", defaults["pipLocation"])),
                "displayPrecision": int(d.get("displayPrecision", defaults["displayPrecision"])),
                "marginRate": float(d.get("marginRate", defaults.get("marginRate", 0.05))),
            }
            self._specs_cache[instrument] = result
            return result
        except Exception as e:
            log.warning("get_instrument_specs error: %s", e)
            self._specs_cache[instrument] = defaults
            return defaults

    def get_position(self, instrument):
        try:
            r = self._request("GET", f"/v3/accounts/{self.account_id}/positions/{instrument}", timeout=10)
            if r.status_code == 200:
                pos = r.json()["position"]
                long_units = int(float(pos["long"]["units"]))
                short_units = int(float(pos["short"]["units"]))
                if long_units != 0 or short_units != 0:
                    return pos
            return None
        except Exception as e:
            log.error("get_position error: %s", e)
            return None

    def get_open_trades(self, instrument: str | None = None) -> list:
        try:
            r = self._request("GET", f"/v3/accounts/{self.account_id}/openTrades", timeout=10)
            if r.status_code == 200:
                trades = r.json().get("trades", [])
                if instrument:
                    trades = [t for t in trades if t.get("instrument") == instrument]
                return trades
            log.warning("get_open_trades failed: %s %s", r.status_code, r.text[:200])
            return []
        except Exception as e:
            log.error("get_open_trades error: %s", e)
            return []

    def get_open_trades_count(self, instrument: str) -> int:
        return len(self.get_open_trades(instrument))

    def get_recent_closed_trades(self, instrument: str | None = None, count: int = 20) -> list:
        try:
            r = self._request(
                "GET",
                f"/v3/accounts/{self.account_id}/trades",
                params={"state": "CLOSED", "count": count},
                timeout=10,
            )
            if r.status_code == 200:
                trades = r.json().get("trades", [])
                if instrument:
                    trades = [t for t in trades if t.get("instrument") == instrument]
                return trades
            log.warning("get_recent_closed_trades failed: %s %s", r.status_code, r.text[:200])
            return []
        except Exception as e:
            log.error("get_recent_closed_trades error: %s", e)
            return []

    def check_pnl(self, position):
        try:
            long_pnl = float(position["long"].get("unrealizedPL", 0))
            short_pnl = float(position["short"].get("unrealizedPL", 0))
            return long_pnl + short_pnl
        except Exception:
            return 0

    def place_order(self, instrument, direction, size, stop_distance, limit_distance, bid: float = None, ask: float = None):
        try:
            specs = self.get_instrument_specs(instrument)
            units_precision = int(specs.get("tradeUnitsPrecision", 0))
            display_precision = int(specs.get("displayPrecision", 2))
            pip_location = int(specs.get("pipLocation", -2))
            pip = 10 ** pip_location

            factor = 10 ** max(units_precision, 0)
            normalized_size = int(abs(float(size)) * factor) / factor
            units_value = normalized_size if direction == "BUY" else -normalized_size
            units_str = f"{units_value:.{units_precision}f}" if units_precision > 0 else str(int(units_value))

            if bid is None or ask is None:
                price, bid, ask = self.get_price(instrument)
                if price is None:
                    return {"success": False, "error": "Cannot get price"}

            entry = ask if direction == "BUY" else bid
            if direction == "BUY":
                sl_price = round(entry - (stop_distance * pip), display_precision)
                tp_price = round(entry + (limit_distance * pip), display_precision)
            else:
                sl_price = round(entry + (stop_distance * pip), display_precision)
                tp_price = round(entry - (limit_distance * pip), display_precision)

            log.info("Placing %s %s | units=%s | entry=%.2f | SL=%.2f | TP=%.2f", direction, instrument, units_str, entry, sl_price, tp_price)
            payload = {
                "order": {
                    "type": "MARKET",
                    "instrument": instrument,
                    "units": units_str,
                    "timeInForce": "FOK",
                    "stopLossOnFill": {"price": str(sl_price), "timeInForce": "GTC"},
                    "takeProfitOnFill": {"price": str(tp_price), "timeInForce": "GTC"},
                }
            }
            r = self._request("POST", f"/v3/accounts/{self.account_id}/orders", json=payload, timeout=15)
            data = r.json()
            log.info("Order response: %s %s", r.status_code, str(data)[:300])
            if r.status_code in [200, 201]:
                if "orderFillTransaction" in data:
                    fill = data["orderFillTransaction"]
                    trade_id = fill.get("id", "N/A")
                    try:
                        fill_price = float(fill.get("price", 0))
                    except (TypeError, ValueError):
                        fill_price = None
                    log.info("Trade placed! ID: %s | Fill price: %s", trade_id, fill_price)
                    return {"success": True, "trade_id": trade_id, "fill_price": fill_price}
                if "orderCancelTransaction" in data:
                    reason = data["orderCancelTransaction"].get("reason", "Unknown")
                    return {"success": False, "error": f"Order cancelled: {reason}"}
                return {"success": True}

            error = data.get("errorMessage", str(data))
            return {"success": False, "error": error}
        except Exception as e:
            log.error("place_order error: %s", e)
            return {"success": False, "error": str(e)}

    def get_trade_pnl(self, trade_id: str):
        try:
            r = self._request("GET", f"/v3/accounts/{self.account_id}/trades/{trade_id}", timeout=10)
            if r.status_code == 200:
                trade = r.json().get("trade", {})
                if trade.get("state") == "CLOSED":
                    pnl = trade.get("realizedPL")
                    return float(pnl) if pnl is not None else None
            return None
        except Exception as e:
            log.warning("get_trade_pnl error: %s", e)
            return None

    def modify_sl(self, trade_id: str, new_sl_price: float) -> dict:
        try:
            payload = {"stopLoss": {"price": f"{new_sl_price:.2f}", "timeInForce": "GTC"}}
            r = self._request("PUT", f"/v3/accounts/{self.account_id}/trades/{trade_id}/orders", json=payload, timeout=15)
            data = r.json()
            if r.status_code in [200, 201]:
                log.info("SL moved to %.2f for trade %s", new_sl_price, trade_id)
                return {"success": True}
            log.warning("modify_sl failed: %s %s", r.status_code, str(data)[:200])
            return {"success": False, "error": data.get("errorMessage", str(data))}
        except Exception as e:
            log.error("modify_sl error: %s", e)
            return {"success": False, "error": str(e)}

    def get_open_trade(self, trade_id: str) -> dict | None:
        try:
            r = self._request("GET", f"/v3/accounts/{self.account_id}/trades/{trade_id}", timeout=10)
            if r.status_code == 200:
                trade = r.json().get("trade", {})
                if trade.get("state") == "OPEN":
                    return trade
            return None
        except Exception as e:
            log.warning("get_open_trade error: %s", e)
            return None

    def close_position(self, instrument):
        try:
            r = self._request(
                "PUT",
                f"/v3/accounts/{self.account_id}/positions/{instrument}/close",
                json={"longUnits": "ALL", "shortUnits": "ALL"},
                timeout=15,
            )
            return {"success": r.status_code == 200}
        except Exception as e:
            log.error("close_position error: %s", e)
            return {"success": False, "error": str(e)}
