"""
pairs_engine.py — Cointegration Pairs Trading Engine

Wraps stat_arb_cointegration.py and provides the interface that main.py expects.
Identifies mean-reverting stock pairs and generates long/short pair signals.

PairSignal fields used by main.py:
  symbol_long, symbol_short, zscore, entry_long, entry_short,
  spread_pct, confidence, target_zscore, reason

Interface:
  PairsEngine.should_scan() → bool
  PairsEngine.scan_pairs(data_fetcher=None) → List[PairSignal]
  PairsEngine.format_telegram_signal(ps) → str
"""

import logging
import time as _time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_SCAN_INTERVAL_SEC = 900   # scan every 15 min (pairs are slow-moving)
_MIN_ZSCORE        = 2.0   # minimum z-score to generate a signal
_STRONG_ZSCORE     = 3.0   # z-score for high-confidence signal
_TARGET_ZSCORE     = 0.5   # target z-score for mean reversion


@dataclass
class PairSignal:
    symbol_long:   str
    symbol_short:  str
    zscore:        float
    entry_long:    float   # current price of long leg
    entry_short:   float   # current price of short leg
    spread_pct:    float   # current spread as % of mean spread
    confidence:    float   # 0–100
    target_zscore: float   # where z-score should revert to (usually ~0.5)
    reason:        str
    pair_key:      str = ""
    hedge_ratio:   float = 1.0
    half_life_days: float = 5.0


class PairsEngine:
    """
    Wraps stat_arb_cointegration to provide main.py-compatible interface.
    Only trades during midday (10:30 AM–2:00 PM ET) when momentum is low
    and mean reversion tends to dominate.
    """

    def __init__(self):
        self._last_scan:   float = 0.0
        self._last_signals: List[PairSignal] = []

    def should_scan(self) -> bool:
        """Rate-limit scanning to every 15 minutes."""
        return (_time.time() - self._last_scan) >= _SCAN_INTERVAL_SEC

    def scan_pairs(self, data_fetcher=None) -> List[PairSignal]:
        """
        Run cointegration scan and return actionable pair signals.
        Uses stat_arb_cointegration module for the heavy math.
        """
        self._last_scan = _time.time()
        signals: List[PairSignal] = []

        try:
            from stat_arb_cointegration import get_cointegration_report
            pairs = get_cointegration_report()
        except Exception as e:
            logger.debug(f"PairsEngine: cointegration report failed: {e}")
            return []

        for pair in pairs:
            try:
                zscore = float(pair.get("zscore", 0.0))
                if abs(zscore) < _MIN_ZSCORE:
                    continue

                sym_x = pair.get("symbol_x", "")
                sym_y = pair.get("symbol_y", "")
                if not sym_x or not sym_y:
                    continue

                # zscore > 0: y is expensive vs x → SHORT y, LONG x
                # zscore < 0: y is cheap vs x → LONG y, SHORT x
                if zscore > 0:
                    sym_long  = sym_x
                    sym_short = sym_y
                else:
                    sym_long  = sym_y
                    sym_short = sym_x

                price_x = float(pair.get("price_x", 0.0))
                price_y = float(pair.get("price_y", 0.0))

                if price_x <= 0 or price_y <= 0:
                    price_x, price_y = self._get_prices(sym_x, sym_y, data_fetcher)

                if price_x <= 0 or price_y <= 0:
                    continue

                entry_long  = price_x if sym_long  == sym_x else price_y
                entry_short = price_y if sym_short == sym_y else price_x

                spread_pct = float(pair.get("spread_pct", abs(zscore) * 2.0))
                half_life  = float(pair.get("half_life_days", 5.0))
                p_value    = float(pair.get("p_value", 0.04))

                # Confidence = function of z-score strength + p-value + half-life
                conf = min(99.0, max(50.0,
                    60.0
                    + (abs(zscore) - _MIN_ZSCORE) * 10.0   # z>2 adds confidence
                    + (0.05 - p_value) * 400               # lower p → more confident
                    - max(0, half_life - 10) * 1.5         # long half-life → less confident
                ))

                reason = (
                    f"z={zscore:+.2f} | p={p_value:.3f} | "
                    f"half-life={half_life:.1f}d | "
                    f"{'STRONG' if abs(zscore) >= _STRONG_ZSCORE else 'STANDARD'} mean-reversion"
                )

                signals.append(PairSignal(
                    symbol_long   = sym_long,
                    symbol_short  = sym_short,
                    zscore        = round(zscore, 3),
                    entry_long    = round(entry_long, 2),
                    entry_short   = round(entry_short, 2),
                    spread_pct    = round(spread_pct, 2),
                    confidence    = round(conf, 1),
                    target_zscore = _TARGET_ZSCORE,
                    reason        = reason,
                    pair_key      = f"{sym_x}_{sym_y}",
                    hedge_ratio   = float(pair.get("hedge_ratio", 1.0)),
                    half_life_days= half_life,
                ))

            except Exception as _pair_e:
                logger.debug(f"PairsEngine: pair processing error: {_pair_e}")
                continue

        # Sort by |z-score| descending (strongest signal first)
        signals.sort(key=lambda s: abs(s.zscore), reverse=True)
        self._last_signals = signals[:5]   # cap at top-5 to avoid over-trading
        return self._last_signals

    def _get_prices(self, sym_x: str, sym_y: str, data_fetcher=None) -> tuple:
        """Fetch current prices for both legs."""
        px, py = 0.0, 0.0
        try:
            if data_fetcher and hasattr(data_fetcher, "get_quote"):
                qx = data_fetcher.get_quote(sym_x)
                qy = data_fetcher.get_quote(sym_y)
                px = float(qx.get("price", 0.0))
                py = float(qy.get("price", 0.0))
            else:
                import yfinance as yf
                for sym, container in [(sym_x, "x"), (sym_y, "y")]:
                    df = yf.download(sym, period="1d", interval="1m",
                                     progress=False, auto_adjust=True)
                    if df is not None and not df.empty:
                        price = float(df["Close"].iloc[-1])
                        if container == "x":
                            px = price
                        else:
                            py = price
        except Exception:
            pass
        return px, py

    def format_telegram_signal(self, ps: PairSignal) -> str:
        direction = "↑" if ps.zscore < 0 else "↓"
        return (
            f"⚖️ <b>PAIRS TRADE</b>  z={ps.zscore:+.2f}\n"
            f"  LONG  <b>{ps.symbol_long}</b>  @ ${ps.entry_long:.2f}\n"
            f"  SHORT <b>{ps.symbol_short}</b>  @ ${ps.entry_short:.2f}\n"
            f"  Spread: {ps.spread_pct:+.1f}%  |  Conf: {ps.confidence:.0f}\n"
            f"  Target: z→{ps.target_zscore:+.1f}  |  ½-life: {ps.half_life_days:.0f}d\n"
            f"  {ps.reason}"
        )


_ENGINE: Optional[PairsEngine] = None


def get_pairs_engine() -> PairsEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = PairsEngine()
    return _ENGINE
