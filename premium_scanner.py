"""
premium_scanner.py — Free equivalents of expensive paid trading tools (v11.0)

6 modules that replicate costly paid services using only free data (yfinance):
  1. Short Squeeze Detector    — replaces Ortex $200/month
  2. Options Flow Analyzer     — replaces Unusual Whales $100/month
  3. Sector Rotation Ranker    — replaces Bloomberg sectors $2000/month
  4. Float Squeeze Filter      — replaces Finviz Elite $25/month
  5. Earnings Edge Calculator  — replaces Benzinga Pro $40/month
  6. Dark Pool Print Detector  — replaces Unusual Whales dark pool $100/month

All functions:
  - Return (score: float, reason: str) or (Optional[float], reason: str)
  - Return (0.0, "error") on any exception — NEVER raise
  - Use module-level dict caches with TTL timestamps
  - Import yfinance lazily to avoid startup slowdown
"""

import logging
import time as _time_mod
from datetime import datetime, timedelta, date
from typing import Optional, Tuple, Dict, Any

import pandas as pd

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# MODULE-LEVEL CACHES  {symbol: (value, timestamp)}
# ─────────────────────────────────────────────────────────────────────────────
_squeeze_cache:   Dict[str, Tuple[Any, float]] = {}   # 4-hour TTL
_options_cache:   Dict[str, Tuple[Any, float]] = {}   # 30-min TTL
_sector_cache:    Dict[str, Tuple[Any, float]] = {}   # 1-hour TTL (shared rankings)
_float_cache:     Dict[str, Tuple[Any, float]] = {}   # 4-hour TTL
_earnings_cache:  Dict[str, Tuple[Any, float]] = {}   # 6-hour TTL

# SPDR sector ETFs — 11 sectors
SPDR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLRE", "XLB", "XLC"]

