"""
auto_improver.py — KingTrades Weekly AI Self-Improvement Agent

Runs every Sunday at 9 PM ET (via crontab — no Claude Code session needed).
Uses the Claude API directly (Haiku model ~$0.003/week = $0.16/year).

What it does:
  1. Reads this week's performance: trades, win rate, P&L, errors
  2. Sends a compact summary to Claude Haiku API
  3. Claude analyzes and returns specific safe improvements
  4. Agent applies ONLY safe config changes (no code changes)
  5. Sends Telegram with what was done and what needs your attention

What it will NEVER auto-apply:
  - Code changes (Python files)
  - Risk limits (MAX_RISK_PER_TRADE, DAILY_LOSS_LIMIT, MAX_POSITIONS)
  - Broker credentials
  - Anything requiring pip install

Cost: ~$0.003 per run (10k tokens Haiku input + 500 output)
      = $0.16/year total AI cost after this

Crontab setup (runs once weekly):
  0 21 * * 0 cd /root/kingtrades && python3 auto_improver.py >> logs/auto_improver.log 2>&1
"""

import json
import logging
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).parent
LOG_DIR  = BASE_DIR / "logs"
ET = ZoneInfo("America/New_York")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AUTO-IMPROVER] %(message)s",
)
logger = logging.getLogger("auto_improver")

# ── Safe parameters the AI is allowed to tune ────────────────────────────────
# Each entry: (env_key, current_default, min_safe, max_safe, description)
TUNABLE_PARAMS = [
    ("FINAL_EXEC_MIN_SCORE",    70.0, 58.0, 80.0,  "Post-booster signal score gate"),
    ("MIN_SIGNAL_SCORE",        60.0, 55.0, 72.0,  "Pre-filter score threshold"),
    ("ADX_MIN_TREND",           12.0,  8.0, 22.0,  "Minimum trend strength (ADX)"),
    ("ATR_SL_MULTIPLIER",        0.75, 0.5,  1.5,  "Stop-loss width (ATR multiple)"),
    ("ATR_TP_MULTIPLIER",        4.0,  2.0,  6.0,  "Take-profit target (ATR multiple)"),
    ("BREAKEVEN_TRIGGER_PCT",    0.10, 0.05, 0.25, "Move to breakeven after this gain %"),
    ("GRAND_SLAM_MIN_SCORE",    82.0, 75.0, 92.0,  "Score threshold for max-size trades"),
]


# ── Data Collection ───────────────────────────────────────────────────────────

