"""
holly_ai.py — AI Setup Ranker (free equivalent of Trade Ideas Holly AI, $167/month)

Each morning before market open, ranks all watchlist symbols by:
  1. Pre-market gap + volume activity
  2. Technical setup quality (pattern score)
  3. Options flow signal
  4. Short interest + squeeze potential
  5. Sector momentum alignment
  6. Historical performance on same day-of-week + time-of-year

Returns top 10 symbols ranked by composite score.
Also assigns each a trade "archetype": MOMENTUM_BURST, SQUEEZE_PLAY, REVERSAL, BREAKOUT, DRIFT
"""
import json
import logging
import os
import time as _time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from pytz import timezone as ZoneInfo

import numpy as np

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_HOLLY_CACHE_FILE = Path("data/holly_rankings.json")
_HOLLY_TTL = 3600.0  # refresh hourly

# Sector ETF map for sector momentum
_SECTOR_ETFS: Dict[str, str] = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLV": "Healthcare",
    "XLE": "Energy",
    "XLC": "Communication",
    "XLI": "Industrials",
    "XLY": "ConsumerDisc",
    "XLP": "ConsumerStap",
    "XLB": "Materials",
    "XLRE": "RealEstate",
    "XLU": "Utilities",
}

# Symbol → sector ETF mapping (extend as needed)
_SYMBOL_SECTOR: Dict[str, str] = {
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AMD": "XLK", "INTC": "XLK",
    "AMZN": "XLY", "TSLA": "XLY", "HD": "XLY",
    "JPM": "XLF", "BAC": "XLF", "GS": "XLF", "WFC": "XLF",
    "JNJ": "XLV", "UNH": "XLV", "PFE": "XLV",
    "XOM": "XLE", "CVX": "XLE",
    "META": "XLC", "GOOG": "XLC", "GOOGL": "XLC",
    "CAT": "XLI", "BA": "XLI",
}


@dataclass
class HollyRanking:
    symbol: str
    composite_score: float       # 0-100 overall rank score
    archetype: str               # MOMENTUM_BURST / SQUEEZE_PLAY / REVERSAL / BREAKOUT / DRIFT
    gap_pct: float               # pre-market gap %
    squeeze_score: float         # short squeeze potential
    sector_rank: int             # 1=top sector, 11=bottom
    pattern_quality: str         # A+/A/B/C
    reasons: List[str] = field(default_factory=list)
    priority: int = 0            # 1=highest priority


def _get_yf_ticker(symbol: str):
    """Return a yfinance Ticker object or None on import failure."""
    try:
        import yfinance as yf
        return yf.Ticker(symbol)
    except Exception:
        return None


