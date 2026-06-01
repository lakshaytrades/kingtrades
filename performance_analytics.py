"""
performance_analytics.py — Institutional-grade performance metrics

Calculates R-multiples, Sharpe, Sortino, Calmar, profit factor,
sector attribution, and compounding progress for top-1% trading analytics.
All metrics calculated from SQLite trade journal (logs/trades/trade_journal.db).
Fail-open — returns empty/zero metrics if journal missing.
"""

import logging
import math
import sqlite3
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── DB path (mirrors trade_journal.py constant) ──────────────────────────────
DB_PATH = Path("logs/trades/trade_journal.db")

# ── Module-level 5-minute cache {cache_key: (timestamp, value)} ──────────────
_CACHE: Dict[str, Any] = {}
_CACHE_TTL = 300  # seconds


def _cache_get(key: str) -> Optional[Any]:
    entry = _CACHE.get(key)
    if entry and (time.monotonic() - entry[0]) < _CACHE_TTL:
        return entry[1]
    return None


def _cache_set(key: str, value: Any) -> None:
    _CACHE[key] = (time.monotonic(), value)


def _load_trades(days: int) -> "pd.DataFrame":
    """Load closed trades from the SQLite journal for the last *days* days.
    Returns an empty DataFrame (with expected columns) when the DB is missing
    or has no data, so callers never see a crash.
    """
    import pandas as pd

    empty = pd.DataFrame(columns=[
        "trade_id", "symbol", "date_ist", "direction",
        "entry_price", "entry_qty", "entry_time_ist", "entry_pattern",
        "stop_loss", "exit_price", "exit_qty", "exit_time_ist",
        "pnl", "net_pnl", "pnl_pct", "outcome", "hold_minutes",
        "signal_score", "regime", "rr_ratio", "atr_at_entry",
    ])

    if not DB_PATH.exists():
        return empty

    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        conn = sqlite3.connect(str(DB_PATH))
        df = pd.read_sql_query(
            "SELECT * FROM trades WHERE date_ist >= ? ORDER BY date_ist ASC",
            conn, params=(cutoff,)
        )
        conn.close()
        # Only include closed trades (exit_price present and non-zero)
        if "exit_price" in df.columns:
            df = df[df["exit_price"].astype(float) > 0].copy()
        return df if not df.empty else empty
    except Exception as exc:
        logger.debug(f"[performance_analytics] _load_trades error: {exc}")
        return empty


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def get_r_multiple_stats(days: int = 30) -> dict:
    """
    R-multiple = actual P&L / initial risk (stop loss distance * qty)

    Returns:
        avg_r            -- average R earned per trade
        expectancy       -- avg_win_r * win_rate - avg_loss_r * loss_rate
        best_r           -- best single trade R-multiple
        worst_r          -- worst single trade R-multiple (negative)
        r_distribution   -- list of R values for distribution analysis
        positive_r_count -- trades > 1R
        two_r_count      -- trades > 2R (target hit)
    """
    cache_key = f"r_multiple_{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    import pandas as pd

    default = {
        "avg_r": 0.0, "expectancy": 0.0, "best_r": 0.0, "worst_r": 0.0,
        "r_distribution": [], "positive_r_count": 0, "two_r_count": 0,
    }

    try:
        df = _load_trades(days)
        if df.empty:
            _cache_set(cache_key, default)
            return default

        df["entry_price"] = pd.to_numeric(df["entry_price"], errors="coerce").fillna(0.0)
        df["stop_loss"]   = pd.to_numeric(df["stop_loss"],   errors="coerce").fillna(0.0)
        df["entry_qty"]   = pd.to_numeric(df["entry_qty"],   errors="coerce").fillna(1.0)
        df["net_pnl"]     = pd.to_numeric(df["net_pnl"],     errors="coerce").fillna(0.0)

        r_values: List[float] = []
        for _, row in df.iterrows():
            ep = float(row["entry_price"])
            sl = float(row["stop_loss"])
            qty = max(float(row["entry_qty"]), 1.0)
            pnl = float(row["net_pnl"])

            # Initial risk in $ per share; fallback to 1% of entry value
            risk_per_share = abs(ep - sl) if sl > 0 and abs(ep - sl) > 1e-6 else ep * 0.01
            initial_risk = risk_per_share * qty

            if initial_risk <= 0:
                initial_risk = max(abs(pnl), ep * 0.01 * qty, 1.0)

            r_values.append(pnl / initial_risk)

        if not r_values:
            _cache_set(cache_key, default)
            return default

        import numpy as np
        r_arr = np.array(r_values)
        wins   = r_arr[r_arr > 0]
        losses = r_arr[r_arr <= 0]

        win_rate   = len(wins) / len(r_arr)
        loss_rate  = 1.0 - win_rate
        avg_win_r  = float(wins.mean())        if len(wins)   > 0 else 0.0
        avg_loss_r = float(abs(losses.mean())) if len(losses) > 0 else 0.0
        expectancy = avg_win_r * win_rate - avg_loss_r * loss_rate

        result = {
            "avg_r":            round(float(r_arr.mean()), 4),
            "expectancy":       round(expectancy, 4),
            "best_r":           round(float(r_arr.max()), 4),
            "worst_r":          round(float(r_arr.min()), 4),
            "r_distribution":   [round(v, 4) for v in r_values],
            "positive_r_count": int((r_arr > 1.0).sum()),
            "two_r_count":      int((r_arr > 2.0).sum()),
        }
        _cache_set(cache_key, result)
        return result
    except Exception as exc:
        logger.debug(f"[performance_analytics] get_r_multiple_stats error: {exc}")
        _cache_set(cache_key, default)
        return default


