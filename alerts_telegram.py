"""
alerts_telegram.py — NSE Momentum Groww AI Bot
Rich Telegram Alerts with Matplotlib Candlestick Charts

Sends:
- Trade entry alerts with chart image, indicators, entry arrow
- Exit alerts with P&L
- Circuit breaker / kill switch alerts
- Morning summary + EOD performance report
- All timestamps in IST
"""

import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict
from zoneinfo import ZoneInfo

import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time, format_currency
from signal_generator import TradeSignal
from risk_manager import RiskManager

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# Try importing telegram libs
try:
    from telegram import Bot
    from telegram.constants import ParseMode
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot not installed — alerts disabled")

# Try importing chart libs
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    CHARTS_AVAILABLE = True
except ImportError:
    CHARTS_AVAILABLE = False
    logger.warning("matplotlib not installed — chart images disabled")


class TelegramAlerter:
    """
    Sends rich trading alerts via Telegram.
    All timestamps shown in IST regardless of server timezone.
    """

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self._bot = None
        if TELEGRAM_AVAILABLE and bot_token and chat_id:
            try:
                self._bot = Bot(token=bot_token)
                logger.info(f"[{format_ist_timestamp()}] Telegram alerter initialized")
            except Exception as e:
                logger.error(f"[{format_ist_timestamp()}] Telegram init error: {e}")

    def _is_ready(self) -> bool:
        return self._bot is not None and bool(self.chat_id)

    # --------------------------------------------------------
    # ENTRY ALERT
    # --------------------------------------------------------

    def send_entry_alert(
        self,
        signal: TradeSignal,
        df_5m: Optional[pd.DataFrame] = None,
        ai_explanation: str = "",
    ) -> bool:
        """Send trade entry alert with chart image and Gemini AI explanation."""
        direction_emoji = "🟢📈" if signal.direction == "LONG" else "🔴📉"
        grade_emoji = {"A+": "💎", "A": "⭐", "B": "✅", "C": "⚠️"}.get(signal.quality_grade, "📊")
        conf_stars = "⭐⭐⭐" if signal.signal_score >= 85 else "⭐⭐" if signal.signal_score >= 72 else "⭐"

        sl_pct  = abs(signal.entry_price - signal.stop_loss) / signal.entry_price * 100
        t1_pct  = abs(signal.target_1 - signal.entry_price) / signal.entry_price * 100
        t2_pct  = abs(signal.target_2 - signal.entry_price) / signal.entry_price * 100
        mtf_str = (
            f"5m:{signal.timeframe_alignment.get('5m','?')} / "
            f"15m:{signal.timeframe_alignment.get('15m','?')} / "
            f"1h:{signal.timeframe_alignment.get('1h','?')}"
        )

        # Build AI explanation line
        ai_line = ""
        if not ai_explanation:
            # Try to get Gemini explanation automatically
            try:
                from ai_brain import get_ai_brain
                brain = get_ai_brain()
                if brain._enabled and signal.indicators:
                    ind = signal.indicators
                    vwap_pos = "above VWAP" if ind.vwap and signal.entry_price >= ind.vwap else "below VWAP"
                    ai_explanation = brain.explain_signal(
                        symbol=signal.symbol,
                        direction=signal.direction,
                        patterns=signal.patterns,
                        score=signal.signal_score,
                        regime=signal.timeframe_alignment.get("regime", "UNKNOWN"),
                        mtf_info=mtf_str,
                        rsi=ind.rsi if ind.rsi else 50,
                        vwap_pos=vwap_pos,
                        volume_ratio=ind.volume_ratio if ind.volume_ratio else 1.0,
                    )
            except Exception:
                pass
        if ai_explanation:
            ai_line = f"\n🤖 *AI Insight:* _{ai_explanation[:300]}_\n"

        text = (
            f"{direction_emoji} *{signal.direction} SIGNAL — {signal.symbol}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{grade_emoji} *Grade:* {signal.quality_grade} | "
            f"*Score:* {signal.signal_score:.0f}/100 {conf_stars} | "
            f"*Size:* {signal.size_multiplier:.1f}x\n"
            f"⏰ *Time:* {signal.signal_time}\n"
            f"\n"
            f"💰 *Entry:* ₹{signal.entry_price:.2f}\n"
            f"🛑 *Stop Loss:* ₹{signal.stop_loss:.2f} (-{sl_pct:.1f}%)\n"
            f"✅ *Target 1:* ₹{signal.target_1:.2f} (+{t1_pct:.1f}%) → exit 50%\n"
            f"🚀 *Target 2:* ₹{signal.target_2:.2f} (+{t2_pct:.1f}%) → exit 30%\n"
            f"🏃 *Runner:* 20% with trailing stop\n"
            f"📊 *R:R:* {signal.risk_reward:.1f}:1 | *ATR:* ₹{signal.atr:.2f}\n"
            f"\n"
            f"📋 *Patterns:* {', '.join(signal.patterns[:3]) if signal.patterns else 'N/A'}\n"
            f"📈 *MTF:* {mtf_str}\n"
            f"💹 *RS vs Nifty:* {signal.relative_strength:+.1f}%\n"
            f"📝 *Setup:* {signal.rationale[:180]}\n"
            f"{ai_line}"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ _Real money at risk. SL is mandatory._"
        )

        chart_buf = None
        if CHARTS_AVAILABLE and df_5m is not None and not df_5m.empty:
            chart_buf = self._generate_chart(df_5m, signal)

        return self._send(text, image_buf=chart_buf, parse_mode="Markdown")

    # --------------------------------------------------------
    # EXIT ALERT
    # --------------------------------------------------------

    def send_exit_alert(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        quantity: int,
        pnl: float,
        reason: str,
    ) -> bool:
        pnl_emoji = "✅ PROFIT" if pnl >= 0 else "❌ LOSS"
        pnl_pct = (pnl / (entry_price * quantity)) * 100 if entry_price * quantity > 0 else 0
        direction_emoji = "📈" if direction == "LONG" else "📉"

        text = (
            f"{direction_emoji} *EXIT — {symbol}* | {pnl_emoji}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ *Exit Time:* {format_ist_timestamp()}\n"
            f"💰 *Entry:* ₹{entry_price:.2f} → *Exit:* ₹{exit_price:.2f}\n"
            f"📦 *Qty:* {quantity}\n"
            f"💵 *P&L:* {format_currency(pnl)} ({pnl_pct:+.2f}%)\n"
            f"📋 *Reason:* {reason}\n"
        )
        return self._send(text, parse_mode="Markdown")

    # --------------------------------------------------------
    # CIRCUIT BREAKER / KILL
    # --------------------------------------------------------

    def send_circuit_alert(self, reason: str) -> bool:
        text = (
            f"🚨 *CIRCUIT BREAKER ACTIVATED*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {format_ist_timestamp()}\n"
            f"📋 Reason: {reason}\n"
            f"⚠️ No new trades until manually resumed.\n"
            f"Use /resume to restart trading."
        )
        return self._send(text, parse_mode="Markdown")

    def send_kill_alert(self) -> bool:
        text = (
            f"🛑 *KILL SWITCH ACTIVATED*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {format_ist_timestamp()}\n"
            f"All positions being squared off.\n"
            f"Bot stopped. Use /resume to restart."
        )
        return self._send(text, parse_mode="Markdown")

    # --------------------------------------------------------
    # DAILY REPORTS
    # --------------------------------------------------------

    def send_morning_brief(self, watchlist: List[str], balance: float,
                           nifty_open: float = 0) -> bool:
        text = (
            f"🌅 *MARKET OPEN — NSE Momentum Bot*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {format_ist_timestamp()}\n"
            f"💰 Available: {format_currency(balance)}\n"
            f"📊 Nifty Open: {nifty_open:.2f}\n"
            f"📋 Watchlist ({len(watchlist)}): {', '.join(watchlist[:10])}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 Bot is scanning for momentum signals...\n"
            f"Trading: 9:15 AM – 3:30 PM IST"
        )
        return self._send(text, parse_mode="Markdown")

    def send_eod_report(self, risk_manager: RiskManager,
                        compounder_summary: str = "",
                        ai_eod: str = "") -> bool:
        """Send end-of-day performance summary with Gemini AI analysis."""
        summary = risk_manager.get_daily_summary()
        pnl = summary["daily_pnl"]
        pnl_pct = summary["daily_pnl_pct"]
        win_rate = summary["win_rate"]

        # Profit emoji with note printing machine style
        if pnl >= 0:
            if pnl_pct >= 1.5:
                pnl_emoji = "💰💰💰 GREAT DAY"
            elif pnl_pct >= 0.5:
                pnl_emoji = "💰💰 GOOD DAY"
            else:
                pnl_emoji = "💰 PROFIT DAY"
        else:
            pnl_emoji = "❌ LOSS DAY"

        # Win rate badge
        if win_rate >= 70:
            wr_badge = "🎯🎯 SNIPER"
        elif win_rate >= 55:
            wr_badge = "🎯 ON TARGET"
        else:
            wr_badge = "📉 REVIEW"

        # Compounder line
        comp_line = f"\n💹 *Compounding:*\n_{compounder_summary[:200]}_\n" if compounder_summary else ""

        # Gemini AI EOD line
        ai_line = f"\n🤖 *AI Review:*\n_{ai_eod[:300]}_\n" if ai_eod else ""

        text = (
            f"📊 *EOD REPORT — {summary['date']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {format_ist_timestamp()} IST\n"
            f"\n"
            f"{pnl_emoji}\n"
            f"💵 *P&L:* {format_currency(pnl)} ({pnl_pct:+.2f}%)\n"
            f"📈 *Trades:* {summary['total_trades']} "
            f"(✅ {summary['wins']} wins | ❌ {summary['losses']} losses)\n"
            f"{wr_badge} *Win Rate:* {win_rate:.1f}%\n"
            f"📉 *Max Drawdown:* {format_currency(summary['max_drawdown'])}\n"
            f"🔴 *Cons. Losses:* {summary['consecutive_losses']}\n"
            f"{comp_line}"
            f"{ai_line}"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 Bot shutting down. Markets reopen 9:15 AM IST."
        )
        return self._send(text, parse_mode="Markdown")

    def send_status(self, risk_manager: RiskManager) -> bool:
        """Send /status response."""
        summary = risk_manager.get_daily_summary()
        pnl = summary["daily_pnl"]
        open_pos = list(risk_manager.state.positions.values())
        pos_lines = "\n".join(
            f"  • {p.symbol} {p.direction} x{p.quantity} | "
            f"Entry: ₹{p.entry_price:.2f} | "
            f"P&L: {format_currency(p.pnl)}"
            for p in open_pos
        ) or "  No open positions"

        text = (
            f"📊 *BOT STATUS — {format_ist_timestamp()}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 *Daily P&L:* {format_currency(pnl)} ({summary['daily_pnl_pct']:+.1f}%)\n"
            f"📋 *Trades today:* {summary['total_trades']} (W:{summary['wins']} L:{summary['losses']})\n"
            f"⏸ *Paused:* {'Yes — ' + summary.get('pause_reason','') if summary['paused'] else 'No'}\n"
            f"🚨 *Circuit breaker:* {'ACTIVE' if summary['circuit_breaker'] else 'Off'}\n"
            f"\n"
            f"📌 *Open Positions ({summary['open_positions']}):*\n"
            f"{pos_lines}"
        )
        return self._send(text, parse_mode="Markdown")

    # --------------------------------------------------------
    # CHART GENERATION
    # --------------------------------------------------------

    def _generate_chart(
        self, df: pd.DataFrame, signal: TradeSignal
    ) -> Optional[io.BytesIO]:
        """Generate candlestick chart with indicators and signal arrow."""
        if not CHARTS_AVAILABLE:
            return None
        try:
            df = df.tail(50).copy()
            fig, (ax1, ax2, ax3) = plt.subplots(
                3, 1, figsize=(12, 8),
                gridspec_kw={"height_ratios": [4, 1.5, 1.5]},
                facecolor="#0d1117"
            )
            fig.suptitle(
                f"{signal.symbol} — {signal.direction} Signal | "
                f"Score: {signal.signal_score:.0f}/100 | {signal.signal_time}",
                color="white", fontsize=11, fontweight="bold"
            )

            # ---- Candlestick ----
            for ax in [ax1, ax2, ax3]:
                ax.set_facecolor("#0d1117")
                ax.tick_params(colors="gray")
                for spine in ax.spines.values():
                    spine.set_color("#30363d")

            x = range(len(df))
            for i, (idx, row) in enumerate(df.iterrows()):
                color = "#26a641" if row["close"] >= row["open"] else "#da3633"
                ax1.plot([i, i], [row["low"], row["high"]], color=color, lw=0.8)
                body_low  = min(row["open"], row["close"])
                body_high = max(row["open"], row["close"])
                ax1.add_patch(plt.Rectangle(
                    (i - 0.4, body_low), 0.8, max(body_high - body_low, 0.01),
                    color=color, zorder=2
                ))

            # EMAs
            if "ema9" in df.columns:
                ax1.plot(x, df["ema9"].values, color="#58a6ff", lw=1, label="EMA9", alpha=0.8)
            if "ema21" in df.columns:
                ax1.plot(x, df["ema21"].values, color="#f0883e", lw=1, label="EMA21", alpha=0.8)
            if "ema50" in df.columns:
                ax1.plot(x, df["ema50"].values, color="#d2a8ff", lw=1, label="EMA50", alpha=0.6)
            if "vwap" in df.columns:
                ax1.plot(x, df["vwap"].values, color="#ffd33d", lw=1.2,
                         linestyle="--", label="VWAP", alpha=0.9)

            # Entry, SL, TP lines
            last_x = len(df) - 1
            ax1.axhline(signal.entry_price, color="#58a6ff", lw=1.5, linestyle="--", alpha=0.9)
            ax1.axhline(signal.stop_loss, color="#da3633", lw=1.5, linestyle="--", alpha=0.9)
            ax1.axhline(signal.target_1, color="#26a641", lw=1.2, linestyle="--", alpha=0.8)
            ax1.axhline(signal.target_2, color="#3fb950", lw=1.2, linestyle=":", alpha=0.8)

            # Signal arrow
            arrow_color = "#26a641" if signal.direction == "LONG" else "#da3633"
            arrow_y = signal.entry_price - signal.atr if signal.direction == "LONG" \
                      else signal.entry_price + signal.atr
            ax1.annotate(
                f"{'▲' if signal.direction == 'LONG' else '▼'} {signal.direction}",
                xy=(last_x, signal.entry_price),
                xytext=(last_x - 3, arrow_y),
                arrowprops=dict(arrowstyle="->", color=arrow_color, lw=2),
                color=arrow_color, fontsize=10, fontweight="bold"
            )

            # Labels
            ax1.text(last_x + 0.3, signal.entry_price, f" Entry ₹{signal.entry_price:.0f}",
                     color="#58a6ff", fontsize=7, va="center")
            ax1.text(last_x + 0.3, signal.stop_loss, f" SL ₹{signal.stop_loss:.0f}",
                     color="#da3633", fontsize=7, va="center")
            ax1.text(last_x + 0.3, signal.target_1, f" T1 ₹{signal.target_1:.0f}",
                     color="#26a641", fontsize=7, va="center")
            ax1.legend(loc="upper left", fontsize=7, facecolor="#0d1117",
                       labelcolor="white", framealpha=0.5)
            ax1.set_ylabel("Price (₹)", color="gray", fontsize=8)

            # ---- Volume ----
            for i, (idx, row) in enumerate(df.iterrows()):
                color = "#26a641" if row["close"] >= row["open"] else "#da3633"
                ax2.bar(i, row["volume"], color=color, alpha=0.7, width=0.8)
            if "volume_sma" in df.columns:
                ax2.plot(x, df["volume_sma"].values, color="#ffd33d", lw=1)
            ax2.set_ylabel("Volume", color="gray", fontsize=7)

            # ---- RSI ----
            if "rsi" in df.columns:
                rsi_vals = df["rsi"].values
                ax3.plot(x, rsi_vals, color="#58a6ff", lw=1.2)
                ax3.axhline(70, color="#da3633", lw=0.8, linestyle="--", alpha=0.7)
                ax3.axhline(30, color="#26a641", lw=0.8, linestyle="--", alpha=0.7)
                ax3.axhline(50, color="gray", lw=0.6, linestyle=":", alpha=0.5)
                ax3.fill_between(x, rsi_vals, 50,
                                 where=[r >= 50 for r in rsi_vals],
                                 color="#26a641", alpha=0.15)
                ax3.fill_between(x, rsi_vals, 50,
                                 where=[r < 50 for r in rsi_vals],
                                 color="#da3633", alpha=0.15)
                ax3.set_ylabel("RSI", color="gray", fontsize=7)
                ax3.set_ylim(0, 100)

            plt.tight_layout()
            buf = io.BytesIO()
            plt.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                        facecolor="#0d1117")
            buf.seek(0)
            plt.close(fig)
            return buf

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Chart generation failed: {e}")
            return None

    # --------------------------------------------------------
    # CORE SEND
    # --------------------------------------------------------

    def _send(
        self,
        text: str,
        image_buf: Optional[io.BytesIO] = None,
        parse_mode: str = "Markdown",
    ) -> bool:
        if not self._is_ready():
            logger.debug(f"Telegram not configured — alert skipped")
            return False
        try:
            import asyncio

            async def _do_send():
                if image_buf:
                    image_buf.seek(0)
                    await self._bot.send_photo(
                        chat_id=self.chat_id,
                        photo=image_buf,
                        caption=text[:1024],
                        parse_mode=parse_mode,
                    )
                else:
                    await self._bot.send_message(
                        chat_id=self.chat_id,
                        text=text[:4096],
                        parse_mode=parse_mode,
                    )

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        future = pool.submit(asyncio.run, _do_send())
                        future.result(timeout=15)
                else:
                    loop.run_until_complete(_do_send())
            except RuntimeError:
                asyncio.run(_do_send())
            return True

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Telegram send error: {e}")
            return False

    def send_text(self, text: str) -> bool:
        """Send plain text message."""
        return self._send(text)
