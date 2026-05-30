"""
institutional_strategies.py — JP Morgan / Goldman / Citadel / Renaissance Grade Signals

6 strategies used by top-tier systematic funds. All free data. All fail-open.

Strategy 1: Cross-Sectional Momentum (AQR, Renaissance, Two Sigma)
  Rank stocks by 20-day return. Top 20% = momentum longs, Bottom 20% = momentum shorts.
  Score: +8 top quintile, -6 bottom quintile.

Strategy 2: VWAP Reclaim (prop desks, CME market makers)
  Price crossing VWAP from below with volume surge = institutional accumulation.
  Score: +12 reclaim + volume > 1.5x, +6 reclaim only.

Strategy 3: Power Hour Momentum (all equity desks)
  3:00–3:30 PM ET = institutional rebalancing window.
  Score: +8 if score >= 72 in power hour, +5 otherwise.

Strategy 4: Statistical Pairs Spread (JP Morgan, Goldman stat arb)
  NVDA/AMD, AAPL/MSFT, GS/JPM, XOM/CVX, COIN/MSTR spread.
  Score: +6 if direction aligns with pairs convergence.

Strategy 5: Time-of-Day RVOL (all algo systems)
  Compare current bar volume to same time slot on prior days (not flat average).
  Score: +12 P95, +7 P85, +3 P75, -4 below P40.

Strategy 6: Sortino-Based Dynamic Sizing (Bridgewater, Citadel)
  Weight position size by per-symbol downside deviation.
  Returns size_multiplier: 0.5x–1.5x based on recent Sortino ratio.
"""

import logging
import time as _time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


# ── Strategy 1: Cross-Sectional Momentum ─────────────────────────────────────

_csm_cache: Dict = {}    # {cache_key: {"ts": float, "ranks": {symbol: pct_rank}}}
_CSM_TTL = 3600.0        # refresh hourly


def get_cross_sectional_rank(symbol: str, watchlist: List[str]) -> Tuple[float, str]:
    """
    Rank symbol by 20-day momentum vs all watchlist peers.
    Returns (score_delta, reason). Cached 1h per watchlist set.
    """
    if not symbol or not watchlist:
        return 0.0, ""
    try:
        import yfinance as yf
        now = _time.monotonic()
        peers = list(dict.fromkeys([symbol] + [s for s in watchlist if s]))
        cache_key = ",".join(sorted(peers[:30]))   # cap to avoid giant keys
        cached = _csm_cache.get(cache_key)
        if cached and now - cached["ts"] < _CSM_TTL:
            ranks = cached["ranks"]
        else:
            # Batch download 22 days to get 20 trading-day returns (weekends add ~2 days)
            raw = yf.download(
                peers[:30], period="22d", interval="1d",
                auto_adjust=True, threads=True, progress=False, timeout=20,
                group_by="ticker",
            )
            if raw is None or raw.empty:
                return 0.0, ""
            returns: Dict[str, float] = {}
            for sym in peers[:30]:
                try:
                    if len(peers) == 1:
                        closes = raw["Close"] if "Close" in raw.columns else None
                    else:
                        closes = raw[sym]["Close"] if sym in raw.columns.get_level_values(0) else None
                    if closes is None or len(closes) < 2:
                        continue
                    ret = float(closes.iloc[-1] / closes.iloc[0] - 1)
                    returns[sym] = ret
                except Exception:
                    continue
            if not returns:
                return 0.0, ""
            vals = list(returns.values())
            sorted_vals = sorted(vals)
            ranks = {}
            for s, r in returns.items():
                pos = sorted_vals.index(r)
                ranks[s] = pos / max(len(sorted_vals) - 1, 1)
            _csm_cache[cache_key] = {"ts": now, "ranks": ranks}

        rank_pct = ranks.get(symbol)
        if rank_pct is None:
            return 0.0, ""
        if rank_pct >= 0.80:
            return 8.0, f"CSM_TOP_QUINTILE rank={rank_pct:.0%}"
        if rank_pct >= 0.60:
            return 3.0, f"CSM_TOP_40 rank={rank_pct:.0%}"
        if rank_pct <= 0.20:
            return -6.0, f"CSM_BOTTOM_QUINTILE rank={rank_pct:.0%}"
        if rank_pct <= 0.40:
            return -2.0, f"CSM_BOTTOM_40 rank={rank_pct:.0%}"
        return 0.0, ""
    except Exception as _e:
        logger.debug(f"get_cross_sectional_rank({symbol}): {_e}")
        return 0.0, ""


