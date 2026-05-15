"""
price_stream.py — Alpaca WebSocket Live Price Cache

Replaces slow yfinance / REST polling during market hours with a persistent
WebSocket connection that pushes real-time quote updates into an in-memory
cache.  All consumers call get_quote() / get_quotes() and get sub-second
data instead of waiting 2–10 s for a REST round-trip.

Auth:
    ALPACA_API_KEY    — from .env
    ALPACA_SECRET_KEY — from .env

Usage:
    from price_stream import get_price_stream

    stream = get_price_stream()
    stream.start(["AAPL", "TSLA", "NVDA"])

    quote = stream.get_quote("AAPL")
    # {"ltp": 182.34, "bid": 182.33, "ask": 182.35, "volume": 4821300,
    #  "timestamp": "2026-05-15 09:31:04 ET", "change_pct": 1.23,
    #  "prev_close": 180.00}

    stream.stop()
"""

import logging
import os
import threading
import time as _time
from typing import Dict, List, Optional

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# In-memory quote store (module-level so it survives across stop/start cycles)
# ─────────────────────────────────────────────────────────────────────────────
_price_cache: Dict[str, Dict] = {}
# Separate store for prev_close — set on first daily bar per symbol
_prev_close_cache: Dict[str, float] = {}

# Guard for concurrent writes from the WS callback thread
_cache_lock = threading.Lock()

# Monotonic timestamp of the last write per symbol — used by get_quote_age()
_quote_ts: Dict[str, float] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _compute_change_pct(ltp: float, prev_close: float) -> float:
    """Return % change vs prev_close, clamped to avoid divide-by-zero."""
    if prev_close and prev_close > 0:
        return round((ltp - prev_close) / prev_close * 100, 2)
    return 0.0


def _ts_str(dt) -> str:
    """Convert a datetime-like object to a formatted ET/IST timestamp string."""
    try:
        if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
            return format_ist_timestamp(dt)
        return str(dt)
    except Exception:
        return str(dt)


# ─────────────────────────────────────────────────────────────────────────────
# PriceStream
# ─────────────────────────────────────────────────────────────────────────────

