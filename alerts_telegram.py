"""
alerts_telegram.py — NSE Momentum Groww AI Bot
Rich Telegram Alert Engine with Charts, Gemini AI Analysis, and EOD Reports

⚠️ WARNING: This bot places REAL orders with REAL money on Groww.
Server runs in UK (UTC) — all timestamps in IST (Asia/Kolkata).

Features:
  • Signal alerts with full rationale, R:R, grade, and projected P&L
  • Candlestick chart images (mplfinance) with entry arrow + indicators
  • Gemini AI signal explanation (plain English "why this trade")
  • Trade fill / exit / SL-hit alerts with P&L in green/red
  • Circuit breaker / kill-switch alerts
  • Status report with live positions and daily P&L
  • EOD performance report with equity curve chart
  • Morning brief (overnight analysis summary)
  • IST timestamps on every message

Usage:
  from alerts_telegram import TelegramAlerter
  alerter = TelegramAlerter(token, chat_id)
  alerter.send_signal(signal, candles_df)
"""

import io
import logging
import os
import textwrap
from datetime import datetime
from typing import Optional, Dict, List, Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests as _requests

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# Currency symbol — set by broker adapter ($ for Alpaca, ₹ for Groww)
try:
    from broker import CURRENCY_SYMBOL as _CUR
except Exception:
    _CUR = "$"

# ── emoji palette ───────────────────────────────────────────
E = {
    "long":   "🟢",
    "short":  "🔴",
    "profit": "✅",
    "loss":   "❌",
    "warn":   "⚠️",
    "kill":   "🚨",
    "pause":  "⏸",
    "resume": "▶️",
    "chart":  "📊",
    "clock":  "🕐",
    "money":  "💰",
    "brain":  "🧠",
    "rocket": "🚀",
    "fire":   "🔥",
    "target": "🎯",
    "shield": "🛡",
    "trophy": "🏆",
    "pin":    "📌",
}


# ============================================================
# CHART BUILDER
# ============================================================

