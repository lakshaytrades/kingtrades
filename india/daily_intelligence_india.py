"""
daily_intelligence_india.py — India Bot Daily Intelligence
India specialist: INR reports, Nifty benchmark, India VIX, FII/DII context.

Sends via Telegram:
  Morning (9:10 AM IST): Nifty level, India VIX, regime, RBI events, day plan
  EOD (4:00 PM IST):     All trades with WHY, INR P&L, monthly target progress

Usage (crontab on VPS):
  10 9  * * 1-5  cd /root/kingtrades/india && python3 daily_intelligence_india.py morning >> /root/kingtrades/logs/india_intel.log 2>&1
  0  16 * * 1-5  cd /root/kingtrades/india && python3 daily_intelligence_india.py eod >> /root/kingtrades/logs/india_intel.log 2>&1
"""
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

import config_india as cfg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [IST] [INDIA-INTEL] %(message)s",
)
logger = logging.getLogger("india_intel")
IST   = ZoneInfo("Asia/Kolkata")

DATA_DIR       = Path(__file__).parent.parent / "data"
DECISIONS_FILE = DATA_DIR / "india_trade_decisions.json"
MONTHLY_FILE   = DATA_DIR / "india_monthly_pnl.json"
DATA_DIR.mkdir(exist_ok=True)

MONTHLY_TARGET_INR = float(os.getenv("INDIA_MONTHLY_TARGET_INR", "25000"))


# ── Telegram ──────────────────────────────────────────────────────────────────

def _send(msg: str, parse_mode: str = "Markdown"):
    try:
        token = cfg.TELEGRAM_BOT_TOKEN
        chat  = cfg.TELEGRAM_CHAT_ID
        if not token or not chat:
            logger.info(f"[TG] {msg[:120]}")
            return
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg, "parse_mode": parse_mode},
            timeout=10,
        )
    except Exception as e:
        logger.debug(f"telegram: {e}")


# ── Trade decision recording ──────────────────────────────────────────────────

def explain_trade(signal) -> str:
    """
    Convert IndiaTradeSignal to plain-English WHY (INR).
    Saves to india_trade_decisions.json.
    Returns Telegram-ready string.
    """
    reasons: List[str] = []
    try:
        score = getattr(signal, "signal_score", 0)
        direction = getattr(signal, "direction", "?")
        entry = getattr(signal, "entry_price", 0)
        sl    = getattr(signal, "stop_loss", 0)
        tp    = getattr(signal, "target_1", 0)
        rr    = getattr(signal, "risk_reward", 0)
        grade = getattr(signal, "quality_grade", "B")
        symbol= getattr(signal, "symbol", "?")

        reasons.append(f"Score {score:.0f}/100 — Grade {grade}")

        if direction == "LONG":
            reasons.append("Momentum bullish across timeframes")
        else:
            reasons.append("Momentum bearish across timeframes")

        for pat in getattr(signal, "patterns", [])[:3]:
            reasons.append(f"Pattern: {pat}")

        if score >= cfg.GRAND_SLAM_MIN_SCORE:
            reasons.append("Grand Slam setup — maximum conviction")

        risk_inr = abs(entry - sl) * getattr(signal, "quantity", 1)
        reward_inr = abs(tp - entry) * getattr(signal, "quantity", 1)
        reasons.append(f"Risk ₹{risk_inr:.0f} → Reward ₹{reward_inr:.0f} (R:R {rr:.1f})")

        # Save decision
        _save_decision(symbol, direction, entry, sl, tp, reasons, score, grade)

        dir_word = "BOUGHT" if direction == "LONG" else "SHORTED"
        action_emoji = "🟢" if direction == "LONG" else "🔴"
        lines = [
            f"🇮🇳 {action_emoji} *[SATAVECTOR INDIA] WHY this trade*",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"*{dir_word} {symbol}* | Grade: {grade} | Score: {score:.0f}/100",
            f"Entry: ₹{entry:.2f} | SL: ₹{sl:.2f} | Target: ₹{tp:.2f}",
            f"R:R {rr:.1f}:1 | Sector: NSE Equity",
            f"{'─' * 30}",
            "*Reasons:*",
        ] + [f"  {i}. {r}" for i, r in enumerate(reasons, 1)]

        return "\n".join(lines)

    except Exception as e:
        logger.debug(f"explain_trade: {e}")
        return f"Trade: {getattr(signal, 'symbol', '?')}"