def get_profit_metrics(days: int = 30) -> dict:
    """
    Returns profit/loss statistics:
        profit_factor       -- gross_wins / gross_losses (>1.5 good, >2 excellent)
        win_rate            -- 0-1
        avg_win_pct         -- average win as % of entry price
        avg_loss_pct        -- average loss as %
        win_loss_ratio      -- avg_win / avg_loss
        largest_win         -- single largest win $
        largest_loss        -- single largest loss $
        consecutive_wins    -- current streak
        consecutive_losses  -- current streak
        avg_hold_minutes    -- average hold time
    """
    cache_key = f"profit_metrics_{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    import pandas as pd

    default = {
        "profit_factor": 0.0, "win_rate": 0.0, "avg_win_pct": 0.0,
        "avg_loss_pct": 0.0, "win_loss_ratio": 0.0,
        "largest_win": 0.0, "largest_loss": 0.0,
        "consecutive_wins": 0, "consecutive_losses": 0,
        "avg_hold_minutes": 0.0,
    }

    try:
        df = _load_trades(days)
        if df.empty:
            _cache_set(cache_key, default)
            return default

        df["net_pnl"]      = pd.to_numeric(df["net_pnl"],      errors="coerce").fillna(0.0)
        df["pnl_pct"]      = pd.to_numeric(df["pnl_pct"],      errors="coerce").fillna(0.0)
        df["hold_minutes"] = pd.to_numeric(df["hold_minutes"],  errors="coerce").fillna(0.0)
        df["entry_price"]  = pd.to_numeric(df["entry_price"],   errors="coerce").fillna(0.0)

        wins   = df[df["net_pnl"] > 0]["net_pnl"]
        losses = df[df["net_pnl"] <= 0]["net_pnl"]

        gross_wins    = float(wins.sum())        if len(wins)   > 0 else 0.0
        gross_losses  = float(abs(losses.sum())) if len(losses) > 0 else 1.0
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else (gross_wins if gross_wins > 0 else 0.0)

        n        = len(df)
        win_rate = len(wins) / n if n > 0 else 0.0

        avg_win_pct  = float(df.loc[df["net_pnl"] > 0,   "pnl_pct"].mean()) if len(wins)   > 0 else 0.0
        avg_loss_pct = float(abs(df.loc[df["net_pnl"] <= 0, "pnl_pct"].mean())) if len(losses) > 0 else 0.0
        avg_win_abs  = float(wins.mean())        if len(wins)   > 0 else 0.0
        avg_loss_abs = float(abs(losses.mean())) if len(losses) > 0 else 1.0
        win_loss_ratio = avg_win_abs / avg_loss_abs if avg_loss_abs > 0 else avg_win_abs

        # Current consecutive streaks (scan from most recent trade)
        outcomes = df.sort_values("entry_time_ist")["net_pnl"].tolist()
        cons_wins = cons_losses = 0
        if outcomes:
            last_sign = 1 if outcomes[-1] > 0 else -1
            for pnl in reversed(outcomes):
                sign = 1 if pnl > 0 else -1
                if sign == last_sign:
                    if last_sign == 1:
                        cons_wins += 1
                    else:
                        cons_losses += 1
                else:
                    break

        result = {
            "profit_factor":      round(profit_factor, 4),
            "win_rate":           round(win_rate, 4),
            "avg_win_pct":        round(avg_win_pct, 4),
            "avg_loss_pct":       round(avg_loss_pct, 4),
            "win_loss_ratio":     round(win_loss_ratio, 4),
            "largest_win":        round(float(wins.max())   if len(wins)   > 0 else 0.0, 2),
            "largest_loss":       round(float(losses.min()) if len(losses) > 0 else 0.0, 2),
            "consecutive_wins":   cons_wins,
            "consecutive_losses": cons_losses,
            "avg_hold_minutes":   round(float(df["hold_minutes"].mean()), 2),
        }
        _cache_set(cache_key, result)
        return result
    except Exception as exc:
        logger.debug(f"[performance_analytics] get_profit_metrics error: {exc}")
        _cache_set(cache_key, default)
        return default