# Mapping of ~100 common US stocks to their primary sector ETF
SYMBOL_SECTOR_MAP: Dict[str, str] = {
    # Technology (XLK)
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AMD": "XLK", "INTC": "XLK",
    "QCOM": "XLK", "AVGO": "XLK", "TXN": "XLK", "MU": "XLK", "LRCX": "XLK",
    "KLAC": "XLK", "AMAT": "XLK", "ASML": "XLK", "TSM": "XLK", "MCHP": "XLK",
    "MRVL": "XLK", "ON": "XLK", "ARM": "XLK", "SMCI": "XLK", "PLTR": "XLK",
    "CRWD": "XLK", "PANW": "XLK", "ZS": "XLK", "NET": "XLK", "DDOG": "XLK",
    "NOW": "XLK", "SNOW": "XLK", "TEAM": "XLK", "HUBS": "XLK", "OKTA": "XLK",
    "MDB": "XLK", "GTLB": "XLK", "U": "XLK", "AI": "XLK", "SOUN": "XLK",
    "BBAI": "XLK", "CRM": "XLK", "ORCL": "XLK", "IBM": "XLK", "ADBE": "XLK",
    "INTU": "XLK", "CSCO": "XLK", "HPQ": "XLK", "ACN": "XLK",
    # Communication Services (XLC)
    "GOOGL": "XLC", "GOOG": "XLC", "META": "XLC", "NFLX": "XLC", "RBLX": "XLC",
    "SNAP": "XLC", "PINS": "XLC", "TWTR": "XLC", "DIS": "XLC", "CMCSA": "XLC",
    "VZ": "XLC", "T": "XLC", "TMUS": "XLC", "CHTR": "XLC",
    # Consumer Discretionary (XLY)
    "AMZN": "XLY", "TSLA": "XLY", "UBER": "XLY", "SHOP": "XLY", "ABNB": "XLY",
    "MELI": "XLY", "LYFT": "XLY", "DASH": "XLY", "YELP": "XLY", "BKNG": "XLY",
    "EXPE": "XLY", "NKE": "XLY", "MCD": "XLY", "SBUX": "XLY",
    # Consumer Staples (XLP)
    "WMT": "XLP", "COST": "XLP", "PG": "XLP", "KO": "XLP", "PEP": "XLP",
    "PM": "XLP", "MDLZ": "XLP", "CL": "XLP",
    # Financials (XLF)
    "JPM": "XLF", "GS": "XLF", "MS": "XLF", "BAC": "XLF", "WFC": "XLF",
    "V": "XLF", "MA": "XLF", "PYPL": "XLF", "SQ": "XLF", "SOFI": "XLF",
    "HOOD": "XLF", "AFRM": "XLF", "COIN": "XLF", "MSTR": "XLF",
    # Energy (XLE)
    "XOM": "XLE", "CVX": "XLE", "OXY": "XLE", "SLB": "XLE", "MPC": "XLE",
    "PSX": "XLE", "VLO": "XLE", "COP": "XLE",
    # Healthcare (XLV)
    "LLY": "XLV", "NVO": "XLV", "MRNA": "XLV", "HIMS": "XLV", "VKTX": "XLV",
    "RXRX": "XLV", "JNJ": "XLV", "PFE": "XLV", "ABBV": "XLV", "BMY": "XLV",
    "MRK": "XLV", "UNH": "XLV", "CVS": "XLV",
    # Industrials (XLI)
    "GE": "XLI", "CAT": "XLI", "LMT": "XLI", "RTX": "XLI", "NOC": "XLI",
    "BA": "XLI", "HON": "XLI", "DE": "XLI", "UPS": "XLI", "FDX": "XLI",
    # Utilities (XLU)
    "NEE": "XLU", "DUK": "XLU", "SO": "XLU", "AEP": "XLU", "D": "XLU",
    # Real Estate (XLRE)
    "AMT": "XLRE", "PLD": "XLRE", "EQIX": "XLRE", "SPG": "XLRE",
    # Materials (XLB)
    "LIN": "XLB", "APD": "XLB", "SHW": "XLB", "FCX": "XLB", "NEM": "XLB",
    # EV / Clean energy (XLE or XLK, map to XLY)
    "RIVN": "XLY", "LCID": "XLY", "NIO": "XLY",
    "PLUG": "XLK", "FSLR": "XLK", "ENPH": "XLK",
    # Crypto-related (XLF)
    "MARA": "XLF", "RIOT": "XLF", "HUT": "XLF", "CLSK": "XLF",
    "BTBT": "XLF", "CIFR": "XLF",
    # Leveraged ETFs — map to their underlying sector
    "TQQQ": "XLK", "TECL": "XLK", "SOXL": "XLK",
    "SPXL": "XLF", "FNGU": "XLK",
    "SPY": "XLF", "QQQ": "XLK", "IWM": "XLF",
}


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 1: Short Squeeze Detector  (replaces Ortex $200/month)
# ─────────────────────────────────────────────────────────────────────────────

