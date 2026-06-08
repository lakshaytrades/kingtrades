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
import os
import threading
import time as _time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

from auth_alpaca import get_auth_manager
from utils import format_ist_timestamp, get_current_ist_time
from price_stream import get_price_stream

logger = logging.getLogger(__name__)

# yfinance 1.3+ uses curl_cffi internally with impersonate="chrome" — do NOT pass
# a requests.Session (causes YFDataException). Let yfinance manage its own session.
_YF_SESSION = None

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


# ─────────────────────────────────────────────────────────────────────────────
# BarCache — batch-fetch all symbols once, serve instantly from memory
# ─────────────────────────────────────────────────────────────────────────────

class BarCache:
    """
    Fetches all watchlist bars in a single yfinance batch call (one HTTP request
    per interval). Refreshes every REFRESH_INTERVAL seconds in the background.
    Serves get_ohlcv() requests from in-memory DataFrames — sub-millisecond.
    """
    REFRESH_INTERVAL = 60    # refresh bars every 60 seconds (was 300 — 5-min stale caused missed entries)

    def __init__(self):
        self._cache: Dict[Tuple[str, str], pd.DataFrame] = {}  # (symbol, interval) → df
        self._last_refresh: Dict[str, float] = {}              # interval → monotonic time
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()   # prevents concurrent refreshes
        self._et = ET

    def get(self, symbol: str, interval: str, lookback_days: int) -> pd.DataFrame:
        cache_key = (symbol, interval)
        now = _time.monotonic()
        last = self._last_refresh.get(interval, 0.0)

        if (now - last) > self.REFRESH_INTERVAL:
            # Determine if cache has any data for this interval (empty = must block on first startup)
            with self._lock:
                cache_empty = not any(k[1] == interval for k in self._cache)

            if cache_empty:
                # First startup: must block until cache is populated
                with self._refresh_lock:
                    if (_time.monotonic() - self._last_refresh.get(interval, 0.0)) > self.REFRESH_INTERVAL:
                        self._last_refresh[interval] = _time.monotonic()
                        try:
                            self._refresh_interval(interval, lookback_days)
                        except Exception as _re:
                            self._last_refresh[interval] = 0.0
                            logger.warning(f"BarCache refresh failed ({interval}): {_re}")
            else:
                # Cache has data: non-blocking try — if another thread is refreshing,
                # serve the existing (slightly stale) data immediately rather than blocking
                acquired = self._refresh_lock.acquire(blocking=False)
                if acquired:
                    try:
                        if (_time.monotonic() - self._last_refresh.get(interval, 0.0)) > self.REFRESH_INTERVAL:
                            self._last_refresh[interval] = _time.monotonic()
                            try:
                                self._refresh_interval(interval, lookback_days)
                            except Exception as _re:
                                self._last_refresh[interval] = 0.0
                                logger.warning(f"BarCache refresh failed ({interval}): {_re}")
                    finally:
                        self._refresh_lock.release()
                # else: refresh already in progress — serve stale data, don't block

        with self._lock:
            df = self._cache.get(cache_key, pd.DataFrame())

        if df.empty:
            return df

        # Return only the requested lookback window
        cutoff = datetime.now(self._et) - timedelta(days=lookback_days + 2)
        return df[df.index >= cutoff]

    def _refresh_interval(self, interval: str, lookback_days: int):
        try:
            import config

            # Include sector ETFs and market reference symbols so market_internals
            # and breadth calculations work — these are NOT added to the trading watchlist.
            _REFERENCE_SYMBOLS = [
                "XLK", "XLF", "XLE", "XLY", "XLI", "XLB", "XLV", "XLU", "XLRE", "XLC", "XLP",
                "SPY", "QQQ", "IWM", "DIA", "UVXY",
            ]
            symbols = list(config.WATCHLIST)
            for s in _REFERENCE_SYMBOLS:
                if s not in symbols:
                    symbols.append(s)

            new_data: Dict[Tuple[str, str], pd.DataFrame] = {}
            t0 = _time.monotonic()

            # ── Alpaca PRIMARY ───────────────────────────────────────────────────
            # Alpaca is always authenticated (it's our broker) — use it as primary.
            # This guarantees data even when Yahoo Finance blocks the server IP.
            try:
                alpaca_count = self._fetch_alpaca_bars(interval, lookback_days, symbols, new_data)
                logger.info(f"[BarCache] Alpaca primary: {alpaca_count}/{len(symbols)} symbols loaded")
            except Exception as _ae:
                logger.warning(f"[BarCache] Alpaca primary error: {_ae}")
                alpaca_count = 0

            # ── yfinance SUPPLEMENT ─────────────────────────────────────────────
            # Fill in any symbols Alpaca missed (rate-limited, delisted, or free-plan gaps).
            _missing = [s for s in symbols if (s, interval) not in new_data]
            if _missing:
                try:
                    import yfinance as yf
                    yf_interval = {
                        "5minute": "5m", "15minute": "15m",
                        "1hour": "60m", "60minute": "60m",
                        "1minute": "1m", "day": "1d",
                    }.get(interval, "5m")
                    days = min(lookback_days + 2, 260 if yf_interval == "1d" else 59)
                    _dl_kwargs = dict(auto_adjust=True, progress=False, threads=False, group_by="ticker")
                    if _YF_SESSION is not None:
                        _dl_kwargs["session"] = _YF_SESSION

                    CHUNK = 25
                    yf_count = 0
                    chunks = [_missing[i:i+CHUNK] for i in range(0, len(_missing), CHUNK)]
                    for chunk in chunks:
                        try:
                            raw = yf.download(chunk, period=f"{days}d", interval=yf_interval, **_dl_kwargs)
                            if raw is None or raw.empty:
                                continue
                            for sym in chunk:
                                try:
                                    if len(chunk) == 1:
                                        df = raw.copy()
                                    else:
                                        lvl0 = list(raw.columns.get_level_values(0))
                                        lvl1 = list(raw.columns.get_level_values(1))
                                        if sym in lvl0:
                                            df = raw[sym].copy()
                                        elif sym in lvl1:
                                            df = raw.xs(sym, axis=1, level=1).copy()
                                        else:
                                            continue
                                    df.columns = [c.lower() for c in df.columns]
                                    needed = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
                                    if len(needed) < 5:
                                        continue
                                    df = df[needed].dropna()
                                    if df.index.tz is None:
                                        df.index = df.index.tz_localize("America/New_York")
                                    else:
                                        df.index = df.index.tz_convert("America/New_York")
                                    df.index.name = "timestamp"
                                    df.sort_index(inplace=True)
                                    if len(df) > 1 and yf_interval in ("1m", "5m", "15m", "60m"):
                                        _bar_mins = {"1m": 1, "5m": 5, "15m": 15, "60m": 60}.get(yf_interval, 5)
                                        _now_et = datetime.now(ET)
                                        _eod_keep = _now_et.hour > 15 or (_now_et.hour == 15 and _now_et.minute >= 50)
                                        if not _eod_keep and (_now_et - df.index[-1]).total_seconds() < _bar_mins * 60:
                                            df = df.iloc[:-1]
                                    if df.empty:
                                        continue
                                    new_data[(sym, interval)] = df
                                    yf_count += 1
                                except Exception:
                                    pass
                        except Exception as _ce:
                            logger.debug(f"BarCache yfinance chunk failed ({interval}): {_ce}")
                    if yf_count > 0:
                        logger.info(f"[BarCache] yfinance supplement: {yf_count} additional symbols")
                except Exception as _yfe:
                    logger.debug(f"[BarCache] yfinance supplement error: {_yfe}")

            elapsed = _time.monotonic() - t0
            with self._lock:
                self._cache.update(new_data)
            self._last_refresh[interval] = _time.monotonic()
            logger.info(
                f"[{format_ist_timestamp()}] BarCache refreshed {interval}: "
                f"{len(new_data)}/{len(symbols)} symbols in {elapsed:.1f}s"
            )
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] BarCache refresh {interval} failed: {e}")


    def _fetch_alpaca_bars(self, interval: str, lookback_days: int, symbols: list, new_data: dict) -> int:
        """Fetch OHLCV bars from Alpaca Markets data API. Primary data source."""
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
            from auth_alpaca import get_auth_manager
            from datetime import datetime as _dt, timedelta as _td

            _TF_MAP = {
                "1minute":  TimeFrame(1,  TimeFrameUnit.Minute),
                "5minute":  TimeFrame(5,  TimeFrameUnit.Minute),
                "15minute": TimeFrame(15, TimeFrameUnit.Minute),
                "1hour":    TimeFrame(1,  TimeFrameUnit.Hour),
                "60minute": TimeFrame(1,  TimeFrameUnit.Hour),
                "day":      TimeFrame(1,  TimeFrameUnit.Day),
            }
            _BAR_MINS = {"1minute": 1, "5minute": 5, "15minute": 15, "1hour": 60, "60minute": 60, "day": 1440}

            tf = _TF_MAP.get(interval, TimeFrame(5, TimeFrameUnit.Minute))
            data_client = get_auth_manager().get_data_client()
            start = _dt.now(ET) - _td(days=min(lookback_days + 2, 30))
            # End 16 min ago — valid for both free SIP (15-min delay) and paid Unlimited plans
            end = _dt.now(ET) - _td(minutes=16)

            count = 0
            CHUNK = 50  # Alpaca handles bulk requests efficiently

            for i in range(0, len(symbols), CHUNK):
                chunk = [s for s in symbols[i:i+CHUNK] if (s, interval) not in new_data]
                if not chunk:
                    continue
                try:
                    req = StockBarsRequest(
                        symbol_or_symbols=chunk,
                        timeframe=tf,
                        start=start,
                        end=end,
                        adjustment="split",
                    )
                    resp = data_client.get_stock_bars(req)
                    # BarSet in alpaca-py 0.43+ is a BaseDataSet (Pydantic model),
                    # NOT a dict — access via .data attribute.
                    bar_dict = resp.data if hasattr(resp, "data") else {}
                    for sym in chunk:
                        bars = bar_dict.get(sym) or []
                        if not bars:
                            continue
                        records = [
                            {"timestamp": b.timestamp, "open": float(b.open), "high": float(b.high),
                             "low": float(b.low), "close": float(b.close), "volume": int(b.volume)}
                            for b in bars
                        ]
                        df = pd.DataFrame(records).set_index("timestamp")
                        if df.index.tz is None:
                            df.index = df.index.tz_localize("UTC")
                        df.index = df.index.tz_convert("America/New_York")
                        df.index.name = "timestamp"
                        df.sort_index(inplace=True)
                        # Drop incomplete last bar — but keep it after 15:50 ET so EOD
                        # exits have current price data for the last 5 minutes of trading.
                        if len(df) > 1:
                            _bar_mins = _BAR_MINS.get(interval, 5)
                            _now_et = datetime.now(ET)
                            _eod_keep = _now_et.hour > 15 or (_now_et.hour == 15 and _now_et.minute >= 50)
                            if not _eod_keep and (_now_et - df.index[-1]).total_seconds() < _bar_mins * 60:
                                df = df.iloc[:-1]
                        if not df.empty:
                            new_data[(sym, interval)] = df
                            count += 1
                except Exception as _ce:
                    logger.debug(f"[BarCache] Alpaca chunk {i//CHUNK+1} failed: {_ce}")

            return count
        except Exception as e:
            logger.warning(f"[BarCache] Alpaca primary failed: {e}")
            return 0


