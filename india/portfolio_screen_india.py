"""
portfolio_screen_india.py — Bloomberg PORT-equivalent Live Portfolio Risk Analytics

Bloomberg PORT/RISK equivalent for NSE intraday trading.
Renders: positions table, risk metrics, equity sparkline, Kelly fractions,
         correlation warnings.

All timestamps in IST (Asia/Kolkata). Fail-open on missing data files.

Usage:
    python3 india/portfolio_screen_india.py           # live
    python3 india/portfolio_screen_india.py --demo    # demo data
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Path / import setup
# ---------------------------------------------------------------------------
_BASE = Path(__file__).parent.parent
sys.path.insert(0, str(_BASE))
sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv(_BASE / ".env")
except ImportError:
    pass

try:
    import config_india as _cfg
    _CAPITAL: float = _cfg.MAX_DAILY_CAPITAL
    _DAILY_LOSS_LIMIT_PCT: float = _cfg.DAILY_LOSS_LIMIT_PCT  # already /100
    _MAX_POSITIONS: int = _cfg.MAX_POSITIONS
except Exception:
    _CAPITAL = 500_000.0
    _DAILY_LOSS_LIMIT_PCT = 0.02
    _MAX_POSITIONS = 10

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich import box

IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------
_DATA_DIR            = _BASE / "data"
_POSITIONS_FILE      = _DATA_DIR / "india_positions.json"
_TRADE_LOG_FILE      = _DATA_DIR / "india_trade_decisions.json"
_BAYES_FILE          = _DATA_DIR / "elite_bayes.json"
_CORRELATION_FILE    = _DATA_DIR / "portfolio_correlation.json"

# ---------------------------------------------------------------------------
# Spark-bar helpers
# ---------------------------------------------------------------------------
_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def _sparkline(values: List[float]) -> str:
    """Map a list of floats onto 8-level Unicode spark characters."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo
    chars: List[str] = []
    for v in values:
        if span == 0:
            idx = 3
        else:
            idx = int((v - lo) / span * (len(_SPARK_CHARS) - 1))
            idx = max(0, min(idx, len(_SPARK_CHARS) - 1))
        chars.append(_SPARK_CHARS[idx])
    return "".join(chars)


# ---------------------------------------------------------------------------
# Demo data
# ---------------------------------------------------------------------------
_DEMO_POSITIONS: Dict[str, Any] = {
    "RELIANCE": {
        "symbol": "RELIANCE",
        "direction": "LONG",
        "entry_price": 2847.5,
        "current_price": 2891.0,
        "quantity": 10,
        "stop_loss": 2801.0,
        "target_1": 2921.0,
        "target_2": 2994.5,
        "quality_grade": "A+",
        "signal_score": 87,
        "entry_time": "2026-06-13T09:47:00+05:30",
        "t1_done": False,
        "pnl": 435.0,
        "atr": 38.5,
    },
    "INFY": {
        "symbol": "INFY",
        "direction": "LONG",
        "entry_price": 1642.0,
        "current_price": 1624.5,
        "quantity": 25,
        "stop_loss": 1618.0,
        "target_1": 1690.0,
        "target_2": 1738.0,
        "quality_grade": "A",
        "signal_score": 78,
        "entry_time": "2026-06-13T09:35:00+05:30",
        "t1_done": False,
        "pnl": -437.5,
        "atr": 22.4,
    },
    "RELIANCE": {
        "symbol": "RELIANCE",
        "direction": "SHORT",
        "entry_price": 912.75,
        "current_price": 898.30,
        "quantity": 50,
        "stop_loss": 932.50,
        "target_1": 882.0,
        "target_2": 851.5,
        "quality_grade": "B+",
        "signal_score": 71,
        "entry_time": "2026-06-13T10:12:00+05:30",
        "t1_done": False,
        "pnl": 722.5,
        "atr": 15.2,
    },
}


