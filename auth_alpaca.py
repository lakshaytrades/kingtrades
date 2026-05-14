"""
auth_alpaca.py — Alpaca API Key Manager

Unlike Groww's daily TOTP tokens, Alpaca API keys never expire.
No daily re-login needed. Just load from env once and trade.

Non-US residents using Alpaca international accounts are not subject
to the US PDT rule — full 4x intraday leverage available without the
$25k minimum restriction.

Env vars required:
  ALPACA_API_KEY       — from alpaca.markets → Paper or Live
  ALPACA_SECRET_KEY    — from alpaca.markets
  ALPACA_PAPER         — "true" (default) for paper trading, "false" for live

Paper trading URL:  https://paper-api.alpaca.markets
Live trading URL:   https://api.alpaca.markets
"""

import logging
import os
from typing import Optional, Tuple

from utils import format_ist_timestamp

logger = logging.getLogger(__name__)


class AlpacaAuthManager:
    """
    Manages Alpaca API credentials.
    Keys never expire — just load once and reuse all session.
    """

    def __init__(self):
        self._api_key    = os.getenv("ALPACA_API_KEY", "")
        self._secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        self._paper      = os.getenv("ALPACA_PAPER", "true").lower() != "false"
        self._trading_client  = None
        self._data_client     = None

    def is_configured(self) -> bool:
        return bool(self._api_key and self._secret_key)

    def get_trading_client(self):
        """Return cached TradingClient, creating if needed."""
        if self._trading_client is None:
            self._trading_client = self._make_trading_client()
        return self._trading_client

    def get_data_client(self):
        """Return cached StockHistoricalDataClient."""
        if self._data_client is None:
            self._data_client = self._make_data_client()
        return self._data_client

    def refresh_token_if_needed(self) -> bool:
        """No-op for Alpaca — keys never expire. Returns False (no refresh needed)."""
        return False

    def validate(self) -> Tuple[bool, str]:
        """Test API keys by fetching account info. Returns (ok, message)."""
        if not self.is_configured():
            return False, "ALPACA_API_KEY / ALPACA_SECRET_KEY missing from .env"
        try:
            client = self.get_trading_client()
            account = client.get_account()
            mode    = "PAPER" if self._paper else "LIVE"
            bp      = float(account.buying_power)
            logger.info(
                f"[{format_ist_timestamp()}] Alpaca {mode} account validated | "
                f"buying_power=${bp:,.0f} | status={account.status}"
            )
            return True, f"Alpaca {mode} OK | buying_power=${bp:,.0f}"
        except Exception as e:
            return False, f"Alpaca API error: {e}"

    # ─────────────────────────────────────────────────────────────────────
    # INTERNAL
    # ─────────────────────────────────────────────────────────────────────

    def _make_trading_client(self):
        try:
            from alpaca.trading.client import TradingClient
            client = TradingClient(
                api_key    = self._api_key,
                secret_key = self._secret_key,
                paper      = self._paper,
            )
            logger.info(
                f"[{format_ist_timestamp()}] AlpacaTradingClient ready "
                f"({'PAPER' if self._paper else 'LIVE'})"
            )
            return client
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] AlpacaTradingClient init failed: {e}")
            raise

    def _make_data_client(self):
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            client = StockHistoricalDataClient(
                api_key    = self._api_key,
                secret_key = self._secret_key,
            )
            return client
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] AlpacaDataClient init failed: {e}")
            raise


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_manager: Optional[AlpacaAuthManager] = None


def get_auth_manager() -> AlpacaAuthManager:
    global _manager
    if _manager is None:
        _manager = AlpacaAuthManager()
        ok, msg = _manager.validate()
        if ok:
            logger.info(f"[{format_ist_timestamp()}] {msg}")
        else:
            logger.warning(f"[{format_ist_timestamp()}] Alpaca auth warning: {msg}")
    return _manager