# ── Strategy 2: VWAP Reclaim ─────────────────────────────────────────────────

def get_vwap_reclaim_score(df_5m, current_price: float, direction: str, vwap: float) -> Tuple[float, str]:
    """
    Detect VWAP reclaim: price was below VWAP in last 3 bars, now above.
    LONG reclaim (below→above) = institutional accumulation signal.
    SHORT breakdown (above→below) = distribution signal.
    """
    try:
        if df_5m is None or df_5m.empty or vwap <= 0 or current_price <= 0:
            return 0.0, ""
        if len(df_5m) < 4:
            return 0.0, ""

        closes = df_5m["close"].values if "close" in df_5m.columns else df_5m["Close"].values
        volumes = df_5m["volume"].values if "volume" in df_5m.columns else df_5m["Volume"].values

        last_close  = float(closes[-1])
        prev_closes = closes[-4:-1]   # 3 bars before last

        was_below_vwap = all(c < vwap for c in prev_closes)
        was_above_vwap = all(c > vwap for c in prev_closes)
        now_above_vwap = last_close > vwap
        now_below_vwap = last_close < vwap

        avg_vol = float(np.mean(volumes[:-1])) if len(volumes) > 1 else 0.0
        cur_vol = float(volumes[-1])
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1.0

        if direction in ("LONG", "BUY"):
            if was_below_vwap and now_above_vwap:
                if vol_ratio >= 1.5:
                    return 12.0, f"VWAP_RECLAIM vol={vol_ratio:.1f}x vwap=${vwap:.2f}"
                return 6.0, f"VWAP_RECLAIM(weak) vol={vol_ratio:.1f}x"
        else:  # SHORT
            if was_above_vwap and now_below_vwap:
                if vol_ratio >= 1.5:
                    return 12.0, f"VWAP_BREAKDOWN vol={vol_ratio:.1f}x vwap=${vwap:.2f}"
                return 6.0, f"VWAP_BREAKDOWN(weak) vol={vol_ratio:.1f}x"
        return 0.0, ""
    except Exception as _e:
        logger.debug(f"get_vwap_reclaim_score: {_e}")
        return 0.0, ""


# ── Strategy 3: Power Hour Momentum ──────────────────────────────────────────

def get_power_hour_score(direction: str, current_score: float) -> Tuple[float, str]:
    """
    Score boost during 3:00–3:30 PM ET institutional rebalancing window.
    Strongest directional moves of the day. Only boosts high-conviction signals.
    """
    try:
        now_et = datetime.now(ET)
        et_hour, et_min = now_et.hour, now_et.minute
        in_power_hour = (et_hour == 15 and et_min < 30)
        if not in_power_hour:
            return 0.0, ""
        if current_score >= 72:
            return 8.0, "POWER_HOUR_BOOST score>=72"
        if current_score >= 60:
            return 5.0, "POWER_HOUR_BOOST score>=60"
        return 0.0, ""
    except Exception as _e:
        logger.debug(f"get_power_hour_score: {_e}")
        return 0.0, ""


# ── Strategy 4: Statistical Pairs Spread ─────────────────────────────────────

