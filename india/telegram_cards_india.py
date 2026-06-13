"""
telegram_cards_india.py — Bloomberg-style rich Telegram cards for NSE India bot
Upstox broker | NSE equity | IST timezone | HTML parse mode

All card functions return True on success, False on failure (fail-open).
Use _send_html(text) for low-level raw HTML sends (also used by bloomberg_india.py).
"""
import logging
import re
import sys
import time as _time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_BASE = Path(__file__).parent.parent

# -- Logging ------------------------------------------------------------------
logger = logging.getLogger("telegram_cards_india")


# ---------------------------------------------------------------------------
# Config access
# ---------------------------------------------------------------------------

def _get_tg_creds() -> tuple[str, str]:
    """Return (token, chat_id) from config_india, or ('', '') if unavailable."""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import config_india as _cfg
        return _cfg.TELEGRAM_BOT_TOKEN, _cfg.TELEGRAM_CHAT_ID
    except Exception:
        return "", ""


# ---------------------------------------------------------------------------
# Low-level send with exponential backoff
# ---------------------------------------------------------------------------

def _send_html(text: str) -> bool:
    """
    Send an HTML-formatted message to Telegram.
    Retries with delays: 2s, 4s, 8s (max 3 attempts).
    Fail-open — logs the message if Telegram is unavailable.
    Returns True on success, False on failure.
    """
    token, chat_id = _get_tg_creds()

    if not token or not chat_id:
        logger.info("[TG-CARD] %s", text[:200])
        return False

    try:
        import requests
    except ImportError:
        logger.warning("[TG-CARD] requests not installed; cannot send to Telegram")
        logger.info("[TG-CARD] %s", text[:500])
        return False

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    delays = [2, 4, 8]
    for attempt, delay in enumerate(delays, start=1):
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload,
                timeout=10,
            )
            if resp.status_code == 200:
                return True
            logger.warning(
                "[TG-CARD] HTTP %s on attempt %d: %s",
                resp.status_code, attempt, resp.text[:120],
            )
        except Exception as exc:
            logger.warning("[TG-CARD] attempt %d failed: %s", attempt, exc)

        if attempt < len(delays):
            _time.sleep(delay)

    # Final fail — log so nothing is silently dropped
    logger.error("[TG-CARD] All retries exhausted. Message:\n%s", text[:500])
    return False


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _now_ist() -> datetime:
    return datetime.now(IST)


def _ist_time_str(dt: datetime | None = None) -> str:
    """Return 'HH:MM IST' string."""
    if dt is None:
        dt = _now_ist()
    return dt.strftime("%H:%M IST")


def _ist_date_str(dt: datetime | None = None) -> str:
    """Return 'Ddd DD-Mon-YYYY' string."""
    if dt is None:
        dt = _now_ist()
    return dt.strftime("%a %d-%b-%Y")


def _pct_str(value: float, decimals: int = 2) -> str:
    """Format percentage with sign and % symbol."""
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.{decimals}f}%"


def _inr(value: float) -> str:
    """Format INR with sign, e.g. '+₹2,340' or '-₹680'."""
    sign = "+" if value >= 0 else "-"
    return f"{sign}₹{abs(value):,.0f}"


def _grade_emoji(grade: str) -> str:
    mapping = {
        "A+": "🔥", "A": "⭐", "B+": "✅", "B": "🟡",
        "C": "🟠", "D": "🔴",
    }
    return mapping.get(grade.upper(), "")


def _direction_icon(direction: str) -> str:
    d = direction.upper()
    if d in ("LONG", "BUY"):
        return "🟢 LONG"
    if d in ("SHORT", "SELL"):
        return "🔴 SHORT"
    return direction


def _progress_bar(pct: float, width: int = 10) -> str:
    """Unicode block progress bar, e.g. ████████░░ 80%"""
    filled = max(0, min(width, round(pct / 100 * width)))
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {pct:.0f}%"


def _vix_regime_icon(vix: float) -> str:
    if vix < 15:
        return "🟢 LOW"
    if vix < 22:
        return "🟡 MEDIUM"
    if vix < 28:
        return "🟠 HIGH"
    return "🔴 EXTREME"


# ---------------------------------------------------------------------------
# Internal text-builder functions
# ---------------------------------------------------------------------------