def get_risk_adjusted_metrics(days: int = 30) -> dict:
    """
    Calculate daily Sharpe, Sortino, and Calmar ratios from trade data.

    Returns:
        daily_sharpe         -- (mean_daily_ret) / std_daily_ret * sqrt(252)
        daily_sortino        -- (mean_daily_ret) / downside_std * sqrt(252)
        calmar               -- annualized_return / max_drawdown
        max_drawdown_pct     -- maximum peak-to-trough drawdown %
        max_drawdown_days    -- days in max drawdown
        current_drawdown_pct -- current drawdown from peak
        var_95               -- 95% Value-at-Risk (1-day, historical)
        avg_daily_pnl        -- average daily P&L $
        best_day             -- best single day P&L $
        worst_day            -- worst single day P&L $
    """
    cache_key = f"risk_adjusted_{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    import pandas as pd
    import numpy as np

    default = {
        "daily_sharpe": 0.0, "daily_sortino": 0.0, "calmar": 0.0,
        "max_drawdown_pct": 0.0, "max_drawdown_days": 0,
        "current_drawdown_pct": 0.0, "var_95": 0.0,
        "avg_daily_pnl": 0.0, "best_day": 0.0, "worst_day": 0.0,
    }

    try:
        df = _load_trades(days)
        if df.empty:
            _cache_set(cache_key, default)
            return default

        df["net_pnl"]  = pd.to_numeric(df["net_pnl"],  errors="coerce").fillna(0.0)
        df["date_ist"] = pd.to_datetime(df["date_ist"], errors="coerce")

        daily_pnl = df.groupby(df["date_ist"].dt.date)["net_pnl"].sum()
        if len(daily_pnl) < 2:
            _cache_set(cache_key, default)
            return default

        daily_arr = daily_pnl.values.astype(float)
        mean_d    = float(daily_arr.mean())
        std_d     = float(daily_arr.std(ddof=1)) if len(daily_arr) > 1 else 1e-9

        # Sharpe (risk-free = 0 for intraday)
        sharpe = (mean_d / std_d) * math.sqrt(252) if std_d > 0 else 0.0

        # Sortino (downside std only)
        downside = daily_arr[daily_arr < 0]
        down_std = (
            float(np.std(downside, ddof=1)) if len(downside) > 1
            else (float(abs(downside[0])) if len(downside) == 1 else 1e-9)
        )
        sortino = (mean_d / down_std) * math.sqrt(252) if down_std > 0 else 0.0

        # Drawdown on cumulative P&L curve
        cumulative  = np.cumsum(daily_arr)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns   = cumulative - running_max  # always <= 0
        max_dd_abs  = float(abs(drawdowns.min())) if len(drawdowns) > 0 else 0.0
        peak_val    = float(running_max[np.argmin(drawdowns)]) if len(drawdowns) > 0 else 0.0
        max_dd_pct  = (max_dd_abs / peak_val * 100.0) if peak_val > 0 else 0.0

        # Max drawdown duration
        in_dd = False
        dd_start = 0
        max_dd_days = 0
        for i, dd in enumerate(drawdowns):
            if dd < 0:
                if not in_dd:
                    dd_start = i
                    in_dd = True
                max_dd_days = max(max_dd_days, i - dd_start + 1)
            else:
                in_dd = False

        # Current drawdown
        curr_dd_pct = 0.0
        if len(cumulative) > 0:
            curr_peak = float(running_max[-1])
            curr_val  = float(cumulative[-1])
            if curr_peak > 0 and curr_val < curr_peak:
                curr_dd_pct = (curr_peak - curr_val) / curr_peak * 100.0

        # Calmar
        ann_return = mean_d * 252
        calmar     = ann_return / max_dd_abs if max_dd_abs > 0 else 0.0

        # VaR 95% (historical simulation)
        var_95 = float(np.percentile(daily_arr, 5)) if len(daily_arr) >= 20 else float(daily_arr.min())

        result = {
            "daily_sharpe":         round(sharpe, 4),
            "daily_sortino":        round(sortino, 4),
            "calmar":               round(calmar, 4),
            "max_drawdown_pct":     round(max_dd_pct, 4),
            "max_drawdown_days":    int(max_dd_days),
            "current_drawdown_pct": round(curr_dd_pct, 4),
            "var_95":               round(var_95, 2),
            "avg_daily_pnl":        round(mean_d, 2),
            "best_day":             round(float(daily_arr.max()), 2),
            "worst_day":            round(float(daily_arr.min()), 2),
        }
        _cache_set(cache_key, result)
        return result
    except Exception as exc:
        logger.debug(f"[performance_analytics] get_risk_adjusted_metrics error: {exc}")
        _cache_set(cache_key, default)
        return default