def _save_decision(symbol, direction, entry, sl, target, reasons, score, grade):
    try:
        decisions = _load_decisions()
        decisions[symbol] = {
            "symbol":    symbol,
            "direction": direction,
            "entry":     entry,
            "sl":        sl,
            "target":    target,
            "reasons":   reasons,
            "score":     score,
            "grade":     grade,
            "time":      datetime.now(IST).isoformat(),
            "pnl":       None,
        }
        DECISIONS_FILE.write_text(json.dumps(decisions, indent=2))
    except Exception as e:
        logger.debug(f"save_decision: {e}")


def record_trade_close(symbol: str, pnl_inr: float):
    """Record actual P&L in INR when trade closes."""
    try:
        decisions = _load_decisions()
        if symbol in decisions:
            decisions[symbol]["pnl"] = round(pnl_inr, 2)
            decisions[symbol]["close_time"] = datetime.now(IST).isoformat()
            DECISIONS_FILE.write_text(json.dumps(decisions, indent=2))
        # Append to monthly totals
        _add_to_monthly(pnl_inr)
    except Exception as e:
        logger.debug(f"record_trade_close: {e}")


def _load_decisions() -> Dict:
    try:
        if DECISIONS_FILE.exists():
            return json.loads(DECISIONS_FILE.read_text())
    except Exception:
        pass
    return {}


def _add_to_monthly(pnl_inr: float):
    try:
        month_key = datetime.now(IST).strftime("%Y-%m")
        data = {}
        if MONTHLY_FILE.exists():
            data = json.loads(MONTHLY_FILE.read_text())
        data[month_key] = round(data.get(month_key, 0) + pnl_inr, 2)
        MONTHLY_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def _get_monthly_pnl() -> float:
    try:
        month_key = datetime.now(IST).strftime("%Y-%m")
        if MONTHLY_FILE.exists():
            data = json.loads(MONTHLY_FILE.read_text())
            return float(data.get(month_key, 0))
    except Exception:
        pass
    return 0.0


def _target_bar(pct: float, width: int = 18) -> str:
    filled = max(0, min(width, int(pct * width)))
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {pct:.0%}"


# ── Market data helpers ───────────────────────────────────────────────────────

def _get_nifty_info() -> dict:
    try:
        from data_fetch_upstox import get_nifty_level as _nifty
        info = _nifty()
        if info.get("level", 0) > 0:
            return {"level": info["level"], "chg_pct": info["change_pct"]}
    except Exception:
        pass
    return {"level": 0, "chg_pct": 0}


def _get_india_vix() -> float:
    try:
        from data_fetch_upstox import get_india_vix as _vix
        return _vix()
    except Exception:
        pass
    return 0.0


def _vix_regime(vix: float) -> str:
    if vix <= 0:
        return "unknown"
    if vix < 12:
        return "ULTRA-LOW (caution — complacency)"
    if vix < 18:
        return "NORMAL ✅"
    if vix < 24:
        return "ELEVATED ⚠️"
    if vix < 30:
        return "HIGH 🔴"
    return "CRISIS 🚨"


def _get_today_decisions() -> List[Dict]:
    try:
        decisions = _load_decisions()
        today = datetime.now(IST).strftime("%Y-%m-%d")
        return [d for d in decisions.values()
                if d.get("time", "").startswith(today)]
    except Exception:
        pass
    return []


# ── Morning brief 2.0 ────────────────────────────────────────────────────────