def _demo_trade_decisions() -> List[Dict[str, Any]]:
    """Synthetic intraday equity curve snapshots for demo mode."""
    base = 500_000.0
    deltas = [0, 120, 280, 190, -80, 310, 475, 540, 620, 710, 680, 720]
    now = datetime.now(tz=IST)
    return [
        {
            "timestamp": now.replace(
                hour=9, minute=15 + i * 3, second=0, microsecond=0
            ).isoformat(),
            "cumulative_pnl": deltas[i],
            "portfolio_value": base + deltas[i],
        }
        for i in range(len(deltas))
    ]


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_positions(demo: bool = False) -> Dict[str, Any]:
    """
    Load open positions from data/india_positions.json.
    Falls back to synthetic demo data if demo=True or the file is missing / empty.
    """
    if not demo:
        try:
            raw = _POSITIONS_FILE.read_text()
            data = json.loads(raw)
            if isinstance(data, dict) and data:
                return data
        except Exception:
            pass
    return _DEMO_POSITIONS


def _load_trade_log(demo: bool = False) -> List[Dict[str, Any]]:
    """Load intraday trade decisions / equity snapshots."""
    if not demo:
        try:
            raw = _TRADE_LOG_FILE.read_text()
            data = json.loads(raw)
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    return _demo_trade_decisions()


def _load_bayes() -> Dict[str, Any]:
    """Load elite_bayes.json for historical win-rates per symbol."""
    try:
        raw = _BAYES_FILE.read_text()
        return json.loads(raw)
    except Exception:
        return {}


def _load_correlation() -> Dict[str, Any]:
    """Load portfolio_correlation.json written by portfolio_rebalancer_india."""
    try:
        raw = _CORRELATION_FILE.read_text()
        return json.loads(raw)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Calculation helpers
# ---------------------------------------------------------------------------

def _get_ist_now() -> datetime:
    return datetime.now(tz=IST)


def _position_age(entry_time_str: str) -> str:
    """Return human-readable age string since entry."""
    try:
        entry_dt = datetime.fromisoformat(entry_time_str)
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.replace(tzinfo=IST)
        delta = _get_ist_now() - entry_dt
        total_minutes = int(delta.total_seconds() / 60)
        if total_minutes < 60:
            return f"{total_minutes}m"
        hours = total_minutes // 60
        mins = total_minutes % 60
        return f"{hours}h{mins:02d}m"
    except Exception:
        return "?"


def _r_multiple(pos: Dict[str, Any]) -> float:
    """
    Compute R-multiple for a position.
    LONG:  (current - entry) / (entry - stop)
    SHORT: (entry - current) / (stop - entry)
    """
    try:
        entry = float(pos["entry_price"])
        current = float(pos["current_price"])
        stop = float(pos["stop_loss"])
        direction = str(pos.get("direction", "LONG")).upper()
        if direction == "LONG":
            risk_per_share = entry - stop
        else:
            risk_per_share = stop - entry
        if abs(risk_per_share) < 0.0001:
            return 0.0
        if direction == "LONG":
            return (current - entry) / risk_per_share
        else:
            return (entry - current) / risk_per_share
    except Exception:
        return 0.0


def _position_pnl(pos: Dict[str, Any]) -> float:
    """Return realised + unrealised P&L from the position dict."""
    try:
        # Use pre-computed pnl if present
        if "pnl" in pos:
            return float(pos["pnl"])
        entry = float(pos["entry_price"])
        current = float(pos["current_price"])
        qty = int(pos["quantity"])
        direction = str(pos.get("direction", "LONG")).upper()
        if direction == "LONG":
            return (current - entry) * qty
        else:
            return (entry - current) * qty
    except Exception:
        return 0.0


def _position_value(pos: Dict[str, Any]) -> float:
    """Deployed capital for one position (entry_price × quantity)."""
    try:
        return float(pos["entry_price"]) * int(pos["quantity"])
    except Exception:
        return 0.0


def _position_var95(pos: Dict[str, Any]) -> float:
    """
    Parametric 1-day 95% VaR for a single position.
    VaR = position_value × (atr / entry_price) × 1.645
    ATR sourced from pos['atr'] or estimated as |entry - stop| / 1.5.
    """
    try:
        entry = float(pos["entry_price"])
        qty = int(pos["quantity"])
        position_value = entry * qty

        atr = pos.get("atr")
        if atr is None or float(atr) <= 0:
            stop = float(pos["stop_loss"])
            atr = abs(entry - stop) / 1.5

        atr_pct = float(atr) / entry
        return position_value * atr_pct * 1.645
    except Exception:
        return 0.0