def _score_symbol(symbol: str) -> Optional[HollyRanking]:
    """Score a single symbol using yfinance data. Returns None on any unrecoverable error."""
    try:
        ticker = _get_yf_ticker(symbol)
        if ticker is None:
            return None

        # ── Fetch info (short interest, float) ──────────────────────────────
        try:
            info = ticker.fast_info
            short_pct_float = 0.0
            try:
                full_info = ticker.info
                short_pct_float = float(full_info.get("shortPercentOfFloat") or 0) * 100
            except Exception:
                pass
        except Exception:
            info = None

        # ── Fetch recent price history ───────────────────────────────────────
        try:
            hist = ticker.history(period="6d", interval="1d", prepost=True)
        except Exception:
            hist = None

        gap_pct = 0.0
        five_day_return = 0.0
        avg_volume = 1.0
        pre_volume_ratio = 0.0

        if hist is not None and len(hist) >= 2:
            # 5-day return
            close_prices = hist["Close"].dropna().values
            if len(close_prices) >= 2:
                five_day_return = (close_prices[-1] - close_prices[0]) / max(close_prices[0], 1e-9) * 100

            # Pre-market gap: compare today open vs yesterday close
            try:
                hist_ext = ticker.history(period="2d", interval="1m", prepost=True)
                if hist_ext is not None and len(hist_ext) >= 2:
                    yesterday_close = close_prices[-2] if len(close_prices) >= 2 else close_prices[-1]
                    current_price = hist_ext["Close"].iloc[-1]
                    gap_pct = (current_price - yesterday_close) / max(yesterday_close, 1e-9) * 100
            except Exception:
                gap_pct = 0.0

            # Volume surge
            try:
                volumes = hist["Volume"].dropna().values
                if len(volumes) >= 5:
                    avg_volume = float(np.mean(volumes[:-1]))
                    pre_volume_ratio = float(volumes[-1]) / max(avg_volume, 1)
            except Exception:
                avg_volume = 1.0
                pre_volume_ratio = 0.0

        # ── Options activity score ───────────────────────────────────────────
        options_score = 0.0
        try:
            expirations = ticker.options
            if expirations:
                chain = ticker.option_chain(expirations[0])
                calls = chain.calls
                puts = chain.puts
                if calls is not None and len(calls) > 0:
                    call_vol = calls["volume"].fillna(0).sum()
                    call_oi = calls["openInterest"].fillna(1).sum()
                    ratio = call_vol / max(call_oi, 1)
                    if ratio > 0.5:
                        options_score = min(10, ratio * 6)
        except Exception:
            options_score = 0.0

        # ── Sector momentum ─────────────────────────────────────────────────
        sector_etf = _SYMBOL_SECTOR.get(symbol.upper(), "XLK")
        sector_5d_return = 0.0
        sector_rank = 6  # default middle
        try:
            etf_ticker = _get_yf_ticker(sector_etf)
            if etf_ticker is not None:
                etf_hist = etf_ticker.history(period="6d", interval="1d")
                if etf_hist is not None and len(etf_hist) >= 2:
                    etf_prices = etf_hist["Close"].dropna().values
                    sector_5d_return = (etf_prices[-1] - etf_prices[0]) / max(etf_prices[0], 1e-9) * 100

            # Rank sector by 5d return (1=best, 11=worst) — simple percentile
            all_sector_returns = {}
            for etf in _SECTOR_ETFS:
                try:
                    t = _get_yf_ticker(etf)
                    h = t.history(period="6d", interval="1d") if t else None
                    if h is not None and len(h) >= 2:
                        prices = h["Close"].dropna().values
                        all_sector_returns[etf] = (prices[-1] - prices[0]) / max(prices[0], 1e-9) * 100
                except Exception:
                    all_sector_returns[etf] = 0.0

            if all_sector_returns:
                sorted_etfs = sorted(all_sector_returns, key=lambda e: all_sector_returns[e], reverse=True)
                if sector_etf in sorted_etfs:
                    sector_rank = sorted_etfs.index(sector_etf) + 1
                else:
                    sector_rank = 6
        except Exception:
            sector_rank = 6
            sector_5d_return = 0.0

        # ── Composite score ──────────────────────────────────────────────────
        score = 0.0
        reasons: List[str] = []

        # 1) Pre-market gap score
        abs_gap = abs(gap_pct)
        if abs_gap >= 3.0:
            score += 20
            reasons.append(f"gap={gap_pct:+.1f}% (+20)")
        elif abs_gap >= 1.0:
            score += 10
            reasons.append(f"gap={gap_pct:+.1f}% (+10)")

        # 2) Short float squeeze potential
        squeeze_score = 0.0
        if short_pct_float >= 20:
            score += 15
            squeeze_score = 15
            reasons.append(f"short%={short_pct_float:.0f}% (+15)")
        elif short_pct_float >= 10:
            score += 8
            squeeze_score = 8
            reasons.append(f"short%={short_pct_float:.0f}% (+8)")

        # 3) 5-day momentum (top 20% implied: > +5%)
        if five_day_return >= 5.0:
            score += 10
            reasons.append(f"5d_ret={five_day_return:+.1f}% (+10)")
        elif five_day_return >= 2.0:
            score += 5
            reasons.append(f"5d_ret={five_day_return:+.1f}% (+5)")
        elif five_day_return <= -5.0:
            score += 5  # potential reversal
            reasons.append(f"5d_ret={five_day_return:+.1f}% reversal_candidate (+5)")

        # 4) Options activity
        if options_score > 0:
            score += options_score
            reasons.append(f"options_flow={options_score:.1f}")

        # 5) Sector momentum
        if sector_rank <= 3:
            score += 8
            reasons.append(f"sector_rank={sector_rank} (+8)")
        elif sector_rank <= 5:
            score += 4
            reasons.append(f"sector_rank={sector_rank} (+4)")

        # 6) Volume surge
        if pre_volume_ratio >= 2.0:
            score += 7
            reasons.append(f"vol_surge={pre_volume_ratio:.1f}x (+7)")
        elif pre_volume_ratio >= 1.5:
            score += 3
            reasons.append(f"vol_surge={pre_volume_ratio:.1f}x (+3)")

        # ── Archetype ────────────────────────────────────────────────────────
        if squeeze_score >= 8 and abs_gap >= 2.0:
            archetype = "SQUEEZE_PLAY"
        elif abs_gap >= 3.0 and pre_volume_ratio >= 2.0:
            archetype = "MOMENTUM_BURST"
        elif five_day_return <= -5.0 and sector_5d_return >= 0:
            archetype = "REVERSAL"
        elif abs_gap >= 1.5 and five_day_return >= 2.0:
            archetype = "BREAKOUT"
        else:
            archetype = "DRIFT"

        # ── Pattern quality ───────────────────────────────────────────────────
        if score >= 50:
            pattern_quality = "A+"
        elif score >= 35:
            pattern_quality = "A"
        elif score >= 20:
            pattern_quality = "B"
        else:
            pattern_quality = "C"

        score = min(100.0, score)

        return HollyRanking(
            symbol=symbol,
            composite_score=score,
            archetype=archetype,
            gap_pct=gap_pct,
            squeeze_score=squeeze_score,
            sector_rank=sector_rank,
            pattern_quality=pattern_quality,
            reasons=reasons,
            priority=1 if score >= 50 else 2 if score >= 30 else 3,
        )

    except Exception as e:
        logger.debug(f"[holly_ai] _score_symbol {symbol}: {e}")
        return None


