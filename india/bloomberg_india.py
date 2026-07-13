"""
bloomberg_india.py — SATA-BLOOMBERG Master Launcher
NSE Momentum Bot | Upstox Broker | IST Timezone

Single entry point for all Bloomberg-equivalent screens:
  - Terminal dashboard (live terminal)
  - Sector heatmap
  - Options intelligence (OMON)
  - Portfolio risk screen (PORT)

Usage:
  python3 india/bloomberg_india.py                       # full terminal (live)
  python3 india/bloomberg_india.py --demo                # full terminal (demo)
  python3 india/bloomberg_india.py --screen sector       # sector heatmap
  python3 india/bloomberg_india.py --screen options      # options screen
  python3 india/bloomberg_india.py --screen portfolio    # portfolio screen
  python3 india/bloomberg_india.py --screen terminal     # live terminal
  python3 india/bloomberg_india.py --screen all          # cycle all screens
  python3 india/bloomberg_india.py --broadcast           # all screens → Telegram
  python3 india/bloomberg_india.py --morning             # morning brief → Telegram
  python3 india/bloomberg_india.py --eod                 # EOD report → Telegram
  python3 india/bloomberg_india.py --status              # one-line bot status
"""

import argparse
import json
import sys
import time as _time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Paths ─────────────────────────────────────────────────────────────────────
_BASE = Path(__file__).parent.parent
sys.path.insert(0, str(_BASE))
sys.path.insert(0, str(Path(__file__).parent))

# ── Timezone ──────────────────────────────────────────────────────────────────
IST = ZoneInfo("Asia/Kolkata")

# ── Load .env ─────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(_BASE / ".env")
except ImportError:
    pass

# ── Rich console ──────────────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    _RICH = True
except ImportError:
    _RICH = False

console = Console() if _RICH else None

# ── Optional screen imports (graceful degradation) ────────────────────────────

_terminal_mod = None
_sector_mod = None
_options_mod = None
_portfolio_mod = None

try:
    import terminal_india as _terminal_mod
except ImportError:
    pass

try:
    import sector_heatmap_india as _sector_mod
except ImportError:
    pass

try:
    import options_screen_india as _options_mod
except ImportError:
    pass

try:
    import portfolio_screen_india as _portfolio_mod
except ImportError:
    pass

# ── Bloomberg startup banner ──────────────────────────────────────────────────

BANNER_PLAIN = r"""
╔══════════════════════════════════════════════════════════════╗
║         SATA-BLOOMBERG  │  NSE MOMENTUM BOT                 ║
║         Upstox Broker   │  Equity MIS   │  IST Timezone     ║
║                                                              ║
║  Screens: LAUNCHPAD  │  SECTOR-HM  │  OMON  │  PORT         ║
║  Signals: 30+ sources  │  ML Ensemble  │  Bayesian Elite    ║
╚══════════════════════════════════════════════════════════════╝
"""


def _print_banner() -> None:
    """Print the Bloomberg-style startup banner."""
    if _RICH and console is not None:
        banner_text = Text()
        banner_text.append("╔══════════════════════════════════════════════════════════════╗\n", style="bold yellow")
        banner_text.append("║", style="bold yellow")
        banner_text.append("         SATA-BLOOMBERG  │  NSE MOMENTUM BOT                 ", style="bold white")
        banner_text.append("║\n", style="bold yellow")
        banner_text.append("║", style="bold yellow")
        banner_text.append("         Upstox Broker   │  Equity MIS   │  IST Timezone     ", style="cyan")
        banner_text.append("║\n", style="bold yellow")
        banner_text.append("║", style="bold yellow")
        banner_text.append("                                                              ", style="white")
        banner_text.append("║\n", style="bold yellow")
        banner_text.append("║", style="bold yellow")
        banner_text.append("  Screens: ", style="white")
        banner_text.append("LAUNCHPAD  │  SECTOR-HM  │  OMON  │  PORT", style="bold green")
        banner_text.append("         ║\n", style="bold yellow")
        banner_text.append("║", style="bold yellow")
        banner_text.append("  Signals: 30+ sources  │  ML Ensemble  │  Bayesian Elite    ", style="magenta")
        banner_text.append("║\n", style="bold yellow")
        banner_text.append("╚══════════════════════════════════════════════════════════════╝", style="bold yellow")
        console.print(banner_text)
    else:
        print(BANNER_PLAIN)