def _open_risk(pos: Dict[str, Any]) -> float:
    """Distance from current price to stop × quantity (unrealised risk in ₹)."""
    try:
        current = float(pos["current_price"])
        stop = float(pos["stop_loss"])
        qty = int(pos["quantity"])
        direction = str(pos.get("direction", "LONG")).upper()
        if direction == "LONG":
            return max(0.0, (current - stop) * qty)
        else:
            return max(0.0, (stop - current) * qty)
    except Exception:
        return 0.0


def _kelly_fraction(
    signal_score: float,
    win_rate: float,
    rr: float = 2.0,
) -> float:
    """
    Kelly fraction = WR - (1-WR)/RR, capped [0, 0.1].
    Expressed as a percentage of capital.
    """
    k = win_rate - (1 - win_rate) / rr
    k = max(0.0, min(k, 0.10))
    # Scale slightly by signal quality (score 60-100 → 0.8x-1.0x)
    quality_factor = 0.8 + 0.2 * max(0.0, (signal_score - 60)) / 40.0
    return k * quality_factor * 100.0  # return as %


def _historical_win_rate(symbol: str, bayes_data: Dict[str, Any]) -> float:
    """Return historical win-rate for symbol from elite_bayes.json or 65% default."""
    try:
        entry = bayes_data.get(symbol, {})
        if isinstance(entry, dict):
            wr = entry.get("win_rate") or entry.get("wr") or entry.get("winrate")
            if wr is not None:
                wr = float(wr)
                return wr if wr <= 1.0 else wr / 100.0
    except Exception:
        pass
    return 0.65


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def compute_risk_metrics(
    positions: Dict[str, Any],
    capital: float,
) -> Dict[str, Any]:
    """
    Compute Bloomberg RISK-panel metrics.

    Returns
    -------
    dict with keys:
        deployed, heat_pct, daily_pnl, daily_dd_pct,
        var_95, avg_kelly, open_risk, n_positions
    """
    deployed = sum(_position_value(p) for p in positions.values())
    heat_pct = (deployed / capital * 100.0) if capital > 0 else 0.0

    daily_pnl = sum(_position_pnl(p) for p in positions.values())
    daily_dd_pct = (abs(min(0.0, daily_pnl)) / capital * 100.0) if capital > 0 else 0.0

    var_95 = sum(_position_var95(p) for p in positions.values())

    bayes_data = _load_bayes()
    kelly_fracs: List[float] = []
    for p in positions.values():
        symbol = p.get("symbol", "")
        score = float(p.get("signal_score", 70))
        wr = _historical_win_rate(symbol, bayes_data)
        kelly_fracs.append(_kelly_fraction(score, wr))
    avg_kelly = sum(kelly_fracs) / len(kelly_fracs) if kelly_fracs else 0.0

    total_open_risk = sum(_open_risk(p) for p in positions.values())

    return {
        "deployed": deployed,
        "heat_pct": heat_pct,
        "daily_pnl": daily_pnl,
        "daily_dd_pct": daily_dd_pct,
        "var_95": var_95,
        "avg_kelly": avg_kelly,
        "open_risk": total_open_risk,
        "n_positions": len(positions),
    }


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _progress_bar(value_pct: float, limit_pct: float, width: int = 30) -> str:
    """ASCII progress bar scaled to limit_pct."""
    filled = int(min(value_pct / limit_pct, 1.0) * width)
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}]"


def _fmt_inr(value: float, sign: bool = True) -> str:
    """Format ₹ amount with commas and optional sign."""
    prefix = "+" if (sign and value >= 0) else ""
    return f"{prefix}₹{value:,.0f}"


def _fmt_pct(value: float, sign: bool = True) -> str:
    prefix = "+" if (sign and value >= 0) else ""
    return f"{prefix}{value:.2f}%"


# ---------------------------------------------------------------------------
# Render: Positions Table
# ---------------------------------------------------------------------------

