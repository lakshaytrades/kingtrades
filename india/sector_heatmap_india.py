"""
sector_heatmap_india.py — NSE Sector Heatmap (Bloomberg HM equivalent)

Displays a Rich terminal heatmap of NSE sector performance:
  - Equal-weighted return per sector (vs prev close)
  - Breadth (advances/declines)
  - Top mover per sector
  - Formatted Telegram message

Usage:
  python3 india/sector_heatmap_india.py              # live mode
  python3 india/sector_heatmap_india.py --demo       # demo mode (no API calls)
  python3 india/sector_heatmap_india.py --telegram   # live + send to Telegram
"""

import logging
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

# ── Path bootstrap ─────────────────────────────────────────────────────────────
_BASE = Path(__file__).parent.parent
sys.path.insert(0, str(_BASE))
sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv(_BASE / ".env")
except ImportError:
    pass

IST = ZoneInfo("Asia/Kolkata")

logger = logging.getLogger("sector_heatmap_india")

# ── NSE Sector definitions (static) ───────────────────────────────────────────
NSE_SECTORS: Dict[str, List[str]] = {
    "NIFTY IT":      ["INFY", "TCS", "WIPRO", "HCLTECH", "TECHM", "LTIM", "MPHASIS"],
    "NIFTY BANK":    ["HDFCBANK", "ICICIBANK", "KOTAKBANK", "AXISBANK", "SBIN",
                      "INDUSINDBK", "BANDHANBNK"],
    "NIFTY AUTO":    ["MARUTI", "TRENT", "M&M", "BAJAJ-AUTO", "EICHERMOT",
                      "HEROMOTOCO"],
    "NIFTY PHARMA":  ["SUNPHARMA", "APOLLOHOSP", "CIPLA", "DIVISLAB", "AUROPHARMA",
                      "TORNTPHARM"],
    "NIFTY METAL":   ["TATASTEEL", "HINDALCO", "JSWSTEEL", "SAIL", "NMDC", "VEDL"],
    "NIFTY ENERGY":  ["RELIANCE", "ONGC", "NTPC", "POWERGRID", "BPCL", "IOC"],
    "NIFTY FMCG":    ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR",
                      "MARICO"],
    "NIFTY REALTY":  ["DLF", "GODREJPROP", "OBEROIRLTY", "PHOENIXLTD", "SOBHA"],
    "NIFTY INFRA":   ["BHARTIARTL", "POLYCAB", "ULTRACEMCO", "GRASIM",
                      "SHREECEM"],
    "NIFTY FIN SVC": ["BAJFINANCE", "BAJAJFINSV", "HDFCLIFE", "SBILIFE",
                      "CHOLAFIN"],
}

# Short display labels for Telegram
_SECTOR_SHORT: Dict[str, str] = {
    "NIFTY IT":      "IT",
    "NIFTY BANK":    "BANK",
    "NIFTY AUTO":    "AUTO",
    "NIFTY PHARMA":  "PHARMA",
    "NIFTY METAL":   "METAL",
    "NIFTY ENERGY":  "ENERGY",
    "NIFTY FMCG":    "FMCG",
    "NIFTY REALTY":  "REALTY",
    "NIFTY INFRA":   "INFRA",
    "NIFTY FIN SVC": "FIN",
}

# ── Type alias ─────────────────────────────────────────────────────────────────
SectorEntry = Dict  # {"ret_pct", "advances", "declines", "top_mover"}
SectorData  = Dict[str, SectorEntry]


# ── 1. Sector performance ──────────────────────────────────────────────────────

def _fetch_ltp_batch(symbols: List[str], client) -> Dict[str, float]:
    """Fetch LTPs in bulk via Upstox. Returns {symbol: ltp}. Empty on failure."""
    try:
        from data_fetch_upstox import get_multiple_ltp
        return get_multiple_ltp(symbols, dhan_client=client)
    except Exception as e:
        logger.debug(f"_fetch_ltp_batch: {e}")
        return {}


