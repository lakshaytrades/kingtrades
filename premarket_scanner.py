"""
premarket_scanner.py — US Momentum Alpaca AI Bot
Pre-Market Gap & Catalyst Scanner

Scans for stocks with significant pre-market moves BEFORE 9:30 AM ET.
These are the highest-probability momentum setups of the day.

Criteria for a high-priority pre-market play:
  - Gap > 4% (earnings beat, FDA approval, analyst upgrade, etc.)
  - Pre-market volume > 500k shares (institutional participation)
  - Gap + Catalyst (EPS beat OR news catalyst from CatalystScanner)

Scored 0-100 and returned sorted by conviction.
Called by morning_intelligence.py and main.py pre-market prep.
"""

import logging
from typing import Dict, List, Optional
from utils import get_current_ist_time, format_ist_timestamp

logger = logging.getLogger(__name__)

PM_CACHE_TTL = 600       # 10 minutes — re-scan every 10 min pre-market
MIN_GAP_PCT  = 4.0       # Minimum gap % to qualify
MIN_PM_VOL   = 200_000   # Minimum pre-market volume


class PremarketScanner:
    """
    Finds high-conviction pre-market gap setups.
    Integrates with CatalystScanner for EPS confirmation.
    """

    def __init__(self):
        self._cache: Optional[List[Dict]] = None
        self._cache_time = None

    # ── PUBLIC ─────────────────────────────────────────────────────────────

    def scan(self, symbols: List[str], top_n: int = 10) -> List[Dict]:
        """
        Scan symbols for pre-market gap plays.

        Returns list of dicts sorted by conviction score:
        [
          {
            "symbol": str,
            "gap_pct": float,
            "pm_volume": int,
            "prev_close": float,
            "pm_price": float,
            "catalyst": bool,
            "catalyst_reason": str,
            "score": float (0-100),
            "reason": str,
          }, ...
        ]
        """
        now = get_current_ist_time()
        if (self._cache is not None and self._cache_time is not None
                and (now - self._cache_time).total_seconds() < PM_CACHE_TTL):
            return self._cache[:top_n]

        results = []
        try:
            from data_fetch_alpaca import get_data_fetcher
            from catalyst_scanner import get_catalyst_scanner
            fetcher     = get_data_fetcher()
            cat_scanner = get_catalyst_scanner()

            for sym in symbols:
                try:
                    # Previous close from yesterday's daily bar
                    daily_df = fetcher.get_ohlcv(sym, interval="day", lookback_days=3)
                    if daily_df is None or len(daily_df) < 2:
                        continue
                    prev_close = float(daily_df["close"].iloc[-2])

                    # Current price (pre-market or regular quote)
                    quote = fetcher.get_quote(sym)
                    if not quote:
                        continue
                    pm_price = float(quote.get("ltp", 0) or 0)
                    if prev_close <= 0 or pm_price <= 0:
                        continue

                    gap_pct = (pm_price - prev_close) / prev_close * 100
                    if abs(gap_pct) < MIN_GAP_PCT:
                        continue

                    pm_vol = int(quote.get("volume", 0) or 0)

                    # Score calculation
                    score = 0.0
                    reasons = []

                    # Gap size: up to 40 pts
                    gap_abs = abs(gap_pct)
                    if gap_abs >= 15:
                        score += 40
                        reasons.append(f"gap {gap_pct:+.1f}%")
                    elif gap_abs >= 10:
                        score += 30
                        reasons.append(f"gap {gap_pct:+.1f}%")
                    elif gap_abs >= 7:
                        score += 22
                        reasons.append(f"gap {gap_pct:+.1f}%")
                    else:
                        score += 12
                        reasons.append(f"gap {gap_pct:+.1f}%")

                    # Catalyst: up to 30 pts
                    cat = cat_scanner.get_catalyst_boost(sym)
                    has_catalyst = cat.get("has_catalyst", False)
                    if has_catalyst:
                        score += min(30, cat.get("boost", 0) * 1.2)
                        reasons.append(cat.get("reason", "catalyst"))

                    # Volume: up to 20 pts
                    if pm_vol >= 1_000_000:
                        score += 20
                        reasons.append(f"PM vol {pm_vol/1e6:.1f}M")
                    elif pm_vol >= 500_000:
                        score += 12
                        reasons.append(f"PM vol {pm_vol/1000:.0f}k")
                    elif pm_vol >= MIN_PM_VOL:
                        score += 6

                    # Direction bonus: gaps above SPY trend score higher for LONG
                    if gap_pct > 0:
                        score += 10   # Gap-up setups preferred (momentum)

                    score = min(score, 100.0)

                    results.append({
                        "symbol":          sym,
                        "gap_pct":         round(gap_pct, 2),
                        "pm_volume":       pm_vol,
                        "prev_close":      round(prev_close, 2),
                        "pm_price":        round(pm_price, 2),
                        "catalyst":        has_catalyst,
                        "catalyst_reason": cat.get("reason", ""),
                        "score":           round(score, 1),
                        "reason":          " | ".join(reasons),
                        "direction":       "LONG" if gap_pct > 0 else "SHORT",
                    })

                except Exception as e:
                    logger.debug(f"PremarketScanner {sym}: {e}")

        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] PremarketScanner error: {e}")

        results.sort(key=lambda x: x["score"], reverse=True)

        if results:
            logger.info(
                f"[{format_ist_timestamp()}] PremarketScanner: "
                f"{len(results)} gap plays found — top: "
                + ", ".join(f"{r['symbol']} {r['gap_pct']:+.1f}% ({r['score']:.0f}pts)"
                            for r in results[:3])
            )

        self._cache = results
        self._cache_time = get_current_ist_time()
        return results[:top_n]

    def get_priority_symbols(self, symbols: List[str], top_n: int = 5) -> List[str]:
        """Return top pre-market gap symbols to prioritize in the watchlist scan."""
        plays = self.scan(symbols, top_n=top_n)
        return [p["symbol"] for p in plays if p["score"] >= 60]

    def clear_cache(self) -> None:
        self._cache = None
        self._cache_time = None


# ── SINGLETON ──────────────────────────────────────────────────────────────

_pm_scanner: Optional[PremarketScanner] = None


def get_premarket_scanner() -> PremarketScanner:
    global _pm_scanner
    if _pm_scanner is None:
        _pm_scanner = PremarketScanner()
    return _pm_scanner