def _render_positions_table(
    positions: Dict[str, Any],
    daily_pnl: float,
    capital: float,
    console: Console,
) -> None:
    now_ist = _get_ist_now()
    time_str = now_ist.strftime("%H:%M IST")

    table = Table(
        title=f"[bold cyan]OPEN POSITIONS[/bold cyan]   [dim]{time_str}[/dim]",
        box=box.SIMPLE_HEAVY,
        expand=True,
        show_lines=True,
        header_style="bold white on dark_blue",
    )
    table.add_column("Symbol",  style="bold",  width=12)
    table.add_column("Dir",     width=6)
    table.add_column("Entry",   justify="right", width=11)
    table.add_column("LTP",     justify="right", width=11)
    table.add_column("P&L ₹",  justify="right", width=9)
    table.add_column("P&L %",  justify="right", width=7)
    table.add_column("R",       justify="right", width=8)
    table.add_column("Age",     justify="right", width=7)
    table.add_column("Grade",   justify="center", width=7)

    for sym, pos in positions.items():
        pnl = _position_pnl(pos)
        entry = float(pos.get("entry_price", 0))
        current = float(pos.get("current_price", 0))
        qty = int(pos.get("quantity", 0))
        direction = str(pos.get("direction", "LONG")).upper()
        grade = str(pos.get("quality_grade", "-"))

        pnl_pct = ((current - entry) / entry * 100.0) if entry != 0 else 0.0
        if direction == "SHORT":
            pnl_pct = -pnl_pct

        r_mult = _r_multiple(pos)
        age = _position_age(str(pos.get("entry_time", "")))

        row_style = "green" if pnl >= 0 else "red"
        dir_style = "bold green" if direction == "LONG" else "bold red"
        r_str = f"{'+' if r_mult >= 0 else ''}{r_mult:.2f}R"

        table.add_row(
            f"[bold]{sym}[/bold]",
            f"[{dir_style}]{direction[:4]}[/{dir_style}]",
            f"[dim]{entry:,.2f}[/dim]",
            f"[{row_style}]{current:,.2f}[/{row_style}]",
            f"[{row_style}]{'+' if pnl >= 0 else ''}{pnl:,.0f}[/{row_style}]",
            f"[{row_style}]{'+' if pnl_pct >= 0 else ''}{pnl_pct:.1f}%[/{row_style}]",
            f"[{'green' if r_mult >= 0 else 'red'}]{r_str}[/{'green' if r_mult >= 0 else 'red'}]",
            f"[dim]{age}[/dim]",
            f"[yellow]{grade}[/yellow]",
        )

    if not positions:
        table.add_row(
            "[dim]— no open positions —[/dim]",
            "", "", "", "", "", "", "", "",
        )

    console.print(table)

    # Portfolio summary line
    pnl_pct_capital = (daily_pnl / capital * 100.0) if capital > 0 else 0.0
    pnl_colour = "green" if daily_pnl >= 0 else "red"
    console.print(
        f"  [bold]Portfolio P&L:[/bold]  "
        f"[{pnl_colour}]{_fmt_inr(daily_pnl)}  "
        f"({_fmt_pct(pnl_pct_capital)} of capital)[/{pnl_colour}]"
    )
    console.print()


# ---------------------------------------------------------------------------
# Render: Risk Metrics Panel
# ---------------------------------------------------------------------------

def _render_risk_panel(
    metrics: Dict[str, Any],
    capital: float,
    console: Console,
) -> None:
    deployed      = metrics["deployed"]
    heat_pct      = metrics["heat_pct"]
    daily_pnl     = metrics["daily_pnl"]
    daily_dd_pct  = metrics["daily_dd_pct"]
    var_95        = metrics["var_95"]
    avg_kelly     = metrics["avg_kelly"]
    open_risk     = metrics["open_risk"]

    heat_limit_pct  = 80.0
    dd_limit_pct    = _DAILY_LOSS_LIMIT_PCT * 100.0  # e.g. 2.0
    heat_bar        = _progress_bar(heat_pct, heat_limit_pct, 30)
    dd_ok           = daily_dd_pct < dd_limit_pct

    heat_colour = (
        "green" if heat_pct < 50
        else "yellow" if heat_pct < 70
        else "red"
    )
    dd_colour = "green" if dd_ok else "red"

    lines: List[str] = [
        f"  [bold]Capital:[/bold]         [cyan]₹{capital:,.0f}[/cyan]",
        f"  [bold]Deployed:[/bold]        [cyan]₹{deployed:,.0f}[/cyan]  "
        f"[dim]({heat_pct:.1f}%)[/dim]",
        f"  [bold]Portfolio Heat:[/bold]  [{heat_colour}]{heat_pct:.1f}%[/{heat_colour}]"
        f"  [dim]{heat_bar}[/dim]  [dim](limit {heat_limit_pct:.0f}%)[/dim]",
        f"  [bold]Daily P&L:[/bold]       "
        f"[{'green' if daily_pnl >= 0 else 'red'}]{_fmt_inr(daily_pnl)}  "
        f"({_fmt_pct(daily_pnl / capital * 100.0 if capital else 0)})"
        f"[/{'green' if daily_pnl >= 0 else 'red'}]",
        f"  [bold]Daily DD:[/bold]        [{dd_colour}]{daily_dd_pct:.2f}%  "
        f"{'[bold green]✅ OK[/bold green]' if dd_ok else '[bold red]⚠ LIMIT HIT[/bold red]'}[/{dd_colour}]"
        f"  [dim](limit {dd_limit_pct:.1f}%)[/dim]",
        f"  [bold]Max 1-day VaR:[/bold]  [yellow]-₹{var_95:,.0f}[/yellow]"
        f"  [dim]@ 95% confidence[/dim]",
        f"  [bold]Kelly Avg:[/bold]       [magenta]{avg_kelly:.2f}%[/magenta]  "
        f"[dim]per trade[/dim]",
        f"  [bold]Open Risk:[/bold]       [cyan]₹{open_risk:,.0f}[/cyan]  "
        f"[dim]({open_risk / capital * 100.0:.2f}% of capital)[/dim]",
    ]

    panel_content = "\n".join(lines)
    console.print(Panel(panel_content, title="[bold cyan]RISK METRICS[/bold cyan]",
                        border_style="blue", expand=True))
    console.print()