def _build_entry_card_text(
    symbol: str,
    direction: str,
    entry: float,
    sl: float,
    t1: float,
    t2: float,
    score: float,
    grade: str,
    rationale: list,
    risk_inr: float,
    rr: float,
    patterns: list,
) -> str:
    sl_pct = (sl - entry) / entry * 100
    t1_pct = (t1 - entry) / entry * 100
    t2_pct = (t2 - entry) / entry * 100
    reward_inr = risk_inr * rr
    dir_icon = _direction_icon(direction)
    grade_icon = _grade_emoji(grade)
    signals_str = " | ".join(rationale) if rationale else "—"
    patterns_str = ", ".join(patterns) if patterns else "—"
    now = _now_ist()

    return (
        f"<pre>╔══════════════════════════════════╗\n"
        f"║  {dir_icon}  {symbol:<10}│ Grade: {grade} {grade_icon}  ║\n"
        f"╚══════════════════════════════════╝</pre>\n"
        f"📊 <b>Score:</b> {score:.0f}/100  │  <b>R:R</b> {rr:.1f}:1\n"
        f"\n"
        f"<code>Entry    ₹ {entry:>10,.2f}\n"
        f"Stop     ₹ {sl:>10,.2f}  ({_pct_str(sl_pct)})\n"
        f"Target1  ₹ {t1:>10,.2f}  ({_pct_str(t1_pct)})\n"
        f"Target2  ₹ {t2:>10,.2f}  ({_pct_str(t2_pct)})\n"
        f"Risk     ₹ {risk_inr:>7,.0f}  │  Reward ₹ {reward_inr:,.0f}</code>\n"
        f"\n"
        f"🔍 <b>Signals:</b> <code>{signals_str}</code>\n"
        f"📐 <b>Patterns:</b> <code>{patterns_str}</code>\n"
        f"⏰ <b>{_ist_time_str(now)}</b>  │  NSE Equity MIS"
    )


def _build_exit_card_text(
    symbol: str,
    direction: str,
    entry: float,
    exit_price: float,
    pnl_inr: float,
    pnl_pct: float,
    r_multiple: float,
    grade: str,
    hold_minutes: int,
    exit_reason: str,
) -> str:
    result_icon = "✅" if pnl_inr >= 0 else "❌"
    pnl_str = _inr(pnl_inr)
    pnl_pct_str = _pct_str(pnl_pct)
    r_sign = "+" if r_multiple >= 0 else ""
    grade_icon = _grade_emoji(grade)
    now = _now_ist()

    return (
        f"<pre>╔══════════════════════════════════╗\n"
        f"║  {result_icon} CLOSED  {symbol:<8}│  {pnl_str}  ║\n"
        f"╚══════════════════════════════════╝</pre>\n"
        f"<code>Entry ₹{entry:,.2f} → Exit ₹{exit_price:,.2f}</code>\n"
        f"<b>P&L:</b> <code>{pnl_str}  ({pnl_pct_str})  │  {r_sign}{r_multiple:.1f}R</code>\n"
        f"Hold: <b>{hold_minutes} min</b>  │  Grade <b>{grade}</b> {grade_icon}  │  "
        f"<code>{exit_reason}</code>\n"
        f"⏰ {_ist_time_str(now)}"
    )


def _build_signal_card_text(
    symbol: str,
    direction: str,
    score: float,
    grade: str,
    rationale: list,
    entry: float,
    sl: float,
    t1: float,
    signal_count_today: int,
) -> str:
    now = _now_ist()
    dir_upper = direction.upper()
    grade_icon = _grade_emoji(grade)
    signals_str = ", ".join(rationale) if rationale else "—"

    return (
        f"🔔 <b>SIGNAL</b>  <code>{symbol}</code>  <b>{dir_upper}</b>  │  "
        f"<b>{grade}</b> {grade_icon}  │  <b>{score:.0f}pts</b>\n"
        f"<code>Entry ₹{entry:,.0f} │ SL ₹{sl:,.0f} │ T1 ₹{t1:,.0f}</code>\n"
        f"Signals: <code>[{signals_str}]</code>\n"
        f"Signal #{signal_count_today} today  │  {_ist_time_str(now)}"
    )