def get_pattern_attribution(days: int = 30) -> list:
    """
    Returns list of dicts sorted by total_pnl descending:
    [{'pattern': str, 'trades': int, 'win_rate': float,
      'avg_pnl': float, 'total_pnl': float, 'avg_r': float}, ...]
    """
    cache_key = f"pattern_attribution_{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    import pandas as pd

    try:
        df = _load_trades(days)
        if df.empty or "entry_pattern" not in df.columns:
            _cache_set(cache_key, [])
            return []

        df["net_pnl"] = pd.to_numeric(df["net_pnl"], errors="coerce").fillna(0.0)

        # Get R values per trade for avg_r
        r_stats = get_r_multiple_stats(days)
        r_dist  = r_stats.get("r_distribution", [])
        r_map: Dict[int, float] = {i: v for i, v in enumerate(r_dist)}

        results = []
        df = df.reset_index(drop=True)
        df["_idx"] = df.index
        for pattern, grp in df.groupby("entry_pattern"):
            if not pattern or pd.isna(pattern):
                pattern = "UNKNOWN"
            n         = len(grp)
            wins      = (grp["net_pnl"] > 0).sum()
            total_pnl = float(grp["net_pnl"].sum())
            avg_pnl   = float(grp["net_pnl"].mean())
            win_rate  = wins / n if n > 0 else 0.0
            indices   = grp["_idx"].tolist()
            r_vals    = [r_map[i] for i in indices if i in r_map]
            avg_r     = sum(r_vals) / len(r_vals) if r_vals else 0.0

            results.append({
                "pattern":   str(pattern),
                "trades":    int(n),
                "win_rate":  round(win_rate, 4),
                "avg_pnl":   round(avg_pnl,  2),
                "total_pnl": round(total_pnl, 2),
                "avg_r":     round(avg_r,    4),
            })

        results.sort(key=lambda x: x["total_pnl"], reverse=True)
        _cache_set(cache_key, results)
        return results
    except Exception as exc:
        logger.debug(f"[performance_analytics] get_pattern_attribution error: {exc}")
        _cache_set(cache_key, [])
        return []


