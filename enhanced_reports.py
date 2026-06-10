"""
enhanced_reports.py — Top-1% Trading Reports

4 reports delivered via Telegram:
1. Morning Brief (8:00 AM ET) — market context + day's top setups
2. Mid-Day Pulse (1:00 PM ET) — P&L snapshot + afternoon strategy
3. EOD Performance Report (4:15 PM ET) — full day metrics + attribution
4. Weekly Edge Report (Friday 4:30 PM ET) — weekly analysis + compounding

All formatted as Bloomberg-style text with clear sections.
All sent via Telegram using TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from env.
All fail-open — never crash the bot.
"""

import logging
import os
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)

# ── last-sent cache to avoid duplicate sends ───────────────────────────────
_last_sent: dict = {}

# ── Timezone helpers ───────────────────────────────────────────────────────
try:
    from utils import get_current_et_time as _get_et
except Exception:
    def _get_et():  # type: ignore[misc]
        from datetime import timezone, timedelta
        return datetime.now(tz=timezone(timedelta(hours=-4)))


# ── Internal send helper ───────────────────────────────────────────────────

def _send_telegram(text: str) -> bool:
    # US bot disabled by owner — never send unless explicitly re-enabled
    import os as _os_g
    if _os_g.getenv("US_BOT_ENABLED", "False") != "True":
        return False
    """Internal: send text to Telegram. Returns True on success."""
    try:
        import requests  # type: ignore[import]
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            logger.debug("enhanced_reports: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return True
        logger.debug(f"enhanced_reports: Telegram HTTP {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        logger.debug(f"enhanced_reports: _send_telegram error: {e}")
        return False


# ── Helper: deduplicate sends ──────────────────────────────────────────────

def _already_sent_today(key: str) -> bool:
    """Return True if we already sent this report today (ET date)."""
    today = _get_et().strftime("%Y-%m-%d")
    return _last_sent.get(key) == today


def _mark_sent(key: str) -> None:
    today = _get_et().strftime("%Y-%m-%d")
    _last_sent[key] = today


# ── Helper: safe market data fetch ────────────────────────────────────────

def _get_spy_vix() -> tuple:
    """Return (spy_pct_str, vix_str) or fallback strings."""
    try:
        import yfinance as yf  # type: ignore[import]
        spy = yf.Ticker("SPY")
        info = spy.fast_info
        prev_close = getattr(info, "previous_close", None) or getattr(info, "regularMarketPreviousClose", None)
        pre_price = getattr(info, "pre_market_price", None) or getattr(info, "regularMarketPrice", None)
        if prev_close and pre_price:
            spy_pct = (pre_price - prev_close) / prev_close * 100
            spy_str = f"{spy_pct:+.1f}%"
        else:
            spy_str = "n/a"
        vix_t = yf.Ticker("^VIX")
        vix_info = vix_t.fast_info
        vix_val = getattr(vix_info, "last_price", None) or getattr(vix_info, "regularMarketPrice", None)
        vix_str = f"{vix_val:.1f}" if vix_val else "n/a"
        return spy_str, vix_str
    except Exception as e:
        logger.debug(f"enhanced_reports: _get_spy_vix error: {e}")
        return "n/a", "n/a"


def _get_sector_leaders() -> list:
    """Return top 3 sector ETFs by 5-day performance."""
    sectors = [
        ("XLK", "Tech"), ("XLF", "Finance"), ("XLE", "Energy"),
        ("XLV", "Health"), ("XLI", "Industrials"), ("XLC", "Comms"),
        ("XLY", "Cons Disc"), ("XLP", "Cons Stpl"), ("XLRE", "Real Est"),
        ("XLB", "Materials"), ("XLU", "Utilities"),
    ]
    results = []
    try:
        import yfinance as yf  # type: ignore[import]
        for ticker, name in sectors:
            try:
                t = yf.Ticker(ticker)
                hist = t.history(period="6d")
                if len(hist) >= 5:
                    chg = (hist["Close"].iloc[-1] - hist["Close"].iloc[-5]) / hist["Close"].iloc[-5] * 100
                    results.append((ticker, name, chg))
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"enhanced_reports: sector leaders error: {e}")
    results.sort(key=lambda x: x[2], reverse=True)
    return results[:3]


def _get_morning_bias() -> tuple:
    """Return (bias_label, bias_score, regime, risk_mode) from morning_intelligence if available."""
    try:
        import morning_intelligence as mi  # type: ignore[import]
        _mod = mi
        last = getattr(_mod, "_last_thesis", None)
        if last is None:
            for attr in dir(_mod):
                obj = getattr(_mod, attr, None)
                if obj and hasattr(obj, "market_bias"):
                    last = obj
                    break
        if last:
            bias = getattr(last, "market_bias", "NEUTRAL")
            score = int(getattr(last, "bias_score", 0) or 0)
            regime = getattr(last, "trading_mode", "NORMAL")
            mult = float(getattr(last, "size_multiplier", 1.0) or 1.0)
            risk_mode = "NORMAL (1.0x size)" if mult == 1.0 else f"{'AGGRESSIVE' if mult > 1 else 'DEFENSIVE'} ({mult:.1f}x size)"
            return bias, score, regime, risk_mode
    except Exception as e:
        logger.debug(f"enhanced_reports: morning_bias error: {e}")
    return "NEUTRAL", 0, "NORMAL", "NORMAL (1.0x size)"


def _bias_emoji(bias: str) -> str:
    b = (bias or "").upper()
    if "BULL" in b:
        return "🟢"
    if "BEAR" in b:
        return "🔴"
    return "🟡"


def _get_watchlist_highlights() -> list:
    """Return list of (symbol, note) tuples for top 5 watchlist stocks."""
    try:
        from premium_scanner import get_top_setups  # type: ignore[import]
        setups = get_top_setups(n=5)
        return [(s.get("symbol", ""), s.get("note", "")) for s in setups]
    except Exception:
        pass
    try:
        from broker import WATCHLIST  # type: ignore[import]
        wl = list(WATCHLIST)[:5]
        return [(sym, "watchlist candidate") for sym in wl]
    except Exception:
        pass
    return []


def _get_capital_info() -> tuple:
    """Return (capital, daily_target, max_risk) floats."""
    try:
        import config  # type: ignore[import]
        cap = float(getattr(config, "MAX_DAILY_CAPITAL", 5000))
        target_pct = float(getattr(config, "DAILY_TARGET_PCT", 1.0))
        risk_pct = float(getattr(config, "MAX_RISK_PER_TRADE_PCT", 0.8))
        daily_target = cap * target_pct / 100
        max_risk = cap * risk_pct / 100
        return cap, daily_target, max_risk
    except Exception:
        return 5000.0, 50.0, 40.0


# ─────────────────────────────────────────────────────────────────────────────
# 1. MORNING BRIEF
# ─────────────────────────────────────────────────────────────────────────────

def send_morning_brief() -> bool:
    """
    Send the morning brief at 8:00 AM ET.
    Gathers market context, bias, sectors, watchlist, and capital info.
    Fail-open — never crashes the bot.
    """
    try:
        if _already_sent_today("morning_brief"):
            logger.debug("enhanced_reports: morning brief already sent today")
            return False

        now_et = _get_et()
        date_str = now_et.strftime("%a %b %d, %Y")
        time_str = now_et.strftime("%I:%M %p ET").lstrip("0")

        spy_pct, vix_str = _get_spy_vix()
        bias, score, regime, risk_mode = _get_morning_bias()
        bias_emoji = _bias_emoji(bias)
        top_sectors = _get_sector_leaders()
        highlights = _get_watchlist_highlights()
        capital, daily_target, max_risk = _get_capital_info()

        # Build sector block
        sector_lines = []
        for i, (ticker, name, pct) in enumerate(top_sectors, 1):
            hot = " 🔥" if i == 1 else ""
            sector_lines.append(f"  {i}. {ticker} ({name}){' ':>4}{pct:+.1f}% 5d{hot}")
        sector_block = "\n".join(sector_lines) if sector_lines else "  Data unavailable"

        # Build watchlist block
        wl_lines = []
        for sym, note in highlights[:5]:
            wl_lines.append(f"  {sym} — {note}")
        wl_block = "\n".join(wl_lines) if wl_lines else "  Watchlist loading..."

        # Key risks (pull from calendar if possible)
        risks = []
        try:
            from economic_calendar import EconomicCalendar  # type: ignore[import]
            cal = EconomicCalendar()
            events = cal.get_today_events() if hasattr(cal, "get_today_events") else []
            for ev in events[:3]:
                name_e = ev.get("event", "")
                t_e = ev.get("time", "")
                impact = ev.get("impact", "")
                if impact in ("HIGH", "CRITICAL") and name_e:
                    risks.append(f"• {name_e} {t_e} ET — pause entries ±30 min")
        except Exception:
            pass
        if not risks:
            risks.append("• Monitor SPY / VIX for regime shifts")
        if vix_str != "n/a":
            try:
                vix_val = float(vix_str)
                if vix_val > 20:
                    risks.append(f"• VIX elevated ({vix_val:.1f}) — use 0.8x size")
            except Exception:
                pass

        risk_block = "\n".join(risks)

        msg = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🌅 <b>KINGTRADES MORNING BRIEF</b>\n"
            f"   {date_str}  |  {time_str}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📊 <b>MARKET CONTEXT</b>\n"
            f"  SPY pre-mkt: {spy_pct} | VIX: {vix_str}\n"
            f"  Bias: {bias_emoji} {bias} (+{score} score)\n"
            f"  Regime: {regime}\n"
            f"  Risk Mode: {risk_mode}\n"
            "\n"
            "🏆 <b>TOP 3 SECTORS TODAY</b>\n"
            f"{sector_block}\n"
            "\n"
            f"⚡ <b>WATCHLIST HIGHLIGHTS</b>\n"
            f"{wl_block}\n"
            "\n"
            "⚠️ <b>KEY RISKS TODAY</b>\n"
            f"{risk_block}\n"
            "\n"
            "🎯 <b>TODAY'S TARGETS</b>\n"
            f"  Daily target: +${daily_target:,.0f} (1% of ${capital:,.0f})\n"
            f"  Capital available: ${capital:,.0f}\n"
            f"  Max risk/trade: ${max_risk:,.0f}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        ok = _send_telegram(msg)
        if ok:
            _mark_sent("morning_brief")
            logger.info("enhanced_reports: morning brief sent")
        return ok

    except Exception as e:
        logger.debug(f"enhanced_reports: send_morning_brief error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 2. MID-DAY PULSE
# ─────────────────────────────────────────────────────────────────────────────

def send_midday_pulse(risk_manager=None) -> bool:
    """
    Send the mid-day pulse at 1:00 PM ET.
    Shows current P&L, open positions, and afternoon outlook.
    Fail-open — never crashes the bot.
    """
    try:
        if _already_sent_today("midday_pulse"):
            logger.debug("enhanced_reports: midday pulse already sent today")
            return False

        now_et = _get_et()
        time_str = now_et.strftime("%I:%M %p ET").lstrip("0")

        # Pull P&L + positions from risk_manager if provided
        closed_pnl = 0.0
        open_unrealized = 0.0
        daily_trades = 0
        daily_wins = 0
        open_positions = []

        if risk_manager is not None:
            try:
                state = risk_manager.state
                closed_pnl = float(getattr(state, "daily_pnl", 0) or 0)
                daily_trades = int(getattr(state, "daily_trades", 0) or 0)
                daily_wins = int(getattr(state, "daily_wins", 0) or 0)
                positions = getattr(state, "positions", {}) or {}
                for sym, pos in positions.items():
                    qty = getattr(pos, "quantity", 0)
                    entry = getattr(pos, "entry_price", 0)
                    unreal = getattr(pos, "unrealized_pnl", 0) or 0
                    direction = getattr(pos, "direction", "LONG")
                    r_mult = float(getattr(pos, "r_multiple", 0) or 0)
                    open_unrealized += float(unreal)
                    open_positions.append((sym, direction, qty, entry, float(unreal), r_mult))
            except Exception as _e:
                logger.debug(f"enhanced_reports: risk_manager read error: {_e}")

        total_pnl = closed_pnl + open_unrealized
        win_rate = (daily_wins / daily_trades * 100) if daily_trades > 0 else 0.0
        target_hit = "✅ TARGET HIT" if total_pnl > 0 else ("⚠️ BELOW TARGET" if total_pnl < 0 else "")
        target_emoji = "✅" if total_pnl >= 0 else "❌"

        # Build positions block
        pos_lines = []
        for sym, direction, qty, entry, unreal, r_mult in open_positions[:5]:
            sign = "+" if unreal >= 0 else ""
            pos_lines.append(
                f"  {sym} {direction}  {qty}sh  @${entry:.2f} → {sign}${unreal:.0f} ({r_mult:+.1f}x R)"
            )
        pos_block = "\n".join(pos_lines) if pos_lines else "  No open positions"

        # SPY status
        spy_pct, _ = _get_spy_vix()

        # Afternoon strategy hint
        strategy = "MOMENTUM mode" if total_pnl >= 0 else "DEFENSIVE mode — protect gains"

        # Upcoming events
        events_lines = []
        try:
            from economic_calendar import EconomicCalendar  # type: ignore[import]
            cal = EconomicCalendar()
            events = cal.get_today_events() if hasattr(cal, "get_today_events") else []
            for ev in events[:2]:
                t_e = ev.get("time", "")
                name_e = ev.get("event", "")
                impact = ev.get("impact", "")
                if name_e and t_e:
                    pause = " (PAUSE entries)" if impact in ("HIGH", "CRITICAL") else ""
                    events_lines.append(f"  {t_e} — {name_e}{pause}")
        except Exception:
            pass
        if not events_lines:
            events_lines.append("  3:00 PM — Power Hour begins ⚡")
        events_block = "\n".join(events_lines)

        msg = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📡 <b>MID-DAY PULSE</b>  |  {time_str}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💰 <b>P&amp;L SO FAR</b>\n"
            f"  Closed:  {'+' if closed_pnl >= 0 else ''}${closed_pnl:,.2f}\n"
            f"  Open:    {'+' if open_unrealized >= 0 else ''}${open_unrealized:,.2f} unrealized\n"
            f"  Total:   {'+' if total_pnl >= 0 else ''}${total_pnl:,.2f} {target_emoji} {target_hit}\n"
            "\n"
            f"📋 <b>OPEN POSITIONS ({len(open_positions)})</b>\n"
            f"{pos_block}\n"
            "\n"
            "📊 <b>TODAY'S STATS</b>\n"
            f"  Trades: {daily_trades} | Wins: {daily_wins} | WR: {win_rate:.0f}%\n"
            "\n"
            "🎯 <b>AFTERNOON OUTLOOK</b>\n"
            f"  Market: SPY {spy_pct} — ongoing\n"
            f"  Strategy: {strategy}\n"
            "\n"
            "⏰ <b>NEXT EVENTS</b>\n"
            f"{events_block}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        ok = _send_telegram(msg)
        if ok:
            _mark_sent("midday_pulse")
            logger.info("enhanced_reports: midday pulse sent")
        return ok

    except Exception as e:
        logger.debug(f"enhanced_reports: send_midday_pulse error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 3. EOD PERFORMANCE REPORT
# ─────────────────────────────────────────────────────────────────────────────

def send_eod_performance_report(daily_stats: dict) -> bool:
    """
    Send the full EOD performance report at 4:15 PM ET.
    daily_stats keys: trades_taken, wins, losses, net_pnl, signals_scanned,
                      best_trade, worst_trade.
    Augments with data from performance_analytics if available.
    Fail-open — never crashes the bot.
    """
    try:
        now_et = _get_et()
        date_str = now_et.strftime("%a %b %d")

        stats = daily_stats or {}
        trades_taken = int(stats.get("trades_taken", 0))
        net_pnl = float(stats.get("net_pnl", 0.0))
        wins = int(stats.get("wins", trades_taken))
        losses = int(stats.get("losses", 0))
        if wins + losses == 0 and trades_taken > 0:
            wins = trades_taken
        signals_scanned = int(stats.get("signals_scanned", 0))
        best_trade = stats.get("best_trade", None)
        worst_trade = stats.get("worst_trade", None)

        # Try to enrich from performance_analytics
        monthly_pnl = 0.0
        monthly_pct = 0.0
        sharpe = 0.0
        sortino = 0.0
        max_dd = 0.0
        win_streak = 0
        profit_factor = 0.0
        avg_hold_min = 0
        avg_r = 0.0
        best_trades_list = []
        worst_trades_list = []
        signal_attribution: dict = {}

        try:
            import pattern_analytics as pa  # type: ignore[import]
            summary = pa.get_daily_summary() if hasattr(pa, "get_daily_summary") else {}
            monthly_pnl = float(summary.get("monthly_pnl", 0) or 0)
            monthly_pct = float(summary.get("monthly_pct", 0) or 0)
            sharpe = float(summary.get("sharpe", 0) or 0)
            sortino = float(summary.get("sortino", 0) or 0)
            max_dd = float(summary.get("max_drawdown_pct", 0) or 0)
            win_streak = int(summary.get("win_streak", 0) or 0)
            profit_factor = float(summary.get("profit_factor", 0) or 0)
            avg_hold_min = int(summary.get("avg_hold_minutes", 0) or 0)
            avg_r = float(summary.get("avg_r_multiple", 0) or 0)
            best_trades_list = summary.get("best_trades", [])[:3]
            worst_trades_list = summary.get("worst_trades", [])[:1]
            signal_attribution = summary.get("signal_attribution", {})
        except Exception as _pe:
            logger.debug(f"enhanced_reports: pattern_analytics read error: {_pe}")

        capital, daily_target, _ = _get_capital_info()
        win_rate = (wins / trades_taken * 100) if trades_taken > 0 else 0.0
        pnl_pct = (net_pnl / capital * 100) if capital else 0.0
        pnl_emoji = "✅" if net_pnl >= 0 else "❌"
        target_achieved = ""
        if daily_target and net_pnl > 0:
            mult = net_pnl / daily_target
            target_achieved = f"[ACHIEVED {mult:.1f}x]" if mult >= 1 else f"[{net_pnl / daily_target * 100:.0f}% of target]"
        elif net_pnl < 0:
            target_achieved = "[MISSED]"

        def r_label(r: float) -> str:
            if r >= 2.0:
                return "EXCELLENT"
            if r >= 1.0:
                return "GOOD"
            if r >= 0.5:
                return "OK"
            return "POOR"

        avg_r_label = r_label(avg_r)

        # Build best trades block
        best_lines = []
        if best_trades_list:
            for i, tr in enumerate(best_trades_list, 1):
                sym = tr.get("symbol", "?")
                side = tr.get("direction", "LONG")
                pnl = float(tr.get("pnl", 0))
                r = float(tr.get("r_multiple", 0))
                sig = tr.get("signal_type", "")
                best_lines.append(f"  {i}. {sym}  {side}   +${pnl:.0f}  (+{r:.1f}R)  {sig}")
        elif best_trade:
            sym = best_trade.get("symbol", "?") if isinstance(best_trade, dict) else str(best_trade)
            best_lines.append(f"  1. {sym}  (best trade)")
        best_block = "\n".join(best_lines) if best_lines else "  No completed trades"

        # Build worst trades block
        worst_lines = []
        if worst_trades_list:
            for tr in worst_trades_list:
                sym = tr.get("symbol", "?")
                side = tr.get("direction", "SHORT")
                pnl = float(tr.get("pnl", 0))
                r = float(tr.get("r_multiple", 0))
                reason = tr.get("exit_reason", "stopped out")
                worst_lines.append(f"  1. {sym}  {side}  ${pnl:.0f}  ({r:.1f}R)  — {reason}")
        elif worst_trade:
            sym = worst_trade.get("symbol", "?") if isinstance(worst_trade, dict) else str(worst_trade)
            worst_lines.append(f"  1. {sym}  (worst trade)")
        worst_block = "\n".join(worst_lines) if worst_lines else "  No losing trades 🎉"

        # Signal attribution block
        attr_lines = []
        for sig_type, data in list(signal_attribution.items())[:5]:
            if isinstance(data, dict):
                w = data.get("wins", 0)
                t = data.get("trades", 0)
                p = float(data.get("pnl", 0))
                emoji = "✅" if w == t else ("❌" if w == 0 else "")
                attr_lines.append(f"  {sig_type}:    {w}/{t} {emoji}  ${p:+.0f}")
        attr_block = "\n".join(attr_lines) if attr_lines else "  Tracking enabled for next session"

        # Monthly progress
        monthly_target_pct = 20.0
        trading_days_in_month = 21
        days_elapsed = now_et.day
        trading_days_elapsed = max(1, int(days_elapsed * 5 / 7))
        trading_days_left = max(0, trading_days_in_month - trading_days_elapsed)
        if monthly_pct >= monthly_target_pct:
            pace_emoji = "🟢 ON TRACK"
        elif trading_days_left > 0:
            needed_per_day = (monthly_target_pct - monthly_pct) / trading_days_left
            pace_emoji = f"🟡 BEHIND (need +{needed_per_day:.2f}%/day)"
        else:
            pace_emoji = "🔴 MONTH END"

        streak_str = f"{win_streak} days 🔥" if win_streak >= 3 else str(win_streak)

        msg = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>KINGTRADES EOD REPORT</b>  |  {date_str}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💰 <b>P&amp;L SUMMARY</b>\n"
            f"  Net P&amp;L:    {'+' if net_pnl >= 0 else ''}${net_pnl:,.2f}  ({pnl_pct:+.1f}%) {pnl_emoji}\n"
            f"  Daily Target: ${daily_target:,.2f}  {target_achieved}\n"
            f"  Capital:    ${capital:,.0f} → ${capital + net_pnl:,.0f}\n"
            "\n"
            "📈 <b>TRADE METRICS</b>\n"
            f"  Signals scanned:  {signals_scanned}\n"
            f"  Trades taken:       {trades_taken}\n"
            f"  Win Rate:         {win_rate:.0f}% ({wins}W/{losses}L)\n"
            f"  Profit Factor:    {profit_factor:.1f}x\n"
            f"  Avg Hold:        {avg_hold_min} min\n"
            "\n"
            "🎯 <b>R-MULTIPLE ANALYSIS</b>\n"
            f"  Avg R earned:    +{avg_r:.1f}R   [{avg_r_label}]\n"
            "\n"
            "🏆 <b>BEST TRADES</b>\n"
            f"{best_block}\n"
            "\n"
            "📉 <b>WORST TRADES</b>\n"
            f"{worst_block}\n"
            "\n"
            "🔍 <b>SIGNAL ATTRIBUTION</b>\n"
            f"{attr_block}\n"
            "\n"
            "📊 <b>MONTHLY PROGRESS</b>\n"
            f"  Month P&amp;L:  ${monthly_pnl:+,.0f}  ({monthly_pct:+.1f}%)\n"
            f"  Target:     +{monthly_target_pct:.0f}%/month\n"
            f"  On pace:    {pace_emoji}\n"
            f"  Days left:  {trading_days_left} trading days\n"
            "\n"
            "📊 <b>30-DAY METRICS</b>\n"
            f"  Sharpe:      {sharpe:.1f}\n"
            f"  Sortino:     {sortino:.1f}\n"
            f"  Max DD:      {max_dd:.1f}%\n"
            f"  Win Streak:  {streak_str}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        ok = _send_telegram(msg)
        if ok:
            logger.info(f"enhanced_reports: EOD report sent (P&L={net_pnl:+.2f})")
        return ok

    except Exception as e:
        logger.debug(f"enhanced_reports: send_eod_performance_report error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 4. WEEKLY EDGE REPORT
# ─────────────────────────────────────────────────────────────────────────────

def send_weekly_report() -> bool:
    """
    Send the weekly edge report on Friday at 4:30 PM ET.
    Aggregates weekly P&L, pattern performance, and next-week playbook.
    Fail-open — never crashes the bot.
    """
    try:
        if _already_sent_today("weekly_report"):
            logger.debug("enhanced_reports: weekly report already sent today")
            return False

        now_et = _get_et()
        # Find Monday of this week
        import datetime as _dt
        days_since_monday = now_et.weekday()  # 0=Mon, 4=Fri
        week_start = now_et.date() - _dt.timedelta(days=days_since_monday)
        week_str = week_start.strftime("Week of %b %-d")

        # Load weekly P&L data
        week_net_pnl = 0.0
        week_pct = 0.0
        best_day_pnl = 0.0
        best_day_name = "N/A"
        worst_day_pnl = 0.0
        worst_day_name = "N/A"
        avg_daily_pnl = 0.0
        weekly_trades = 0
        weekly_wins = 0
        weekly_profit_factor = 0.0
        weekly_avg_r = 0.0
        weekly_max_dd = 0.0
        pattern_performance: list = []
        hot_sectors: list = []
        cold_sectors: list = []
        capital, _, _ = _get_capital_info()

        try:
            import json
            import pathlib
            try:
                import config  # type: ignore[import]
                weekly_file = pathlib.Path(getattr(config, "WEEKLY_DATA_FILE", "data/weekly_pnl.json"))
            except Exception:
                weekly_file = pathlib.Path("data/weekly_pnl.json")
            if weekly_file.exists():
                data = json.loads(weekly_file.read_text())
                week_net_pnl = float(data.get("net_pnl", 0) or 0)
                week_pct = float(data.get("weekly_pct", 0) or 0)
                days_data = data.get("days", {})
                if days_data:
                    day_pnls = [(d, float(v)) for d, v in days_data.items()]
                    day_pnls.sort(key=lambda x: x[1], reverse=True)
                    if day_pnls:
                        best_day_name, best_day_pnl = day_pnls[0]
                        worst_day_name, worst_day_pnl = day_pnls[-1]
                        avg_daily_pnl = sum(v for _, v in day_pnls) / len(day_pnls)
        except Exception as _we:
            logger.debug(f"enhanced_reports: weekly file read error: {_we}")

        # Pull pattern performance from pattern_analytics
        try:
            import pattern_analytics as pa  # type: ignore[import]
            weekly = pa.get_weekly_summary() if hasattr(pa, "get_weekly_summary") else {}
            weekly_trades = int(weekly.get("total_trades", 0) or 0)
            weekly_wins = int(weekly.get("wins", 0) or 0)
            weekly_profit_factor = float(weekly.get("profit_factor", 0) or 0)
            weekly_avg_r = float(weekly.get("avg_r_multiple", 0) or 0)
            weekly_max_dd = float(weekly.get("max_drawdown_pct", 0) or 0)
            pattern_performance = weekly.get("pattern_performance", [])[:5]
            hot_sectors = weekly.get("hot_sectors", [])[:2]
            cold_sectors = weekly.get("cold_sectors", [])[:1]
        except Exception as _pae:
            logger.debug(f"enhanced_reports: pattern_analytics weekly error: {_pae}")

        weekly_wr = (weekly_wins / weekly_trades * 100) if weekly_trades > 0 else 0.0

        # Build pattern leaderboard
        pattern_lines = []
        avoid_patterns = []
        for i, p in enumerate(pattern_performance, 1):
            if isinstance(p, dict):
                name = p.get("name", "")
                w = int(p.get("wins", 0))
                t = int(p.get("trades", 0))
                pnl = float(p.get("pnl", 0))
                wr = (w / t * 100) if t > 0 else 0
                if wr < 40 and t >= 3:
                    avoid_patterns.append(f"  ❌ AVOID: {name} patterns {wr:.0f}% WR")
                else:
                    pattern_lines.append(f"  {i}. {name}:    {w}/{t}  {wr:.0f}% WR  +${pnl:.0f}")
        pattern_block = "\n".join(pattern_lines) if pattern_lines else "  Tracking enabled for next week"
        avoid_block = "\n".join(avoid_patterns) if avoid_patterns else ""

        # Build sector blocks
        sector_hot_lines = []
        for s in hot_sectors:
            if isinstance(s, dict):
                sector_hot_lines.append(
                    f"  {s.get('ticker', '')} ({s.get('name', '')}):    "
                    f"{s.get('trades', 0)} trades, {s.get('win_rate', 0):.0f}% WR"
                )
        sector_cold_lines = []
        for s in cold_sectors:
            if isinstance(s, dict):
                sector_cold_lines.append(
                    f"  ⚠️ Skip: {s.get('ticker', '')} ({s.get('name', '')}) — "
                    f"{s.get('win_rate', 0):.0f}% WR this week"
                )
        sector_block_parts = []
        if sector_hot_lines:
            sector_block_parts.extend(sector_hot_lines)
        if sector_cold_lines:
            sector_block_parts.extend(sector_cold_lines)
        sector_block = "\n".join(sector_block_parts) if sector_block_parts else "  Sector tracking building..."

        # Compounding projection
        week_target_pct = 5.0  # 5%/week = 20%/month
        new_capital = capital * (1 + week_pct / 100)
        projected_month_pct = ((1 + week_pct / 100) ** 4 - 1) * 100
        projected_month_capital = capital * (1 + projected_month_pct / 100)
        compounding_emoji = "🎯" if projected_month_pct >= 20 else "🔄"

        week_pace = "✅" if week_pct >= week_target_pct else f"({week_pct:.1f}% vs {week_target_pct:.0f}% target)"

        # Next week playbook
        focus_patterns = [p.get("name", "") for p in pattern_performance[:2] if isinstance(p, dict) and p.get("wins", 0) > 0]
        focus_str = " + ".join(focus_patterns) if focus_patterns else "high-quality setups"
        avoid_str = " + ".join([
            p.get("name", "") for p in pattern_performance
            if isinstance(p, dict) and p.get("wins", 0) == 0 and p.get("trades", 0) >= 2
        ][:1]) or "low-WR patterns"
        sharpe_note = "NORMAL (1.0x)" if weekly_avg_r >= 1.0 else "DEFENSIVE (0.8x)"

        msg = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 <b>WEEKLY EDGE REPORT</b>  |  {week_str}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💰 <b>WEEK SUMMARY</b>\n"
            f"  Net P&amp;L:    {'+' if week_net_pnl >= 0 else ''}${week_net_pnl:,.0f}  ({week_pct:+.1f}%)\n"
            f"  Best day:   {best_day_name} {'+' if best_day_pnl >= 0 else ''}${best_day_pnl:,.0f}\n"
            f"  Worst day:  {worst_day_name} {'+' if worst_day_pnl >= 0 else ''}${worst_day_pnl:,.0f}\n"
            f"  Avg daily:  {'+' if avg_daily_pnl >= 0 else ''}${avg_daily_pnl:,.0f}\n"
            "\n"
            "📈 <b>WEEKLY METRICS</b>\n"
            f"  Total trades: {weekly_trades} | WR: {weekly_wr:.0f}%\n"
            f"  Profit factor: {weekly_profit_factor:.1f}x\n"
            f"  Avg R: +{weekly_avg_r:.1f}R\n"
            f"  Max Drawdown: {weekly_max_dd:.1f}%\n"
            "\n"
            "🏆 <b>BEST PATTERNS THIS WEEK</b>\n"
            f"{pattern_block}\n"
        )

        if avoid_block:
            msg += f"{avoid_block}\n"

        msg += (
            "\n"
            "🔥 <b>SECTOR PERFORMANCE</b>\n"
            f"{sector_block}\n"
            "\n"
            "📊 <b>MONTHLY COMPOUNDING PROGRESS</b>\n"
            f"  Week P&amp;L: {week_pct:+.1f}% {week_pace}\n"
            f"  Target pace: +{week_target_pct:.0f}%/week for 20%/month\n"
            f"  Capital: ${capital:,.0f} → ${new_capital:,.0f} ({week_pct:+.1f}%)\n"
            f"  Projected month-end: ${projected_month_capital:,.0f} ({projected_month_pct:+.1f}%) {compounding_emoji}\n"
            "\n"
            "🎯 <b>NEXT WEEK PLAYBOOK</b>\n"
            f"  - Focus: {focus_str}\n"
            f"  - Avoid: {avoid_str}\n"
            f"  - Size: {sharpe_note}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        ok = _send_telegram(msg)
        if ok:
            _mark_sent("weekly_report")
            logger.info(f"enhanced_reports: weekly report sent (week P&L={week_net_pnl:+.2f})")
        return ok

    except Exception as e:
        logger.debug(f"enhanced_reports: send_weekly_report error: {e}")
        return False