def _build_morning_brief_text(
    nifty_level: float,
    nifty_chg_pct: float,
    vix: float,
    vix_regime: str,
    pcr: float,
    fii_net_cr: float,
    sector_leaders: list,
    events_today: list,
    regime_summary: str,
    watchlist_count: int = 0,
    min_score: float = 0.0,
) -> str:
    now = _now_ist()
    nifty_str = _pct_str(nifty_chg_pct)
    vix_icon = _vix_regime_icon(vix)
    pcr_bias = "BULLISH" if pcr > 1.0 else "BEARISH" if pcr < 0.8 else "NEUTRAL"
    fii_abs = f"₹{abs(fii_net_cr):.0f}Cr"
    fii_sign = "+" if fii_net_cr >= 0 else "-"
    fii_str = f"{fii_sign}{fii_abs}"
    fii_flow = "NET BUY" if fii_net_cr >= 0 else "NET SELL"

    sector_parts = []
    for name, chg in sector_leaders:
        arrow = "▲" if chg >= 0 else "▼"
        sector_parts.append(f"{name} {arrow} {_pct_str(chg, 1)}")
    sectors_str = " │ ".join(sector_parts) if sector_parts else "—"
    events_str = ", ".join(events_today) if events_today else "None high-impact today"

    wl_line = ""
    if watchlist_count or min_score:
        wl_line = (
            f"\n⚡ Bot armed │ Watchlist: <b>{watchlist_count}</b> symbols "
            f"│ Min score: <b>{min_score:.0f}</b>"
        )

    return (
        f"📋 <b>MORNING BRIEF</b>  {_ist_date_str(now)}  {_ist_time_str(now)}\n"
        f"<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        f"NIFTY  <b>{nifty_level:,.0f}</b>  {nifty_str}  │  VIX  <b>{vix:.1f}</b>  {vix_icon}\n"
        f"PCR    <b>{pcr:.2f}</b> {pcr_bias}    │  FII  <b>{fii_str}</b> {fii_flow}\n"
        f"<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        f"REGIME: <b>{regime_summary}</b>\n"
        f"SECTORS: <code>{sectors_str}</code>\n"
        f"EVENTS: {events_str}"
        f"{wl_line}"
    )


def _build_eod_report_text(
    total_pnl_inr: float,
    win_rate: float,
    trades: int,
    wins: int,
    losses: int,
    best_trade: tuple,
    worst_trade: tuple,
    max_dd_pct: float,
    monthly_pnl_inr: float,
    monthly_target_inr: float,
    sharpe: float,
    capital: float = 0.0,
) -> str:
    now = _now_ist()
    pnl_pct = (total_pnl_inr / capital * 100) if capital else 0.0
    pnl_str = _inr(total_pnl_inr)
    pnl_pct_str = _pct_str(pnl_pct)
    pnl_ok = "✅" if total_pnl_inr >= 0 else "❌"

    wr_pct = win_rate * 100 if win_rate <= 1.0 else win_rate
    dd_ok = "✅" if max_dd_pct < 2.0 else "⚠️"
    sharpe_icon = "🔥" if sharpe >= 2.0 else "✅" if sharpe >= 1.5 else "🟡"

    best_sym, best_pnl, best_r = best_trade
    worst_sym, worst_pnl, worst_r = worst_trade
    best_str = f"{best_sym} {_inr(best_pnl)}  (+{best_r:.1f}R)"
    worst_str = f"{worst_sym} {_inr(worst_pnl)}  ({worst_r:+.1f}R)"

    monthly_pct = (monthly_pnl_inr / monthly_target_inr * 100) if monthly_target_inr else 0.0
    monthly_pct = min(100.0, max(0.0, monthly_pct))
    bar = _progress_bar(monthly_pct)

    # Days left in month
    if now.month < 12:
        next_month_start = datetime(now.year, now.month + 1, 1, tzinfo=IST)
    else:
        next_month_start = datetime(now.year + 1, 1, 1, tzinfo=IST)
    days_left = (next_month_start - now).days

    return (
        f"📊 <b>EOD REPORT</b>  {_ist_date_str(now)}\n"
        f"<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        f"P&L Today   <b>{pnl_str}  ({pnl_pct_str})</b>  {pnl_ok}\n"
        f"Win Rate    <b>{wr_pct:.0f}%</b>  ({wins}W / {losses}L)  │  <b>{trades}</b> trades\n"
        f"Best Trade  <code>{best_str}</code>\n"
        f"Worst Trade <code>{worst_str}</code>\n"
        f"Max DD      <b>{max_dd_pct:.1f}%</b>  {dd_ok} under limit\n"
        f"Sharpe      <b>{sharpe:.2f}</b> {sharpe_icon}\n"
        f"<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        f"Monthly Progress: <code>{bar}</code>\n"
        f"<code>₹{monthly_pnl_inr:,.0f} / ₹{monthly_target_inr:,.0f} target  │  +{days_left}d left</code>"
    )