def _fetch_prev_close(symbols: List[str], client) -> Dict[str, float]:
    """
    Fetch previous close for each symbol via Upstox daily candles.
    Returns {symbol: prev_close}.  Empty on failure.
    """
    result: Dict[str, float] = {}
    try:
        from data_fetch_upstox import get_ohlcv
        for sym in symbols:
            try:
                df = get_ohlcv(sym, interval="1d", period="5d")
                if df is not None and len(df) >= 2:
                    result[sym] = float(df["close"].iloc[-2])
                elif df is not None and len(df) == 1:
                    result[sym] = float(df["close"].iloc[-1])
            except Exception as inner_e:
                logger.debug(f"prev_close {sym}: {inner_e}")
    except Exception as e:
        logger.debug(f"_fetch_prev_close: {e}")
    return result


def get_sector_performance(client=None) -> SectorData:
    """
    For each sector, fetch LTP + prev close of constituent stocks from Upstox.
    Computes equal-weighted average return.

    Returns:
      {
        sector_name: {
          "ret_pct":   float,          # equal-weighted avg return
          "advances":  int,            # stocks > 0%
          "declines":  int,            # stocks < 0%
          "top_mover": (symbol, pct),  # best absolute mover
        }
      }

    Fail-open: returns synthetic demo data if Upstox is unavailable.
    """
    # Collect all symbols
    all_symbols: List[str] = []
    for stocks in NSE_SECTORS.values():
        all_symbols.extend(stocks)
    all_symbols = list(dict.fromkeys(all_symbols))  # deduplicate, preserve order

    ltps: Dict[str, float] = {}
    prev_closes: Dict[str, float] = {}

    if client is not None:
        ltps = _fetch_ltp_batch(all_symbols, client)
        if ltps:
            prev_closes = _fetch_prev_close(all_symbols, client)

    # If we got nothing, fall back to demo data
    if not ltps:
        logger.debug("Upstox unavailable — using synthetic demo data")
        return _synthetic_sector_data()

    result: SectorData = {}
    for sector, stocks in NSE_SECTORS.items():
        returns: List[Tuple[str, float]] = []
        for sym in stocks:
            ltp = ltps.get(sym)
            prev = prev_closes.get(sym)
            if ltp and prev and prev > 0:
                pct = (ltp - prev) / prev * 100.0
                returns.append((sym, round(pct, 2)))
            elif ltp:
                # prev close unavailable; use 0% placeholder
                returns.append((sym, 0.0))

        if not returns:
            result[sector] = {
                "ret_pct": 0.0,
                "advances": 0,
                "declines": 0,
                "top_mover": ("—", 0.0),
            }
            continue

        ret_values = [r for _, r in returns]
        avg_ret = sum(ret_values) / len(ret_values)
        advances = sum(1 for r in ret_values if r > 0)
        declines = sum(1 for r in ret_values if r < 0)
        top = max(returns, key=lambda x: abs(x[1]))

        result[sector] = {
            "ret_pct":   round(avg_ret, 2),
            "advances":  advances,
            "declines":  declines,
            "top_mover": top,
        }

    return result


def _synthetic_sector_data(seed: Optional[int] = None) -> SectorData:
    """
    Generate deterministic synthetic sector data for demo mode.
    Seed is today's date integer (YYYYMMDD) so output is stable within a day.
    """
    if seed is None:
        seed = int(datetime.now(IST).strftime("%Y%m%d"))
    rng = random.Random(seed)

    result: SectorData = {}
    for sector, stocks in NSE_SECTORS.items():
        # Sector bias: random walk centred at 0, max ±3%
        sector_bias = rng.uniform(-3.0, 3.0)
        returns: List[Tuple[str, float]] = []
        for sym in stocks:
            # Each stock: sector bias + idiosyncratic noise ±1.5%
            pct = sector_bias + rng.uniform(-1.5, 1.5)
            returns.append((sym, round(pct, 2)))

        ret_values = [r for _, r in returns]
        avg_ret = sum(ret_values) / len(ret_values)
        advances = sum(1 for r in ret_values if r > 0)
        declines = sum(1 for r in ret_values if r < 0)
        top = max(returns, key=lambda x: abs(x[1]))

        result[sector] = {
            "ret_pct":   round(avg_ret, 2),
            "advances":  advances,
            "declines":  declines,
            "top_mover": top,
        }
    return result


