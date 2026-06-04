"""
tape_classifier.py — Real-Time Tape Classification via Lee-Ready Rule (v28.0)

Lee & Ready (1991): classify each trade print as buyer or seller initiated.
Quote rule: price > midpoint → BUY; price < midpoint → SELL
Tick rule:  price > prev_price → BUY; price < prev_price → SELL

True Order Flow Imbalance (OFI):
  OFI = (buy_volume - sell_volume) / total_volume
  OFI > 0.60: institutional accumulation → LONG signal +8
  OFI < 0.40: institutional distribution → penalize LONG

Free proxy for: $500-5000/month real-time Level-2 data feed.

Integration with Alpaca: price_stream.py already buffers trade prints.
TapeClassifier reads from PriceStream if available, else uses bar data proxy.
"""

import logging
import time as _time
from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 120   # 2-minute rolling OFI window
_MIN_PRINTS = 5         # need at least 5 prints to score


class TapeClassifier:
    def __init__(self):
        # {symbol: deque of (timestamp, buy_vol, sell_vol)}
        self._prints: Dict[str, Deque] = defaultdict(lambda: deque(maxlen=500))
        self._last_price: Dict[str, float] = {}

    def record_trade(self, symbol: str, price: float, size: int,
                     timestamp: float, bid: float = 0.0, ask: float = 0.0):
        """
        Record a trade print. Called from Alpaca WebSocket stream.
        Classifies as buy/sell using Lee-Ready rule.
        """
        try:
            buy_vol  = 0
            sell_vol = 0

            classification = self._classify(symbol, price, size, bid, ask)
            if classification == "BUY":
                buy_vol = size
            else:
                sell_vol = size

            self._prints[symbol].append((timestamp, buy_vol, sell_vol))
            self._last_price[symbol] = price
        except Exception as e:
            logger.debug(f"[tape] record_trade {symbol}: {e}")

    def _classify(self, symbol: str, price: float, size: int,
                  bid: float, ask: float) -> str:
        """Lee-Ready classification: quote rule first, tick rule as tiebreaker."""
        # Quote rule: if bid/ask available
        if bid > 0 and ask > 0:
            midpoint = (bid + ask) / 2.0
            if price > midpoint:
                return "BUY"
            elif price < midpoint:
                return "SELL"

        # Tick rule: compare to last trade price
        last = self._last_price.get(symbol)
        if last is not None and last > 0:
            if price > last:
                return "BUY"
            elif price < last:
                return "SELL"

        # Zero-tick: same price as last → use previous classification
        # Default to BUY (slight long bias in US markets historically)
        return "BUY"

    def get_ofi(self, symbol: str, window_seconds: int = _WINDOW_SECONDS) -> Optional[float]:
        """
        Compute Order Flow Imbalance over the last window_seconds.
        Returns float in [0, 1]: 0=all sell, 0.5=balanced, 1=all buy.
        Returns None if insufficient data.
        """
        try:
            now = _time.monotonic()
            cutoff = now - window_seconds
            prints = self._prints.get(symbol)
            if not prints:
                return None

            total_buy  = 0
            total_sell = 0
            count = 0

            for ts, bv, sv in prints:
                if ts >= cutoff:
                    total_buy  += bv
                    total_sell += sv
                    count += 1

            if count < _MIN_PRINTS or (total_buy + total_sell) == 0:
                return None

            return total_buy / (total_buy + total_sell)

        except Exception as e:
            logger.debug(f"[tape] get_ofi {symbol}: {e}")
            return None

    def get_ofi_score(self, symbol: str, direction: str) -> Tuple[float, str]:
        """
        Score OFI for trade signal generation.
        Strong buy flow + LONG = positive; sell flow + LONG = negative.
        """
        ofi = self.get_ofi(symbol)
        if ofi is None:
            # Fallback: try to infer from BarCache
            ofi = self._infer_ofi_from_bars(symbol)
            if ofi is None:
                return 0.0, "ofi:no-data"
            suffix = "(bar-proxy)"
        else:
            suffix = "(live-tape)"

        if direction == "LONG":
            if ofi > 0.70:
                return 10.0, f"ofi:strong-buy({ofi:.2f}){suffix}"
            elif ofi > 0.60:
                return 7.0, f"ofi:buy-flow({ofi:.2f}){suffix}"
            elif ofi > 0.55:
                return 3.0, f"ofi:mild-buy({ofi:.2f}){suffix}"
            elif ofi < 0.35:
                return -8.0, f"ofi:strong-sell({ofi:.2f}){suffix}"
            elif ofi < 0.45:
                return -4.0, f"ofi:sell-flow({ofi:.2f}){suffix}"
        else:  # SHORT
            if ofi < 0.30:
                return 10.0, f"ofi:strong-sell({ofi:.2f}){suffix}"
            elif ofi < 0.40:
                return 7.0, f"ofi:sell-flow({ofi:.2f}){suffix}"
            elif ofi < 0.45:
                return 3.0, f"ofi:mild-sell({ofi:.2f}){suffix}"
            elif ofi > 0.65:
                return -8.0, f"ofi:strong-buy-vs-short({ofi:.2f}){suffix}"
            elif ofi > 0.55:
                return -4.0, f"ofi:buy-flow-vs-short({ofi:.2f}){suffix}"

        return 0.0, f"ofi:neutral({ofi:.2f}){suffix}"

    def _infer_ofi_from_bars(self, symbol: str) -> Optional[float]:
        """
        Proxy OFI from 5-min bar data when live tape is unavailable.
        Closing price position within bar range (Buy pressure proxy):
          Close near High → buyer-initiated, OFI ~ 0.65
          Close near Low  → seller-initiated, OFI ~ 0.35
        """
        try:
            from data_fetch_alpaca import get_data_fetcher
            fetcher = get_data_fetcher()
            mtf = fetcher.get_multi_timeframe_data(symbol)
            df = mtf.get("5m")
            if df is None or len(df) < 3:
                return None

            close_col = "close" if "close" in df.columns else "Close"
            high_col  = "high"  if "high"  in df.columns else "High"
            low_col   = "low"   if "low"   in df.columns else "Low"

            closes = df[close_col].values[-5:].astype(float)
            highs  = df[high_col].values[-5:].astype(float)
            lows   = df[low_col].values[-5:].astype(float)

            scores = []
            for c, h, lo in zip(closes, highs, lows):
                rng = h - lo
                if rng > 0:
                    scores.append((c - lo) / rng)

            if not scores:
                return None

            return sum(scores) / len(scores)

        except Exception:
            return None


# ── Singleton ────────────────────────────────────────────────────────────────

_classifier: Optional[TapeClassifier] = None


def get_tape_classifier() -> TapeClassifier:
    global _classifier
    if _classifier is None:
        _classifier = TapeClassifier()
        logger.debug("[tape] TapeClassifier singleton created")
    return _classifier


def record_alpaca_trade(symbol: str, price: float, size: int,
                        timestamp: float = 0.0, bid: float = 0.0, ask: float = 0.0):
    """Convenience wrapper called from price_stream.py on each trade print."""
    if timestamp == 0.0:
        timestamp = _time.monotonic()
    get_tape_classifier().record_trade(symbol, price, size, timestamp, bid, ask)