_bar_cache: Optional["BarCache"] = None
_bar_cache_lock = threading.Lock()

def get_bar_cache() -> "BarCache":
    global _bar_cache
    if _bar_cache is None:
        with _bar_cache_lock:
            if _bar_cache is None:
                _bar_cache = BarCache()
    return _bar_cache


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
                end=_dt.now(ET) - _td(hours=1),  # daily bars: 1h delay is fine
                adjustment="split",
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

            # Compute VWAP and cumulative daily volume from cached 5-min bars
            above_vwap  = ltp >= float(b.open)   # default: price vs today's open
            vwap_val    = float(b.open)           # default fallback
            daily_volume = int(b.volume)
            try:
                cache        = get_bar_cache()
                df_5m        = cache.get(symbol, "5minute", 1)
                if not df_5m.empty:
                    session_open = datetime.now(ET).replace(hour=9, minute=30, second=0, microsecond=0)
                    df_sess = df_5m[df_5m.index >= session_open]
                    if not df_sess.empty:
                        tp  = (df_sess["high"] + df_sess["low"] + df_sess["close"]) / 3
                        vol = df_sess["volume"]
                        cum_vol = vol.sum()
                        if cum_vol > 0:
                            vwap_val    = float((tp * vol).sum() / cum_vol)
                            above_vwap  = ltp > vwap_val
                            daily_volume = int(cum_vol)
            except Exception:
                pass

            result = {
                "ltp":          ltp,
                "bid":          float(q.bid_price),
                "ask":          float(q.ask_price),
                "bid_size":     float(getattr(q, "bid_size", 0) or 0),
                "ask_size":     float(getattr(q, "ask_size", 0) or 0),
                "volume":       int(b.volume),
                "daily_volume": daily_volume,
                "open":         float(b.open),
                "high":         float(b.high),
                "low":          float(b.low),
                "close":        float(b.close),
                "prev_close":   prev_close,
                "change_pct":   change_pct,
                "symbol":       symbol,
                "vwap":         round(vwap_val, 4),
                "above_vwap":   above_vwap,
            }
            self._quote_cache[symbol] = result
            self._quote_ts[symbol]    = now
            return result

        except Exception as e:
            _err_str = str(e)
            if "429" in _err_str or "rate" in _err_str.lower():
                logger.warning(f"[data_fetch] Rate limit hit for {symbol} — backing off 3s")
                import time as _t; _t.sleep(3)
            else:
                logger.debug(f"get_quote({symbol}) failed: {e}")
            return {
                "ltp": 0.0, "bid": 0.0, "ask": 0.0, "volume": 0,
                "daily_volume": 0, "open": 0.0, "high": 0.0, "low": 0.0,
                "close": 0.0, "change_pct": 0.0, "symbol": symbol,
                "vwap": 0.0, "above_vwap": False, "error": True,
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
        # WebSocket disabled — always use REST polling
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
            # Data delay: free SIP plan requires 15-min delay; paid Unlimited plan can use 1 min.
            # Default 16 matches _fetch_alpaca_bars() so per-symbol calls work on the free plan.
            # Override with ALPACA_DATA_DELAY_MINUTES=1 for paid Unlimited plan.
            _delay  = int(os.getenv("ALPACA_DATA_DELAY_MINUTES", "16"))
            end_et  = now_et - timedelta(minutes=max(1, _delay))
            start   = now_et - timedelta(days=lookback_days + 2)  # +2 for weekends

            req  = StockBarsRequest(
                symbol_or_symbols = symbol,
                timeframe         = tf,
                start             = start,
                end               = end_et,
                adjustment        = "split",
            )
            data_client = self._auth.get_data_client()
            bars = data_client.get_stock_bars(req)

            if symbol not in bars or not bars[symbol]:
                # Alpaca returned 0 bars — fall through to yfinance fallback
                raise ValueError(f"Alpaca returned 0 bars for {symbol}/{interval}")

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
            _es = str(e)
            if "429" in _es or "rate" in _es.lower():
                logger.warning(f"[data_fetch] Rate limit on bars {symbol}/{interval} — backing off 3s")
                import time as _t; _t.sleep(3)
            else:
                logger.debug(f"Alpaca bars {symbol}/{interval}: {e} — using BarCache")
            return get_bar_cache().get(symbol, interval, lookback_days)

    def get_multi_timeframe_data(self, symbol: str) -> Dict[str, Optional[pd.DataFrame]]:
        """
        Fetch 5m / 15m / 1h OHLCV for signal_generator multi-timeframe analysis.
        BarCache-first: serves from in-memory cache when warm, avoiding per-symbol Alpaca
        calls during concurrent scans (which cause the 180s scan timeout on VPS).
        Keys: "5m", "15m", "1h"
        """
        specs = [("5m", "5minute", 5), ("15m", "15minute", 10), ("1h", "1hour", 30)]
        data: Dict[str, Optional[pd.DataFrame]] = {}
        bc = get_bar_cache()
        for key, interval, days in specs:
            try:
                # BarCache-first: in-memory hit is sub-millisecond and avoids Alpaca rate limits
                df = bc.get(symbol, interval, days)
                if not df.empty:
                    data[key] = df
                    continue
                # Cache miss — fall back to direct Alpaca call
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
        interval_map = {"5m": "5minute", "15m": "15minute", "1h": "1hour", "60m": "1hour",
                        "1Day": "day", "1d": "day", "day": "day"}
        alpaca_interval = interval_map.get(interval, interval)
        df = self.get_ohlcv(symbol, interval=alpaca_interval, lookback_days=days)
        return df if not df.empty else None

    def get_today_candles(self, symbol: str, interval: str = "5m") -> Optional[pd.DataFrame]:
        """Return today's intraday candles. Alias used by main.py for alerts and health checks."""
        return self.get_candles(symbol, interval=interval, days=1)

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
                    "qty":        float(p.qty),  # float: fractional shares have qty like 0.5
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


# ─────────────────────────────────────────────────────────────────────────────
# VIX LEVEL FETCH — cached, yfinance ^VIX
# ─────────────────────────────────────────────────────────────────────────────

_vix_cache: Dict = {"level": 0.0, "ts": 0.0}
_VIX_TTL = 300.0   # refresh every 5 minutes


def get_vix_level() -> float:
    """
    Returns current VIX level fetched from yfinance ^VIX.
    Cached for 5 minutes. Returns 18.0 (optimal zone default) on failure.
    """
    global _vix_cache
    now = _time.monotonic()
    if now - _vix_cache["ts"] < _VIX_TTL and _vix_cache["level"] > 0:
        return _vix_cache["level"]
    try:
        import yfinance as yf
        _kw = {"session": _YF_SESSION} if _YF_SESSION is not None else {}
        tick = yf.Ticker("^VIX", **_kw)
        info = tick.fast_info
        vix = float(getattr(info, "last_price", 0) or getattr(info, "regularMarketPrice", 0) or 0)
        if vix <= 0:
            hist = tick.history(period="1d", interval="5m")
            if not hist.empty:
                vix = float(hist["Close"].iloc[-1])
        if vix > 0:
            _vix_cache = {"level": round(vix, 2), "ts": now}
            logger.debug(f"VIX fetched: {vix:.1f}")
            return _vix_cache["level"]
    except Exception as e:
        logger.debug(f"VIX fetch failed: {e}")
    return _vix_cache.get("level") or 18.0


# ─────────────────────────────────────────────────────────────────────────────
# VIX3M LEVEL FETCH — cached, yfinance ^VIX3M (3-month VIX)
# ─────────────────────────────────────────────────────────────────────────────

_vix3m_cache: Dict = {"level": 0.0, "ts": 0.0}
_VIX3M_TTL = 300.0   # refresh every 5 minutes


def get_vix3m_level() -> float:
    """
    Returns current VIX3M (3-month VIX) level from yfinance ^VIX3M.
    Used for VIX term structure: VIX/VIX3M ratio signals backwardation/contango.
    Cached 5 min. Returns 0.0 on failure so the scoring block safely skips.
    """
    global _vix3m_cache
    now = _time.monotonic()
    if now - _vix3m_cache["ts"] < _VIX3M_TTL and _vix3m_cache["level"] > 0:
        return _vix3m_cache["level"]
    try:
        import yfinance as yf
        _kw = {"session": _YF_SESSION} if _YF_SESSION is not None else {}
        tick = yf.Ticker("^VIX3M", **_kw)
        info = tick.fast_info
        v = float(getattr(info, "last_price", 0) or getattr(info, "regularMarketPrice", 0) or 0)
        if v <= 0:
            hist = tick.history(period="1d", interval="5m")
            if not hist.empty:
                v = float(hist["Close"].iloc[-1])
        if v > 0:
            _vix3m_cache = {"level": round(v, 2), "ts": now}
            logger.debug(f"VIX3M fetched: {v:.1f}")
            return _vix3m_cache["level"]
    except Exception as e:
        logger.debug(f"VIX3M fetch failed: {e}")
    return _vix3m_cache.get("level") or 0.0


# ─────────────────────────────────────────────────────────────────────────────
# PRE-MARKET DATA — volume ratio + consecutive-up bars before 9:30 AM ET
# ─────────────────────────────────────────────────────────────────────────────

# Per-symbol per-day cache: {symbol: {"date": date, "ratio": float, "consec": int}}
_pm_data_cache: Dict = {}


def get_premarket_data(symbol: str, avg_daily_vol: int = 0) -> Dict:
    """
    Fetch pre-market 5-min bars (4:00–9:30 AM ET) and compute:
      - premarket_volume_ratio: PM volume / (20-day avg × 0.08 premarket fraction)
      - premarket_consecutive_up: consecutive up 5-min closes from most-recent bar back

    Cached per-symbol per trading day. Returns zeros after 11 AM or on any error.
    """
    from datetime import date as _date
    today = datetime.now(ET).date()
    now_et = datetime.now(ET)

    # Only meaningful before 11 AM ET
    if now_et.hour >= 11:
        return {"premarket_volume_ratio": 0.0, "premarket_consecutive_up": 0}

    cached = _pm_data_cache.get(symbol)
    if cached and cached.get("date") == today:
        return {"premarket_volume_ratio": cached["ratio"], "premarket_consecutive_up": cached["consec"]}

    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        pm_start = datetime(today.year, today.month, today.day, 4, 0, 0, tzinfo=ET)
        pm_end   = min(
            datetime(today.year, today.month, today.day, 9, 30, 0, tzinfo=ET),
            now_et - timedelta(minutes=1),
        )
        if pm_end <= pm_start:
            return {"premarket_volume_ratio": 0.0, "premarket_consecutive_up": 0}

        fetcher     = get_data_fetcher()
        data_client = fetcher._auth.get_data_client()
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame(5, TimeFrameUnit.Minute),
            start=pm_start,
            end=pm_end,
        )
        bars_resp = data_client.get_stock_bars(req)
        if symbol not in bars_resp or not bars_resp[symbol]:
            return {"premarket_volume_ratio": 0.0, "premarket_consecutive_up": 0}

        closes  = [float(b.close)  for b in bars_resp[symbol]]
        volumes = [int(b.volume)   for b in bars_resp[symbol]]

        pm_total_vol = sum(volumes)
        # Premarket is ~8% of a full trading day's volume on active stocks.
        ref_vol = max(avg_daily_vol * 0.08, 1)
        vol_ratio = round(pm_total_vol / ref_vol, 2) if avg_daily_vol > 0 else 0.0

        consecutive_up = 0
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] > closes[i - 1]:
                consecutive_up += 1
            else:
                break

        _pm_data_cache[symbol] = {"date": today, "ratio": vol_ratio, "consec": consecutive_up}
        logger.debug(
            f"[PM] {symbol}: vol_ratio={vol_ratio:.1f}x consec_up={consecutive_up} "
            f"pm_vol={pm_total_vol:,}"
        )
        return {"premarket_volume_ratio": vol_ratio, "premarket_consecutive_up": consecutive_up}

    except Exception as e:
        logger.debug(f"get_premarket_data({symbol}): {e}")
        return {"premarket_volume_ratio": 0.0, "premarket_consecutive_up": 0}