def get_short_squeeze_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Detect short squeeze potential using yfinance short-float data.

    Returns (score_delta, reason).
    Positive score = bullish squeeze fuel (LONG).
    Negative score = dangerous for SHORT entries.
    Returns (0.0, "error") on any failure.
    """
    try:
        now = _time_mod.time()
        cache_key = f"{symbol}_{direction}"
        if cache_key in _squeeze_cache:
            cached_val, cached_ts = _squeeze_cache[cache_key]
            if now - cached_ts < 4 * 3600:   # 4-hour TTL
                return cached_val

        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}

        float_short_pct = float(info.get("shortPercentOfFloat") or 0.0)
        short_ratio     = float(info.get("shortRatio") or 0.0)

        if float_short_pct == 0.0:
            result = (0.0, "no short data available")
            _squeeze_cache[cache_key] = (result, now)
            return result

        # Score LONG direction — high short float = potential squeeze fuel
        if direction == "LONG":
            if float_short_pct > 0.20:
                score = 10.0
                reason = f"squeeze fuel: {float_short_pct:.0%} short float (HIGH, ratio={short_ratio:.1f}x)"
            elif float_short_pct > 0.15:
                score = 6.0
                reason = f"squeeze potential: {float_short_pct:.0%} short float (ELEVATED)"
            elif float_short_pct > 0.10:
                score = 3.0
                reason = f"modest squeeze setup: {float_short_pct:.0%} short float"
            else:
                score = 0.0
                reason = f"low short float {float_short_pct:.0%} — minimal squeeze risk"
        elif direction == "SHORT":
            # Shorting into a heavily shorted stock risks a violent squeeze
            if float_short_pct > 0.20:
                score = -8.0
                reason = f"SQUEEZE DANGER: {float_short_pct:.0%} short float — shorting vs crowd"
            elif float_short_pct > 0.15:
                score = -5.0
                reason = f"elevated short risk: {float_short_pct:.0%} float short"
            elif float_short_pct > 0.10:
                score = -2.0
                reason = f"mild short crowding: {float_short_pct:.0%} short float"
            else:
                score = 0.0
                reason = f"short float {float_short_pct:.0%} — safe to short"
        else:
            score = 0.0
            reason = f"unknown direction {direction}"

        result = (score, reason)
        _squeeze_cache[cache_key] = (result, now)
        return result

    except Exception as e:
        logger.debug(f"[suppressed] get_short_squeeze_score({symbol}): {e}")
        return (0.0, "error")


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 2: Options Flow Analyzer  (replaces Unusual Whales $100/month)
# ─────────────────────────────────────────────────────────────────────────────

def get_options_flow_score(symbol: str, direction: str) -> Tuple[Optional[float], str]:
    """
    Detect unusual options flow (institutional call/put sweeps) via yfinance.

    Returns (score_delta, reason).
    Returns (None, reason) only if data is completely unavailable.
    Returns (0.0, "error") on exception.
    """
    try:
        now = _time_mod.time()
        cache_key = f"{symbol}_{direction}"
        if cache_key in _options_cache:
            cached_val, cached_ts = _options_cache[cache_key]
            if now - cached_ts < 30 * 60:   # 30-minute TTL
                return cached_val

        import yfinance as yf
        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            result = (0.0, "no options data available")
            _options_cache[cache_key] = (result, now)
            return result

        # Use nearest expiry for most time-sensitive institutional signal
        nearest_exp = expirations[0]
        chain = ticker.option_chain(nearest_exp)
        calls = chain.calls
        puts  = chain.puts

        if calls is None or calls.empty or puts is None or puts.empty:
            result = (0.0, "empty options chain")
            _options_cache[cache_key] = (result, now)
            return result

        # Put/Call ratio by total volume
        total_call_vol = float(calls["volume"].fillna(0).sum())
        total_put_vol  = float(puts["volume"].fillna(0).sum())

        if total_call_vol + total_put_vol == 0:
            result = (0.0, "zero options volume")
            _options_cache[cache_key] = (result, now)
            return result

        pc_ratio = total_put_vol / max(total_call_vol, 1.0)

        # Unusual call sweep: near-term call volume > 3x call OI = bullish institutional bet
        call_vol_series = calls["volume"].fillna(0)
        call_oi_series  = calls["openInterest"].fillna(0)
        bullish_sweep = bool(
            (call_oi_series > 0).any()
            and (call_vol_series / call_oi_series.replace(0, float("nan"))).max() > 3.0
        )

        # Unusual put sweep: near-term put volume > 3x put OI = bearish institutional hedge
        put_vol_series = puts["volume"].fillna(0)
        put_oi_series  = puts["openInterest"].fillna(0)
        bearish_sweep = bool(
            (put_oi_series > 0).any()
            and (put_vol_series / put_oi_series.replace(0, float("nan"))).max() > 3.0
        )

        score = 0.0
        reason_parts = [f"P/C={pc_ratio:.2f}"]

        if direction == "LONG":
            if bullish_sweep:
                score += 10.0
                reason_parts.append("BULLISH SWEEP: call vol>3x OI (institutional buying)")
            if pc_ratio < 0.5:
                score += 8.0
                reason_parts.append(f"bullish sentiment: P/C={pc_ratio:.2f}<0.50")
            elif pc_ratio > 2.0:
                score -= 6.0
                reason_parts.append(f"extreme put hedging: P/C={pc_ratio:.2f}>2.0")
            if bearish_sweep:
                score -= 4.0
                reason_parts.append("bearish put sweep detected")
        elif direction == "SHORT":
            if bearish_sweep:
                score += 8.0
                reason_parts.append("BEARISH SWEEP: put vol>3x OI (institutional hedging)")
            if pc_ratio > 2.0:
                score += 6.0
                reason_parts.append(f"extreme put hedging: P/C={pc_ratio:.2f}>2.0")
            elif pc_ratio < 0.5:
                score -= 8.0
                reason_parts.append(f"bullish call sentiment: P/C={pc_ratio:.2f}<0.50")
            if bullish_sweep:
                score -= 4.0
                reason_parts.append("bullish call sweep vs SHORT entry")

        result = (score, " | ".join(reason_parts))
        _options_cache[cache_key] = (result, now)
        return result

    except Exception as e:
        logger.debug(f"[suppressed] get_options_flow_score({symbol}): {e}")
        return (0.0, "error")


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 3: Sector Rotation Ranker  (replaces Bloomberg sectors $2000/month)
# ─────────────────────────────────────────────────────────────────────────────

def _get_sector_rankings() -> Dict[str, int]:
    """
    Fetch 5-day returns for all 11 SPDR ETFs and rank them 1 (best) to 11 (worst).
    Cached 1 hour (shared across all symbols).
    """
    now = _time_mod.time()
    cache_key = "__sector_rankings__"
    if cache_key in _sector_cache:
        cached_val, cached_ts = _sector_cache[cache_key]
        if now - cached_ts < 3600:  # 1-hour TTL
            return cached_val

    try:
        import yfinance as yf
        data = yf.download(
            " ".join(SPDR_ETFS),
            period="7d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        if data is None or data.empty:
            return {}

        if "Close" in data.columns:
            close_df = data["Close"]
        else:
            try:
                close_df = data.xs("Close", axis=1, level=0)
            except Exception:
                close_df = data

        returns = {}
        for etf in SPDR_ETFS:
            if etf in close_df.columns:
                series = close_df[etf].dropna()
                if len(series) >= 2:
                    returns[etf] = float((series.iloc[-1] / series.iloc[0]) - 1.0)

        # Rank: 1 = best performance (highest return)
        sorted_etfs = sorted(returns.keys(), key=lambda e: returns[e], reverse=True)
        rankings = {etf: rank + 1 for rank, etf in enumerate(sorted_etfs)}

        _sector_cache[cache_key] = (rankings, now)
        return rankings

    except Exception as e:
        logger.debug(f"[suppressed] _get_sector_rankings: {e}")
        return {}


def get_sector_rotation_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Score based on the symbol's sector ETF 5-day momentum rank.

    Rank 1-2  → +7 (top performers — institutional money flowing in)
    Rank 3-4  → +4 (above average)
    Rank 5-7  → 0  (neutral middle)
    Rank 8-11 → -5 (underperforming sector — fight the flow)

    Returns (0.0, "error") on failure.
    """
    try:
        sector_etf = SYMBOL_SECTOR_MAP.get(symbol.upper())
        if not sector_etf:
            return (0.0, f"sector unknown for {symbol}")

        rankings = _get_sector_rankings()
        if not rankings:
            return (0.0, "sector rankings unavailable")

        rank = rankings.get(sector_etf, 6)   # default neutral if ETF not ranked

        if rank <= 2:
            base_score = 7.0
            label = f"TOP sector: {sector_etf} rank #{rank}/11"
        elif rank <= 4:
            base_score = 4.0
            label = f"above-avg sector: {sector_etf} rank #{rank}/11"
        elif rank <= 7:
            base_score = 0.0
            label = f"neutral sector: {sector_etf} rank #{rank}/11"
        else:
            base_score = -5.0
            label = f"WEAK sector: {sector_etf} rank #{rank}/11"

        # For SHORT: trading with a weak sector on a short is actually good
        if direction == "SHORT":
            score = -base_score
            if score > 0:
                label = label + " (SHORT favoured in weak sector)"
            elif score < 0:
                label = label + " (SHORT into top sector — risky)"
        else:
            score = base_score

        return (score, label)

    except Exception as e:
        logger.debug(f"[suppressed] get_sector_rotation_score({symbol}): {e}")
        return (0.0, "error")


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 4: Float Squeeze Filter  (replaces Finviz Elite $25/month)
# ─────────────────────────────────────────────────────────────────────────────