# ---------------------------------------------------------------------------
# Render: Equity Sparkline
# ---------------------------------------------------------------------------

def _render_sparkline(trade_log: List[Dict[str, Any]], console: Console) -> None:
    """Render today's intraday equity curve as ASCII sparkline."""
    pnl_values: List[float] = []
    timestamps: List[str] = []

    for entry in trade_log:
        try:
            ts_str = entry.get("timestamp", "")
            dt = datetime.fromisoformat(ts_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
            dt_ist = dt.astimezone(IST)
            today = _get_ist_now().date()
            if dt_ist.date() != today:
                continue
            pnl = entry.get("cumulative_pnl", entry.get("pnl", 0.0))
            pnl_values.append(float(pnl))
            timestamps.append(dt_ist.strftime("%H:%M"))
        except Exception:
            continue

    if not pnl_values:
        console.print("[dim]TODAY'S EQUITY  — no intraday data available[/dim]")
        console.print()
        return

    spark = _sparkline(pnl_values)
    start_time = timestamps[0] if timestamps else "09:15"
    end_time   = timestamps[-1] if timestamps else "?"
    total_pnl  = pnl_values[-1]
    capital    = _CAPITAL
    total_pct  = total_pnl / capital * 100.0 if capital > 0 else 0.0
    pnl_colour = "green" if total_pnl >= 0 else "red"

    equity_value = capital + total_pnl

    console.print(
        Panel(
            f"  [cyan]₹{equity_value:,.0f}[/cyan]  "
            f"[dim]{spark}[/dim]  "
            f"[{pnl_colour}]{_fmt_inr(total_pnl)} ({_fmt_pct(total_pct)})[/{pnl_colour}]",
            title=f"[bold cyan]TODAY'S EQUITY[/bold cyan]  "
                  f"[dim]{start_time}→{end_time} IST[/dim]",
            border_style="blue",
            expand=True,
        )
    )
    console.print()


# ---------------------------------------------------------------------------
# Render: Kelly Fractions
# ---------------------------------------------------------------------------

def _render_kelly_panel(
    positions: Dict[str, Any],
    bayes_data: Dict[str, Any],
    console: Console,
) -> None:
    table = Table(
        title="[bold cyan]KELLY FRACTIONS[/bold cyan]",
        box=box.SIMPLE,
        expand=True,
        header_style="bold white",
    )
    table.add_column("Symbol",   width=14)
    table.add_column("Grade",    justify="center", width=8)
    table.add_column("Score",    justify="right",  width=7)
    table.add_column("WR(hist)", justify="right",  width=9)
    table.add_column("Kelly",    justify="right",  width=8)
    table.add_column("Position ₹", justify="right", width=14)

    for sym, pos in positions.items():
        grade = str(pos.get("quality_grade", "-"))
        score = float(pos.get("signal_score", 70))
        wr    = _historical_win_rate(sym, bayes_data)
        kelly = _kelly_fraction(score, wr)
        pos_val = _position_value(pos)

        table.add_row(
            f"[bold]{sym}[/bold]",
            f"[yellow]{grade}[/yellow]",
            f"[dim]{score:.0f}[/dim]",
            f"[cyan]{wr * 100:.0f}%[/cyan]",
            f"[magenta]{kelly:.2f}%[/magenta]",
            f"[dim]₹{pos_val:,.0f}[/dim]",
        )

    if not positions:
        table.add_row("[dim]— no positions —[/dim]", "", "", "", "", "")

    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# Render: Correlation Warnings
# ---------------------------------------------------------------------------

def _render_correlation_panel(console: Console) -> None:
    corr_data = _load_correlation()

    # Build pair list from correlation matrix
    # Expected format: {"RELIANCE": {"ONGC": 0.81, "TCS": 0.32}, ...}
    high_pairs: List[Tuple[str, str, float]] = []
    seen: set = set()

    for sym_a, peers in corr_data.items():
        if not isinstance(peers, dict):
            continue
        for sym_b, corr_val in peers.items():
            key = tuple(sorted([sym_a, sym_b]))
            if key in seen:
                continue
            seen.add(key)
            try:
                c = float(corr_val)
                if c > 0.7:
                    high_pairs.append((sym_a, sym_b, c))
            except Exception:
                pass

    high_pairs.sort(key=lambda x: -x[2])

    lines: List[str] = []
    if high_pairs:
        for sym_a, sym_b, corr_val in high_pairs:
            level = "EXTREME" if corr_val > 0.85 else "HIGH"
            colour = "red" if corr_val > 0.85 else "yellow"
            lines.append(
                f"  [bold {colour}]{sym_a} ↔ {sym_b}:[/bold {colour}]  "
                f"[{colour}]{corr_val:.2f} {level}[/{colour}]"
                f" [dim]— consider reducing[/dim]"
            )
    else:
        lines.append(
            "  [bold green]All pair correlations < 0.70[/bold green]  ✅"
        )

    # Check if correlation file doesn't exist at all
    if not corr_data:
        lines = ["  [dim]No correlation data available — run portfolio_rebalancer_india.py[/dim]"]

    panel_content = "\n".join(lines)
    border = "red" if high_pairs else "green"
    title  = "[bold red]CORRELATION ALERT[/bold red]" if high_pairs else "[bold green]CORRELATION STATUS[/bold green]"

    console.print(Panel(panel_content, title=title, border_style=border, expand=True))
    console.print()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_portfolio_screen(demo: bool = False) -> None:
    """
    Full Rich console output:
    1. Positions table
    2. Risk metrics panel
    3. Equity sparkline
    4. Kelly fractions
    5. Correlation warnings
    """
    console = Console()

    positions   = load_positions(demo=demo)
    trade_log   = _load_trade_log(demo=demo)
    bayes_data  = _load_bayes()

    metrics = compute_risk_metrics(positions, _CAPITAL)

    # Header
    now_ist = _get_ist_now()
    mode_tag = "[bold yellow] [DEMO][/bold yellow]" if demo else "[bold green] [LIVE][/bold green]"
    console.rule(
        f"[bold cyan]NSE PORTFOLIO RISK SCREEN[/bold cyan]{mode_tag}  "
        f"[dim]{now_ist.strftime('%Y-%m-%d %H:%M:%S IST')}[/dim]"
    )
    console.print()

    _render_positions_table(positions, metrics["daily_pnl"], _CAPITAL, console)
    _render_risk_panel(metrics, _CAPITAL, console)
    _render_sparkline(trade_log, console)
    _render_kelly_panel(positions, bayes_data, console)
    _render_correlation_panel(console)

    console.rule("[dim]bloomberg-port-india[/dim]")


def get_portfolio_risk_metrics(demo: bool = False) -> Dict[str, Any]:
    """
    Return risk metrics dict for use by main_india.py or bloomberg_india.py.

    Keys: deployed, heat_pct, daily_pnl, daily_dd_pct, var_95,
          avg_kelly, open_risk, n_positions, capital
    """
    positions = load_positions(demo=demo)
    metrics   = compute_risk_metrics(positions, _CAPITAL)
    metrics["capital"] = _CAPITAL
    return metrics


def show_portfolio_screen(demo: bool = False) -> None:
    """Entry point: renders to terminal."""
    render_portfolio_screen(demo=demo)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _demo_mode = "--demo" in sys.argv
    show_portfolio_screen(demo=_demo_mode)
