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
from price_stream import get_price_stream

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
        self._auth              = get_auth_manager()
        self._balance_cache:    Dict  = {}
        self._balance_ts:       float = 0.0
        self._quote_cache:      Dict  = {}
        self._quote_ts:         Dict  = {}
        self._prev_close_cache: Dict  = {}   # symbol → prev-day close price
        self._prev_close_date:  str   = ""   # cache is valid only for this calendar date

    # ─────────────────────────────────────────────────────────────────────
    # PREV-CLOSE CACHE
    # ─────────────────────────────────────────────────────────────────────

    def _get_prev_close(self, symbol: str) -> float:
        """
        Returns yesterday's official closing price for the symbol.
        Uses the last fully completed daily bar (bars[-2] when today's session
        is open, bars[-1] pre-market / after yesterday's close).

        Result is cached per-symbol for the entire trading day and reset at midnight.
        Falls back to 0 (caller must guard division by zero) on any API error.
        """
        from datetime import date as _date
        today_str = str(_date.today())

        if self._prev_close_date != today_str:
            self._prev_close_cache.clear()
            self._prev_close_date = today_str

        if symbol in self._prev_close_cache:
            return self._prev_close_cache[symbol]

        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            from datetime import datetime as _dt, timedelta as _td

            data_client = self._auth.get_data_client()
            start = _dt.now(ET) - _td(days=7)   # fetch last 7 calendar days (~5 trading days)
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=start,
                feed="iex",
            )
            bars_resp = data_client.get_stock_bars(req)
            bars = bars_resp[symbol] if symbol in bars_resp else []

            if len(bars) >= 2:
                prev_close = float(bars[-2].close)   # second-to-last bar = yesterday
            elif len(bars) == 1:
                prev_close = float(bars[-1].close)   # only one bar available
            else:
                logger.debug(f"_get_prev_close({symbol}): no daily bars returned")
                return 0.0

            self._prev_close_cache[symbol] = prev_close
            return prev_close

        except Exception as _e:
            logger.debug(f"_get_prev_close({symbol}): {_e}")
            return 0.0

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
            ltp = float(q.ask_price or mid)

            prev_close = self._get_prev_close(symbol)
            if prev_close > 0:
                change_pct = round((ltp - prev_close) / prev_close * 100, 2)
            else:
                # Fallback: intra-bar change when prev_close unavailable
                change_pct = round((float(b.close) - float(b.open)) / max(float(b.open), 0.01) * 100, 2)

            result = {
                "ltp":        ltp,
                "bid":        float(q.bid_price),
                "ask":        float(q.ask_price),
                "volume":     int(b.volume),
                "open":       float(b.open),
                "high":       float(b.high),
                "low":        float(b.low),
                "close":      float(b.close),
                "prev_close": prev_close,
                "change_pct": change_pct,
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

    def get_quote_live(self, symbol: str) -> Optional[Dict]:
        """
        Returns the latest quote for *symbol*, preferring the live WebSocket
        cache (sub-second latency) and falling back to the REST ``get_quote()``
        if the stream is not running or has not yet received data for the symbol.

        Return value has the same keys as ``get_quote()``:
            ltp, bid, ask, volume, timestamp, change_pct, prev_close
        (REST fallback also includes open, high, low, close, symbol.)

        This is a drop-in upgrade path — existing callers of ``get_quote()``
        can switch to ``get_quote_live()`` for lower latency with zero other
        changes.
        """
        stream = get_price_stream()
        if stream.is_running():
            cached = stream.get_quote(symbol)
            if cached is not None:
                return cached
            # Stream running but no data yet for this symbol — subscribe it
            # so the next call will hit the fast path.
            if symbol.upper() not in stream.subscribed_symbols:
                try:
                    stream.start([symbol])
                except Exception as exc:
                    logger.debug(
                        f"[{format_ist_timestamp()}] get_quote_live: "
                        f"subscribe {symbol} failed: {exc}"
                    )

        # Fallback to REST poll
        logger.debug(
            f"[{format_ist_timestamp()}] get_quote_live({symbol}): "
            "stream miss — falling back to REST"
        )
        rest = self.get_quote(symbol)
        return rest if rest else None

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
                ltp_bar    = float(bar.close)
                prev_c     = self._get_prev_close(sym)
                if prev_c > 0:
                    chg = round((ltp_bar - prev_c) / prev_c * 100, 2)
                else:
                    chg = round((ltp_bar - float(bar.open)) / max(float(bar.open), 0.01) * 100, 2)
                result.append({
                    "symbol":     sym,
                    "ltp":        ltp_bar,
                    "volume":     int(bar.volume),
                    "change_pct": chg,
                    "prev_close": prev_c,
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
                feed              = "iex",   # free tier — SIP requires paid subscription
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
            logger.warning(f"[{format_ist_timestamp()}] get_ohlcv({symbol}, {interval}) FAILED: {e}")
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
                logger.warning(f"[{format_ist_timestamp()}] MTF {symbol}/{key} FAILED: {e}")
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

            import os as _os
            paper = _os.getenv("ALPACA_PAPER", "true").lower() != "false"
            # Paper accounts and non-PDT accounts have 1x buying power (equity = real capital).
            # Live PDT-qualified accounts report buying_power at 4x margin — divide to get real capital.
            # Use non_marginable_buying_power if available, else equity for paper, buying_power/4 for live.
            non_margin_bp = float(getattr(account, "non_marginable_buying_power", 0) or 0)
            if paper:
                # Paper: Alpaca reports buying_power as 2x for paper accounts (or just equity)
                # Use equity as the safe available capital measure
                available = round(equity, 2)
            elif non_margin_bp > 0 and non_margin_bp < buying_power * 0.6:
                # Live non-PDT or cash account: use non-marginable BP directly
                available = round(non_margin_bp, 2)
            else:
                # Live PDT 4x margin account
                available = round(buying_power / 4, 2)

            result = {
                "available":    available,
                "buying_power": round(buying_power, 2),
                "used_margin":  round(initial_margin, 2),
                "total":        round(equity, 2),
                "_from_cache":  False,
                "currency":     "USD",
            }
            self._balance_cache = result.copy()
            self._balance_ts    = now
            logger.debug(
                f"[{format_ist_timestamp()}] Balance: equity=${equity:,.0f} | "
                f"available=${available:,.0f} | buying_power=${buying_power:,.0f} "
                f"({'paper' if paper else '4x margin'})"
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
