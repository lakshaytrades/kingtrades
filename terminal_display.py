"""
terminal_display.py — KingTrades Bloomberg Terminal Dashboard

Bloomberg-style rich terminal UI that reads live bot state from
/tmp/kingtrades_state.json (written by main.py every 30 seconds).

Run separately (works while bot is running):
    python3 terminal_display.py

The dashboard auto-refreshes every 2 seconds. Press Ctrl+C to exit.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from zoneinfo import ZoneInfo

STATE_FILE = Path("/tmp/kingtrades_state.json")
REFRESH_HZ = 2  # seconds between display refreshes
VERSION    = "3.0"
ET         = ZoneInfo("America/New_York")

try:
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.align import Align
    from rich.columns import Columns
    from rich.rule import Rule
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


# ── colour palette ──────────────────────────────────────────
C_GREEN  = "bright_green"
C_RED    = "bright_red"
C_YELLOW = "bright_yellow"
C_BLUE   = "cyan"
C_WHITE  = "white"
C_DIM    = "dim white"
C_GOLD   = "yellow"
C_HEAD   = "bold cyan"


def _now_et() -> str:
    return datetime.now(ET).strftime("%H:%M:%S ET")


def _score_bar(score: float, width: int = 10) -> str:
    filled = max(0, min(width, int(round(score / 100 * width))))
    return "█" * filled + "░" * (width - filled)


def _pct_color(pct: float) -> str:
    if pct > 0:
        return C_GREEN
    if pct < 0:
        return C_RED
    return C_DIM


def _read_state() -> Dict[str, Any]:
    """Read bot state from JSON file. Returns empty dict if unavailable."""
    try:
        if not STATE_FILE.exists():
            return {}
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


# ============================================================
# PANEL BUILDERS
# ============================================================

def _header_panel(state: Dict) -> Panel:
    """Top bar: market context + time."""
    spy     = state.get("spy_price", 0.0)
    spy_chg = state.get("spy_pct", 0.0)
    qqq     = state.get("qqq_price", 0.0)
    qqq_chg = state.get("qqq_pct", 0.0)
    vix     = state.get("vix", 0.0)
    regime  = state.get("regime", "UNKNOWN")
    session = state.get("session", "—")
    bot_ver = state.get("version", VERSION)
    updated = state.get("updated_at", "—")

    spy_arrow = "▲" if spy_chg >= 0 else "▼"
    qqq_arrow = "▲" if qqq_chg >= 0 else "▼"
    vix_color = C_RED if vix > 25 else (C_YELLOW if vix > 18 else C_GREEN)
    vix_tag   = "HIGH ⚠" if vix > 25 else ("ELEV" if vix > 18 else "NORM ✓")
    reg_color = C_GREEN if "BULL" in regime.upper() or "TREND" in regime.upper() else (
                C_RED   if "BEAR" in regime.upper() else C_YELLOW)

    t = Text()
    t.append(f"  KingTrades v{bot_ver}", style="bold cyan")
    t.append(f"  ║  ", style=C_DIM)
    t.append(f"SPY ${spy:.2f} {spy_arrow}{spy_chg:+.2f}%", style=C_GREEN if spy_chg >= 0 else C_RED)
    t.append(f"  ║  ", style=C_DIM)
    t.append(f"QQQ ${qqq:.2f} {qqq_arrow}{qqq_chg:+.2f}%", style=C_GREEN if qqq_chg >= 0 else C_RED)
    t.append(f"  ║  ", style=C_DIM)
    t.append(f"VIX {vix:.1f} {vix_tag}", style=vix_color)
    t.append(f"  ║  ", style=C_DIM)
    t.append(f"REGIME: {regime}", style=reg_color)
    t.append(f"  ║  SESSION: {session}", style=C_BLUE)
    t.append(f"  ║  {_now_et()}", style=C_GOLD)
    t.append(f"  ║  updated {updated}", style=C_DIM)

    return Panel(Align.left(t), style="bold", box=box.HEAVY, padding=(0, 0))


def _positions_panel(state: Dict) -> Panel:
    """Open positions table."""
    positions = state.get("positions", [])

    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style=C_HEAD,
        min_width=80,
        expand=True,
    )
    table.add_column("SYMBOL",  style="bold white",  width=8)
    table.add_column("DIR",     style=C_BLUE,         width=6)
    table.add_column("ENTRY",   justify="right",      width=9)
    table.add_column("CURRENT", justify="right",      width=9)
    table.add_column("P&L",     justify="right",      width=10)
    table.add_column("%",       justify="right",      width=8)
    table.add_column("STOP",    justify="right",      width=9)
    table.add_column("TARGET",  justify="right",      width=9)
    table.add_column("STATUS",  width=14)

    if not positions:
        table.add_row(
            "—", "—", "—", "—", "—", "—", "—", "—",
            Text("No open positions", style=C_DIM),
        )
    else:
        for pos in positions[:8]:
            pnl     = pos.get("pnl", 0.0)
            pnl_pct = pos.get("pnl_pct", 0.0)
            arrow   = "↗ TOWARD T2" if pnl_pct > 1.5 else ("↗ TOWARD T1" if pnl_pct > 0 else "↘ HOLD")
            pnl_col = C_GREEN if pnl >= 0 else C_RED
            pct_col = _pct_color(pnl_pct)
            table.add_row(
                pos.get("symbol", "—"),
                pos.get("direction", "—"),
                f"${pos.get('entry', 0):.2f}",
                f"${pos.get('current', 0):.2f}",
                Text(f"{'+' if pnl>=0 else ''}{pnl:,.0f}", style=pnl_col),
                Text(f"{pnl_pct:+.2f}%", style=pct_col),
                f"${pos.get('stop', 0):.2f}",
                f"${pos.get('target1', 0):.2f}",
                Text(arrow, style=pnl_col),
            )

    return Panel(table, title="[bold cyan]OPEN POSITIONS[/bold cyan]",
                 box=box.HEAVY, border_style="cyan")


def _pnl_panel(state: Dict) -> Panel:
    """Today's P&L summary with progress bar."""
    capital    = state.get("capital", 8000.0)
    realized   = state.get("realized_pnl", 0.0)
    unrealized = state.get("unrealized_pnl", 0.0)
    total      = realized + unrealized
    target_pct = state.get("target_pct", 1.0)
    target_amt = capital * target_pct / 100
    progress   = min(100, (total / target_amt * 100)) if target_amt > 0 else 0
    bar        = _score_bar(progress, 20)

    t = Text()
    t.append(f"  Realized:    ", style=C_DIM)
    t.append(f"${realized:+,.2f}", style=C_GREEN if realized >= 0 else C_RED)
    t.append(f"\n  Unrealized:  ", style=C_DIM)
    t.append(f"${unrealized:+,.2f}", style=C_GREEN if unrealized >= 0 else C_RED)
    t.append(f"\n  ─────────────────────\n", style=C_DIM)
    t.append(f"  TOTAL:  ", style="bold white")
    t.append(f"${total:+,.2f}  ({total/capital*100:+.2f}%)\n" if capital > 0 else f"${total:+,.2f}\n",
             style=C_GREEN if total >= 0 else C_RED)
    t.append(f"\n  Target: ", style=C_DIM)
    t.append(f"${target_amt:,.0f}", style=C_GOLD)
    t.append(f"  ({target_pct:.1f}%)\n", style=C_DIM)
    t.append(f"  [{bar}] {progress:.0f}%\n", style=C_GREEN if progress >= 100 else C_YELLOW)
    if progress >= 100:
        t.append(f"  🎯 DAILY TARGET HIT! ✅", style="bold green")

    return Panel(t, title="[bold cyan]TODAY'S P&L[/bold cyan]",
                 box=box.HEAVY, border_style="cyan")


