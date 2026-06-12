"""
alerts_telegram.py — SataVector Bloomberg Terminal Alert Engine
Professional-grade Telegram alerts modelled on Bloomberg / institutional trading desk output.

Features:
  • Bloomberg-style data-dense message formatting
  • Candlestick chart images (mplfinance) with entry arrow + SL/TP levels
  • Gemini AI signal explanation embedded in entry alerts
  • Full trade lifecycle: signal → fill → exit → SL → EOD report
  • Circuit breaker / kill-switch alerts with immediate action guidance
  • Live portfolio status (/status command)
  • Morning market brief with market context
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
except Exception as _e:
    print(f"[suppressed] {_e}")
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
    "signal": "⚡",
    "lock":   "🔒",
    "stop":   "🛑",
}

# Bloomberg separator line
_SEP = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"


# ============================================================
# BLOOMBERG FORMATTING HELPERS
# ============================================================

def _sep() -> str:
    return _SEP

def _score_bar(score: float) -> str:
    """Return a 10-char filled/empty block bar: ████████░░ for score 80/100."""
    filled = max(0, min(10, int(round(score / 10))))
    return "█" * filled + "░" * (10 - filled)

def _dir_arrow(pct: float) -> str:
    """Return ▲ for positive, ▼ for negative percentage."""
    return "▲" if pct >= 0 else "▼"

def _signed(val: float, cur: str = "") -> str:
    """Format a value with explicit +/- sign."""
    if val >= 0:
        return f"+{cur}{val:,.2f}"
    return f"-{cur}{abs(val):,.2f}"

def _pnl_str(pnl: float, capital: float = 0) -> str:
    """Format P&L with sign, optional percentage."""
    sign = "+" if pnl >= 0 else "-"
    s = f"{sign}{_CUR}{abs(pnl):,.0f}"
    if capital > 0:
        pct = abs(pnl) / capital * 100
        s += f"  ({sign}{pct:.2f}%)"
    return s


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

        df = candles.copy().tail(60)
        df.index = pd.DatetimeIndex(df.index)
        df.columns = [c.capitalize() for c in df.columns]

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

        x      = list(range(len(cumulative)))
        color  = "#00C853" if cumulative[-1] >= 0 else "#FF1744"
        ax1.plot(x, cumulative, color=color, linewidth=2.5, zorder=3)
        ax1.fill_between(x, cumulative, alpha=0.15, color=color)
        ax1.axhline(0, color="#555555", linestyle="--", lw=1)
        ax1.set_facecolor("#0D1117")
        ax1.set_title("SataVector — Equity Curve", color="white", fontsize=11)
        ax1.set_ylabel(f"Cumulative P&L ({_CUR})", color="#CCCCCC", fontsize=9)
        ax1.tick_params(colors="#AAAAAA")
        ax1.grid(True, color="#333333", linestyle=":", alpha=0.5)

        bar_colors = ["#00C853" if p >= 0 else "#FF1744" for p in pnls]
        ax2.bar(x, pnls, color=bar_colors, width=0.7)
        ax2.axhline(0, color="#555555", linestyle="--", lw=1)
        ax2.set_facecolor("#0D1117")
        ax2.set_ylabel(f"Trade P&L ({_CUR})", color="#CCCCCC", fontsize=9)
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
# BLOOMBERG TERMINAL ALERTER
# ============================================================

class TelegramAlerter:
    """
    Bloomberg Terminal-grade Telegram alert engine.

    All public methods are synchronous — async sending is handled internally.
    Gracefully degrades if telegram or matplotlib are not installed.
    """

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token    = bot_token
        self.chat_id      = str(chat_id)
        self._bot         = None
        self._ready       = False
        self._risk_manager = None   # set via set_risk_manager() after bot init
        self._init_bot()

    def set_risk_manager(self, rm) -> None:
        """Link risk manager so exit/entry alerts can include live win-rate stats."""
        self._risk_manager = rm

    def _live_stats(self) -> tuple:
        """Returns (n_trades, wins, losses, wr_pct, daily_pnl) from risk manager."""
        try:
            rm = self._risk_manager
            if rm is None:
                return 0, 0, 0, 0.0, 0.0
            state = getattr(rm, "state", None)
            if state is None:
                return 0, 0, 0, 0.0, 0.0
            n      = state.daily_trades
            wins   = state.winning_trades
            losses = state.losing_trades
            wr     = (wins / n * 100) if n > 0 else 0.0
            pnl    = state.daily_pnl
            return n, wins, losses, wr, pnl
        except Exception:
            return 0, 0, 0, 0.0, 0.0

    def _live_stats_line(self) -> str:
        n, wins, losses, wr, pnl = self._live_stats()
        pnl_s = f"+{_CUR}{pnl:,.0f}" if pnl >= 0 else f"-{_CUR}{abs(pnl):,.0f}"
        if n == 0:
            return f"📊 Session: `0 trades` | WR: `—` | P&L: `{_CUR}0`"
        return (
            f"📊 Session: *{n}* trades | W/L: `{wins}/{losses}` | "
            f"WR: `{wr:.0f}%` | P&L: *{pnl_s}*"
        )

    # --------------------------------------------------------
    # INIT
    # --------------------------------------------------------

    def _init_bot(self):
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram not configured (missing token/chat_id) — alerts disabled")
            return
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
        """Send message via direct Telegram Bot HTTP API (synchronous)."""
        if not self._ready:
            logger.debug("Telegram not ready — alert suppressed")
            return False
        try:
            if image_buf:
                image_buf.seek(0)
                post_data: dict = {"chat_id": self.chat_id, "caption": text[:1024]}
                if parse_mode:
                    post_data["parse_mode"] = parse_mode
                resp = _requests.post(
                    f"{self._api_base}/sendPhoto",
                    data=post_data,
                    files={"photo": ("chart.png", image_buf, "image/png")},
                    timeout=15,
                )
            else:
                msg_data: dict = {"chat_id": self.chat_id, "text": text[:4096]}
                if parse_mode:
                    msg_data["parse_mode"] = parse_mode
                resp = _requests.post(
                    f"{self._api_base}/sendMessage",
                    json=msg_data,
                    timeout=15,
                )
            if not resp.ok:
                # Retry as plain text when Telegram rejects our formatting
                # (e.g. dynamic content like "<" in rejection reasons breaks HTML mode)
                if resp.status_code == 400 and "parse entities" in resp.text and parse_mode:
                    import re as _re
                    plain = _re.sub(r'<[^>]+>', '', text)  # strip HTML tags
                    logger.debug("Telegram parse-entities error — retrying as plain text")
                    return self._send(plain, image_buf, parse_mode="")
                logger.warning(f"Telegram API {resp.status_code}: {resp.text[:200]}")
                return False
            return True
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    def send_text(self, text: str) -> bool:
        return self._send(text, parse_mode="HTML")

    def send_html(self, text: str) -> bool:
        """Send message with HTML parse mode (supports <b>, <i>, <code> tags)."""
        return self._send(text, parse_mode="HTML")

    # --------------------------------------------------------
    # SIGNAL ALERT  (Bloomberg style — with chart)
    # --------------------------------------------------------

    def send_signal(self, signal, candles_df: Optional[pd.DataFrame] = None) -> bool:
        """Send a Bloomberg-style trade signal alert with chart."""
        direction_emoji = E["long"] if signal.direction == "LONG" else E["short"]
        grade           = getattr(signal, "quality_grade", "B")
        size_mult       = getattr(signal, "size_multiplier", 1.0)
        qty             = getattr(signal, "quantity", 0)
        is_grand_slam   = getattr(signal, "grand_slam", False) or (size_mult >= 1.9)

        risk_amount = abs(signal.entry_price - signal.stop_loss) * qty
        proj_t1     = abs(signal.target_1 - signal.entry_price) * qty
        proj_t2     = abs(signal.target_2 - signal.entry_price) * qty
        rr          = signal.risk_reward if hasattr(signal, "risk_reward") and signal.risk_reward else 0
        patterns_str = ", ".join(signal.patterns[:3]) if signal.patterns else "—"
        score        = getattr(signal, "signal_score", 0)
        mtf          = signal.timeframe_alignment or {}
        mtf_ok       = all(v in ("BULLISH", "BEARISH", "ALIGNED", True) or (isinstance(v, (int, float)) and v > 50)
                           for v in list(mtf.values())[:3]) if mtf else False
        rationale    = textwrap.shorten(
            signal.rationale or "Multi-timeframe momentum confirmed",
            300, placeholder="...",
        )

        slam_line = "  🏆 *GRAND SLAM* — 2× size\n" if is_grand_slam else ""
        mtf_icon  = "✅" if mtf_ok else "⚠️"

        stats_line = self._live_stats_line()
        text = (
            f"{direction_emoji} *{signal.direction} SIGNAL — {signal.symbol}*\n"
            f"{slam_line}"
            f"{_sep()}\n"
            f"  *GRADE: {grade}* | Score: `{score:.0f}/100` `{_score_bar(score)}`\n"
            f"  Pattern: `{patterns_str}`\n"
            f"{_sep()}\n"
            f"  ENTRY  `{_CUR}{signal.entry_price:.2f}`\n"
            f"  SIZE   `{qty} shrs`  ×  `{size_mult:.1f}×`\n"
            f"  RISK   `{_CUR}{risk_amount:,.0f}`\n"
            f"{_sep()}\n"
            f"  SL  →  `{_CUR}{signal.stop_loss:.2f}`   hard stop\n"
            f"  T1  →  `{_CUR}{signal.target_1:.2f}`   +1.5×ATR  [40%]\n"
            f"  T2  →  `{_CUR}{signal.target_2:.2f}`   +3.5×ATR  [20%]\n"
            f"  R:R    `{rr:.1f} : 1`\n"
            f"{_sep()}\n"
            f"  MTF    {mtf_icon} 5m · 15m · 1h\n"
            f"  NEWS   `{'Clear ✅' if signal.news_clear else 'BLOCKED ⚠️'}`\n"
            f"{_sep()}\n"
            f"  _{rationale}_\n"
            f"{_sep()}\n"
            f"  {stats_line}\n"
            f"  {E['clock']} `{signal.signal_time or format_ist_timestamp()}`"
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
    # TRADE FILL  (Bloomberg ORDER EXECUTED)
    # --------------------------------------------------------

    def send_trade_fill(self, symbol: str, direction: str, qty: int,
                        price: float, order_id: str = "",
                        stop_loss: float = 0.0, target_1: float = 0.0,
                        target_2: float = 0.0, signal_score: float = 0.0,
                        quality_grade: str = "") -> bool:
        emoji    = E["signal"] if direction == "LONG" else E["short"]
        dir_word = "LONG ENTRY" if direction == "LONG" else "SHORT ENTRY"
        notional = price * qty
        risk_amt = abs(price - stop_loss) * qty if stop_loss > 0 else 0.0
        risk_pct = 0.0
        try:
            import config as _c
            cap = getattr(_c, "MAX_DAILY_CAPITAL", 0)
            risk_pct = (risk_amt / cap * 100) if cap > 0 else 0.0
        except Exception:
            pass

        grade_line = f"  GRADE  <code>{quality_grade}</code>" if quality_grade else ""
        score_line = f"  SCORE  <code>{signal_score:.0f}/100</code>  <code>{_score_bar(signal_score)}</code>" if signal_score > 0 else ""
        sl_line    = f"  SL  →  <code>{_CUR}{stop_loss:.2f}</code>   hard stop" if stop_loss > 0 else ""
        t1_line    = f"  T1  →  <code>{_CUR}{target_1:.2f}</code>   [40% exit]" if target_1 > 0 else ""
        t2_line    = f"  T2  →  <code>{_CUR}{target_2:.2f}</code>   [20% exit]" if target_2 > 0 else ""
        levels     = "\n".join(x for x in [sl_line, t1_line, t2_line] if x)
        stats      = self._live_stats_line()

        text = (
            f"👑 <b>SATAVECTOR</b> — lakshaytrades\n"
            f"{emoji} <b>ORDER EXECUTED — {dir_word}</b>\n"
            f"{_sep()}\n"
            f"  <b>{symbol}</b>  |  <code>{direction}</code>\n"
            f"{_sep()}\n"
            f"  ENTRY    <code>{_CUR}{price:.2f}</code>     {format_ist_timestamp()}\n"
            f"  SIZE     <code>{qty} shrs</code>    <code>{_CUR}{notional:,.0f}</code> notional\n"
            + (f"  RISK     <code>{_CUR}{risk_amt:,.0f}</code>     <code>{risk_pct:.2f}%</code> capital\n" if risk_amt > 0 else "")
            + (f"{_sep()}\n{grade_line}\n{score_line}\n" if (grade_line or score_line) else "")
            + (f"{_sep()}\n{levels}\n" if levels else "")
            + (f"{_sep()}\n{stats}\n" if stats else "")
            + f"{_sep()}\n"
            f"  {E['clock']} <code>{format_ist_timestamp()}</code>"
        )
        return self._send(text, parse_mode="HTML")

    # --------------------------------------------------------
    # EXIT ALERT  (Bloomberg POSITION CLOSED)
    # --------------------------------------------------------

    def send_exit(self, symbol: str, direction: str, qty: int,
                  entry: float, exit_price: float, pnl: float,
                  reason: str = "Target", order_id: str = "") -> bool:
        pnl_emoji = E["profit"] if pnl >= 0 else E["loss"]
        pct = ((exit_price - entry) / entry * 100) if direction == "LONG" else ((entry - exit_price) / entry * 100)
        n, wins, losses, wr, day_pnl = self._live_stats()
        day_pnl_str = _pnl_str(day_pnl)
        target_hit = False
        target_pct = 1.0
        capital    = 0.0
        try:
            import config as _c
            capital    = getattr(_c, "MAX_DAILY_CAPITAL", 0)
            target_pct = getattr(_c, "DAILY_PROFIT_TARGET_PCT", 1.0)
            target_hit = day_pnl >= capital * target_pct / 100 if capital > 0 else False
        except Exception:
            pass

        target_line = f"\n  {E['target']} <b>DAILY {target_pct:.1f}% TARGET: ACHIEVED</b> ✅" if target_hit else ""
        reason_clean = reason.replace("_", " ").upper()

        text = (
            f"👑 <b>SATAVECTOR</b> — lakshaytrades\n"
            f"{pnl_emoji} <b>POSITION CLOSED — {reason_clean}</b>\n"
            f"{_sep()}\n"
            f"  <b>{symbol}</b>  |  <code>{direction}</code>  |  <code>{reason_clean}</code>\n"
            f"{_sep()}\n"
            f"  ENTRY    <code>{_CUR}{entry:.2f}</code>\n"
            f"  EXIT     <code>{_CUR}{exit_price:.2f}</code>   <code>{pct:+.2f}%</code>\n"
            f"  QTY      <code>{qty} shrs</code>\n"
            f"{_sep()}\n"
            f"  NET P&L  <b>{_pnl_str(pnl)}</b>\n"
            f"{_sep()}\n"
            f"  DAY P&L  <code>{day_pnl_str}</code>\n"
            f"  TRADES   <code>{n}</code> today   <code>{wins}W / {losses}L</code>\n"
            f"  WIN RATE <code>{wr:.1f}%</code>"
            f"{target_line}\n"
            f"{_sep()}\n"
            f"  {E['clock']} <code>{format_ist_timestamp()}</code>"
        )
        return self._send(text, parse_mode="HTML")

    # --------------------------------------------------------
    # STOP-LOSS HIT  (Bloomberg STOP TRIGGERED)
    # --------------------------------------------------------

    def send_sl_hit(self, symbol: str, direction: str, entry: float,
                    sl_price: float, loss: float) -> bool:
        n, wins, losses, wr, day_pnl = self._live_stats()
        consec = 0
        try:
            import config as _c
            cap   = getattr(_c, "MAX_DAILY_CAPITAL", 8000)
            limit = getattr(_c, "CONSECUTIVE_LOSS_LIMIT", 3)
            rm    = self._risk_manager
            if rm:
                state  = getattr(rm, "state", None)
                consec = getattr(state, "consecutive_losses", 0) if state else 0
        except Exception:
            cap, limit, consec = 8000, 3, 0

        loss_pct  = abs(loss) / cap * 100 if cap > 0 else 0
        size_next = "35% — anti-martingale" if consec >= 2 else "100% — normal"
        day_pnl_str = _pnl_str(day_pnl)

        text = (
            f"{E['stop']} *STOP LOSS HIT*\n"
            f"{_sep()}\n"
            f"  *{symbol}*  |  `{direction}`  |  SL TRIGGERED\n"
            f"{_sep()}\n"
            f"  ENTRY    `{_CUR}{entry:.2f}`\n"
            f"  SL HIT   `{_CUR}{sl_price:.2f}`\n"
            f"{_sep()}\n"
            f"  LOSS     `{_CUR}{abs(loss):,.0f}`   `{loss_pct:.2f}%` capital\n"
            f"{_sep()}\n"
            f"  DAY P&L  `{day_pnl_str}`\n"
            f"  CONSEC   `{consec} / {limit}` loss limit\n"
            f"  NEXT SZ  `{size_next}`\n"
            f"{_sep()}\n"
            f"  {E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # TRAILING STOP UPDATE  (Bloomberg TRAIL MOVE)
    # --------------------------------------------------------

    def send_trailing_stop_update(
        self,
        symbol: str,
        old_sl: float,
        new_sl: float,
        current_price: float,
        stage: str = "BREAKEVEN",
        risk_before: float = 0.0,
        risk_after: float = 0.0,
    ) -> bool:
        """Bloomberg-style trailing stop / breakeven lock alert."""
        if stage == "BREAKEVEN":
            title = "BREAKEVEN LOCK ACTIVATED"
            note  = "Risk on trade: FREE TRADE ✅"
        elif stage == "T1_LOCK":
            title = "T1 PROFIT LOCK ACTIVATED"
            note  = f"Locking in partial profit — SL above entry"
        else:
            title = f"TRAIL STOP MOVED — {stage}"
            note  = ""

        text = (
            f"{E['lock']} *TRAILING STOP — {symbol}*\n"
            f"{_sep()}\n"
            f"  *{title}*\n"
            f"{_sep()}\n"
            f"  PRICE    `{_CUR}{current_price:.2f}`\n"
            f"  SL OLD   `{_CUR}{old_sl:.2f}`\n"
            f"  SL NEW   `{_CUR}{new_sl:.2f}`  ✅\n"
            + (f"  RISK     `{_CUR}{risk_before:,.0f}` → `{_CUR}{risk_after:,.0f}`\n" if risk_before > 0 else "")
            + f"{_sep()}\n"
            f"  {note}\n"
            f"  {E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # CIRCUIT BREAKER / KILL
    # --------------------------------------------------------

    def send_circuit_breaker(self, reason: str) -> bool:
        text = (
            f"{E['kill']} *CIRCUIT BREAKER TRIGGERED*\n"
            f"{_sep()}\n"
            f"  Reason: `{reason}`\n"
            f"{_sep()}\n"
            f"  All new entries PAUSED.\n"
            f"  Existing positions being monitored.\n"
            f"  Send /resume to override.\n"
            f"{_sep()}\n"
            f"  {E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    def send_kill_alert(self) -> bool:
        text = (
            f"{E['kill']} *KILL SWITCH ACTIVATED*\n"
            f"{_sep()}\n"
            f"  Emergency stop received.\n"
            f"  ALL positions being squared off.\n"
            f"  New trading HALTED for the day.\n"
            f"{_sep()}\n"
            f"  {E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # STATUS REPORT  (Bloomberg LIVE PORTFOLIO)
    # --------------------------------------------------------

    def send_status(self, risk_manager=None, balance_available: float = None,
                    margin_used: float = None) -> bool:
        if risk_manager is None:
            return self._send(
                f"{E['chart']} *PORTFOLIO STATUS*\n"
                f"{_sep()}\n"
                f"  {format_ist_timestamp()}\n"
                f"  No risk data available.\n"
                f"{_sep()}"
            )

        state     = getattr(risk_manager, "state", None)
        positions = state.positions      if state else {}
        capital   = state.daily_capital if state else 0
        n_trades  = state.daily_trades  if state else 0
        wins      = state.winning_trades if state else 0
        losses    = state.losing_trades  if state else 0
        wr_pct    = (wins / n_trades * 100) if n_trades > 0 else 0.0
        paused    = state.trading_paused if state else False

        # Pre-market: risk manager not yet initialized — use live broker balance as capital
        if capital <= 0 and balance_available and balance_available > 0:
            capital = balance_available

        if balance_available and balance_available > 0 and capital > 0:
            # Realized P&L = current balance minus opening capital for the day
            _rm_pnl = state.daily_pnl if state else 0
            daily_pnl = _rm_pnl if _rm_pnl != 0 else (balance_available - capital if balance_available != capital else 0)
        else:
            daily_pnl = state.daily_pnl if state else 0

        pnl_str  = _pnl_str(daily_pnl, capital)
        status   = "PAUSED ⏸" if paused else "ACTIVE ✅"

        target_pct = 1.0
        try:
            import config as _c
            target_pct = getattr(_c, "DAILY_PROFIT_TARGET_PCT", 1.0)
        except Exception:
            pass
        target_amt = capital * target_pct / 100 if capital > 0 else 0
        target_hit = daily_pnl >= target_amt if target_amt > 0 else False
        target_progress = (daily_pnl / target_amt * 100) if target_amt > 0 else 0
        target_bar  = _score_bar(min(100, target_progress))

        lines = [
            f"{E['chart']} *LIVE PORTFOLIO — {format_ist_timestamp()}*",
            f"{_sep()}",
            f"  STATUS   `{status}`",
            f"  CAPITAL  `{_CUR}{capital:,.0f}`",
        ]
        if balance_available is not None:
            bal_str = f"  BALANCE  `{_CUR}{balance_available:,.2f}`"
            if margin_used:
                bal_str += f"   MARGIN `{_CUR}{margin_used:,.2f}`"
            lines.append(bal_str)
        lines += [
            f"  DAY P&L  *{pnl_str}*",
            f"  TARGET   `{_CUR}{target_pct:.1f}%` = `{_CUR}{target_amt:,.0f}`  `{target_bar}` `{target_progress:.0f}%`",
            f"{_sep()}",
        ]

        if positions:
            lines.append(f"  OPEN POSITIONS ({len(positions)})")
            lines.append(f"  {'SYMBOL':<8}{'DIR':<7}{'ENTRY':>8}{'NOW':>8}{'P&L':>9}")
            lines.append(f"  {'──────':<8}{'─────':<7}{'──────':>8}{'──────':>8}{'─────':>9}")
            for sym, pos in list(positions.items())[:8]:
                p_pnl = getattr(pos, "pnl", 0)
                cur   = getattr(pos, "current_price", pos.entry_price)
                arrow = "↗" if p_pnl >= 0 else "↘"
                lines.append(
                    f"  {sym:<8}{pos.direction:<7}"
                    f"`{_CUR}{pos.entry_price:.2f}`"
                    f"`{_CUR}{cur:.2f}`"
                    f"  `{'+' if p_pnl>=0 else ''}{p_pnl:,.0f}` {arrow}"
                )
            lines.append(_sep())
        else:
            lines.append(f"  No open positions")
            lines.append(_sep())

        lines += [
            f"  TRADES   `{n_trades}` today   `{wins}W / {losses}L`   WR: `{wr_pct:.1f}%`",
            f"  {'🎯 1% TARGET HIT ✅' if target_hit else f'⏳ In progress — need {_CUR}{max(0, target_amt - daily_pnl):,.0f} more'}",
            f"{_sep()}",
        ]

        return self._send("\n".join(lines))

    # --------------------------------------------------------
    # HOURLY HEARTBEAT  — always-on live dashboard
    # --------------------------------------------------------

    def send_heartbeat(self, open_positions: dict = None) -> bool:
        """
        Hourly live update: WR, P&L, open positions, regime, circuit state.
        Fires automatically every 60 min during market hours from main.py.
        """
        n, wins, losses, wr, pnl = self._live_stats()
        pnl_str  = _pnl_str(pnl)
        wr_bar   = _score_bar(wr) if n > 0 else "░░░░░░░░░░"
        n_open   = len(open_positions) if open_positions else 0

        try:
            import config as _c
            capital    = getattr(_c, "MAX_DAILY_CAPITAL", 0)
            target_pct = getattr(_c, "DAILY_PROFIT_TARGET_PCT", 1.0)
            target_amt = capital * target_pct / 100 if capital > 0 else 0
            progress   = (pnl / target_amt * 100) if target_amt > 0 else 0
            target_bar = _score_bar(min(100, progress))
            target_str = f"`{target_bar}` `{progress:.0f}%` of `{_CUR}{target_amt:,.0f}` daily target"
        except Exception:
            target_str = ""

        rm = self._risk_manager
        paused  = getattr(getattr(rm, "state", None), "trading_paused", False) if rm else False
        consec  = getattr(getattr(rm, "state", None), "consecutive_losses", 0) if rm else 0
        circuit = "⏸ PAUSED" if paused else ("⚠️ CAUTION" if consec >= 2 else "✅ ACTIVE")

        regime_str = ""
        try:
            regime_name = getattr(self, "_last_regime", "")
            if not regime_name and rm:
                regime_name = getattr(getattr(rm, "_signal_gen", None), "_last_regime_name", "")
            if regime_name:
                regime_str = f"  REGIME   `{regime_name}`\n"
        except Exception:
            pass

        pos_lines = ""
        if open_positions:
            pos_lines = f"  {'─'*28}\n"
            for sym, pos in list(open_positions.items())[:5]:
                p_pnl = getattr(pos, "pnl", 0)
                arrow = "↗" if p_pnl >= 0 else "↘"
                pos_lines += (
                    f"  {sym:<7} `{pos.direction:<5}` "
                    f"`{'+' if p_pnl >= 0 else ''}{_CUR}{abs(p_pnl):,.0f}` {arrow}\n"
                )

        text = (
            f"⏱ *SATAVECTOR HOURLY UPDATE*\n"
            f"{_sep()}\n"
            f"  {format_ist_timestamp()}\n"
            f"{_sep()}\n"
            f"  STATUS   `{circuit}`\n"
            f"  DAY P&L  *{pnl_str}*\n"
            f"  {target_str}\n"
            f"{_sep()}\n"
            f"  TRADES   `{n}` closed today   `{wins}W / {losses}L`\n"
            f"  WIN RATE `{wr:.1f}%`  `{wr_bar}`\n"
            f"  OPEN     `{n_open}` position{'s' if n_open != 1 else ''}\n"
            f"{regime_str}"
            f"{pos_lines}"
            f"{_sep()}\n"
            f"  /status for full detail · /kill to stop"
        )
        return self._send(text)

    # --------------------------------------------------------
    # PAUSE / RESUME
    # --------------------------------------------------------

    def send_pause(self, reason: str = "") -> bool:
        text = (
            f"{E['pause']} *TRADING PAUSED*\n"
            f"{_sep()}\n"
            f"  {'Reason: ' + reason if reason else 'Manual pause.'}\n"
            f"  Send /resume to restart entries.\n"
            f"{_sep()}\n"
            f"  {E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    def send_resume(self) -> bool:
        return self._send(
            f"{E['resume']} *TRADING RESUMED*\n"
            f"{_sep()}\n"
            f"  Scanning for A+ setups...\n"
            f"{_sep()}\n"
            f"  {E['clock']} `{format_ist_timestamp()}`"
        )

    # --------------------------------------------------------
    # SQUARE-OFF WARNING
    # --------------------------------------------------------

    def send_squareoff_warning(self, positions) -> bool:
        n = len(positions) if hasattr(positions, "__len__") else 0
        text = (
            f"{E['warn']} *SQUARE-OFF WARNING — 3:50 PM ET*\n"
            f"{_sep()}\n"
            f"  `{n}` open position(s) will be force-closed.\n"
            f"  Closing at market price — EOD mandatory.\n"
            f"{_sep()}\n"
            f"  {E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # MORNING BRIEF  (Bloomberg MARKET BRIEF)
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
        Bloomberg-style morning market brief.

        Accepts two calling styles:
          1. send_morning_brief(brief_dict)             — new style (dict from overnight_analyzer)
          2. send_morning_brief(watchlist, avail, nifty) — legacy style from main.py
        """
        if isinstance(brief_or_watchlist, dict):
            brief      = brief_or_watchlist
            bias       = brief.get("day_bias", "NEUTRAL")
            bias_score = brief.get("bias_score", 0)
            vix_data   = brief.get("vix", {})
            vix        = vix_data.get("vix", 0) if isinstance(vix_data, dict) else 0
            spy_data   = brief.get("spy_gap", {})
            gap_pct    = spy_data.get("gap_pct", 0) if isinstance(spy_data, dict) else 0
            spy_price  = spy_data.get("price", 0)   if isinstance(spy_data, dict) else 0
            risks      = brief.get("key_risks", [])
            ai_thesis  = brief.get("ai_thesis", "")
            watchlist  = brief.get("top_watchlist", [])
            avail_cap  = available or brief.get("available_capital", 0)
            nifty_ltp  = nifty_open or brief.get("spy_open", 0)
        else:
            watchlist  = brief_or_watchlist or []
            avail_cap  = available
            nifty_ltp  = nifty_open
            spy_price  = nifty_ltp
            bias, bias_score, vix, gap_pct = "NEUTRAL", 0, 15.0, 0.0
            risks, ai_thesis = [], ""

        bias_emoji   = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(bias, "🟡")
        gap_arrow    = _dir_arrow(gap_pct)
        vix_regime   = "HIGH ⚠️" if vix > 25 else ("ELEVATED" if vix > 18 else "NORMAL ✅")
        risks_str    = "\n".join(f"  • {r}" for r in risks[:4]) if risks else "  No high-impact events today"
        thesis       = textwrap.shorten(ai_thesis or "Scanning for momentum setups...", 250, placeholder="...")
        wl_str       = "  " + ", ".join(f"`{s}`" for s in watchlist[:12]) if watchlist else "  Loading..."

        target_pct, target_amt, risk_budget = 1.0, 0.0, 0.0
        try:
            import config as _c
            target_pct  = getattr(_c, "DAILY_PROFIT_TARGET_PCT", 1.0)
            cap         = avail_cap or getattr(_c, "MAX_DAILY_CAPITAL", 0)
            target_amt  = cap * target_pct / 100
            risk_budget = cap * getattr(_c, "DAILY_LOSS_LIMIT_PCT", 2.0) / 100
        except Exception:
            pass

        now_str   = format_ist_timestamp()
        date_str  = get_current_ist_time().strftime("%Y-%m-%d")
        sess_time = get_current_ist_time()
        # session label based on ET hour
        et_hour = (sess_time.hour - 5) % 24  # rough ET from IST
        if et_hour < 10:
            session = "OPENING DRIVE (2.2× size)"
        elif et_hour < 12:
            session = "MID-MORNING (1.0× size)"
        else:
            session = "MIDDAY (0.8× size)"

        text = (
            f"{E['rocket']} *MARKET BRIEF — {date_str}*\n"
            f"{_sep()}\n"
            f"  SPY      `{_CUR}{spy_price:,.2f}`    {gap_arrow} `{gap_pct:+.2f}%`\n"
            f"  VIX      `{vix:.1f}`         {vix_regime}\n"
            f"{_sep()}\n"
            f"  REGIME   {bias_emoji} *{bias}*   score: `{bias_score:+d}`\n"
            f"  SESSION  `{session}`\n"
            f"{_sep()}\n"
            f"  CAPITAL  `{_CUR}{avail_cap:,.0f}`\n"
            f"  TARGET   `{_CUR}{target_amt:,.0f}`  ({target_pct:.1f}% of cap)\n"
            f"  RISK MAX `{_CUR}{risk_budget:,.0f}`  (daily loss limit)\n"
            f"{_sep()}\n"
            f"  WATCHLIST ({len(watchlist)} stocks)\n"
            f"{wl_str}\n"
            f"{_sep()}\n"
            f"  CALENDAR\n{risks_str}\n"
        )
        if ai_thesis:
            text += f"{_sep()}\n  {E['brain']} _{thesis}_\n"
        text += (
            f"{_sep()}\n"
            f"  🤖 {__import__('config').BOT_DISPLAY_NAME} — ARMED & READY\n"
            f"  {E['clock']} `{now_str}`"
        )
        if oc_summary:
            text += f"\n{_sep()}\n{oc_summary}"
        if fii_summary:
            text += f"\n{_sep()}\n{fii_summary}"

        return self._send(text[:4096])

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
        """Exit alert called by main.py after a position is closed."""
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
    # EOD PERFORMANCE REPORT  (Bloomberg EOD REPORT)
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

        wins_pnl   = [t.get("pnl", 0) for t in all_trades if t.get("pnl", 0) > 0]
        losses_pnl = [t.get("pnl", 0) for t in all_trades if t.get("pnl", 0) < 0]
        avg_win    = sum(wins_pnl)  / len(wins_pnl)   if wins_pnl  else 0.0
        avg_loss   = sum(losses_pnl)/ len(losses_pnl) if losses_pnl else 0.0
        ev_trade   = (wr_pct/100 * avg_win) + ((1 - wr_pct/100) * avg_loss) if n_trades > 0 else 0.0

        best  = max(all_trades, key=lambda t: t.get("pnl", 0), default=None)
        worst = min(all_trades, key=lambda t: t.get("pnl", 0), default=None)
        best_str  = f"{best.get('symbol','?')}  `+{_CUR}{best.get('pnl',0):,.0f}`"  if best  else "—"
        worst_str = f"{worst.get('symbol','?')}  `{_CUR}{worst.get('pnl',0):,.0f}`" if worst else "—"

        try:
            import config as _cfg
            _daily_tgt_pct = getattr(_cfg, "DAILY_PROFIT_TARGET_PCT", 1.0)
        except Exception:
            _daily_tgt_pct = 1.0
        target_hit  = (daily_pnl >= capital * _daily_tgt_pct / 100) if capital > 0 else False
        new_capital = capital + daily_pnl

        sign   = "+" if daily_pnl >= 0 else "-"
        border = "=" * 34
        pnl_box = (
            f"  {border}\n"
            f"  |  NET P&L:  {sign}{_CUR}{abs(daily_pnl):,.0f}  ({sign}{abs(pnl_pct):.2f}%)  |\n"
            f"  {border}"
        )

        text = (
            f"📋 *EOD PERFORMANCE REPORT — {format_ist_timestamp()}*\n"
            f"{_sep()}\n"
            f"{pnl_box}\n"
            f"{_sep()}\n"
            f"  TRADES      `{n_trades}`     signals fired\n"
            f"  WINNERS     `{wins}`     `{wr_pct:.1f}%` win rate\n"
            f"  LOSERS      `{losses}`     avg `{_CUR}{abs(avg_loss):,.0f}` per loss\n"
            f"  AVG WIN     `+{_CUR}{avg_win:,.0f}`\n"
            f"  BEST        {best_str}\n"
            f"  WORST       {worst_str}\n"
            f"{_sep()}\n"
            f"  GROSS P&L   `{_pnl_str(daily_pnl)}`\n"
            f"  CAPITAL     `{_CUR}{capital:,.0f}` → `{_CUR}{new_capital:,.0f}`\n"
            f"{_sep()}\n"
            f"  EV/TRADE    `{'+' if ev_trade>=0 else ''}{_CUR}{ev_trade:,.0f}`\n"
            f"  DAILY TGT   `{'✅ ACHIEVED' if target_hit else f'❌ MISSED ({_daily_tgt_pct:.1f}%)'}`\n"
            f"{_sep()}\n"
            f"  🤖 {__import__('config').BOT_DISPLAY_NAME}  |  Next session: 09:30 ET\n"
            f"  {E['clock']} `{format_ist_timestamp()}`"
        )

        chart = _build_equity_curve(all_trades, capital) if all_trades else None
        return self._send(text, chart)

    # --------------------------------------------------------
    # 1% TARGET ACHIEVED  (Bloomberg 1% CELEBRATION)
    # --------------------------------------------------------

    def send_target_achieved(
        self,
        daily_pnl: float,
        capital: float,
        trades: int,
        wins: int,
        losses: int,
    ) -> bool:
        """
        Bloomberg-grade 1% daily target celebration.
        Called by main.py _check_profit_lock() on first LOCK activation.
        """
        pct = (daily_pnl / capital * 100) if capital > 0 else 0.0
        wr  = (wins / trades * 100) if trades > 0 else 0.0
        border = "=" * 36
        text = (
            f"🎯 *DAILY 1% TARGET ACHIEVED*\n"
            f"{_sep()}\n"
            f"  {border}\n"
            f"  |  P&L: +{_CUR}{daily_pnl:,.0f}  (+{pct:.2f}%)    |\n"
            f"  {border}\n"
            f"{_sep()}\n"
            f"  TRADES   `{trades}`   W/L: `{wins}/{losses}`   WR: `{wr:.0f}%`\n"
            f"{_sep()}\n"
            f"  {E['lock']} All stops tightened → entry+0.3×ATR\n"
            f"  ⚡ New entries: A+ only at 60% size\n"
            f"  💡 Type /pause to stop all new entries\n"
            f"{_sep()}\n"
            f"  🏆 *Know your number. Hit it. Protect it.*\n"
            f"  {E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # TOKEN REFRESH
    # --------------------------------------------------------

    def send_token_refresh(self, success: bool, method: str = "TOTP") -> bool:
        emoji  = E["profit"] if success else E["loss"]
        status = "SUCCEEDED" if success else "FAILED — using previous token"
        text = (
            f"{emoji} *Auth Token Refresh*\n"
            f"{_sep()}\n"
            f"  Status: `{status}`\n"
            f"  Method: `{method}`\n"
            f"{_sep()}\n"
            f"  {E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # WATCHLIST UPDATE
    # --------------------------------------------------------

    def send_watchlist(self, symbols: List[str]) -> bool:
        wl = "  " + ", ".join(f"`{s}`" for s in symbols[:20])
        text = (
            f"{E['pin']} *WATCHLIST — {len(symbols)} symbols*\n"
            f"{_sep()}\n"
            f"{wl}\n"
            f"{_sep()}\n"
            f"  {E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # AI INSIGHT
    # --------------------------------------------------------

    def send_ai_insight(self, symbol: str, insight: str,
                        trade_review: str = "") -> bool:
        text = (
            f"{E['brain']} *AI INSIGHT — {symbol}*\n"
            f"{_sep()}\n"
            f"  _{textwrap.shorten(insight, 500, placeholder='...')}_\n"
        )
        if trade_review:
            text += (
                f"{_sep()}\n"
                f"  *Trade Review:*\n"
                f"  _{textwrap.shorten(trade_review, 350, placeholder='...')}_\n"
            )
        text += f"{_sep()}\n  {E['clock']} `{format_ist_timestamp()}`"
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
            f"{_sep()}\n"
            f"  {body}\n"
            f"{_sep()}\n"
            f"  {E['clock']} `{format_ist_timestamp()}`"
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
                f"{_sep()}\n"
                f"  Underlying: `{pos.underlying}`\n"
                f"  Contract:   `{pos.opt_symbol}`\n"
                f"  Type:       `{pos.direction}`\n"
                f"  Qty:        `{pos.contracts}` contract(s)\n"
                f"  Premium:    `{_CUR}{pos.entry_premium:.2f}` per contract\n"
                f"  Total Cost: `{_CUR}{pos.entry_premium * pos.contracts * 100:.0f}`\n"
                f"  DTE:        `{pos.dte}` day(s)\n"
                f"  Stop:       `{_CUR}{pos.entry_premium * 0.55:.2f}` (−45%)\n"
                f"  Target 1:   `{_CUR}{pos.entry_premium * 1.80:.2f}` (+80%)\n"
                f"  Target 2:   `{_CUR}{pos.entry_premium * 2.50:.2f}` (+150%)\n"
                f"{_sep()}\n"
                f"  {E['clock']} `{format_ist_timestamp()}`"
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
                f"{_sep()}\n"
                f"  Contract:  `{pos.opt_symbol}`\n"
                f"  Entry:     `{_CUR}{pos.entry_premium:.2f}`\n"
                f"  Exit:      `{_CUR}{exit_premium:.2f}`\n"
                f"  P&L:       `{_CUR}{pnl_usd:+.0f}` ({pnl_pct:+.1f}%)\n"
                f"  Contracts: `{pos.contracts}×100`\n"
                f"{_sep()}\n"
                f"  {E['clock']} `{format_ist_timestamp()}`"
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
            lines = [f"🔍 *UNUSUAL OPTIONS ACTIVITY*\n{_sep()}"]
            for u in uoa_list[:5]:
                lines.append(
                    f"  `{u['symbol']}` {u['type'].upper()} "
                    f"strike={_CUR}{u['strike']:.0f} "
                    f"vol/OI={u['vol_oi']:.1f}× "
                    f"dte={u['dte']}d"
                )
            lines.append(f"{_sep()}\n  {E['clock']} `{format_ist_timestamp()}`")
            return self._send("\n".join(lines))
        except Exception as e:
            logger.debug(f"send_options_uoa_alert: {e}")
            return False

    def send_capital_projection(self, capital: float = 500.0) -> bool:
        """Send P&L projection via capital_calculator."""
        try:
            from capital_calculator import get_telegram_summary
            text = get_telegram_summary(capital)
            return self._send(text)
        except Exception as e:
            logger.debug(f"send_capital_projection: {e}")
            return False