# ── 2. Rich heatmap table ──────────────────────────────────────────────────────

def _ret_color(ret: float) -> str:
    """Map return pct to a Rich colour name."""
    if ret > 1.5:
        return "bright_green bold"
    if ret > 0.5:
        return "green"
    if ret > 0.0:
        return "dim green"
    if ret < -1.5:
        return "bright_red bold"
    if ret < -0.5:
        return "red"
    return "dim red"


def _bar(ret: float, max_chars: int = 10) -> str:
    """
    ASCII bar proportional to return, max ±max_chars.
    Positive: filled ▌ on left side, dashes on right.
    Negative: dashes on left, filled ▌ on right.
    Zero: all dashes.
    """
    bar_char = "▌"
    empty_char = "░"
    clamped = max(-10.0, min(10.0, ret))
    filled = round(abs(clamped) / 10.0 * max_chars)

    if ret >= 0:
        return bar_char * filled + empty_char * (max_chars - filled)
    return empty_char * (max_chars - filled) + bar_char * filled


def render_sector_heatmap(sector_data: SectorData):
    """
    Build and return a Rich Table showing sector performance.

    Columns: Sector | Return | Bar | Breadth | Top Mover
    """
    from rich.table import Table
    from rich.text import Text

    table = Table(
        title="NSE Sector Heatmap",
        show_header=True,
        header_style="bold white",
        border_style="bright_black",
        expand=False,
    )
    table.add_column("Sector",    style="bold cyan", width=14, no_wrap=True)
    table.add_column("Return",    justify="right",   width=8)
    table.add_column("Bar",       justify="left",    width=12)
    table.add_column("Breadth",   justify="center",  width=8)
    table.add_column("Top Mover", justify="left",    width=16)

    # Sort sectors by return descending for impact
    sorted_sectors = sorted(
        sector_data.items(),
        key=lambda kv: kv[1].get("ret_pct", 0.0),
        reverse=True,
    )

    for sector, entry in sorted_sectors:
        ret = entry.get("ret_pct", 0.0)
        adv = entry.get("advances", 0)
        dec = entry.get("declines", 0)
        top_sym, top_pct = entry.get("top_mover", ("—", 0.0))

        color    = _ret_color(ret)
        ret_text = Text(f"{ret:+.2f}%", style=color)
        bar_text = Text(_bar(ret), style=color)
        breadth  = f"{adv}A/{dec}D"
        top_sign = "+" if top_pct >= 0 else ""
        top_str  = f"{top_sym} {top_sign}{top_pct:.1f}%"

        table.add_row(sector, ret_text, bar_text, breadth, top_str)

    return table


# ── 3. Top movers table ────────────────────────────────────────────────────────