def _risk_panel(state: Dict) -> Panel:
    """Risk monitor panel."""
    capital   = state.get("capital", 8000.0)
    heat      = state.get("heat", 0.0)
    risk_used = state.get("risk_used", 0.0)
    risk_max  = state.get("risk_max", capital * 0.02)
    n_pos     = state.get("n_positions", 0)
    max_pos   = state.get("max_positions", 5)
    heat_pct  = heat / capital * 100 if capital > 0 else 0
    risk_pct  = risk_used / risk_max * 100 if risk_max > 0 else 0
    risk_col  = C_RED if risk_pct >= 80 else (C_YELLOW if risk_pct >= 50 else C_GREEN)
    heat_col  = C_RED if heat_pct >= 50 else C_DIM

    t = Text()
    t.append(f"  Capital:    ", style=C_DIM)
    t.append(f"${capital:,.0f}\n", style=C_WHITE)
    t.append(f"  Heat:       ", style=C_DIM)
    t.append(f"${heat:,.0f}  ({heat_pct:.1f}%)\n", style=heat_col)
    t.append(f"  Risk used:  ", style=C_DIM)
    t.append(f"${risk_used:,.0f}", style=risk_col)
    t.append(f" / ${risk_max:,.0f}\n", style=C_DIM)
    t.append(f"  [{_score_bar(risk_pct)} {risk_pct:.0f}%]\n", style=risk_col)
    t.append(f"\n  Positions:  ", style=C_DIM)
    t.append(f"{n_pos} / {max_pos} max\n", style=C_WHITE)
    paused = state.get("paused", False)
    cb     = state.get("circuit_breaker", False)
    t.append(f"\n  State:  ", style=C_DIM)
    if cb:
        t.append("⛔ CIRCUIT BREAKER", style="bold red")
    elif paused:
        t.append("⏸  PAUSED", style=C_YELLOW)
    else:
        t.append("✅ ACTIVE", style=C_GREEN)

    return Panel(t, title="[bold cyan]RISK MONITOR[/bold cyan]",
                 box=box.HEAVY, border_style="cyan")


