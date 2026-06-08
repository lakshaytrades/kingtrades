"""
auto_improver_india.py — Weekly AI Self-Improvement for India Bot
India specialist: tunes INDIA_* params in .env using Claude Haiku.
Runs Sunday 9 PM IST (not ET like the US bot).

Cost: ~$0.003/run = ₹0.25/run = ₹13/year

Crontab (on VPS, IST = UTC+5:30):
  30 15 * * 0  cd /root/kingtrades/india && python3 auto_improver_india.py >> /root/kingtrades/logs/india_auto_improver.log 2>&1
  (15:30 UTC = 21:00 IST)
"""
import json
import logging
import os
import re
import sys
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

BASE_DIR = Path(__file__).parent.parent
LOG_DIR  = BASE_DIR / "logs"
IST      = ZoneInfo("Asia/Kolkata")


def _parse_ist(ts: str) -> datetime:
    """Parse ISO timestamp string to IST-aware datetime, handling naive and aware inputs."""
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=IST)   # assume IST if no tz info
        return dt.astimezone(IST)
    except Exception:
        return datetime(2000, 1, 1, tzinfo=IST)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [IST] [INDIA-IMPROVER] %(message)s",
)
# Server runs in UTC — render %(asctime)s in IST (IST defined above).
def _ist_log_converter(*args):
    return datetime.fromtimestamp(args[-1], IST).timetuple()
logging.Formatter.converter = staticmethod(_ist_log_converter)
logger = logging.getLogger("india_improver")

# ── India-specific tunable parameters ────────────────────────────────────────
# (env_key, default, min_safe, max_safe, description)
TUNABLE_PARAMS = [
    ("INDIA_FINAL_EXEC_MIN_SCORE", 70.0, 58.0, 80.0,  "Post-filter score gate (NSE)"),
    ("INDIA_MIN_SIGNAL_SCORE",     60.0, 55.0, 72.0,  "Pre-filter score threshold"),
    ("INDIA_ATR_SL_MULTIPLIER",     1.5,  1.0,  2.5,  "Stop-loss width (NSE is more volatile)"),
    ("INDIA_ATR_TP_MULTIPLIER",     3.0,  2.0,  5.0,  "Take-profit target"),
    ("INDIA_BREAKEVEN_TRIGGER_PCT", 0.10, 0.05, 0.25, "Breakeven trigger %"),
    ("INDIA_GRAND_SLAM_MIN_SCORE", 82.0, 75.0, 92.0,  "Grand Slam threshold"),
]


def _read_env_value(key: str) -> Optional[str]:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def _get_current_params() -> Dict[str, float]:
    result = {}
    for key, default, _, _, _ in TUNABLE_PARAMS:
        val = _read_env_value(key) or os.getenv(key)
        try:
            result[key] = float(val) if val else default
        except Exception:
            result[key] = default
    return result


def _get_performance_summary() -> Dict:
    summary = {
        "period_days": 7,
        "market": "NSE India (INR)",
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "total_pnl_inr": 0.0,
        "avg_win_inr": 0.0,
        "avg_loss_inr": 0.0,
        "rr_ratio": None,
        "signals_scanned": 0,
        "signals_approved": 0,
        "approval_rate": None,
        "top_errors": [],
        "disabled_modules": [],
        "watchdog_fixes": 0,
        "current_params": _get_current_params(),
    }

    # India decisions JSON
    decisions_file = BASE_DIR / "data" / "india_trade_decisions.json"
    if decisions_file.exists():
        try:
            decisions = json.loads(decisions_file.read_text())
            since = datetime.now(IST) - timedelta(days=7)
            week_trades = [
                d for d in decisions.values()
                if d.get("pnl") is not None and
                   _parse_ist(d.get("time", "2000-01-01")) >= since
            ]
            if week_trades:
                pnls  = [d["pnl"] for d in week_trades]
                wins  = [p for p in pnls if p > 0]
                losses= [p for p in pnls if p <= 0]
                summary["total_trades"]  = len(pnls)
                summary["wins"]          = len(wins)
                summary["losses"]        = len(losses)
                summary["win_rate"]      = round(len(wins)/len(pnls), 3) if pnls else None
                summary["total_pnl_inr"] = round(sum(pnls), 2)
                summary["avg_win_inr"]   = round(sum(wins)/len(wins), 2) if wins else 0
                summary["avg_loss_inr"]  = round(sum(losses)/len(losses), 2) if losses else 0
                if wins and losses:
                    summary["rr_ratio"] = round(
                        abs(summary["avg_win_inr"] / summary["avg_loss_inr"]), 2)
        except Exception as e:
            logger.debug(f"decisions read: {e}")

    # Log scan
    log_file = LOG_DIR / f"india_{datetime.now(IST).strftime('%Y-%m-%d')}.log"
    if not log_file.exists():
        log_file = LOG_DIR / "india_output.log"
    if log_file.exists():
        try:
            result = subprocess.run(["tail", "-n", "5000", str(log_file)],
                                    capture_output=True, text=True)
            lines = result.stdout.splitlines()
            summary["signals_scanned"]  = sum(1 for l in lines if "scanning" in l.lower())
            summary["signals_approved"] = sum(1 for l in lines if "SIGNAL:" in l)
            if summary["signals_scanned"] > 0:
                summary["approval_rate"] = round(
                    summary["signals_approved"] / summary["signals_scanned"], 4)
            error_counts: Dict[str, int] = {}
            for line in lines:
                if "ERROR" in line or "CRITICAL" in line:
                    m = re.search(r"(Error|Exception|failed|blocked)[:\s]+([^\n]{0,60})", line, re.I)
                    if m:
                        k = m.group(0)[:80]
                        error_counts[k] = error_counts.get(k, 0) + 1
            summary["top_errors"] = sorted(error_counts.items(), key=lambda x: -x[1])[:5]
        except Exception:
            pass

    # Watchdog state
    state_file = BASE_DIR / "data" / "india_watchdog_state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
            summary["watchdog_fixes"]   = len(state.get("auto_fixes_this_week", []))
            summary["disabled_modules"] = state.get("disabled_modules", [])
        except Exception:
            pass

    return summary


