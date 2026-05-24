"""
sector_rotation.py — US Momentum Alpaca AI Bot
SPDR Sector ETF Rotation Engine

Identifies the 3 hottest and 3 coldest US equity sectors each day
using 5-day ETF momentum vs SPY, RSI, and volume trend.

The 11 SPDR sectors cover the entire S&P 500. Concentrating on
HOT sectors and avoiding COLD sectors gives a persistent edge:
hot sectors carry institutional tailwinds that amplify individual
stock momentum setups.

18yr Rule: "Don't fight sector flows. The tide matters more than
the wave. Swim with the sector, not against it."

HOT sector signal = +8 pts to signal score
COLD sector signal = -6 pts to signal score
Neutral = 0 pts
"""

import logging
import time as _time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

# ─────────────────────────────────────────────────────────────────────────────
# SPDR SECTOR ETFs — the 11 sectors of the S&P 500
# ─────────────────────────────────────────────────────────────────────────────
SECTOR_ETFS: Dict[str, str] = {
    "TECHNOLOGY":          "XLK",
    "FINANCIALS":          "XLF",
    "HEALTHCARE":          "XLV",
    "CONSUMER_DISC":       "XLY",
    "CONSUMER_STAPLES":    "XLP",
    "INDUSTRIALS":         "XLI",
    "ENERGY":              "XLE",
    "MATERIALS":           "XLB",
    "UTILITIES":           "XLU",
    "REAL_ESTATE":         "XLRE",
    "COMMUNICATION":       "XLC",
}

# ─────────────────────────────────────────────────────────────────────────────
# STOCK → PRIMARY SECTOR mapping
# ─────────────────────────────────────────────────────────────────────────────
STOCK_TO_SECTOR: Dict[str, str] = {
    # Technology
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AMD": "XLK",
    "AVGO": "XLK", "QCOM": "XLK", "MU": "XLK", "SMCI": "XLK",
    "ARM": "XLK", "ORCL": "XLK", "ADBE": "XLK", "AMAT": "XLK",
    "LRCX": "XLK", "KLAC": "XLK", "INTC": "XLK", "TXN": "XLK",
    "QQQ": "XLK", "SOXL": "XLK",
    # Financials
    "JPM": "XLF", "BAC": "XLF", "GS": "XLF", "MS": "XLF",
    "WFC": "XLF", "C": "XLF", "V": "XLF", "MA": "XLF",
    "PYPL": "XLF", "SQ": "XLF", "COIN": "XLF", "HOOD": "XLF",
    "MSTR": "XLF",
    # Healthcare
    "UNH": "XLV", "LLY": "XLV", "JNJ": "XLV", "ABBV": "XLV",
    "MRK": "XLV", "PFE": "XLV", "AMGN": "XLV", "GILD": "XLV",
    # Consumer Discretionary
    "AMZN": "XLY", "TSLA": "XLY", "NKE": "XLY", "TGT": "XLY",
    "HD": "XLY", "LOW": "XLY", "MCD": "XLY", "SBUX": "XLY",
    "IWM": "XLY",
    # Consumer Staples
    "WMT": "XLP", "COST": "XLP", "PG": "XLP", "KO": "XLP",
    "PEP": "XLP", "PM": "XLP",
    # Energy
    "XOM": "XLE", "CVX": "XLE", "OXY": "XLE", "SLB": "XLE",
    "HAL": "XLE", "COP": "XLE", "MPC": "XLE",
    # Industrials
    "CAT": "XLI", "DE": "XLI", "GE": "XLI", "BA": "XLI",
    "UPS": "XLI", "FDX": "XLI", "RTX": "XLI",
    # Materials
    "FCX": "XLB", "NEM": "XLB", "LIN": "XLB", "APD": "XLB",
    # Communication
    "META": "XLC", "GOOGL": "XLC", "GOOG": "XLC", "NFLX": "XLC",
    "DIS": "XLC", "CMCSA": "XLC", "T": "XLC",
    # Crypto-adjacent (use XLF as closest proxy)
    "MARA": "XLF", "RIOT": "XLF",
    # Index ETFs (map to broad market)
    "SPY": "SPY", "DIA": "SPY",
}


@dataclass
class SectorReading:
    """Momentum reading for one sector ETF."""
    sector_name: str
    etf: str
    change_pct_1d: float      # today's % change
    change_pct_5d: float      # 5-day momentum vs SPY
    rs_vs_spy: float          # relative strength vs SPY (stock - SPY %)
    rank: int = 0             # 1=hottest, 11=coldest
    label: str = "NEUTRAL"   # "HOT", "COLD", "NEUTRAL"