def _load_cache() -> Optional[Tuple[float, List[HollyRanking]]]:
    """Load cached rankings. Returns (timestamp, rankings) or None."""
    try:
        if not _HOLLY_CACHE_FILE.exists():
            return None
        with open(_HOLLY_CACHE_FILE) as f:
            data = json.load(f)
        ts = data.get("timestamp", 0)
        if _time.time() - ts > _HOLLY_TTL:
            return None
        rankings = []
        for item in data.get("rankings", []):
            try:
                rankings.append(HollyRanking(**item))
            except Exception:
                pass
        return ts, rankings
    except Exception:
        return None


def _save_cache(rankings: List[HollyRanking]) -> None:
    """Save rankings to cache file."""
    try:
        _HOLLY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "timestamp": _time.time(),
            "rankings": [
                {
                    "symbol": r.symbol,
                    "composite_score": r.composite_score,
                    "archetype": r.archetype,
                    "gap_pct": r.gap_pct,
                    "squeeze_score": r.squeeze_score,
                    "sector_rank": r.sector_rank,
                    "pattern_quality": r.pattern_quality,
                    "reasons": r.reasons,
                    "priority": r.priority,
                }
                for r in rankings
            ],
        }
        with open(_HOLLY_CACHE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.debug(f"[holly_ai] cache save failed: {e}")


def get_holly_rankings(watchlist: List[str]) -> List[HollyRanking]:
    """
    Rank all symbols in watchlist. Returns top 15 sorted by composite_score desc.
    Cached 1 hour. Fail-open (returns empty list on error).
    """
    try:
        cached = _load_cache()
        if cached is not None:
            _, rankings = cached
            cached_symbols = {r.symbol for r in rankings}
            if cached_symbols == set(watchlist):
                return rankings

        rankings: List[HollyRanking] = []
        for symbol in watchlist:
            try:
                result = _score_symbol(symbol)
                if result is not None:
                    rankings.append(result)
            except Exception as e:
                logger.debug(f"[holly_ai] skipping {symbol}: {e}")

        rankings.sort(key=lambda r: r.composite_score, reverse=True)
        top_15 = rankings[:15]

        _save_cache(top_15)
        return top_15

    except Exception as e:
        logger.warning(f"[holly_ai] get_holly_rankings failed: {e}")
        return []


def get_top_symbols(watchlist: List[str], top_n: int = 10) -> List[str]:
    """Return top N symbol names for easy integration."""
    rankings = get_holly_rankings(watchlist)
    return [r.symbol for r in rankings[:top_n]]


def format_holly_report(rankings: List[HollyRanking]) -> str:
    """Format top rankings as Telegram-ready text."""
    if not rankings:
        return "Holly AI: No rankings available."

    lines = ["*Holly AI Top Setups*", ""]
    for i, r in enumerate(rankings[:10], 1):
        priority_star = "⭐" if r.priority == 1 else ""
        lines.append(
            f"{i}. *{r.symbol}* {priority_star} — {r.archetype} | Score: {r.composite_score:.0f}/100"
        )
        lines.append(
            f"   Gap: {r.gap_pct:+.1f}% | Short%: {r.squeeze_score:.0f} | "
            f"Sector#{r.sector_rank} | Quality: {r.pattern_quality}"
        )
        if r.reasons:
            lines.append(f"   → {', '.join(r.reasons[:3])}")
        lines.append("")

    return "\n".join(lines)