def _build_risk_alert_text(
    alert_type: str,
    message: str,
    portfolio_heat_pct: float,
    daily_dd_pct: float,
) -> str:
    return (
        f"⚠️ <b>RISK ALERT — {alert_type}</b>\n"
        f"Portfolio heat: <b>{portfolio_heat_pct:.0f}%</b>  │  "
        f"Daily DD: <b>{daily_dd_pct:.1f}%</b>\n"
        f"{message}\n"
        f"⏰ {_ist_time_str()}"
    )


def _build_kill_switch_text(
    reason: str,
    positions_closed: int,
    pnl_today: float,
) -> str:
    pnl_str = _inr(pnl_today)
    pnl_icon = "✅" if pnl_today >= 0 else "❌"

    return (
        f"🚨 <b>KILL SWITCH ACTIVATED</b>\n"
        f"<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        f"Reason:   <b>{reason}</b>\n"
        f"Closed:   <b>{positions_closed}</b> position(s)\n"
        f"P&L:      <b>{pnl_str}</b> {pnl_icon}\n"
        f"<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        f"⛔ All trading halted.  ⏰ {_ist_time_str()}"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_trade_entry_card(
    symbol: str,
    direction: str,
    entry: float,
    sl: float,
    t1: float,
    t2: float,
    score: float,
    grade: str,
    rationale: list,
    risk_inr: float,
    rr: float,
    patterns: list | None = None,
) -> bool:
    """Send Bloomberg-style trade entry card. Returns True on success."""
    text = _build_entry_card_text(
        symbol, direction, entry, sl, t1, t2, score, grade,
        rationale, risk_inr, rr, patterns or [],
    )
    return _send_html(text)


def send_trade_exit_card(
    symbol: str,
    direction: str,
    entry: float,
    exit_price: float,
    pnl_inr: float,
    pnl_pct: float,
    r_multiple: float,
    grade: str,
    hold_minutes: int,
    exit_reason: str,
) -> bool:
    """Send Bloomberg-style trade exit card. Returns True on success."""
    text = _build_exit_card_text(
        symbol, direction, entry, exit_price, pnl_inr, pnl_pct,
        r_multiple, grade, hold_minutes, exit_reason,
    )
    return _send_html(text)


def send_signal_alert_card(
    symbol: str,
    direction: str,
    score: float,
    grade: str,
    rationale: list,
    entry: float,
    sl: float,
    t1: float,
    signal_count_today: int,
) -> bool:
    """Send compact signal alert for MANUAL_SIGNALS_ONLY mode. Returns True on success."""
    text = _build_signal_card_text(
        symbol, direction, score, grade, rationale, entry, sl, t1, signal_count_today,
    )
    return _send_html(text)


def send_morning_brief_card(
    nifty_level: float,
    nifty_chg_pct: float,
    vix: float,
    vix_regime: str,
    pcr: float,
    fii_net_cr: float,
    sector_leaders: list,
    events_today: list,
    regime_summary: str,
    watchlist_count: int = 0,
    min_score: float = 0.0,
) -> bool:
    """Send morning market brief card. Returns True on success."""
    text = _build_morning_brief_text(
        nifty_level, nifty_chg_pct, vix, vix_regime, pcr, fii_net_cr,
        sector_leaders, events_today, regime_summary, watchlist_count, min_score,
    )
    return _send_html(text)


def send_eod_report_card(
    total_pnl_inr: float,
    win_rate: float,
    trades: int,
    wins: int,
    losses: int,
    best_trade: tuple,
    worst_trade: tuple,
    max_dd_pct: float,
    monthly_pnl_inr: float,
    monthly_target_inr: float,
    sharpe: float,
    capital: float = 0.0,
) -> bool:
    """Send EOD performance report card. Returns True on success."""
    text = _build_eod_report_text(
        total_pnl_inr, win_rate, trades, wins, losses,
        best_trade, worst_trade, max_dd_pct,
        monthly_pnl_inr, monthly_target_inr, sharpe, capital,
    )
    return _send_html(text)