def build_morning_brief_v2() -> str:
    """Build comprehensive pre-market intelligence message. Returns plain text string."""
    now = datetime.now(IST)
    lines = [
        f"🌅 SATAVECTOR MORNING INTELLIGENCE — {now.strftime('%H:%M IST')}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 {now.strftime('%A, %d %b %Y')} | NSE India",
        "",
    ]

    bias_score = 0

    # ── Global overnight ──────────────────────────────────────────────────────
    try:
        import global_cues_india
        cues = global_cues_india.fetch()
        lines.append("🌍 GLOBAL OVERNIGHT:")
        lines.append(f"  • SGX Nifty: {cues['sgx_nifty_chg']:+.2f}%")
        lines.append(f"  • Dow Jones: {cues['dow_chg']:+.2f}% | S&P 500: {cues['sp500_chg']:+.2f}%")
        lines.append(f"  • USD/INR: {cues['usdinr_chg']:+.2f}%")
        lines.append(f"  • Global Bias: {cues['bias']}")
        bias_score += cues.get("bias_score", 0) // 2  # half weight
        lines.append("")
    except Exception:
        pass

    # ── India VIX ─────────────────────────────────────────────────────────────
    vix = _get_india_vix()
    if vix > 0:
        lines.append("📊 INDIA VIX:")
        lines.append(f"  • VIX: {vix:.1f} — {_vix_regime(vix)}")
        if vix < 18:
            bias_score += 1
        elif vix > 24:
            bias_score -= 2
        lines.append("")

    # ── FII/DII ───────────────────────────────────────────────────────────────
    try:
        from fii_dii_india import get_fii_dii_score
        fii_result = get_fii_dii_score()
        if fii_result:
            lines.append("💰 FII/DII:")
            lines.append(f"  • {fii_result.get('summary', 'N/A')}")
            fii_adj = fii_result.get("score_delta", 0)
            if fii_adj > 0:
                bias_score += 2
            elif fii_adj < 0:
                bias_score -= 2
            lines.append("")
    except Exception:
        pass

    # ── Corporate events ──────────────────────────────────────────────────────
    try:
        from corporate_events_india import get_today_events
        events = get_today_events()
        if events:
            lines.append("📅 TODAY'S KEY EVENTS:")
            for ev in events[:3]:
                lines.append(f"  • {ev}")
            lines.append("")
    except Exception:
        pass

    # ── Nifty key levels ──────────────────────────────────────────────────────
    try:
        nifty = _get_nifty_info()
        if nifty["level"] > 0:
            n = nifty["level"]
            chg = nifty["chg_pct"]
            lines.append("📍 NIFTY 50:")
            lines.append(f"  • Level: {n:,.0f} ({chg:+.2f}%)")
            if chg > 0.5:
                bias_score += 1
            elif chg < -0.5:
                bias_score -= 1
            lines.append("")
    except Exception:
        pass

    # ── Recommended bias ─────────────────────────────────────────────────────
    if bias_score >= 3:
        bias_label = "🟢 BULLISH"
        bias_detail = "Global + domestic momentum aligned"
    elif bias_score <= -3:
        bias_label = "🔴 BEARISH"
        bias_detail = "Global weakness + selling pressure"
    else:
        bias_label = "⚪ NEUTRAL"
        bias_detail = "Mixed signals — be selective"

    lines.append(f"🎯 TODAY'S RECOMMENDED BIAS: {bias_label}")
    lines.append(f"   {bias_detail}")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("Scanning 80 NSE symbols | 7 gates | 17 signal sources")

    # Store day bias for main bot to read
    try:
        DAY_BIAS["bias"]  = bias_label.split()[-1]
        DAY_BIAS["score"] = bias_score
    except Exception:
        pass

    return "\n".join(lines)


DAY_BIAS: dict = {"bias": "NEUTRAL", "score": 0}


