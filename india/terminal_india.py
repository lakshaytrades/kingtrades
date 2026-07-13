"""
terminal_india.py — SATA-BLOOMBERG NSE Momentum Bot Terminal Dashboard

Bloomberg LAUNCHPAD-style rich terminal UI for the India intraday bot.
Auto-refreshes every 3 seconds showing all key bot state in one screen.

Usage:
    python3 india/terminal_india.py           # live mode (reads real files)
    python3 india/terminal_india.py --demo    # demo mode (synthesised data)
    python3 india/terminal_india.py --once    # render once and exit

Press Ctrl+C to exit cleanly.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, date, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Path bootstrap — allow running from repo root or india/ sub-directory
# ---------------------------------------------------------------------------
_BASE = Path(__file__).parent.parent
_INDIA = Path(__file__).parent
sys.path.insert(0, str(_BASE))
sys.path.insert(0, str(_INDIA))

# ---------------------------------------------------------------------------
# Rich imports
# ---------------------------------------------------------------------------
try:
    from rich import box
    from rich.align import Align
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    HAS_RICH = True
except ImportError:  # pragma: no cover
    HAS_RICH = False

# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------
IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN_IST  = time(9, 15)
MARKET_CLOSE_IST = time(15, 30)
PRE_MARKET_START = time(9, 0)

# ---------------------------------------------------------------------------
# Colour palette (Bloomberg-ish: black bg, green/red/cyan/yellow)
# ---------------------------------------------------------------------------
C_GREEN  = "bright_green"
C_RED    = "bright_red"
C_YELLOW = "bright_yellow"
C_CYAN   = "cyan"
C_WHITE  = "white"
C_DIM    = "dim white"
C_GOLD   = "yellow"
C_HEAD   = "bold cyan"

# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------
_DATA_DIR = _BASE / "data"
_LOG_DIR  = _BASE / "logs"

_POSITIONS_FILE = _DATA_DIR / "india_positions.json"
_SIGNALS_FILE   = _DATA_DIR / "india_signals.json"
_TRADES_FILE    = _DATA_DIR / "india_trade_decisions.json"
_FII_DII_FILE   = _DATA_DIR / "fii_dii_cache.json"
_MARKET_FILE    = _DATA_DIR / "india_market_overview.json"
_PCR_FILE       = _DATA_DIR / "india_pcr_cache.json"


# ===========================================================================
# Helpers
# ===========================================================================

def _now_ist() -> datetime:
    """Return current time in IST (always timezone-aware)."""
    return datetime.now(IST)


def _ist_str(fmt: str = "%H:%M:%S IST") -> str:
    return _now_ist().strftime(fmt)


def _market_status() -> str:
    now = _now_ist().time()
    if MARKET_OPEN_IST <= now <= MARKET_CLOSE_IST:
        return "OPEN"
    if PRE_MARKET_START <= now < MARKET_OPEN_IST:
        return "PRE-MARKET"
    return "CLOSED"


def _today_log_path() -> Path:
    return _LOG_DIR / f"india_{date.today().strftime('%Y-%m-%d')}.log"


def _read_json(path: Path) -> Any:
    """Read JSON file; return empty dict on any failure."""
    try:
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _read_json_list(path: Path) -> List[Any]:
    """Read JSON file expected to be a list; return [] on failure."""
    try:
        if not path.exists():
            return []
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _grade_color(grade: str) -> str:
    g = (grade or "").upper()
    if g == "A+":
        return "bright_green"
    if g == "A":
        return "green"
    if g == "B":
        return "yellow"
    return "dim"  # C or unknown


def _pnl_color(val: float) -> str:
    if val > 0:
        return C_GREEN
    if val < 0:
        return C_RED
    return C_DIM


def _mini_bar(value: float, max_val: float, width: int = 14) -> str:
    if max_val <= 0:
        return "░" * width
    frac = min(1.0, max(0.0, value / max_val))
    filled = round(frac * width)
    return "█" * filled + "░" * (width - filled)


# ===========================================================================
# Demo data synthesiser
# ===========================================================================

def _demo_positions() -> List[Dict]:
    return [
        {
            "symbol": "RELIANCE", "direction": "LONG", "entry_price": 2845.50,
            "ltp": 2872.30, "pnl": 1608.0, "pnl_pct": 0.94,
            "stop_loss": 2803.00, "target_1": 2900.00, "grade": "A+",
            "age_min": 47,
        },
        {
            "symbol": "INFY", "direction": "LONG", "entry_price": 1530.75,
            "ltp": 1518.40, "pnl": -617.5, "pnl_pct": -0.81,
            "stop_loss": 1510.00, "target_1": 1565.00, "grade": "A",
            "age_min": 22,
        },
        {
            "symbol": "HDFCBANK", "direction": "SHORT", "entry_price": 1685.00,
            "ltp": 1671.60, "pnl": 938.0, "pnl_pct": 0.80,
            "stop_loss": 1705.00, "target_1": 1648.00, "grade": "B",
            "age_min": 63,
        },
        {
            "symbol": "TATASTEEL", "direction": "LONG", "entry_price": 152.40,
            "ltp": 155.85, "pnl": 2415.0, "pnl_pct": 2.26,
            "stop_loss": 148.50, "target_1": 157.50, "grade": "A+",
            "age_min": 91,
        },
    ]


def _demo_signals() -> List[Dict]:
    now_ist = _now_ist()
    return [
        {"time": (now_ist - timedelta(minutes=3)).strftime("%H:%M"),
         "symbol": "BAJFINANCE", "direction": "LONG", "score": 84.2,
         "grade": "A+", "status": "EXECUTED"},
        {"time": (now_ist - timedelta(minutes=9)).strftime("%H:%M"),
         "symbol": "WIPRO", "direction": "SHORT", "score": 71.5,
         "grade": "A", "status": "PASSED"},
        {"time": (now_ist - timedelta(minutes=14)).strftime("%H:%M"),
         "symbol": "SBIN", "direction": "LONG", "score": 65.8,
         "grade": "B", "status": "EXECUTED"},
        {"time": (now_ist - timedelta(minutes=21)).strftime("%H:%M"),
         "symbol": "TITAN", "direction": "LONG", "score": 58.1,
         "grade": "C", "status": "REJECTED"},
        {"time": (now_ist - timedelta(minutes=28)).strftime("%H:%M"),
         "symbol": "LT", "direction": "SHORT", "score": 73.4,
         "grade": "A", "status": "EXECUTED"},
        {"time": (now_ist - timedelta(minutes=36)).strftime("%H:%M"),
         "symbol": "AXISBANK", "direction": "LONG", "score": 88.9,
         "grade": "A+", "status": "EXECUTED"},
        {"time": (now_ist - timedelta(minutes=44)).strftime("%H:%M"),
         "symbol": "MARUTI", "direction": "SHORT", "score": 62.0,
         "grade": "B", "status": "PASSED"},
        {"time": (now_ist - timedelta(minutes=55)).strftime("%H:%M"),
         "symbol": "TATAPOWER", "direction": "LONG", "score": 54.7,
         "grade": "C", "status": "REJECTED"},
        {"time": (now_ist - timedelta(minutes=67)).strftime("%H:%M"),
         "symbol": "HINDALCO", "direction": "LONG", "score": 77.3,
         "grade": "A", "status": "EXECUTED"},
        {"time": (now_ist - timedelta(minutes=79)).strftime("%H:%M"),
         "symbol": "ONGC", "direction": "SHORT", "score": 69.1,
         "grade": "B", "status": "PASSED"},
    ]


def _demo_market() -> Dict:
    return {
        "nifty_level": 24318.45,
        "nifty_change_pct": 0.48,
        "nifty_trend": "BULLISH",
        "vix_level": 14.72,
        "vix_regime": "NORMAL",
        "advances": 1342,
        "declines": 685,
        "unchanged": 173,
        "fii_net_cr": 1245.8,
        "dii_net_cr": -387.4,
        "pcr_chg_oi": 1.18,
        "connected": True,
    }


def _demo_pnl() -> Dict:
    return {
        "gross_pnl": 4344.0,
        "net_pnl": 3921.5,
        "wins": 5,
        "losses": 2,
        "best_symbol": "TATASTEEL",
        "best_pnl": 2415.0,
        "worst_symbol": "INFY",
        "worst_pnl": -617.5,
        "capital": 500000.0,
        "starting_capital": 500000.0,
        "monthly_pnl_pct": 3.2,
        "monthly_target_pct": 10.0,
    }


def _demo_logs() -> List[str]:
    now_ist = _now_ist()
    return [
        f"[{(now_ist - timedelta(seconds=8)).strftime('%H:%M:%S')}] [INFO] [signal_india] A+ signal: BAJFINANCE LONG score=84.2",
        f"[{(now_ist - timedelta(seconds=22)).strftime('%H:%M:%S')}] [INFO] [execution] Order placed BAJFINANCE 35 qty @ MKT",
        f"[{(now_ist - timedelta(seconds=55)).strftime('%H:%M:%S')}] [INFO] [risk_india] Portfolio heat=2.8% within limits",
        f"[{(now_ist - timedelta(seconds=91)).strftime('%H:%M:%S')}] [INFO] [signal_india] Score gate fail: TITAN 58.1 < 67.0",
        f"[{(now_ist - timedelta(seconds=134)).strftime('%H:%M:%S')}] [INFO] [main_india] Scan complete — 3 signals evaluated",
        f"[{(now_ist - timedelta(seconds=178)).strftime('%H:%M:%S')}] [INFO] [fii_dii] FII net +1245.8 Cr -> BULLISH bias applied",
        f"[{(now_ist - timedelta(seconds=220)).strftime('%H:%M:%S')}] [INFO] [vix_regime] India VIX 14.72 -> NORMAL regime",
        f"[{(now_ist - timedelta(seconds=270)).strftime('%H:%M:%S')}] [INFO] [main_india] Market OPEN -- trading window active",
    ]


# ===========================================================================
# Live data readers
# ===========================================================================

def _load_positions() -> List[Dict]:
    data = _read_json(_POSITIONS_FILE)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return list(data.values())
    return []


def _load_signals() -> List[Dict]:
    data = _read_json_list(_SIGNALS_FILE)
    return data[-10:] if len(data) > 10 else data


def _load_market() -> Dict:
    market: Dict = _read_json(_MARKET_FILE)
    fii_dii = _read_json(_FII_DII_FILE)
    pcr = _read_json(_PCR_FILE)
    if fii_dii:
        market.setdefault("fii_net_cr", fii_dii.get("fii_net_cr"))
        market.setdefault("dii_net_cr", fii_dii.get("dii_net_cr"))
    if pcr:
        market.setdefault("pcr_chg_oi", pcr.get("pcr_chg_oi"))
    return market


def _load_pnl() -> Dict:
    trades = _read_json_list(_TRADES_FILE)
    if not trades:
        return {}
    wins   = [t for t in trades if t.get("pnl", 0) > 0]
    losses = [t for t in trades if t.get("pnl", 0) < 0]
    gross  = sum(t.get("pnl", 0) for t in trades)
    # Rough transaction cost: 0.05% per trade (brokerage + taxes)
    costs  = sum(
        abs(t.get("entry_price", 0) * t.get("quantity", 1))
        for t in trades
    ) * 0.0005
    best  = max(trades, key=lambda t: t.get("pnl", 0), default={})
    worst = min(trades, key=lambda t: t.get("pnl", 0), default={})
    try:
        from config_india import MAX_DAILY_CAPITAL, MONTHLY_TARGET_PCT
        capital           = MAX_DAILY_CAPITAL
        monthly_target_pct = MONTHLY_TARGET_PCT
    except Exception:
        capital            = 500000.0
        monthly_target_pct = 10.0
    return {
        "gross_pnl":          gross,
        "net_pnl":            gross - costs,
        "wins":               len(wins),
        "losses":             len(losses),
        "best_symbol":        best.get("symbol", "—"),
        "best_pnl":           best.get("pnl", 0.0),
        "worst_symbol":       worst.get("symbol", "—"),
        "worst_pnl":          worst.get("pnl", 0.0),
        "capital":            capital,
        "starting_capital":   capital,
        "monthly_pnl_pct":    gross / capital * 100 if capital > 0 else 0.0,
        "monthly_target_pct": monthly_target_pct,
    }


def _load_logs() -> List[str]:
    lines: List[str] = []
    try:
        log_path = _today_log_path()
        if not log_path.exists():
            return []
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
        for line in reversed(all_lines):
            stripped = line.rstrip()
            if stripped:
                lines.append(stripped)
            if len(lines) >= 8:
                break
        lines.reverse()
    except Exception:
        pass
    return lines


# ===========================================================================
# Panel builders
# ===========================================================================

def _build_header(connected: bool = True) -> Panel:
    """Top header bar: title, IST time, market status, connectivity."""
    now_str    = _ist_str("%Y-%m-%d  %H:%M:%S  IST")
    status     = _market_status()
    status_col = C_GREEN if status == "OPEN" else (C_YELLOW if status == "PRE-MARKET" else C_RED)
    conn_label = "Upstox CONNECTED" if connected else "Upstox OFFLINE"
    conn_color = "bright_green" if connected else "bright_red"

    t = Text()
    t.append("  SATA-BLOOMBERG  ", style="bold white on dark_blue")
    t.append("  NSE MOMENTUM BOT  ", style="bold cyan")
    t.append(f"  {now_str}  ", style=C_GOLD)
    t.append("  Market: ", style=C_DIM)
    t.append(f"{status}  ", style=status_col)
    t.append(f"  [{conn_label}]", style=conn_color)

    return Panel(
        Align.left(t),
        style="bold",
        box=box.HEAVY,
        padding=(0, 0),
    )


def _build_positions_panel(positions: List[Dict]) -> Panel:
    """Panel 1 (top-left): open positions table."""
    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style=C_HEAD,
        expand=True,
    )
    table.add_column("SYMBOL",  style="bold white",  width=10)
    table.add_column("DIR",     style=C_CYAN,        width=6)
    table.add_column("ENTRY",   justify="right",     width=9)
    table.add_column("LTP",     justify="right",     width=9)
    table.add_column("P&L Rs",  justify="right",     width=11)
    table.add_column("P&L%",    justify="right",     width=7)
    table.add_column("SL",      justify="right",     width=9)
    table.add_column("T1",      justify="right",     width=9)
    table.add_column("GRADE",   justify="center",    width=6)
    table.add_column("AGE(m)",  justify="right",     width=7)

    if not positions:
        table.add_row(
            Text("—", style=C_DIM),
            Text("—", style=C_DIM),
            Text("—", style=C_DIM),
            Text("—", style=C_DIM),
            Text("No open positions", style=C_DIM),
            Text("—", style=C_DIM),
            Text("—", style=C_DIM),
            Text("—", style=C_DIM),
            Text("—", style=C_DIM),
            Text("—", style=C_DIM),
        )
    else:
        for pos in positions[:8]:
            pnl       = float(pos.get("pnl", 0.0))
            pnl_pct   = float(pos.get("pnl_pct", 0.0))
            grade     = str(pos.get("grade", "B"))
            pnl_col   = _pnl_color(pnl)
            pct_col   = _pnl_color(pnl_pct)
            entry     = float(pos.get("entry_price", pos.get("entry", 0.0)))
            ltp       = float(pos.get("ltp", pos.get("current", 0.0)))
            sl        = float(pos.get("stop_loss", pos.get("stop", 0.0)))
            t1        = float(pos.get("target_1", pos.get("target1", 0.0)))
            age       = int(pos.get("age_min", 0))
            direction = str(pos.get("direction", "—"))
            dir_col   = C_GREEN if direction == "LONG" else C_RED

            table.add_row(
                str(pos.get("symbol", "—")),
                Text(direction, style=dir_col),
                f"Rs{entry:,.2f}",
                f"Rs{ltp:,.2f}",
                Text(f"{'+'if pnl>=0 else ''}{pnl:,.0f}", style=pnl_col),
                Text(f"{pnl_pct:+.2f}%", style=pct_col),
                f"Rs{sl:,.2f}",
                f"Rs{t1:,.2f}",
                Text(grade, style=_grade_color(grade)),
                str(age),
            )

    return Panel(
        table,
        title="[bold cyan]OPEN POSITIONS[/bold cyan]",
        box=box.HEAVY,
        border_style="cyan",
    )


def _build_signals_panel(signals: List[Dict]) -> Panel:
    """Panel 2 (top-right): recent signals queue."""
    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style=C_HEAD,
        expand=True,
    )
    table.add_column("TIME",   width=7)
    table.add_column("SYMBOL", style="bold white", width=12)
    table.add_column("DIR",    width=6)
    table.add_column("SCORE",  justify="right", width=7)
    table.add_column("GRADE",  justify="center", width=6)
    table.add_column("STATUS", width=10)

    if not signals:
        table.add_row(
            "—",
            Text("No signals yet", style=C_DIM),
            "—", "—", "—", "—",
        )
    else:
        for sig in signals:
            grade     = str(sig.get("grade", "B"))
            score     = float(sig.get("score", sig.get("signal_score", 0.0)))
            status    = str(sig.get("status", "—"))
            direction = str(sig.get("direction", "—"))
            dir_col   = C_GREEN if direction == "LONG" else C_RED
            st_col    = (
                C_GREEN  if status == "EXECUTED"
                else (C_YELLOW if status == "PASSED" else C_DIM)
            )
            table.add_row(
                str(sig.get("time", sig.get("signal_time", "—"))[:5]),
                str(sig.get("symbol", "—")),
                Text(direction, style=dir_col),
                f"{score:.1f}",
                Text(grade, style=_grade_color(grade)),
                Text(status, style=st_col),
            )

    return Panel(
        table,
        title="[bold cyan]SIGNALS QUEUE[/bold cyan]",
        box=box.HEAVY,
        border_style="cyan",
    )


def _build_market_panel(market: Dict) -> Panel:
    """Panel 3 (mid-left): market overview."""
    nifty_lvl   = market.get("nifty_level")
    nifty_chg   = market.get("nifty_change_pct")
    nifty_trend = market.get("nifty_trend", "—")
    vix_lvl     = market.get("vix_level")
    vix_regime  = market.get("vix_regime", "—")
    advances    = market.get("advances")
    declines    = market.get("declines")
    unchanged   = market.get("unchanged")
    fii_net     = market.get("fii_net_cr")
    dii_net     = market.get("dii_net_cr")
    pcr         = market.get("pcr_chg_oi")

    # Nifty color/arrow
    if nifty_chg is not None:
        nc          = float(nifty_chg)
        nifty_arrow = "^ " if nc >= 0 else "v "
        nifty_col   = C_GREEN if nc >= 0 else C_RED
    else:
        nifty_arrow, nifty_col, nc = "  ", C_DIM, 0.0

    # VIX color
    if vix_lvl is not None:
        v = float(vix_lvl)
        vix_col = C_RED if v > 22 else (C_YELLOW if v > 16 else C_GREEN)
        if vix_regime == "—":
            vix_regime = "HIGH" if v > 22 else ("NORMAL" if v > 12 else "LOW")
    else:
        vix_col = C_DIM

    fii_col = C_GREEN if (fii_net is not None and float(fii_net) > 0) else C_RED
    dii_col = C_GREEN if (dii_net is not None and float(dii_net) > 0) else C_RED

    t = Text()
    t.append("  NIFTY 50:  ", style=C_DIM)
    if nifty_lvl is not None:
        t.append(f"{float(nifty_lvl):,.2f}  {nifty_arrow}{nc:+.2f}%", style=nifty_col)
    else:
        t.append("—", style=C_DIM)
    if nifty_trend != "—":
        trend_col = C_GREEN if "BULL" in nifty_trend.upper() else C_RED
        t.append(f"  [{nifty_trend}]", style=trend_col)
    t.append("\n")

    t.append("  India VIX: ", style=C_DIM)
    if vix_lvl is not None:
        t.append(f"{float(vix_lvl):.2f}  {vix_regime}", style=vix_col)
    else:
        t.append("—", style=C_DIM)
    t.append("\n")

    t.append("  Breadth:   ", style=C_DIM)
    if advances is not None and declines is not None:
        t.append(f"Adv {int(advances)}", style=C_GREEN)
        t.append(f"  Dec {int(declines)}", style=C_RED)
        if unchanged is not None:
            t.append(f"  Unch {int(unchanged)}", style=C_DIM)
    else:
        t.append("—", style=C_DIM)
    t.append("\n")

    t.append("  FII:       ", style=C_DIM)
    if fii_net is not None:
        t.append(f"Rs{float(fii_net):+,.1f} Cr", style=fii_col)
    else:
        t.append("—", style=C_DIM)
    t.append("\n")

    t.append("  DII:       ", style=C_DIM)
    if dii_net is not None:
        t.append(f"Rs{float(dii_net):+,.1f} Cr", style=dii_col)
    else:
        t.append("—", style=C_DIM)
    t.append("\n")

    t.append("  PCR(ChgOI):", style=C_DIM)
    if pcr is not None:
        p = float(pcr)
        pcr_col = C_GREEN if p > 1.2 else (C_RED if p < 0.8 else C_YELLOW)
        t.append(f" {p:.2f}", style=pcr_col)
    else:
        t.append(" —", style=C_DIM)

    return Panel(
        t,
        title="[bold cyan]MARKET OVERVIEW[/bold cyan]",
        box=box.HEAVY,
        border_style="cyan",
    )


def _build_pnl_panel(pnl: Dict) -> Panel:
    """Panel 4 (mid-right): today's P&L."""
    gross   = float(pnl.get("gross_pnl", 0.0))
    net     = float(pnl.get("net_pnl", 0.0))
    wins    = int(pnl.get("wins", 0))
    losses  = int(pnl.get("losses", 0))
    total   = wins + losses
    wr      = (wins / total * 100) if total > 0 else 0.0
    best_s  = str(pnl.get("best_symbol", "—"))
    best_p  = float(pnl.get("best_pnl", 0.0))
    worst_s = str(pnl.get("worst_symbol", "—"))
    worst_p = float(pnl.get("worst_pnl", 0.0))
    capital = float(pnl.get("capital", 500000.0))
    mo_pct  = float(pnl.get("monthly_pnl_pct", 0.0))
    mo_tgt  = float(pnl.get("monthly_target_pct", 10.0))
    mo_prog = min(100.0, (mo_pct / mo_tgt * 100)) if mo_tgt > 0 else 0.0

    gross_col = _pnl_color(gross)
    net_col   = _pnl_color(net)
    wr_col    = C_GREEN if wr >= 55 else (C_YELLOW if wr >= 45 else C_RED)

    t = Text()
    t.append("  Gross P&L:  ", style=C_DIM)
    t.append(f"{'+'if gross>=0 else ''}Rs{gross:,.0f}\n", style=gross_col)
    t.append("  Net P&L:    ", style=C_DIM)
    t.append(f"{'+'if net>=0 else ''}Rs{net:,.0f}\n", style=net_col)
    t.append("  ────────────────────────\n", style=C_DIM)
    t.append("  Trades:     ", style=C_DIM)
    t.append(f"{wins}W", style=C_GREEN)
    t.append(" / ", style=C_DIM)
    t.append(f"{losses}L", style=C_RED)
    t.append(f"   WR: ", style=C_DIM)
    t.append(f"{wr:.0f}%\n", style=wr_col)
    t.append("  Best:       ", style=C_DIM)
    t.append(f"{best_s}  +Rs{best_p:,.0f}\n", style=C_GREEN)
    t.append("  Worst:      ", style=C_DIM)
    t.append(f"{worst_s}  Rs{worst_p:,.0f}\n", style=C_RED)
    t.append("  ────────────────────────\n", style=C_DIM)
    t.append("  Capital:    ", style=C_DIM)
    t.append(f"Rs{capital:,.0f}\n", style=C_WHITE)
    # Monthly progress bar
    bar     = _mini_bar(mo_pct, mo_tgt, 14)
    bar_col = C_GREEN if mo_prog >= 100 else (C_YELLOW if mo_prog >= 50 else C_DIM)
    t.append("  Monthly:    ", style=C_DIM)
    t.append(f"[{bar}] {mo_pct:.2f}% / {mo_tgt:.0f}%\n", style=bar_col)
    if mo_prog >= 100:
        t.append("  MONTHLY TARGET ACHIEVED!\n", style="bold green")

    return Panel(
        t,
        title="[bold cyan]TODAY'S P&L[/bold cyan]",
        box=box.HEAVY,
        border_style="cyan",
    )


