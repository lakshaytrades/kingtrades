"""
main.py — NSE Momentum Groww AI Bot
Central Orchestrator with IST Market Hours + Auto-Shutdown + AI Learning

⚠️ WARNING: THIS BOT PLACES REAL ORDERS WITH REAL MONEY ON GROWW.
⚠️ Server runs in UK (UTC) — ALL market logic uses IST (Asia/Kolkata).
⚠️ Start with LIVE_TRADING_ENABLED=False until confident in the setup.
⚠️ Monitor manually for at least 2 weeks before increasing capital.

Full lifecycle:
  [24/7 Background]   continuous_learner.py — downloads data, learns, adapts
  [08:00 AM IST]      Overnight analysis (global markets, Gift Nifty, VIX)
  [08:30 AM IST]      TOTP login to Groww + full pre-market stock scan
  [08:30 AM IST]      Morning brief → Telegram (watchlist, top picks, events)
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
import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List
from zoneinfo import ZoneInfo

from utils import (
    format_ist_timestamp, get_current_ist_time, is_market_open_ist,
    is_pre_market_ist, should_force_squareoff_ist, is_squareoff_time_ist,
    minutes_until_market_open, minutes_until_market_close,
    setup_logging, is_market_day_ist
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
        self._token_refreshed_date = ""   # "YYYY-MM-DD" — prevents double-refresh
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
        self.oc_analyzer = None   # Option Chain analyzer
        self.fii_tracker = None   # FII/DII flow tracker
        self.gap_analyzer = None  # Pre-market gap analyzer
        self.block_deal_scanner = None  # Institutional block/bulk deal scanner
        self.sector_rotation = None     # Sector momentum rotation engine
        self.pairs_engine = None        # Pairs trading (midday arbitrage)
        self.options_signals = None     # Nifty/BankNifty CE/PE signals
        self.orb_strategy = None        # Opening Range Breakout (9:15-9:45 AM)
        self.scalping_engine = None     # Opening drive scalper (9:15-10:00 AM)
        self._last_trade_date = ""
        self._overnight_run_today = False

        # Automation state
        self._watchlist_cache: List[str] = []
        self._watchlist_cache_time: Optional[datetime] = None
        self._watchlist_cache_ttl = 900       # 15 min cache
        self._last_heartbeat_min = -1          # track heartbeat by minute
        self._token_refresh_attempts = 0
        self._premarket_scan_done = False
        self._capital_file = Path("data/capital.json")
        self._capital_file.parent.mkdir(exist_ok=True)
        self._last_reconcile_time: Optional[datetime] = None  # position reconciliation
        self._weekly_pnl_file = Path(config.WEEKLY_DATA_FILE)
        self._weekly_pnl_file.parent.mkdir(exist_ok=True)
        self._weekly_mode: str = "NORMAL"   # NORMAL / PROTECT / LOCKED

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

        # Apply adaptive thresholds to signal generator (from self-learning)
        if self.signal_gen:
            self.signal_gen.min_score = adaptive_cfg.min_signal_score

        # Apply EOD-trained parameters (from yesterday's walk-forward optimization)
        self._apply_eod_trained_params()

        # Initialize MTF analyzer
        from multi_timeframe import MultiTimeframeAnalyzer
        self.mtf_analyzer = MultiTimeframeAnalyzer()
        logger.info(f"[{format_ist_timestamp()}] MTF analyzer ready")

        # Initialize institutional intelligence modules
        try:
            from option_chain import get_option_chain_analyzer
            self.oc_analyzer = get_option_chain_analyzer()
            logger.info(f"[{format_ist_timestamp()}] Option Chain analyzer ready")
        except Exception as e:
            self.oc_analyzer = None
            logger.warning(f"[{format_ist_timestamp()}] Option Chain init failed: {e}")

        try:
            from fii_dii_tracker import get_fii_dii_tracker
            self.fii_tracker = get_fii_dii_tracker()
            logger.info(f"[{format_ist_timestamp()}] FII/DII tracker ready")
        except Exception as e:
            self.fii_tracker = None
            logger.warning(f"[{format_ist_timestamp()}] FII/DII tracker init failed: {e}")

        try:
            from block_deal_scanner import get_block_deal_scanner
            self.block_deal_scanner = get_block_deal_scanner()
            logger.info(f"[{format_ist_timestamp()}] Block deal scanner ready")
        except Exception as e:
            self.block_deal_scanner = None
            logger.warning(f"[{format_ist_timestamp()}] Block deal scanner init failed: {e}")

        try:
            from sector_rotation import get_sector_rotation_engine
            self.sector_rotation = get_sector_rotation_engine()
            logger.info(f"[{format_ist_timestamp()}] Sector rotation engine ready")
        except Exception as e:
            self.sector_rotation = None
            logger.warning(f"[{format_ist_timestamp()}] Sector rotation init failed: {e}")

        try:
            from pairs_trading import get_pairs_engine
            self.pairs_engine = get_pairs_engine()
            logger.info(f"[{format_ist_timestamp()}] Pairs trading engine ready")
        except Exception as e:
            self.pairs_engine = None
            logger.warning(f"[{format_ist_timestamp()}] Pairs trading init failed: {e}")

        try:
            from options_signals import get_options_signal_generator
            self.options_signals = get_options_signal_generator(oc_analyzer=self.oc_analyzer)
            logger.info(f"[{format_ist_timestamp()}] Options signals generator ready")
        except Exception as e:
            self.options_signals = None
            logger.warning(f"[{format_ist_timestamp()}] Options signals init failed: {e}")

        try:
            from orb_strategy import get_orb_strategy
            self.orb_strategy = get_orb_strategy()
            logger.info(f"[{format_ist_timestamp()}] ORB strategy ready")
        except Exception as e:
            self.orb_strategy = None
            logger.warning(f"[{format_ist_timestamp()}] ORB strategy init failed: {e}")

        try:
            from scalping_engine import get_scalping_engine
            self.scalping_engine = get_scalping_engine()
            logger.info(f"[{format_ist_timestamp()}] Scalping engine ready")
        except Exception as e:
            self.scalping_engine = None
            logger.warning(f"[{format_ist_timestamp()}] Scalping engine init failed: {e}")

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

    def _api_health_check(self) -> bool:
        """
        Verify Groww API is responding before starting the trading session.
        Prevents silent failures where orders appear to place but don't execute.

        Returns True if API is healthy, False if there's a connectivity issue.
        """
        if not config.API_HEALTH_CHECK_ENABLED:
            return True

        try:
            logger.info(f"[{format_ist_timestamp()}] Running API health check...")

            # 1. Check auth token is valid
            from auth_groww import get_auth_manager
            manager = get_auth_manager()
            token = manager.get_valid_token()
            if not token:
                logger.error(
                    f"[{format_ist_timestamp()}] ❌ API health: No valid auth token. "
                    "Run python update_token.py --test to diagnose."
                )
                if self.alerter:
                    self.alerter.send_text(
                        "🚨 API HEALTH FAIL: No valid Groww token at market open!\n"
                        "Bot will NOT trade today. Fix: check GROWW_TOTP_SECRET in .env"
                    )
                return False

            # 2. Test a quote fetch (proves API is connected and token works)
            from data_fetch_groww import get_data_fetcher
            fetcher = get_data_fetcher()
            test_quote = fetcher.get_quote("RELIANCE")
            if not test_quote or not test_quote.get("ltp"):
                logger.warning(
                    f"[{format_ist_timestamp()}] ⚠️ API health: Quote fetch returned "
                    f"empty for RELIANCE. Market may not be open yet or API is slow."
                )
                # Non-fatal — market may be just opening
                return True

            ltp = test_quote.get("ltp", 0)
            logger.info(
                f"[{format_ist_timestamp()}] ✅ API health OK — "
                f"RELIANCE LTP: ₹{ltp:.2f} | Token valid"
            )
            return True

        except Exception as e:
            logger.error(
                f"[{format_ist_timestamp()}] ❌ API health check failed: {e}"
            )
            if self.alerter:
                self.alerter.send_text(
                    f"🚨 API HEALTH FAIL at market open: {e}\n"
                    "Check Groww connectivity and token."
                )
            return False

    def initialize_market_day(self):
        """Called once at market open each day (9:15 AM IST)."""
        if self._day_initialized:
            return

        logger.info(f"[{format_ist_timestamp()}] 🔔 MARKET OPEN — Initializing trading day...")

        # ── API health check — verify connectivity before any trading ──
        api_ok = self._api_health_check()
        if not api_ok:
            logger.warning(
                f"[{format_ist_timestamp()}] ⚠️ API health check failed — "
                "bot will continue but trading may be impaired. Monitor closely."
            )
            # Not fatal — allow the day to proceed but alert was sent

        # Get live balance from Groww; fall back to auto-compounded capital
        balance_info = self.fetcher.get_account_balance()
        available = balance_info.get("available", 0)
        if available == 0:
            available = self._load_compounded_capital()

        # Sync any open positions from Groww (recovery after restart)
        self._sync_positions_from_groww()

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

        # ── Pre-load gap analysis for all watchlist symbols ────────────
        # Gap data is used in Gate 8 of high_accuracy_filter.
        # Load once at open — gaps don't change during the session.
        try:
            from gap_analyzer import get_gap_analyzer
            gap_analyzer = get_gap_analyzer()
            gap_analyzer.clear()   # Fresh data for new day
            gap_analyzer.load_gaps_for_watchlist(self.fetcher, watchlist)
            self.gap_analyzer = gap_analyzer

            # Send gapped stocks summary to Telegram
            gap_summary = gap_analyzer.get_gapped_stocks_summary()
            if "No significant gaps" not in gap_summary:
                logger.info(f"[{format_ist_timestamp()}] {gap_summary}")
                if self.alerter:
                    self.alerter.send_text(gap_summary)
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Gap analysis failed: {e}")
            self.gap_analyzer = None

        # ── Sector rotation: score sectors and filter watchlist ───────
        try:
            if self.sector_rotation:
                sector_summary = self.sector_rotation.format_telegram_summary()
                logger.info(f"[{format_ist_timestamp()}] {sector_summary}")
                if self.alerter:
                    self.alerter.send_text(sector_summary)
                # Concentrate watchlist on hot sectors
                watchlist = self.sector_rotation.filter_watchlist_by_sector(watchlist, top_n=3)
                logger.info(
                    f"[{format_ist_timestamp()}] Sector-filtered watchlist: "
                    f"{len(watchlist)} stocks in top 3 sectors"
                )
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Sector rotation failed: {e}")

        # ── Block deal scan at open ───────────────────────────────────
        try:
            if self.block_deal_scanner:
                buy_symbols = self.block_deal_scanner.scan_and_alert()
                if buy_symbols:
                    # Prepend institutional buy targets to watchlist (highest priority)
                    watchlist = [s for s in buy_symbols if s not in watchlist] + watchlist
                    logger.info(
                        f"[{format_ist_timestamp()}] Block deal buy targets added: {buy_symbols}"
                    )
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Block deal scan failed: {e}")

        # ── Pre-load corporate actions for today ──────────────────────
        try:
            from corporate_actions import get_corp_filter
            corp_filter = get_corp_filter()
            today_events = corp_filter.get_upcoming_events_today()
            if today_events:
                event_text = "⚠️ Corporate actions TODAY:\n" + "\n".join(
                    f"  {e['symbol']}: {e['purpose']}" for e in today_events[:10]
                )
                logger.info(f"[{format_ist_timestamp()}] {event_text}")
                if self.alerter:
                    self.alerter.send_text(event_text)
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Corp actions load failed: {e}")

        # Morning brief (AI thesis + global cues + events + OC + FII/DII)
        try:
            # Gather option chain summary
            oc_summary = ""
            if self.oc_analyzer:
                try:
                    oc_summary = self.oc_analyzer.format_telegram("NIFTY")
                    # Apply OC FII size multiplier to risk manager
                    oc_result = self.oc_analyzer.analyze("NIFTY")
                    if oc_result and self.risk_manager:
                        self.risk_manager.set_institutional_multiplier(
                            oc_mult=1.1 if oc_result.direction_bias == "BULLISH" else (
                                0.9 if oc_result.direction_bias == "BEARISH" else 1.0
                            )
                        )
                except Exception as e:
                    logger.warning(f"OC morning: {e}")

            # Gather FII/DII summary + apply size multiplier
            fii_summary = ""
            if self.fii_tracker:
                try:
                    fii_summary = self.fii_tracker.format_telegram()
                    fii_mult = self.fii_tracker.get_position_size_multiplier()
                    if self.risk_manager:
                        self.risk_manager.set_institutional_multiplier(fii_mult=fii_mult)
                except Exception as e:
                    logger.warning(f"FII/DII morning: {e}")

            # Send combined morning brief
            self.alerter.send_morning_brief(
                watchlist, available, nifty_open,
                oc_summary=oc_summary,
                fii_summary=fii_summary,
            )

            # Also send overnight analysis text
            if self.overnight:
                brief = self.overnight.format_morning_brief()
                if self.calendar:
                    brief += "\n" + self.calendar.format_upcoming_events()
                self.alerter.send_text(brief)
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Morning brief failed: {e}")

        # ── RL Brain: reset daily state at market open ────────────────
        try:
            from rl_agent import LakshKingRL
            LakshKingRL.reset()
            rl_status = LakshKingRL.status_message()
            logger.info(f"[{format_ist_timestamp()}] {rl_status}")
            if self.alerter:
                self.alerter.send_text(rl_status)
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] RL reset failed: {e}")

        # Log day-of-week mode
        now_ist  = get_current_ist_time()
        dow      = now_ist.weekday()
        dow_name = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"][dow]
        dow_mult = config.DOW_SIZE_MULTIPLIERS.get(dow, 1.0)
        dow_min  = config.DOW_MIN_SCORE.get(dow, config.MIN_SIGNAL_SCORE)
        dow_max  = config.DOW_MAX_TRADES.get(dow, config.MAX_TRADES_PER_DAY)
        logger.info(
            f"[{format_ist_timestamp()}] Day initialized | "
            f"Balance: {available} | Nifty: {nifty_open} | "
            f"Watchlist: {len(watchlist)} stocks\n"
            f"  DOW mode: {dow_name} | Size: {dow_mult:.0%} | "
            f"Min score: {dow_min:.0f} | Max trades: {dow_max}"
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

                today_str = now_ist.strftime("%Y-%m-%d")
                now_mins  = now_ist.hour * 60 + now_ist.minute

                # ── 7:00 AM IST: Pre-market health check + auto-fix ───────────
                # Runs before you wake up. Clears stale cache, validates
                # credentials, tests TOTP login. Sends Telegram confirmation
                # or specific fix instructions by 7:05 AM.
                if (now_ist.hour == 7 and now_ist.minute < 10
                        and getattr(self, "_health_check_date", "") != today_str):
                    self._health_check_date = today_str
                    self._run_premarket_health_check(today_str)

                # ── 8:30 AM–4:00 PM IST: TOTP login — retry every 5 min ─────
                # Groww resets tokens at 6:00 AM IST daily.
                # Login at 8:30 AM; retry every 5 min if it fails.
                # Stop at 4:00 PM — no point logging in after market close.
                if (8 * 60 + 30 <= now_mins <= 16 * 60
                        and self._token_refreshed_date != today_str):
                    last_try   = getattr(self, "_last_login_try_ts", None)
                    secs_since = (now_ist - last_try).total_seconds() if last_try else 999
                    if secs_since >= 300:  # retry every 5 minutes
                        self._last_login_try_ts = now_ist
                        self._do_morning_login_and_scan(today_str)

                # ── Reset for new calendar date ────────────────────────────────
                # Token refresh flag resets at midnight (new calendar day),
                # not at 9 AM — so 5:50 AM refresh is seen as "today's refresh"
                if today_str != getattr(self, "_last_trade_date", ""):
                    self._day_initialized = False
                    self.eod_done = False
                    self._token_refreshed_today = False   # legacy compat
                    self._token_refresh_attempts = 0
                    self._premarket_scan_done = False
                    self._watchlist_cache = []
                    self._watchlist_cache_time = None
                    self._last_heartbeat_min  = -1
                    self._last_login_try_ts   = None
                    self._login_attempt_count = 0
                    self._last_trade_date     = today_str
                    logger.info(
                        f"[{format_ist_timestamp()}] 📅 New trading day: {today_str}"
                    )

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
        1. Auto-heal any crashed modules
        2. Update all open positions (trailing stops, SL hits)
        3. Check Nifty circuit breaker
        4. Calendar & VIX blackout check
        5. Scan watchlist for new signals
        6. Execute valid signals
        """
        try:
            # 0. Self-heal — reinit anything that crashed
            self._auto_heal()

            # 1. Update open positions (ALWAYS — even if paused)
            self._update_positions()

            # 1b. Reconcile positions every 10 min (detect server-side SL hits)
            self._reconcile_positions()

            # 1c. Update weekly P&L mode (4-day profit optimizer)
            self._update_weekly_mode()
            if self._weekly_mode == "LOCKED":
                logger.debug(f"[{format_ist_timestamp()}] Weekly target hit — locked to A+ only")
                return

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
            now_ist = get_current_ist_time()
            if self.risk_manager.state.circuit_breaker_active:
                logger.warning(f"[{format_ist_timestamp()}] 🔴 Circuit breaker ACTIVE — no new trades")
                if self.alerter and not getattr(self, "_cb_alerted_hour", None) == now_ist.hour:
                    self._cb_alerted_hour = now_ist.hour
                    self.alerter.send_html(
                        "🔴 <b>Circuit Breaker Active</b>\n"
                        "No new orders will be placed this session.\n"
                        "Send /resume to override."
                    )
                return

            if self.risk_manager.state.trading_paused:
                logger.warning(f"[{format_ist_timestamp()}] ⏸ Trading PAUSED — no new trades")
                return

            # Hourly heartbeat (on the hour, e.g. 9:00, 10:00, 11:00...)
            if now_ist.minute < 2 and now_ist.hour != self._last_heartbeat_min:
                self._send_heartbeat()
                self._last_heartbeat_min = now_ist.hour

            # 4. Scan watchlist (15-min cached)
            watchlist = self._get_watchlist_cached()
            max_new = config.MAX_POSITIONS - len(self.risk_manager.state.positions)
            if max_new <= 0:
                logger.info(f"[{format_ist_timestamp()}] Max positions ({config.MAX_POSITIONS}) reached — no new entries")
                return

            signals = self.signal_gen.scan_watchlist(
                symbols=watchlist,
                max_signals=min(max_new, 3)  # Max 3 new signals per cycle
            )

            # 4b. Apply day-of-week minimum score filter
            dow = now_ist.weekday()
            dow_min = config.DOW_MIN_SCORE.get(dow, config.MIN_SIGNAL_SCORE)
            dow_max_trades = config.DOW_MAX_TRADES.get(dow, config.MAX_TRADES_PER_DAY)
            if self.risk_manager.state.daily_trades >= dow_max_trades:
                logger.info(
                    f"[{format_ist_timestamp()}] DOW max trades "
                    f"({dow_max_trades}) reached for {['Mon','Tue','Wed','Thu','Fri'][dow]}"
                )
                return
            before_filter = len(signals)
            signals = [s for s in signals if s.signal_score >= dow_min]
            if self._weekly_mode == "PROTECT":
                signals = [s for s in signals if s.quality_grade in ("A+", "A")]

            # Alert once per hour if scanned symbols but found nothing
            if before_filter == 0 and self.alerter:
                last_no_sig = getattr(self, "_no_signal_alerted_hour", -1)
                if last_no_sig != now_ist.hour:
                    self._no_signal_alerted_hour = now_ist.hour
                    day_name = ["Mon","Tue","Wed","Thu","Fri"][dow]
                    logger.info(
                        f"[{format_ist_timestamp()}] Scan complete — 0 signals on "
                        f"{len(watchlist)} symbols (min_score={dow_min:.0f}, day={day_name})"
                    )
                    try:
                        self.alerter.send_html(
                            f"📊 <b>Market Scan — No Signals</b>\n"
                            f"Scanned {len(watchlist)} stocks at "
                            f"{now_ist.strftime('%H:%M')} IST\n"
                            f"Min score required: {dow_min:.0f} | Day: {day_name}\n"
                            f"<i>Bot is running — waiting for quality setups</i>"
                        )
                    except Exception:
                        pass

            # 4c. Options signals (Nifty/BankNifty CE/PE) — scan at start of valid windows
            if self.options_signals:
                try:
                    if self.options_signals.is_valid_time():
                        opts = self.options_signals.scan()
                        for opt in opts:
                            if self.alerter:
                                self.alerter.send_text(
                                    self.options_signals.format_telegram_signal(opt)
                                )
                except Exception as e:
                    logger.debug(f"Options signals failed: {e}")

            # 4d. Pairs trading — only during midday (11:00–13:30 IST)
            if self.pairs_engine and self.pairs_engine.should_scan():
                try:
                    pair_signals = self.pairs_engine.scan_pairs(
                        data_fetcher=self.fetcher
                    )
                    for ps in pair_signals:
                        logger.info(
                            f"[{format_ist_timestamp()}] PAIRS: "
                            f"LONG {ps.symbol_long} / SHORT {ps.symbol_short} "
                            f"z={ps.zscore:.2f}"
                        )
                        if self.alerter:
                            self.alerter.send_text(
                                self.pairs_engine.format_telegram_signal(ps)
                            )
                except Exception as e:
                    logger.debug(f"Pairs scan failed: {e}")

            # 4e. Periodic block deal rescan (every 15 min during market hours)
            if self.block_deal_scanner and now_ist.minute % 15 == 0:
                try:
                    new_buys = self.block_deal_scanner.scan_and_alert()
                    if new_buys:
                        logger.info(
                            f"[{format_ist_timestamp()}] New block deal targets: {new_buys}"
                        )
                except Exception as e:
                    logger.debug(f"Block deal rescan failed: {e}")

            # 4f. Opening Range Breakout (9:31–9:45 AM IST only)
            if self.orb_strategy and self.orb_strategy.is_orb_time():
                try:
                    orb_setups = self.orb_strategy.scan_symbols(watchlist, self.fetcher)
                    for setup in orb_setups:
                        orb_sig = self.orb_strategy.to_trade_signal(setup)
                        if orb_sig:
                            signals.append(orb_sig)
                        if self.alerter:
                            self.alerter.send_text(
                                self.orb_strategy.format_telegram_alert(setup)
                            )
                    if orb_setups:
                        logger.info(
                            f"[{format_ist_timestamp()}] ORB: {len(orb_setups)} breakout(s) found"
                        )
                except Exception as e:
                    logger.debug(f"ORB scan failed: {e}")

            # 4g. Scalping engine (9:15–10:00 AM and 13:30–14:30 IST)
            if self.scalping_engine and self.scalping_engine.is_scalp_time():
                try:
                    nifty_chg = 0.0
                    if nifty_q:
                        nifty_chg = nifty_q.get("change_pct", 0.0)
                    scalp_signals = self.scalping_engine.scan(
                        watchlist, self.fetcher, nifty_change_pct=nifty_chg
                    )
                    for ss in scalp_signals:
                        if self.alerter:
                            self.alerter.send_text(
                                self.scalping_engine.format_telegram_alert(ss)
                            )
                        logger.info(
                            f"[{format_ist_timestamp()}] SCALP: {ss.symbol} "
                            f"{ss.direction} | momentum={ss.momentum_pct:+.2f}%"
                        )
                except Exception as e:
                    logger.debug(f"Scalping scan failed: {e}")

            # 5. Execute signals
            for signal in signals:
                # Apply FII/DII institutional size multiplier to signal
                if self.fii_tracker:
                    try:
                        fii_mult = self.fii_tracker.get_position_size_multiplier()
                        signal.size_multiplier = round(
                            signal.size_multiplier * fii_mult * overnight_mult, 2
                        )
                        signal.size_multiplier = max(0.25, min(signal.size_multiplier, 2.0))
                    except Exception:
                        pass

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

                # ── Smart Trade Health Monitor (runs BEFORE trailing stop) ──
                # Fetch last 3 candles for candle-reversal rule (cheap — uses cache)
                recent_candles = None
                try:
                    df_today = self.fetcher.get_today_candles(pos.symbol, interval="5m")
                    if df_today is not None and len(df_today) >= 2:
                        recent_candles = df_today.tail(3).to_dict("records")
                except Exception:
                    pass

                health = self.risk_manager.check_position_health(pos, ltp, recent_candles)

                if health["action"] == "EXIT_NOW":
                    logger.info(
                        f"[{format_ist_timestamp()}] HEALTH EXIT: {pos.symbol} | "
                        f"{health['reason']}"
                    )
                    result = self.executor.place_exit_order(
                        symbol=pos.symbol,
                        quantity=pos.quantity,
                        direction=pos.direction,
                        reason=health["reason"],
                        use_market_order=True,
                    )
                    if result.success:
                        pnl = pos.pnl
                        self.alerter.send_exit_alert(
                            pos.symbol, pos.direction, pos.entry_price,
                            ltp, pos.quantity, pnl, health["reason"]
                        )
                    continue   # skip trailing-stop logic for this position

                elif health["action"] == "BREAK_EVEN":
                    new_sl = health["new_sl"]
                    logger.info(
                        f"[{format_ist_timestamp()}] BREAK-EVEN: {pos.symbol} | "
                        f"{health['reason']}"
                    )
                    pos.stop_loss = new_sl
                    self.executor.modify_stop_loss(pos.symbol, new_sl)
                    # Fall through — let trailing stop logic run normally

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
    # 4-DAY WEEKLY PROFIT OPTIMIZER
    # --------------------------------------------------------

    def _load_weekly_pnl(self) -> dict:
        """Load this week's accumulated P&L from disk."""
        try:
            if self._weekly_pnl_file.exists():
                return json.loads(self._weekly_pnl_file.read_text())
        except Exception:
            pass
        return {"week": "", "net_pnl": 0.0, "days_traded": 0, "profitable_days": 0}

    def _save_weekly_pnl(self, data: dict) -> None:
        try:
            self._weekly_pnl_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.debug(f"Weekly PnL save: {e}")

    def _update_weekly_pnl(self) -> None:
        """Called at EOD — add today's P&L to the weekly tracker."""
        if not self.risk_manager:
            return
        now  = get_current_ist_time()
        week = now.strftime("%Y-W%W")   # e.g. "2026-W15"
        data = self._load_weekly_pnl()
        if data.get("week") != week:
            data = {"week": week, "net_pnl": 0.0, "days_traded": 0, "profitable_days": 0}

        today_pnl = self.risk_manager.state.daily_pnl
        data["net_pnl"]        = round(data["net_pnl"] + today_pnl, 2)
        data["days_traded"]    += 1
        data["profitable_days"] += 1 if today_pnl > 0 else 0
        data["capital"]         = config.MAX_DAILY_CAPITAL
        data["weekly_pct"]      = round(data["net_pnl"] / config.MAX_DAILY_CAPITAL * 100, 2)
        self._save_weekly_pnl(data)

        day_name = ["Mon", "Tue", "Wed", "Thu", "Fri"][now.weekday()]
        logger.info(
            f"[{format_ist_timestamp()}] Weekly tracker [{week}] "
            f"Day {data['days_traded']}/5 ({day_name}): "
            f"Today ₹{today_pnl:+,.0f} | "
            f"Week ₹{data['net_pnl']:+,.0f} ({data['weekly_pct']:+.2f}%) | "
            f"Profitable days: {data['profitable_days']}"
        )
        # Send weekly summary on Friday EOD
        if now.weekday() == 4 and self.alerter:
            try:
                self.alerter.send_text(
                    f"📊 <b>Weekly Summary</b>\n"
                    f"Week: {week}\n"
                    f"Net P&L: ₹{data['net_pnl']:+,.0f} ({data['weekly_pct']:+.2f}%)\n"
                    f"Profitable days: {data['profitable_days']}/5\n"
                    f"Target: 4/5 days 🎯"
                )
            except Exception:
                pass

    def _update_weekly_mode(self) -> None:
        """
        Check weekly P&L and set trading mode:
          NORMAL  — below profit lock target, trade normally
          PROTECT — hit 1.5% weekly → A/A+ only, 50% size via DOW mult
          LOCKED  — hit 2.0% weekly target → no new entries, protect gains
        """
        try:
            data = self._load_weekly_pnl()
            now  = get_current_ist_time()
            week = now.strftime("%Y-W%W")
            if data.get("week") != week:
                self._weekly_mode = "NORMAL"
                return
            pct = data.get("weekly_pct", 0.0)
            # Add today's unrealised P&L
            if self.risk_manager:
                today_pnl  = self.risk_manager.state.daily_pnl
                total_pct  = pct + (today_pnl / max(config.MAX_DAILY_CAPITAL, 1) * 100)
            else:
                total_pct = pct

            if total_pct >= config.WEEKLY_PROFIT_TARGET_PCT:
                if self._weekly_mode != "LOCKED":
                    self._weekly_mode = "LOCKED"
                    logger.info(
                        f"[{format_ist_timestamp()}] 🔒 Weekly profit target "
                        f"{config.WEEKLY_PROFIT_TARGET_PCT}% reached "
                        f"({total_pct:.2f}%) — LOCKED (no new entries)"
                    )
                    if self.alerter:
                        self.alerter.send_text(
                            f"🎯 <b>Weekly Profit Target Hit!</b>\n"
                            f"Week P&L: {total_pct:.2f}% ≥ {config.WEEKLY_PROFIT_TARGET_PCT}%\n"
                            f"Mode: LOCKED — protecting gains, no new entries.\n"
                            f"Existing positions monitored until 3:20 PM."
                        )
            elif total_pct >= config.WEEKLY_PROFIT_LOCK_PCT:
                if self._weekly_mode not in ("PROTECT", "LOCKED"):
                    self._weekly_mode = "PROTECT"
                    logger.info(
                        f"[{format_ist_timestamp()}] 🛡 Weekly profit at "
                        f"{total_pct:.2f}% — PROTECT mode (A/A+ only)"
                    )
                    if self.alerter:
                        self.alerter.send_text(
                            f"🛡 <b>Weekly Protect Mode</b>\n"
                            f"Week P&L: {total_pct:.2f}% — protecting gains.\n"
                            f"Only A/A+ grade trades allowed."
                        )
            elif total_pct <= -config.WEEKLY_LOSS_STOP_PCT:
                if self._weekly_mode != "LOCKED":
                    self._weekly_mode = "LOCKED"
                    logger.warning(
                        f"[{format_ist_timestamp()}] 🛑 Weekly loss limit "
                        f"−{config.WEEKLY_LOSS_STOP_PCT}% hit "
                        f"({total_pct:.2f}%) — LOCKED (no new entries)"
                    )
                    if self.alerter:
                        self.alerter.send_text(
                            f"🛑 <b>Weekly Loss Limit Hit!</b>\n"
                            f"Week P&L: {total_pct:.2f}% ≤ −{config.WEEKLY_LOSS_STOP_PCT}%\n"
                            f"Stopped new entries for the week. Rest and review."
                        )
            else:
                self._weekly_mode = "NORMAL"
        except Exception as e:
            logger.debug(f"Weekly mode check: {e}")

    # --------------------------------------------------------
    # POSITION RECONCILIATION (every 10 min during market hours)
    # --------------------------------------------------------

    def _reconcile_positions(self):
        """
        Sync the bot's local position tracker against Groww's actual positions.
        Runs every 10 minutes during market hours.

        Handles two drift cases:
        1. Bot tracks position but Groww doesn't have it → exchange/server SL hit
           Bot removes the ghost entry and alerts Telegram.
        2. Groww has position the bot doesn't know about → add and monitor it.
        """
        if not self.fetcher or not self.risk_manager:
            return

        now = get_current_ist_time()
        if (self._last_reconcile_time and
                (now - self._last_reconcile_time).total_seconds() < 600):
            return  # Not yet 10 min since last reconcile
        self._last_reconcile_time = now

        try:
            groww_raw = self.fetcher.get_positions()
            if groww_raw is None:
                return

            # Build set of symbols Groww actually holds (non-zero quantity)
            groww_syms: set = {
                str(p.get("symbol", ""))
                for p in groww_raw
                if int(p.get("quantity", 0)) != 0
            }
            bot_syms: set = set(self.risk_manager.state.positions.keys())

            # ── Case 1: ghost positions (bot tracks, Groww doesn't) ──────────
            for sym in list(bot_syms - groww_syms):
                pos = self.risk_manager.state.positions.pop(sym, None)
                if not pos:
                    continue
                # Update daily P&L so the loss/gain is accounted for
                try:
                    q = self.fetcher.get_quote(sym)
                    ltp = float(q.get("ltp", pos.entry_price)) if q else pos.entry_price
                    pnl = (ltp - pos.entry_price) * pos.quantity if pos.direction == "LONG" \
                          else (pos.entry_price - ltp) * pos.quantity
                    self.risk_manager.state.daily_pnl += pnl
                    self.risk_manager.state.available_capital += ltp * pos.quantity
                except Exception:
                    pass

                logger.warning(
                    f"[{format_ist_timestamp()}] Reconcile REMOVED: {sym} "
                    f"({pos.direction} {pos.quantity}@₹{pos.entry_price:.2f}) — "
                    f"Groww shows no position (SL hit or exchange square-off)"
                )
                try:
                    self.alerter.send_text(
                        f"🔄 <b>Position Auto-Reconciled</b>\n"
                        f"Symbol: <b>{sym}</b>\n"
                        f"Direction: {pos.direction} | Qty: {pos.quantity}\n"
                        f"Entry: ₹{pos.entry_price:.2f}\n"
                        f"Removed: Groww closed position (SL hit or square-off)\n"
                        f"Time: {format_ist_timestamp()}"
                    )
                except Exception:
                    pass

            # ── Case 2: unknown positions (Groww has, bot doesn't) ───────────
            for sym in list(groww_syms - bot_syms):
                raw = next((p for p in groww_raw if str(p.get("symbol", "")) == sym), None)
                if not raw:
                    continue
                qty = int(raw.get("quantity", 0))
                avg = float(raw.get("avg_price", 0))
                if qty == 0 or avg == 0:
                    continue
                from risk_manager import Position
                pos = Position(
                    symbol=sym,
                    direction="LONG" if qty > 0 else "SHORT",
                    quantity=abs(qty),
                    entry_price=avg,
                    stop_loss=avg * 0.98,   # 2% fallback SL until ATR calc
                    target1=avg * 1.02,
                    target2=avg * 1.04,
                    entry_time=get_current_ist_time(),
                )
                self.risk_manager.state.positions[sym] = pos
                logger.warning(
                    f"[{format_ist_timestamp()}] Reconcile ADDED: {sym} "
                    f"({'LONG' if qty > 0 else 'SHORT'} {abs(qty)}@₹{avg:.2f}) — "
                    f"found in Groww but not in bot tracker"
                )
                try:
                    self.alerter.send_text(
                        f"🔄 <b>Unknown Position Detected</b>\n"
                        f"Symbol: <b>{sym}</b>\n"
                        f"Direction: {'LONG' if qty > 0 else 'SHORT'} | Qty: {abs(qty)}\n"
                        f"Avg Price: ₹{avg:.2f}\n"
                        f"Added to tracker — monitoring with 2% fallback SL."
                    )
                except Exception:
                    pass

        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Position reconciliation error: {e}")

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

        # Update weekly P&L tracker (4-day profit mode)
        self._update_weekly_pnl()

        # Save compounded capital for tomorrow
        self._save_compounded_capital()

        # Send EOD reports via email + Discord
        try:
            from email_reporter import send_eod_reports
            send_eod_reports(
                journal=self.journal,
                risk_manager=self.risk_manager,
                login_ok=self._token_refreshed_date == get_current_ist_time().strftime("%Y-%m-%d"),
            )
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] EOD email/Discord report: {e}")

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
    # 7:00 AM PRE-MARKET HEALTH CHECK + AUTO-FIX
    # --------------------------------------------------------

    def _run_premarket_health_check(self, today_str: str = ""):
        """
        Runs at 7:00 AM IST — 2 hours before market open, 1.5 before login.

        Checks everything, auto-fixes what it can, sends Telegram report.
        If unfixable, sends specific instructions so you can act by 9:15 AM.
        """
        logger.info(f"[{format_ist_timestamp()}] 🔍 7:00 AM health check running...")
        issues   = []
        fixed    = []
        critical = []

        # ── 1. Check TOTP secret ─────────────────────────────────────────
        totp_secret = os.getenv("GROWW_TOTP_SECRET", "")
        if not totp_secret:
            critical.append(
                "❌ GROWW_TOTP_SECRET missing from .env\n"
                "  Fix: add GROWW_TOTP_SECRET=<base32 secret from groww.in>"
            )
        else:
            try:
                import pyotp
                code = pyotp.TOTP(totp_secret).now()
                if not code or len(code) != 6:
                    critical.append(f"❌ TOTP secret invalid — generated '{code}' (expected 6 digits)")
                else:
                    logger.info(f"[{format_ist_timestamp()}] ✅ TOTP secret valid (test code: {code})")
            except Exception as e:
                critical.append(f"❌ TOTP secret error: {e}")

        # ── 2. Check + auto-fix vendor key ───────────────────────────────
        from auth_groww import (
            get_auth_manager, _extract_vendor_key_from_jwt,
            _is_expired_by_6am_reset, _TOKEN_CACHE_FILE
        )
        mgr = get_auth_manager()

        if not mgr._vendor_key:
            # Try to recover from GROWW_AUTH_TOKEN
            raw = os.getenv("GROWW_AUTH_TOKEN", "")
            if raw:
                vk = _extract_vendor_key_from_jwt(raw)
                if vk:
                    mgr._vendor_key = vk
                    fixed.append(f"✅ Vendor key auto-recovered from GROWW_AUTH_TOKEN: {vk[:8]}...")
                elif len(raw) < 64 and "." not in raw:
                    mgr._vendor_key = raw
                    fixed.append(f"✅ Using GROWW_AUTH_TOKEN as vendor key: {raw[:8]}...")
                else:
                    critical.append(
                        "❌ Cannot extract vendor key from GROWW_AUTH_TOKEN\n"
                        "  Fix: add GROWW_VENDOR_KEY=<key> to .env\n"
                        "  Get it: python3 auth_groww.py --extract-key"
                    )
            else:
                critical.append(
                    "❌ Neither GROWW_VENDOR_KEY nor GROWW_AUTH_TOKEN is set\n"
                    "  Fix: add GROWW_AUTH_TOKEN=<your token from groww.in> to .env"
                )

        # ── 3. Clear stale token cache (expired tokens cause silent failures) ─
        try:
            if _TOKEN_CACHE_FILE.exists():
                import json as _j
                data = _j.loads(_TOKEN_CACHE_FILE.read_text())
                ts_str = data.get("timestamp", "")
                if ts_str:
                    from datetime import datetime
                    from zoneinfo import ZoneInfo
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
                    if _is_expired_by_6am_reset(ts):
                        _TOKEN_CACHE_FILE.unlink()
                        mgr._token           = None
                        mgr._token_timestamp = None
                        fixed.append("✅ Stale token cache cleared (expired at 6 AM reset)")
        except Exception as e:
            issues.append(f"⚠️ Cache check: {e}")

        # ── 4. Live TOTP login test ───────────────────────────────────────
        login_ok = False
        if mgr._vendor_key and totp_secret:
            logger.info(f"[{format_ist_timestamp()}] Testing live TOTP login...")
            try:
                login_ok = mgr.refresh_token_if_needed()
                if login_ok:
                    fixed.append("✅ Live TOTP login test PASSED — token valid for today")
                    # Push fresh token into clients immediately
                    self._reinit_api_clients()
                    # Mark token as refreshed so 8:30 AM block is skipped
                    self._token_refreshed_date  = today_str
                    self._token_refreshed_today = True
                else:
                    issues.append(
                        "⚠️ Live login test failed — will retry at 8:30 AM\n"
                        "  (May be a temporary Groww server issue)"
                    )
            except Exception as e:
                issues.append(f"⚠️ Login test exception: {e}")

        # ── 5. Check internet connectivity ───────────────────────────────
        try:
            import requests as _req
            r = _req.get("https://api.groww.in", timeout=5)
            logger.info(f"[{format_ist_timestamp()}] ✅ Groww API reachable (HTTP {r.status_code})")
        except Exception:
            issues.append("⚠️ Cannot reach api.groww.in — check VPS internet connection")

        # ── 6. Check service memory / uptime ─────────────────────────────
        try:
            import resource
            mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            if mem_mb > 800:
                issues.append(f"⚠️ High memory usage: {mem_mb:.0f} MB — consider restarting")
        except Exception:
            pass

        # ── Build Telegram report ─────────────────────────────────────────
        now_str = get_current_ist_time().strftime("%d %b %Y")
        if critical:
            lines = [
                f"🚨 <b>Pre-Market Check — ACTION NEEDED</b>",
                f"📅 {now_str} | 7:00 AM IST",
                "",
                "<b>Critical issues (fix before 9:15 AM):</b>",
            ]
            for c in critical:
                lines.append(c)
            if issues:
                lines += ["", "<b>Warnings:</b>"]
                for w in issues:
                    lines.append(w)
            lines += [
                "",
                "⏰ Market opens 9:15 AM IST",
                "Bot will keep retrying — fix critical items ASAP.",
            ]
        elif issues and not login_ok:
            lines = [
                f"⚠️ <b>Pre-Market Check — Minor Issues</b>",
                f"📅 {now_str} | 7:00 AM IST",
                "",
            ]
            if fixed:
                lines += ["<b>Auto-fixed:</b>"] + fixed + [""]
            lines += ["<b>Warnings (bot will retry):</b>"] + issues
            lines += ["", "Bot retries login at 8:30 AM — likely fine."]
        else:
            lines = [
                f"✅ <b>Pre-Market Check — All Systems Go</b>",
                f"📅 {now_str} | 7:00 AM IST",
                "",
            ]
            if fixed:
                lines += ["<b>Auto-fixed:</b>"] + fixed + [""]
            lines += [
                "🔑 Groww login: ✅ Ready",
                "🔢 TOTP secret: ✅ Valid",
                "🌐 API reachable: ✅",
                "",
                "⏰ Pre-market scan at 8:30 AM IST",
                "📈 Trading starts at 9:15 AM IST",
            ]

        # Only send Telegram if there's a problem — never wake user for green status
        has_problem = bool(critical) or (issues and not login_ok)
        if has_problem:
            try:
                if self.alerter:
                    self.alerter.send_html("\n".join(lines))
                else:
                    import requests as _req, config as _cfg
                    if _cfg.TELEGRAM_BOT_TOKEN and _cfg.TELEGRAM_CHAT_ID:
                        _req.post(
                            f"https://api.telegram.org/bot{_cfg.TELEGRAM_BOT_TOKEN}/sendMessage",
                            json={"chat_id": _cfg.TELEGRAM_CHAT_ID, "parse_mode": "HTML",
                                  "text": "\n".join(lines)},
                            timeout=10,
                        )
            except Exception as e:
                logger.error(f"[{format_ist_timestamp()}] Health check alert failed: {e}")
        # If all OK — log silently, no Telegram, no wake-up needed
        logger.info(
            f"[{format_ist_timestamp()}] ✅ Health check done — "
            f"{'PROBLEMS FOUND — alerted' if has_problem else 'all systems go (silent)'} | "
            f"fixed={len(fixed)} issues={len(issues)} critical={len(critical)}"
        )

    # --------------------------------------------------------
    # SELF-HEALING: reinit API clients after fresh token
    # --------------------------------------------------------

    def _reinit_api_clients(self):
        """Push fresh token into fetcher and executor. Auto-reinits if crashed."""
        for name, obj, init_fn in [
            ("fetcher",  self.fetcher,  "_init_api"),
            ("executor", self.executor, "_init_api"),
        ]:
            if not obj:
                continue
            for method in ("_refresh_api_if_needed", init_fn):
                fn = getattr(obj, method, None)
                if fn:
                    try:
                        fn()
                        break
                    except Exception as e:
                        logger.debug(f"{name}.{method}(): {e}")

    def _auto_heal(self):
        """
        Called at the start of each trading cycle.
        Reinitializes any module that has crashed or gone None.
        Silently fixes issues without stopping the bot.
        """
        try:
            # Reinit fetcher if dead
            if not self.fetcher:
                from data_fetch_groww import GrowwDataFetcher
                self.fetcher = GrowwDataFetcher()
                logger.warning(f"[{format_ist_timestamp()}] ♻️ Fetcher reinitialized")

            # Reinit executor if dead
            if not self.executor:
                from execution_groww import GrowwExecutor
                self.executor = GrowwExecutor(
                    self.risk_manager,
                    live_enabled=config.LIVE_TRADING_ENABLED
                )
                logger.warning(f"[{format_ist_timestamp()}] ♻️ Executor reinitialized")

            # Reinit signal generator if dead
            if not self.signal_gen:
                from signal_generator import SignalGenerator
                self.signal_gen = SignalGenerator(
                    data_fetcher=self.fetcher,
                    news_filter=self.news_filter,
                    min_signal_score=config.MIN_SIGNAL_SCORE,
                    high_confidence_score=config.HIGH_CONFIDENCE_SCORE,
                )
                logger.warning(f"[{format_ist_timestamp()}] ♻️ Signal generator reinitialized")

            # Reinit alerter if dead
            if not self.alerter:
                from alerts_telegram import TelegramAlerter
                self.alerter = TelegramAlerter(
                    bot_token=config.TELEGRAM_BOT_TOKEN,
                    chat_id=config.TELEGRAM_CHAT_ID,
                )
                logger.warning(f"[{format_ist_timestamp()}] ♻️ Alerter reinitialized")

            # Check if token expired mid-day (edge case: restart between 6-8:30 AM)
            from auth_groww import get_auth_manager
            mgr = get_auth_manager()
            if not mgr.get_valid_token():
                logger.warning(f"[{format_ist_timestamp()}] ♻️ Token missing mid-session — refreshing...")
                mgr.refresh_token_if_needed()
                self._reinit_api_clients()

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Auto-heal error: {e}")

    # --------------------------------------------------------
    # MORNING LOGIN + PRE-MARKET SCAN (8:30 AM IST)
    # --------------------------------------------------------

    def _do_morning_login_and_scan(self, today_str: str = ""):
        """
        8:30 AM IST: TOTP login + full pre-market stock scan.
        Auto-called every 5 minutes until login succeeds.
        No manual action ever needed.
        """
        now_ist  = get_current_ist_time()
        now_mins = now_ist.hour * 60 + now_ist.minute
        attempt  = getattr(self, "_login_attempt_count", 0) + 1
        setattr(self, "_login_attempt_count", attempt)

        logger.info(
            f"[{format_ist_timestamp()}] ☀️ Morning TOTP login attempt #{attempt}..."
        )

        # 1. TOTP login
        try:
            from auth_groww import get_auth_manager
            mgr     = get_auth_manager()
            success = mgr.refresh_token_if_needed()
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Auth error: {e}")
            success = False

        if not success:
            # Keep retrying — do NOT mark as done so the loop retries in 5 min
            logger.warning(
                f"[{format_ist_timestamp()}] Login attempt #{attempt} failed — "
                "auto-retry in 5 minutes..."
            )
            return  # _token_refreshed_date NOT set → loop will retry

        # Login succeeded
        self._token_refreshed_date  = today_str
        self._token_refreshed_today = True
        self._token_refresh_attempts += 1
        setattr(self, "_login_attempt_count", 0)

        # Push fresh token into fetcher and executor
        self._reinit_api_clients()
        logger.info(f"[{format_ist_timestamp()}] ✅ Morning TOTP login successful")

        # 2. Run overnight analysis if not yet done (usually runs at 8:00 AM)
        if not self._overnight_run_today:
            self._run_overnight_analysis()

        # 3. Full pre-market scan + Telegram morning brief
        try:
            watchlist = self.watchlist_mgr.get_watchlist(
                data_fetcher=self.fetcher, learner=self.learner
            )
            logger.info(
                f"[{format_ist_timestamp()}] Pre-market scan: {len(watchlist)} stocks..."
            )

            # Scan top 50 for momentum picks (pre-open prices / yesterday close)
            picks = []
            for sym in watchlist[:50]:
                try:
                    df = self.fetcher.get_candles(sym, interval="5m", days=2)
                    if df is None or len(df) < 20:
                        continue
                    q = self.fetcher.get_quote(sym)
                    if not q:
                        continue
                    chg  = float(q.get("change_pct", 0))
                    vol  = int(q.get("volume", 0))
                    ltp  = float(q.get("ltp", 0))
                    score = abs(chg) * 10 + (1 if vol > 500000 else 0)
                    if abs(chg) >= 0.2:
                        picks.append((sym, chg, ltp, vol, score))
                except Exception:
                    continue

            picks.sort(key=lambda x: x[4], reverse=True)
            top5 = picks[:5]

            # Build Telegram morning brief
            now_ist  = get_current_ist_time()
            dow      = now_ist.weekday()
            dow_name = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"][dow]
            dow_min  = config.DOW_MIN_SCORE.get(dow, config.MIN_SIGNAL_SCORE)
            dow_max  = config.DOW_MAX_TRADES.get(dow, config.MAX_TRADES_PER_DAY)

            lines = [
                f"☀️ <b>KingTrades — Morning Scan Ready</b>",
                f"📅 {now_ist.strftime('%d %b %Y')} | {dow_name}",
                f"🔑 Groww login: ✅ TOTP",
                f"📊 Watchlist: {len(watchlist)} stocks scanned",
                f"",
                f"<b>⚙️ Today's Settings</b>",
                f"Min signal score: {dow_min:.0f}",
                f"Max trades: {dow_max}",
                f"",
            ]

            if top5:
                lines.append("<b>🎯 Pre-Market Movers</b>")
                for i, (sym, chg, ltp, vol, _) in enumerate(top5, 1):
                    arrow = "📈" if chg > 0 else "📉"
                    lines.append(f"{i}. {arrow} <b>{sym}</b> ₹{ltp:.1f} ({chg:+.2f}%)")
                lines.append("")

            lines.append("⏰ Market opens 9:15 AM IST — watching for breakout signals")

            if self.alerter:
                self.alerter.send_html("\n".join(lines))

            self._premarket_scan_done = True
            logger.info(
                f"[{format_ist_timestamp()}] ✅ Pre-market scan done. "
                "Top picks: " + (", ".join(p[0] for p in top5) if top5 else "none yet")
            )
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Pre-market scan error: {e}")

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
        """
        Async Telegram command handler with Conflict retry.
        Render deploys overlap briefly — old + new instance both poll simultaneously,
        causing 409 Conflict. Retry with backoff until old instance dies (~30s).
        """
        import telegram.error as tg_error
        from telegram.ext import Application, CommandHandler

        # ── Command handlers (defined once, reused across retries) ──────
        async def cmd_kill(update, context):
            if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                return
            logger.critical(f"[{format_ist_timestamp()}] /kill received!")
            self.risk_manager.emergency_stop()
            self.alerter.send_kill_alert()
            self.executor.square_off_all("KILL SWITCH by Telegram /kill")

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

        async def cmd_balance(update, context):
            if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                return
            try:
                now_str = format_ist_timestamp()

                # ── Live Groww balance ────────────────────────────────────
                bal = self.fetcher.get_account_balance() if self.fetcher else {}
                available   = bal.get("available",   0)
                used_margin = bal.get("used_margin",  0)
                collateral  = bal.get("collateral",   0)
                total       = bal.get("total",        available + used_margin)
                opening     = bal.get("opening",      total)

                # ── Bot state (use correct RiskState attribute names) ─────
                compounded  = self._load_compounded_capital()
                rm          = self.risk_manager
                daily_pnl   = rm.state.daily_pnl      if rm else 0
                open_count  = len(rm.state.positions)  if rm else 0
                trades_done = rm.state.daily_trades    if rm else 0
                loss_pct    = rm.state.daily_loss_pct  if rm else 0
                daily_loss_limit = config.MAX_DAILY_CAPITAL * config.DAILY_LOSS_LIMIT_PCT / 100

                # ── Open positions live value ─────────────────────────────
                positions_pnl   = 0.0
                positions_lines = []
                if rm and rm.state.positions:
                    for sym, pos in list(rm.state.positions.items()):
                        try:
                            q = self.fetcher.get_quote(sym)
                            ltp = q.get("ltp", 0) if q else 0
                            if ltp and pos.entry_price:
                                mult = 1 if pos.direction in ("LONG", "BUY") else -1
                                pos_pnl = mult * (ltp - pos.entry_price) * pos.quantity
                                positions_pnl += pos_pnl
                                icon = "🟢" if pos_pnl >= 0 else "🔴"
                                positions_lines.append(
                                    f"  {icon} {sym}: ₹{ltp:.1f} "
                                    f"({pos_pnl:+,.0f})"
                                )
                        except Exception:
                            pass

                # ── Margin utilisation % ─────────────────────────────────
                util_pct = (used_margin / total * 100) if total > 0 else 0

                # ── Build HTML message ────────────────────────────────────
                lines = [
                    "💰 <b>Groww Account Balance</b>",
                    f"🕐 {now_str}",
                    "",
                    "<b>📊 Live Funds</b>",
                    f"  Available Cash : ₹{available:,.2f}",
                    f"  Margin Used    : ₹{used_margin:,.2f}  ({util_pct:.1f}%)",
                ]
                if collateral > 0:
                    lines.append(f"  Collateral     : ₹{collateral:,.2f}")
                lines += [
                    f"  Opening Balance: ₹{opening:,.2f}",
                    f"  Total Net Value: ₹{total:,.2f}",
                    "",
                    "<b>📈 Today's Trading</b>",
                    f"  Realised P&L   : ₹{daily_pnl:+,.2f}",
                ]
                if open_count > 0:
                    lines += [
                        f"  Unrealised P&L : ₹{positions_pnl:+,.2f}",
                        f"  Total P&L      : ₹{daily_pnl + positions_pnl:+,.2f}",
                    ]
                lines += [
                    f"  Trades Today   : {trades_done}",
                    f"  Open Positions : {open_count}",
                    f"  Daily Loss     : {loss_pct:.2f}% of ₹{daily_loss_limit:,.0f} limit",
                ]
                if positions_lines:
                    lines += ["", "<b>📌 Open Positions</b>"]
                    lines.extend(positions_lines)

                lines += [
                    "",
                    "<b>⚙️ Bot Config</b>",
                    f"  Bot Capital    : ₹{compounded:,.0f}",
                    f"  Base Capital   : ₹{config.MAX_DAILY_CAPITAL:,.0f}",
                    f"  Daily Loss Lim : ₹{daily_loss_limit:,.0f}",
                    f"  Live Trading   : {'✅ ON' if config.LIVE_TRADING_ENABLED else '🔒 OFF'}",
                ]

                # Show cache notice if data is from cache (market closed / API offline)
                if bal.get("_from_cache"):
                    age = bal.get("_cache_age_min", 0)
                    lines += [
                        "",
                        f"⚠️ <i>Live balance unavailable — showing cached data from {age:.0f} min ago.</i>",
                        "<i>Groww balance API is only active during market hours (9:15 AM–3:30 PM IST).</i>",
                    ]
                elif available == 0 and not bal.get("_from_cache"):
                    lines += [
                        "",
                        "⚠️ <i>Groww returned ₹0 — balance API may be offline outside market hours.</i>",
                        "<i>Balance will update automatically during trading hours.</i>",
                    ]

                self.alerter.send_html("\n".join(lines))

            except Exception as e:
                logger.error(f"[{format_ist_timestamp()}] /balance error: {e}")
                self.alerter.send_text(f"Balance fetch error: {e}")

        async def cmd_capital(update, context):
            if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                return
            try:
                data = json.loads(self._capital_file.read_text()) if self._capital_file.exists() else {}
                base = config.MAX_DAILY_CAPITAL
                compounded = data.get("compounded_capital", base)
                growth = data.get("total_growth_pct", 0)
                date = data.get("date", "never")
                self.alerter.send_text(
                    f"📈 <b>Capital Growth</b>\n"
                    f"Base: ₹{base:,.0f}\n"
                    f"Current: ₹{compounded:,.0f}\n"
                    f"Total Growth: {growth:+.2f}%\n"
                    f"Last Updated: {date}"
                )
            except Exception as e:
                self.alerter.send_text(f"Capital data error: {e}")

        # ── Retry loop — handles Render deployment overlap ───────────────
        max_retries = 15
        retry_delay = 20  # seconds; old instance usually dies within 30s

        # Kill any stale polling session from previous deployment before starting
        try:
            import requests as _req
            _req.get(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
                "/deleteWebhook?drop_pending_updates=true",
                timeout=10,
            )
            logger.info(f"[{format_ist_timestamp()}] Telegram: cleared stale webhook/session")
        except Exception as _e:
            logger.debug(f"deleteWebhook cleanup: {_e}")

        for attempt in range(max_retries):
            app = None
            try:
                app = (
                    Application.builder()
                    .token(config.TELEGRAM_BOT_TOKEN)
                    .connect_timeout(30)
                    .read_timeout(30)
                    .build()
                )

                app.add_handler(CommandHandler("kill",       cmd_kill))
                app.add_handler(CommandHandler("status",     cmd_status))
                app.add_handler(CommandHandler("pause",      cmd_pause))
                app.add_handler(CommandHandler("resume",     cmd_resume))
                app.add_handler(CommandHandler("watchlist",  cmd_watchlist))
                app.add_handler(CommandHandler("report",     cmd_report))
                app.add_handler(CommandHandler("balance",    cmd_balance))
                app.add_handler(CommandHandler("capital",    cmd_capital))

                # Absorb 409 Conflict inside the PTB network loop — prevents crash on deploy
                async def _tg_error_handler(update, context):
                    if isinstance(context.error, tg_error.Conflict):
                        logger.debug("Telegram Conflict absorbed by error handler — still running")
                    else:
                        logger.warning(f"[{format_ist_timestamp()}] TG error: {context.error}")
                app.add_error_handler(_tg_error_handler)

                await app.initialize()
                await app.start()
                await app.updater.start_polling(
                    drop_pending_updates=True,
                    allowed_updates=["message"],
                )
                logger.info(f"[{format_ist_timestamp()}] Telegram polling active")

                while self.running:
                    await asyncio.sleep(1)

                await app.updater.stop()
                await app.stop()
                await app.shutdown()
                return  # Clean exit

            except tg_error.Conflict:
                logger.warning(
                    f"[{format_ist_timestamp()}] Telegram Conflict — previous instance still running. "
                    f"Retry {attempt + 1}/{max_retries} in {retry_delay}s..."
                )
                if app:
                    try:
                        await app.shutdown()
                    except Exception:
                        pass
                await asyncio.sleep(retry_delay)
                retry_delay = min(int(retry_delay * 1.5), 120)

            except Exception as e:
                logger.error(f"[{format_ist_timestamp()}] Telegram listener error: {e}")
                if app:
                    try:
                        await app.shutdown()
                    except Exception:
                        pass
                return

        logger.error(
            f"[{format_ist_timestamp()}] Telegram listener gave up after {max_retries} retries. "
            "Alerts still work — only /commands are unavailable."
        )

    # --------------------------------------------------------
    # EOD-TRAINED ADAPTIVE PARAMETERS
    # --------------------------------------------------------

    def _apply_eod_trained_params(self) -> None:
        """
        Load yesterday's walk-forward optimized params and apply to live trading.
        Called once at startup and again each morning at 9:00 AM IST.

        Updates:
          config.ATR_SL_MULTIPLIER   → better stop-loss distance
          config.ATR_TP_MULTIPLIER   → better target distance
          config.MAX_RISK_PER_TRADE_PCT
          signal_gen.min_signal_score → adaptive entry quality bar
        """
        try:
            from trainer import EODSelfTrainer
            params = EODSelfTrainer.load_params()
            if not params or not params.get("improved", False):
                return  # No trained params or no improvement — keep defaults

            date_str = params.get("date", "?")

            # Apply ATR SL/TP multipliers to config (live override)
            if "atr_sl" in params:
                config.ATR_SL_MULTIPLIER = float(params["atr_sl"])
            if "atr_t1" in params:
                config.ATR_TP_MULTIPLIER = float(params["atr_t1"])
            if "risk_pct" in params:
                # Hard cap at 1% for safety regardless of trainer output
                config.MAX_RISK_PER_TRADE_PCT = min(float(params["risk_pct"]), 1.0)
                if self.risk_manager:
                    self.risk_manager.max_risk_pct = config.MAX_RISK_PER_TRADE_PCT

            # Apply trained min_score to signal generator
            if "min_score" in params and self.signal_gen:
                self.signal_gen.min_score = float(params["min_score"])

            logger.info(
                f"[{format_ist_timestamp()}] ✅ EOD params applied (trained {date_str}): "
                f"ATR_SL={config.ATR_SL_MULTIPLIER}x  "
                f"ATR_T1={config.ATR_TP_MULTIPLIER}x  "
                f"Risk={config.MAX_RISK_PER_TRADE_PCT}%  "
                f"MinScore={params.get('min_score', '?')}  "
                f"Sharpe={params.get('sharpe', 0):.2f}"
            )
            # Notify via Telegram so user sees params being applied
            try:
                self.alerter.send_text(
                    f"🧠 <b>Trained Params Active</b> — {date_str}\n"
                    f"ATR SL: {config.ATR_SL_MULTIPLIER}×  "
                    f"ATR T1: {config.ATR_TP_MULTIPLIER}×\n"
                    f"Risk/trade: {config.MAX_RISK_PER_TRADE_PCT}%  "
                    f"Min score: {params.get('min_score', '?')}\n"
                    f"OOS Sharpe: {params.get('sharpe', 0):.2f}  "
                    f"WR: {params.get('win_rate', 0):.1f}%"
                )
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] EOD param load failed: {e}")

    # --------------------------------------------------------
    # AUTO-COMPOUND CAPITAL
    # --------------------------------------------------------

    def _load_compounded_capital(self) -> float:
        """
        Returns today's capital = base + cumulative net P&L.
        Reads from data/capital.json written each EOD.
        Caps at 5× base to prevent runaway sizing after a lucky streak.
        """
        base = config.MAX_DAILY_CAPITAL
        try:
            if self._capital_file.exists():
                data = json.loads(self._capital_file.read_text())
                compounded = float(data.get("compounded_capital", base))
                cap = base * 5
                compounded = max(base, min(compounded, cap))
                if compounded != base:
                    logger.info(
                        f"[{format_ist_timestamp()}] Auto-compound: "
                        f"base ₹{base:,.0f} → today ₹{compounded:,.0f}"
                    )
                return compounded
        except Exception as e:
            logger.warning(f"Capital load error: {e}")
        return base

    def _save_compounded_capital(self):
        """Save today's P&L to capital.json for tomorrow's compound."""
        try:
            if not self.risk_manager:
                return
            today_pnl = self.risk_manager.state.daily_pnl
            base = config.MAX_DAILY_CAPITAL
            existing = json.loads(self._capital_file.read_text()) if self._capital_file.exists() else {}
            prev = float(existing.get("compounded_capital", base))
            new_capital = max(base * 0.8, prev + today_pnl)  # max 20% drawdown on capital
            self._capital_file.write_text(json.dumps({
                "date": get_current_ist_time().strftime("%Y-%m-%d"),
                "base_capital": base,
                "today_pnl": round(today_pnl, 2),
                "compounded_capital": round(new_capital, 2),
                "total_growth_pct": round((new_capital - base) / base * 100, 2),
            }, indent=2))
            logger.info(
                f"[{format_ist_timestamp()}] Capital saved: "
                f"₹{prev:,.0f} + P&L ₹{today_pnl:+,.0f} = ₹{new_capital:,.0f}"
            )
        except Exception as e:
            logger.warning(f"Capital save error: {e}")

    # --------------------------------------------------------
    # POSITION SYNC ON RESTART
    # --------------------------------------------------------

    def _sync_positions_from_groww(self):
        """
        On startup/restart, read open positions from Groww and register
        them in the risk manager so trailing stops & exits work correctly.
        Called during initialize_market_day() if market is open.
        """
        if not self.fetcher or not self.risk_manager:
            return
        try:
            positions = self.fetcher.get_positions()
            if not positions:
                return
            synced = 0
            for p in positions:
                sym = p.get("symbol", "")
                qty = int(p.get("quantity", 0))
                avg = float(p.get("avg_price", 0))
                if not sym or qty == 0:
                    continue
                # Register in risk manager state so bot tracks them
                if sym not in self.risk_manager.state.positions:
                    from risk_manager import Position
                    pos = Position(
                        symbol=sym,
                        direction="LONG" if qty > 0 else "SHORT",
                        quantity=abs(qty),
                        entry_price=avg,
                        stop_loss=avg * 0.98,   # 2% fallback SL until ATR recalculated
                        target1=avg * 1.02,
                        target2=avg * 1.04,
                        entry_time=get_current_ist_time(),
                    )
                    self.risk_manager.state.positions[sym] = pos
                    synced += 1
            if synced:
                logger.info(
                    f"[{format_ist_timestamp()}] Synced {synced} open position(s) from Groww"
                )
                self.alerter.send_text(
                    f"🔄 Bot restarted — synced {synced} open position(s) from Groww.\n"
                    "Trailing stops re-applied. Monitoring active."
                )
        except Exception as e:
            logger.warning(f"Position sync error: {e}")

    # --------------------------------------------------------
    # PRE-MARKET TOP PICKS SCAN
    # --------------------------------------------------------

    def _send_premarket_scan(self):
        """
        At 9:05 AM IST: scan full watchlist for the top 5 high-momentum
        setups and send a 'Today's Top Picks' Telegram alert.
        """
        if not self.signal_gen or not self.alerter:
            return
        try:
            watchlist = self.watchlist_mgr.get_watchlist(
                data_fetcher=self.fetcher, learner=self.learner
            )
            logger.info(
                f"[{format_ist_timestamp()}] Pre-market scan: {len(watchlist)} stocks..."
            )
            # Quick score scan (limit candle fetches)
            picks = []
            for sym in watchlist[:30]:
                try:
                    df = self.fetcher.get_candles(sym, interval="5m", days=2)
                    if df is None or len(df) < 20:
                        continue
                    q = self.fetcher.get_quote(sym)
                    if not q:
                        continue
                    chg = float(q.get("change_pct", 0))
                    vol = int(q.get("volume", 0))
                    ltp = float(q.get("ltp", 0))
                    # Simple momentum score: abs(change) + volume surge proxy
                    score = abs(chg) * 10 + (1 if vol > 500000 else 0)
                    if abs(chg) >= 0.3:  # Only stocks moving
                        picks.append((sym, chg, ltp, vol, score))
                except Exception:
                    continue
            picks.sort(key=lambda x: x[4], reverse=True)
            top5 = picks[:5]
            if not top5:
                return
            lines = [f"🎯 <b>Pre-Market Top Picks</b> — {get_current_ist_time().strftime('%d %b %Y')}\n"]
            for i, (sym, chg, ltp, vol, _) in enumerate(top5, 1):
                arrow = "📈" if chg > 0 else "📉"
                lines.append(f"{i}. {arrow} <b>{sym}</b> ₹{ltp:.1f} ({chg:+.2f}%)")
            lines.append("\n⏰ Market opens 9:15 AM IST — watch for breakout confirmation")
            self.alerter.send_text("\n".join(lines))
            logger.info(
                f"[{format_ist_timestamp()}] Pre-market picks sent: "
                + ", ".join(p[0] for p in top5)
            )
        except Exception as e:
            logger.warning(f"Pre-market scan error: {e}")

    # --------------------------------------------------------
    # HEARTBEAT
    # --------------------------------------------------------

    def _send_heartbeat(self):
        """Send hourly 'bot alive' status to Telegram during market hours."""
        try:
            if not self.risk_manager or not self.alerter:
                return
            state = self.risk_manager.state
            n_pos = len(state.positions)
            pnl = state.daily_pnl
            # Prefer risk-manager capital (always valid — guarded against 0 override)
            cap = state.available_capital
            # Also show live Groww balance
            live_bal = 0.0
            try:
                bal_info = self.fetcher.get_account_balance() if self.fetcher else {}
                live_bal = bal_info.get("available", 0) if bal_info else 0
            except Exception:
                pass
            status = "🟢 TRADING" if not state.trading_paused else "⏸ PAUSED"
            if state.circuit_breaker_active:
                status = "🔴 CIRCUIT BREAK"
            pos_symbols = ", ".join(state.positions.keys()) if state.positions else "none"
            bal_line = f"₹{live_bal:,.0f}" if live_bal > 0 else f"₹{cap:,.0f} (cached)"
            self.alerter.send_html(
                f"💓 <b>KingTrades Heartbeat</b> — {format_ist_timestamp()}\n"
                f"Status: {status}\n"
                f"Positions: {n_pos} ({pos_symbols})\n"
                f"Day P&amp;L: ₹{pnl:+,.0f}\n"
                f"Available: {bal_line}"
            )
        except Exception as e:
            logger.debug(f"Heartbeat error: {e}")

    # --------------------------------------------------------
    # CACHED WATCHLIST
    # --------------------------------------------------------

    def _get_watchlist_cached(self) -> List[str]:
        """Get watchlist with 15-min cache to avoid excessive API calls."""
        now = get_current_ist_time()
        if (not self._watchlist_cache or
                self._watchlist_cache_time is None or
                (now - self._watchlist_cache_time).total_seconds() > self._watchlist_cache_ttl):
            self._watchlist_cache = self.watchlist_mgr.get_watchlist(
                data_fetcher=self.fetcher, learner=self.learner
            )
            self._watchlist_cache_time = now
        return self._watchlist_cache

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
