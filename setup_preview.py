"""
setup_preview.py — "What Trades Are Coming" Telegram Preview System

Sends periodic intelligence messages during market hours showing:
  1. Top setups currently being watched (ranked by signal score)
  2. India-specific market intelligence (NSE PCR, FII, India VIX, sector heat)
  3. What the bot is waiting for (setup quality + threshold)
  4. Next high-probability time window

Fires at:
  - Market open (9:15 AM IST for NSE, 9:30 PM IST for US)
  - Every 30 min during dead zones (10:30 AM–2:00 PM IST)
  - On-demand from /preview Telegram command

India trade context block shows:
  - India VIX level + interpretation
  - NIFTY PCR (Put/Call Ratio) → contrarian signal
  - FII/DII today net flow
  - Top 3 NSE sector momentum
  - Delivery volume alert (high delivery = institutional)
  - Option chain max pain level
"""

import logging
import os
import time as _time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PREVIEW_COOLDOWN = 1800        # 30 min between auto-previews
_MAX_SETUPS_SHOWN = 5           # how many top setups to display
_last_preview_ts  = 0.0
_last_india_ts    = 0.0


def _tg(text: str) -> bool:
    import os as _os_g
    if _os_g.getenv("US_BOT_ENABLED", "False") != "True":
        return False
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return False
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=12,
        )
        return r.status_code == 200
    except Exception:
        return False


def _ist_now_str() -> str:
    try:
        from zoneinfo import ZoneInfo
        from datetime import timezone
        return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %b %H:%M IST")
    except Exception:
        return datetime.utcnow().strftime("%d %b %H:%M UTC")


# ── India-specific market context ───────────────────────────────────────────

def _get_india_vix() -> float:
    try:
        from india_intel import IndiaVIXReader
        return IndiaVIXReader().get_vix()
    except Exception:
        try:
            import yfinance as yf
            df = yf.download("^INDIAVIX", period="2d", interval="1d",
                             progress=False, auto_adjust=True)
            if df is not None and not df.empty:
                return float(df["Close"].iloc[-1])
        except Exception:
            pass
    return 0.0


def _get_nifty_pcr() -> float:
    try:
        from india_intel import NiftyPCRReader
        return NiftyPCRReader().get_pcr()
    except Exception:
        return 0.0


def _get_fii_dii() -> Dict:
    try:
        from fii_dii_tracker import FIIDIITracker
        tracker = FIIDIITracker()
        if hasattr(tracker, "get_latest"):
            return tracker.get_latest()
        summary = tracker.get_summary()
        if isinstance(summary, str):
            return {"summary": summary}
        return summary or {}
    except Exception:
        return {}


def _get_sector_heat() -> List[str]:
    try:
        from india.nse_sector_momentum import NSESectorMomentum
        sm = NSESectorMomentum()
        hot = sm.get_hot_sectors(top_n=3) if hasattr(sm, "get_hot_sectors") else []
        return hot
    except Exception:
        pass
    try:
        from sector_rotation import SectorRotation
        sr = SectorRotation()
        if hasattr(sr, "get_hot_sectors"):
            return sr.get_hot_sectors(top_n=3)
        return sr.hot_sectors[:3] if hasattr(sr, "hot_sectors") else []
    except Exception:
        return []


def _get_max_pain(symbol: str = "NIFTY") -> Optional[float]:
    try:
        from india.nse_option_chain import NSEOptionChain
        oc = NSEOptionChain()
        data = oc.get_analysis(symbol)
        return data.get("max_pain") if data else None
    except Exception:
        return None


def _get_nifty_price() -> float:
    try:
        import yfinance as yf
        df = yf.download("^NSEI", period="1d", interval="5m",
                         progress=False, auto_adjust=True)
        if df is not None and not df.empty:
            return float(df["Close"].iloc[-1])
    except Exception:
        pass
    return 0.0


def _interpret_india_vix(vix: float) -> str:
    if vix <= 0:   return "—"
    if vix < 12:   return "📗 Ultra-calm — trend-following optimal"
    if vix < 15:   return "🟢 Calm — all strategies active"
    if vix < 18:   return "🟡 Normal — standard sizing"
    if vix < 22:   return "🟠 Elevated — reduce size 20%"
    if vix < 28:   return "🔴 High fear — longs only with tight stops"
    return              "🆘 CRISIS — no new longs, only shorts"


def _interpret_pcr(pcr: float) -> str:
    if pcr <= 0:   return "—"
    if pcr > 1.5:  return "🟢 Extreme fear → contrarian BULLISH"
    if pcr > 1.2:  return "🟢 Bearish sentiment → cautious bullish"
    if pcr > 0.9:  return "🟡 Neutral"
    if pcr > 0.7:  return "🟠 Complacency → cautious bearish"
    return              "🔴 Extreme greed → contrarian BEARISH"