class PriceStream:
    """
    Maintains a live price cache backed by an Alpaca WebSocket stream.

    The WebSocket runs in a dedicated daemon thread.  Quote callbacks write to
    `_price_cache` under a lock; readers never block the callback thread.

    Auto-reconnect: if the stream thread exits unexpectedly (network drop,
    auth error, etc.) a watchdog thread will relaunch it after 5 s.
    """

    _RECONNECT_DELAY = 5  # seconds before reconnect attempt

    def __init__(self):
        self._api_key    = os.getenv("ALPACA_API_KEY", "")
        self._secret_key = os.getenv("ALPACA_SECRET_KEY", "")

        self._symbols: List[str] = []
        self._stream_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._stream_instance = None  # alpaca StockDataStream object

    # ─────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────

    def start(self, symbols: List[str]) -> None:
        """
        Subscribe to real-time quotes for *symbols* and start the WebSocket
        in a background thread.  Safe to call multiple times — adds new
        symbols to an already-running stream.
        """
        if not symbols:
            logger.warning(f"[{format_ist_timestamp()}] PriceStream.start() called with empty symbol list")
            return

        if not self._api_key or not self._secret_key:
            logger.error(
                f"[{format_ist_timestamp()}] PriceStream: ALPACA_API_KEY / ALPACA_SECRET_KEY "
                "not set — live stream disabled"
            )
            return

        # Deduplicate while preserving order
        new_symbols = [s.upper() for s in symbols if s.upper() not in self._symbols]
        self._symbols.extend(new_symbols)

        if self._running:
            # Stream already active — subscribe additional symbols
            if new_symbols and self._stream_instance is not None:
                try:
                    self._stream_instance.subscribe_quotes(
                        self._on_quote, *new_symbols
                    )
                    logger.info(
                        f"[{format_ist_timestamp()}] PriceStream: subscribed additional "
                        f"symbols {new_symbols}"
                    )
                except Exception as exc:
                    logger.warning(
                        f"[{format_ist_timestamp()}] PriceStream: failed to subscribe "
                        f"new symbols live: {exc}"
                    )
            return

        self._stop_event.clear()
        self._running = True

        # Main stream thread
        self._stream_thread = threading.Thread(
            target=self._stream_loop,
            name="price-stream",
            daemon=True,
        )
        self._stream_thread.start()

        # Watchdog thread — restarts stream if it dies unexpectedly
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="price-stream-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

        logger.info(
            f"[{format_ist_timestamp()}] PriceStream started for "
            f"{len(self._symbols)} symbols: {self._symbols}"
        )

    def stop(self) -> None:
        """Clean shutdown — unsubscribes and stops both threads."""
        if not self._running:
            return

        logger.info(f"[{format_ist_timestamp()}] PriceStream stopping…")
        self._running = False
        self._stop_event.set()

        # Ask the Alpaca stream to close
        if self._stream_instance is not None:
            try:
                self._stream_instance.stop()
            except Exception as exc:
                logger.debug(f"[{format_ist_timestamp()}] stream.stop() raised: {exc}")
            self._stream_instance = None

        for thread in (self._stream_thread, self._watchdog_thread):
            if thread and thread.is_alive():
                thread.join(timeout=10)

        logger.info(f"[{format_ist_timestamp()}] PriceStream stopped")

    def get_quote(self, symbol: str) -> Optional[Dict]:
        """
        Return the latest cached quote for *symbol*, or None if not yet
        received.

        Keys: ltp, bid, ask, volume, timestamp, change_pct, prev_close
        """
        sym = symbol.upper()
        with _cache_lock:
            return dict(_price_cache[sym]) if sym in _price_cache else None

    def get_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """
        Bulk get — returns {symbol: quote_dict} for every symbol that has
        data.  Missing symbols are simply absent from the result dict.
        """
        result: Dict[str, Dict] = {}
        with _cache_lock:
            for sym in symbols:
                key = sym.upper()
                if key in _price_cache:
                    result[key] = dict(_price_cache[key])
        return result

    def is_running(self) -> bool:
        """True if the stream thread is alive and actively connected."""
        return (
            self._running
            and self._stream_thread is not None
            and self._stream_thread.is_alive()
        )

    @property
    def subscribed_symbols(self) -> List[str]:
        """Read-only list of currently subscribed symbols."""
        return list(self._symbols)

    def get_quote_age(self, symbol: str) -> Optional[float]:
        """Return seconds since last update for symbol, or None if never updated."""
        with _cache_lock:
            ts = _quote_ts.get(symbol.upper(), 0)
        return _time.monotonic() - ts if ts > 0 else None

    # ─────────────────────────────────────────────────────────────────────
    # WebSocket callbacks
    # ─────────────────────────────────────────────────────────────────────

    async def _on_quote(self, quote) -> None:
        """
        Called by the Alpaca SDK for every incoming quote update.
        Writes to _price_cache under _cache_lock.
        """
        try:
            sym   = quote.symbol.upper()
            bid   = float(quote.bid_price or 0)
            ask   = float(quote.ask_price or 0)
            ltp   = ask if ask > 0 else (bid if bid > 0 else 0.0)
            # Use bid/ask mid-point as LTP when both are available
            if bid > 0 and ask > 0:
                ltp = round((bid + ask) / 2, 4)

            prev_close = _prev_close_cache.get(sym, 0.0)
            change_pct = _compute_change_pct(ltp, prev_close)

            ts_str = _ts_str(getattr(quote, "timestamp", get_current_ist_time()))

            entry = {
                "ltp":        ltp,
                "bid":        bid,
                "ask":        ask,
                "volume":     int(getattr(quote, "bid_size", 0) or 0),
                "timestamp":  ts_str,
                "change_pct": change_pct,
                "prev_close": prev_close,
            }

            with _cache_lock:
                _price_cache[sym] = entry
                _quote_ts[sym] = _time.monotonic()

            logger.debug(
                f"[{format_ist_timestamp()}] quote {sym}: ltp={ltp:.4f} "
                f"bid={bid:.4f} ask={ask:.4f} chg={change_pct:+.2f}%"
            )

        except Exception as exc:
            logger.warning(
                f"[{format_ist_timestamp()}] _on_quote error ({getattr(quote, 'symbol', '?')}): {exc}"
            )

    async def _on_bar(self, bar) -> None:
        """
        Called on each intraday bar.  We use the first bar of the day to
        capture prev_close (the bar's open equals previous session's close
        only on the very first 1-min bar at 09:30).  We also update volume.
        """
        try:
            sym = bar.symbol.upper()
            close = float(bar.close or 0)
            open_ = float(bar.open or 0)
            vol   = int(bar.volume or 0)

            # Store first-bar open as prev_close proxy if not yet set
            if sym not in _prev_close_cache and open_ > 0:
                _prev_close_cache[sym] = open_
                logger.debug(
                    f"[{format_ist_timestamp()}] prev_close set for {sym}: {open_:.4f} "
                    "(from first intraday bar open)"
                )

            prev_close = _prev_close_cache.get(sym, 0.0)
            change_pct = _compute_change_pct(close, prev_close)

            with _cache_lock:
                if sym in _price_cache:
                    _price_cache[sym]["ltp"]        = close
                    _price_cache[sym]["volume"]      = vol
                    _price_cache[sym]["change_pct"]  = change_pct
                    _price_cache[sym]["prev_close"]  = prev_close
                    _price_cache[sym]["timestamp"]   = _ts_str(
                        getattr(bar, "timestamp", get_current_ist_time())
                    )
                else:
                    # Populate cache from bar if quote hasn't arrived yet
                    _price_cache[sym] = {
                        "ltp":        close,
                        "bid":        0.0,
                        "ask":        0.0,
                        "volume":     vol,
                        "timestamp":  _ts_str(getattr(bar, "timestamp", get_current_ist_time())),
                        "change_pct": change_pct,
                        "prev_close": prev_close,
                    }

        except Exception as exc:
            logger.warning(
                f"[{format_ist_timestamp()}] _on_bar error ({getattr(bar, 'symbol', '?')}): {exc}"
            )

    # ─────────────────────────────────────────────────────────────────────
    # Stream thread
    # ─────────────────────────────────────────────────────────────────────

    def _stream_loop(self) -> None:
        """
        Runs inside the dedicated stream thread.  Creates a StockDataStream,
        subscribes to quotes and minute bars for all symbols, then calls
        stream.run() which blocks until the connection drops or stop() is
        called.
        """
        while not self._stop_event.is_set():
            try:
                from alpaca.data.live import StockDataStream

                self._stream_instance = StockDataStream(
                    api_key    = self._api_key,
                    secret_key = self._secret_key,
                )

                symbols = list(self._symbols)
                if not symbols:
                    logger.warning(
                        f"[{format_ist_timestamp()}] PriceStream: no symbols to subscribe — waiting"
                    )
                    self._stop_event.wait(timeout=5)
                    continue

                self._stream_instance.subscribe_quotes(self._on_quote, *symbols)
                self._stream_instance.subscribe_bars(self._on_bar, *symbols)

                logger.info(
                    f"[{format_ist_timestamp()}] PriceStream WS connected. "
                    f"Subscribed quotes+bars for {len(symbols)} symbols."
                )

                # Blocking call — returns on clean stop or exception
                self._stream_instance.run()

            except Exception as exc:
                if self._stop_event.is_set():
                    # Normal shutdown triggered stop_event before run() returned
                    break
                logger.warning(
                    f"[{format_ist_timestamp()}] PriceStream WS disconnected: {exc}. "
                    f"Reconnecting in {self._RECONNECT_DELAY}s…"
                )
                self._stream_instance = None
                self._stop_event.wait(timeout=self._RECONNECT_DELAY)

        logger.info(f"[{format_ist_timestamp()}] PriceStream stream thread exiting")

    # ─────────────────────────────────────────────────────────────────────
    # Watchdog thread
    # ─────────────────────────────────────────────────────────────────────

    def _watchdog_loop(self) -> None:
        """
        Monitors the stream thread.  If it dies while self._running is still
        True, the watchdog restarts it after a short delay.
        """
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=15)
            if self._stop_event.is_set():
                break
            if (
                self._running
                and self._stream_thread is not None
                and not self._stream_thread.is_alive()
            ):
                logger.warning(
                    f"[{format_ist_timestamp()}] PriceStream watchdog: stream thread dead. "
                    f"Restarting in {self._RECONNECT_DELAY}s…"
                )
                _time.sleep(self._RECONNECT_DELAY)
                if not self._stop_event.is_set():
                    self._stream_thread = threading.Thread(
                        target=self._stream_loop,
                        name="price-stream",
                        daemon=True,
                    )
                    self._stream_thread.start()
                    logger.info(
                        f"[{format_ist_timestamp()}] PriceStream watchdog: stream thread restarted"
                    )

        logger.debug(f"[{format_ist_timestamp()}] PriceStream watchdog thread exiting")


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_stream_singleton: Optional[PriceStream] = None
_singleton_lock = threading.Lock()


def get_price_stream() -> PriceStream:
    """
    Return the module-level singleton PriceStream.
    Thread-safe — safe to call from multiple modules at import time.
    """
    global _stream_singleton
    if _stream_singleton is None:
        with _singleton_lock:
            if _stream_singleton is None:
                _stream_singleton = PriceStream()
                logger.info(f"[{format_ist_timestamp()}] PriceStream singleton created")
    return _stream_singleton
