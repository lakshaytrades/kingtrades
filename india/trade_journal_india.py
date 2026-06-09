"""
trade_journal_india.py — Persistent SQLite trade log for KingTrades India Bot.

Records every closed trade and provides daily/weekly/monthly P&L summaries
sent via Telegram on demand (/monthly, /weekly) or automatically on the 1st
of each month at startup.
"""
import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger("trade_journal")

_BASE   = Path(__file__).parent.parent
_DB_DIR = _BASE / "logs"
_DB_DIR.mkdir(exist_ok=True)
DB_PATH = _DB_DIR / "india_trades.db"


@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _init_db():
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date  TEXT    NOT NULL,          -- YYYY-MM-DD IST
                symbol      TEXT    NOT NULL,
                direction   TEXT    NOT NULL,          -- LONG / SHORT
                entry_price REAL    NOT NULL DEFAULT 0,
                exit_price  REAL    NOT NULL DEFAULT 0,
                quantity    INTEGER NOT NULL DEFAULT 0,
                pnl         REAL    NOT NULL DEFAULT 0, -- INR
                score       REAL,
                grade       TEXT,
                closed_at   TEXT    NOT NULL           -- ISO IST timestamp
            )
        """)


_init_db()


def record_trade(
    symbol:      str,
    direction:   str,
    entry_price: float,
    exit_price:  float,
    quantity:    int,
    pnl:         float,
    score:       float = 0.0,
    grade:       str   = "",
    closed_at:   Optional[datetime] = None,
):
    """Insert one closed trade into the journal."""
    try:
        ts   = closed_at or datetime.now(IST)
        date_str = ts.strftime("%Y-%m-%d")
        with _conn() as con:
            con.execute(
                """INSERT INTO trades
                   (trade_date, symbol, direction, entry_price, exit_price,
                    quantity, pnl, score, grade, closed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (date_str, symbol, direction,
                 round(entry_price, 4), round(exit_price, 4),
                 int(quantity), round(pnl, 4),
                 round(score, 2), grade,
                 ts.isoformat()),
            )
    except Exception as e:
        logger.error(f"trade_journal record: {e}")


def _query_summary(where_clause: str, params: tuple) -> dict:
    """Low-level aggregation over a date range."""
    with _conn() as con:
        rows = con.execute(
            f"SELECT pnl, entry_price, quantity FROM trades WHERE {where_clause}",
            params,
        ).fetchall()

    if not rows:
        return {}

    pnls   = [r["pnl"] for r in rows]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total  = len(pnls)
    gross_win  = sum(wins)
    gross_loss = abs(sum(losses))

    return {
        "trades":        total,
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      len(wins) / total if total else 0.0,
        "total_pnl":     sum(pnls),
        "avg_win":       gross_win  / len(wins)   if wins   else 0.0,
        "avg_loss":      gross_loss / len(losses) if losses else 0.0,
        "profit_factor": gross_win  / gross_loss  if gross_loss else float("inf"),
        "best_trade":    max(pnls),
        "worst_trade":   min(pnls),
    }


def get_monthly_summary(year: int, month: int) -> dict:
    like = f"{year:04d}-{month:02d}-%"
    return _query_summary("trade_date LIKE ?", (like,))


def get_weekly_summary() -> dict:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return _query_summary(
        "trade_date >= ? AND trade_date <= ?",
        (monday.isoformat(), sunday.isoformat()),
    )


def get_daily_summary(d: Optional[date] = None) -> dict:
    d = d or date.today()
    return _query_summary("trade_date = ?", (d.isoformat(),))


def get_all_time_summary() -> dict:
    return _query_summary("1=1", ())


# --------------------------------------------------------------------------
# Telegram-formatted message builders
# --------------------------------------------------------------------------

def _bar(pct: float, width: int = 10) -> str:
    filled = max(0, min(width, round(abs(pct) * width)))
    return "█" * filled + "░" * (width - filled)


