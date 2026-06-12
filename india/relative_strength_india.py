"""
relative_strength_india.py — IBD-style Relative Strength ranking for NSE
Compares each stock's 5-day return vs Nifty50 → ranks 1–99.
Score: +12 for RS>85 LONG, +8 for RS>70 LONG, +10 for RS<30 SHORT.
Cache 30 min.
"""
import logging
import time as _time
from typing import Dict, List, Optional

logger = logging.getLogger("rs_india")

_rs_cache: Dict[str, float] = {}   # {symbol: rs_raw_return_diff}
_rs_scores: Dict[str, int] = {}    # {symbol: 1-99}
_cache_ts = 0.0
_CACHE_TTL = 1800.0  # 30 min


class RelativeStrength:
    def update_rs_scores(self, symbols: List[str], dhan_client=None) -> Dict[str, int]:
        """
        Compute 5-day return for each symbol vs Nifty50.
        Rank symbols by outperformance → percentile 1-99.
        Returns {symbol: rs_score}.
        """
        global _rs_cache, _rs_scores, _cache_ts
        now = _time.monotonic()
        if _rs_scores and now - _cache_ts < _CACHE_TTL:
            return _rs_scores

        try:
            from data_fetch_dhan import get_ohlcv, get_nifty_level

            nifty_info = get_nifty_level()
            nifty_chg = nifty_info.get("change_pct", 0.0)

            raw: Dict[str, float] = {}
            for sym in symbols:
                try:
                    df = get_ohlcv(sym, interval="5m", period="5d")
                    if df is None or df.empty or len(df) < 10:
                        continue
                    # 5-day return: last close / first close - 1
                    first_close = float(df["close"].iloc[0])
                    last_close  = float(df["close"].iloc[-1])
                    if first_close > 0:
                        stock_5d = (last_close / first_close - 1) * 100
                        raw[sym] = stock_5d - nifty_chg  # outperformance vs Nifty
                except Exception:
                    pass

            if not raw:
                return _rs_scores or {}

            # Rank to percentile 1-99
            sorted_syms = sorted(raw.keys(), key=lambda s: raw[s])
            n = len(sorted_syms)
            scores: Dict[str, int] = {}
            for i, sym in enumerate(sorted_syms):
                pct = int((i / max(n - 1, 1)) * 98) + 1  # 1 to 99
                scores[sym] = pct
                _rs_cache[sym] = raw[sym]

            _rs_scores = scores
            _cache_ts  = now
            return scores

        except Exception as e:
            logger.debug(f"update_rs_scores: {e}")
            return _rs_scores or {}

    def get_rs_score(self, symbol: str) -> int:
        return _rs_scores.get(symbol.upper(), 50)

    def score_signal(self, symbol: str, direction: str) -> int:
        rs = self.get_rs_score(symbol)
        if direction == "LONG":
            if rs > 85:  return 12
            if rs > 70:  return 8
            if rs < 40:  return -6
        elif direction == "SHORT":
            if rs < 15:  return 14
            if rs < 30:  return 10
            if rs > 60:  return -6
        return 0

    def get_sector_rs(self, symbol: str, sector_map: Dict[str, str]) -> float:
        sym   = symbol.upper()
        sector = sector_map.get(sym)
        if not sector:
            return 0.0
        my_raw = _rs_cache.get(sym, 0.0)
        peers  = [_rs_cache[s] for s, sec in sector_map.items()
                  if sec == sector and s != sym and s in _rs_cache]
        if not peers:
            return 0.0
        avg_peer = sum(peers) / len(peers)
        return round(my_raw - avg_peer, 3)