# ─────────────────────────────────────────────────────────────────────────────
# TOP MOVERS — Alpaca snapshot screener (gainers + high-RVOL)
# ─────────────────────────────────────────────────────────────────────────────

_movers_cache: Dict = {"data": [], "ts": 0.0}
_MOVERS_TTL = 1800.0  # 30-minute cache


def get_top_movers(n: int = 15) -> List[Dict]:
    """
    Return today's top % gainers/losers with high relative volume.
    Uses Alpaca's most-active snapshot, filtered for liquid US stocks.
    Falls back to yfinance if Alpaca screener not available.

    Returns list of dicts: {"symbol": str, "change_pct": float, "volume": int, "price": float}
    """
    global _movers_cache
    now = _time.monotonic()
    if now - _movers_cache["ts"] < _MOVERS_TTL and _movers_cache["data"]:
        return _movers_cache["data"][:n]

    results = []
    try:
        fetcher = get_data_fetcher()
        try:
            client = fetcher._auth.get_data_client()
        except Exception:
            client = None
        # Try Alpaca screener (most_actives endpoint)
        if client and hasattr(client, "get_stock_most_actives"):
            from alpaca.data.requests import MostActivesRequest
            req  = MostActivesRequest(top=50, by="volume")
            resp = client.get_stock_most_actives(req)
            for item in getattr(resp, "most_actives", []):
                sym = getattr(item, "symbol", "")
                if not sym or len(sym) > 5:
                    continue
                results.append({
                    "symbol":     sym,
                    "change_pct": float(getattr(item, "percent_change", 0) or 0),
                    "volume":     int(getattr(item, "volume", 0) or 0),
                    "price":      float(getattr(item, "price", 0) or 0),
                })
    except Exception as _e:
        logger.debug(f"Alpaca most_actives failed: {_e}")

    # yfinance fallback — screen S&P 500 for biggest movers
    if not results:
        try:
            import yfinance as yf
            _SCREEN = [
                "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AMD","NFLX","COIN",
                "CRWD","PANW","ZS","DDOG","NET","SNOW","AVGO","MU","ARM","SMCI",
                "PLTR","MSTR","MARA","RIOT","SOFI","HOOD","UBER","SHOP","ABNB",
                "MELI","RBLX","MRNA","HIMS","JPM","GS","MS","XOM","CVX","OXY",
                "TQQQ","SPXL","SOXL","IWM","SPY","QQQ",
            ]
            _kw = {"session": _YF_SESSION} if _YF_SESSION is not None else {}
            tickers = yf.Tickers(" ".join(_SCREEN), **_kw)
            for sym in _SCREEN:
                try:
                    t = tickers.tickers.get(sym)
                    if not t:
                        continue
                    info = t.fast_info
                    price = float(getattr(info, "last_price", 0) or 0)
                    prev  = float(getattr(info, "previous_close", 0) or 0)
                    vol   = int(getattr(info, "three_month_average_volume", 0) or 0)
                    if price > 0 and prev > 0:
                        chg = (price - prev) / prev * 100
                        results.append({"symbol": sym, "change_pct": chg, "volume": vol, "price": price})
                except Exception:
                    continue
        except Exception as _e:
            logger.debug(f"yfinance movers fallback failed: {_e}")

    # Sort by absolute % change (biggest movers = most opportunity)
    results.sort(key=lambda x: abs(x.get("change_pct", 0)), reverse=True)
    # Filter: price > $5, at least some volume
    results = [r for r in results if r.get("price", 0) > 5 and r.get("change_pct", 0) != 0]

    if results:
        _movers_cache = {"data": results, "ts": now}
        logger.info(f"Top movers fetched: {[r['symbol'] for r in results[:8]]}")

    return results[:n]
