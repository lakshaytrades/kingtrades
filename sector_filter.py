"""
sector_filter.py — Sector ETF Alignment Filter

O'Neil rule: "Only buy the leading stocks in leading sectors."
Top prop desks check the sector ETF before entering any single stock.
If NVDA is setting up bullish but SMH is down — skip. The sector is fighting you.

All checks are fail-open (return True on error) and cached 5 minutes.
"""

import time
import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Map every stock in the watchlist to its sector ETF
STOCK_TO_ETF: Dict[str, str] = {
    # Semiconductors → SMH
    "NVDA":"SMH","AMD":"SMH","INTC":"SMH","QCOM":"SMH","AVGO":"SMH","MU":"SMH",
    "AMAT":"SMH","LRCX":"SMH","KLAC":"SMH","MRVL":"SMH","ON":"SMH","TXN":"SMH",
    "ASML":"SMH","TSM":"SMH","ARM":"SMH","SMCI":"SMH",
    # Big Tech → QQQ
    "AAPL":"QQQ","MSFT":"QQQ","GOOGL":"QQQ","GOOG":"QQQ","META":"QQQ",
    "AMZN":"QQQ","NFLX":"QQQ","ADBE":"QQQ","CRM":"QQQ",
    # AI/Cloud → QQQ
    "PLTR":"QQQ","SNOW":"QQQ","NET":"QQQ","DDOG":"QQQ","MDB":"QQQ",
    # Finance → XLF
    "JPM":"XLF","GS":"XLF","MS":"XLF","BAC":"XLF","C":"XLF","WFC":"XLF",
    "BLK":"XLF","SCHW":"XLF","V":"XLF","MA":"XLF","AXP":"XLF",
    # Energy → XLE
    "XOM":"XLE","CVX":"XLE","COP":"XLE","OXY":"XLE","SLB":"XLE","HAL":"XLE",
    # EV/Auto → XLY
    "TSLA":"XLY","F":"XLY","GM":"XLY","RIVN":"XLY","LCID":"XLY",
    # Crypto-adjacent → BITX (use BTC ETF proxy)
    "COIN":"BITX","MSTR":"BITX","MARA":"BITX","RIOT":"BITX","HUT":"BITX",
    # Biotech → XBI
    "MRNA":"XBI","BNTX":"XBI","NVAX":"XBI","REGN":"XBI","BIIB":"XBI",
    # Defense → XAR
    "LMT":"XAR","RTX":"XAR","NOC":"XAR","GD":"XAR","BA":"XAR",
    # Consumer → XLY
    "HD":"XLY","LOW":"XLY","TGT":"XLY","WMT":"XLY","COST":"XLY",
}

_etf_cache: Dict[str, Tuple[float, float]] = {}   # {etf: (chg_pct, timestamp)}
_CACHE_TTL = 300.0   # 5 minutes


def _get_etf_change_pct(etf: str) -> Optional[float]:
    """Fetch ETF % change today via yfinance. Cached 5 min. Returns None on error."""
    now = time.time()
    if etf in _etf_cache:
        cached_val, cached_ts = _etf_cache[etf]
        if now - cached_ts < _CACHE_TTL:
            return cached_val
    try:
        import yfinance as yf
        ticker = yf.Ticker(etf)
        hist = ticker.history(period="2d", interval="1d")
        if hist is None or len(hist) < 2:
            return None
        prev_close = float(hist['Close'].iloc[-2])
        last_close = float(hist['Close'].iloc[-1])
        if prev_close <= 0:
            return None
        chg = (last_close - prev_close) / prev_close * 100.0
        _etf_cache[etf] = (chg, now)
        return chg
    except Exception as exc:
        logger.debug(f"sector_filter: {etf} fetch error: {exc}")
        return None


def check_sector_alignment(symbol: str, direction: str) -> Tuple[bool, str]:
    """
    Check that the stock's sector ETF is moving in the same direction.

    Returns (pass, reason). Fail-open: returns (True, "SECTOR_SKIP") on any error.

    Thresholds:
      Sector ETF up >0.5%  → favors LONG (block SHORT unless score exceptional)
      Sector ETF down <-0.5% → favors SHORT (block LONG unless score exceptional)
      Within ±0.5%        → neutral (allow both directions)
    """
    try:
        import config as _cfg_sf
        if not getattr(_cfg_sf, 'SECTOR_FILTER_ENABLED', True):
            return True, "SECTOR_DISABLED"

        etf = STOCK_TO_ETF.get(symbol.upper())
        if not etf:
            return True, "SECTOR_UNKNOWN"   # not in our map — allow

        chg = _get_etf_change_pct(etf)
        if chg is None:
            return True, "SECTOR_DATA_MISSING"

        is_long  = direction in ('LONG', 'BUY')
        is_short = direction in ('SHORT', 'SELL')

        if is_long and chg < -1.0:
            return False, (
                f"Sector ETF {etf} is down {chg:.1f}% today — "
                f"don't buy {symbol} against a falling sector. O'Neil: leading stocks in leading sectors."
            )
        if is_short and chg > 1.0:
            return False, (
                f"Sector ETF {etf} is up {chg:.1f}% today — "
                f"don't short {symbol} against a rising sector."
            )
        return True, f"SECTOR_OK({etf}{chg:+.1f}%)"
    except Exception as exc:
        logger.debug(f"check_sector_alignment error: {exc}")
        return True, "SECTOR_SKIP"
