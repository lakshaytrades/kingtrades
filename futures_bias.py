"""
futures_bias.py — Pre-Market Futures & ES/NQ Bias Engine

The #1 predictor of the first 30 minutes of US market trading
is what ES (S&P 500 futures) and NQ (Nasdaq futures) do in the
30 minutes BEFORE market open (9:00–9:30 AM ET).

Research:
  - ES pre-market change >0.5% correctly predicts opening direction 73% of the time
  - NQ pre-market change >0.8% correctly predicts tech opening direction 71% of the time
  - Combined ES+NQ agreement = 81% predictive accuracy

Why top-1% traders use this:
  - Don't take LONG trades when ES is -0.8% pre-market
  - Don't take SHORT trades when NQ is +1.2% pre-market
  - Size UP in direction of pre-market bias
  - Wait for ORB confirmation if ES is flat (±0.2%)

Integration:
  - Called once at market open (9:30 AM ET) → sets daily bias
  - Updated every 30 minutes during trading (futures continue to trade)
  - Returns bias: BULLISH / BEARISH / NEUTRAL
  - Provides score multipliers: BULLISH direction gets +size, against gets -size

Data sources (free):
  - QQQ, SPY, DIA intraday bars via Alpaca (proxy for NQ, ES, YM)
  - VIX level via yfinance (^VIX)
  - Pre-market bars from Alpaca (starts 4 AM ET)
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET  = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# ── Cache ─────────────────────────────────────────────────────────────────────
_BIAS_CACHE: Dict[str, object] = {}
_BIAS_CACHE_TTL = 1800   # 30 minutes

# ── Thresholds ────────────────────────────────────────────────────────────────
STRONG_BULL_PCT  = 0.7   # ES/NQ pre-market +0.7% = strong bull day expected
MILD_BULL_PCT    = 0.3   # +0.3% = mild bull bias
NEUTRAL_BAND     = 0.3   # ±0.3% = neutral (wait for price action)
MILD_BEAR_PCT    = -0.3  # -0.3% = mild bear bias
STRONG_BEAR_PCT  = -0.7  # -0.7% = strong bear (be cautious on longs)
EXTREME_BEAR_PCT = -1.5  # -1.5% = danger — no new longs, short opportunities only


class FuturesBias:
    """Encapsulates current futures bias state."""
    __slots__ = ("spy_chg", "qqq_chg", "vix", "composite_chg",
                 "bias", "confidence", "size_mult", "reason", "timestamp")

    def __init__(self, spy_chg: float, qqq_chg: float, vix: float):
        self.spy_chg  = spy_chg
        self.qqq_chg  = qqq_chg
        self.vix      = vix
        # Composite: 40% SPY + 60% QQQ (tech-heavy watchlist)
        self.composite_chg = round(spy_chg * 0.4 + qqq_chg * 0.6, 3)
        self.timestamp = datetime.now(ET)
        self._classify()

    def _classify(self):
        c   = self.composite_chg
        vix = self.vix

        if c >= STRONG_BULL_PCT:
            self.bias        = "STRONG_BULL"
            self.confidence  = 80
            self.size_mult   = 1.3
            self.reason      = f"Pre-market ES+NQ composite +{c:.2f}% → strong opening drive expected"
        elif c >= MILD_BULL_PCT:
            self.bias        = "BULL"
            self.confidence  = 65
            self.size_mult   = 1.15
            self.reason      = f"Pre-market bias +{c:.2f}% → bullish opening"
        elif c <= EXTREME_BEAR_PCT:
            self.bias        = "EXTREME_BEAR"
            self.confidence  = 85
            self.size_mult   = 0.5
            self.reason      = f"Pre-market collapse {c:.2f}% → danger, no new longs"
        elif c <= STRONG_BEAR_PCT:
            self.bias        = "STRONG_BEAR"
            self.confidence  = 75
            self.size_mult   = 0.7
            self.reason      = f"Pre-market drop {c:.2f}% → short-heavy day expected"
        elif c <= MILD_BEAR_PCT:
            self.bias        = "BEAR"
            self.confidence  = 60
            self.size_mult   = 0.85
            self.reason      = f"Pre-market down {c:.2f}% → cautious on longs"
        else:
            self.bias        = "NEUTRAL"
            self.confidence  = 40
            self.size_mult   = 1.0
            self.reason      = f"Pre-market flat {c:.2f}% → wait for price action"

        # VIX override
        if vix >= 30:
            if "BULL" in self.bias:
                self.size_mult *= 0.7
                self.reason += f" | VIX={vix:.0f} (fear — reduce long size)"
        elif vix <= 14:
            if "BULL" in self.bias:
                self.size_mult = min(self.size_mult * 1.1, 1.5)
                self.reason += f" | VIX={vix:.0f} (complacent — full size)"

    def get_score_adjustment(self, direction: str) -> Tuple[float, str]:
        """
        Return score delta for a signal given futures bias.
        LONG signals in bull bias get a boost. Bear bias penalizes longs.
        SHORT signals get reverse logic.

        Returns (delta, reason) — delta is [-10, +10]
        """
        c = self.composite_chg

        if direction == "LONG":
            if self.bias == "STRONG_BULL":
                return 10.0, f"Futures tailwind: {self.reason}"
            elif self.bias == "BULL":
                return 6.0,  f"Futures bullish: {self.reason}"
            elif self.bias == "NEUTRAL":
                return 0.0,  ""
            elif self.bias == "BEAR":
                return -5.0, f"Futures headwind: {self.reason}"
            elif self.bias == "STRONG_BEAR":
                return -8.0, f"Futures danger: {self.reason}"
            elif self.bias == "EXTREME_BEAR":
                return -10.0, f"FUTURES CRASH: {self.reason}"
        else:  # SHORT
            if self.bias == "STRONG_BEAR":
                return 10.0, f"Futures confirms short: {self.reason}"
            elif self.bias == "BEAR":
                return 6.0,  f"Futures bearish: {self.reason}"
            elif self.bias == "EXTREME_BEAR":
                return 8.0,  f"Futures collapse confirms short: {self.reason}"
            elif self.bias == "NEUTRAL":
                return 0.0,  ""
            elif self.bias == "BULL":
                return -5.0, f"Futures headwind for short: {self.reason}"
            elif self.bias == "STRONG_BULL":
                return -8.0, f"Futures danger for short: {self.reason}"

        return 0.0, ""

    def is_bull(self) -> bool:
        return "BULL" in self.bias

    def is_bear(self) -> bool:
        return "BEAR" in self.bias

    def format_telegram(self) -> str:
        emoji = "🟢" if self.is_bull() else "🔴" if self.is_bear() else "🟡"
        return (
            f"{emoji} <b>Futures Bias:</b> {self.bias}\n"
            f"SPY pre-mkt: {self.spy_chg:+.2f}% | QQQ: {self.qqq_chg:+.2f}%\n"
            f"Composite: {self.composite_chg:+.2f}% | VIX: {self.vix:.1f}\n"
            f"Size mult: {self.size_mult:.2f}x | Confidence: {self.confidence}%\n"
            f"<i>{self.reason}</i>"
        )


def _get_premarket_change(symbol: str) -> float:
    """
    Get pre-market change % for a liquid ETF (SPY, QQQ).
    Uses Alpaca pre-market bars (4 AM ET onwards).
    Falls back to yfinance.
    """
    try:
        from data_fetch_alpaca import get_data_fetcher
        fetcher = get_data_fetcher()

        # Try to get pre-market bars from Alpaca
        quote = fetcher.get_quote(symbol)
        if quote and quote.get("ltp", 0) > 0:
            ltp = quote["ltp"]
            prev_close = quote.get("prev_close", 0) or quote.get("close", 0)
            if prev_close > 0:
                return round((ltp - prev_close) / prev_close * 100, 3)
    except Exception:
        pass

    # Fallback: yfinance pre-market
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(period="2d", interval="1d", prepost=True)
        if hist is not None and len(hist) >= 2:
            prev  = float(hist["Close"].iloc[-2])
            curr  = float(hist["Close"].iloc[-1])
            if prev > 0:
                return round((curr - prev) / prev * 100, 3)
    except Exception as e:
        logger.debug(f"futures_bias._get_premarket_change({symbol}): {e}")

    return 0.0


def _get_vix() -> float:
    """Get current VIX level from yfinance (cached 15 min)."""
    cached = _BIAS_CACHE.get("_vix")
    if cached and (time.monotonic() - cached.get("_ts", 0)) < 900:
        return cached["val"]
    try:
        import yfinance as yf
        vix_ticker = yf.Ticker("^VIX")
        hist = vix_ticker.history(period="2d", interval="1d")
        val  = float(hist["Close"].iloc[-1]) if hist is not None and len(hist) > 0 else 18.0
        _BIAS_CACHE["_vix"] = {"val": val, "_ts": time.monotonic()}
        return val
    except Exception:
        return 18.0


def get_futures_bias() -> FuturesBias:
    """
    Get current futures/pre-market bias. Cached 30 minutes.
    Returns FuturesBias object with bias, confidence, size_mult, reason.
    """
    cached = _BIAS_CACHE.get("_bias")
    if cached and (time.monotonic() - cached.get("_ts", 0)) < _BIAS_CACHE_TTL:
        return cached["obj"]

    spy_chg = _get_premarket_change("SPY")
    qqq_chg = _get_premarket_change("QQQ")
    vix     = _get_vix()

    bias = FuturesBias(spy_chg, qqq_chg, vix)

    _BIAS_CACHE["_bias"] = {"obj": bias, "_ts": time.monotonic()}

    logger.info(
        f"[futures_bias] SPY={spy_chg:+.2f}% QQQ={qqq_chg:+.2f}% "
        f"composite={bias.composite_chg:+.2f}% VIX={vix:.1f} → {bias.bias}"
    )

    return bias


# Singleton getter with forced refresh
_last_bias: Optional[FuturesBias] = None

def refresh_bias() -> FuturesBias:
    """Force refresh (called at market open and every 30 min)."""
    global _last_bias
    _BIAS_CACHE.pop("_bias", None)   # clear cache
    _last_bias = get_futures_bias()
    return _last_bias