def _build_log_panel(log_lines: List[str]) -> Panel:
    """Panel 5 (bottom): recent log lines."""
    t = Text()
    for line in log_lines:
        if "[ERROR]" in line or "[CRITICAL]" in line:
            t.append(line + "\n", style=C_RED)
        elif "[WARNING]" in line or "[WARN]" in line:
            t.append(line + "\n", style=C_YELLOW)
        else:
            t.append(line + "\n", style=C_DIM)
    if not log_lines:
        t.append("  No log entries for today.", style=C_DIM)

    return Panel(
        t,
        title="[bold cyan]LOG / NEWS TICKER[/bold cyan]",
        box=box.HEAVY,
        border_style="cyan",
    )


# ===========================================================================
# Layout builder — public API
# ===========================================================================

def build_layout(demo: bool = False) -> "Layout":
    """
    Build and return the full terminal layout renderable.

    Parameters
    ----------
    demo : bool
        If True, populate all panels with synthesised demo data.

    Returns
    -------
    rich.layout.Layout
        A fully-populated layout ready to be rendered.
    """
    # ── load data ─────────────────────────────────────────────────────────────
    if demo:
        positions = _demo_positions()
        signals   = _demo_signals()
        market    = _demo_market()
        pnl       = _demo_pnl()
        log_lines = _demo_logs()
        connected = True
    else:
        positions = _load_positions()
        signals   = _load_signals()
        market    = _load_market()
        pnl       = _load_pnl()
        log_lines = _load_logs()
        connected = bool(market.get("connected"))

    # ── build panels ─────────────────────────────────────────────────────────
    header  = _build_header(connected=connected)
    pos_pnl = _build_positions_panel(positions)
    sig_pnl = _build_signals_panel(signals)
    mkt_pnl = _build_market_panel(market)
    day_pnl = _build_pnl_panel(pnl)
    log_pnl = _build_log_panel(log_lines)

    # ── assemble layout ───────────────────────────────────────────────────────
    layout = Layout()

    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="top",    ratio=4),
        Layout(name="middle", ratio=3),
        Layout(name="bottom", ratio=2),
    )

    layout["top"].split_row(
        Layout(name="positions", ratio=6),
        Layout(name="signals",   ratio=4),
    )

    layout["middle"].split_row(
        Layout(name="market", ratio=5),
        Layout(name="pnl",    ratio=5),
    )

    layout["header"].update(header)
    layout["positions"].update(pos_pnl)
    layout["signals"].update(sig_pnl)
    layout["market"].update(mkt_pnl)
    layout["pnl"].update(day_pnl)
    layout["bottom"].update(log_pnl)

    return layout