def send_morning_brief_v2():
    """Send the enhanced morning briefing to Telegram."""
    if getattr(cfg, "MORNING_INTEL_ENABLED", True):
        try:
            msg = build_morning_brief_v2()
            _send(msg)
            logger.info("Morning brief 2.0 sent")
            return
        except Exception as e:
            logger.debug(f"send_morning_brief_v2: {e}")
    send_morning_brief()


# ── Bloomberg-style HTML formatters ──────────────────────────────────────────

def _bloomberg_morning_html() -> str:
    """Build a Bloomberg BN-style HTML morning brief for Telegram."""
    now_ist = datetime.now(IST)
    date_str = now_ist.strftime("%A, %d %b %Y")

    nifty  = _get_nifty_info()
    vix    = _get_india_vix()

    # Nifty line
    nifty_arrow = "▲" if nifty["chg_pct"] >= 0 else "▼"
    nifty_icon  = "🟢" if nifty["chg_pct"] >= 0 else "🔴"
    nifty_line  = (f"{nifty_icon} <b>NIFTY</b>  <code>{nifty['level']:>8,.0f}</code>  "
                   f"{nifty_arrow} <b>{nifty['chg_pct']:+.2f}%</b>"
                   if nifty["level"] else "NIFTY: unavailable")

    # VIX
    vix_regime = "🟢 LOW" if vix < 15 else ("🔴 HIGH" if vix > 22 else "🟡 NORMAL")
    vix_line   = f"<b>India VIX</b>  <code>{vix:>5.1f}</code>  {vix_regime}" if vix else "VIX: unavailable"

    # FII/DII from cached file
    fii_line = ""
    try:
        fii_cache = DATA_DIR / "fii_dii_cache.json"
        if fii_cache.exists():
            fii_data = json.loads(fii_cache.read_text())
            fii_net  = float(fii_data.get("fii_net", 0))
            dii_net  = float(fii_data.get("dii_net", 0))
            net_icon = "🟢" if fii_net + dii_net > 0 else "🔴"
            fii_line = (f"{net_icon} <b>FII/DII</b>  "
                        f"FII {fii_net/1e7:+.0f}Cr  │  DII {dii_net/1e7:+.0f}Cr")
    except Exception:
        pass

    # Global cues from cache
    global_line = ""
    try:
        gc_cache = DATA_DIR / "global_cues_cache.json"
        if gc_cache.exists():
            gc = json.loads(gc_cache.read_text())
            sgx_chg = float(gc.get("sgx_nifty_chg_pct", 0))
            dow_chg = float(gc.get("dow_futures_chg_pct", 0))
            usdinr  = float(gc.get("usdinr", 0))
            global_line = (f"🌍 <b>GLOBAL</b>  SGX {sgx_chg:+.2f}%  "
                           f"Dow {dow_chg:+.2f}%"
                           + (f"  USD/INR {usdinr:.2f}" if usdinr else ""))
    except Exception:
        pass

    # Regime
    regime_str = "TRENDING" if vix and vix < 20 else ("HIGH_VIX" if vix and vix > 22 else "NORMAL")
    strategy_tip = {
        "TRENDING": "✅ Momentum &amp; ORB preferred  |  avoid mean-reversion fades",
        "HIGH_VIX":  "⚠️  Reduce size  |  tighter SL  |  prefer SHORT setups",
        "NORMAL":    "✅ All setups valid  |  standard parameters",
    }.get(regime_str, "")

    # Events
    event_line = ""
    try:
        from news_filter_india import next_blackout_event
        event = next_blackout_event()
        if event:
            event_line = f"⚠️ <b>EVENT</b>  {event}"
    except Exception:
        pass

    # Monthly progress
    monthly    = _get_monthly_pnl()
    monthly_pct = min(monthly / MONTHLY_TARGET_INR, 1.0) if MONTHLY_TARGET_INR else 0
    bar_w = 16
    filled = max(0, min(bar_w, int(monthly_pct * bar_w)))
    bar = "█" * filled + "░" * (bar_w - filled)
    pct_display = f"{monthly_pct:.0%}"

    sections = [
        f"📋 <b>MORNING BRIEF</b>  {date_str}  {now_ist.strftime('%H:%M')} IST",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        nifty_line,
        vix_line,
    ]
    if global_line: sections.append(global_line)
    if fii_line:    sections.append(fii_line)
    if event_line:  sections.append(event_line)
    sections += [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📐 <b>REGIME</b>  {regime_str}",
        strategy_tip,
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📈 <b>MONTHLY</b>  ₹{monthly:+,.0f} / ₹{MONTHLY_TARGET_INR:,.0f}",
        f"<code>[{bar}]</code> {pct_display}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"⚡ <b>Bot armed</b>  │  Market: 09:15 IST  │  Min score: {getattr(cfg, 'FINAL_EXEC_MIN_SCORE', 65)}",
    ]
    return "\n".join(s for s in sections if s)


