"""
data_fetch_alpaca.py — US Stock Market Data via Alpaca

Provides the same interface as data_fetch_groww.py so main.py
and signal_generator.py need zero changes.

Data sources:
  - Real-time quotes: Alpaca Markets Data API (IEX feed, free tier)
  - Historical OHLCV: Alpaca bars endpoint (1min, 5min, 15min, 1hr)
  - Account balance: Alpaca trading API (buying_power / 4 = real capital)

Leverage note:
  Alpaca reports buying_power already multiplied by margin (4x for PDT,
  or configured margin multiplier). We divide by 4 to get "real capital"
  that the risk manager understands, then risk_manager × 4x position sizing
  matches the actual margin available.
"""

import logging
import time as _time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from auth_alpaca import get_auth_manager
from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)

# ET timezone for market hours and bar timestamps
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except ImportError:
    import pytz
    ET = pytz.timezone("America/New_York")

# ─────────────────────────────────────────────────────────────────────────────
# US MOMENTUM WATCHLIST  (high-liquidity stocks best for intraday momentum)
# ─────────────────────────────────────────────────────────────────────────────

US_WATCHLIST = [
    # Mega-cap tech — highest volume, tight spreads, explosive momentum
    "NVDA", "TSLA", "AAPL", "MSFT", "META", "AMZN", "GOOGL", "AMD",
    # High-beta momentum names
    "NFLX", "COIN", "SHOP", "UBER", "PLTR", "SOFI",
    # Sector leaders
    "JPM", "GS", "XOM", "CVX", "BA", "CAT",
    # ETFs for pairs / macro context
    "SPY", "QQQ", "XLK", "XLF", "XLE",
]

# Known high-correlation pairs for pairs_engine (same interface as NSE_PAIRS)
US_CORR_PAIRS = [
    ("NVDA", "AMD"),       # Semiconductor
    ("AAPL", "MSFT"),      # Mega-cap tech
    ("META", "GOOGL"),     # Digital ads
    ("JPM", "GS"),         # Banks
    ("XOM", "CVX"),        # Oil majors
    ("SPY", "QQQ"),        # Index ETFs
]


