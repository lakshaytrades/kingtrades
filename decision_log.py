"""
decision_log.py — KingTrades Trade Decision Logger

Records every signal scanned (taken OR rejected) with the exact reason.
Writes to SQLite so we can query: "Why aren't more trades being placed?"

Schema:
  signal_decisions — one row per symbol scanned per cycle
  trade_outcomes   — one row per closed trade (fill_price, pnl, hold_time)
  daily_summary    — one row per trading day (aggregated stats)
"""

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except ImportError:
    from pytz import timezone as ZoneInfo
    _ET = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)

DB_PATH = Path("data/decision_log.db")
_lock   = threading.Lock()

# ── Outcome constants ────────────────────────────────────────────────────────
TAKEN                = "TAKEN"
REJ_SCORE            = "REJECTED_SCORE"
REJ_HAF              = "REJECTED_HAF"
REJ_INTERNALS        = "REJECTED_INTERNALS"
REJ_ELITE            = "REJECTED_ELITE"
REJ_HEAT             = "REJECTED_HEAT"
REJ_ORDER_FAIL       = "REJECTED_ORDER_FAIL"
REJ_OTHER            = "REJECTED_OTHER"


@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS signal_decisions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_et            TEXT NOT NULL,
            date_et          TEXT NOT NULL,
            time_et          TEXT NOT NULL,
            symbol           TEXT NOT NULL,
            direction        TEXT NOT NULL,
            ai_score         REAL,
            outcome          TEXT NOT NULL,
            rejection_stage  TEXT,
            rejection_reason TEXT,
            gates_failed     TEXT,
            gates_passed     TEXT,
            quality_grade    TEXT,
            haf_score        REAL,
            fill_price       REAL,
            quantity         INTEGER,
            patterns         TEXT,
            entry_price      REAL,
            stop_loss        REAL,
            target_1         REAL,
            risk_reward      REAL
        );

        CREATE TABLE IF NOT EXISTS trade_outcomes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_et           TEXT NOT NULL,
            date_et         TEXT NOT NULL,
            symbol          TEXT NOT NULL,
            direction       TEXT NOT NULL,
            entry_price     REAL,
            exit_price      REAL,
            quantity        INTEGER,
            pnl             REAL,
            exit_reason     TEXT,
            hold_minutes    REAL,
            entry_score     REAL,
            quality_grade   TEXT,
            was_winner      INTEGER
        );

        CREATE TABLE IF NOT EXISTS daily_summary (
            date_et          TEXT PRIMARY KEY,
            total_scanned    INTEGER DEFAULT 0,
            taken            INTEGER DEFAULT 0,
            rej_score        INTEGER DEFAULT 0,
            rej_haf          INTEGER DEFAULT 0,
            rej_internals    INTEGER DEFAULT 0,
            rej_elite        INTEGER DEFAULT 0,
            rej_heat         INTEGER DEFAULT 0,
            rej_other        INTEGER DEFAULT 0,
            wins             INTEGER DEFAULT 0,
            losses           INTEGER DEFAULT 0,
            total_pnl        REAL DEFAULT 0,
            pass_rate_pct    REAL DEFAULT 0,
            win_rate_pct     REAL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_decisions_date ON signal_decisions(date_et);
        CREATE INDEX IF NOT EXISTS idx_outcomes_date  ON trade_outcomes(date_et);
        """)


def _now_et() -> Tuple[str, str, str]:
    """Returns (ts_iso, date_str, time_str) in ET."""
    now = datetime.now(_ET)
    return now.strftime("%Y-%m-%dT%H:%M:%S"), now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")


# ── Public logging functions ─────────────────────────────────────────────────

def log_rejected_score(symbol: str, direction: str, ai_score: float, threshold: float) -> None:
    ts, date, time_s = _now_et()
    reason = f"Score {ai_score:.1f} < threshold {threshold:.0f}"
    with _lock, _conn() as con:
        con.execute(
            "INSERT INTO signal_decisions (ts_et,date_et,time_et,symbol,direction,ai_score,"
            "outcome,rejection_stage,rejection_reason) VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, date, time_s, symbol, direction, ai_score,
             REJ_SCORE, "SCORE_GATE", reason)
        )
    _bump_daily(date, REJ_SCORE)


def log_rejected_haf(symbol: str, direction: str, ai_score: float,
                     filter_result) -> None:
    ts, date, time_s = _now_et()
    gates_failed = json.dumps(getattr(filter_result, "gates_failed", []))
    gates_passed = json.dumps(getattr(filter_result, "gates_passed", []))
    reason       = getattr(filter_result, "rejection_reason", "HAF filtered")
    grade        = getattr(filter_result, "quality_grade", "")
    haf_score    = getattr(filter_result, "final_score", None)
    with _lock, _conn() as con:
        con.execute(
            "INSERT INTO signal_decisions (ts_et,date_et,time_et,symbol,direction,ai_score,"
            "outcome,rejection_stage,rejection_reason,gates_failed,gates_passed,"
            "quality_grade,haf_score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, date, time_s, symbol, direction, ai_score,
             REJ_HAF, "HAF_GATE", reason, gates_failed, gates_passed, grade, haf_score)
        )
    _bump_daily(date, REJ_HAF)


def log_rejected_internals(symbol: str, direction: str, ai_score: float,
                           reason: str) -> None:
    ts, date, time_s = _now_et()
    with _lock, _conn() as con:
        con.execute(
            "INSERT INTO signal_decisions (ts_et,date_et,time_et,symbol,direction,ai_score,"
            "outcome,rejection_stage,rejection_reason) VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, date, time_s, symbol, direction, ai_score,
             REJ_INTERNALS, "INTERNALS_GATE", reason)
        )
    _bump_daily(date, REJ_INTERNALS)


def log_rejected_elite(symbol: str, direction: str, ai_score: float,
                       reason: str) -> None:
    ts, date, time_s = _now_et()
    with _lock, _conn() as con:
        con.execute(
            "INSERT INTO signal_decisions (ts_et,date_et,time_et,symbol,direction,ai_score,"
            "outcome,rejection_stage,rejection_reason) VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, date, time_s, symbol, direction, ai_score,
             REJ_ELITE, "ELITE_BRAIN", reason)
        )
    _bump_daily(date, REJ_ELITE)


def log_rejected_heat(symbol: str, direction: str, ai_score: float,
                      reason: str) -> None:
    ts, date, time_s = _now_et()
    with _lock, _conn() as con:
        con.execute(
            "INSERT INTO signal_decisions (ts_et,date_et,time_et,symbol,direction,ai_score,"
            "outcome,rejection_stage,rejection_reason) VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, date, time_s, symbol, direction, ai_score,
             REJ_HEAT, "HEAT_GUARD", reason)
        )
    _bump_daily(date, REJ_HEAT)


def log_trade_taken(signal) -> None:
    ts, date, time_s = _now_et()
    patterns = json.dumps(getattr(signal, "patterns", []))
    with _lock, _conn() as con:
        con.execute(
            "INSERT INTO signal_decisions (ts_et,date_et,time_et,symbol,direction,ai_score,"
            "outcome,quality_grade,fill_price,quantity,patterns,entry_price,stop_loss,"
            "target_1,risk_reward) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, date, time_s,
             signal.symbol, signal.direction,
             getattr(signal, "signal_score", 0),
             TAKEN,
             getattr(signal, "quality_grade", "B"),
             getattr(signal, "entry_price", 0),
             getattr(signal, "quantity", 0),
             patterns,
             getattr(signal, "entry_price", 0),
             getattr(signal, "stop_loss", 0),
             getattr(signal, "target_1", 0),
             getattr(signal, "risk_reward", 0),
             )
        )
    _bump_daily(date, TAKEN)


def log_trade_outcome(symbol: str, direction: str, entry_price: float,
                      exit_price: float, quantity: int, pnl: float,
                      exit_reason: str, entry_time_str: str,
                      entry_score: float = 0.0, quality_grade: str = "B") -> None:
    ts, date, time_s = _now_et()
    # Estimate hold time
    try:
        from utils import get_current_et_time
        now_et = datetime.now(_ET)
        # entry_time_str may be IST or ET — just store 0 if parse fails
        hold_minutes = 0.0
    except Exception:
        hold_minutes = 0.0

    won = 1 if pnl > 0 else 0
    with _lock, _conn() as con:
        con.execute(
            "INSERT INTO trade_outcomes (ts_et,date_et,symbol,direction,entry_price,"
            "exit_price,quantity,pnl,exit_reason,hold_minutes,entry_score,"
            "quality_grade,was_winner) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, date, symbol, direction, entry_price, exit_price, quantity,
             pnl, exit_reason, hold_minutes, entry_score, quality_grade, won)
        )
    # Update daily summary P&L + win/loss
    with _lock, _conn() as con:
        con.execute(
            "INSERT INTO daily_summary(date_et) VALUES(?) ON CONFLICT(date_et) DO NOTHING",
            (date,)
        )
        if won:
            con.execute(
                "UPDATE daily_summary SET wins=wins+1, total_pnl=total_pnl+? WHERE date_et=?",
                (pnl, date)
            )
        else:
            con.execute(
                "UPDATE daily_summary SET losses=losses+1, total_pnl=total_pnl+? WHERE date_et=?",
                (pnl, date)
            )


def _bump_daily(date: str, outcome: str) -> None:
    col_map = {
        TAKEN:         "taken",
        REJ_SCORE:     "rej_score",
        REJ_HAF:       "rej_haf",
        REJ_INTERNALS: "rej_internals",
        REJ_ELITE:     "rej_elite",
        REJ_HEAT:      "rej_heat",
        REJ_ORDER_FAIL:"rej_other",
        REJ_OTHER:     "rej_other",
    }
    col = col_map.get(outcome, "rej_other")
    with _conn() as con:
        con.execute(
            "INSERT INTO daily_summary(date_et) VALUES(?) ON CONFLICT(date_et) DO NOTHING",
            (date,)
        )
        con.execute(
            f"UPDATE daily_summary SET total_scanned=total_scanned+1, {col}={col}+1 WHERE date_et=?",
            (date,)
        )


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_today_stats(date_et: Optional[str] = None) -> Dict:
    if not date_et:
        _, date_et, _ = _now_et()
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM daily_summary WHERE date_et=?", (date_et,)
        ).fetchone()
        if not row:
            return {}
        d = dict(row)
        taken = d.get("taken", 0)
        total = d.get("total_scanned", 0)
        wins  = d.get("wins", 0)
        losses = d.get("losses", 0)
        d["pass_rate_pct"] = round(taken / max(total, 1) * 100, 1)
        d["win_rate_pct"]  = round(wins / max(wins + losses, 1) * 100, 1)
        return d


def get_top_rejection_reasons(date_et: Optional[str] = None, limit: int = 5) -> List[Dict]:
    """Return top rejection reasons for a day with counts."""
    if not date_et:
        _, date_et, _ = _now_et()
    with _conn() as con:
        rows = con.execute("""
            SELECT rejection_stage, rejection_reason, COUNT(*) as cnt
            FROM signal_decisions
            WHERE date_et=? AND outcome != 'TAKEN'
            GROUP BY rejection_stage, rejection_reason
            ORDER BY cnt DESC
            LIMIT ?
        """, (date_et, limit)).fetchall()
        return [dict(r) for r in rows]


def get_today_trades(date_et: Optional[str] = None) -> List[Dict]:
    if not date_et:
        _, date_et, _ = _now_et()
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM trade_outcomes WHERE date_et=? ORDER BY ts_et",
            (date_et,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_gate_block_rates(days: int = 5) -> Dict:
    """Percentage each gate is responsible for, averaged over N days."""
    with _conn() as con:
        row = con.execute("""
            SELECT
                SUM(rej_score)     as score,
                SUM(rej_haf)       as haf,
                SUM(rej_internals) as internals,
                SUM(rej_elite)     as elite,
                SUM(rej_heat)      as heat,
                SUM(rej_other)     as other,
                SUM(total_scanned) as total
            FROM daily_summary
            ORDER BY date_et DESC
            LIMIT ?
        """, (days,)).fetchone()
        if not row or not row["total"]:
            return {}
        total = row["total"]
        return {
            "score":     round(row["score"] / total * 100, 1),
            "haf":       round(row["haf"] / total * 100, 1),
            "internals": round(row["internals"] / total * 100, 1),
            "elite":     round(row["elite"] / total * 100, 1),
            "heat":      round(row["heat"] / total * 100, 1),
        }


def build_daily_report(date_et: Optional[str] = None) -> str:
    """Build a human-readable daily Telegram report."""
    if not date_et:
        _, date_et, _ = _now_et()

    stats  = get_today_stats(date_et)
    rejections = get_top_rejection_reasons(date_et, limit=6)
    trades = get_today_trades(date_et)

    if not stats:
        return f"No data logged for {date_et}"

    total   = stats.get("total_scanned", 0)
    taken   = stats.get("taken", 0)
    wins    = stats.get("wins", 0)
    losses  = stats.get("losses", 0)
    pnl     = stats.get("total_pnl", 0)
    pass_r  = stats.get("pass_rate_pct", 0)
    wr      = stats.get("win_rate_pct", 0)
    pnl_sign = "+" if pnl >= 0 else ""

    lines = [
        f"📊 <b>TRADE DECISION LOG — {date_et}</b>",
        f"{'─'*35}",
        f"🔍 Signals scanned: <b>{total}</b>",
        f"✅ Trades taken:    <b>{taken}</b>  ({pass_r:.1f}% pass rate)",
        f"❌ Signals rejected: <b>{total - taken}</b>",
        "",
    ]

    # Rejection breakdown
    rej_map = {
        "SCORE_GATE":   (stats.get("rej_score", 0),     "Score too low"),
        "HAF_GATE":     (stats.get("rej_haf", 0),       "HAF filter"),
        "INTERNALS_GATE":(stats.get("rej_internals", 0),"Market internals"),
        "ELITE_BRAIN":  (stats.get("rej_elite", 0),     "Elite Brain"),
        "HEAT_GUARD":   (stats.get("rej_heat", 0),      "Portfolio heat"),
    }
    rej_total = sum(v for v, _ in rej_map.values())
    if rej_total > 0:
        lines.append("<b>Why signals were rejected:</b>")
        for stage, (cnt, label) in sorted(rej_map.items(), key=lambda x: -x[1][0]):
            if cnt > 0:
                pct = cnt / max(total - taken, 1) * 100
                bar = "█" * min(int(pct / 10), 10)
                lines.append(f"  {bar} {label}: {cnt} ({pct:.0f}%)")
        lines.append("")

    # Top specific reasons
    if rejections:
        lines.append("<b>Top specific rejection reasons:</b>")
        for i, r in enumerate(rejections[:4], 1):
            reason = (r.get("rejection_reason") or r.get("rejection_stage") or "")[:60]
            lines.append(f"  {i}. {reason} ({r['cnt']}×)")
        lines.append("")

    # Trades taken
    if trades:
        lines.append("<b>Trades taken today:</b>")
        for t in trades:
            sym   = t.get("symbol", "?")
            dirn  = t.get("direction", "?")
            ep    = t.get("entry_price", 0)
            xp    = t.get("exit_price", 0)
            tpnl  = t.get("pnl") or 0
            xr    = t.get("exit_reason", "")
            icon  = "✅" if tpnl > 0 else "❌"
            pnl_s = f"{'+' if tpnl >= 0 else ''}{tpnl:.2f}"
            lines.append(
                f"  {icon} {sym} {dirn} ${ep:.2f}→${xp:.2f} | P&L: ${pnl_s} | {xr}"
            )
        lines.append("")

    # P&L summary
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    lines.append(f"{pnl_emoji} <b>Day P&L: {pnl_sign}${pnl:.2f}</b>")
    lines.append(f"🎯 Win rate: {wr:.0f}%  W:{wins} / L:{losses}")

    return "\n".join(lines)


# Initialise DB on import
try:
    init_db()
except Exception as _e:
    logger.warning(f"decision_log DB init failed: {_e}")