def get_sector_attribution(days: int = 30) -> list:
    """
    Group trades by sector (from SECTOR_MAP in watchlist_manager.py).
    Returns list sorted by total_pnl:
    [{'sector': str, 'etf': str, 'trades': int, 'win_rate': float,
      'total_pnl': float, 'avg_pnl': float}, ...]
    """
    cache_key = f"sector_attribution_{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    import pandas as pd

    # Sector ETF mapping
    SECTOR_ETF = {
        "TECH": "XLK", "AI_SEMI": "SOXX", "FINTECH": "XLF",
        "BANKS": "KBE", "ENERGY": "XLE", "HEALTHCARE": "XLV",
        "CONSUMER": "XLY", "DEFENSE": "ITA", "ETF": "SPY",
        "HIGH_BETA": "IWM", "OTHER": "",
    }

    sym_sector: Dict[str, str] = {}
    try:
        from watchlist_manager import SECTOR_MAP
        for sector, syms in SECTOR_MAP.items():
            for sym in syms:
                if sym not in sym_sector:
                    sym_sector[sym.upper()] = sector
    except Exception:
        pass

    try:
        df = _load_trades(days)
        if df.empty:
            _cache_set(cache_key, [])
            return []

        df["net_pnl"] = pd.to_numeric(df["net_pnl"], errors="coerce").fillna(0.0)
        df["sector"]  = df["symbol"].str.upper().map(sym_sector).fillna("OTHER")

        results = []
        for sector, grp in df.groupby("sector"):
            n         = len(grp)
            wins      = (grp["net_pnl"] > 0).sum()
            total_pnl = float(grp["net_pnl"].sum())
            avg_pnl   = float(grp["net_pnl"].mean())
            win_rate  = wins / n if n > 0 else 0.0
            results.append({
                "sector":    str(sector),
                "etf":       SECTOR_ETF.get(str(sector), ""),
                "trades":    int(n),
                "win_rate":  round(win_rate, 4),
                "total_pnl": round(total_pnl, 2),
                "avg_pnl":   round(avg_pnl,  2),
            })

        results.sort(key=lambda x: x["total_pnl"], reverse=True)
        _cache_set(cache_key, results)
        return results
    except Exception as exc:
        logger.debug(f"[performance_analytics] get_sector_attribution error: {exc}")
        _cache_set(cache_key, [])
        return []


def get_time_of_day_stats(days: int = 30) -> dict:
    """
    Group trades by entry time bucket:
        OPEN        9:15-10:00
        MORNING    10:00-11:30
        MIDDAY     11:30-13:30
        AFTERNOON  13:30-15:00
        POWER_HOUR 15:00-15:30

    Returns: {'OPEN': {'trades': n, 'win_rate': f, 'avg_pnl': f}, ...}
    """
    cache_key = f"tod_stats_{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    import pandas as pd

    BUCKETS = [
        ("OPEN",        "09:15", "10:00"),
        ("MORNING",     "10:00", "11:30"),
        ("MIDDAY",      "11:30", "13:30"),
        ("AFTERNOON",   "13:30", "15:00"),
        ("POWER_HOUR",  "15:00", "15:30"),
    ]
    default = {b[0]: {"trades": 0, "win_rate": 0.0, "avg_pnl": 0.0} for b in BUCKETS}

    try:
        df = _load_trades(days)
        if df.empty or "entry_time_ist" not in df.columns:
            _cache_set(cache_key, default)
            return default

        df["net_pnl"]   = pd.to_numeric(df["net_pnl"], errors="coerce").fillna(0.0)
        df["_entry_dt"] = pd.to_datetime(df["entry_time_ist"], errors="coerce")
        df["_time_str"] = df["_entry_dt"].dt.strftime("%H:%M")
        df              = df.dropna(subset=["_time_str"])

        result: dict = {}
        for bucket_name, start_t, end_t in BUCKETS:
            mask = (df["_time_str"] >= start_t) & (df["_time_str"] < end_t)
            grp  = df[mask]
            n    = len(grp)
            if n == 0:
                result[bucket_name] = {"trades": 0, "win_rate": 0.0, "avg_pnl": 0.0}
            else:
                wins = (grp["net_pnl"] > 0).sum()
                result[bucket_name] = {
                    "trades":   int(n),
                    "win_rate": round(wins / n, 4),
                    "avg_pnl":  round(float(grp["net_pnl"].mean()), 2),
                }

        _cache_set(cache_key, result)
        return result
    except Exception as exc:
        logger.debug(f"[performance_analytics] get_time_of_day_stats error: {exc}")
        _cache_set(cache_key, default)
        return default


