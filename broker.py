"""
broker.py — Broker Adapter (switch between Alpaca and Groww)

Set BROKER=alpaca (default) or BROKER=groww in .env to select.

Exports a unified interface so main.py imports from here and
never needs to know which broker is active:

  from broker import (
      get_auth_manager, get_data_fetcher, get_executor_class,
      is_market_open, is_pre_market, is_squareoff_time,
      should_force_squareoff, MARKET_SYMBOL_AAPL_OR_RELIANCE,
      WATCHLIST, CURRENCY_SYMBOL
  )
"""

import logging
import os

logger = logging.getLogger(__name__)

BROKER = os.getenv("BROKER", "alpaca").lower()


# ─────────────────────────────────────────────────────────────────────────────
# ALPACA (US stocks — default)
# ─────────────────────────────────────────────────────────────────────────────

if BROKER == "alpaca":
    from auth_alpaca import get_auth_manager, AlpacaAuthManager as AuthManagerClass
    from data_fetch_alpaca import get_data_fetcher, AlpacaDataFetcher as DataFetcherClass
    from data_fetch_alpaca import US_WATCHLIST as WATCHLIST
    from execution_alpaca import AlpacaExecutor as ExecutorClass
    from utils import (
        is_market_open_et  as is_market_open,
        is_us_squareoff_time as is_squareoff_time,
        is_us_squareoff_time as should_force_squareoff,
        format_et_timestamp  as format_market_timestamp,
        get_current_et_time  as get_current_market_time,
    )

    CURRENCY_SYMBOL    = "$"
    HEALTH_CHECK_SYMBOL = "AAPL"
    MARKET_NAME        = "NYSE/NASDAQ"

    def is_pre_market() -> bool:
        """US pre-market: 8:30–9:30 AM ET (watchlist prep window)."""
        from utils import get_current_et_time
        from datetime import time
        t = get_current_et_time().time()
        return time(8, 30) <= t < time(9, 30)

    def do_morning_login() -> bool:
        """Alpaca: just validate API keys (no TOTP needed)."""
        mgr = get_auth_manager()
        ok, msg = mgr.validate()
        if ok:
            logger.info(f"Alpaca auth OK: {msg}")
        else:
            logger.error(f"Alpaca auth FAIL: {msg}")
        return ok

    def get_executor(risk_manager, live_enabled: bool = False):
        from execution_alpaca import get_executor
        return get_executor(risk_manager, live_enabled)


# ─────────────────────────────────────────────────────────────────────────────
# GROWW (NSE India — legacy)
# ─────────────────────────────────────────────────────────────────────────────

elif BROKER == "groww":
    from auth_groww import get_auth_manager, GrowwAuthManager as AuthManagerClass  # type: ignore
    from data_fetch_groww import get_data_fetcher, GrowwDataFetcher as DataFetcherClass  # type: ignore
    from execution_groww import GrowwExecutor as ExecutorClass  # type: ignore
    from utils import (
        is_market_open_ist     as is_market_open,
        is_squareoff_time_ist  as is_squareoff_time,
        should_force_squareoff_ist as should_force_squareoff,
        format_ist_timestamp   as format_market_timestamp,
        get_current_ist_time   as get_current_market_time,
    )

    WATCHLIST          = []   # watchlist_manager fills this for NSE
    CURRENCY_SYMBOL    = "₹"
    HEALTH_CHECK_SYMBOL = "RELIANCE"
    MARKET_NAME        = "NSE"

    def is_pre_market() -> bool:
        from utils import is_pre_market_ist
        return is_pre_market_ist()

    def do_morning_login() -> bool:
        try:
            mgr     = get_auth_manager()
            success = mgr.refresh_token_if_needed()
            return success
        except Exception as e:
            logger.error(f"Groww morning login failed: {e}")
            return False

    def get_executor(risk_manager, live_enabled: bool = False):
        return ExecutorClass(risk_manager, live_enabled=live_enabled)


else:
    raise ValueError(f"Unknown BROKER={BROKER!r}. Set BROKER=alpaca or BROKER=groww in .env")


logger.info(f"Broker adapter loaded: {BROKER.upper()} ({MARKET_NAME})")