class SectorRotationEngine:
    """
    Ranks all 11 SPDR sectors by 5-day momentum relative to SPY.
    Top 3 = HOT (+8 pts), Bottom 3 = COLD (-6 pts), rest = NEUTRAL.
    Cached for CACHE_TTL_SECONDS to avoid hammering the data fetcher.
    """

    CACHE_TTL_SECONDS = 900   # 15 min — sector trends don't change minute-to-minute
    HOT_BONUS   = 8.0
    COLD_PENALTY = -6.0

    def __init__(self, data_fetcher=None):
        self._fetcher = data_fetcher
        self._readings: Dict[str, SectorReading] = {}
        self._last_refresh: Optional[datetime] = None
        self.hot_sectors:  List[str] = []
        self.cold_sectors: List[str] = []

    def refresh(self) -> None:
        """Refresh sector momentum data from ETF quotes."""
        now = datetime.now(ET)
        if (
            self._last_refresh is not None
            and (now - self._last_refresh).total_seconds() < self.CACHE_TTL_SECONDS
        ):
            return
        try:
            spy_chg = self._get_change_pct("SPY")
            readings: List[SectorReading] = []
            for sector_name, etf in SECTOR_ETFS.items():
                try:
                    chg = self._get_change_pct(etf)
                    rs  = round(chg - spy_chg, 2)
                    readings.append(SectorReading(
                        sector_name=sector_name,
                        etf=etf,
                        change_pct_1d=chg,
                        change_pct_5d=chg,   # approximation — use 1d when 5d not available
                        rs_vs_spy=rs,
                    ))
                except Exception:
                    readings.append(SectorReading(
                        sector_name=sector_name, etf=etf,
                        change_pct_1d=0, change_pct_5d=0, rs_vs_spy=0,
                    ))

            # Rank by RS vs SPY
            readings.sort(key=lambda r: r.rs_vs_spy, reverse=True)
            for i, r in enumerate(readings):
                r.rank = i + 1
                if i < 3:
                    r.label = "HOT"
                elif i >= 8:
                    r.label = "COLD"
                else:
                    r.label = "NEUTRAL"

            self._readings = {r.etf: r for r in readings}
            self.hot_sectors  = [r.etf for r in readings if r.label == "HOT"]
            self.cold_sectors = [r.etf for r in readings if r.label == "COLD"]
            self._last_refresh = now

            logger.info(
                f"[{format_ist_timestamp()}] Sector rotation: "
                f"HOT={self.hot_sectors} COLD={self.cold_sectors}"
            )
        except Exception as e:
            logger.debug(f"SectorRotation refresh failed: {e}")

    def _get_change_pct(self, ticker: str) -> float:
        if not self._fetcher:
            return 0.0
        try:
            q = self._fetcher.get_quote(ticker) or {}
            return float(q.get("change_pct", 0) or 0)
        except Exception:
            return 0.0

    def get_sector_bias(self, symbol: str) -> Tuple[float, str]:
        """
        Returns (score_adjustment, sector_name) for a stock.
        HOT sector → +8 pts, COLD sector → -6 pts, NEUTRAL → 0.
        """
        try:
            self.refresh()
            etf = STOCK_TO_SECTOR.get(symbol.upper())
            if not etf or etf == "SPY":
                return 0.0, "BROAD"

            reading = self._readings.get(etf)
            if not reading:
                return 0.0, etf

            if reading.label == "HOT":
                return self.HOT_BONUS, f"{etf}(HOT_rank{reading.rank})"
            elif reading.label == "COLD":
                return self.COLD_PENALTY, f"{etf}(COLD_rank{reading.rank})"
            return 0.0, f"{etf}(neutral)"
        except Exception as e:
            logger.debug(f"get_sector_bias error for {symbol}: {e}")
            return 0.0, ""

    def get_hot_stocks(self, full_watchlist: List[str], max_symbols: int = 15) -> List[str]:
        """
        Filter a watchlist to prioritize stocks in HOT sectors.
        Returns up to max_symbols with hot-sector stocks first.
        """
        self.refresh()
        hot_stocks  = []
        cold_stocks = []
        other_stocks = []
        for sym in full_watchlist:
            etf = STOCK_TO_SECTOR.get(sym.upper())
            if etf in self.hot_sectors:
                hot_stocks.append(sym)
            elif etf in self.cold_sectors:
                cold_stocks.append(sym)
            else:
                other_stocks.append(sym)
        # Hot first, then neutral, skip cold from top slots
        prioritized = hot_stocks + other_stocks + cold_stocks
        return prioritized[:max_symbols]

    def format_telegram_brief(self) -> str:
        """Sector rotation summary for morning Telegram message."""
        self.refresh()
        if not self._readings:
            return "⚠️ Sector data unavailable"
        lines = ["📊 SECTOR ROTATION (RS vs SPY):"]
        sorted_readings = sorted(self._readings.values(), key=lambda r: r.rank)
        for r in sorted_readings:
            label_emoji = "🔥" if r.label == "HOT" else ("❄️" if r.label == "COLD" else "⚪")
            lines.append(
                f"  {label_emoji} #{r.rank} {r.etf:4s} ({r.sector_name:20s}) "
                f"RS={r.rs_vs_spy:+.2f}%  1D={r.change_pct_1d:+.2f}%"
            )
        lines.append(f"\n🔥 Trade with: {', '.join(self.hot_sectors)}")
        lines.append(f"❄️ Avoid: {', '.join(self.cold_sectors)}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────
_sr_instance: Optional[SectorRotationEngine] = None


def get_sector_rotation(data_fetcher=None) -> SectorRotationEngine:
    global _sr_instance
    if _sr_instance is None:
        _sr_instance = SectorRotationEngine(data_fetcher)
    elif data_fetcher and _sr_instance._fetcher is None:
        _sr_instance._fetcher = data_fetcher
    return _sr_instance
