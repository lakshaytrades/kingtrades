"""
watchlist_manager.py — US Momentum Alpaca AI Bot
Dynamic US Stock Selection — Scans for Best Momentum Candidates

18yr Rule: "Don't trade 100 stocks. Find the 5-10 that are ALIVE today.
Momentum clusters. Where money flows, more money follows."

Features:
- 100+ stock universe (S&P 500 core + Nasdaq momentum + high-beta)
- Pre-market momentum scan at 9:00 AM ET
- Sector strength ranking — trade leaders not laggards
- ADR filter (min 1.0% daily range for intraday viability)
- Volume filter (today's vol vs average)
- Relative strength vs SPY
- Auto-removes symbols from blacklist (from self-learning)
"""

import logging
from datetime import timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np

from utils import format_ist_timestamp, get_current_ist_time, get_current_ist_date
import config

logger = logging.getLogger(__name__)

# -----------------------------------------------------------
# FULL LIQUID UNIVERSE (NYSE/NASDAQ — high volume / intraday)
# -----------------------------------------------------------
LIQUID_UNIVERSE = [
    # Mega-cap tech (deepest liquidity, daily momentum movers)
    "AAPL","MSFT","NVDA","AMZN","GOOGL","GOOG","META","TSLA",
    "AMD","NFLX","ADBE","CRM","ORCL","INTC","QCOM","MU",
    "AVGO","TXN","AMAT","LRCX","KLAC","MRVL","ON","MPWR",
    # High-beta momentum / meme stocks
    "COIN","HOOD","MSTR","PLTR","RBLX","SNAP","UBER","LYFT",
    "RIVN","LCID","NIO","SOFI","UPST","AFRM","OPEN","OPENDOOR",
    "ARM","SMCI","IONQ","QUBT","RGTI",
    # ETFs — best for scalping (most liquid instruments)
    "SPY","QQQ","IWM","TQQQ","SQQQ","SPXL","SPXS","UVXY",
    "XLK","XLF","XLE","XLV","XLY","XLRE",
    # Financials & Banks
    "JPM","GS","MS","BAC","WFC","C","BLK","SCHW","V","MA","PYPL",
    # Energy & Commodities
    "XOM","CVX","OXY","COP","SLB","HAL","MPC","PSX",
    # Healthcare & Biotech
    "UNH","JNJ","LLY","PFE","MRNA","BNTX","ABBV","BMY",
    "AMGN","GILD","BIIB","REGN","VRTX","DXCM",
    # Consumer & Retail
    "AMZN","WMT","TGT","COST","HD","LOW","MCD","SBUX",
    # Industrial & Defense
    "BA","LMT","RTX","NOC","GD","CAT","DE","GE",
]

# Sector mapping (US market)
SECTOR_MAP = {
    "TECH":       ["AAPL","MSFT","NVDA","AMD","INTC","QCOM","MU","AVGO","ADBE","CRM","ORCL"],
    "AI_SEMI":    ["NVDA","AMD","AVGO","AMAT","LRCX","KLAC","MU","MRVL","ARM","SMCI"],
    "FINTECH":    ["COIN","HOOD","SOFI","UPST","AFRM","PYPL","V","MA","SQ"],
    "BANKS":      ["JPM","GS","MS","BAC","WFC","C","BLK","SCHW"],
    "ENERGY":     ["XOM","CVX","OXY","COP","SLB","HAL","MPC","PSX"],
    "HEALTHCARE": ["UNH","LLY","PFE","MRNA","ABBV","BMY","AMGN","GILD","REGN","VRTX"],
    "CONSUMER":   ["AMZN","WMT","TGT","COST","HD","MCD","SBUX","TSLA"],
    "DEFENSE":    ["LMT","RTX","NOC","GD","BA"],
    "ETF":        ["SPY","QQQ","IWM","TQQQ","XLK","XLF","XLE"],
    "HIGH_BETA":  ["MSTR","PLTR","COIN","RIVN","NIO","SOFI","RBLX","SNAP"],
}