def _bloomberg_eod_html(decisions: list, day_pnl: float, wins: int, losses: int,
                         monthly: float) -> str:
    """Build a Bloomberg PORT-style HTML EOD report for Telegram."""
    now_ist = datetime.now(IST)
    date_str = now_ist.strftime("%d %b %Y")
    total = wins + losses
    wr    = wins / total if total else 0

    pnl_icon  = "✅" if day_pnl > 0 else ("❌" if day_pnl < 0 else "⏸")
    wr_icon   = "🟢" if wr >= 0.60 else ("🟡" if wr >= 0.50 else "🔴")
    monthly_pct = min(monthly / MONTHLY_TARGET_INR, 1.0) if MONTHLY_TARGET_INR else 0
    bar_w = 16
    filled = max(0, min(bar_w, int(monthly_pct * bar_w)))
    bar    = "█" * filled + "░" * (bar_w - filled)

    # Trade ledger
    ledger_lines = []
    best_pnl = worst_pnl = 0.0
    best_sym = worst_sym = ""
    for i, d in enumerate(decisions, 1):
        pnl_d = d.get("pnl")
        if pnl_d is None:
            continue
        pnl_d = float(pnl_d)
        sl_dist = abs(d.get("entry", 0) - d.get("sl", d.get("entry", 1))) or 1
        r_mult  = pnl_d / max(sl_dist * d.get("qty", 1), 1)
        row_icon = "🟢" if pnl_d > 0 else "🔴"
        ledger_lines.append(
            f"  {row_icon} {i}. <b>{d['symbol']}</b> {d['direction']}  "
            f"₹{pnl_d:+,.0f}  (<code>{r_mult:+.1f}R</code>)  {d.get('grade','B')}"
        )
        if pnl_d > best_pnl:
            best_pnl, best_sym = pnl_d, d["symbol"]
        if pnl_d < worst_pnl:
            worst_pnl, worst_sym = pnl_d, d["symbol"]

    # ASCII sparkline from decisions timeline
    pnls_seq = [float(d.get("pnl") or 0) for d in decisions if d.get("pnl") is not None]
    if pnls_seq:
        running = 0.0
        equity_pts = []
        for p in pnls_seq:
            running += p
            equity_pts.append(running)
        mn, mx = min(equity_pts), max(equity_pts)
        rng = mx - mn or 1
        spark_chars = "▁▂▃▄▅▆▇█"
        spark = "".join(spark_chars[int((v - mn) / rng * 7)] for v in equity_pts)
        spark_line = f"<code>{spark}</code>"
    else:
        spark_line = ""

    sections = [
        f"📊 <b>EOD REPORT</b>  {date_str}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"{pnl_icon} <b>Day P&amp;L</b>   ₹{day_pnl:+,.0f}",
        f"{wr_icon} <b>Win Rate</b>   {wr:.0%}  ({wins}W / {losses}L)",
    ]
    if best_sym:
        sections.append(f"🏆 <b>Best</b>     {best_sym}  ₹{best_pnl:+,.0f}")
    if worst_sym and worst_sym != best_sym:
        sections.append(f"💀 <b>Worst</b>    {worst_sym}  ₹{worst_pnl:+,.0f}")
    if spark_line:
        sections += ["━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                     f"📈 Equity curve: {spark_line}"]
    if ledger_lines:
        sections += ["━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                     "<b>TRADE LEDGER</b>"] + ledger_lines
    sections += [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 <b>Monthly</b>  ₹{monthly:+,.0f} / ₹{MONTHLY_TARGET_INR:,.0f}",
        f"<code>[{bar}]</code>  {monthly_pct:.0%}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(sections)


# ── Morning brief (legacy) ────────────────────────────────────────────────────

def send_morning_brief():
    # Try Bloomberg-style HTML first, fall back to legacy plain text
    try:
        html = _bloomberg_morning_html()
        _send(html, parse_mode="HTML")
        logger.info("Morning brief (Bloomberg-style) sent")
        return
    except Exception as e:
        logger.debug(f"bloomberg morning brief failed, falling back: {e}")

    nifty   = _get_nifty_info()
    vix     = _get_india_vix()
    monthly = _get_monthly_pnl()

    nifty_emoji = "🟢" if nifty["chg_pct"] >= 0 else "🔴"
    nifty_str   = (f"{nifty_emoji} Nifty: `{nifty['level']:,.0f}` "
                   f"({nifty['chg_pct']:+.1f}%)" if nifty["level"] else "Nifty: unavailable")

    vix_str = f"India VIX: `{vix:.1f}` — {_vix_regime(vix)}" if vix else "India VIX: unavailable"

    monthly_pct  = monthly / MONTHLY_TARGET_INR if MONTHLY_TARGET_INR else 0
    monthly_days = datetime.now(IST).day
    expected_pct = monthly_days / 22   # ~22 trading days/month
    on_track     = "✅ On track" if monthly_pct >= expected_pct * 0.80 else "⚠️ Behind pace"

    # Upcoming events
    try:
        from news_filter_india import next_blackout_event
        event = next_blackout_event()
        event_str = f"⚠️ *Event*: {event}" if event else ""
    except Exception:
        event_str = ""

    # FII/DII flow
    fii_str = ""
    try:
        from fii_dii_tracker import FIIDIITracker
        tracker = FIIDIITracker()
        flow    = tracker.get_today_flow()
        if flow:
            fii_emoji = "🟢" if flow.combined_net > 0 else "🔴"
            fii_str = (f"{fii_emoji} FII: ₹{flow.fii_net/1e7:.0f}Cr | "
                       f"DII: ₹{flow.dii_net/1e7:.0f}Cr | "
                       f"Trend: {tracker.get_flow_bias()}")
    except Exception:
        pass

    # Nifty option chain
    oc_str = ""
    try:
        from option_chain import OptionChainAnalyzer
        oc  = OptionChainAnalyzer()
        res = oc.analyze("NIFTY")
        if res:
            oc_str = (f"📊 PCR: `{res.pcr:.2f}` | Max Pain: `{res.max_pain:,.0f}` | "
                      f"Bias: `{res.direction_bias}`")
    except Exception:
        pass

    filled = max(0, min(18, int(monthly_pct * 18)))
    bar = "█" * filled + "░" * (18 - filled)
    lines = [
        f"🇮🇳 🌅 *SATAVECTOR INDIA — Morning Brief*",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 {datetime.now(IST).strftime('%A, %d %b %Y')} | NSE | Upstox",
        f"{'─' * 30}",
        nifty_str,
        vix_str,
    ]
    if oc_str:    lines.append(oc_str)
    if fii_str:   lines.append(fii_str)
    if event_str: lines.append(event_str)
    lines += [
        f"{'─' * 30}",
        f"*Monthly P&L:* ₹{monthly:+,.0f} / ₹{MONTHLY_TARGET_INR:,.0f}",
        f"`[{bar}]` {monthly_pct:.0%}",
        on_track,
        f"{'─' * 30}",
        f"Market opens: 9:15 AM IST",
        f"Scanning 50 NSE symbols | 26 gates | 12 signal sources | ORB active",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    msg = "\n".join(lines)
    _send(msg)
    logger.info("Morning brief sent")


# ── EOD report ────────────────────────────────────────────────────────────────

def send_eod_report():
    decisions = _get_today_decisions()
    monthly   = _get_monthly_pnl()
    monthly_pct = monthly / MONTHLY_TARGET_INR if MONTHLY_TARGET_INR else 0

    day_pnl  = sum(d.get("pnl") or 0 for d in decisions)
    wins_cnt = sum(1 for d in decisions if (d.get("pnl") or 0) > 0)
    loss_cnt = sum(1 for d in decisions if (d.get("pnl") or 0) <= 0 and d.get("pnl") is not None)

    # Try Bloomberg-style HTML first
    try:
        html = _bloomberg_eod_html(decisions, day_pnl, wins_cnt, loss_cnt, monthly)
        _send(html, parse_mode="HTML")
        logger.info(f"EOD report (Bloomberg-style) sent: {wins_cnt+loss_cnt} trades, ₹{day_pnl:+.0f}")
        return
    except Exception as e:
        logger.debug(f"bloomberg eod failed, falling back: {e}")

    day_pnl   = sum(d.get("pnl") or 0 for d in decisions)
    wins      = sum(1 for d in decisions if (d.get("pnl") or 0) > 0)
    losses    = sum(1 for d in decisions if (d.get("pnl") or 0) <= 0 and d.get("pnl") is not None)
    total     = wins + losses
    win_rate  = wins / total if total else 0

    filled = max(0, min(18, int(monthly_pct * 18)))
    bar = "█" * filled + "░" * (18 - filled)
    pnl_emoji = "🟢" if day_pnl > 0 else ("🔴" if total > 0 else "⏸")
    wr_filled = max(0, min(10, int(win_rate * 10)))
    wr_bar = "█" * wr_filled + "░" * (10 - wr_filled)

    lines = [
        f"🇮🇳 📊 *SATAVECTOR INDIA — EOD Report*",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 {datetime.now(IST).strftime('%d %b %Y')} | NSE | Upstox",
        f"{'─' * 30}",
        f"{pnl_emoji} Day P&L: `₹{day_pnl:+,.0f}`",
        f"Trades: {total}  Wins: {wins}  Losses: {losses}",
        f"Win rate: `{win_rate:.0%}` [{wr_bar}]" if total else "No trades — filters protected capital",
        f"{'─' * 30}",
        f"*Monthly:* ₹{monthly:+,.0f} / ₹{MONTHLY_TARGET_INR:,.0f}",
        f"`[{bar}]` {monthly_pct:.0%}",
        f"{'─' * 30}",
    ]

    # Trade details
    if decisions:
        lines.append("*Today's trades:*")
        for d in decisions:
            pnl = d.get("pnl")
            pnl_str = f"₹{pnl:+,.0f}" if pnl is not None else "open"
            emoji   = "🟢" if (pnl or 0) > 0 else ("🔴" if pnl is not None else "⏳")
            lines.append(f"  {emoji} {d['symbol']} {d['direction']} → `{pnl_str}`")
            reasons = d.get("reasons", [])
            if reasons:
                lines.append(f"     _Why: {reasons[0]}_")
    else:
        lines.append("_No trades today — 26 gates held. Capital preserved._")

    if day_pnl > 0:
        lines.append(f"\n✅ Profitable session! +₹{day_pnl:,.0f}")
    elif total > 0:
        lines.append(f"\n🔴 Loss day — watchdog reviewing parameters")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    _send("\n".join(lines))
    logger.info(f"EOD report sent: {total} trades, ₹{day_pnl:+.0f}")


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "morning"
    if cmd == "morning":
        send_morning_brief()
    elif cmd == "eod":
        send_eod_report()
    else:
        print(f"Unknown command: {cmd}. Use: morning | eod")