class AlpacaDataFetcher:
    """
    Fetches US stock market data via Alpaca API.
    Interface matches data_fetch_groww.GrowwDataFetcher exactly.
    """

    BALANCE_CACHE_TTL = 30   # seconds before re-fetching balance

    def __init__(self):
        self._auth          = get_auth_manager()
        self._balance_cache: Dict = {}
        self._balance_ts: float   = 0.0
        self._quote_cache:  Dict  = {}
        self._quote_ts:     Dict  = {}

    # ─────────────────────────────────────────────────────────────────────
    # QUOTES
    # ─────────────────────────────────────────────────────────────────────

    def get_quote(self, symbol: str) -> Dict:
        """
        Returns dict: {ltp, bid, ask, volume, open, high, low, close, change_pct}
        Cached for 2s to avoid hammering the API on multi-symbol scans.
        """
        now = _time.monotonic()
        cached_at = self._quote_ts.get(symbol, 0)
        if now - cached_at < 2.0 and symbol in self._quote_cache:
            return self._quote_cache[symbol]

        try:
            from alpaca.data.requests import StockLatestQuoteRequest, StockLatestBarRequest
            data_client = self._auth.get_data_client()

            quote_req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            bar_req   = StockLatestBarRequest(symbol_or_symbols=symbol)

            quote_data = data_client.get_stock_latest_quote(quote_req)
            bar_data   = data_client.get_stock_latest_bar(bar_req)

            q   = quote_data[symbol]
            b   = bar_data[symbol]
            mid = (q.ask_price + q.bid_price) / 2

            result = {
                "ltp":        float(q.ask_price or mid),
                "bid":        float(q.bid_price),
                "ask":        float(q.ask_price),
                "volume":     int(b.volume),
                "open":       float(b.open),
                "high":       float(b.high),
                "low":        float(b.low),
                "close":      float(b.close),
                "change_pct": round((float(b.close) - float(b.open)) / max(float(b.open), 0.01) * 100, 2),
                "symbol":     symbol,
            }
            self._quote_cache[symbol] = result
            self._quote_ts[symbol]    = now
            return result

        except Exception as e:
            logger.debug(f"get_quote({symbol}) failed: {e}")
            return {
                "ltp": 0.0, "bid": 0.0, "ask": 0.0, "volume": 0,
                "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0,
                "change_pct": 0.0, "symbol": symbol,
            }

    def get_ltp(self, symbol: str) -> float:
        return self.get_quote(symbol).get("ltp", 0.0)

    def scan_watchlist(self, symbols: List[str]) -> List[Dict]:
        """Batch quote for list of symbols. Returns [{symbol, ltp, volume, change_pct}]."""
        if not symbols:
            return []
        try:
            from alpaca.data.requests import StockLatestBarRequest
            data_client = self._auth.get_data_client()
            req  = StockLatestBarRequest(symbol_or_symbols=symbols)
            bars = data_client.get_stock_latest_bar(req)

            result = []
            for sym, bar in bars.items():
                chg = round((float(bar.close) - float(bar.open)) / max(float(bar.open), 0.01) * 100, 2)
                result.append({
                    "symbol":     sym,
                    "ltp":        float(bar.close),
                    "volume":     int(bar.volume),
                    "change_pct": chg,
                    "open":       float(bar.open),
                    "high":       float(bar.high),
                    "low":        float(bar.low),
                })
            return result
        except Exception as e:
            logger.debug(f"scan_watchlist failed: {e}")
            return []

    # ─────────────────────────────────────────────────────────────────────
    # HISTORICAL OHLCV
    # ─────────────────────────────────────────────────────────────────────

    def get_ohlcv(
        self,
        symbol:       str,
        interval:     str = "5minute",
        lookback_days: int = 5,
    ) -> pd.DataFrame:
        """
        Returns DataFrame with columns: open, high, low, close, volume
        Index: datetime in ET (matches NSE IST behaviour — timezone-aware)

        interval: "5minute" | "15minute" | "60minute" | "1minute"
        """
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

            tf_map = {
                "1minute":   TimeFrame(1,  TimeFrameUnit.Minute),
                "5minute":   TimeFrame(5,  TimeFrameUnit.Minute),
                "15minute":  TimeFrame(15, TimeFrameUnit.Minute),
                "60minute":  TimeFrame(1,  TimeFrameUnit.Hour),
                "1hour":     TimeFrame(1,  TimeFrameUnit.Hour),
                "day":       TimeFrame(1,  TimeFrameUnit.Day),
            }
            tf = tf_map.get(interval, TimeFrame(5, TimeFrameUnit.Minute))

            now_et  = datetime.now(ET)
            start   = now_et - timedelta(days=lookback_days + 2)  # +2 for weekends

            req  = StockBarsRequest(
                symbol_or_symbols = symbol,
                timeframe         = tf,
                start             = start,
                end               = now_et,
                adjustment        = "split",
            )
            data_client = self._auth.get_data_client()
            bars = data_client.get_stock_bars(req)

            if symbol not in bars or not bars[symbol]:
                return pd.DataFrame()

            rows = []
            for bar in bars[symbol]:
                rows.append({
                    "timestamp": bar.timestamp,
                    "open":      float(bar.open),
                    "high":      float(bar.high),
                    "low":       float(bar.low),
                    "close":     float(bar.close),
                    "volume":    int(bar.volume),
                })

            df = pd.DataFrame(rows)
            df.set_index("timestamp", inplace=True)
            df.index = pd.DatetimeIndex(df.index).tz_convert(ET)
            df.sort_index(inplace=True)
            return df

        except Exception as e:
            logger.debug(f"get_ohlcv({symbol}, {interval}) failed: {e}")
            return pd.DataFrame()

    def get_multi_timeframe_data(self, symbol: str) -> Dict[str, Optional[pd.DataFrame]]:
        """
        Fetch 5m / 15m / 1h OHLCV for signal_generator multi-timeframe analysis.
        Returns same structure as GrowwDataFetcher.get_multi_timeframe_data().
        Keys: "5m", "15m", "1h"
        """
        specs = [("5m", "5minute", 5), ("15m", "15minute", 10), ("1h", "1hour", 30)]
        data: Dict[str, Optional[pd.DataFrame]] = {}
        for key, interval, days in specs:
            try:
                df = self.get_ohlcv(symbol, interval=interval, lookback_days=days)
                data[key] = df if not df.empty else None
            except Exception as e:
                logger.debug(f"MTF {symbol}/{key}: {e}")
                data[key] = None
        return data

    def get_nifty_quote(self) -> Optional[Dict]:
        """
        For US market: return SPY (S&P500 ETF) as the market index proxy.
        Replaces NSE Nifty 50 reference for regime / relative-strength checks.
        """
        try:
            q = self.get_quote("SPY")
            if q and q.get("ltp"):
                return {
                    "symbol": "SPY",
                    "ltp":    q["ltp"],
                    "change_pct": q.get("change_pct", 0.0),
                }
        except Exception as e:
            logger.debug(f"get_nifty_quote (SPY proxy) failed: {e}")
        return None

    def get_candles(self, symbol: str, interval: str = "5m", days: int = 5) -> Optional[pd.DataFrame]:
        """Alias matching GrowwDataFetcher.get_candles() so shared code works."""
        interval_map = {"5m": "5minute", "15m": "15minute", "1h": "1hour", "60m": "1hour"}
        alpaca_interval = interval_map.get(interval, interval)
        df = self.get_ohlcv(symbol, interval=alpaca_interval, lookback_days=days)
        return df if not df.empty else None

    # ─────────────────────────────────────────────────────────────────────
    # ACCOUNT BALANCE
    # ─────────────────────────────────────────────────────────────────────

    def get_account_balance(self) -> Dict:
        """
        Returns: {available, used_margin, total, _from_cache}

        Alpaca buying_power already reflects 4x margin.
        We return buying_power/4 as 'available' so risk_manager treats
        it as real capital — position sizing then naturally uses 4x buying power.
        """
        now = _time.monotonic()
        if now - self._balance_ts < self.BALANCE_CACHE_TTL and self._balance_cache:
            cached = self._balance_cache.copy()
            cached["_from_cache"] = True
            return cached

        try:
            trading_client = self._auth.get_trading_client()
            account = trading_client.get_account()

            buying_power  = float(account.buying_power)
            equity        = float(account.equity)
            initial_margin= float(account.initial_margin or 0)

            result = {
                "available":    round(buying_power / 4, 2),   # real capital (4x leverage)
                "buying_power": round(buying_power, 2),        # actual 4x power
                "used_margin":  round(initial_margin, 2),
                "total":        round(equity, 2),
                "_from_cache":  False,
                "currency":     "USD",
            }
            self._balance_cache = result.copy()
            self._balance_ts    = now
            logger.debug(
                f"[{format_ist_timestamp()}] Balance: equity=${equity:,.0f} | "
                f"buying_power=${buying_power:,.0f} (4x)"
            )
            return result

        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] get_account_balance failed: {e}")
            if self._balance_cache:
                cached = self._balance_cache.copy()
                cached["_from_cache"] = True
                return cached
            return {"available": 0, "buying_power": 0, "used_margin": 0,
                    "total": 0, "_from_cache": False, "currency": "USD"}

    # ─────────────────────────────────────────────────────────────────────
    # POSITIONS & ORDERS
    # ─────────────────────────────────────────────────────────────────────

    def get_positions(self) -> List[Dict]:
        """Returns list of open positions as dicts."""
        try:
            trading_client = self._auth.get_trading_client()
            positions = trading_client.get_all_positions()
            result = []
            for p in positions:
                result.append({
                    "symbol":     p.symbol,
                    "qty":        int(p.qty),
                    "side":       p.side.value,   # "long" or "short"
                    "avg_price":  float(p.avg_entry_price),
                    "market_val": float(p.market_value or 0),
                    "unrealized_pnl": float(p.unrealized_pl or 0),
                })
            return result
        except Exception as e:
            logger.debug(f"get_positions failed: {e}")
            return []

    def get_order_status(self, order_id: str) -> Dict:
        """Returns order status dict."""
        try:
            trading_client = self._auth.get_trading_client()
            order = trading_client.get_order_by_id(order_id)
            filled_qty = int(order.filled_qty or 0)
            return {
                "order_id":   str(order.id),
                "status":     order.status.value,
                "filled_qty": filled_qty,
                "filled_avg_price": float(order.filled_avg_price or 0),
                "symbol":     order.symbol,
                "side":       order.side.value,
            }
        except Exception as e:
            logger.debug(f"get_order_status({order_id}) failed: {e}")
            return {"order_id": order_id, "status": "unknown", "filled_qty": 0}

    def is_market_open(self) -> bool:
        """Ask Alpaca if the US market is currently open."""
        try:
            trading_client = self._auth.get_trading_client()
            clock = trading_client.get_clock()
            return clock.is_open
        except Exception:
            return False


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_fetcher: Optional[AlpacaDataFetcher] = None


def get_data_fetcher() -> AlpacaDataFetcher:
    global _fetcher
    if _fetcher is None:
        _fetcher = AlpacaDataFetcher()
        logger.info(f"[{format_ist_timestamp()}] AlpacaDataFetcher initialized")
    return _fetcher
