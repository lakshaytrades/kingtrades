"""
market_data_store.py — NSE Momentum Groww AI Bot
Historical Market Data Store — Grows Daily Automatically

Stores ALL fetched candle data locally in SQLite.
After market close, downloads fresh data for every watchlist stock.
This gives the bot an ever-growing dataset to learn from.

Benefits:
- Faster signals (cached data, no API call delays)
- Offline trainer has fresh data every day
- Detects market regime changes over time
- Tracks seasonal patterns (expiry weeks, budget months, etc.)
- Never depends solely on live API during market hours
"""

import logging
import sqlite3
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

from utils import (
    format_ist_timestamp, get_current_ist_time, get_current_ist_date,
    convert_to_ist, IST, UTC
)

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
CANDLE_DB = DATA_DIR / "candle_store.db"
STATS_DB  = DATA_DIR / "market_stats.db"


class MarketDataStore:
    """
    Local growing database of NSE candle data.
    Auto-downloads after each market session.
    """

    def __init__(self):
        self._init_candle_db()
        self._init_stats_db()

    # --------------------------------------------------------
    # DATABASE INIT
    # --------------------------------------------------------

    def _init_candle_db(self):
        with sqlite3.connect(CANDLE_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candles (
                    symbol    TEXT    NOT NULL,
                    interval  TEXT    NOT NULL,
                    ts_ist    TEXT    NOT NULL,
                    open      REAL,
                    high      REAL,
                    low       REAL,
                    close     REAL,
                    volume    INTEGER,
                    PRIMARY KEY (symbol, interval, ts_ist)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sym_int ON candles(symbol, interval)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS download_log (
                    symbol      TEXT,
                    interval    TEXT,
                    last_ts_ist TEXT,
                    downloaded_at TEXT,
                    candle_count  INTEGER,
                    PRIMARY KEY (symbol, interval)
                )
            """)
            conn.commit()
        logger.debug(f"[{format_ist_timestamp()}] Candle DB ready: {CANDLE_DB}")

    def _init_stats_db(self):
        with sqlite3.connect(STATS_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    symbol       TEXT    NOT NULL,
                    date_ist     TEXT    NOT NULL,
                    open         REAL,
                    high         REAL,
                    low          REAL,
                    close        REAL,
                    volume       INTEGER,
                    adr_pct      REAL,
                    gap_pct      REAL,
                    return_pct   REAL,
                    relative_str REAL,
                    nifty_return REAL,
                    PRIMARY KEY (symbol, date_ist)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS symbol_meta (
                    symbol       TEXT PRIMARY KEY,
                    sector       TEXT,
                    avg_adr_pct  REAL,
                    avg_volume   INTEGER,
                    last_updated TEXT,
                    total_days   INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS market_calendar (
                    date_ist    TEXT PRIMARY KEY,
                    is_holiday  INTEGER DEFAULT 0,
                    event_name  TEXT,
                    nifty_open  REAL,
                    nifty_close REAL,
                    nifty_ret   REAL,
                    market_mood TEXT
                )
            """)
            conn.commit()

    # --------------------------------------------------------
    # SAVE CANDLES
    # --------------------------------------------------------

    def save_candles(self, symbol: str, interval: str, df: pd.DataFrame) -> int:
        """
        Save a DataFrame of OHLCV candles to the store.
        Ignores duplicates (upsert). Returns count saved.
        """
        if df is None or df.empty:
            return 0
        rows = []
        for ts, row in df.iterrows():
            ts_str = str(ts) if hasattr(ts, '__str__') else str(ts)
            rows.append((
                symbol, interval, ts_str,
                float(row.get("open",  0)),
                float(row.get("high",  0)),
                float(row.get("low",   0)),
                float(row.get("close", 0)),
                int(row.get("volume",  0)),
            ))
        with sqlite3.connect(CANDLE_DB) as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?,?,?)",
                rows
            )
            conn.execute(
                "INSERT INTO download_log VALUES (?,?,?,?,?) "
                "ON CONFLICT(symbol,interval) DO UPDATE SET "
                "last_ts_ist=excluded.last_ts_ist, "
                "downloaded_at=excluded.downloaded_at, "
                "candle_count=candle_count+excluded.candle_count",
                (symbol, interval, rows[-1][2] if rows else "",
                 format_ist_timestamp(), len(rows))
            )
            conn.commit()
        logger.debug(f"[{format_ist_timestamp()}] Saved {len(rows)} {interval} candles for {symbol}")
        return len(rows)

    # --------------------------------------------------------
    # LOAD CANDLES
    # --------------------------------------------------------

    def load_candles(
        self,
        symbol:   str,
        interval: str,
        days:     int = 30,
        from_date: Optional[date] = None,
    ) -> Optional[pd.DataFrame]:
        """Load candles from local store. Falls back to None if not available."""
        cutoff = str(
            from_date or (get_current_ist_date() - timedelta(days=days))
        )
        with sqlite3.connect(CANDLE_DB) as conn:
            df = pd.read_sql(
                "SELECT ts_ist, open, high, low, close, volume FROM candles "
                "WHERE symbol=? AND interval=? AND ts_ist >= ? "
                "ORDER BY ts_ist",
                conn, params=(symbol, interval, cutoff)
            )
        if df.empty:
            return None
        df["ts_ist"] = pd.to_datetime(df["ts_ist"])
        df = df.set_index("ts_ist")
        df.index.name = "datetime"
        return df

    def get_candle_count(self, symbol: str, interval: str) -> int:
        with sqlite3.connect(CANDLE_DB) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM candles WHERE symbol=? AND interval=?",
                (symbol, interval)
            ).fetchone()
        return row[0] if row else 0

    def get_last_download_info(self, symbol: str, interval: str) -> Dict:
        with sqlite3.connect(CANDLE_DB) as conn:
            row = conn.execute(
                "SELECT last_ts_ist, downloaded_at, candle_count FROM download_log "
                "WHERE symbol=? AND interval=?",
                (symbol, interval)
            ).fetchone()
        if row:
            return {"last_ts": row[0], "downloaded_at": row[1], "count": row[2]}
        return {}

    # --------------------------------------------------------
    # DAILY STATS (per-stock performance tracking)
    # --------------------------------------------------------

    def save_daily_stat(self, symbol: str, stat: Dict):
        today = str(get_current_ist_date())
        with sqlite3.connect(STATS_DB) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO daily_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    symbol, today,
                    stat.get("open", 0), stat.get("high", 0),
                    stat.get("low", 0),  stat.get("close", 0),
                    stat.get("volume", 0),
                    stat.get("adr_pct", 0), stat.get("gap_pct", 0),
                    stat.get("return_pct", 0), stat.get("relative_str", 0),
                    stat.get("nifty_return", 0),
                )
            )
            conn.commit()

    def get_daily_stats(self, symbol: str, days: int = 30) -> pd.DataFrame:
        cutoff = str(get_current_ist_date() - timedelta(days=days))
        with sqlite3.connect(STATS_DB) as conn:
            df = pd.read_sql(
                "SELECT * FROM daily_stats WHERE symbol=? AND date_ist >= ? ORDER BY date_ist",
                conn, params=(symbol, cutoff)
            )
        return df

    def get_avg_adr(self, symbol: str, days: int = 20) -> float:
        """Average daily range % over last N days."""
        df = self.get_daily_stats(symbol, days)
        if df.empty or "adr_pct" not in df.columns:
            return 0.0
        return float(df["adr_pct"].mean())

    # --------------------------------------------------------
    # MARKET CALENDAR
    # --------------------------------------------------------

    def log_market_day(self, nifty_open: float, nifty_close: float, events: List[str]):
        today = str(get_current_ist_date())
        ret   = ((nifty_close - nifty_open) / nifty_open * 100) if nifty_open else 0
        mood  = "BULLISH" if ret > 0.5 else "BEARISH" if ret < -0.5 else "FLAT"
        with sqlite3.connect(STATS_DB) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO market_calendar VALUES (?,0,?,?,?,?,?)",
                (today, ", ".join(events), nifty_open, nifty_close, round(ret, 2), mood)
            )
            conn.commit()

    def get_market_calendar(self, days: int = 60) -> pd.DataFrame:
        cutoff = str(get_current_ist_date() - timedelta(days=days))
        with sqlite3.connect(STATS_DB) as conn:
            df = pd.read_sql(
                "SELECT * FROM market_calendar WHERE date_ist >= ? ORDER BY date_ist",
                conn, params=(cutoff,)
            )
        return df

    # --------------------------------------------------------
    # SEASONAL / STATISTICAL ANALYSIS
    # --------------------------------------------------------

    def get_day_of_week_stats(self) -> pd.DataFrame:
        """
        Returns P&L statistics by weekday.
        18yr rule: "Monday opens are often choppy. Friday has profit-taking."
        """
        df = self.get_market_calendar(days=365)
        if df.empty:
            return pd.DataFrame()
        df["dow"] = pd.to_datetime(df["date_ist"]).dt.day_name()
        return df.groupby("dow")["nifty_ret"].agg(["mean", "std", "count"]).round(3)

    def get_monthly_seasonality(self) -> pd.DataFrame:
        """Returns average Nifty return by month over stored history."""
        df = self.get_market_calendar(days=730)
        if df.empty:
            return pd.DataFrame()
        df["month"] = pd.to_datetime(df["date_ist"]).dt.month_name()
        return df.groupby("month")["nifty_ret"].agg(["mean", "count"]).round(3)

    def get_top_correlated_stocks(self, nifty_rets: pd.Series, symbols: List[str], top_n: int = 10) -> List[str]:
        """
        Find stocks most correlated to Nifty.
        High correlation = beta play on Nifty direction.
        """
        correlations = []
        for sym in symbols:
            df = self.get_daily_stats(sym, days=30)
            if len(df) < 10:
                continue
            try:
                corr = df["return_pct"].corr(nifty_rets.tail(len(df)))
                correlations.append((sym, corr))
            except Exception:
                pass
        correlations.sort(key=lambda x: abs(x[1]), reverse=True)
        return [sym for sym, _ in correlations[:top_n]]

    # --------------------------------------------------------
    # BULK DOWNLOAD (post-market)
    # --------------------------------------------------------

    def download_all(self, symbols: List[str], fetcher, intervals: List[str] = None) -> Dict:
        """
        Download latest candle data for all symbols.
        Called at EOD (after 3:30 PM IST) to keep store fresh.
        """
        if intervals is None:
            intervals = ["5m", "15m", "1h", "1d"]

        results = {"downloaded": 0, "failed": 0, "symbols": []}

        for symbol in symbols:
            for interval in intervals:
                try:
                    days = {"5m": 10, "15m": 30, "1h": 90, "1d": 365}.get(interval, 30)
                    df   = fetcher.get_candles(symbol, interval=interval, days=days)
                    if df is not None and not df.empty:
                        saved = self.save_candles(symbol, interval, df)
                        results["downloaded"] += saved
                except Exception as e:
                    results["failed"] += 1
                    logger.debug(f"Download failed {symbol} {interval}: {e}")

            # Save daily stat
            try:
                q = fetcher.get_quote(symbol)
                if q:
                    df_d = self.load_candles(symbol, "1d", days=21)
                    adr  = 0.0
                    if df_d is not None and len(df_d) > 1:
                        recent = df_d.tail(20)
                        adr    = float(((recent["high"] - recent["low"]) / recent["close"]).mean() * 100)
                    self.save_daily_stat(symbol, {
                        "open":        q.get("open", 0),
                        "high":        q.get("high", 0),
                        "low":         q.get("low",  0),
                        "close":       q.get("ltp",  0),
                        "volume":      q.get("volume", 0),
                        "adr_pct":     round(adr, 2),
                        "return_pct":  q.get("change_pct", 0),
                    })
                    results["symbols"].append(symbol)
            except Exception:
                pass

        logger.info(
            f"[{format_ist_timestamp()}] Data store updated: "
            f"{results['downloaded']} candles, "
            f"{len(results['symbols'])} symbols, "
            f"{results['failed']} failures"
        )
        return results

    def get_store_summary(self) -> str:
        """Human-readable summary of what's in the store."""
        with sqlite3.connect(CANDLE_DB) as conn:
            total = conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
            symbols = conn.execute("SELECT COUNT(DISTINCT symbol) FROM candles").fetchone()[0]
            oldest  = conn.execute("SELECT MIN(ts_ist) FROM candles").fetchone()[0]
        return (
            f"Data Store: {total:,} candles | "
            f"{symbols} symbols | "
            f"Since: {str(oldest)[:10] if oldest else 'empty'}"
        )


# Singleton
_store: Optional[MarketDataStore] = None

def get_data_store() -> MarketDataStore:
    global _store
    if _store is None:
        _store = MarketDataStore()
    return _store