# ── Screen helpers ────────────────────────────────────────────────────────────

def _section_header(label: str) -> None:
    """Print a section divider with a screen label."""
    line = f"═══ {label} ═══"
    if _RICH and console is not None:
        console.print(f"\n[bold cyan]{line}[/bold cyan]\n")
    else:
        print(f"\n{line}\n")


def _warn(msg: str) -> None:
    if _RICH and console is not None:
        console.print(f"[bold red][WARN][/bold red] {msg}")
    else:
        print(f"[WARN] {msg}", file=sys.stderr)


def _info(msg: str) -> None:
    if _RICH and console is not None:
        console.print(f"[dim]{msg}[/dim]")
    else:
        print(msg)


# ── Individual screen launchers ───────────────────────────────────────────────

def _run_terminal(demo: bool = False) -> None:
    """Launch the live terminal dashboard."""
    if _terminal_mod is None:
        _warn("terminal_india.py not found — terminal screen unavailable.")
        return
    try:
        _terminal_mod.run_dashboard(demo=demo, once=False)
    except Exception as exc:
        _warn(f"Terminal screen failed: {exc}")


def _run_sector(demo: bool = False) -> None:
    """Show the sector heatmap (once)."""
    if _sector_mod is None:
        _warn("sector_heatmap_india.py not found — sector screen unavailable.")
        return
    try:
        _sector_mod.show_sector_heatmap(demo=demo)
    except Exception as exc:
        _warn(f"Sector heatmap failed: {exc}")


def _run_options(demo: bool = False) -> None:
    """Show the options intelligence screen (once)."""
    if _options_mod is None:
        _warn("options_screen_india.py not found — options screen unavailable.")
        return
    try:
        _options_mod.show_options_screen(demo=demo)
    except Exception as exc:
        _warn(f"Options screen failed: {exc}")


def _run_portfolio(demo: bool = False) -> None:
    """Show the portfolio risk screen (once)."""
    if _portfolio_mod is None:
        _warn("portfolio_screen_india.py not found — portfolio screen unavailable.")
        return
    try:
        _portfolio_mod.show_portfolio_screen(demo=demo)
    except Exception as exc:
        _warn(f"Portfolio screen failed: {exc}")


# ── --screen all: cycle through screens ───────────────────────────────────────

def _screen_all(demo: bool = False, pause: float = 3.0) -> None:
    """Cycle through all four screens, pausing between each."""
    _section_header("[1/4] SECTOR HEATMAP")
    _run_sector(demo=demo)
    _time.sleep(pause)

    _section_header("[2/4] OPTIONS INTELLIGENCE")
    _run_options(demo=demo)
    _time.sleep(pause)

    _section_header("[3/4] PORTFOLIO RISK")
    _run_portfolio(demo=demo)
    _time.sleep(pause)

    _section_header("[4/4] LIVE TERMINAL (press Ctrl+C to exit)")
    _run_terminal(demo=demo)


# ── Telegram broadcast helpers ────────────────────────────────────────────────