PAIRS = [
    ("NVDA", "AMD"),
    ("AAPL", "MSFT"),
    ("GS",   "JPM"),
    ("XOM",  "CVX"),
    ("COIN", "MSTR"),
]
_pairs_cache: Dict = {}   # {pair_key: {"ts": float, "z_score": float, "laggard": str}}
_PAIRS_TTL = 1800.0       # 30 min cache


def _fetch_pair_zscore(sym_a: str, sym_b: str) -> Tuple[float, str]:
    """
    Compute 20-day z-score of normalized spread between sym_a and sym_b.
    Returns (z_score, laggard_symbol).
    Positive z means sym_a is expensive relative to sym_b.
    """
    try:
        import yfinance as yf
        raw = yf.download(
            [sym_a, sym_b], period="22d", interval="1d",
            auto_adjust=True, threads=True, progress=False, timeout=15,
            group_by="ticker",
        )
        if raw is None or raw.empty:
            return 0.0, ""
        try:
            closes_a = raw[sym_a]["Close"].dropna()
            closes_b = raw[sym_b]["Close"].dropna()
        except KeyError:
            return 0.0, ""
        if len(closes_a) < 5 or len(closes_b) < 5:
            return 0.0, ""
        # Normalize by dividing by first close (base-100 both series)
        norm_a = closes_a / float(closes_a.iloc[0])
        norm_b = closes_b / float(closes_b.iloc[0])
        spread = norm_a - norm_b
        z = float((spread.iloc[-1] - spread.mean()) / (spread.std() + 1e-9))
        laggard = sym_b if z > 0 else sym_a   # positive z = sym_a led, sym_b lagged
        return z, laggard
    except Exception as _e:
        logger.debug(f"_fetch_pair_zscore({sym_a},{sym_b}): {_e}")
        return 0.0, ""