def _stats_panel(state: Dict) -> Panel:
    """Daily trading statistics."""
    n_trades = state.get("n_trades", 0)
    wins     = state.get("wins", 0)
    losses   = state.get("losses", 0)
    wr       = state.get("win_rate", 0.0)
    avg_win  = state.get("avg_win", 0.0)
    avg_loss = state.get("avg_loss", 0.0)
    ev       = state.get("ev_trade", 0.0)
    best_sym = state.get("best_symbol", "—")
    best_pnl = state.get("best_pnl", 0.0)
    consec_l = state.get("consecutive_losses", 0)

    t = Text()
    t.append(f"  Trades:    ", style=C_DIM)
    t.append(f"{n_trades}", style=C_WHITE)
    t.append(f"   Wins: ", style=C_DIM)
    t.append(f"{wins}", style=C_GREEN)
    t.append(f"  Losses: ", style=C_DIM)
    t.append(f"{losses}\n", style=C_RED)
    t.append(f"  Win Rate:  ", style=C_DIM)
    t.append(f"{wr:.1f}%\n", style=C_GREEN if wr >= 55 else (C_YELLOW if wr >= 45 else C_RED))
    t.append(f"  Avg Win:   ", style=C_DIM)
    t.append(f"+${avg_win:,.0f}\n", style=C_GREEN)
    t.append(f"  Avg Loss:  ", style=C_DIM)
    t.append(f"-${abs(avg_loss):,.0f}\n", style=C_RED)
    t.append(f"  EV/Trade:  ", style=C_DIM)
    t.append(f"{'+' if ev >= 0 else ''}${ev:,.0f}\n", style=C_GREEN if ev > 0 else C_RED)
    t.append(f"\n  Best:      ", style=C_DIM)
    t.append(f"{best_sym}  +${best_pnl:,.0f}\n", style=C_GREEN)
    if consec_l >= 2:
        t.append(f"\n  ⚠ Consec losses: {consec_l}  → size reduced", style=C_YELLOW)

    return Panel(t, title="[bold cyan]DAILY STATS[/bold cyan]",
                 box=box.HEAVY, border_style="cyan")


def _session_panel(state: Dict) -> Panel:
    """Session info and next events."""
    session     = state.get("session", "—")
    sess_mult   = state.get("session_mult", 1.0)
    eod_time    = state.get("eod_time", "15:35 ET")
    next_event  = state.get("next_event", "—")
    vix         = state.get("vix", 0.0)
    vix_regime  = "VIX >35 → NO LONGS ⛔" if vix > 35 else (
                  "VIX >25 → 75% size ⚠" if vix > 25 else "VIX normal ✅")
    recent_logs = state.get("recent_logs", [])

    t = Text()
    t.append(f"  Session:    ", style=C_DIM)
    t.append(f"{session}\n", style=C_BLUE)
    t.append(f"  Size mult:  ", style=C_DIM)
    t.append(f"{sess_mult:.2f}×\n", style=C_GOLD)
    t.append(f"  VIX regime: ", style=C_DIM)
    vix_col = C_RED if vix > 35 else (C_YELLOW if vix > 25 else C_GREEN)
    t.append(f"{vix_regime}\n", style=vix_col)
    t.append(f"  Next event: ", style=C_DIM)
    t.append(f"{next_event}\n", style=C_WHITE)
    t.append(f"  EOD report: ", style=C_DIM)
    t.append(f"{eod_time}\n", style=C_DIM)

    if recent_logs:
        t.append(f"\n  ─── Recent ───────────────────\n", style=C_DIM)
        for log in recent_logs[-4:]:
            t.append(f"  {log}\n", style=C_DIM)

    return Panel(t, title="[bold cyan]SESSION[/bold cyan]",
                 box=box.HEAVY, border_style="cyan")