def _read_env_value(key: str) -> Optional[str]:
    """Read a value from .env file."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def _get_current_params() -> Dict[str, float]:
    """Return current values of all tunable parameters."""
    result = {}
    for key, default, _, _, _ in TUNABLE_PARAMS:
        val = _read_env_value(key) or os.getenv(key)
        try:
            result[key] = float(val) if val else default
        except Exception:
            result[key] = default
    return result


def _get_performance_summary() -> Dict:
    """
    Read this week's trading performance from logs and trade journal DB.
    Returns compact dict for Claude to analyze.
    """
    summary = {
        "period_days": 7,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "total_pnl_usd": 0.0,
        "avg_win_usd": 0.0,
        "avg_loss_usd": 0.0,
        "rr_ratio": None,
        "signals_scanned": 0,
        "signals_approved": 0,
        "approval_rate": None,
        "top_errors": [],
        "disabled_modules": [],
        "watchdog_fixes": 0,
        "current_params": _get_current_params(),
    }

    # Trade journal DB
    db_candidates = [
        BASE_DIR / "data" / "trade_journal.db",
        BASE_DIR / "logs" / "trades" / "journal.db",
    ]
    for db_path in db_candidates:
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                cur = conn.cursor()
                since = (datetime.now() - timedelta(days=7)).isoformat()
                # Try common schema patterns
                for table in ("trades", "trade_journal", "journal"):
                    try:
                        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE timestamp > ?", (since,))
                        total = cur.fetchone()[0]
                        if total:
                            cur.execute(f"SELECT pnl FROM {table} WHERE timestamp > ?", (since,))
                            pnls = [row[0] for row in cur.fetchall() if row[0] is not None]
                            wins  = [p for p in pnls if p > 0]
                            losses= [p for p in pnls if p <= 0]
                            summary["total_trades"]  = len(pnls)
                            summary["wins"]          = len(wins)
                            summary["losses"]        = len(losses)
                            summary["win_rate"]      = round(len(wins)/len(pnls), 3) if pnls else None
                            summary["total_pnl_usd"] = round(sum(pnls), 2)
                            summary["avg_win_usd"]   = round(sum(wins)/len(wins), 2) if wins else 0
                            summary["avg_loss_usd"]  = round(sum(losses)/len(losses), 2) if losses else 0
                            if wins and losses:
                                summary["rr_ratio"] = round(
                                    abs(summary["avg_win_usd"] / summary["avg_loss_usd"]), 2
                                )
                        break
                    except Exception:
                        continue
                conn.close()
                break
            except Exception:
                pass

    # Log scan for signals and errors
    log_file = LOG_DIR / "bot_output.log"
    if not log_file.exists():
        today = datetime.now(ET).strftime("%Y-%m-%d")
        log_file = LOG_DIR / f"trading_{today}.log"

    if log_file.exists():
        try:
            result = subprocess.run(
                ["tail", "-n", "5000", str(log_file)],
                capture_output=True, text=True
            )
            lines = result.stdout.splitlines()
            summary["signals_scanned"]  = sum(1 for l in lines if "scanning" in l.lower() or "SCAN" in l)
            summary["signals_approved"] = sum(1 for l in lines if "SIGNAL APPROVED" in l or "ORDER PLACED" in l)
            if summary["signals_scanned"] > 0:
                summary["approval_rate"] = round(
                    summary["signals_approved"] / summary["signals_scanned"], 4
                )

            # Top errors
            error_counts: Dict[str, int] = {}
            for line in lines:
                if "ERROR" in line or "CRITICAL" in line:
                    # Truncate to error type
                    m = re.search(r"(Error|Exception|failed|blocked|rejected)[:\s]+([^\n]{0,60})", line, re.I)
                    if m:
                        key = m.group(0)[:80]
                        error_counts[key] = error_counts.get(key, 0) + 1
            summary["top_errors"] = sorted(error_counts.items(), key=lambda x: -x[1])[:5]
        except Exception:
            pass

    # Watchdog state
    state_file = BASE_DIR / "data" / "watchdog_state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
            fixes = state.get("auto_fixes_this_week", [])
            summary["watchdog_fixes"]    = len(fixes)
            summary["disabled_modules"]  = state.get("disabled_modules", [])
        except Exception:
            pass

    return summary


# ── Claude API Call ───────────────────────────────────────────────────────────

def _call_claude_api(performance: Dict, params: Dict) -> Optional[str]:
    """
    Send performance data to Claude Haiku for analysis.
    Returns suggested parameter changes as JSON string, or None on failure.
    Cost: ~$0.003 per call.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY") or _read_env_value("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping AI analysis")
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        # Compact prompt — minimize tokens, maximize signal
        prompt = f"""You are a quant analyst reviewing a week of live US equities trading bot performance.
Suggest parameter adjustments to improve results. ONLY return a JSON object.

PERFORMANCE (last 7 days):
{json.dumps(performance, indent=2)}

TUNABLE PARAMETERS (key, current_value, min_allowed, max_allowed):
{json.dumps([(k, params.get(k, d), mn, mx) for k, d, mn, mx, _ in TUNABLE_PARAMS], indent=2)}

Rules:
- Only suggest changes for parameters in the list above
- Stay within [min_allowed, max_allowed] bounds strictly
- If win_rate is None or total_trades < 5, suggest NO changes (insufficient data)
- Maximum 3 parameter changes per run
- Each change must improve either: win_rate, avg_win/avg_loss ratio, or signal approval rate

Return ONLY valid JSON in this exact format (no extra text):
{{
  "changes": [
    {{"param": "PARAM_NAME", "new_value": 0.0, "reason": "one sentence"}}
  ],
  "analysis": "2-3 sentence summary of what the data shows",
  "needs_human": "describe any issue requiring human action, or empty string"
}}"""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    except ImportError:
        logger.warning("anthropic package not installed: pip3 install anthropic")
        return None
    except Exception as e:
        logger.error(f"Claude API call failed: {e}")
        return None


# ── Apply Changes ─────────────────────────────────────────────────────────────