def get_float_squeeze_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Score based on float size and float turnover (volume as % of float).
    Small float + high volume = rocket fuel.

    Returns (score_delta, reason).
    Returns (0.0, "error") on failure.
    """
    try:
        now = _time_mod.time()
        cache_key = f"{symbol}_{direction}"
        if cache_key in _float_cache:
            cached_val, cached_ts = _float_cache[cache_key]
            if now - cached_ts < 4 * 3600:   # 4-hour TTL
                return cached_val

        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info   = ticker.info or {}

        float_shares   = float(info.get("floatShares") or 0)
        avg_volume     = float(info.get("averageVolume") or info.get("averageVolume10days") or 0)
        today_volume   = float(info.get("volume") or info.get("regularMarketVolume") or 0)

        if float_shares <= 0:
            result = (0.0, "float data unavailable")
            _float_cache[cache_key] = (result, now)
            return result

        vol_for_turnover = today_volume if today_volume > 0 else avg_volume
        float_turnover = vol_for_turnover / float_shares if float_shares > 0 else 0.0
        float_m = float_shares / 1_000_000  # in millions

        if direction == "LONG":
            if float_shares < 20_000_000 and float_turnover > 0.30:
                score = 8.0
                reason = f"ROCKET FUEL: float={float_m:.1f}M, turnover={float_turnover:.1%}"
            elif float_shares < 50_000_000 and float_turnover > 0.20:
                score = 5.0
                reason = f"low float momentum: float={float_m:.1f}M, turnover={float_turnover:.1%}"
            elif float_shares < 100_000_000:
                score = 2.0
                reason = f"manageable float: {float_m:.1f}M shares"
            elif float_shares > 1_000_000_000:
                score = -2.0
                reason = f"very large float: {float_m:.0f}M — momentum unlikely"
            else:
                score = 0.0
                reason = f"normal float: {float_m:.0f}M shares"
        else:
            # For SHORT, large float = easier to short (liquidity), small float = risky
            if float_shares < 20_000_000 and float_turnover > 0.30:
                score = -5.0
                reason = f"DANGEROUS SHORT: tiny float={float_m:.1f}M + hot turnover — squeeze risk"
            elif float_shares < 50_000_000:
                score = -2.0
                reason = f"small float SHORT: {float_m:.1f}M — squeeze risk elevated"
            else:
                score = 0.0
                reason = f"float={float_m:.0f}M — safe for SHORT"

        result = (score, reason)
        _float_cache[cache_key] = (result, now)
        return result

    except Exception as e:
        logger.debug(f"[suppressed] get_float_squeeze_score({symbol}): {e}")
        return (0.0, "error")


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 5: Earnings Edge Calculator  (replaces Benzinga Pro $40/month)
# ─────────────────────────────────────────────────────────────────────────────

def get_earnings_edge_score(symbol: str) -> Tuple[Optional[float], str]:
    """
    Score based on proximity to the next earnings date.

    Returns (None, reason)    ONLY if earnings is TOMORROW (too risky — skip trade).
    Returns (score, reason)   in all other cases.
    Returns (0.0, "error")    on exception.

    Pre-earnings window (2-4 days before) → +6 (pre-earnings run common)
    Post-earnings breakout (1-3 days after gap up > 3%) → +8
    Day of earnings → 0.0 neutral (do NOT block)
    Tomorrow → None (hard skip)
    >5 days from earnings → 0.0
    """
    try:
        now_ts = _time_mod.time()
        if symbol in _earnings_cache:
            cached_val, cached_ts = _earnings_cache[symbol]
            if now_ts - cached_ts < 6 * 3600:   # 6-hour TTL
                return cached_val

        import yfinance as yf
        ticker = yf.Ticker(symbol)
        cal    = ticker.calendar

        earnings_date = None

        if cal is not None:
            # calendar may be a DataFrame or dict depending on yfinance version
            if hasattr(cal, "empty") and not cal.empty:
                try:
                    if hasattr(cal, "index") and "Earnings Date" in cal.index:
                        ed_val = cal.loc["Earnings Date"].iloc[0]
                    else:
                        ed_val = cal.iloc[0, 0]
                    if hasattr(ed_val, "date"):
                        earnings_date = ed_val.date()
                    elif isinstance(ed_val, date):
                        earnings_date = ed_val
                except Exception:
                    pass

            if earnings_date is None and hasattr(cal, "get"):
                ed_raw = cal.get("Earnings Date") or cal.get("earningsDate")
                if ed_raw is not None:
                    if hasattr(ed_raw, "__iter__") and not isinstance(ed_raw, str):
                        for item in ed_raw:
                            if hasattr(item, "date"):
                                earnings_date = item.date()
                                break
                            elif isinstance(item, (date, datetime)):
                                earnings_date = item if isinstance(item, date) else item.date()
                                break
                    elif hasattr(ed_raw, "date"):
                        earnings_date = ed_raw.date()

        if earnings_date is None:
            result = (0.0, "earnings date unknown — no catalyst")
            _earnings_cache[symbol] = (result, now_ts)
            return result

        today = datetime.utcnow().date()
        days_diff = (earnings_date - today).days   # positive = future, negative = past

        if days_diff == 1:
            # Earnings TOMORROW — too risky (gap risk, binary outcome)
            result = (None, f"earnings TOMORROW ({earnings_date}) — skip to avoid gap risk")
            _earnings_cache[symbol] = (result, now_ts)
            return result

        elif days_diff == 0:
            result = (0.0, f"earnings TODAY ({earnings_date}) — neutral, monitoring")
            _earnings_cache[symbol] = (result, now_ts)
            return result

        elif 2 <= days_diff <= 4:
            # Pre-earnings run — stocks often drift up into report
            result = (6.0, f"pre-earnings run window: {days_diff}d before earnings ({earnings_date})")
            _earnings_cache[symbol] = (result, now_ts)
            return result

        elif -3 <= days_diff <= -1:
            # Post-earnings: check if there was a gap up
            try:
                hist = ticker.history(period="5d", interval="1d", auto_adjust=True)
                if len(hist) >= 2:
                    hist_dates = [d.date() if hasattr(d, "date") else d for d in hist.index]
                    earnings_idx = None
                    for i, d in enumerate(hist_dates):
                        if d == earnings_date:
                            earnings_idx = i
                            break
                    if earnings_idx is not None and earnings_idx > 0:
                        prev_close = float(hist["Close"].iloc[earnings_idx - 1])
                        earnings_open = float(hist["Open"].iloc[earnings_idx])
                        gap_pct = (earnings_open - prev_close) / prev_close * 100
                        if gap_pct > 3.0:
                            result = (8.0, f"post-earnings gap-up {gap_pct:+.1f}% — breakout continuation")
                            _earnings_cache[symbol] = (result, now_ts)
                            return result
            except Exception:
                pass
            result = (0.0, f"post-earnings ({abs(days_diff)}d after) — no gap catalyst")
            _earnings_cache[symbol] = (result, now_ts)
            return result

        else:
            result = (0.0, f"earnings in {days_diff}d ({earnings_date}) — no edge window")
            _earnings_cache[symbol] = (result, now_ts)
            return result

    except Exception as e:
        logger.debug(f"[suppressed] get_earnings_edge_score({symbol}): {e}")
        return (0.0, "error")


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 6: Dark Pool Print Detector  (replaces Unusual Whales $100/month)
# ─────────────────────────────────────────────────────────────────────────────

def get_dark_pool_score(
    symbol: str, df_5m: pd.DataFrame, direction: str
) -> Tuple[float, str]:
    """
    Detect dark pool / institutional block trade signatures from 5-min candle data.

    Dark pool accumulation signature:
      Any bar in last 5 bars where:
        volume > 5x rolling_20_bar_avg_volume  AND  |price_change_pct| < 0.3%
      (huge volume, tiny price move = institutional quietly absorbing supply)

    Dark pool distribution signature:
      Any bar in last 5 bars where:
        volume > 5x rolling_20_bar_avg_volume  AND  price drops > 0.3%

    No caching needed — uses in-memory df_5m (already loaded).
    Returns (0.0, "error") on failure.
    """
    try:
        if df_5m is None or df_5m.empty or len(df_5m) < 21:
            return (0.0, "insufficient bars for dark pool analysis")

        # Normalise column names to lowercase
        df = df_5m.copy()
        df.columns = [c.lower() for c in df.columns]

        if "volume" not in df.columns:
            return (0.0, "no volume data")
        if "close" not in df.columns or "open" not in df.columns:
            return (0.0, "no price data")

        vol   = df["volume"].astype(float)
        close = df["close"].astype(float)
        open_ = df["open"].astype(float)

        # Rolling 20-bar average volume (exclude current bar from the look-back)
        rolling_avg = vol.rolling(window=20, min_periods=10).mean().shift(1)

        # Price change % per bar (absolute and directional)
        price_change_pct    = ((close - open_) / open_.replace(0, float("nan")) * 100).abs()
        price_direction_pct = (close - open_) / open_.replace(0, float("nan")) * 100

        # Analyse last 5 bars
        last_n = 5
        tail_start = max(0, len(df) - last_n)
        tail_vol   = vol.iloc[tail_start:]
        tail_avg   = rolling_avg.iloc[tail_start:]
        tail_pc    = price_change_pct.iloc[tail_start:]
        tail_pd    = price_direction_pct.iloc[tail_start:]

        accumulation_detected = False
        distribution_detected = False

        for i in range(len(tail_vol)):
            avg = tail_avg.iloc[i]
            if pd.isna(avg) or avg <= 0:
                continue
            vol_ratio_bar = tail_vol.iloc[i] / avg
            if vol_ratio_bar >= 5.0:
                pc    = tail_pc.iloc[i]
                pd_val = tail_pd.iloc[i]
                if pc < 0.3:
                    # Huge volume, tiny price move = institutional absorption
                    accumulation_detected = True
                elif pd_val < -0.3:
                    # Huge volume, price drops = distribution
                    distribution_detected = True

        if direction == "LONG":
            if accumulation_detected:
                return (9.0, "DARK POOL ACCUMULATION: 5x+ volume + price flat — institutional buying")
            if distribution_detected:
                return (-7.0, "DARK POOL DISTRIBUTION: 5x+ volume + price drop — institutional selling")
        elif direction == "SHORT":
            if distribution_detected:
                return (9.0, "DARK POOL DISTRIBUTION: 5x+ volume + price drop — institutional selling")
            if accumulation_detected:
                return (-5.0, "DARK POOL ACCUMULATION: institutional buying vs SHORT — risky")

        return (0.0, "no dark pool signature detected in last 5 bars")

    except Exception as e:
        logger.debug(f"[suppressed] get_dark_pool_score({symbol}): {e}")
        return (0.0, "error")