def _make_layout(state: Dict) -> Layout:
    """Compose the full Bloomberg terminal layout."""
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="positions", size=12),
        Layout(name="bottom", size=14),
    )
    layout["bottom"].split_row(
        Layout(name="pnl",     ratio=1),
        Layout(name="risk",    ratio=1),
        Layout(name="stats",   ratio=1),
        Layout(name="session", ratio=1),
    )
    layout["header"].update(_header_panel(state))
    layout["positions"].update(_positions_panel(state))
    layout["pnl"].update(_pnl_panel(state))
    layout["risk"].update(_risk_panel(state))
    layout["stats"].update(_stats_panel(state))
    layout["session"].update(_session_panel(state))
    return layout


# ============================================================
# FALLBACK (no rich)
# ============================================================

def _plain_display(state: Dict) -> None:
    """Minimal ASCII display when rich is not installed."""
    os.system("clear")
    SEP = "=" * 70
    print(SEP)
    print(f"  KingTrades v{VERSION}  —  Bloomberg Terminal  —  {_now_et()}")
    print(SEP)

    capital    = state.get("capital", 0.0)
    total_pnl  = state.get("realized_pnl", 0.0) + state.get("unrealized_pnl", 0.0)
    target_pct = state.get("target_pct", 1.0)
    target_amt = capital * target_pct / 100
    progress   = total_pnl / target_amt * 100 if target_amt > 0 else 0

    print(f"\n  CAPITAL:   ${capital:,.0f}")
    print(f"  DAY P&L:   {'+' if total_pnl >= 0 else ''}${total_pnl:,.0f}  ({total_pnl/capital*100:+.2f}% of cap)" if capital else f"  DAY P&L:   ${total_pnl:,.0f}")
    print(f"  TARGET:    ${target_amt:,.0f}  ({progress:.0f}% achieved)")

    positions = state.get("positions", [])
    if positions:
        print(f"\n  POSITIONS ({len(positions)}):")
        print(f"  {'SYMBOL':<8} {'DIR':<6} {'ENTRY':>9} {'CURRENT':>9} {'P&L':>9} {'%':>7}")
        for pos in positions[:5]:
            pnl = pos.get("pnl", 0)
            print(
                f"  {pos.get('symbol',''):<8} {pos.get('direction',''):<6}"
                f" ${pos.get('entry',0):>8.2f}"
                f" ${pos.get('current',0):>8.2f}"
                f" ${pnl:>+8.0f}"
                f" {pos.get('pnl_pct',0):>+6.2f}%"
            )
    else:
        print("\n  No open positions")

    n_trades = state.get("n_trades", 0)
    wins     = state.get("wins", 0)
    losses   = state.get("losses", 0)
    wr       = state.get("win_rate", 0.0)
    print(f"\n  STATS:  {n_trades} trades  |  {wins}W / {losses}L  |  {wr:.1f}% WR")
    print(f"\n  Updated: {state.get('updated_at','—')}")
    print(SEP)
    print("  Refreshing every 2s — Ctrl+C to exit")


# ============================================================
# MAIN
# ============================================================

def run_dashboard() -> None:
    if not HAS_RICH:
        print("rich not installed — running plain mode (pip install rich)")
        try:
            while True:
                state = _read_state()
                _plain_display(state)
                time.sleep(REFRESH_HZ)
        except KeyboardInterrupt:
            print("\nDashboard stopped.")
        return

    console = Console()

    if not STATE_FILE.exists():
        console.print(
            f"[yellow]Waiting for bot state file: {STATE_FILE}[/yellow]\n"
            f"[dim]Bot must be running and writing state to that path.[/dim]"
        )

    console.print(
        f"[bold cyan]KingTrades Bloomberg Terminal v{VERSION}[/bold cyan]  "
        f"[dim]Reading {STATE_FILE}  •  Refreshing every {REFRESH_HZ}s  •  Ctrl+C to exit[/dim]"
    )
    time.sleep(1)

    try:
        with Live(
            console=console,
            refresh_per_second=1.0 / REFRESH_HZ,
            screen=True,
        ) as live:
            while True:
                state = _read_state()
                live.update(_make_layout(state))
                time.sleep(REFRESH_HZ)
    except KeyboardInterrupt:
        console.print("\n[dim]Dashboard stopped.[/dim]")


if __name__ == "__main__":
    run_dashboard()
