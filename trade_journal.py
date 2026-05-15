"""
trade_journal.py — NSE Momentum Groww AI Bot
SQLite Trade Journal + Pattern Performance Tracker

Logs every trade with full SEBI-compliant fields.
Powers the self-learning module by tracking which patterns/times/stocks perform best.

18yr rule: "Keep a journal. Review it EVERY day. 
The market is a teacher — it only teaches those who pay attention."
"""

import logging
import sqlite3
import json
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time, get_current_ist_date

logger = logging.getLogger(__name__)

DB_PATH = "logs/trades/trade_journal.db"


@dataclass
class TradeRecord:
    """One complete trade from entry to exit."""
    # Identifiers
    trade_id:       str  = ""
    order_id:       str  = ""
    symbol:         str  = ""
    date_ist:       str  = ""

    # Entry
    direction:      str   = ""      # "LONG" or "SHORT"
    entry_price:    float = 0.0
    entry_qty:      int   = 0
    entry_time_ist: str   = ""
    entry_pattern:  str   = ""      # e.g. "VWAP_BOUNCE+ORB_BREAKOUT"
    signal_score:   float = 0.0
    regime:         str   = ""
    session:        str   = ""
    mtf_aligned:    bool  = False
    volume_ratio:   float = 1.0
    rsi_at_entry:   float = 50.0
    atr_at_entry:   float = 0.0

    # Risk levels
    stop_loss:      float = 0.0
    target_1:       float = 0.0
    target_2:       float = 0.0
    risk_pct:       float = 0.0
    rr_ratio:       float = 0.0

    # Exit
    exit_price:     float = 0.0
    exit_qty:       int   = 0
    exit_time_ist:  str   = ""
    exit_reason:    str   = ""      # "HIT_T1","HIT_T2","HIT_SL","FORCE_EXIT","KILL"

    # Result
    pnl:            float = 0.0
    pnl_pct:        float = 0.0
    brokerage:      float = 0.0
    net_pnl:        float = 0.0
    outcome:        str   = ""      # "WIN" or "LOSS"
    hold_minutes:   float = 0.0

    # Metadata
    notes:          str   = ""