def get_pairs_signal(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Statistical pairs convergence: if symbol is the laggard in a known pair
    and spread > 2 std devs, trade the reversion.
    Returns (score_delta, reason).
    """
    try:
        now = _time.monotonic()
        for sym_a, sym_b in PAIRS:
            if symbol not in (sym_a, sym_b):
                continue
            pair_key = f"{sym_a}_{sym_b}"
            cached = _pairs_cache.get(pair_key)
            if cached and now - cached["ts"] < _PAIRS_TTL:
                z_score = cached["z_score"]
                laggard = cached["laggard"]
            else:
                z_score, laggard = _fetch_pair_zscore(sym_a, sym_b)
                _pairs_cache[pair_key] = {"ts": now, "z_score": z_score, "laggard": laggard}

            if not laggard or abs(z_score) < 2.0:
                continue   # spread not extreme enough

            # Convergence trade: buy the laggard, sell the leader
            if symbol == laggard:
                if direction in ("LONG", "BUY"):
                    # Laggard should catch up → LONG aligns with convergence
                    return 6.0, f"PAIRS_CONVERGENCE {sym_a}/{sym_b} z={z_score:.1f} laggard={laggard}"
            else:
                # symbol is the leader (expensive side)
                if direction in ("SHORT", "SELL"):
                    # Leader should revert down → SHORT aligns with convergence
                    return 6.0, f"PAIRS_CONVERGENCE {sym_a}/{sym_b} z={z_score:.1f} leader={symbol}"
        return 0.0, ""
    except Exception as _e:
        logger.debug(f"get_pairs_signal({symbol}): {_e}")
        return 0.0, ""


# ── Strategy 5: Time-of-Day RVOL ─────────────────────────────────────────────

_tod_history: Dict[str, Dict[str, List[int]]] = {}   # {symbol: {time_slot: [vols]}}
_TOD_MIN_SESSIONS = 3    # need at least 3 prior sessions for a meaningful comparison


def record_bar_volume(symbol: str, bar_time_str: str, volume: int) -> None:
    """Record a bar's volume for the given time slot. Call once per bar."""
    if not symbol or not bar_time_str:
        return
    try:
        slot = bar_time_str[:5]   # "HH:MM"
        if symbol not in _tod_history:
            _tod_history[symbol] = {}
        if slot not in _tod_history[symbol]:
            _tod_history[symbol][slot] = []
        hist = _tod_history[symbol][slot]
        hist.append(int(volume))
        if len(hist) > 30:   # keep 30 sessions max
            hist.pop(0)
    except Exception:
        pass


def get_tod_rvol_score(symbol: str, bar_time_str: str, current_volume: int, direction: str) -> Tuple[float, str]:
    """
    Compare current bar volume to same-time-slot historical average.
    9:35 AM bar vs average 9:35 AM bars from prior sessions.
    Returns (score_delta, reason).
    """
    try:
        if not symbol or not bar_time_str or current_volume <= 0:
            return 0.0, ""
        slot = bar_time_str[:5]
        hist = _tod_history.get(symbol, {}).get(slot, [])
        # Need prior sessions only (exclude today's — current_volume is today's)
        prior = hist[:-1] if len(hist) > 1 else hist
        if len(prior) < _TOD_MIN_SESSIONS:
            return 0.0, ""
        avg = float(np.mean(prior))
        if avg <= 0:
            return 0.0, ""
        ratio = current_volume / avg
        pct_rank = float(np.mean([1 if v <= current_volume else 0 for v in prior]))

        if pct_rank >= 0.95:
            return 12.0, f"TOD_RVOL_P95 {ratio:.1f}x slot={slot}"
        if pct_rank >= 0.85:
            return 7.0, f"TOD_RVOL_P85 {ratio:.1f}x slot={slot}"
        if pct_rank >= 0.75:
            return 3.0, f"TOD_RVOL_P75 {ratio:.1f}x slot={slot}"
        if pct_rank <= 0.40:
            return -4.0, f"TOD_RVOL_LOW p{pct_rank:.0%} {ratio:.1f}x slot={slot}"
        return 0.0, ""
    except Exception as _e:
        logger.debug(f"get_tod_rvol_score({symbol}): {_e}")
        return 0.0, ""


# ── Strategy 6: Sortino-Based Dynamic Sizing ──────────────────────────────────

_trade_returns: Dict[str, List[float]] = {}   # {symbol: [pnl_pct, ...]}
_MIN_SORTINO_SAMPLES = 3


def record_trade_result(symbol: str, pnl_pct: float) -> None:
    """Record trade outcome for per-symbol Sortino calculation."""
    if not symbol:
        return
    if symbol not in _trade_returns:
        _trade_returns[symbol] = []
    _trade_returns[symbol].append(float(pnl_pct))
    if len(_trade_returns[symbol]) > 100:
        _trade_returns[symbol].pop(0)


def get_sortino_size_multiplier(symbol: str) -> float:
    """
    Returns size multiplier (0.5–1.5) based on per-symbol Sortino ratio.
    Sortino > 2.0:  1.5x (high conviction — consistent winners)
    Sortino 1.0–2.0: 1.1x
    Sortino 0–1.0:   1.0x (neutral)
    Sortino < 0:     0.5x (net losing on this symbol)
    """
    try:
        returns = _trade_returns.get(symbol, [])
        if len(returns) < _MIN_SORTINO_SAMPLES:
            return 1.0
        arr = np.array(returns, dtype=float)
        mean_return = float(np.mean(arr))
        downside = arr[arr < 0]
        if len(downside) == 0:
            # All winners — max conviction
            return 1.5
        downside_std = float(np.std(downside))
        if downside_std <= 0:
            return 1.0
        sortino = mean_return / downside_std
        if sortino > 2.0:
            return 1.5
        if sortino >= 1.0:
            return 1.1
        if sortino >= 0:
            return 1.0
        return 0.5   # net losing symbol
    except Exception as _e:
        logger.debug(f"get_sortino_size_multiplier({symbol}): {_e}")
        return 1.0
