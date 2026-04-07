"""
import asyncio
import threading
import os
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
# Your existing imports...
def start_simple_telegram_listener():
    from alerts_telegram import TelegramAlerter   # ← move import here
    
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        logger.warning("Telegram token or chat_id missing")
        return

    alerter = TelegramAlerter(token, chat_id)

    # ... rest of your listener code
main.py — NSE Momentum Groww AI Bot
Central Orchestrator with IST Market Hours + Auto-Shutdown + AI Learning

⚠️ WARNING: THIS BOT PLACES REAL ORDERS WITH REAL MONEY ON GROWW.
⚠️ Server runs in UK (UTC) — ALL market logic uses IST (Asia/Kolkata).
⚠️ Start with LIVE_TRADING_ENABLED=False until confident in the setup.
⚠️ Monitor manually for at least 2 weeks before increasing capital.

Full lifecycle:
  [24/7 Background]   continuous_learner.py — downloads data, learns, adapts
  [08:00 AM IST]      Overnight analysis (global markets, Gift Nifty, VIX)
  [08:30 AM IST]      Morning brief → Telegram
  [08:45 AM IST]      TOTP login to Groww → token refresh
  [09:00 AM IST]      Pre-market watchlist scan + AI stock assessment
  [09:15 AM IST]      Market open → trading begins
  [09:15–10:00 IST]   OPENING DRIVE — most aggressive momentum window
  [10:00–11:00 IST]   Morning session — normal trading
  [11:00–13:00 IST]   MIDDAY CHOP — 70% reduced size / skip
  [13:30–15:00 IST]   Afternoon trend — institutional activity
  [15:15 AM IST]      Square-off warning sent
  [15:20 AM IST]      Force close all positions
  [15:30 AM IST]      EOD shutdown
  [16:00 PM IST]      Self-learning cycle
  [16:30 PM IST]      AI trade review → lessons extracted
  [17:00 PM IST]      Incremental trainer run
"""

import asyncio
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime
from typing import Optional, List
from zoneinfo import ZoneInfo