def render_top_movers(sector_data: SectorData, n: int = 5):
    """
    Build a Rich Table of the top N gainers and top N losers across all sectors.

    Columns: Symbol | Sector | Return | Signal
    """
    from rich.table import Table
    from rich.text import Text

    # Gather (symbol, sector, pct) from top_mover per sector
    all_movers: List[Tuple[str, str, float]] = []
    for sector, entry in sector_data.items():
        top_sym, top_pct = entry.get("top_mover", ("—", 0.0))
        if top_sym != "—":
            all_movers.append((top_sym, sector, top_pct))

    all_movers.sort(key=lambda x: x[2], reverse=True)
    gainers = all_movers[:n]
    losers  = list(reversed(all_movers[-n:])) if len(all_movers) >= n else []

    table = Table(
        title=f"Top {n} Gainers & Losers",
        show_header=True,
        header_style="bold white",
        border_style="bright_black",
        expand=False,
    )
    table.add_column("Symbol",  style="bold", width=14, no_wrap=True)
    table.add_column("Sector",  width=14, no_wrap=True)
    table.add_column("Return",  justify="right", width=8)
    table.add_column("Signal",  width=12)

    for sym, sector, pct in gainers:
        color  = _ret_color(pct)
        signal = "STRONG BUY" if pct > 1.5 else ("BUY" if pct > 0.5 else "WATCH")
        table.add_row(
            Text(sym, style="bold green"),
            sector,
            Text(f"{pct:+.2f}%", style=color),
            Text(signal, style=color),
        )

    if gainers and losers:
        table.add_section()

    for sym, sector, pct in losers:
        color  = _ret_color(pct)
        signal = "STRONG SELL" if pct < -1.5 else ("SELL" if pct < -0.5 else "WATCH")
        table.add_row(
            Text(sym, style="bold red"),
            sector,
            Text(f"{pct:+.2f}%", style=color),
            Text(signal, style=color),
        )

    return table


# ── 4. Show full heatmap in terminal ──────────────────────────────────────────

def show_sector_heatmap(demo: bool = False) -> str:
    """
    Print the full heatmap + top movers to terminal using Rich Console.

    Returns a formatted text string suitable for Telegram.

    Args:
        demo: If True, uses synthetic data instead of live API calls.
    """
    from rich.console import Console
    from rich.rule import Rule

    console = Console()
    now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")

    if demo:
        sector_data = _synthetic_sector_data()
        console.print(Rule(f"[bold yellow]DEMO MODE[/bold yellow] — {now_ist}"))
    else:
        client = _get_upstox_client()
        sector_data = get_sector_performance(client)
        console.print(Rule(f"[bold cyan]NSE SECTOR HEATMAP[/bold cyan] — {now_ist}"))

    console.print(render_sector_heatmap(sector_data))
    console.print()
    console.print(render_top_movers(sector_data, n=5))
    console.print()

    return get_telegram_text(sector_data)


# ── 5. Telegram text formatter ─────────────────────────────────────────────────

def get_telegram_text(sector_data: SectorData) -> str:
    """
    Returns an HTML-formatted string for Telegram representing the heatmap.

    Format example:
      📊 NSE SECTOR HEATMAP  HH:MM IST
      ━━━━━━━━━━━━━━━━━━━━━━━━━━━
      🟢 IT      +1.24%  6A/1D  INFY +2.1%
      🔴 METAL   -0.92%  1A/5D  TATAST -1.8%
      ...
      ━━━━━━━━━━━━━━━━━━━━━━━━━━━
      🏆 TOP: INFY +2.1%  DRRED +1.9%
      💀 WORST: TATAST -1.8%  HINDALCO -1.5%
    """
    now_str = datetime.now(IST).strftime("%H:%M IST")
    sep = "━" * 27

    lines = [
        f"📊 <b>NSE SECTOR HEATMAP</b>  {now_str}",
        sep,
    ]

    sorted_sectors = sorted(
        sector_data.items(),
        key=lambda kv: kv[1].get("ret_pct", 0.0),
        reverse=True,
    )

    all_movers: List[Tuple[str, str, float]] = []

    for sector, entry in sorted_sectors:
        ret     = entry.get("ret_pct", 0.0)
        adv     = entry.get("advances", 0)
        dec     = entry.get("declines", 0)
        top_sym, top_pct = entry.get("top_mover", ("—", 0.0))

        icon      = "🟢" if ret >= 0 else "🔴"
        label     = _SECTOR_SHORT.get(sector, sector)
        ret_str   = f"{ret:+.2f}%"
        breadth   = f"{adv}A/{dec}D"
        top_sign  = "+" if top_pct >= 0 else ""
        top_label = f"{top_sym} {top_sign}{top_pct:.1f}%"

        # Bold strong moves
        if abs(ret) > 1.5:
            ret_str = f"<b>{ret_str}</b>"

        lines.append(
            f"{icon} {label:<7}  {ret_str:>8}  {breadth:>6}  {top_label}"
        )

        if top_sym != "—":
            all_movers.append((top_sym, sector, top_pct))

    lines.append(sep)

    all_movers.sort(key=lambda x: x[2], reverse=True)
    gainers_str = "  ".join(
        f"{s} {'+' if p >= 0 else ''}{p:.1f}%"
        for s, _, p in all_movers[:3]
    )
    losers_str = "  ".join(
        f"{s} {p:.1f}%"
        for s, _, p in reversed(all_movers[-3:])
    )

    if gainers_str:
        lines.append(f"🏆 TOP: {gainers_str}")
    if losers_str:
        lines.append(f"💀 WORST: {losers_str}")

    return "\n".join(lines)


