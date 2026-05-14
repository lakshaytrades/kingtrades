"""
broker.py — Broker Adapter (Alpaca — US NYSE/NASDAQ)

Unified interface so main.py imports from here:

  from broker import (
      get_auth_manager, get_data_fetcher, get_executor,
      is_market_open, is_pre_market, is_squareoff_time,
      should_force_squareoff, WATCHLIST, CURRENCY_SYMBOL,
      HEALTH_CHECK_SYMBOL, MARKET_NAME,
  )
"""

import logging

logger = logging.getLogger(__name__)

from auth_alpaca import get_auth_manager, AlpacaAuthManager as AuthManagerClass
from data_fetch_alpaca import get_data_fetcher, AlpacaDataFetcher as DataFetcherClass
from data_fetch_alpaca import US_WATCHLIST as WATCHLIST
from execution_alpaca import AlpacaExecutor as ExecutorClass
from utils import (
    is_market_open_et    as is_market_open,
    is_us_squareoff_time as is_squareoff_time,
    is_us_squareoff_time as should_force_squareoff,
    format_et_timestamp  as format_market_timestamp,
    get_current_et_time  as get_current_market_time,
)

CURRENCY_SYMBOL     = "$"
HEALTH_CHECK_SYMBOL = "AAPL"
MARKET_NAME         = "NYSE/NASDAQ"


def is_pre_market() -> bool:
    """US pre-market: 8:30–9:30 AM ET."""
    from utils import is_pre_market_et
    return is_pre_market_et()


def do_morning_login() -> bool:
    """Alpaca: validate API keys (no TOTP needed)."""
    mgr = get_auth_manager()
    ok, msg = mgr.validate()
    if ok:
        logger.info(f"Alpaca auth OK: {msg}")
    else:
        logger.error(f"Alpaca auth FAIL: {msg}")
    return ok


def get_executor(risk_manager, live_enabled: bool = False):
    from execution_alpaca import get_executor as _get
    return _get(risk_manager, live_enabled)


logger.info(f"Broker adapter loaded: ALPACA ({MARKET_NAME})")