def _call_claude_api(performance: Dict, params: Dict) -> Optional[str]:
    api_key = os.getenv("ANTHROPIC_API_KEY") or _read_env_value("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping AI analysis")
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = f"""You are a quant analyst for a live NSE India intraday trading bot (Dhan broker, INR capital).
Analyze weekly performance and suggest INDIA_* parameter changes. ONLY return JSON.

PERFORMANCE (last 7 days, INR):
{json.dumps(performance, indent=2)}

TUNABLE PARAMETERS (key, current, min, max):
{json.dumps([(k, params.get(k, d), mn, mx) for k, d, mn, mx, _ in TUNABLE_PARAMS], indent=2)}

NSE-specific context:
- NSE is more volatile than US markets (India VIX typically 12–25)
- ATR_SL_MULTIPLIER should be wider for NSE (1.5–2.0x vs 0.75x for US)
- Opening range breakout and power hour (3:00–3:30 PM IST) are key NSE patterns
- If win_rate is None or total_trades < 5: suggest NO changes

Rules: max 3 changes, stay within bounds strictly.
Return ONLY valid JSON:
{{"changes": [{{"param": "PARAM_NAME", "new_value": 0.0, "reason": "one sentence"}}],
  "analysis": "2-3 sentence NSE-specific analysis",
  "needs_human": "issue requiring human action or empty string"}}"""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except ImportError:
        logger.warning("anthropic not installed: pip3 install --break-system-packages anthropic")
        return None
    except Exception as e:
        logger.error(f"Claude API failed: {e}")
        return None


def _apply_changes(changes: List[Dict]) -> List[str]:
    applied = []
    env_path = BASE_DIR / ".env"
    param_bounds = {k: (mn, mx) for k, _, mn, mx, _ in TUNABLE_PARAMS}

    for change in changes:
        param   = change.get("param", "")
        new_val = change.get("new_value")
        reason  = change.get("reason", "")

        if param not in param_bounds:
            logger.warning(f"Skipping unsafe param: {param}")
            continue
        mn, mx = param_bounds[param]
        try:
            new_val = float(new_val)
        except (TypeError, ValueError):
            continue
        if not (mn <= new_val <= mx):
            logger.warning(f"Out of bounds: {param}={new_val} ({mn}–{mx})")
            continue
        try:
            if env_path.exists():
                content = env_path.read_text()
                if param in content:
                    content = re.sub(rf"{param}\s*=\s*[\d.]+",
                                     f"{param}={new_val}", content)
                else:
                    content += f"\n{param}={new_val}\n"
                env_path.write_text(content)
            applied.append(f"{param}: → {new_val} ({reason})")
            logger.info(f"Applied: {param}={new_val} — {reason}")
        except Exception as e:
            logger.error(f"Failed to write {param}: {e}")
    return applied


def _telegram_send(msg: str):
    try:
        import config_india as cfg
        import requests
        token = cfg.TELEGRAM_BOT_TOKEN
        chat  = cfg.TELEGRAM_CHAT_ID
        if not token or not chat:
            logger.info(f"[TG] {msg[:100]}")
            return
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat,
                  "text": f"🤖 *India Auto-Improver*\n{msg}",
                  "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        logger.debug(f"telegram: {e}")


def run():
    logger.info("=" * 60)
    logger.info(f"India Auto-Improver starting — {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")

    perf   = _get_performance_summary()
    params = _get_current_params()
    logger.info(f"Trades: {perf['total_trades']} | Win rate: {perf['win_rate']} | P&L: ₹{perf['total_pnl_inr']:+.2f}")

    response_text    = _call_claude_api(perf, params)
    changes_applied  = []
    analysis_text    = ""
    needs_human      = ""

    if response_text:
        try:
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                changes_applied = _apply_changes(data.get("changes", []))
                analysis_text   = data.get("analysis", "")
                needs_human     = data.get("needs_human", "")
        except Exception as e:
            logger.error(f"Parse response: {e}")

    lines = ["*India Weekly AI Analysis*", ""]
    if perf["total_trades"] > 0:
        lines += [
            f"Trades: `{perf['total_trades']}` | "
            f"Win rate: `{perf['win_rate']:.0%}`" if perf["win_rate"] else
            f"Trades: `{perf['total_trades']}`",
            f"P&L: `₹{perf['total_pnl_inr']:+.2f}` | R:R: `{perf['rr_ratio']}`" if perf["rr_ratio"] else "",
        ]
    else:
        lines.append("No trades this week")

    if analysis_text:
        lines += ["", f"*AI says:* {analysis_text}"]

    if changes_applied:
        lines += ["", "*Auto-applied changes (INDIA params):*"]
        for c in changes_applied:
            lines.append(f"  • {c}")
    else:
        lines.append("\n_No parameter changes needed_")

    if needs_human:
        lines += ["", f"⚠️ *Needs your attention:*", needs_human]

    _telegram_send("\n".join(l for l in lines if l is not None))
    logger.info(f"Done. Applied {len(changes_applied)} changes. Cost: ~₹0.25")
    logger.info("=" * 60)


if __name__ == "__main__":
    run()