def _tg_send(msg: str) -> None:
    """Send a plain text message to Telegram (best-effort)."""
    import os
    import requests  # type: ignore

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        _warn("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping Telegram send.")
        return
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        requests.post(
            url,
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as exc:
        _warn(f"Telegram send failed: {exc}")


def _broadcast_sector(demo: bool = False) -> None:
    if _sector_mod is None:
        _warn("sector_heatmap_india.py not available for broadcast.")
        return
    try:
        fn = getattr(_sector_mod, "send_sector_heatmap_telegram", None)
        if fn is not None:
            fn(demo=demo)
        else:
            _warn("sector_heatmap_india has no send_sector_heatmap_telegram() — displaying locally.")
            _run_sector(demo=demo)
    except Exception as exc:
        _warn(f"Sector broadcast failed: {exc}")


def _broadcast_options(demo: bool = False) -> None:
    if _options_mod is None:
        _warn("options_screen_india.py not available for broadcast.")
        return
    try:
        fn = getattr(_options_mod, "send_options_screen_telegram", None)
        if fn is not None:
            fn(demo=demo)
        else:
            _warn("options_screen_india has no send_options_screen_telegram() — displaying locally.")
            _run_options(demo=demo)
    except Exception as exc:
        _warn(f"Options broadcast failed: {exc}")


def _broadcast_portfolio(demo: bool = False) -> None:
    if _portfolio_mod is None:
        _warn("portfolio_screen_india.py not available for broadcast.")
        return
    try:
        fn = getattr(_portfolio_mod, "send_portfolio_screen_telegram", None)
        if fn is not None:
            fn(demo=demo)
        else:
            _warn("portfolio_screen_india has no send_portfolio_screen_telegram() — displaying locally.")
            _run_portfolio(demo=demo)
    except Exception as exc:
        _warn(f"Portfolio broadcast failed: {exc}")


# ── --broadcast: send all screens to Telegram ─────────────────────────────────

def _do_broadcast(demo: bool = False) -> None:
    """Send sector heatmap, options screen, and portfolio summary to Telegram."""
    _info("Broadcasting sector heatmap...")
    _broadcast_sector(demo=demo)
    _time.sleep(2)

    _info("Broadcasting options screen...")
    _broadcast_options(demo=demo)
    _time.sleep(2)

    _info("Broadcasting portfolio summary...")
    _broadcast_portfolio(demo=demo)
    _info("Broadcast complete.")


# ── --morning: morning brief package ──────────────────────────────────────────

def _do_morning(demo: bool = False) -> None:
    """Send morning brief package to Telegram (9:10 AM brief)."""
    _info("Sending morning brief package...")

    _broadcast_sector(demo=demo)
    _time.sleep(2)

    _broadcast_options(demo=demo)
    _time.sleep(2)

    # Try daily_intelligence_india.send_morning_brief() if available
    try:
        import daily_intelligence_india as _di
        fn = getattr(_di, "send_morning_brief_v2", None) or getattr(_di, "send_morning_brief", None)
        if fn is not None:
            _info("Sending morning intelligence brief...")
            fn()
        else:
            _warn("daily_intelligence_india has no send_morning_brief() function.")
    except ImportError:
        _warn("daily_intelligence_india.py not available — skipping morning intelligence.")
    except Exception as exc:
        _warn(f"Morning intelligence failed: {exc}")

    _info("Morning brief complete.")


# ── --eod: end-of-day report ───────────────────────────────────────────────────

def _do_eod(demo: bool = False) -> None:
    """Send EOD report to Telegram."""
    _info("Sending EOD report...")

    # Try daily_intelligence_india.send_eod_report() first
    try:
        import daily_intelligence_india as _di
        fn = getattr(_di, "send_eod_report", None)
        if fn is not None:
            _info("Sending EOD intelligence report...")
            fn()
        else:
            _warn("daily_intelligence_india has no send_eod_report() function.")
    except ImportError:
        _warn("daily_intelligence_india.py not available — skipping EOD intelligence.")
    except Exception as exc:
        _warn(f"EOD intelligence failed: {exc}")

    _time.sleep(2)
    _broadcast_portfolio(demo=demo)

    _info("EOD report complete.")


# ── --status: one-line bot status ─────────────────────────────────────────────

def _do_status() -> None:
    """Print a one-line bot status to stdout and exit."""
    now_ist = datetime.now(tz=IST)
    time_str = now_ist.strftime("%H:%M")

    # Determine market state
    from datetime import time as _time_type
    mkt_open = _time_type(9, 15)
    mkt_close = _time_type(15, 30)
    now_t = now_ist.time()
    if mkt_open <= now_t <= mkt_close:
        market_state = "OPEN"
    elif now_t < mkt_open:
        market_state = "PRE-MARKET"
    else:
        market_state = "CLOSED"

    # Try to read live state from /tmp/india_state.json (written by main_india.py)
    positions = 0
    pnl_inr = 0.0
    daily_dd_pct = 0.0
    bot_state = "UNKNOWN"
    capital = 0.0

    state_file = Path("/tmp/india_state.json")
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
            positions = len(state.get("positions", {}))
            pnl_inr = float(state.get("daily_pnl", 0.0))
            capital = float(state.get("capital", 1.0))
            bot_state = "RUNNING" if state.get("live") else "MANUAL"
            if state.get("circuit_hit"):
                bot_state = "CIRCUIT-BREAKER"
            elif state.get("loss_guard"):
                bot_state = "LOSS-GUARD"
            # Daily drawdown = negative pnl as % of capital
            if pnl_inr < 0 and capital > 0:
                daily_dd_pct = abs(pnl_inr) / capital * 100
        except Exception:
            pass

    pnl_sign = "+" if pnl_inr >= 0 else ""
    line = (
        f"[SATA-BLOOMBERG] {time_str} IST | "
        f"MARKET: {market_state} | "
        f"Positions: {positions} | "
        f"P&L: {pnl_sign}₹{pnl_inr:,.0f} | "
        f"Daily DD: {daily_dd_pct:.1f}% | "
        f"Bot: {bot_state}"
    )
    print(line)


# ── CLI argument parser ────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bloomberg_india.py",
        description="SATA-BLOOMBERG — NSE Momentum Bot master launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 india/bloomberg_india.py                    Full terminal (live)
  python3 india/bloomberg_india.py --demo             Full terminal (demo data)
  python3 india/bloomberg_india.py --screen sector    Sector heatmap only
  python3 india/bloomberg_india.py --screen options   Options intelligence only
  python3 india/bloomberg_india.py --screen portfolio Portfolio risk screen
  python3 india/bloomberg_india.py --screen all       Cycle all screens
  python3 india/bloomberg_india.py --broadcast        All screens → Telegram
  python3 india/bloomberg_india.py --morning          Morning brief → Telegram
  python3 india/bloomberg_india.py --eod              EOD report → Telegram
  python3 india/bloomberg_india.py --status           One-line bot status
""",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        default=False,
        help="Use synthetic/demo data (no live API calls required)",
    )
    parser.add_argument(
        "--screen",
        choices=["sector", "options", "portfolio", "terminal", "all"],
        default=None,
        metavar="SCREEN",
        help="Show a specific screen: sector | options | portfolio | terminal | all",
    )
    parser.add_argument(
        "--broadcast",
        action="store_true",
        default=False,
        help="Send all screens to Telegram",
    )
    parser.add_argument(
        "--morning",
        action="store_true",
        default=False,
        help="Send morning brief package to Telegram",
    )
    parser.add_argument(
        "--eod",
        action="store_true",
        default=False,
        help="Send EOD report to Telegram",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        default=False,
        help="Print one-line bot status to stdout and exit",
    )
    return parser


# ── Main entry point ──────────────────────────────────────────────────────────

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # --status: no banner, just the status line
    if args.status:
        _do_status()
        return

    # All other modes print the startup banner first
    _print_banner()

    if args.broadcast:
        _do_broadcast(demo=args.demo)
        return

    if args.morning:
        _do_morning(demo=args.demo)
        return

    if args.eod:
        _do_eod(demo=args.demo)
        return

    # Screen routing
    screen = args.screen  # None means default (terminal)

    if screen is None or screen == "terminal":
        _run_terminal(demo=args.demo)
    elif screen == "sector":
        _run_sector(demo=args.demo)
    elif screen == "options":
        _run_options(demo=args.demo)
    elif screen == "portfolio":
        _run_portfolio(demo=args.demo)
    elif screen == "all":
        _screen_all(demo=args.demo)


if __name__ == "__main__":
    main()
