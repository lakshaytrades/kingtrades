"""
options_screen_india.py — Bloomberg OMON-equivalent NIFTY Option Chain Intelligence Screen

Bloomberg OMON (Options Monitor) equivalent for NIFTY, rendered in the terminal
using the `rich` library.  Sections:
  1. Summary header  — key numbers in one line
  2. OI Bar Chart    — ATM ±5% strikes with ASCII bars, PCR per strike, IV columns
  3. Key Levels      — Max Pain, Call Wall, Put Wall, GEX Flip
  4. GEX Summary     — regime, total GEX (₹ Cr), percentile, trade bias
  5. IV Term Structure — near vs far expiry slope
  6. Change-in-OI PCR  — fresh money flow analysis

CLI usage:
  python3 india/options_screen_india.py           # live NSE data
  python3 india/options_screen_india.py --demo    # synthetic demo (no API)
  python3 india/options_screen_india.py --telegram # live + send to Telegram
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

# ── Path setup (allow running from any cwd) ────────────────────────────────────
_BASE = Path(__file__).parent.parent
sys.path.insert(0, str(_BASE))
sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv(_BASE / ".env")
except ImportError:
    pass

# ── IST timezone ──────────────────────────────────────────────────────────────
IST = ZoneInfo("Asia/Kolkata")

logger = logging.getLogger("options_screen_india")

# ── Rich imports (fail-open) ─────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box
    _RICH_OK = True
except ImportError:
    _RICH_OK = False
    logger.warning("rich not installed — terminal output degraded")


# ══════════════════════════════════════════════════════════════════════════════
#  Demo / Synthetic Data
# ══════════════════════════════════════════════════════════════════════════════

def _build_demo_strikes(spot: float = 24850.0) -> List[dict]:
    """
    Build a realistic synthetic NIFTY option chain centred around spot.
    Returns list of dicts matching real chain structure used by our callers.
    """
    step = 50
    atm_strike = round(spot / step) * step

    # (offset, call_oi, put_oi, call_iv, put_iv, call_chg_oi, put_chg_oi)
    strike_template = [
        (-250, 45_000,  20_000, 17.2, 18.5,  2_000,  1_000),
        (-200, 60_000,  30_000, 16.8, 17.9,  3_000,  1_500),
        (-150, 80_000,  55_000, 16.0, 17.2,  5_000,  2_500),
        (-100, 110_000, 180_000, 15.2, 16.2, 8_000, 20_000),  # put wall region
        ( -50, 160_000, 220_000, 14.5, 15.5, 10_000, 25_000),
        (   0, 310_000, 280_000, 13.1, 14.2, 18_000, 22_000),  # ATM
        (  50, 220_000, 140_000, 13.8, 14.9, 12_000, 15_000),
        ( 100, 180_000,  95_000, 14.2, 15.1,  9_000,  8_000),
        ( 150, 130_000,  65_000, 14.8, 15.6,  7_000,  4_000),
        ( 200,  90_000,  40_000, 15.5, 16.3,  4_000,  2_000),  # call wall region
        ( 250,  60_000,  25_000, 16.2, 17.0,  2_000,  1_000),
        ( 300,  40_000,  15_000, 17.0, 17.8,  1_000,    500),
        (-300,  30_000,  10_000, 18.0, 19.2,  1_500,    500),
    ]

    rows: List[dict] = []
    for offset, c_oi, p_oi, c_iv, p_iv, c_chg, p_chg in strike_template:
        strike = atm_strike + offset
        rows.append({
            "strike": float(strike),
            "call_oi": float(c_oi),
            "put_oi": float(p_oi),
            "call_iv": float(c_iv),
            "put_iv": float(p_iv),
            "call_chg_oi": float(c_chg),
            "put_chg_oi": float(p_chg),
        })

    return sorted(rows, key=lambda r: r["strike"])


def _build_demo_data() -> dict:
    """Return fully populated synthetic data dict — no API calls."""
    spot = 24850.0
    strikes = _build_demo_strikes(spot)

    total_call_oi = sum(r["call_oi"] for r in strikes)
    total_put_oi  = sum(r["put_oi"]  for r in strikes)
    pcr = round(total_put_oi / total_call_oi, 4) if total_call_oi else 1.0

    # Max pain: strike where total pain (ITM call + ITM put loss) is minimised
    pain_map: Dict[float, float] = {}
    for row in strikes:
        target = row["strike"]
        pain = 0.0
        for r in strikes:
            if r["strike"] < target:
                pain += r["call_oi"] * (target - r["strike"])
            if r["strike"] > target:
                pain += r["put_oi"] * (r["strike"] - target)
        pain_map[target] = pain
    max_pain = min(pain_map, key=pain_map.get)

    strikes_above = {r["strike"]: r["call_oi"] for r in strikes if r["strike"] > spot}
    strikes_below = {r["strike"]: r["put_oi"]  for r in strikes if r["strike"] < spot}
    call_wall = max(strikes_above, key=strikes_above.get) if strikes_above else 0.0
    put_wall  = max(strikes_below, key=strikes_below.get) if strikes_below else 0.0

    call_wall_oi = strikes_above.get(call_wall, 0.0)
    put_wall_oi  = strikes_below.get(put_wall,  0.0)

    total_call_chg = sum(max(r["call_chg_oi"], 0.0) for r in strikes)
    total_put_chg  = sum(max(r["put_chg_oi"],  0.0) for r in strikes)
    coi_pcr = round(total_put_chg / total_call_chg, 4) if total_call_chg else 1.0

    return {
        "symbol": "NIFTY",
        "spot": spot,
        "pcr": pcr,
        "max_pain": max_pain,
        "call_wall": call_wall,
        "call_wall_oi": call_wall_oi,
        "put_wall": put_wall,
        "put_wall_oi": put_wall_oi,
        "strikes": strikes,
        "gex_total": -42.3,
        "gex_regime": "NEGATIVE",
        "gex_percentile": 23.0,
        "gex_flip": 24_750.0,
        "iv_term_regime": "NORMAL",
        "iv_term_slope": -1.4,
        "near_expiry_label": "13-Jun",
        "near_iv": 13.8,
        "far_expiry_label": "26-Jun",
        "far_iv": 15.2,
        "coi_pcr": coi_pcr,
        "coi_pcr_regime": "BULLISH_BIAS",
        "new_call_oi": total_call_chg,
        "new_put_oi": total_put_chg,
        "vix": 14.2,
        "error": None,
        "demo": True,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Live Data Fetch
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_vix() -> float:
    """Fetch India VIX from NSE. Returns 0.0 on failure."""
    try:
        import requests
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })
        try:
            s.get("https://www.nseindia.com/", timeout=8)
        except Exception:
            pass
        resp = s.get(
            "https://www.nseindia.com/api/allIndices",
            headers={"Referer": "https://www.nseindia.com/"},
            timeout=10,
        )
        data = resp.json()
        for item in data.get("data", []):
            if item.get("index") == "INDIA VIX":
                return float(item.get("last", 0) or 0)
    except Exception as e:
        logger.debug(f"_fetch_vix: {e}")
    return 0.0


def _fetch_nse_chain_raw(symbol: str = "NIFTY") -> dict:
    """
    Fetch raw NSE option chain.  Returns processed dict with per-strike rows.
    Fail-open returns {}.
    """
    try:
        import requests
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })
        try:
            s.get("https://www.nseindia.com/", timeout=8)
        except Exception:
            pass
        url = (
            f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
            if symbol.upper() in ("NIFTY", "BANKNIFTY", "FINNIFTY")
            else f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"
        )
        resp = s.get(url, headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        records = data.get("records", {})
        raw_strikes = records.get("data", [])
        spot = float(records.get("underlyingValue", 0) or 0)
        expiry_dates: List[str] = records.get("expiryDates", [])

        rows: List[dict] = []
        for entry in raw_strikes:
            ce = entry.get("CE") or {}
            pe = entry.get("PE") or {}
            strike = float(entry.get("strikePrice") or 0)
            if strike <= 0:
                continue
            rows.append({
                "strike": strike,
                "call_oi": float(ce.get("openInterest") or 0),
                "put_oi":  float(pe.get("openInterest")  or 0),
                "call_iv": float(ce.get("impliedVolatility") or 0),
                "put_iv":  float(pe.get("impliedVolatility")  or 0),
                "call_chg_oi": float(ce.get("changeinOpenInterest") or 0),
                "put_chg_oi":  float(pe.get("changeinOpenInterest")  or 0),
            })

        return {
            "spot": spot,
            "strikes": rows,
            "expiry_dates": expiry_dates,
        }
    except Exception as e:
        logger.debug(f"_fetch_nse_chain_raw: {e}")
        return {}


def _get_iv_term_labels(expiry_dates: List[str]) -> Tuple[str, str]:
    """Return (near_label, far_label) from sorted expiry date list."""
    from datetime import date as _date
    today = datetime.now(IST).date()
    parsed: List[Tuple[_date, str]] = []
    for s in expiry_dates:
        for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
            try:
                from datetime import datetime as _dt
                d = _dt.strptime(s.strip(), fmt).date()
                if d >= today:
                    parsed.append((d, s))
                break
            except ValueError:
                pass
    parsed.sort(key=lambda x: x[0])
    near_label = parsed[0][1] if len(parsed) > 0 else "near"
    far_label  = parsed[1][1] if len(parsed) > 1 else "far"
    return near_label, far_label


def fetch_options_data(symbol: str = "NIFTY") -> dict:
    """
    Fetch all option chain metrics for the given NSE symbol.

    Returns a unified dict ready for rendering.  All external calls are
    wrapped in try/except so failures produce sensible defaults (fail-open).
    The returned dict always has the same keys regardless of errors.
    """
    now_ist = datetime.now(IST).strftime("%H:%M IST")

    # ── Safe defaults ─────────────────────────────────────────────────────────
    result: dict = {
        "symbol": symbol,
        "spot": 0.0,
        "pcr": 0.0,
        "max_pain": 0.0,
        "call_wall": 0.0,
        "call_wall_oi": 0.0,
        "put_wall": 0.0,
        "put_wall_oi": 0.0,
        "strikes": [],
        "gex_total": 0.0,
        "gex_regime": "NEUTRAL",
        "gex_percentile": 50.0,
        "gex_flip": 0.0,
        "iv_term_regime": "UNKNOWN",
        "iv_term_slope": 0.0,
        "near_expiry_label": "",
        "near_iv": 0.0,
        "far_expiry_label": "",
        "far_iv": 0.0,
        "coi_pcr": 1.0,
        "coi_pcr_regime": "NEUTRAL",
        "new_call_oi": 0.0,
        "new_put_oi": 0.0,
        "vix": 0.0,
        "timestamp": now_ist,
        "error": None,
        "demo": False,
    }

    # ── 1. Raw chain ─────────────────────────────────────────────────────────
    try:
        raw = _fetch_nse_chain_raw(symbol)
        if raw:
            result["spot"] = raw.get("spot", 0.0)
            result["strikes"] = raw.get("strikes", [])
    except Exception as e:
        result["error"] = str(e)

    spot = result["spot"]
    strikes_rows: List[dict] = result["strikes"]

    # ── 2. PCR from nse_option_chain module ──────────────────────────────────
    try:
        from nse_option_chain import (
            get_put_call_ratio,
            get_max_pain,
            get_call_wall,
            get_put_wall,
            get_change_in_oi_pcr,
        )
        pcr = get_put_call_ratio(symbol)
        if pcr > 0:
            result["pcr"] = pcr

        mp = get_max_pain(symbol)
        if mp:
            result["max_pain"] = mp

        if spot > 0:
            cw = get_call_wall(symbol, spot)
            pw = get_put_wall(symbol, spot)
            if cw:
                result["call_wall"] = cw
                cw_oi = next(
                    (r["call_oi"] for r in strikes_rows if r["strike"] == cw), 0.0
                )
                result["call_wall_oi"] = cw_oi
            if pw:
                result["put_wall"] = pw
                pw_oi = next(
                    (r["put_oi"] for r in strikes_rows if r["strike"] == pw), 0.0
                )
                result["put_wall_oi"] = pw_oi

        coi_pcr, coi_regime = get_change_in_oi_pcr(symbol)
        result["coi_pcr"] = coi_pcr
        result["coi_pcr_regime"] = coi_regime

        # Compute new call/put OI totals from raw rows
        new_call = sum(max(r["call_chg_oi"], 0.0) for r in strikes_rows)
        new_put  = sum(max(r["put_chg_oi"],  0.0) for r in strikes_rows)
        result["new_call_oi"] = new_call
        result["new_put_oi"]  = new_put

    except Exception as e:
        logger.debug(f"fetch_options_data nse_option_chain: {e}")

    # ── 3. GEX from gex_signal_india module ──────────────────────────────────
    try:
        from gex_signal_india import get_nifty_gex
        gex = get_nifty_gex()
        result["gex_total"]      = gex.get("gex_total", 0.0)
        result["gex_regime"]     = gex.get("regime", "NEUTRAL")
        result["gex_percentile"] = gex.get("gex_percentile", 50.0)
        result["gex_flip"]       = gex.get("flip_level", 0.0)
    except Exception as e:
        logger.debug(f"fetch_options_data gex: {e}")

    # ── 4. IV Term Structure ─────────────────────────────────────────────────
    try:
        from gex_signal_india import get_iv_term_structure
        ts_regime, ts_slope, ts_reason = get_iv_term_structure()
        result["iv_term_regime"] = ts_regime
        result["iv_term_slope"]  = ts_slope

        # Parse near/far IV values from the reason string if available
        # Fallback: compute from raw chain rows
        if raw and raw.get("expiry_dates"):
            near_lbl, far_lbl = _get_iv_term_labels(raw["expiry_dates"])
            result["near_expiry_label"] = near_lbl
            result["far_expiry_label"]  = far_lbl

        # Try to extract near/far IV from reason string (e.g. "near=13.8<far=15.2")
        import re
        if ts_reason:
            m_near = re.search(r"near=(\d+\.?\d*)", ts_reason)
            m_far  = re.search(r"far=(\d+\.?\d*)",  ts_reason)
            if m_near:
                result["near_iv"] = float(m_near.group(1))
            if m_far:
                result["far_iv"]  = float(m_far.group(1))

    except Exception as e:
        logger.debug(f"fetch_options_data iv_term: {e}")

    # ── 5. India VIX ─────────────────────────────────────────────────────────
    try:
        result["vix"] = _fetch_vix()
    except Exception as e:
        logger.debug(f"fetch_options_data vix: {e}")

    return result


# ══════════════════════════════════════════════════════════════════════════════
#  Rendering helpers
# ══════════════════════════════════════════════════════════════════════════════

_BAR_MAX_WIDTH = 10  # characters per side of the OI bar chart


def _oi_bar(oi: float, max_oi: float, width: int = _BAR_MAX_WIDTH) -> str:
    """Return ASCII bar string proportional to oi / max_oi."""
    if max_oi <= 0 or oi <= 0:
        return "░" * width
    filled = round(min(oi / max_oi, 1.0) * width)
    return "█" * filled + "░" * (width - filled)


def _pcr_label(pcr: float) -> Tuple[str, str]:
    """Return (label, color) for a PCR value."""
    if pcr >= 1.3:
        return ("VERY_BULLISH", "bright_green")
    if pcr >= 1.1:
        return ("BULLISH", "green")
    if pcr >= 0.9:
        return ("NEUTRAL", "yellow")
    if pcr >= 0.7:
        return ("BEARISH", "red")
    return ("VERY_BEARISH", "bright_red")


def _pcr_emoji(pcr: float) -> str:
    if pcr >= 1.1:
        return "🟢"
    if pcr >= 0.9:
        return "🟡"
    return "🔴"


def _gex_color(regime: str) -> str:
    if regime == "POSITIVE":
        return "cyan"
    if regime == "NEGATIVE":
        return "magenta"
    return "yellow"


def _coi_pcr_arrow(regime: str) -> str:
    if "BULL" in regime:
        return "↑"
    if "BEAR" in regime:
        return "↓"
    return "→"


def _bias_label(data: dict) -> Tuple[str, str]:
    """Derive overall directional bias from PCR, GEX, C-OI PCR."""
    bull_pts = 0
    bear_pts = 0

    pcr = data.get("pcr", 1.0)
    if pcr >= 1.1:
        bull_pts += 1
    elif pcr <= 0.9:
        bear_pts += 1

    gex = data.get("gex_regime", "NEUTRAL")
    if gex == "NEGATIVE":
        # momentum amplified — neutral on direction but track COI PCR
        pass

    coi = data.get("coi_pcr_regime", "NEUTRAL")
    if "BULL" in coi:
        bull_pts += 2
    elif "BEAR" in coi:
        bear_pts += 2

    if bull_pts > bear_pts:
        return ("BULLISH", "green")
    if bear_pts > bull_pts:
        return ("BEARISH", "red")
    return ("NEUTRAL", "yellow")


# ══════════════════════════════════════════════════════════════════════════════
#  Main screen renderer
# ══════════════════════════════════════════════════════════════════════════════

def render_options_screen(data: dict) -> None:
    """
    Render the full Bloomberg OMON-equivalent screen to the terminal.
    Uses `rich` if available, falls back to plain text.
    """
    if not _RICH_OK:
        _render_plain(data)
        return

    console = Console()
    symbol  = data.get("symbol", "NIFTY")
    spot    = data.get("spot", 0.0)
    pcr     = data.get("pcr", 0.0)
    gex_reg = data.get("gex_regime", "NEUTRAL")
    max_pain = data.get("max_pain", 0.0)
    iv_reg  = data.get("iv_term_regime", "UNKNOWN")
    vix     = data.get("vix", 0.0)
    ts      = data.get("timestamp", datetime.now(IST).strftime("%H:%M IST"))
    demo    = data.get("demo", False)

    pcr_lbl, _ = _pcr_label(pcr)
    pcr_icon    = _pcr_emoji(pcr)
    bias_lbl, bias_color = _bias_label(data)

    demo_tag = "  [dim][DEMO][/dim]" if demo else ""

    # ── 1. Summary Header ─────────────────────────────────────────────────────
    gex_display = f"GEX {gex_reg}"
    iv_display  = f"IV Term {iv_reg}"
    vix_display = f"VIX {vix:.1f}" if vix > 0 else "VIX --"

    header_text = (
        f"[bold white]{symbol}[/bold white]  "
        f"[bold yellow]{spot:,.0f}[/bold yellow]  [dim]│[/dim]  "
        f"PCR [bold]{pcr:.2f}[/bold] {pcr_icon}  [dim]│[/dim]  "
        f"[{_gex_color(gex_reg)}]{gex_display}[/{_gex_color(gex_reg)}]  [dim]│[/dim]  "
        f"Max Pain [bold]{max_pain:,.0f}[/bold]  [dim]│[/dim]  "
        f"{iv_display}  [dim]│[/dim]  "
        f"[cyan]{vix_display}[/cyan]"
        f"{demo_tag}"
    )
    console.print(Panel(Text.from_markup(header_text), title=f"[bold green]NIFTY OPTIONS INTEL  {ts}[/bold green]", box=box.DOUBLE_EDGE))

    # ── 2. OI Bar Chart ───────────────────────────────────────────────────────
    strikes_rows: List[dict] = data.get("strikes", [])
    call_wall  = data.get("call_wall", 0.0)
    put_wall   = data.get("put_wall", 0.0)

    # Filter ATM ±5%
    if spot > 0 and strikes_rows:
        atm_rows = [r for r in strikes_rows if abs(r["strike"] - spot) / spot <= 0.05]
    else:
        atm_rows = strikes_rows[:15]  # fallback

    if atm_rows:
        max_call_oi = max((r["call_oi"] for r in atm_rows), default=1.0) or 1.0
        max_put_oi  = max((r["put_oi"]  for r in atm_rows), default=1.0) or 1.0
        max_oi_both = max(max_call_oi, max_put_oi)

        tbl = Table(
            title=f"NIFTY OPTION CHAIN  (nearest expiry, ATM ±5%)  spot={spot:,.0f}",
            box=box.SIMPLE_HEAD,
            show_lines=False,
            header_style="bold cyan",
            title_style="bold white",
        )
        tbl.add_column("Strike",   justify="right",  style="white",        min_width=8)
        tbl.add_column("Call OI",  justify="right",  style="green",        min_width=9)
        tbl.add_column("← CALLS",  justify="right",  style="bright_green", min_width=12)
        tbl.add_column("│",        justify="center", style="dim",          min_width=1)
        tbl.add_column("PUTS →",   justify="left",   style="bright_red",   min_width=12)
        tbl.add_column("Put OI",   justify="right",  style="red",          min_width=9)
        tbl.add_column("PCR",      justify="right",  style="yellow",       min_width=5)
        tbl.add_column("IV Call",  justify="right",  style="cyan",         min_width=8)
        tbl.add_column("IV Put",   justify="right",  style="magenta",      min_width=7)

        for row in sorted(atm_rows, key=lambda r: r["strike"], reverse=True):
            st   = row["strike"]
            c_oi = row["call_oi"]
            p_oi = row["put_oi"]
            c_iv = row["call_iv"]
            p_iv = row["put_iv"]

            is_atm  = spot > 0 and abs(st - spot) <= 25
            is_cwall = (st == call_wall)
            is_pwall = (st == put_wall)

            row_pcr = round(p_oi / c_oi, 2) if c_oi > 0 else 0.0

            strike_str = f"{st:,.0f}"
            if is_atm:
                strike_str = f"[bold yellow]{strike_str}★[/bold yellow]"
            elif is_cwall:
                strike_str = f"[bold yellow]{strike_str}↑[/bold yellow]"
            elif is_pwall:
                strike_str = f"[bold yellow]{strike_str}↓[/bold yellow]"

            call_oi_str = f"{c_oi:,.0f}"
            put_oi_str  = f"{p_oi:,.0f}"
            if is_cwall:
                call_oi_str = f"[bold yellow]{call_oi_str}[/bold yellow]"
            if is_pwall:
                put_oi_str  = f"[bold yellow]{put_oi_str}[/bold yellow]"

            call_bar = _oi_bar(c_oi, max_oi_both)
            put_bar  = _oi_bar(p_oi, max_oi_both)

            atm_suffix = "  [dim]← ATM[/dim]" if is_atm else ""
            iv_c_str = f"{c_iv:.1f}%" if c_iv > 0 else "--"
            iv_p_str = f"{p_iv:.1f}%" if p_iv > 0 else "--"
            pcr_str  = f"{row_pcr:.2f}" if row_pcr > 0 else "--"

            tbl.add_row(
                strike_str + atm_suffix,
                call_oi_str,
                call_bar,
                "│",
                put_bar,
                put_oi_str,
                pcr_str,
                iv_c_str,
                iv_p_str,
            )

        console.print(tbl)
    else:
        console.print("[dim]No ATM strikes found (no data)[/dim]")

    # ── 3. Key Levels + 4. GEX + 5. IV Term + 6. C-OI PCR ───────────────────
    # Side-by-side panels using a Table with no borders
    layout = Table.grid(expand=True, padding=(0, 2))
    layout.add_column(ratio=1)
    layout.add_column(ratio=1)

    # -- Key Levels ---
    kl = Table(title="KEY LEVELS", box=box.SIMPLE, title_style="bold white", header_style="dim")
    kl.add_column("", style="dim")
    kl.add_column("", style="bold")
    kl.add_column("", style="dim")

    kl.add_row("Max Pain:",  f"{max_pain:,.0f}",  "(gravity level)" if max_pain else "")
    cw = data.get("call_wall", 0.0)
    pw = data.get("put_wall",  0.0)
    cw_oi = data.get("call_wall_oi", 0.0)
    pw_oi = data.get("put_wall_oi",  0.0)
    kl.add_row(
        "Call Wall:",
        f"[bold yellow]{cw:,.0f}[/bold yellow]" if cw else "--",
        f"(resistance, {cw_oi/1000:.0f}K OI)" if cw_oi else "(resistance)",
    )
    kl.add_row(
        "Put Wall:",
        f"[bold yellow]{pw:,.0f}[/bold yellow]" if pw else "--",
        f"(support, {pw_oi/1000:.0f}K OI)" if pw_oi else "(support)",
    )
    gex_flip = data.get("gex_flip", 0.0)
    kl.add_row(
        "GEX Flip:",
        f"{gex_flip:,.0f}" if gex_flip else "--",
        "(intraday pivot)" if gex_flip else "",
    )

    # -- GEX Summary ---
    gex_total   = data.get("gex_total", 0.0)
    gex_pct     = data.get("gex_percentile", 50.0)
    gex_reg     = data.get("gex_regime", "NEUTRAL")
    gex_desc = {
        "NEGATIVE": "dealer short-gamma → amplifies moves",
        "POSITIVE": "dealer long-gamma → dampens moves",
        "NEUTRAL": "near flip zone",
    }.get(gex_reg, "")
    gex_signal = "MOMENTUM setups preferred  ↗" if gex_reg == "NEGATIVE" else \
                 "MEAN-REVERSION setups preferred  ↔" if gex_reg == "POSITIVE" else \
                 "No regime edge"

    gx = Table(title="GAMMA EXPOSURE", box=box.SIMPLE, title_style="bold white", header_style="dim")
    gx.add_column("", style="dim")
    gx.add_column("", style="bold")

    gx.add_row("Regime:",    f"[{_gex_color(gex_reg)}]{gex_reg} GEX[/{_gex_color(gex_reg)}]  ({gex_desc})")
    gx.add_row("GEX Total:", f"{'−' if gex_total < 0 else '+'}₹{abs(gex_total):.1f} Cr")
    gx.add_row("Percentile:", f"{gex_pct:.0f}th (of 20-day history)")
    gx.add_row("Signal:",    gex_signal)

    layout.add_row(kl, gx)
    console.print(layout)

    # -- IV Term Structure ---
    iv_reg   = data.get("iv_term_regime", "UNKNOWN")
    iv_slope = data.get("iv_term_slope",  0.0)
    near_lbl = data.get("near_expiry_label", "near")
    near_iv  = data.get("near_iv", 0.0)
    far_lbl  = data.get("far_expiry_label",  "far")
    far_iv   = data.get("far_iv",  0.0)

    iv_slope_desc = {
        "NORMAL":   "no near-term fear",
        "INVERTED": "near-term fear / event risk",
        "FLAT":     "term structure flat",
        "UNKNOWN":  "data unavailable",
    }.get(iv_reg, "")
    slope_sign = f"{iv_slope:+.1f}%" if iv_slope != 0 else "--"

    iv_panel = Table(title="IV TERM STRUCTURE", box=box.SIMPLE, title_style="bold white", header_style="dim")
    iv_panel.add_column("", style="dim")
    iv_panel.add_column("", style="bold")

    iv_panel.add_row(f"Near expiry ({near_lbl}):", f"{near_iv:.1f}% ATM" if near_iv else "--")
    iv_panel.add_row(f"Far expiry  ({far_lbl}):",  f"{far_iv:.1f}% ATM"  if far_iv  else "--")
    iv_panel.add_row("Slope:", f"{slope_sign}  ({iv_reg} — {iv_slope_desc})")

    # -- Change-in-OI PCR ---
    coi_pcr    = data.get("coi_pcr", 1.0)
    coi_regime = data.get("coi_pcr_regime", "NEUTRAL")
    new_call   = data.get("new_call_oi", 0.0)
    new_put    = data.get("new_put_oi",  0.0)
    coi_arrow  = _coi_pcr_arrow(coi_regime)

    coi_panel = Table(title="CHANGE-IN-OI PCR  (new positions only)", box=box.SIMPLE, title_style="bold white", header_style="dim")
    coi_panel.add_column("", style="dim")
    coi_panel.add_column("", style="bold")

    coi_panel.add_row("New Call OI:", f"+{new_call:,.0f}" if new_call else "--")
    coi_panel.add_row("New Put OI:",  f"+{new_put:,.0f}"  if new_put  else "--")
    coi_panel.add_row("C-OI PCR:",   f"{coi_pcr:.2f}  [bold]{coi_regime}[/bold]  {coi_arrow}")

    layout2 = Table.grid(expand=True, padding=(0, 2))
    layout2.add_column(ratio=1)
    layout2.add_column(ratio=1)
    layout2.add_row(iv_panel, coi_panel)
    console.print(layout2)

    # ── Bias Footer ───────────────────────────────────────────────────────────
    bias_lbl, bias_color = _bias_label(data)
    bias_text = (
        f"[bold {bias_color}]🎯 BIAS: {bias_lbl}[/bold {bias_color}]"
        f"  —  prefer [bold]{'LONG' if bias_lbl == 'BULLISH' else 'SHORT' if bias_lbl == 'BEARISH' else 'NEUTRAL'} setups[/bold]"
    )
    console.print(Panel(Text.from_markup(bias_text), box=box.SIMPLE))


def _render_plain(data: dict) -> None:
    """Fallback plain-text renderer when rich is not installed."""
    symbol = data.get("symbol", "NIFTY")
    spot   = data.get("spot", 0.0)
    pcr    = data.get("pcr", 0.0)
    gex_reg = data.get("gex_regime", "NEUTRAL")
    max_pain = data.get("max_pain", 0.0)
    iv_reg = data.get("iv_term_regime", "UNKNOWN")
    vix    = data.get("vix", 0.0)
    ts     = data.get("timestamp", datetime.now(IST).strftime("%H:%M IST"))

    print(f"\nNIFTY OPTIONS INTEL  {ts}")
    print("=" * 70)
    print(f"{symbol}  {spot:,.0f}  |  PCR {pcr:.2f}  |  GEX {gex_reg}  |  Max Pain {max_pain:,.0f}  |  IV Term {iv_reg}  |  VIX {vix:.1f}")
    print("=" * 70)

    strikes_rows: List[dict] = data.get("strikes", [])
    if strikes_rows and spot > 0:
        atm_rows = [r for r in strikes_rows if abs(r["strike"] - spot) / spot <= 0.05]
        max_c = max((r["call_oi"] for r in atm_rows), default=1.0) or 1.0
        max_p = max((r["put_oi"]  for r in atm_rows), default=1.0) or 1.0
        max_oi = max(max_c, max_p)
        print(f"\n{'Strike':>9}  {'Call OI':>9}  {'<CALLS':>10}  |  {'PUTS>':10}  {'Put OI':>9}  {'PCR':>5}  {'IV C':>6}  {'IV P':>6}")
        print("-" * 80)
        for row in sorted(atm_rows, key=lambda r: r["strike"], reverse=True):
            st = row["strike"]
            atm_marker = "★" if spot > 0 and abs(st - spot) <= 25 else " "
            call_bar = _oi_bar(row["call_oi"], max_oi)
            put_bar  = _oi_bar(row["put_oi"],  max_oi)
            row_pcr  = row["put_oi"] / row["call_oi"] if row["call_oi"] > 0 else 0
            print(
                f"{st:>8,.0f}{atm_marker}  {row['call_oi']:>9,.0f}  {call_bar:>10}  |  "
                f"{put_bar:10}  {row['put_oi']:>9,.0f}  {row_pcr:>5.2f}  "
                f"{row['call_iv']:>5.1f}%  {row['put_iv']:>5.1f}%"
            )

    print(f"\nMax Pain: {data.get('max_pain',0):,.0f}  |  Call Wall: {data.get('call_wall',0):,.0f}  |  Put Wall: {data.get('put_wall',0):,.0f}")
    print(f"GEX: {data.get('gex_total',0):+.1f} Cr  ({data.get('gex_regime','--')})  Pct={data.get('gex_percentile',50):.0f}th")
    print(f"C-OI PCR: {data.get('coi_pcr',1):.2f}  {data.get('coi_pcr_regime','--')}")
    bias_lbl, _ = _bias_label(data)
    print(f"\nBIAS: {bias_lbl}")


# ══════════════════════════════════════════════════════════════════════════════
#  Telegram
# ══════════════════════════════════════════════════════════════════════════════

def get_telegram_text(data: dict) -> str:
    """
    Return a concise HTML-formatted Telegram message summarising options intel.
    """
    symbol   = data.get("symbol", "NIFTY")
    pcr      = data.get("pcr", 0.0)
    gex_reg  = data.get("gex_regime", "NEUTRAL")
    max_pain = data.get("max_pain", 0.0)
    cw       = data.get("call_wall", 0.0)
    pw       = data.get("put_wall", 0.0)
    coi_pcr  = data.get("coi_pcr", 1.0)
    coi_reg  = data.get("coi_pcr_regime", "NEUTRAL")
    iv_reg   = data.get("iv_term_regime", "UNKNOWN")
    near_iv  = data.get("near_iv", 0.0)
    far_iv   = data.get("far_iv", 0.0)
    spot     = data.get("spot", 0.0)
    ts       = data.get("timestamp", datetime.now(IST).strftime("%H:%M IST"))
    demo     = data.get("demo", False)

    pcr_lbl, _ = _pcr_label(pcr)
    pcr_icon   = _pcr_emoji(pcr)
    bias_lbl, _ = _bias_label(data)

    gex_note = {
        "NEGATIVE": "NEGATIVE → momentum favoured",
        "POSITIVE": "POSITIVE → range-bound favoured",
        "NEUTRAL":  "NEUTRAL",
    }.get(gex_reg, gex_reg)

    spot_vs_pain = ""
    if spot > 0 and max_pain > 0:
        if spot > max_pain:
            spot_vs_pain = f"  (spot {spot:,.0f} above)"
        elif spot < max_pain:
            spot_vs_pain = f"  (spot {spot:,.0f} below)"

    near_iv_str = f"{near_iv:.1f}%" if near_iv else "--"
    far_iv_str  = f"{far_iv:.1f}%"  if far_iv  else "--"

    bias_arrow = "⬆️" if bias_lbl == "BULLISH" else "⬇️" if bias_lbl == "BEARISH" else "➡️"
    demo_note = "\n<i>[DEMO DATA]</i>" if demo else ""

    lines = [
        f"📊 <b>{symbol} OPTIONS INTEL  {ts}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"PCR       {pcr:.2f}  {pcr_icon} {pcr_lbl}",
        f"GEX       {gex_note}",
        f"Max Pain  {max_pain:,.0f}{spot_vs_pain}",
        f"Call Wall {cw:,.0f}  │  Put Wall {pw:,.0f}",
        f"C-OI PCR  {coi_pcr:.2f}  {coi_reg}",
        f"IV Term   {iv_reg}  near {near_iv_str}  far {far_iv_str}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"{bias_arrow} BIAS: <b>{bias_lbl}</b> — prefer <b>{'LONG' if bias_lbl == 'BULLISH' else 'SHORT' if bias_lbl == 'BEARISH' else 'NEUTRAL'} setups</b>",
        demo_note,
    ]
    return "\n".join(l for l in lines if l is not None)


def _send_telegram(text: str) -> None:
    """Send HTML message to Telegram. Fail-open."""
    try:
        import requests as _req
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat  = os.getenv("TELEGRAM_CHAT_ID",   "")
        if not token or not chat:
            try:
                import sys
                sys.path.insert(0, str(_BASE / "india"))
                import config_india as _cfg
                token = getattr(_cfg, "TELEGRAM_BOT_TOKEN", "")
                chat  = getattr(_cfg, "TELEGRAM_CHAT_ID",   "")
            except Exception:
                pass
        if not token or not chat:
            logger.info("[TG] No credentials — printing instead:\n" + text)
            return
        _req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        logger.info("[TG] Options intel sent.")
    except Exception as e:
        logger.debug(f"_send_telegram: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  Public entry points
# ══════════════════════════════════════════════════════════════════════════════

def show_options_screen(demo: bool = False, symbol: str = "NIFTY") -> None:
    """Print full screen to terminal."""
    if demo:
        data = _build_demo_data()
        data["timestamp"] = datetime.now(IST).strftime("%H:%M IST")
    else:
        data = fetch_options_data(symbol)
    render_options_screen(data)


def send_options_telegram(demo: bool = False, symbol: str = "NIFTY") -> None:
    """Fetch live data and send compact summary to Telegram."""
    if demo:
        data = _build_demo_data()
        data["timestamp"] = datetime.now(IST).strftime("%H:%M IST")
    else:
        data = fetch_options_data(symbol)
    text = get_telegram_text(data)
    _send_telegram(text)


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NIFTY Options Intelligence Screen (Bloomberg OMON equivalent)"
    )
    parser.add_argument("--demo",     action="store_true", help="Use synthetic demo data (no API)")
    parser.add_argument("--telegram", action="store_true", help="Also send compact summary to Telegram")
    parser.add_argument("--symbol",   default="NIFTY",     help="NSE symbol (default: NIFTY)")
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args()

    show_options_screen(demo=args.demo, symbol=args.symbol)

    if args.telegram:
        send_options_telegram(demo=args.demo, symbol=args.symbol)