def _build_candle_chart(
    candles: pd.DataFrame,
    signal_price: float,
    stop_loss: float,
    target_1: float,
    target_2: float,
    direction: str,
    symbol: str,
    grade: str = "B",
) -> Optional[io.BytesIO]:
    """
    Build a candlestick chart with entry/SL/TP lines and a signal arrow.
    Returns a BytesIO PNG buffer, or None on failure.
    """
    try:
        import mplfinance as mpf
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches

        # Use last 60 candles for clarity
        df = candles.copy().tail(60)
        df.index = pd.DatetimeIndex(df.index)
        df.columns = [c.capitalize() for c in df.columns]

        # Ensure required columns
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col not in df.columns:
                return None

        entry_line = [signal_price] * len(df)
        sl_line    = [stop_loss]    * len(df)
        t1_line    = [target_1]     * len(df)
        t2_line    = [target_2]     * len(df)

        ap = [
            mpf.make_addplot(entry_line, color="#FFD700", width=1.5, linestyle="--"),
            mpf.make_addplot(sl_line,    color="#FF4444", width=1.2, linestyle=":"),
            mpf.make_addplot(t1_line,    color="#44FF88", width=1.0, linestyle="-."),
            mpf.make_addplot(t2_line,    color="#00FFCC", width=0.8, linestyle="-."),
        ]

        mc = mpf.make_marketcolors(
            up="#00C853", down="#FF1744",
            wick={"up": "#00C853", "down": "#FF1744"},
            edge={"up": "#00C853", "down": "#FF1744"},
            volume={"up": "#00C85380", "down": "#FF174480"},
        )
        s = mpf.make_mpf_style(
            base_mpl_style="dark_background",
            marketcolors=mc,
            gridstyle=":",
            gridcolor="#333333",
            facecolor="#0D1117",
            figcolor="#0D1117",
            rc={
                "axes.labelcolor": "#CCCCCC",
                "xtick.color": "#AAAAAA",
                "ytick.color": "#AAAAAA",
            },
        )

        fig, axes = mpf.plot(
            df,
            type="candle",
            style=s,
            addplot=ap,
            volume=True,
            figratio=(14, 7),
            figscale=1.2,
            title=f"\n{symbol}  |  {direction}  |  Grade: {grade}  |  {format_ist_timestamp()}",
            returnfig=True,
        )

        # Entry arrow annotation on last candle
        ax = axes[0]
        price_range = float(df["High"].max()) - float(df["Low"].min())
        offset = price_range * 0.05
        x_pos  = len(df) - 1
        color  = "#00C853" if direction == "LONG" else "#FF1744"
        if direction == "LONG":
            ax.annotate(
                "▲ ENTRY",
                xy=(x_pos, signal_price),
                xytext=(max(x_pos - 4, 0), signal_price - offset * 2),
                fontsize=8, color=color, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=color, lw=1.5),
            )
        else:
            ax.annotate(
                "▼ ENTRY",
                xy=(x_pos, signal_price),
                xytext=(max(x_pos - 4, 0), signal_price + offset * 2),
                fontsize=8, color=color, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=color, lw=1.5),
            )

        # Legend
        patches = [
            mpatches.Patch(color="#FFD700", label=f"Entry {_CUR}{signal_price:.2f}"),
            mpatches.Patch(color="#FF4444", label=f"SL {_CUR}{stop_loss:.2f}"),
            mpatches.Patch(color="#44FF88", label=f"T1 {_CUR}{target_1:.2f}"),
            mpatches.Patch(color="#00FFCC", label=f"T2 {_CUR}{target_2:.2f}"),
        ]
        ax.legend(
            handles=patches, loc="upper left", fontsize=7,
            facecolor="#1A1A2E", edgecolor="#444", labelcolor="white",
        )

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=140, bbox_inches="tight",
                    facecolor="#0D1117")
        plt.close(fig)
        buf.seek(0)
        return buf

    except Exception as e:
        logger.warning(f"Chart generation failed: {e}")
        return None