class TradeJournal:
    """
    SQLite-based trade journal with pattern and timing analytics.
    Self-learning module reads from here to adapt strategy parameters.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id       TEXT PRIMARY KEY,
                    order_id       TEXT,
                    symbol         TEXT,
                    date_ist       TEXT,
                    direction      TEXT,
                    entry_price    REAL,
                    entry_qty      INTEGER,
                    entry_time_ist TEXT,
                    entry_pattern  TEXT,
                    signal_score   REAL,
                    regime         TEXT,
                    session        TEXT,
                    mtf_aligned    INTEGER,
                    volume_ratio   REAL,
                    rsi_at_entry   REAL,
                    atr_at_entry   REAL,
                    stop_loss      REAL,
                    target_1       REAL,
                    target_2       REAL,
                    risk_pct       REAL,
                    rr_ratio       REAL,
                    exit_price     REAL,
                    exit_qty       INTEGER,
                    exit_time_ist  TEXT,
                    exit_reason    TEXT,
                    pnl            REAL,
                    pnl_pct        REAL,
                    brokerage      REAL,
                    net_pnl        REAL,
                    outcome        TEXT,
                    hold_minutes   REAL,
                    notes          TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_summary (
                    date_ist          TEXT PRIMARY KEY,
                    total_trades      INTEGER,
                    wins              INTEGER,
                    losses            INTEGER,
                    gross_pnl         REAL,
                    net_pnl           REAL,
                    win_rate          REAL,
                    best_trade_pnl    REAL,
                    worst_trade_pnl   REAL,
                    max_drawdown      REAL,
                    capital_deployed  REAL,
                    return_pct        REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pattern_stats (
                    pattern_name    TEXT PRIMARY KEY,
                    total_trades    INTEGER DEFAULT 0,
                    wins            INTEGER DEFAULT 0,
                    losses          INTEGER DEFAULT 0,
                    total_pnl       REAL DEFAULT 0,
                    avg_pnl         REAL DEFAULT 0,
                    win_rate        REAL DEFAULT 0,
                    avg_hold_min    REAL DEFAULT 0,
                    last_updated    TEXT
                )
            """)
            conn.commit()
        logger.info(f"[{format_ist_timestamp()}] Trade journal DB ready: {self.db_path}")

    # -------------------------------------------------------
    # WRITE
    # -------------------------------------------------------

    def log_trade(self, trade: TradeRecord):
        """Insert or update a trade record."""
        if not trade.trade_id:
            trade.trade_id = f"{trade.symbol}_{trade.entry_time_ist.replace(' ','_').replace(':','')}"
        if not trade.date_ist:
            trade.date_ist = str(get_current_ist_date())

        d = asdict(trade)
        d["mtf_aligned"] = int(trade.mtf_aligned)

        cols = ", ".join(d.keys())
        placeholders = ", ".join("?" * len(d))
        updates = ", ".join(f"{k}=excluded.{k}" for k in d if k != "trade_id")

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"INSERT INTO trades ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT(trade_id) DO UPDATE SET {updates}",
                list(d.values())
            )
            conn.commit()

        logger.info(
            f"[{format_ist_timestamp()}] Journal: {trade.symbol} {trade.direction} "
            f"{trade.outcome} ₹{trade.net_pnl:+.0f}"
        )
        self._update_pattern_stats(trade)

    def _update_pattern_stats(self, trade: TradeRecord):
        """Update per-pattern win rate statistics."""
        if not trade.entry_pattern or not trade.outcome:
            return
        patterns = trade.entry_pattern.split("+")
        for pattern in patterns:
            pattern = pattern.strip()
            if not pattern:
                continue
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO pattern_stats (pattern_name, total_trades, wins, losses, total_pnl, last_updated)
                    VALUES (?, 1, ?, ?, ?, ?)
                    ON CONFLICT(pattern_name) DO UPDATE SET
                        total_trades = total_trades + 1,
                        wins   = wins   + excluded.wins,
                        losses = losses + excluded.losses,
                        total_pnl = total_pnl + excluded.total_pnl,
                        last_updated = excluded.last_updated
                """, (
                    pattern,
                    1 if trade.outcome == "WIN" else 0,
                    1 if trade.outcome == "LOSS" else 0,
                    trade.net_pnl,
                    format_ist_timestamp(),
                ))
                conn.execute("""
                    UPDATE pattern_stats SET
                        avg_pnl  = total_pnl / total_trades,
                        win_rate = CAST(wins AS REAL) / total_trades * 100
                    WHERE pattern_name = ?
                """, (pattern,))
                conn.commit()

    def save_daily_summary(self, summary: Dict):
        """Persist end-of-day summary."""
        date_str = summary.get("date_ist", str(get_current_ist_date()))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO daily_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(date_ist) DO UPDATE SET
                    total_trades=excluded.total_trades,
                    wins=excluded.wins,
                    losses=excluded.losses,
                    gross_pnl=excluded.gross_pnl,
                    net_pnl=excluded.net_pnl,
                    win_rate=excluded.win_rate,
                    best_trade_pnl=excluded.best_trade_pnl,
                    worst_trade_pnl=excluded.worst_trade_pnl,
                    max_drawdown=excluded.max_drawdown,
                    capital_deployed=excluded.capital_deployed,
                    return_pct=excluded.return_pct
            """, (
                date_str,
                summary.get("total_trades", 0),
                summary.get("wins", 0),
                summary.get("losses", 0),
                summary.get("gross_pnl", 0),
                summary.get("net_pnl", 0),
                summary.get("win_rate", 0),
                summary.get("best_trade_pnl", 0),
                summary.get("worst_trade_pnl", 0),
                summary.get("max_drawdown", 0),
                summary.get("capital_deployed", 0),
                summary.get("return_pct", 0),
            ))
            conn.commit()

    # -------------------------------------------------------
    # READ / ANALYTICS
    # -------------------------------------------------------

    def get_today_trades(self) -> List[TradeRecord]:
        today = str(get_current_ist_date())
        return self._fetch_trades("WHERE date_ist = ?", (today,))

    def get_trades_last_n_days(self, n: int = 30) -> pd.DataFrame:
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql(
                "SELECT * FROM trades ORDER BY entry_time_ist DESC LIMIT ?",
                conn, params=(n * 20,)
            )
        return df

    def get_pattern_stats(self) -> pd.DataFrame:
        """Return pattern performance table sorted by win rate."""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql(
                "SELECT * FROM pattern_stats WHERE total_trades >= 3 ORDER BY win_rate DESC",
                conn
            )
        return df

    def get_best_patterns(self, top_n: int = 5, min_trades: int = 5) -> List[Dict]:
        """Return top N patterns by win rate (min_trades filter)."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT pattern_name, total_trades, win_rate, avg_pnl
                FROM pattern_stats
                WHERE total_trades >= ?
                ORDER BY win_rate DESC, avg_pnl DESC
                LIMIT ?
            """, (min_trades, top_n)).fetchall()
        return [{"pattern": r[0], "trades": r[1], "win_rate": r[2], "avg_pnl": r[3]} for r in rows]

    def get_worst_patterns(self, bottom_n: int = 5, min_trades: int = 5) -> List[Dict]:
        """Return bottom N patterns — candidates for disabling."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT pattern_name, total_trades, win_rate, avg_pnl
                FROM pattern_stats
                WHERE total_trades >= ?
                ORDER BY win_rate ASC
                LIMIT ?
            """, (min_trades, bottom_n)).fetchall()
        return [{"pattern": r[0], "trades": r[1], "win_rate": r[2], "avg_pnl": r[3]} for r in rows]

    def get_session_stats(self) -> pd.DataFrame:
        """P&L breakdown by session (OPENING_DRIVE / MORNING / MIDDAY / AFTERNOON)."""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql("""
                SELECT session,
                       COUNT(*) as trades,
                       SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                       ROUND(AVG(pnl_pct),2) as avg_pnl_pct,
                       ROUND(SUM(net_pnl),2) as total_pnl
                FROM trades GROUP BY session ORDER BY total_pnl DESC
            """, conn)
        return df

    def get_daily_equity_curve(self) -> pd.DataFrame:
        """Cumulative P&L by day for equity curve chart."""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql("""
                SELECT date_ist, SUM(net_pnl) as daily_pnl
                FROM trades GROUP BY date_ist ORDER BY date_ist
            """, conn)
        if not df.empty:
            df["cumulative_pnl"] = df["daily_pnl"].cumsum()
        return df

    def get_today_summary(self) -> Dict:
        trades = self.get_today_trades()
        if not trades:
            return {"total_trades": 0, "wins": 0, "losses": 0, "net_pnl": 0, "win_rate": 0}
        wins   = sum(1 for t in trades if t.outcome == "WIN")
        losses = sum(1 for t in trades if t.outcome == "LOSS")
        net    = sum(t.net_pnl for t in trades)
        return {
            "date_ist":      str(get_current_ist_date()),
            "total_trades":  len(trades),
            "wins":          wins,
            "losses":        losses,
            "net_pnl":       round(net, 2),
            "win_rate":      round(wins / len(trades) * 100, 1) if trades else 0,
            "best_trade":    max((t.net_pnl for t in trades), default=0),
            "worst_trade":   min((t.net_pnl for t in trades), default=0),
        }

    def _fetch_trades(self, where_clause: str = "", params: tuple = ()) -> List[TradeRecord]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(f"SELECT * FROM trades {where_clause} ORDER BY entry_time_ist DESC", params).fetchall()
        records = []
        for row in rows:
            d = dict(row)
            d["mtf_aligned"] = bool(d.get("mtf_aligned", 0))
            try:
                records.append(TradeRecord(**d))
            except Exception as _e:
                logger.debug(f"[suppressed] {_e}")
        return records


# Singleton
_journal: Optional[TradeJournal] = None

def get_journal() -> TradeJournal:
    global _journal
    if _journal is None:
        _journal = TradeJournal()
    return _journal