def get_monthly_compounding_progress(daily_capital: float = 5000.0) -> dict:
    """
    Track progress toward 1%/day compounding target.

    Returns:
        month_start_capital  -- capital at start of current month
        current_capital      -- capital now (start + MTD P&L)
        month_pnl            -- MTD P&L $
        month_pnl_pct        -- MTD P&L %
        target_1pct_daily    -- daily $ target = current_capital * 0.01
        days_traded          -- trading days this month with at least 1 trade
        days_on_target       -- days where daily P&L >= 1% of daily_capital
        projected_month_end  -- if avg daily return continues for 22 trading days
        on_track             -- True if pacing for >= 20%/month
        streak_days          -- consecutive profitable days (most recent streak)
    """
    cache_key = f"monthly_progress_{int(daily_capital)}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    import pandas as pd

    default = {
        "month_start_capital": daily_capital,
        "current_capital":     daily_capital,
        "month_pnl":           0.0,
        "month_pnl_pct":       0.0,
        "target_1pct_daily":   round(daily_capital * 0.01, 2),
        "days_traded":         0,
        "days_on_target":      0,
        "projected_month_end": daily_capital,
        "on_track":            False,
        "streak_days":         0,
    }

    try:
        today       = date.today()
        month_start = today.replace(day=1)
        days_since  = (today - month_start).days + 1

        df = _load_trades(days_since + 1)
        if df.empty:
            _cache_set(cache_key, default)
            return default

        df["net_pnl"]  = pd.to_numeric(df["net_pnl"],  errors="coerce").fillna(0.0)
        df["date_ist"] = pd.to_datetime(df["date_ist"], errors="coerce")
        df = df[df["date_ist"].dt.date >= month_start].copy()

        if df.empty:
            _cache_set(cache_key, default)
            return default

        daily_pnl  = df.groupby(df["date_ist"].dt.date)["net_pnl"].sum().sort_index()
        days_traded   = len(daily_pnl)
        month_pnl     = float(daily_pnl.sum())
        month_pnl_pct = (month_pnl / daily_capital * 100.0) if daily_capital > 0 else 0.0
        current_cap   = daily_capital + month_pnl

        # Days on target (>= 1% of daily_capital per day)
        days_on_target = int((daily_pnl >= daily_capital * 0.01).sum())

        # Projected month end: compound avg daily % over remaining trading days
        avg_daily_pct   = month_pnl_pct / days_traded / 100.0 if days_traded > 0 else 0.0
        trading_days_left = max(0, 22 - days_traded)
        projected = (
            current_cap * ((1 + avg_daily_pct) ** trading_days_left)
            if avg_daily_pct > -1 else current_cap
        )

        # On track: pacing >= 20%/month => need ~0.91%/day over 22 days
        on_track = (month_pnl_pct / days_traded >= 0.91) if days_traded > 0 else False

        # Consecutive profitable days streak (most recent)
        streak = 0
        for pnl in reversed(daily_pnl.tolist()):
            if pnl > 0:
                streak += 1
            else:
                break

        result = {
            "month_start_capital": round(daily_capital, 2),
            "current_capital":     round(current_cap, 2),
            "month_pnl":           round(month_pnl, 2),
            "month_pnl_pct":       round(month_pnl_pct, 4),
            "target_1pct_daily":   round(current_cap * 0.01, 2),
            "days_traded":         int(days_traded),
            "days_on_target":      int(days_on_target),
            "projected_month_end": round(projected, 2),
            "on_track":            bool(on_track),
            "streak_days":         int(streak),
        }
        _cache_set(cache_key, result)
        return result
    except Exception as exc:
        logger.debug(f"[performance_analytics] get_monthly_compounding_progress error: {exc}")
        _cache_set(cache_key, default)
        return default