def _apply_changes(changes: List[Dict]) -> List[str]:
    """
    Apply safe parameter changes to .env file.
    Returns list of human-readable change descriptions.
    """
    applied = []
    env_path = BASE_DIR / ".env"
    param_bounds = {k: (mn, mx) for k, _, mn, mx, _ in TUNABLE_PARAMS}

    for change in changes:
        param  = change.get("param", "")
        new_val = change.get("new_value")
        reason = change.get("reason", "")

        if param not in param_bounds:
            logger.warning(f"Skipping unsafe param: {param}")
            continue

        mn, mx = param_bounds[param]
        try:
            new_val = float(new_val)
        except (TypeError, ValueError):
            continue

        if not (mn <= new_val <= mx):
            logger.warning(f"Skipping out-of-bounds: {param}={new_val} (bounds {mn}-{mx})")
            continue

        # Write to .env
        try:
            if env_path.exists():
                content = env_path.read_text()
                if param in content:
                    content = re.sub(
                        rf"{param}\s*=\s*[\d.]+",
                        f"{param}={new_val}",
                        content
                    )
                else:
                    content += f"\n{param}={new_val}\n"
                env_path.write_text(content)
            else:
                with open(env_path, "a") as f:
                    f.write(f"\n{param}={new_val}\n")

            applied.append(f"{param}: → {new_val} ({reason})")
            logger.info(f"Applied: {param}={new_val} — {reason}")
        except Exception as e:
            logger.error(f"Failed to write {param}: {e}")

    return applied


# ── Telegram ──────────────────────────────────────────────────────────────────

def _telegram_send(msg: str):
    try:
        try:
            import dotenv
            dotenv.load_dotenv(BASE_DIR / ".env")
        except Exception:
            pass
        token = os.getenv("TELEGRAM_BOT_TOKEN", "") or _read_env_value("TELEGRAM_BOT_TOKEN") or ""
        chat  = os.getenv("TELEGRAM_CHAT_ID",   "") or _read_env_value("TELEGRAM_CHAT_ID")   or ""
        if not token or not chat:
            logger.info(f"[TELEGRAM] {msg[:100]}")
            return
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": f"🤖 *Auto-Improver*\n{msg}", "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        logger.debug(f"telegram: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    logger.info("=" * 60)
    logger.info("KingTrades Auto-Improver starting")
    logger.info(f"Date: {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}")

    # 1. Collect performance data
    logger.info("Collecting performance data...")
    perf = _get_performance_summary()
    params = _get_current_params()

    logger.info(
        f"Trades: {perf['total_trades']} | "
        f"Win rate: {perf['win_rate']} | "
        f"P&L: ${perf['total_pnl_usd']:.2f}"
    )

    # 2. Call Claude API
    logger.info("Calling Claude Haiku API for analysis...")
    response_text = _call_claude_api(perf, params)

    changes_applied = []
    analysis_text   = ""
    needs_human     = ""

    if response_text:
        try:
            # Extract JSON from response (Claude sometimes adds explanation text)
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                changes_applied = _apply_changes(data.get("changes", []))
                analysis_text   = data.get("analysis", "")
                needs_human     = data.get("needs_human", "")
        except Exception as e:
            logger.error(f"Parse response: {e}\nRaw: {response_text[:200]}")

    # 3. Build Telegram report
    lines = ["*Weekly AI Analysis Complete*", ""]

    if perf["total_trades"] > 0:
        lines += [
            f"Trades: `{perf['total_trades']}` | "
            f"Win rate: `{perf['win_rate']:.0%}`" if perf['win_rate'] else
            f"Trades: `{perf['total_trades']}`",
            f"P&L: `${perf['total_pnl_usd']:+.2f}` | "
            f"R:R: `{perf['rr_ratio']}`" if perf['rr_ratio'] else "",
        ]
    else:
        lines.append("No trades this week (bot may still be in warm-up)")

    if analysis_text:
        lines += ["", f"*AI says:* {analysis_text}"]

    if changes_applied:
        lines += ["", "*Auto-applied changes:*"]
        for c in changes_applied:
            lines.append(f"  • {c}")
        lines.append("_Bot will use new values on next scan — no restart needed_")
    else:
        lines.append("\n_No parameter changes needed this week_")

    if perf["disabled_modules"]:
        lines += ["", f"*Disabled modules:* {', '.join(perf['disabled_modules'])}"]
        lines.append("Re-enable: remove the =False lines from .env")

    if needs_human:
        lines += ["", f"⚠️ *Needs your attention:*", needs_human]

    _telegram_send("\n".join(l for l in lines if l is not None))

    # 4. Log cost estimate
    logger.info(f"Analysis complete. Applied {len(changes_applied)} changes.")
    logger.info("Estimated API cost this run: ~$0.003")
    logger.info("=" * 60)


if __name__ == "__main__":
    run()