def send_risk_alert_card(
    alert_type: str,
    message: str,
    portfolio_heat_pct: float,
    daily_dd_pct: float,
) -> bool:
    """Send risk / circuit-breaker alert card. Returns True on success."""
    text = _build_risk_alert_text(alert_type, message, portfolio_heat_pct, daily_dd_pct)
    return _send_html(text)


def send_kill_switch_card(
    reason: str,
    positions_closed: int,
    pnl_today: float,
) -> bool:
    """Send kill-switch activation card. Returns True on success."""
    text = _build_kill_switch_text(reason, positions_closed, pnl_today)
    return _send_html(text)


# ---------------------------------------------------------------------------
# Demo / self-test
# ---------------------------------------------------------------------------

def _demo() -> None:
    """Print all card templates with synthetic data (no Telegram send)."""
    sep = "\n" + "=" * 60 + "\n"

    cards = [
        (
            "CARD 1 — TRADE ENTRY",
            _build_entry_card_text(
                symbol="RELIANCE",
                direction="LONG",
                entry=2847.50,
                sl=2801.00,
                t1=2921.00,
                t2=2994.50,
                score=87,
                grade="A+",
                rationale=["ORB_BULL", "VWAP_RECLAIM", "RSI_CROSS"],
                risk_inr=920,
                rr=2.8,
                patterns=["Bull_Flag", "Engulfing"],
            ),
        ),
        (
            "CARD 2 — TRADE EXIT",
            _build_exit_card_text(
                symbol="RELIANCE",
                direction="LONG",
                entry=2847.50,
                exit_price=2921.00,
                pnl_inr=2340,
                pnl_pct=2.58,
                r_multiple=2.8,
                grade="A+",
                hold_minutes=47,
                exit_reason="TARGET_1_HIT",
            ),
        ),
        (
            "CARD 3 — SIGNAL ALERT",
            _build_signal_card_text(
                symbol="HDFCBANK",
                direction="LONG",
                score=91,
                grade="A+",
                rationale=["VWAP_RECLAIM", "ORB_BULL", "MACD_CROSS"],
                entry=1642,
                sl=1618,
                t1=1690,
                signal_count_today=4,
            ),
        ),
        (
            "CARD 4 — MORNING BRIEF",
            _build_morning_brief_text(
                nifty_level=24850,
                nifty_chg_pct=0.34,
                vix=14.2,
                vix_regime="LOW",
                pcr=1.18,
                fii_net_cr=842,
                sector_leaders=[("IT", 1.2), ("BANK", 0.8), ("METAL", -0.4)],
                events_today=[],
                regime_summary="TRENDING_BULL — momentum setups preferred",
                watchlist_count=42,
                min_score=65,
            ),
        ),
        (
            "CARD 5 — EOD REPORT",
            _build_eod_report_text(
                total_pnl_inr=4840,
                win_rate=0.67,
                trades=6,
                wins=4,
                losses=2,
                best_trade=("INFY", 2100, 1.8),
                worst_trade=("SBIN", -680, -1.0),
                max_dd_pct=1.2,
                monthly_pnl_inr=18200,
                monthly_target_inr=23500,
                sharpe=2.41,
                capital=500000,
            ),
        ),
        (
            "CARD 6 — RISK ALERT",
            _build_risk_alert_text(
                alert_type="DAILY_LOSS_LIMIT",
                message=(
                    "PAUSING new entries — daily limit 2% near\n"
                    "Action: Hold existing, no new signals"
                ),
                portfolio_heat_pct=68,
                daily_dd_pct=1.8,
            ),
        ),
        (
            "CARD 7 — KILL SWITCH",
            _build_kill_switch_text(
                reason="/kill command from Telegram",
                positions_closed=3,
                pnl_today=1560,
            ),
        ),
    ]

    print("=" * 60)
    print("DEMO — telegram_cards_india.py — All Card Formats")
    print("=" * 60)

    for label, card_html in cards:
        print(sep)
        print(f"[{label}]")
        print()
        # Strip HTML tags for readable terminal output
        plain = re.sub(r"<[^>]+>", "", card_html)
        print(plain)

    print(sep)
    print("All 7 cards rendered successfully.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--demo" in sys.argv:
        _demo()
        sys.exit(0)
    print("Usage: python3 india/telegram_cards_india.py --demo")
    sys.exit(1)