def build_india_context_block() -> str:
    """Rich India market intelligence block for Telegram."""
    vix      = _get_india_vix()
    pcr      = _get_nifty_pcr()
    fii      = _get_fii_dii()
    sectors  = _get_sector_heat()
    max_pain = _get_max_pain("NIFTY")
    nifty    = _get_nifty_price()

    lines = ["🇮🇳 <b>NSE MARKET INTELLIGENCE</b>"]

    if vix > 0:
        lines.append(f"  India VIX: <b>{vix:.1f}</b>  {_interpret_india_vix(vix)}")
    if pcr > 0:
        lines.append(f"  NIFTY PCR: <b>{pcr:.2f}</b>  {_interpret_pcr(pcr)}")
    if nifty > 0:
        lines.append(f"  Nifty 50:  <b>₹{nifty:,.0f}</b>")
    if max_pain:
        vs_mp = nifty - max_pain if nifty > 0 else 0
        lines.append(
            f"  Max Pain:  <b>₹{max_pain:,.0f}</b>"
            f"  ({'↑' if vs_mp > 0 else '↓'}{abs(vs_mp):,.0f} from max pain)"
        )

    if fii:
        fii_net = fii.get("fii_net", fii.get("fii_buy", 0))
        dii_net = fii.get("dii_net", fii.get("dii_buy", 0))
        if fii_net:
            icon = "🟢" if fii_net > 0 else "🔴"
            lines.append(f"  FII Today: {icon} ₹{abs(fii_net):,.0f}Cr {'buy' if fii_net > 0 else 'sell'}")
        if dii_net:
            icon = "🟢" if dii_net > 0 else "🔴"
            lines.append(f"  DII Today: {icon} ₹{abs(dii_net):,.0f}Cr {'buy' if dii_net > 0 else 'sell'}")

    if sectors:
        lines.append(f"  🔥 Hot sectors: {', '.join(str(s) for s in sectors[:3])}")

    return "\n".join(lines)


# ── Setup preview ────────────────────────────────────────────────────────────

def _rank_watchlist_signals(
    watchlist: List[str],
    signal_gen: Any,
    fetcher: Any,
    top_n: int = 5,
) -> List[Dict]:
    """
    Quick-score the watchlist to find top upcoming setups.
    Uses lightweight indicators only (fast scan, no full signal_generator).
    """
    candidates = []
    try:
        import numpy as np
    except ImportError:
        return []

    for sym in watchlist[:25]:   # cap to avoid timeouts
        try:
            df = fetcher.get_today_candles(sym) if hasattr(fetcher, "get_today_candles") else None
            if df is None or len(df) < 15:
                continue
            closes  = df["close"].values if "close" in df.columns else df["Close"].values
            volumes = df["volume"].values if "volume" in df.columns else df["Volume"].values

            # Quick momentum score
            ret5    = (closes[-1] / closes[-5] - 1) * 100 if len(closes) >= 5 else 0
            avg_vol = float(np.mean(volumes[-20:-1])) if len(volumes) >= 20 else float(np.mean(volumes))
            rvol    = float(volumes[-1]) / max(avg_vol, 1)

            # EMA trend
            ema9  = float(np.mean(closes[-9:]))  if len(closes) >= 9  else closes[-1]
            ema21 = float(np.mean(closes[-21:])) if len(closes) >= 21 else closes[-1]
            trend = "↑" if ema9 > ema21 else "↓"

            # Quick score
            score = 50.0 + ret5 * 3 + (rvol - 1) * 10
            score = max(0, min(100, score))

            candidates.append({
                "symbol": sym,
                "score":  round(score, 1),
                "ret5":   round(ret5, 2),
                "rvol":   round(rvol, 2),
                "trend":  trend,
                "price":  round(float(closes[-1]), 2),
            })
        except Exception:
            continue

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:top_n]