def _build_equity_curve(trades: List[Dict], capital: float) -> Optional[io.BytesIO]:
    """Build a daily equity curve PNG from completed trades list."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        if not trades:
            return None

        pnls       = [t.get("pnl", 0) for t in trades]
        labels     = [t.get("symbol", f"T{i+1}")[:6] for i, t in enumerate(trades)]
        cumulative = list(pd.Series(pnls).cumsum())

        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(12, 7),
            facecolor="#0D1117",
            gridspec_kw={"height_ratios": [2, 1]},
        )
        fig.patch.set_facecolor("#0D1117")

        # Equity curve
        x      = list(range(len(cumulative)))
        color  = "#00C853" if cumulative[-1] >= 0 else "#FF1744"
        ax1.plot(x, cumulative, color=color, linewidth=2.5, zorder=3)
        ax1.fill_between(x, cumulative, alpha=0.15, color=color)
        ax1.axhline(0, color="#555555", linestyle="--", lw=1)
        ax1.set_facecolor("#0D1117")
        ax1.set_title("Equity Curve — Today's Trades", color="white", fontsize=11)
        ax1.set_ylabel("Cumulative P&L ({_CUR})", color="#CCCCCC", fontsize=9)
        ax1.tick_params(colors="#AAAAAA")
        ax1.grid(True, color="#333333", linestyle=":", alpha=0.5)

        # Per-trade bar
        bar_colors = ["#00C853" if p >= 0 else "#FF1744" for p in pnls]
        ax2.bar(x, pnls, color=bar_colors, width=0.7)
        ax2.axhline(0, color="#555555", linestyle="--", lw=1)
        ax2.set_facecolor("#0D1117")
        ax2.set_ylabel("Trade P&L ({_CUR})", color="#CCCCCC", fontsize=9)
        ax2.tick_params(colors="#AAAAAA")
        ax2.set_xticks(x)
        ax2.set_xticklabels(labels, rotation=35, color="#AAAAAA", fontsize=7)
        ax2.grid(True, color="#333333", linestyle=":", alpha=0.5)

        plt.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                    facecolor="#0D1117")
        plt.close(fig)
        buf.seek(0)
        return buf

    except Exception as e:
        logger.warning(f"Equity curve chart failed: {e}")
        return None


# ============================================================
# TELEGRAM ALERTER
# ============================================================

class TelegramAlerter:
    """
    Rich Telegram alert engine with full IST timestamps.

    All public methods are synchronous — async sending is handled internally.
    Gracefully degrades if telegram or matplotlib are not installed.
    """

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id   = str(chat_id)
        self._bot      = None
        self._ready    = False
        self._init_bot()

    # --------------------------------------------------------
    # INIT
    # --------------------------------------------------------

    def _init_bot(self):
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram not configured (missing token/chat_id) — alerts disabled")
            return
        # Use direct HTTP instead of python-telegram-bot to avoid asyncio event-loop issues
        self._api_base = f"https://api.telegram.org/bot{self.bot_token}"
        self._ready = True
        logger.info(f"[{format_ist_timestamp()}] Telegram alerter ready (direct HTTP)")

    def initialize(self) -> bool:
        return self._ready

    def _is_ready(self) -> bool:
        return self._ready

    # --------------------------------------------------------
    # INTERNAL SEND
    # --------------------------------------------------------

    def _send(self, text: str, image_buf: Optional[io.BytesIO] = None,
              parse_mode: str = "Markdown") -> bool:
        """
        Send message via direct Telegram Bot HTTP API.
        No asyncio — pure requests, works from any thread/context.
        """
        if not self._ready:
            logger.debug("Telegram not ready — alert suppressed")
            return False
        try:
            if image_buf:
                image_buf.seek(0)
                resp = _requests.post(
                    f"{self._api_base}/sendPhoto",
                    data={
                        "chat_id":    self.chat_id,
                        "caption":    text[:1024],
                        "parse_mode": parse_mode,
                    },
                    files={"photo": ("chart.png", image_buf, "image/png")},
                    timeout=15,
                )
            else:
                resp = _requests.post(
                    f"{self._api_base}/sendMessage",
                    json={
                        "chat_id":    self.chat_id,
                        "text":       text[:4096],
                        "parse_mode": parse_mode,
                    },
                    timeout=15,
                )
            if not resp.ok:
                logger.warning(f"Telegram API {resp.status_code}: {resp.text[:200]}")
                return False
            return True
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    def send_text(self, text: str) -> bool:
        return self._send(text)

    def send_html(self, text: str) -> bool:
        """Send message with HTML parse mode (supports <b>, <i>, <code> tags)."""
        return self._send(text, parse_mode="HTML")

    # --------------------------------------------------------
    # SIGNAL ALERT  (core alert with chart)
    # --------------------------------------------------------

    def send_signal(self, signal, candles_df: Optional[pd.DataFrame] = None) -> bool:
        """
        Send a rich trade signal alert.
        `signal` is a TradeSignal dataclass from signal_generator.py
        """
        direction_emoji = E["long"] if signal.direction == "LONG" else E["short"]
        grade_emoji     = getattr(signal, "grade_emoji", E["target"])
        grade           = getattr(signal, "quality_grade", "B")
        size_mult       = getattr(signal, "size_multiplier", 1.0)
        qty             = getattr(signal, "quantity", 0)

        risk_amount = abs(signal.entry_price - signal.stop_loss) * qty
        proj_t1     = abs(signal.target_1 - signal.entry_price) * qty
        proj_t2     = abs(signal.target_2 - signal.entry_price) * qty
        patterns_str = ", ".join(signal.patterns[:5]) if signal.patterns else "—"
        mtf          = signal.timeframe_alignment or {}
        mtf_str      = " | ".join(f"{tf}: {v}" for tf, v in list(mtf.items())[:3]) if mtf else "—"
        rationale    = textwrap.shorten(
            signal.rationale or "Signal confirmed by multi-timeframe momentum",
            350, placeholder="...",
        )

        text = (
            f"{direction_emoji} *{signal.direction} SIGNAL — {signal.symbol}*\n"
            f"{grade_emoji} Grade: `{grade}` | Score: `{signal.signal_score:.0f}/100` | Size: `{size_mult:.1f}x`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{E['target']} *Entry:* `{_CUR}{signal.entry_price:.2f}`\n"
            f"🛑 *Stop Loss:* `{_CUR}{signal.stop_loss:.2f}`\n"
            f"🎯 *Target 1:* `{_CUR}{signal.target_1:.2f}`\n"
            f"🎯 *Target 2:* `{_CUR}{signal.target_2:.2f}`\n"
            f"📐 *R:R:* `{signal.risk_reward:.1f}:1` | Qty: `{qty}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{E['money']} Risk: `{_CUR}{risk_amount:,.0f}` | T1 P&L: `{_CUR}{proj_t1:,.0f}` | T2: `{_CUR}{proj_t2:,.0f}`\n"
            f"📈 Patterns: `{patterns_str}`\n"
            f"⏱ MTF: `{mtf_str}`\n"
            f"📰 News: `{'Clear' if signal.news_clear else 'BLOCKED — event nearby'}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{E['brain']} *AI Rationale:*\n_{rationale}_\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{E['clock']} `{signal.signal_time or format_ist_timestamp()}`"
        )

        chart = None
        if candles_df is not None and not candles_df.empty:
            chart = _build_candle_chart(
                candles_df, signal.entry_price, signal.stop_loss,
                signal.target_1, signal.target_2,
                signal.direction, signal.symbol, grade,
            )

        return self._send(text, chart)

    # --------------------------------------------------------
    # TRADE FILL
    # --------------------------------------------------------

    def send_trade_fill(self, symbol: str, direction: str, qty: int,
                        price: float, order_id: str = "") -> bool:
        emoji = E["long"] if direction == "LONG" else E["short"]
        text = (
            f"{emoji} *ORDER FILLED — {symbol}*\n"
            f"Direction: `{direction}` | Qty: `{qty}` | Price: `{_CUR}{price:.2f}`\n"
            f"Order ID: `{order_id}`\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # EXIT ALERT (Profit / Loss)
    # --------------------------------------------------------

    def send_exit(self, symbol: str, direction: str, qty: int,
                  entry: float, exit_price: float, pnl: float,
                  reason: str = "Target", order_id: str = "") -> bool:
        pnl_emoji = E["profit"] if pnl >= 0 else E["loss"]
        pnl_str   = f"+{_CUR}{pnl:,.0f}" if pnl >= 0 else f"-{_CUR}{abs(pnl):,.0f}"
        pct = ((exit_price - entry) / entry * 100) if direction == "LONG" else ((entry - exit_price) / entry * 100)
        text = (
            f"{pnl_emoji} *EXIT — {symbol}*\n"
            f"Direction: `{direction}` | Qty: `{qty}`\n"
            f"Entry: `{_CUR}{entry:.2f}` → Exit: `{_CUR}{exit_price:.2f}` (`{pct:+.2f}%`)\n"
            f"P&L: *{pnl_str}*\n"
            f"Reason: `{reason}`\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # STOP-LOSS HIT
    # --------------------------------------------------------

    def send_sl_hit(self, symbol: str, direction: str, entry: float,
                    sl_price: float, loss: float) -> bool:
        text = (
            f"{E['loss']} *STOP-LOSS HIT — {symbol}*\n"
            f"Direction: `{direction}` | Entry: `{_CUR}{entry:.2f}` | SL: `{_CUR}{sl_price:.2f}`\n"
            f"Loss: `{_CUR}{abs(loss):,.0f}`\n"
            f"{E['warn']} Reviewing consecutive losses...\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # CIRCUIT BREAKER / KILL
    # --------------------------------------------------------

    def send_circuit_breaker(self, reason: str) -> bool:
        text = (
            f"{E['kill']} *CIRCUIT BREAKER TRIGGERED*\n"
            f"Reason: `{reason}`\n"
            f"All new entries PAUSED. Existing positions being monitored.\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    def send_kill_alert(self) -> bool:
        text = (
            f"{E['kill']} *KILL SWITCH ACTIVATED*\n"
            f"Emergency stop received. ALL positions being squared off.\n"
            f"New trading HALTED for the day.\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # STATUS REPORT
    # --------------------------------------------------------

    def send_status(self, risk_manager=None, balance_available: float = None,
                    margin_used: float = None) -> bool:
        if risk_manager is None:
            return self._send(
                f"{E['chart']} *Bot Status*\n"
                f"`{format_ist_timestamp()}`\nNo risk data available."
            )

        state     = getattr(risk_manager, "state", None)
        positions = state.positions      if state else {}
        daily_pnl = state.daily_pnl     if state else 0
        capital   = state.daily_capital if state else 0
        n_trades  = state.daily_trades  if state else 0
        wins      = state.winning_trades if state else 0
        wr_pct    = (wins / n_trades * 100) if n_trades > 0 else 0.0
        paused    = state.trading_paused if state else False

        pnl_emoji = E["profit"] if daily_pnl >= 0 else E["loss"]
        pnl_str   = f"+{_CUR}{daily_pnl:,.0f}" if daily_pnl >= 0 else f"-{_CUR}{abs(daily_pnl):,.0f}"

        lines = [
            f"{E['chart']} *Bot Status — {format_ist_timestamp()}*",
        ]
        if balance_available is not None:
            bal_line = f"💰 Balance: `{_CUR}{balance_available:,.2f}`"
            if margin_used:
                bal_line += f" | Margin Used: `{_CUR}{margin_used:,.2f}`"
            lines.append(bal_line)
        lines += [
            f"Daily P&L: {pnl_emoji} *{pnl_str}*",
            f"Capital: `{_CUR}{capital:,.0f}` | Trades: `{n_trades}` | Win Rate: `{wr_pct:.1f}%`",
            f"Open Positions: `{len(positions)}` | State: `{'PAUSED' if paused else 'ACTIVE'}`",
        ]
        if positions:
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            for sym, pos in list(positions.items())[:8]:
                p_pnl  = getattr(pos, "pnl", 0)
                p_emj  = "🟢" if p_pnl >= 0 else "🔴"
                cur    = getattr(pos, "current_price", pos.entry_price)
                lines.append(
                    f"{p_emj} `{sym}` {pos.direction} "
                    f"{_CUR}{pos.entry_price:.2f}→{_CUR}{cur:.2f} | "
                    f"P&L: `{'+' if p_pnl>=0 else ''}{p_pnl:,.0f}`"
                )

        return self._send("\n".join(lines))

    # --------------------------------------------------------
    # PAUSE / RESUME
    # --------------------------------------------------------

    def send_pause(self, reason: str = "") -> bool:
        text = (
            f"{E['pause']} *Trading PAUSED*\n"
            f"{'Reason: ' + reason if reason else 'Manual pause.'}\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    def send_resume(self) -> bool:
        return self._send(
            f"{E['resume']} *Trading RESUMED*\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )

    # --------------------------------------------------------
    # SQUARE-OFF WARNING
    # --------------------------------------------------------

    def send_squareoff_warning(self, positions) -> bool:
        n = len(positions) if hasattr(positions, "__len__") else 0
        text = (
            f"{E['warn']} *SQUARE-OFF WARNING — 3:50 PM ET*\n"
            f"`{n}` open position(s) will be force-closed at market price.\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # MORNING BRIEF
    # --------------------------------------------------------

    def send_morning_brief(
        self,
        brief_or_watchlist=None,
        available: float = 0,
        nifty_open: float = 0,
        oc_summary: str = "",
        fii_summary: str = "",
    ) -> bool:
        """
        Backward-compatible morning brief.

        Can be called two ways:
          1. send_morning_brief(brief_dict)             — new style (dict from overnight_analyzer)
          2. send_morning_brief(watchlist, avail, nifty) — legacy style from main.py
        """
        # Detect calling style
        if isinstance(brief_or_watchlist, dict):
            brief      = brief_or_watchlist
            bias       = brief.get("day_bias", "NEUTRAL")
            bias_score = brief.get("bias_score", 0)
            vix_data   = brief.get("vix", {})
            vix        = vix_data.get("vix", 0) if isinstance(vix_data, dict) else 0
            spy_data   = brief.get("spy_gap", {})
            gap_pct    = spy_data.get("gap_pct", 0) if isinstance(spy_data, dict) else 0
            risks      = brief.get("key_risks", [])
            ai_thesis  = brief.get("ai_thesis", "")
            watchlist  = brief.get("top_watchlist", [])
            avail_cap  = available or brief.get("available_capital", 0)
            nifty_ltp  = nifty_open or brief.get("spy_open", 0)
        else:
            # Legacy: send_morning_brief(watchlist_list, available_float, nifty_open_float)
            watchlist  = brief_or_watchlist or []
            avail_cap  = available
            nifty_ltp  = nifty_open
            bias, bias_score, vix, gap_pct = "NEUTRAL", 0, 15.0, 0.0
            risks, ai_thesis = [], ""

        bias_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(bias, "🟡")
        wl_str     = ", ".join(f"`{s}`" for s in watchlist[:10]) if watchlist else "—"
        risks_str  = "\n".join(f"  • {r}" for r in risks[:4]) if risks else "  No high-impact events"
        thesis     = textwrap.shorten(ai_thesis or "Scanning for momentum setups...", 350, placeholder="...")

        text = (
            f"{E['rocket']} *MORNING BRIEF — {format_ist_timestamp()}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{bias_emoji} Day Bias: *{bias}* (score: `{bias_score:+d}`)\n"
            f"VIX: `{vix:.1f}` | SPY Gap: `{gap_pct:+.2f}%` | "
            f"SPY: `{_CUR}{nifty_ltp:,.2f}` | Capital: `{_CUR}{avail_cap:,.0f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"*Key Risks:*\n{risks_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"*Watchlist ({len(watchlist)} stocks):* {wl_str}\n"
        )
        if oc_summary:
            text += f"━━━━━━━━━━━━━━━━━━━━\n{oc_summary}\n"
        if fii_summary:
            text += f"━━━━━━━━━━━━━━━━━━━━\n{fii_summary}\n"
        if ai_thesis:
            text += f"━━━━━━━━━━━━━━━━━━━━\n{E['brain']} *AI Thesis:* _{thesis}_"
        return self._send(text)

    # --------------------------------------------------------
    # ENTRY ALERT (alias for send_signal — used by main.py)
    # --------------------------------------------------------

    def send_entry_alert(self, signal, candles_df=None) -> bool:
        """Alias for send_signal() — called by main.py after order placement."""
        return self.send_signal(signal, candles_df)

    # --------------------------------------------------------
    # EXIT ALERT — positional signature used by main.py
    # --------------------------------------------------------

    def send_exit_alert(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        quantity: int,
        pnl: float,
        reason: str = "Target",
        order_id: str = "",
    ) -> bool:
        """
        Exit alert called by main.py after a position is closed.
        Signature: (symbol, direction, entry_price, exit_price, qty, pnl, reason)
        Delegates to send_exit() which has qty before entry in its signature.
        """
        return self.send_exit(
            symbol=symbol,
            direction=direction,
            qty=quantity,
            entry=entry_price,
            exit_price=exit_price,
            pnl=pnl,
            reason=reason,
            order_id=order_id,
        )

    # --------------------------------------------------------
    # EOD PERFORMANCE REPORT
    # --------------------------------------------------------

    def send_eod_report(self, risk_manager=None,
                        trades: Optional[List[Dict]] = None,
                        capital: float = 0) -> bool:
        state      = getattr(risk_manager, "state", None) if risk_manager else None
        all_trades = trades or []
        daily_pnl  = state.daily_pnl      if state else sum(t.get("pnl", 0) for t in all_trades)
        n_trades   = state.daily_trades   if state else len(all_trades)
        wins       = state.winning_trades if state else sum(1 for t in all_trades if t.get("pnl", 0) > 0)
        losses     = state.losing_trades  if state else sum(1 for t in all_trades if t.get("pnl", 0) < 0)
        wr_pct     = (wins / n_trades * 100) if n_trades > 0 else 0.0
        pnl_pct    = (daily_pnl / capital * 100) if capital > 0 else 0.0

        pnl_emoji  = E["trophy"] if daily_pnl > 0 else (E["loss"] if daily_pnl < 0 else E["chart"])
        pnl_str    = (f"+{_CUR}{daily_pnl:,.0f} (+{pnl_pct:.2f}%)"
                      if daily_pnl >= 0
                      else f"-{_CUR}{abs(daily_pnl):,.0f} ({pnl_pct:.2f}%)")

        best  = max(all_trades, key=lambda t: t.get("pnl", 0), default=None)
        worst = min(all_trades, key=lambda t: t.get("pnl", 0), default=None)
        best_str  = f"{best.get('symbol','?')} `+{_CUR}{best.get('pnl',0):,.0f}`"   if best  else "—"
        worst_str = f"{worst.get('symbol','?')} `-{_CUR}{abs(worst.get('pnl',0)):,.0f}`" if worst else "—"

        # Daily target slice = 5% monthly ÷ ~22 trading days
        target_hit = (daily_pnl >= capital * 0.05 / 22) if capital > 0 else False

        text = (
            f"{pnl_emoji} *END-OF-DAY REPORT — {format_ist_timestamp()}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Net P&L: *{pnl_str}*\n"
            f"Trades: `{n_trades}` | Wins: `{wins}` | Losses: `{losses}`\n"
            f"Win Rate: `{wr_pct:.1f}%`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{E['fire']} Best Trade:  {best_str}\n"
            f"{E['loss']} Worst Trade: {worst_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{'Daily target achieved!' if target_hit else 'Below daily target slice (5%/mo)'}\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )

        chart = _build_equity_curve(all_trades, capital) if all_trades else None
        return self._send(text, chart)

    # --------------------------------------------------------
    # TOKEN REFRESH
    # --------------------------------------------------------

    def send_token_refresh(self, success: bool, method: str = "TOTP") -> bool:
        emoji  = E["profit"] if success else E["loss"]
        status = "succeeded" if success else "FAILED — using previous token"
        text = (
            f"{emoji} *Alpaca Auth {status.split()[0].capitalize()}*\n"
            f"Status: `{status}`\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # WATCHLIST UPDATE
    # --------------------------------------------------------

    def send_watchlist(self, symbols: List[str]) -> bool:
        wl = ", ".join(f"`{s}`" for s in symbols[:20])
        text = (
            f"{E['pin']} *Watchlist Updated*\n"
            f"Scanning `{len(symbols)}` stocks:\n{wl}\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # AI INSIGHT
    # --------------------------------------------------------

    def send_ai_insight(self, symbol: str, insight: str,
                        trade_review: str = "") -> bool:
        text = (
            f"{E['brain']} *AI Insight — {symbol}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"_{textwrap.shorten(insight, 600, placeholder='...')}_\n"
        )
        if trade_review:
            text += (
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"*Trade Review:*\n"
                f"_{textwrap.shorten(trade_review, 400, placeholder='...')}_\n"
            )
        text += f"{E['clock']} `{format_ist_timestamp()}`"
        return self._send(text)

    # --------------------------------------------------------
    # GENERIC ALERT
    # --------------------------------------------------------

    def send_alert(self, title: str, body: str, level: str = "INFO") -> bool:
        level_emoji = {
            "INFO":    "ℹ️",
            "WARNING": E["warn"],
            "ERROR":   "🚨",
            "SUCCESS": E["profit"],
        }.get(level, "ℹ️")
        text = (
            f"{level_emoji} *{title}*\n"
            f"{body}\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # OPTIONS ALERTS
    # --------------------------------------------------------

    def send_options_entry_alert(self, pos) -> bool:
        """Alert for new options position entry."""
        try:
            direction_emoji = "📈" if "CALL" in pos.direction else "📉"
            text = (
                f"{direction_emoji} *OPTIONS ENTRY*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Underlying: `{pos.underlying}`\n"
                f"Contract:   `{pos.opt_symbol}`\n"
                f"Type:       `{pos.direction}`\n"
                f"Qty:        `{pos.contracts}` contract(s)\n"
                f"Premium:    `{_CUR}{pos.entry_premium:.2f}` per contract\n"
                f"Total Cost: `{_CUR}{pos.entry_premium * pos.contracts * 100:.0f}`\n"
                f"DTE:        `{pos.dte}` day(s)\n"
                f"Stop:       `{_CUR}{pos.entry_premium * 0.55:.2f}` (−45%)\n"
                f"Target 1:   `{_CUR}{pos.entry_premium * 1.80:.2f}` (+80%)\n"
                f"Target 2:   `{_CUR}{pos.entry_premium * 2.50:.2f}` (+150%)\n"
                f"{E['clock']} `{format_ist_timestamp()}`"
            )
            return self._send(text)
        except Exception as e:
            logger.debug(f"send_options_entry_alert: {e}")
            return False

    def send_options_exit_alert(self, pos, exit_premium: float, reason: str) -> bool:
        """Alert for options position exit with P&L."""
        try:
            if pos.entry_premium > 0:
                pnl_pct = (exit_premium - pos.entry_premium) / pos.entry_premium * 100
            else:
                pnl_pct = 0.0
            pnl_usd = (exit_premium - pos.entry_premium) * pos.contracts * 100
            emoji = E["profit"] if pnl_usd >= 0 else E["loss"]
            text = (
                f"{emoji} *OPTIONS EXIT — {reason}*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Contract:  `{pos.opt_symbol}`\n"
                f"Entry:     `{_CUR}{pos.entry_premium:.2f}`\n"
                f"Exit:      `{_CUR}{exit_premium:.2f}`\n"
                f"P&L:       `{_CUR}{pnl_usd:+.0f}` ({pnl_pct:+.1f}%)\n"
                f"Contracts: `{pos.contracts}×100`\n"
                f"{E['clock']} `{format_ist_timestamp()}`"
            )
            return self._send(text)
        except Exception as e:
            logger.debug(f"send_options_exit_alert: {e}")
            return False

    def send_options_uoa_alert(self, uoa_list: list) -> bool:
        """Alert for unusual options activity detected."""
        if not uoa_list:
            return False
        try:
            lines = ["🔍 *Unusual Options Activity*\n━━━━━━━━━━━━━━━━━━━━"]
            for u in uoa_list[:5]:
                lines.append(
                    f"• `{u['symbol']}` {u['type'].upper()} "
                    f"strike={_CUR}{u['strike']:.0f} "
                    f"vol/OI={u['vol_oi']:.1f}× "
                    f"dte={u['dte']}d"
                )
            lines.append(f"{E['clock']} `{format_ist_timestamp()}`")
            return self._send("\n".join(lines))
        except Exception as e:
            logger.debug(f"send_options_uoa_alert: {e}")
            return False

    def send_capital_projection(self, capital: float = 500.0) -> bool:
        """Send $500 (or custom) account P&L projection via capital_calculator."""
        try:
            from capital_calculator import get_telegram_summary
            text = get_telegram_summary(capital)
            return self._send(text)
        except Exception as e:
            logger.debug(f"send_capital_projection: {e}")
            return False