# ===========================================================================
# run_dashboard — public API
# ===========================================================================

def run_dashboard(demo: bool = False, once: bool = False) -> None:
    """
    Launch the live dashboard.

    Parameters
    ----------
    demo : bool
        If True, use synthesised data (no file reads).
    once : bool
        If True, render once and exit (useful for piping / --once flag).
    """
    if not HAS_RICH:  # pragma: no cover
        print("ERROR: 'rich' library not installed. Run: pip install rich", file=sys.stderr)
        sys.exit(1)

    console = Console()

    if once:
        console.print(build_layout(demo=demo))
        return

    try:
        with Live(
            build_layout(demo=demo),
            refresh_per_second=0.33,
            screen=True,
            console=console,
        ) as live:
            import time as _time_mod
            while True:
                _time_mod.sleep(3)
                live.update(build_layout(demo=demo))
    except KeyboardInterrupt:
        console.print("\n[dim]Dashboard stopped.[/dim]")


# ===========================================================================
# CLI entry point
# ===========================================================================

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SATA-BLOOMBERG NSE Momentum Bot Terminal Dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 india/terminal_india.py               # live mode\n"
            "  python3 india/terminal_india.py --demo        # demo mode\n"
            "  python3 india/terminal_india.py --once --demo # snapshot and exit\n"
        ),
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Use synthesised demo data (no file reads needed)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Render once and exit (for piping or non-interactive mode)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_dashboard(demo=args.demo, once=args.once)