def format_monthly_report(year: int, month: int) -> str:
    import calendar
    s = get_monthly_summary(year, month)
    if not s:
        return (f"📅 {calendar.month_abbr[month]} {year} — No trades recorded yet.\n"
                f"Tip: start the bot and let it detect signals.")

    month_name = calendar.month_name[month]
    wr  = s["win_rate"]
    pf  = s["profit_factor"]
    pnl = s["total_pnl"]

    pnl_icon = "🟢" if pnl > 0 else "🔴"
    wr_icon  = "✅" if wr >= 0.55 else ("⚠️" if wr >= 0.45 else "❌")
    pf_str   = f"{pf:.2f}" if pf != float("inf") else "∞ (no losses)"

    sep = "━" * 28
    lines = [
        f"📊 MONTHLY REPORT — {month_name} {year}",
        sep,
        f"Trades: {s['trades']}  |  {s['wins']}W / {s['losses']}L",
        f"Win Rate:  {wr_icon} {wr:.0%}  [{_bar(wr)}]",
        f"Prof.Factor: {pf_str}",
        sep,
        f"Total P&L:  {pnl_icon} Rs.{pnl:+,.2f}",
        f"Avg Win:    Rs.{s['avg_win']:,.2f}",
        f"Avg Loss:   Rs.{s['avg_loss']:,.2f}",
        f"Best:       Rs.{s['best_trade']:+,.2f}",
        f"Worst:      Rs.{s['worst_trade']:+,.2f}",
        sep,
    ]

    # Edge quality commentary
    if s["trades"] < 5:
        lines.append("⚠️ Too few trades for reliable stats (need ≥ 5).")
    elif wr >= 0.57 and pf >= 1.5:
        lines.append("🏆 Strong edge — keep the same settings.")
    elif wr >= 0.50 and pf >= 1.2:
        lines.append("✅ Positive edge — consistent momentum style.")
    elif pf >= 1.0:
        lines.append("🟡 Thin edge — monitor for 2 more weeks.")
    else:
        lines.append("🔴 Negative edge this month — review signal settings.")

    # All-time context
    at = get_all_time_summary()
    if at and at["trades"] > s["trades"]:
        at_pnl = at["total_pnl"]
        lines.append(f"\nAll-time: {at['trades']} trades | "
                     f"Rs.{at_pnl:+,.0f} | WR {at['win_rate']:.0%}")

    return "\n".join(lines)


def format_weekly_report() -> str:
    today   = date.today()
    monday  = today - timedelta(days=today.weekday())
    sunday  = monday + timedelta(days=6)
    s = get_weekly_summary()
    week_label = f"{monday.strftime('%d %b')} – {sunday.strftime('%d %b %Y')}"

    if not s:
        return f"📅 This week ({week_label}) — No trades yet."

    wr   = s["win_rate"]
    pnl  = s["total_pnl"]
    pnl_icon = "🟢" if pnl > 0 else "🔴"
    wr_icon  = "✅" if wr >= 0.55 else ("⚠️" if wr >= 0.45 else "❌")
    pf_str   = f"{s['profit_factor']:.2f}" if s["profit_factor"] != float("inf") else "∞"

    sep = "━" * 28
    return "\n".join([
        f"📅 WEEKLY REPORT — {week_label}",
        sep,
        f"Trades: {s['trades']}  |  {s['wins']}W / {s['losses']}L",
        f"Win Rate:  {wr_icon} {wr:.0%}  [{_bar(wr)}]",
        f"Prof.Factor: {pf_str}",
        sep,
        f"Total P&L: {pnl_icon} Rs.{pnl:+,.2f}",
        f"Avg Win:   Rs.{s['avg_win']:,.2f}",
        f"Avg Loss:  Rs.{s['avg_loss']:,.2f}",
        sep,
    ])


def maybe_send_monthly_summary(tg_fn) -> None:
    """
    Call at bot startup. If today is the 1st of the month, send last month's
    full report automatically so Lakshay gets the summary without asking.
    """
    try:
        now = datetime.now(IST)
        if now.day != 1:
            return
        # Last month
        first_of_this_month = now.date().replace(day=1)
        last_month_end  = first_of_this_month - timedelta(days=1)
        year  = last_month_end.year
        month = last_month_end.month
        report = format_monthly_report(year, month)
        tg_fn(f"📬 Auto-Monthly Summary\n{report}")
    except Exception as e:
        logger.debug(f"maybe_send_monthly_summary: {e}")
