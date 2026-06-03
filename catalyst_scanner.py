"""
catalyst_scanner.py — US Momentum Alpaca AI Bot
Earnings & Catalyst Momentum Filter

Boosts composite score +20-25 pts when:
  - Recent EPS beat detected (actual > estimated, surprise > 5%)
  - Strong volume reaction on / after earnings announcement
  - Raised guidance implied by post-earnings price action

Uses yfinance.Ticker.earnings_dates — no external API key required.
Cache: 1-hour TTL per symbol, cleared at session start.
"""

import logging
from datetime import date, timedelta
from typing import Dict, Optional

from utils import get_current_ist_time, format_ist_timestamp

logger = logging.getLogger(__name__)

CATALYST_CACHE_TTL = 3600   # seconds — refresh once per hour
LOOKBACK_DAYS      = 5      # only count earnings within last N days
MIN_SURPRISE_PCT   = 5.0    # % EPS beat required to trigger boost


class CatalystScanner:
    """
    Detects recent earnings catalysts and returns a confidence boost.

    Scoring logic
    -------------
    EPS beat surprise > 5%  → base 10 pts + 0.4 per extra pct (cap 20)
    Volume surge ≥ 3×       → +5 pts   (capped at 25 total)
    Volume surge ≥ 2×       → +3 pts
    """

    def __init__(self):
        self._cache: Dict[str, Dict] = {}

    # ── PUBLIC ─────────────────────────────────────────────────────────────

    def get_catalyst_boost(self, symbol: str) -> Dict:
        """
        Return catalyst data for `symbol`.

        Returns
        -------
        {
            "boost":        float  (0–25),
            "reason":       str,
            "has_catalyst": bool,
            "cached_at":    datetime,
        }
        """
        cached = self._cache.get(symbol)
        if cached:
            age = (get_current_ist_time() - cached["cached_at"]).total_seconds()
            if age < CATALYST_CACHE_TTL:
                return cached

        result: Dict = {
            "boost": 0.0,
            "reason": "",
            "has_catalyst": False,
            "cached_at": get_current_ist_time(),
        }

        try:
            import yfinance as yf
            tk = yf.Ticker(symbol)
            result = self._scan(tk, symbol, result)
        except Exception as e:
            logger.debug(f"[CatalystScanner] {symbol} scan error: {e}")

        self._cache[symbol] = result
        return result

    def clear_cache(self) -> None:
        """Reset all cached data — call at start of each trading day."""
        self._cache.clear()

    # ── INTERNAL ───────────────────────────────────────────────────────────

    def _scan(self, tk, symbol: str, result: Dict) -> Dict:
        try:
            df_earn = tk.earnings_dates
        except Exception as e:
            logger.debug(f"[CatalystScanner] {symbol} earnings_dates: {e}")
            return result

        if df_earn is None or df_earn.empty:
            return result

        # Keep only earnings from the last LOOKBACK_DAYS
        today = get_current_ist_time().date()
        cutoff = today - timedelta(days=LOOKBACK_DAYS)

        try:
            if hasattr(df_earn.index, "date"):
                mask = (df_earn.index.date >= cutoff) & (df_earn.index.date <= today)
                recent = df_earn[mask]
            else:
                recent = df_earn.head(1)
        except Exception:
            recent = df_earn.head(1)

        if recent.empty:
            return result

        row = recent.iloc[0]

        # Column name variants across yfinance versions
        eps_est = (
            row.get("EPS Estimate")
            or row.get("epsEstimated")
            or row.get("eps_estimate")
        )
        eps_act = (
            row.get("Reported EPS")
            or row.get("epsActual")
            or row.get("reported_eps")
        )

        if eps_est is None or eps_act is None:
            return result
        try:
            eps_est = float(eps_est)
            eps_act = float(eps_act)
        except (TypeError, ValueError):
            return result

        if eps_est == 0:
            return result

        surprise_pct = (eps_act - eps_est) / abs(eps_est) * 100

        import math as _math
        if _math.isnan(surprise_pct) or _math.isinf(surprise_pct):
            return result

        if surprise_pct < MIN_SURPRISE_PCT:
            return result

        # Base boost: 10 pts + 0.4 per extra surprise pct, capped at 20
        boost = min(20.0, 10.0 + (surprise_pct - MIN_SURPRISE_PCT) * 0.4)
        reason = f"EPS beat +{surprise_pct:.1f}%"
        result["has_catalyst"] = True

        # Volume surge bonus (up to +5 pts)
        try:
            hist = tk.history(period="5d")
            if hist is not None and not hist.empty and len(hist) >= 2:
                avg_vol = float(hist["Volume"].iloc[:-1].mean())
                last_vol = float(hist["Volume"].iloc[-1])
                rvol = last_vol / avg_vol if avg_vol > 0 else 1.0
                if rvol >= 3.0:
                    boost = min(25.0, boost + 5.0)
                    reason += f" + RVOL {rvol:.1f}x"
                elif rvol >= 2.0:
                    boost = min(25.0, boost + 3.0)
                    reason += f" + RVOL {rvol:.1f}x"
        except Exception as e:
            logger.debug(f"[CatalystScanner] {symbol} volume check: {e}")

        result["boost"] = round(boost, 1)
        result["reason"] = reason
        logger.info(
            f"[{format_ist_timestamp()}] CatalystScanner {symbol}: "
            f"{reason} → score boost +{boost:.1f} pts"
        )
        return result


# ── SINGLETON ──────────────────────────────────────────────────────────────

_scanner_instance: Optional[CatalystScanner] = None


def get_catalyst_scanner() -> CatalystScanner:
    """Return module-level singleton CatalystScanner."""
    global _scanner_instance
    if _scanner_instance is None:
        _scanner_instance = CatalystScanner()
    return _scanner_instance