class WatchlistManager:
    """Dynamic watchlist engine - finds best momentum stocks each morning."""

    def __init__(self, name="momentum_watchlist", symbols=None):
        self.name = name
        self.symbols = symbols if symbols is not None else []
        self.last_updated = None

        # Scan state — initialised here so get_watchlist() never hits AttributeError
        self._watchlist: List[str] = []
        self._scored_stocks: List[Dict] = []
        self._sector_leaders: Dict = {}
        self._last_scan_time: Optional[float] = None
        self._scan_ttl: int = 15 * 60  # Rescan every 15 min

    # -------------------------------------------------------------------------
    # MAIN WATCHLIST GETTER
    # -------------------------------------------------------------------------

    def get_watchlist(self, data_fetcher=None, learner=None) -> List[str]:
        """
        Return today's active watchlist.
        Order: user-defined > momentum-scanned > default.
        """
        # User-defined list takes priority
        if config.CUSTOM_WATCHLIST_STR:
            wl = [s.strip().upper() for s in config.CUSTOM_WATCHLIST_STR.split(",") if s.strip()]
            if learner:
                wl = [s for s in wl if not learner.is_symbol_blacklisted(s)]
            return wl

        # Check if scan is fresh
        if (data_fetcher and
                (self._last_scan_time is None or
                 (get_current_ist_time().timestamp() - self._last_scan_time) > self._scan_ttl)):
            self._run_momentum_scan(data_fetcher, learner)

        if self._watchlist:
            return self._watchlist

        # Fallback to default
        return config.DEFAULT_WATCHLIST[:15]

    # -------------------------------------------------------
    # MOMENTUM SCAN
    # -------------------------------------------------------

    def _run_momentum_scan(self, data_fetcher, learner=None, top_n: int = 15):
        """
        Score all liquid stocks by momentum.
        18yr rule: "Trade what's moving RIGHT NOW, not what moved yesterday."
        """
        logger.info(f"[{format_ist_timestamp()}] Running momentum scan on {len(LIQUID_UNIVERSE)} stocks...")
        scores = []

        import time as _time
        for i, symbol in enumerate(LIQUID_UNIVERSE):
            # Skip blacklisted symbols
            if learner and learner.is_symbol_blacklisted(symbol):
                continue
            try:
                score = self._score_stock(symbol, data_fetcher)
                if score and score["tradeable"]:
                    scores.append(score)
            except Exception as e:
                logger.debug(f"Score failed {symbol}: {e}")
            # Throttle: 5 requests per 2s to stay within Alpaca rate limits
            if i % 5 == 4:
                _time.sleep(2.0)

        if not scores:
            logger.warning(f"[{format_ist_timestamp()}] Momentum scan returned no results")
            self._watchlist = config.DEFAULT_WATCHLIST[:top_n]
            return

        # Sort by composite momentum score
        scores.sort(key=lambda x: x["momentum_score"], reverse=True)
        self._scored_stocks = scores

        # Take top N
        self._watchlist = [s["symbol"] for s in scores[:top_n]]
        self._last_scan_time = get_current_ist_time().timestamp()

        # Update sector leaders
        self._sector_leaders = self._rank_sector_leaders(scores)

        logger.info(
            f"[{format_ist_timestamp()}] Watchlist updated: {self._watchlist[:8]}... "
            f"({len(self._watchlist)} stocks)"
        )

    def _score_stock(self, symbol: str, data_fetcher) -> Optional[Dict]:
        """Score a single stock for momentum potential."""
        try:
            quote = data_fetcher.get_quote(symbol)
            if not quote or quote["ltp"] <= 0:
                return None

            ltp         = float(quote["ltp"])
            change_pct  = float(quote.get("change_pct", 0))
            volume      = int(quote.get("volume", 0))
            high        = float(quote.get("high", ltp))
            low         = float(quote.get("low", ltp))

            # ADR check — must be above 0.8% for intraday viability
            intraday_range_pct = (high - low) / low * 100 if low > 0 else 0

            # Price filter: $1–$10000
            if ltp < 1 or ltp > 10000:
                return None

            # Momentum score
            score = 0.0

            # 1. Price change momentum (most important)
            score += abs(change_pct) * 10
            if change_pct > 0:
                score += 5   # Bullish bias bonus

            # 2. Intraday range (are we getting movement today?)
            score += intraday_range_pct * 5

            # 3. Volume factor — above 500K shares = liquid for US stocks
            if volume > 500_000:
                score += 15
            elif volume > 100_000:
                score += 8
            elif volume < 50_000:
                score -= 20  # Too illiquid

            # 4. Price momentum strength
            if abs(change_pct) > 2:
                score += 20  # Strong mover
            elif abs(change_pct) > 1:
                score += 10

            tradeable = (
                intraday_range_pct >= 0.5 and   # Some movement today
                volume >= 50_000 and             # Minimum liquidity
                ltp >= 1                         # Price filter
            )

            return {
                "symbol":        symbol,
                "ltp":           ltp,
                "change_pct":    change_pct,
                "volume":        volume,
                "range_pct":     round(intraday_range_pct, 2),
                "momentum_score": round(score, 1),
                "tradeable":     tradeable,
            }
        except Exception as e:
            logger.debug(f"[{format_ist_timestamp()}] Score error {symbol}: {e}")
            return None

    # -------------------------------------------------------
    # SECTOR LEADERSHIP
    # -------------------------------------------------------

    def _rank_sector_leaders(self, scores: List[Dict]) -> Dict[str, List[str]]:
        """
        Find top 2 stocks per sector by momentum score.
        18yr rule: "Trade sector leaders — they move first and furthest."
        """
        score_map = {s["symbol"]: s["momentum_score"] for s in scores}
        sector_leaders = {}

        for sector, symbols in SECTOR_MAP.items():
            sector_scores = [
                (sym, score_map.get(sym, 0))
                for sym in symbols if sym in score_map
            ]
            sector_scores.sort(key=lambda x: x[1], reverse=True)
            leaders = [sym for sym, _ in sector_scores[:2]]
            if leaders:
                sector_leaders[sector] = leaders

        return sector_leaders

    def get_sector_leaders(self) -> Dict[str, List[str]]:
        return self._sector_leaders

    def get_top_movers(self, n: int = 5) -> List[Dict]:
        """Return top N momentum stocks with their scores."""
        return self._scored_stocks[:n]

    def get_sector_for_symbol(self, symbol: str) -> str:
        for sector, syms in SECTOR_MAP.items():
            if symbol in syms:
                return sector
        return "OTHER"

    # -------------------------------------------------------
    # RELATIVE STRENGTH
    # -------------------------------------------------------

    def calculate_relative_strength(
        self,
        stock_change_pct: float,
        spy_change_pct: float,
    ) -> float:
        """
        Relative strength = stock return - SPY return.
        Positive = outperforming SPY (strong stock).
        18yr rule: "Buy the strongest stock in the strongest sector.
        Never buy a stock weaker than SPY in a weak market."
        """
        return stock_change_pct - spy_change_pct

    def filter_by_relative_strength(
        self,
        symbols: List[str],
        data_fetcher,
        spy_change_pct: float,
        min_rs: float = 0.3,
    ) -> List[str]:
        """Keep only stocks outperforming SPY by at least min_rs%."""
        strong = []
        for sym in symbols:
            try:
                q = data_fetcher.get_quote(sym)
                if q:
                    rs = self.calculate_relative_strength(q.get("change_pct", 0), spy_change_pct)
                    if rs >= min_rs:
                        strong.append(sym)
            except Exception:
                pass
        return strong if strong else symbols[:10]

    def format_watchlist_message(self) -> str:
        """Telegram-friendly watchlist summary."""
        if not self._scored_stocks:
            wl = config.DEFAULT_WATCHLIST[:10]
            return "📋 Watchlist (default):\n" + "\n".join(f"  • {s}" for s in wl)

        lines = ["📋 Today's Momentum Watchlist\n"]
        for i, s in enumerate(self._scored_stocks[:10], 1):
            arrow = "🟢" if s["change_pct"] >= 0 else "🔴"
            lines.append(
                f"{i}. {arrow} {s['symbol']:12s} "
                f"${s['ltp']:,.2f}  {s['change_pct']:+.1f}%  "
                f"Vol:{s['volume']//1000:.0f}K  "
                f"Score:{s['momentum_score']:.0f}"
            )
        return "\n".join(lines)