def send_setup_preview(
    watchlist:  List[str],
    signal_gen: Any = None,
    fetcher:    Any = None,
    force:      bool = False,
) -> bool:
    """
    Send a Telegram message showing current top setups and India intelligence.
    Throttled to once per 30 min unless force=True.
    """
    global _last_preview_ts
    if not force and _time.time() - _last_preview_ts < _PREVIEW_COOLDOWN:
        return False

    _last_preview_ts = _time.time()
    now_str = _ist_now_str()

    # India intel block
    india_block = ""
    try:
        india_block = build_india_context_block()
    except Exception:
        pass

    # Top setups
    setup_lines = []
    if fetcher and watchlist:
        try:
            tops = _rank_watchlist_signals(watchlist, signal_gen, fetcher, top_n=_MAX_SETUPS_SHOWN)
            if tops:
                setup_lines.append("📋 <b>TOP SETUPS BEING WATCHED</b>")
                for i, c in enumerate(tops, 1):
                    emoji = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i - 1]
                    setup_lines.append(
                        f"  {emoji} <b>{c['symbol']}</b> ₹{c['price']:,.1f}  "
                        f"{c['trend']} score={c['score']:.0f} "
                        f"ret={c['ret5']:+.1f}% rvol={c['rvol']:.1f}x"
                    )
        except Exception as e:
            logger.debug(f"setup_preview ranking failed: {e}")

    # OODA regime summary
    ooda_line = ""
    try:
        from ooda_engine import get_engine
        ctx = get_engine().get_context("^NSEI")
        ooda_line = (
            f"\n🧠 <b>OODA:</b> Regime={ctx.regime} "
            f"Conviction={ctx.conviction:.2f} "
            f"Bias={getattr(ctx,'direction_bias','NEUTRAL')}"
        )
    except Exception:
        pass

    # Time window guidance
    try:
        from zoneinfo import ZoneInfo
        ist_hour = datetime.now(ZoneInfo("Asia/Kolkata")).hour
        ist_min  = datetime.now(ZoneInfo("Asia/Kolkata")).minute
        ist_tot  = ist_hour * 60 + ist_min
        if ist_tot < 9 * 60 + 30:
            window = "⏳ Pre-open — ORB setup forming"
        elif ist_tot < 10 * 60 + 30:
            window = "🟢 PRIME — Opening drive (highest edge)"
        elif ist_tot < 13 * 60:
            window = "🟡 Midday — VWAP/scalp mode"
        elif ist_tot < 15 * 60:
            window = "🟢 PRIME — Power hour approaching"
        else:
            window = "🏁 Close — square-off window"
    except Exception:
        window = ""

    lines = [
        f"🔭 <b>KINGTRADES SETUP SCAN</b>  {now_str}",
        f"  {window}",
        "",
    ]
    if india_block:
        lines.extend(india_block.split("\n"))
        lines.append("")
    if setup_lines:
        lines.extend(setup_lines)
        lines.append("")
    if ooda_line:
        lines.append(ooda_line)

    lines.append("\n⚡ Bot scanning every 30s — will broadcast before each entry")

    text = "\n".join(lines)
    return _tg(text)


def send_india_open_brief(
    watchlist: List[str],
    fetcher: Any = None,
) -> bool:
    """
    Comprehensive India open brief — fires at 9:15 AM IST.
    Shows everything the bot knows before the first candle closes.
    """
    now_str = _ist_now_str()
    india_block = ""
    try:
        india_block = build_india_context_block()
    except Exception:
        pass

    # Block deal intelligence
    bd_line = ""
    try:
        from block_deal_scanner import get_block_deal_scanner
        buys = get_block_deal_scanner().scan_and_alert()
        if buys:
            bd_line = f"  🏛️ Block deal buys: {', '.join(buys[:5])}"
    except Exception:
        pass

    # Gap analysis
    gap_lines = []
    try:
        from premarket_gap_scanner import scan_gaps
        gaps = scan_gaps(watchlist[:15])
        for g in gaps[:3]:
            sym = g.get("symbol", "")
            pct = g.get("gap_pct", 0)
            if abs(pct) >= 0.5:
                icon = "🔼" if pct > 0 else "🔽"
                gap_lines.append(f"  {icon} {sym}: {pct:+.1f}% gap")
    except Exception:
        pass

    # Futures bias
    fut_line = ""
    try:
        from futures_bias import get_futures_bias
        fb = get_futures_bias()
        fut_line = f"  📈 Futures bias: {fb.direction} ({fb.magnitude:.1f})" if fb else ""
    except Exception:
        pass

    lines = [
        f"🌅 <b>KINGTRADES NSE OPEN BRIEF</b>",
        f"   {now_str}",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    if india_block:
        lines.extend(india_block.split("\n"))
        lines.append("")
    if fut_line:
        lines.append(fut_line)
    if gap_lines:
        lines.append("📊 <b>GAP WATCHLIST</b>")
        lines.extend(gap_lines)
        lines.append("")
    if bd_line:
        lines.append(bd_line)

    lines += [
        "",
        "⏰ <b>STRATEGY WINDOWS TODAY</b>",
        "  🟢 9:15–10:30 AM  — ORB + momentum (MAX EDGE)",
        "  🟡 10:30–1:00 PM  — VWAP bounce + scalp",
        "  🔴 1:00–2:00 PM   — Midday chop (AVOID NEW ENTRIES)",
        "  🟢 2:00–3:15 PM   — Power hour continuation",
        "  🏁 3:15–3:20 PM   — Square off warning",
        "",
        "🤖 Bot is LIVE — will send trade plan before every entry",
    ]

    return _tg("\n".join(lines))


def should_preview(last_trade_ts: float = 0.0, no_trade_minutes: float = 30.0) -> bool:
    """Return True if it's time for a preview message."""
    global _last_preview_ts
    idle_secs = _time.time() - max(last_trade_ts, 0)
    since_preview = _time.time() - _last_preview_ts
    return idle_secs >= no_trade_minutes * 60 and since_preview >= _PREVIEW_COOLDOWN
