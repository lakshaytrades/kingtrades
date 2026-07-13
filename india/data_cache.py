"""
data_cache.py — Local Parquet cache for NSE OHLCV data.

Stores downloaded data in data/cache/<SYMBOL>_<interval>.parquet (or .csv.gz
if no Parquet engine is installed).
Cache is valid for 26h (intraday) or 7 days (daily bars).
All timestamps stored in IST (Asia/Kolkata).

Usage:
    from data_cache import save_data, load_data, cache_stats
    save_data(data_dict, interval="1h", source="upstox")
    data = load_data(["RELIANCE", "TCS"], interval="1h")
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from zoneinfo import ZoneInfo

logger = logging.getLogger("data_cache")
IST = ZoneInfo("Asia/Kolkata")

_CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
_MAX_AGE_INTRADAY_H = 26   # intraday bars stale after 26h (one trading day)
_MAX_AGE_DAILY_D = 7       # daily bars stale after 7 days

# ---------------------------------------------------------------------------
# Engine detection — prefer Parquet, fall back to gzipped CSV
# ---------------------------------------------------------------------------
_USE_PARQUET: bool = False
try:
    import pyarrow  # noqa: F401
    _USE_PARQUET = True
    logger.debug("data_cache: using pyarrow (Parquet)")
except ImportError:
    try:
        import fastparquet  # noqa: F401
        _USE_PARQUET = True
        logger.debug("data_cache: using fastparquet (Parquet)")
    except ImportError:
        logger.debug("data_cache: no Parquet engine found — using CSV.GZ fallback")


def _cache_path(symbol: str, interval: str) -> Path:
    """Return the canonical cache file path for a symbol/interval pair."""
    safe = symbol.upper().replace("-", "_").replace(".", "_")
    ext = ".parquet" if _USE_PARQUET else ".csv.gz"
    return _CACHE_DIR / f"{safe}_{interval}{ext}"


def _write_df(df: pd.DataFrame, path: Path) -> None:
    """Write DataFrame to cache (Parquet or CSV.GZ)."""
    if _USE_PARQUET:
        df.to_parquet(path, engine="auto")
    else:
        # CSV: convert tz-aware index to string so it round-trips cleanly
        df_out = df.copy()
        df_out.index = df_out.index.astype(str)
        df_out.to_csv(path, compression="gzip")


def _read_df(path: Path) -> pd.DataFrame:
    """Read DataFrame from cache (Parquet or CSV.GZ)."""
    if _USE_PARQUET:
        return pd.read_parquet(path, engine="auto")
    else:
        df = pd.read_csv(path, index_col=0, parse_dates=True, compression="gzip")
        return df


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_data(data: Dict[str, pd.DataFrame], interval: str, source: str = "unknown") -> int:
    """
    Save downloaded data to the local cache.

    Parameters
    ----------
    data     : dict mapping symbol -> OHLCV DataFrame
    interval : bar interval string, e.g. "1h", "5m", "1d"
    source   : informational label (e.g. "yfinance", "upstox")

    Returns
    -------
    Number of symbols successfully saved.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    saved = 0
    for sym, df in data.items():
        try:
            if df is None or df.empty:
                continue
            path = _cache_path(sym, interval)
            _df = df.copy()
            # Ensure IST timezone is preserved
            if _df.index.tz is None:
                _df.index = _df.index.tz_localize(IST)
            elif str(_df.index.tz) != "Asia/Kolkata":
                _df.index = _df.index.tz_convert(IST)
            _write_df(_df, path)
            saved += 1
        except Exception as e:
            logger.debug(f"cache save {sym}: {e}")
    if saved:
        logger.info(
            f"Cached {saved} symbols ({interval}) from {source} -> {_CACHE_DIR}"
        )
    return saved


def load_data(
    symbols: List[str],
    interval: str,
    max_age_hours: Optional[int] = None,
) -> Dict[str, pd.DataFrame]:
    """
    Load cached data for the given symbols and interval.

    Silently skips symbols whose cache file is missing or stale.

    Parameters
    ----------
    symbols      : list of NSE ticker symbols (case-insensitive)
    interval     : bar interval string, e.g. "1h", "5m", "1d"
    max_age_hours: override default staleness threshold (hours)

    Returns
    -------
    Dict of symbol -> DataFrame.  May be a partial result if some symbols
    have no valid cache entry.
    """
    if max_age_hours is None:
        is_daily = interval in ("1d", "1wk")
        max_age_hours = _MAX_AGE_DAILY_D * 24 if is_daily else _MAX_AGE_INTRADAY_H

    cutoff = datetime.now(IST) - timedelta(hours=max_age_hours)
    result: Dict[str, pd.DataFrame] = {}
    missing: List[str] = []

    for sym in symbols:
        path = _cache_path(sym, interval)
        try:
            if not path.exists():
                missing.append(sym)
                continue
            # Fast staleness check via file modification time
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=IST)
            if mtime < cutoff:
                missing.append(sym)
                logger.debug(
                    f"Stale cache: {sym} ({interval}) — "
                    f"last updated {mtime.strftime('%Y-%m-%d %H:%M IST')}"
                )
                continue
            df = _read_df(path)
            if df is None or df.empty:
                missing.append(sym)
                continue
            # Ensure IST timezone on the index
            if df.index.tz is None:
                df.index = df.index.tz_localize(IST)
            elif str(df.index.tz) != "Asia/Kolkata":
                df.index = df.index.tz_convert(IST)
            result[sym] = df
        except Exception as e:
            logger.debug(f"cache load {sym}: {e}")
            missing.append(sym)

    if missing:
        shown = ", ".join(missing[:10])
        ellipsis = "..." if len(missing) > 10 else ""
        logger.info(f"Cache miss ({interval}): {shown}{ellipsis}")
    return result


def _all_cache_extensions() -> List[str]:
    """Return the list of cache file extensions to scan.

    Always scans both .parquet and .csv.gz so that files saved by one engine
    remain visible when a different engine is active in the current process.
    """
    return [".parquet", ".csv.gz"]


def cache_stats() -> dict:
    """Return a summary of what is currently in the cache directory."""
    if not _CACHE_DIR.exists():
        return {"symbols": 0, "size_mb": 0.0, "intervals": [], "cache_dir": str(_CACHE_DIR)}
    files: List[Path] = []
    for ext in _all_cache_extensions():
        files.extend(_CACHE_DIR.glob(f"*{ext}"))
    size_mb = sum(f.stat().st_size for f in files) / 1024 / 1024
    # Extract interval from filenames: <SYMBOL>_<interval><ext>
    intervals: set = set()
    for f in files:
        for ext in _all_cache_extensions():
            if f.name.endswith(ext):
                stem = f.name[: -len(ext)]
                intervals.add(stem.rsplit("_", 1)[-1])
                break
    return {
        "symbols": len(files),
        "size_mb": round(size_mb, 2),
        "intervals": sorted(intervals),
        "cache_dir": str(_CACHE_DIR),
    }


def list_cached_symbols(interval: str) -> List[str]:
    """List all symbols cached for a given interval (any engine extension)."""
    if not _CACHE_DIR.exists():
        return []
    seen: set = set()
    for ext in _all_cache_extensions():
        suffix = f"_{interval}{ext}"
        for f in _CACHE_DIR.glob(f"*{suffix}"):
            seen.add(f.name[: -len(suffix)])
    return sorted(seen)