# ── 6. Send heatmap via Telegram ──────────────────────────────────────────────

def send_sector_heatmap_telegram(client=None) -> bool:
    """
    Fetch live sector data, format as Telegram HTML, and send.

    Args:
        client: Optional Upstox client. If None, attempts to build one from env.

    Returns:
        True on success, False on failure.
    """
    try:
        if client is None:
            client = _get_upstox_client()
        sector_data = get_sector_performance(client)
        text = get_telegram_text(sector_data)
        return _send_telegram(text)
    except Exception as e:
        logger.warning(f"send_sector_heatmap_telegram: {e}")
        return False


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_upstox_client():
    """Attempt to build an Upstox client from environment. Returns None on failure."""
    try:
        import os
        import upstox_client  # type: ignore
        cfg = upstox_client.Configuration()
        cfg.access_token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
        if not cfg.access_token:
            return None
        api_client = upstox_client.ApiClient(configuration=cfg)

        class _UpstoxProxy:
            market_quote = upstox_client.MarketQuoteApi(api_client)
            history      = upstox_client.HistoryApi(api_client)

        return _UpstoxProxy()
    except Exception as e:
        logger.debug(f"_get_upstox_client: {e}")
        return None


def _send_telegram(text: str) -> bool:
    """Send an HTML-formatted message via Telegram Bot API."""
    try:
        import config_india as cfg  # type: ignore
        token = cfg.TELEGRAM_BOT_TOKEN
        chat  = cfg.TELEGRAM_CHAT_ID
        if not token or not chat:
            logger.info("[TG no-creds] %s", text[:120])
            return False
        import requests  # type: ignore
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        if resp.status_code == 200:
            logger.info("Sector heatmap sent to Telegram")
            return True
        logger.warning(
            "Telegram send failed: %s %s", resp.status_code, resp.text[:200]
        )
        return False
    except Exception as e:
        logger.warning(f"_send_telegram: {e}")
        return False


# ── CLI entry point ───────────────────────────────────────────────────────────

def _main() -> int:
    import argparse
    import logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="[%(asctime)s IST] [%(levelname)s] [sector_heatmap] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="NSE Sector Heatmap — Bloomberg HM equivalent"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Demo mode — synthesise data, no API calls",
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="Live mode + send heatmap to Telegram",
    )
    args = parser.parse_args()

    if args.demo:
        show_sector_heatmap(demo=True)
    elif args.telegram:
        from rich.console import Console
        from rich.rule import Rule

        client = _get_upstox_client()
        sector_data = get_sector_performance(client)

        console = Console()
        now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
        console.print(
            Rule(f"[bold cyan]NSE SECTOR HEATMAP[/bold cyan] — {now_ist}")
        )
        console.print(render_sector_heatmap(sector_data))
        console.print()
        console.print(render_top_movers(sector_data, n=5))
        console.print()

        ok = send_sector_heatmap_telegram(client)
        if ok:
            logger.info("Telegram delivery: OK")
        else:
            logger.warning("Telegram delivery: FAILED (check credentials)")
    else:
        # Live mode
        show_sector_heatmap(demo=False)

    return 0


if __name__ == "__main__":
    sys.exit(_main())