from utils import (
    format_ist_timestamp, get_current_ist_time, is_market_open_ist,
    is_pre_market_ist, should_force_squareoff_ist, is_squareoff_time_ist,
    minutes_until_market_open, minutes_until_market_close,
    is_token_refresh_time, setup_logging, is_market_day_ist
)
import config

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class TradingBot:
    """
    Main NSE Momentum Trading Bot Orchestrator.
    Handles the full lifecycle from startup to EOD shutdown.
    """

    def __init__(self):
        self.running = False
        self.market_open_today = False
        self.eod_done = False
        self._token_refreshed_today = False
        self._day_initialized = False
        self._scan_interval = 60  # seconds between full watchlist scans

        # Modules (initialized lazily after auth)
        self.fetcher = None
        self.risk_manager = None
        self.executor = None
        self.signal_gen = None
        self.alerter = None
        self.news_filter = None
        self.watchlist_mgr = None
        self.dashboard = None
        self.mtf_analyzer = None
        self.journal = None
        self.learner = None
        self.ai_brain = None
        self.overnight = None
        self.calendar = None
        self.data_store = None
        self.cont_learner = None
        self._last_trade_date = ""
        self._overnight_run_today = False

    # --------------------------------------------------------
    # STARTUP
    # --------------------------------------------------------

    def initialize(self) -> bool:
        """Initialize all bot components. Returns True if ready."""
        logger.info(f"[{format_ist_timestamp()}] 🚀 NSE Momentum Bot initializing...")
        logger.info(f"[{format_ist_timestamp()}] Server time: {datetime.now(IST).astimezone(ZoneInfo('UTC'))} UTC")
        logger.info(f"[{format_ist_timestamp()}] IST time: {format_ist_timestamp()}")

        # Validate config
        issues = config.validate_config()
        if issues:
            for issue in issues:
                logger.warning(f"[{format_ist_timestamp()}] Config warning: {issue}")

        if config.LIVE_TRADING_ENABLED:
            logger.warning(
                f"[{format_ist_timestamp()}] ⚡⚡⚡ LIVE TRADING ENABLED ⚡⚡⚡\n"
                "REAL ORDERS WILL BE PLACED ON GROWW. "
                "Capital: " + str(config.MAX_DAILY_CAPITAL)
            )
        else:
            logger.info(f"[{format_ist_timestamp()}] 🔒 DRY RUN MODE — No real orders")

        # Initialize Groww auth
        from auth_groww import initialize_auth, get_auth_manager
        if not initialize_auth():
            logger.warning(f"[{format_ist_timestamp()}] Auth init incomplete — will retry at 8:45 AM IST")

        # Initialize data fetcher
        from data_fetch_groww import GrowwDataFetcher
        self.fetcher = GrowwDataFetcher()

        # Initialize risk manager
        from risk_manager import RiskManager
        self.risk_manager = RiskManager(
            max_daily_capital=config.MAX_DAILY_CAPITAL,
            max_risk_pct=config.MAX_RISK_PER_TRADE_PCT,
            daily_loss_limit_pct=config.DAILY_LOSS_LIMIT_PCT,
            max_positions=config.MAX_POSITIONS,
            nifty_circuit_pct=config.NIFTY_CIRCUIT_PCT,
            consecutive_loss_limit=config.CONSECUTIVE_LOSS_LIMIT,
            pause_minutes=config.PAUSE_AFTER_LOSSES_MINUTES,
        )

        # Initialize executor
        from execution_groww import GrowwExecutor
        self.executor = GrowwExecutor(
            self.risk_manager,
            live_enabled=config.LIVE_TRADING_ENABLED
        )

        # Initialize news filter
        from news_filter import NewsFilter
        self.news_filter = NewsFilter(
            news_api_key=config.NEWS_API_KEY,
            blackout_minutes=config.NEWS_BLACKOUT_MINUTES
        )

        # Initialize watchlist manager
        from watchlist_manager import WatchlistManager
        self.watchlist_mgr = WatchlistManager()

        # Initialize signal generator
        from signal_generator import SignalGenerator
        self.signal_gen = SignalGenerator(
            data_fetcher=self.fetcher,
            news_filter=self.news_filter,
            min_signal_score=config.MIN_SIGNAL_SCORE,
            high_confidence_score=config.HIGH_CONFIDENCE_SCORE,
        )

        # Initialize Telegram alerter
        from alerts_telegram import TelegramAlerter
        self.alerter = TelegramAlerter(
            bot_token=config.TELEGRAM_BOT_TOKEN,
            chat_id=config.TELEGRAM_CHAT_ID,
        )

        # Initialize AI Brain (Claude-powered market intelligence)
        try:
            from ai_brain import get_ai_brain
            self.ai_brain = get_ai_brain()
            logger.info(f"[{format_ist_timestamp()}] AI Brain initialized "
                        f"({'Claude API connected' if self.ai_brain._enabled else 'rule-based mode'})")
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] AI Brain failed: {e}")

        # Initialize market data store
        try:
            from market_data_store import get_data_store
            self.data_store = get_data_store()
            logger.info(f"[{format_ist_timestamp()}] {self.data_store.get_store_summary()}")
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Data store failed: {e}")

        # Initialize overnight analyzer
        try:
            from overnight_analyzer import get_overnight_analyzer
            self.overnight = get_overnight_analyzer()
            logger.info(f"[{format_ist_timestamp()}] Overnight analyzer ready")
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Overnight analyzer failed: {e}")

        # Initialize economic calendar
        try:
            from economic_calendar import get_calendar
            self.calendar = get_calendar()
            events = self.calendar.get_today_events()
            if events:
                logger.info(f"[{format_ist_timestamp()}] Today's events: "
                            + ", ".join(e['event'] for e in events))
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Calendar failed: {e}")

        # Initialize trade journal (SQLite)
        from trade_journal import get_journal
        self.journal = get_journal()
        logger.info(f"[{format_ist_timestamp()}] Trade journal ready")

        # Initialize self-learning engine
        from self_learning import get_learner
        self.learner = get_learner()
        adaptive_cfg = self.learner.config
        logger.info(
            f"[{format_ist_timestamp()}] Self-learning loaded "
            f"(v{adaptive_cfg.version}, min_score={adaptive_cfg.min_signal_score:.0f}, "
            f"WR history={adaptive_cfg.overall_win_rate:.1f}%)"
        )

        # Apply adaptive thresholds to signal generator
        if self.signal_gen and hasattr(self.signal_gen, 'min_signal_score'):
            self.signal_gen.min_signal_score = adaptive_cfg.min_signal_score

        # Initialize MTF analyzer
        from multi_timeframe import MultiTimeframeAnalyzer
        self.mtf_analyzer = MultiTimeframeAnalyzer()
        logger.info(f"[{format_ist_timestamp()}] MTF analyzer ready")

        # Initialize dashboard (wired to journal)
        from dashboard import PerformanceDashboard
        self.dashboard = PerformanceDashboard(journal=self.journal, alerter=self.alerter)

        # Start Telegram command listener (background thread)
        self._start_telegram_listener()

        # Start continuous learner in background (24/7 learning)
        try:
            from continuous_learner import ContinuousLearner
            self.cont_learner = ContinuousLearner()
            self.cont_learner.start(blocking=False)
            logger.info(f"[{format_ist_timestamp()}] Continuous learner started (background)")
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Continuous learner failed: {e}")

        logger.info(f"[{format_ist_timestamp()}] ✅ Bot initialized successfully")
        return True

    # --------------------------------------------------------
    # MARKET DAY INITIALIZATION
    # --------------------------------------------------------

    def initialize_market_day(self):
        """Called once at market open each day (9:15 AM IST)."""
        if self._day_initialized:
            return

        logger.info(f"[{format_ist_timestamp()}] 🔔 MARKET OPEN — Initializing trading day...")

        # Get live balance from Groww
        balance_info = self.fetcher.get_account_balance()
        available = balance_info.get("available", 0)
        if available == 0 and not config.LIVE_TRADING_ENABLED:
            available = config.MAX_DAILY_CAPITAL  # Use configured capital in dry run

        # Get Nifty opening price
        nifty_q = self.fetcher.get_nifty_quote()
        nifty_open = nifty_q.get("ltp", 0) if nifty_q else 0

        # Initialize risk manager for the day
        self.risk_manager.initialize_day(available, nifty_open)

        # Build watchlist (sector filter from overnight analysis)
        watchlist = self.watchlist_mgr.get_watchlist(
            data_fetcher=self.fetcher, learner=self.learner
        )
        if self.overnight:
            avoid_sectors = self.overnight.get_sectors_to_avoid()
            if avoid_sectors:
                watchlist = [
                    s for s in watchlist
                    if self.watchlist_mgr.get_sector_for_symbol(s) not in avoid_sectors
                ]

        # Morning brief (AI thesis + global cues + events)
        try:
            self.alerter.send_morning_brief(watchlist, available, nifty_open)
            if self.overnight:
                brief = self.overnight.format_morning_brief()
                if self.calendar:
                    brief += "\n" + self.calendar.format_upcoming_events()
                import asyncio
                asyncio.run(self.alerter.send_text(brief))
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Morning brief failed: {e}")

        logger.info(
            f"[{format_ist_timestamp()}] Day initialized | "
            f"Balance: {available} | Nifty: {nifty_open} | "
            f"Watchlist: {len(watchlist)} stocks"
        )
        self._day_initialized = True
        self.market_open_today = True

    # --------------------------------------------------------
    # MAIN TRADING LOOP
    # --------------------------------------------------------

    def run(self):
        """Main bot run loop. Blocks until market close."""
        self.running = True
        logger.info(f"[{format_ist_timestamp()}] Bot running. Waiting for market open...")

        try:
            while self.running:
                now_ist = get_current_ist_time()

                # Daily TOTP token refresh at 8:45 AM IST
                if is_token_refresh_time() and not self._token_refreshed_today:
                    self._do_token_refresh()

                # Reset for new day
                if now_ist.hour == 9 and now_ist.minute < 10:
                    if now_ist.strftime("%Y-%m-%d") != getattr(self, "_last_trade_date", ""):
                        self._day_initialized = False
                        self.eod_done = False
                        self._token_refreshed_today = False
                        self._last_trade_date = now_ist.strftime("%Y-%m-%d")

                # Overnight analysis at 8:00 AM IST (before market)
                if (now_ist.hour == 8 and now_ist.minute < 5
                        and not self._overnight_run_today):
                    self._run_overnight_analysis()

                # Pre-market: build watchlist + holiday check
                if is_pre_market_ist() and not self._day_initialized:
                    # Check if today is a holiday
                    if self.calendar and self.calendar.is_holiday_today():
                        events = self.calendar.get_today_events()
                        holiday = next((e["event"] for e in events if e["impact"] == "HOLIDAY"), "Holiday")
                        logger.info(f"[{format_ist_timestamp()}] NSE Holiday today: {holiday}. Bot idle.")
                        time.sleep(3600)
                        continue
                    logger.info(f"[{format_ist_timestamp()}] Pre-market: preparing watchlist...")
                    self.watchlist_mgr.get_watchlist(
                        data_fetcher=self.fetcher, learner=self.learner
                    )

                # Market is open
                if is_market_open_ist():
                    # Initialize day on first open
                    if not self._day_initialized:
                        self.initialize_market_day()

                    # Check for force square-off time (3:20 PM IST)
                    if should_force_squareoff_ist():
                        if not self.eod_done:
                            self._do_eod_squareoff()
                    # Warning at 3:15 PM IST
                    elif is_squareoff_time_ist():
                        open_pos = self.risk_manager.state.positions
                        if open_pos:
                            logger.warning(
                                f"[{format_ist_timestamp()}] ⏰ 3:15 PM IST — "
                                f"{len(open_pos)} positions still open. Square-off in 5 min!"
                            )
                            self.alerter.send_text(
                                f"⚠️ 3:15 PM IST — Square-off in 5 min! "
                                f"{len(open_pos)} positions open."
                            )
                    else:
                        # Normal trading cycle
                        self._trading_cycle()

                elif self.market_open_today and not self.eod_done:
                    # Market just closed
                    self._do_eod_shutdown()

                else:
                    # Waiting for market
                    mins = minutes_until_market_open()
                    if mins > 0:
                        sleep_secs = min(30, max(5, mins * 30))
                        logger.debug(
                            f"[{format_ist_timestamp()}] Market closed. "
                            f"Opens in {mins:.0f} min. Sleeping {sleep_secs:.0f}s..."
                        )
                        time.sleep(sleep_secs)
                    else:
                        time.sleep(10)
                    continue

                time.sleep(self._scan_interval)

        except KeyboardInterrupt:
            logger.info(f"[{format_ist_timestamp()}] Bot stopped by user (Ctrl+C)")
        finally:
            self._cleanup()

    # --------------------------------------------------------
    # TRADING CYCLE
    # --------------------------------------------------------

    def _trading_cycle(self):
        """
        One full scan cycle:
        1. Update all open positions (trailing stops, SL hits)
        2. Check Nifty circuit breaker
        3. Calendar & VIX blackout check
        4. Scan watchlist for new signals
        5. Execute valid signals
        """
        try:
            # 1. Update open positions (ALWAYS — even if paused)
            self._update_positions()

            # 2. Check Nifty circuit
            nifty_q = self.fetcher.get_nifty_quote()
            if nifty_q:
                self.risk_manager.check_nifty_circuit(nifty_q.get("ltp", 0))

            # 3. Economic calendar blackout check
            if self.calendar:
                blackout, reason = self.calendar.is_blackout_now()
                if blackout:
                    logger.info(f"[{format_ist_timestamp()}] Blackout: {reason}")
                    return

            # 3b. F&O expiry warning
            if self.calendar and self.calendar.is_fno_expiry_today():
                logger.debug(f"[{format_ist_timestamp()}] F&O expiry day — extra caution")

            # 3c. Apply overnight VIX size multiplier
            overnight_mult = 1.0
            if self.overnight:
                overnight_mult = self.overnight.get_size_multiplier()

            # 4. Check if we can take new trades
            if self.risk_manager.state.circuit_breaker_active:
                logger.debug(f"[{format_ist_timestamp()}] Circuit breaker — skipping new signals")
                return

            if self.risk_manager.state.trading_paused:
                logger.debug(f"[{format_ist_timestamp()}] Trading paused — skipping signals")
                return

            # 4. Scan watchlist
            watchlist = self.watchlist_mgr.get_watchlist(
                data_fetcher=self.fetcher, learner=self.learner
            )
            max_new = config.MAX_POSITIONS - len(self.risk_manager.state.positions)
            if max_new <= 0:
                logger.debug(f"[{format_ist_timestamp()}] Max positions reached — no new entries")
                return

            signals = self.signal_gen.scan_watchlist(
                symbols=watchlist,
                max_signals=min(max_new, 3)  # Max 3 new signals per cycle
            )

            # 5. Execute signals
            for signal in signals:
                logger.info(f"[{format_ist_timestamp()}] {signal.summary()}")
                result = self.executor.place_entry_order(signal)
                if result.success:
                    # Send Telegram alert with chart
                    try:
                        df_5m = self.fetcher.get_today_candles(signal.symbol)
                        self.alerter.send_entry_alert(signal, df_5m)
                    except Exception as e:
                        logger.warning(f"Alert failed: {e}")

            # Print dashboard periodically
            if get_current_ist_time().minute % 15 == 0:
                self.dashboard.print_live_dashboard(self.risk_manager)
                self.dashboard.print_positions(self.risk_manager)

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Trading cycle error: {e}")

    # --------------------------------------------------------
    # POSITION MANAGEMENT
    # --------------------------------------------------------

    def _update_positions(self):
        """Update trailing stops and check SL/TP for all open positions."""
        positions = list(self.risk_manager.state.positions.values())
        for pos in positions:
            try:
                quote = self.fetcher.get_quote(pos.symbol)
                if not quote:
                    continue
                ltp = quote.get("ltp", 0)
                action = self.risk_manager.update_trailing_stop(pos, ltp)

                if action["action"] == "EXIT":
                    logger.info(
                        f"[{format_ist_timestamp()}] EXIT signal: {pos.symbol} | "
                        f"{action['reason']}"
                    )
                    result = self.executor.place_exit_order(
                        symbol=pos.symbol,
                        quantity=pos.quantity,
                        direction=pos.direction,
                        reason=action["reason"],
                        use_market_order=True
                    )
                    if result.success:
                        pnl = pos.pnl
                        self.alerter.send_exit_alert(
                            pos.symbol, pos.direction, pos.entry_price,
                            ltp, pos.quantity, pnl, action["reason"]
                        )

                elif action["action"] in ("PARTIAL_EXIT_T1", "PARTIAL_EXIT_T2"):
                    exit_qty = action.get("exit_qty", max(1, pos.quantity // 2))
                    result = self.executor.place_exit_order(
                        pos.symbol, exit_qty, pos.direction,
                        reason=action["reason"]
                    )
                    if result.success:
                        # Update local position quantity
                        pos.quantity = max(0, pos.quantity - exit_qty)
                        self.executor.modify_stop_loss(pos.symbol, action["new_sl"])
                        pnl_partial = (ltp - pos.entry_price) * exit_qty if pos.direction == "LONG" else (pos.entry_price - ltp) * exit_qty
                        logger.info(
                            f"[{format_ist_timestamp()}] {action['action']}: "
                            f"{pos.symbol} {exit_qty}qty @ ₹{ltp:.2f} | "
                            f"Partial P&L: ₹{pnl_partial:.0f}"
                        )
                        try:
                            self.alerter.send_exit_alert(
                                pos.symbol, pos.direction, pos.entry_price,
                                ltp, exit_qty, pnl_partial, action["reason"]
                            )
                        except Exception:
                            pass

                elif action["action"] == "UPDATE_SL":
                    self.executor.modify_stop_loss(pos.symbol, action["new_sl"])

            except Exception as e:
                logger.error(f"Position update error {pos.symbol}: {e}")

    # --------------------------------------------------------
    # EOD
    # --------------------------------------------------------

    def _do_eod_squareoff(self):
        """Force square-off all positions at 3:20 PM IST."""
        if not self.eod_done:
            logger.warning(f"[{format_ist_timestamp()}] 🔴 EOD SQUARE-OFF (3:20 PM IST)")
            self.executor.square_off_all("EOD automatic square-off 3:20 PM IST")
            self.eod_done = True

    def _do_eod_shutdown(self):
        """End-of-day tasks: report, logging, shutdown."""
        logger.info(f"[{format_ist_timestamp()}] Market closed. Running EOD tasks...")
        if not self.eod_done:
            self._do_eod_squareoff()

        # Run self-learning cycle (adapts parameters for tomorrow)
        if self.learner:
            try:
                logger.info(f"[{format_ist_timestamp()}] Running self-learning cycle...")
                self.learner.run_learning_cycle(lookback_days=20)
                logger.info(f"[{format_ist_timestamp()}] {self.learner.get_learning_summary()}")
            except Exception as e:
                logger.error(f"[{format_ist_timestamp()}] Self-learning error: {e}")

        # EOD performance report (sends to Telegram with chart)
        if self.dashboard:
            try:
                report = self.dashboard.generate_eod_report(
                    capital=config.MAX_DAILY_CAPITAL,
                    learner=self.learner
                )
                self.dashboard.print_pattern_table()
                logger.info(f"[{format_ist_timestamp()}] EOD: P&L={report.get('net_pnl',0):+.0f} "
                            f"WR={report.get('win_rate',0):.1f}%")
            except Exception as e:
                logger.error(f"[{format_ist_timestamp()}] EOD report error: {e}")

        self.market_open_today = False
        self.eod_done = True
        logger.info(f"[{format_ist_timestamp()}] Bot EOD complete. Shutting down.")
        self.running = False

    # --------------------------------------------------------
    # OVERNIGHT / PRE-MARKET INTELLIGENCE
    # --------------------------------------------------------

    def _run_overnight_analysis(self):
        """Run global market analysis at 8:00 AM IST. Sets day's trading bias."""
        logger.info(f"[{format_ist_timestamp()}] Running overnight intelligence...")
        try:
            if self.overnight:
                result = self.overnight.run(ai_brain=self.ai_brain)
                bias   = result.get("day_bias", "NEUTRAL")
                score  = result.get("bias_score", 0)
                risks  = result.get("key_risks", [])

                # Log VIX warning
                vix = result.get("vix", {}).get("vix", 15)
                if vix > 20:
                    logger.warning(
                        f"[{format_ist_timestamp()}] HIGH VIX={vix:.1f} today — "
                        "using 50% position sizes"
                    )

                logger.info(
                    f"[{format_ist_timestamp()}] Day bias: {bias} "
                    f"(score={score:+d}, VIX={vix:.1f})"
                )
                if risks:
                    for r in risks:
                        logger.warning(f"[{format_ist_timestamp()}] Risk: {r}")

            self._overnight_run_today = True
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Overnight analysis failed: {e}")
            self._overnight_run_today = True  # Don't retry

    # --------------------------------------------------------
    # TOKEN REFRESH
    # --------------------------------------------------------

    def _do_token_refresh(self):
        """Refresh Groww token at 8:45 AM IST via TOTP."""
        from auth_groww import get_auth_manager
        mgr = get_auth_manager()
        success = mgr.refresh_token_if_needed()
        if success and self.fetcher:
            self.fetcher._refresh_api_if_needed()
            if self.executor:
                self.executor._init_api()
        self._token_refreshed_today = True
        logger.info(f"[{format_ist_timestamp()}] Token refresh {'✅ succeeded' if success else '❌ failed'}")

    # --------------------------------------------------------
    # TELEGRAM COMMAND LISTENER
    # --------------------------------------------------------

    def _start_telegram_listener(self):
        """Start Telegram bot command listener in background thread."""
        if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
            logger.warning(f"[{format_ist_timestamp()}] Telegram not configured — command listener disabled")
            return

        def listener_thread():
            try:
                asyncio.run(self._telegram_listener())
            except Exception as e:
                logger.error(f"Telegram listener error: {e}")

        t = threading.Thread(target=listener_thread, daemon=True)
        t.start()
        logger.info(f"[{format_ist_timestamp()}] Telegram command listener started")

    async def _telegram_listener(self):
        """Async Telegram command handler."""
        try:
            from telegram.ext import Application, CommandHandler

            app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

            async def cmd_kill(update, context):
                if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                    return
                logger.critical(f"[{format_ist_timestamp()}] /kill received!")
                self.risk_manager.emergency_stop()
                self.alerter.send_kill_alert()
                await self.executor.square_off_all("KILL SWITCH by Telegram /kill")

            async def cmd_status(update, context):
                if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                    return
                self.alerter.send_status(self.risk_manager)

            async def cmd_pause(update, context):
                if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                    return
                self.risk_manager._pause_trading("Manual pause via /pause")
                self.alerter.send_text(f"⏸ Trading paused at {format_ist_timestamp()}")

            async def cmd_resume(update, context):
                if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                    return
                self.risk_manager.manual_resume()
                self.alerter.send_text(f"▶️ Trading resumed at {format_ist_timestamp()}")

            async def cmd_watchlist(update, context):
                if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                    return
                status = self.watchlist_mgr.format_watchlist_message()
                self.alerter.send_text(status)

            async def cmd_report(update, context):
                if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                    return
                self.alerter.send_eod_report(self.risk_manager)

            app.add_handler(CommandHandler("kill", cmd_kill))
            app.add_handler(CommandHandler("status", cmd_status))
            app.add_handler(CommandHandler("pause", cmd_pause))
            app.add_handler(CommandHandler("resume", cmd_resume))
            app.add_handler(CommandHandler("watchlist", cmd_watchlist))
            app.add_handler(CommandHandler("report", cmd_report))

            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            # Run indefinitely
            while self.running:
                await asyncio.sleep(1)
            await app.updater.stop()
            await app.stop()

        except Exception as e:
            logger.error(f"Telegram listener exception: {e}")

    # --------------------------------------------------------
    # CLEANUP
    # --------------------------------------------------------

    def _cleanup(self):
        """Graceful shutdown."""
        self.running = False
        logger.info(f"[{format_ist_timestamp()}] Bot cleanup complete.")


# ============================================================
# ENTRY POINT
# ============================================================

def main():
    # Setup IST logging
    setup_logging(
        log_dir=config.LOG_DIR,
        level=config.LOG_LEVEL,
        module_name="kingtrades"
    )

    logger.info("=" * 60)
    logger.info("  NSE MOMENTUM GROWW AI BOT")
    logger.info("  ⚠️  REAL MONEY — LIVE TRADING BOT")
    logger.info(f"  Server: UK (UTC) | Trading: IST (Asia/Kolkata)")
    logger.info(f"  IST Time: {format_ist_timestamp()}")
    logger.info(f"  Live Trading: {'⚡ ENABLED' if config.LIVE_TRADING_ENABLED else '🔒 DISABLED'}")
    logger.info("=" * 60)

    bot = TradingBot()
    if not bot.initialize():
        logger.critical("Bot initialization failed. Exiting.")
        sys.exit(1)

    # Handle system signals gracefully
    def handle_signal(sig, frame):
        logger.info(f"\n[{format_ist_timestamp()}] Signal {sig} received — shutting down...")
        bot.running = False
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    bot.run()


if __name__ == "__main__":
    main()
