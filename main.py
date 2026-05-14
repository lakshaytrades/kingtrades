"""
main.py — US Momentum Alpaca AI Bot
Central Orchestrator with ET Market Hours + Auto-Shutdown + AI Learning

⚠️ WARNING: THIS BOT PLACES REAL ORDERS WITH REAL MONEY ON ALPACA.
⚠️ Server may be UTC — ALL market logic uses ET (America/New_York).
⚠️ Start with LIVE_TRADING_ENABLED=False until confident in the setup.
⚠️ Monitor manually for at least 2 weeks before increasing capital.

Full lifecycle:
  [24/7 Background]   continuous_learner.py — downloads data, learns, adapts
  [08:00 AM ET]       Overnight analysis (global markets, VIX, futures)
  [08:30 AM ET]       Alpaca API validation + full pre-market stock scan
  [08:45 AM ET]       Morning brief → Telegram (watchlist, top picks, events)
  [09:30 AM ET]       Market open → trading begins
  [09:30–10:30 ET]    OPENING DRIVE — most aggressive momentum window
  [10:30–11:30 ET]    Morning session — normal trading
  [11:30–13:30 ET]    MIDDAY CHOP — 50% reduced size / skip
  [13:30–15:30 ET]    Afternoon trend — institutional activity
  [15:45 PM ET]       Square-off warning sent
  [15:50 PM ET]       Force close all positions
  [16:00 PM ET]       EOD shutdown
  [16:30 PM ET]       Self-learning cycle
  [17:00 PM ET]       AI trade review → lessons extracted
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
    setup_logging, is_market_day_ist,
    format_et_timestamp, get_current_et_time,
)
import config

# Broker adapter — Alpaca (US NYSE/NASDAQ)
from broker import (
    get_auth_manager as _broker_get_auth,
    get_data_fetcher  as _broker_get_fetcher,
    get_executor      as _broker_get_executor,
    is_market_open    as _broker_is_market_open,
    is_pre_market     as _broker_is_pre_market,
    is_squareoff_time as _broker_is_squareoff,
    should_force_squareoff as _broker_force_squareoff,
    do_morning_login  as _broker_morning_login,
    CURRENCY_SYMBOL, HEALTH_CHECK_SYMBOL, MARKET_NAME, WATCHLIST as BROKER_WATCHLIST,
)

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
IST = ET   # alias — all IST references now mean ET


class TradingBot:
    """
    US Momentum Alpaca Trading Bot Orchestrator.
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
        self.morning_intel = None       # Morning intelligence — day thesis + mode
        self.profit_engine = None       # Daily profit target + compounding engine
        self.elite_brain   = None       # 12-module signal fusion (Grand Slam detector)
        self.burst_detector = None      # Explosive momentum burst scanner
        self.options_scalper = None     # US options scalping engine (Alpaca only)
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
        self._day_bias_score: int = 0        # overnight bias -100 to +100 (set at market open)

    # --------------------------------------------------------
    # STARTUP
    # --------------------------------------------------------

    def initialize(self) -> bool:
        """Initialize all bot components. Returns True if ready."""
        logger.info(f"[{format_ist_timestamp()}] 🚀 US Momentum Bot initializing...")
        logger.info(f"[{format_ist_timestamp()}] Server time: {datetime.now(IST).astimezone(ZoneInfo('UTC'))} UTC")
        logger.info(f"[{format_ist_timestamp()}] ET time: {format_ist_timestamp()}")

        # Validate config
        issues = config.validate_config()
        if issues:
            for issue in issues:
                logger.warning(f"[{format_ist_timestamp()}] Config warning: {issue}")

        if config.LIVE_TRADING_ENABLED:
            logger.warning(
                f"[{format_ist_timestamp()}] ⚡⚡⚡ LIVE TRADING ENABLED ⚡⚡⚡\n"
                "REAL ORDERS WILL BE PLACED VIA ALPACA. "
                "Capital: $" + str(config.MAX_DAILY_CAPITAL)
            )
        else:
            logger.info(f"[{format_ist_timestamp()}] 🔒 DRY RUN MODE — No real orders")

        # Initialize broker auth (Alpaca: just validates API keys; Groww: TOTP)
        logger.info(f"[{format_ist_timestamp()}] Broker: {MARKET_NAME}")
        self.fetcher = _broker_get_fetcher()

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
        self.executor = _broker_get_executor(
            self.risk_manager,
            live_enabled=config.LIVE_TRADING_ENABLED,
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

        # NSE-only modules — all disabled (Alpaca US mode)
        self.oc_analyzer = self.fii_tracker = self.block_deal_scanner = None
        self.sector_rotation = self.pairs_engine = self.options_signals = None

        # Options Scalping Engine — Alpaca US options
        self.options_scalper = None
        try:
            from options_scalping import get_options_scalping_engine
            self.options_scalper = get_options_scalping_engine(
                live_enabled=config.LIVE_TRADING_ENABLED
            )
            logger.info(f"[{format_ist_timestamp()}] Options Scalping Engine ready (live={config.LIVE_TRADING_ENABLED})")
        except Exception as e:
            self.options_scalper = None
            logger.warning(f"[{format_ist_timestamp()}] Options scalping init failed: {e}")

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

        # Initialize Morning Intelligence engine
        try:
            from morning_intelligence import get_morning_intelligence
            self.morning_intel = get_morning_intelligence(
                fii_tracker     = self.fii_tracker,
                oc_analyzer     = self.oc_analyzer,
                sector_rotation = self.sector_rotation,
                overnight       = self.overnight,
                gap_analyzer    = self.gap_analyzer,
                calendar        = self.calendar,
                news_filter     = self.news_filter,
            )
            logger.info(f"[{format_ist_timestamp()}] Morning Intelligence engine ready")
        except Exception as e:
            self.morning_intel = None
            logger.warning(f"[{format_ist_timestamp()}] Morning Intelligence init failed: {e}")

        # Initialize Daily Profit Engine ($200+ target management + compounding)
        try:
            from daily_profit_engine import get_profit_engine
            self.profit_engine = get_profit_engine()
            logger.info(f"[{format_ist_timestamp()}] Daily Profit Engine ready (target: ${config.DAILY_PROFIT_TARGET:,.0f}/day)")
        except Exception as e:
            self.profit_engine = None
            logger.warning(f"[{format_ist_timestamp()}] Profit engine init failed: {e}")

        # Initialize Elite Brain (12-module signal fusion + Grand Slam detector)
        try:
            from elite_brain import get_elite_brain
            self.elite_brain = get_elite_brain()
            logger.info(f"[{format_ist_timestamp()}] Elite Brain ready — 12-module fusion engine")
        except Exception as e:
            self.elite_brain = None
            logger.warning(f"[{format_ist_timestamp()}] Elite Brain init failed: {e}")

        # Initialize Momentum Burst Detector (explosive 3-5% move scanner)
        try:
            from momentum_burst import get_burst_detector
            self.burst_detector = get_burst_detector()
            logger.info(f"[{format_ist_timestamp()}] Momentum Burst Detector ready")
        except Exception as e:
            self.burst_detector = None
            logger.warning(f"[{format_ist_timestamp()}] Burst detector init failed: {e}")

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

        # Send startup message to Telegram with live balance
        try:
            bal = self.fetcher.get_account_balance() if self.fetcher else {}
            available = bal.get("available", 0)
            mode = "⚡ LIVE TRADING" if config.LIVE_TRADING_ENABLED else "🔒 DRY RUN"
            wl_count = len(config.WATCHLIST)
            self.alerter.send_text(
                f"🚀 <b>KingTrades Bot Started</b>\n"
                f"<code>{format_ist_timestamp()}</code>\n\n"
                f"Mode: <b>{mode}</b>\n"
                f"Balance: <b>${available:,.2f}</b>\n"
                f"Daily Target: <b>${config.DAILY_PROFIT_TARGET:,.0f}</b>\n"
                f"Watchlist: <b>{wl_count} stocks</b>\n\n"
                f"Strategies: MTF + SmartMoney + ProfitMaximizer\n"
                f"Market opens at 9:30 AM ET"
            )
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Startup Telegram message failed: {e}")

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

            # 1. Auth health check
            auth_ok = _broker_morning_login()
            if not auth_ok:
                logger.error(f"[{format_ist_timestamp()}] ❌ API health: Broker auth failed")
                if self.alerter:
                    self.alerter.send_text(
                        f"🚨 API HEALTH FAIL: {MARKET_NAME} auth failed!\n"
                        "Check API credentials in .env"
                    )
                return False

            # 2. Test a live quote fetch
            test_quote = self.fetcher.get_quote(HEALTH_CHECK_SYMBOL)
            if not test_quote or not test_quote.get("ltp"):
                logger.warning(
                    f"[{format_ist_timestamp()}] ⚠️ API health: Quote fetch empty for "
                    f"{HEALTH_CHECK_SYMBOL} — market may not be open yet"
                )
                return True   # Non-fatal

            ltp = test_quote.get("ltp", 0)
            logger.info(
                f"[{format_ist_timestamp()}] ✅ API health OK — "
                f"{HEALTH_CHECK_SYMBOL} LTP: {CURRENCY_SYMBOL}{ltp:.2f}"
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
        """Called once at market open each day (9:30 AM ET)."""
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

        # Get SPY opening price as market reference
        nifty_q = self.fetcher.get_nifty_quote()
        nifty_open = nifty_q.get("ltp", 0) if nifty_q else 0

        # Initialize risk manager for the day
        self.risk_manager.initialize_day(available, nifty_open)

        # Initialize Profit Engine for the day
        try:
            if self.profit_engine:
                daily_target = float(os.getenv("DAILY_PROFIT_TARGET", "200"))
                self.profit_engine.initialize(available, daily_target=daily_target)
                logger.info(
                    f"[{format_ist_timestamp()}] Profit Engine initialized | "
                    f"Balance: ${available:,.2f} | Target: ${daily_target:,.2f}"
                )
                if self.alerter:
                    self.alerter.send_text(
                        f"💰 <b>Daily Target Set: ${daily_target:,.2f}</b>\n"
                        f"Balance: ${available:,.2f} | Buying power: ${available:,.2f}\n"
                        f"🎯 Stretch: ${daily_target*1.5:,.2f} | Max: ${daily_target*2:,.2f}\n"
                        f"Mode: NORMAL — Trading begins now"
                    )
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Profit engine day init failed: {e}")

        # ── Overnight bias_score → opening size multiplier ─────────────
        if self.overnight:
            self._day_bias_score = self.overnight.get_bias_score()
            _bias_tag = (
                "VERY BULLISH" if self._day_bias_score >= 60
                else "BULLISH" if self._day_bias_score >= 30
                else "SLIGHTLY BULLISH" if self._day_bias_score >= 15
                else "VERY BEARISH" if self._day_bias_score <= -60
                else "BEARISH" if self._day_bias_score <= -30
                else "SLIGHTLY BEARISH" if self._day_bias_score <= -15
                else "NEUTRAL"
            )
            logger.info(
                f"[{format_ist_timestamp()}] Overnight bias: {_bias_tag} "
                f"(score={self._day_bias_score:+d})"
            )

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

            # ── Morning Intelligence — full day thesis ────────────────────
            if self.morning_intel:
                try:
                    thesis = self.morning_intel.generate(watchlist)
                    self.alerter.send_text(self.morning_intel.format_telegram(thesis))
                    self.morning_intel.apply_to_risk_manager(thesis, self.risk_manager)
                    logger.info(
                        f"[{format_ist_timestamp()}] Day thesis: {thesis.market_bias} | "
                        f"Mode: {thesis.trading_mode} | Size: {thesis.size_multiplier}x | "
                        f"VIX: {thesis.vix:.1f}"
                    )
                except Exception as e:
                    logger.warning(f"Morning intelligence failed: {e}")
                    # Fallback to basic morning brief
                    self.alerter.send_morning_brief(watchlist, available, nifty_open,
                                                    oc_summary=oc_summary, fii_summary=fii_summary)
            else:
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

            # Send $500 micro-account capital projection once per day at open
            try:
                capital = config.MAX_DAILY_CAPITAL
                self.alerter.send_capital_projection(capital)
            except Exception as _cp_e:
                logger.debug(f"Capital projection send failed: {_cp_e}")
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
            f"Balance: ${available:,.2f} | SPY: ${nifty_open:,.2f} | "
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

                # ── 7:00 AM ET: Pre-market health check + auto-fix ─────────
                if (now_ist.hour == 7 and now_ist.minute < 10
                        and getattr(self, "_health_check_date", "") != today_str):
                    self._health_check_date = today_str
                    self._run_premarket_health_check(today_str)

                # ── 8:30 AM–4:00 PM ET: Auth check + pre-market scan ───────
                # Alpaca key validation (instant, no TOTP needed).
                if (8 * 60 + 30 <= now_mins <= 16 * 60
                        and self._token_refreshed_date != today_str):
                    last_try   = getattr(self, "_last_login_try_ts", None)
                    secs_since = (now_ist - last_try).total_seconds() if last_try else 999
                    if secs_since >= 300:
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

                # Overnight analysis at 8:00 AM ET (before market)
                if (now_ist.hour == 8 and now_ist.minute < 5
                        and not self._overnight_run_today):
                    self._run_overnight_analysis()

                # Pre-market: build watchlist
                if _broker_is_pre_market() and not self._day_initialized:
                    if self.calendar and hasattr(self.calendar, 'is_holiday_today') and self.calendar.is_holiday_today():
                        events = self.calendar.get_today_events()
                        holiday = next((e["event"] for e in events if e["impact"] == "HOLIDAY"), "Holiday")
                        logger.info(f"[{format_ist_timestamp()}] Market holiday today: {holiday}. Bot idle.")
                        time.sleep(3600)
                        continue
                    logger.info(f"[{format_ist_timestamp()}] Pre-market: preparing watchlist for {MARKET_NAME}...")
                    if BROKER_WATCHLIST:
                        # Alpaca: use built-in US watchlist, no dynamic NSE scan needed
                        logger.info(f"[{format_ist_timestamp()}] Using {MARKET_NAME} watchlist ({len(BROKER_WATCHLIST)} symbols)")
                    else:
                        self.watchlist_mgr.get_watchlist(
                            data_fetcher=self.fetcher, learner=self.learner
                        )

                # Market is open
                if _broker_is_market_open():
                    # Initialize day on first open
                    if not self._day_initialized:
                        self.initialize_market_day()

                    # Check for force square-off
                    if _broker_force_squareoff():
                        if not self.eod_done:
                            self._do_eod_squareoff()
                    # Square-off warning
                    elif _broker_is_squareoff():
                        open_pos = self.risk_manager.state.positions
                        if open_pos:
                            logger.warning(
                                f"[{format_ist_timestamp()}] ⏰ Near close — "
                                f"{len(open_pos)} positions still open. Squaring off soon!"
                            )
                            self.alerter.send_text(
                                f"⚠️ Near close — Squaring off! "
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
                            f"{now_ist.strftime('%H:%M')} ET\n"
                            f"Min score required: {dow_min:.0f} | Day: {day_name}\n"
                            f"<i>Bot is running — waiting for quality setups</i>"
                        )
                    except Exception:
                        pass

            # 4c. NSE options signals — disabled (Alpaca mode)
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

            # 4d. Pairs trading — disabled (NSE-only feature)
            if self.pairs_engine and self.pairs_engine.should_scan():
                try:
                    from signal_generator import TradeSignal as _TS
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
                        # Convert each leg to a TradeSignal for live execution
                        for _direction, _sym, _entry in [
                            ("LONG",  ps.symbol_long,  ps.entry_long),
                            ("SHORT", ps.symbol_short, ps.entry_short),
                        ]:
                            if _entry <= 0:
                                continue
                            _sl_pct  = max(0.012, min(0.025, abs(ps.spread_pct) / 100 * 0.5))
                            _t1_pct  = max(0.020, abs(ps.spread_pct) / 100 * (abs(ps.zscore) - ps.target_zscore) / max(abs(ps.zscore), 0.01) * 0.5)
                            _t2_pct  = _t1_pct * 1.5
                            if _direction == "LONG":
                                _sl  = round(_entry * (1 - _sl_pct), 2)
                                _t1  = round(_entry * (1 + _t1_pct), 2)
                                _t2  = round(_entry * (1 + _t2_pct), 2)
                            else:
                                _sl  = round(_entry * (1 + _sl_pct), 2)
                                _t1  = round(_entry * (1 - _t1_pct), 2)
                                _t2  = round(_entry * (1 - _t2_pct), 2)
                            _score  = min(92.0, 70.0 + abs(ps.zscore) * 4 + ps.confidence * 0.1)
                            _grade  = "A+" if _score >= 88 else "A" if _score >= 82 else "B"
                            _mult   = 1.2 if _grade == "A+" else 1.1 if _grade == "A" else 1.0
                            _rr     = round(abs(_t1 - _entry) / max(abs(_sl - _entry), 0.01), 2)
                            _ts = _TS(
                                symbol=_sym, direction=_direction,
                                signal_score=round(_score, 1),
                                entry_price=_entry, stop_loss=_sl,
                                target_1=_t1, target_2=_t2,
                                risk_reward=_rr, atr=round(_entry * 0.01, 2),
                                patterns=["PAIRS_MEAN_REVERSION"],
                                quality_grade=_grade, size_multiplier=_mult,
                                rationale=f"Pairs z={ps.zscore:+.2f} | {ps.reason}",
                                is_high_confidence=_score >= 80,
                            )
                            signals.append(_ts)
                except Exception as e:
                    logger.debug(f"Pairs scan failed: {e}")

            # 4e. Block deal rescan (every 15 min) + immediate signal scan for new buys
            if self.block_deal_scanner and now_ist.minute % 15 == 0:
                try:
                    new_buys = self.block_deal_scanner.scan_and_alert()
                    if new_buys:
                        logger.info(
                            f"[{format_ist_timestamp()}] Block deal: new institutional "
                            f"buy targets — immediate scan: {new_buys}"
                        )
                        # Scan only the hot block-deal stocks right now for signals
                        _bd_signals = self.signal_gen.scan_watchlist(
                            symbols=new_buys, max_signals=len(new_buys)
                        )
                        for _bd_sig in _bd_signals:
                            _bd_sig.rationale = "[BLOCK-DEAL] " + _bd_sig.rationale
                            _bd_sig.size_multiplier = min(
                                _bd_sig.size_multiplier * 1.2, 2.0
                            )
                            signals.append(_bd_sig)
                        if _bd_signals:
                            logger.info(
                                f"[{format_ist_timestamp()}] Block deal immediate scan: "
                                f"{len(_bd_signals)} signal(s) added"
                            )
                except Exception as e:
                    logger.debug(f"Block deal rescan failed: {e}")

            # 4f. Opening Range Breakout (9:31–9:45 AM ET only)
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

            # 4g. Scalping engine (9:30–10:00 AM and 13:30–14:30 ET)
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
                        ts = self.scalping_engine.to_trade_signal(ss)
                        if ts:
                            self.scalping_engine.register_scalp(ss.symbol)
                            signals.append(ts)
                except Exception as e:
                    logger.debug(f"Scalping scan failed: {e}")

            # 4h. Momentum Burst Detector (opening 9:15-10:15 + afternoon 13:30-14:45)
            if self.burst_detector and self.burst_detector.is_burst_time():
                try:
                    burst_setups = self.burst_detector.scan(watchlist[:20], self.fetcher)
                    for bs in burst_setups:
                        burst_ts = self.burst_detector.to_trade_signal(bs)
                        if burst_ts:
                            signals.append(burst_ts)
                            logger.info(
                                f"[{format_ist_timestamp()}] {bs.summary()}"
                            )
                            if self.alerter:
                                self.alerter.send_html(
                                    f"🚀 <b>BURST SIGNAL: {bs.symbol} {bs.direction}</b>\n"
                                    f"Score: {bs.burst_score:.0f}/100 | "
                                    f"RVOL: {bs.rvol:.1f}x\n"
                                    f"Conditions: {', '.join(bs.conditions_met[:4])}\n"
                                    f"Entry: ${bs.entry_price:.2f} | "
                                    f"SL: ${bs.stop_loss:.2f} | "
                                    f"T1: ${bs.target_1:.2f}"
                                )
                except Exception as e:
                    logger.debug(f"Burst scan failed: {e}")

            # 5. Execute signals
            for signal in signals:
                # 5a. Profit Engine gate — check if we should still be trading
                if self.profit_engine:
                    ok, mode = self.profit_engine.should_take_trade()
                    if not ok:
                        logger.info(
                            f"[{format_ist_timestamp()}] Profit Engine BLOCKED — {mode}"
                        )
                        break
                    # Enforce min score from current mode
                    engine_min = self.profit_engine.get_min_signal_score()
                    if signal.signal_score < engine_min:
                        logger.debug(
                            f"[{format_ist_timestamp()}] {signal.symbol}: score "
                            f"{signal.signal_score:.0f} < engine min {engine_min:.0f} "
                            f"(mode: {mode})"
                        )
                        continue
                    # Get optimal capital deployment from profit engine
                    try:
                        deployment = self.profit_engine.get_capital_deployment(
                            quality_grade=signal.quality_grade,
                            signal_score=signal.signal_score,
                            size_multiplier=signal.size_multiplier,
                        )
                        if deployment.blocked:
                            logger.info(
                                f"[{format_ist_timestamp()}] Deployment blocked: "
                                f"{deployment.reason}"
                            )
                            continue
                        # Override size_multiplier based on engine's capital plan
                        # Translate capital_rupees → size multiplier relative to default
                        if self.profit_engine._available_balance > 0:
                            default_pct = config.MAX_CAPITAL_PER_TRADE_PCT / 100
                            engine_pct  = deployment.capital_rupees / max(
                                self.profit_engine._available_balance, 1
                            )
                            signal.size_multiplier = round(
                                engine_pct / max(default_pct, 0.01), 2
                            )
                            signal.size_multiplier = max(0.3, min(signal.size_multiplier, 3.0))
                            logger.info(
                                f"[{format_ist_timestamp()}] {signal.symbol}: "
                                + deployment.reason
                            )
                    except Exception as dep_err:
                        logger.debug(f"Deployment calc: {dep_err}")

                # 5b. Apply FII/DII + overnight bias + directional day bias
                try:
                    fii_mult = 1.0
                    if self.fii_tracker:
                        fii_mult = self.fii_tracker.get_position_size_multiplier()

                    # Directional day-bias multiplier from overnight analysis
                    _bias = self._day_bias_score
                    if _bias >= 60:
                        _dir_mult = 1.25 if signal.direction == "LONG" else 0.75
                    elif _bias >= 30:
                        _dir_mult = 1.15 if signal.direction == "LONG" else 0.85
                    elif _bias >= 15:
                        _dir_mult = 1.08 if signal.direction == "LONG" else 0.92
                    elif _bias <= -60:
                        _dir_mult = 0.65 if signal.direction == "LONG" else 1.25
                    elif _bias <= -30:
                        _dir_mult = 0.80 if signal.direction == "LONG" else 1.15
                    elif _bias <= -15:
                        _dir_mult = 0.92 if signal.direction == "LONG" else 1.08
                    else:
                        _dir_mult = 1.0

                    signal.size_multiplier = round(
                        signal.size_multiplier * fii_mult * overnight_mult * _dir_mult, 2
                    )
                    signal.size_multiplier = max(0.25, min(signal.size_multiplier, 2.0))
                except Exception:
                    pass

                # 5c. Portfolio Heat Guard — sector concentration + correlation check
                try:
                    from portfolio_heat import get_heat_guard
                    _heat_ok, _heat_reason = get_heat_guard().apply_to_signal(
                        signal,
                        open_positions    = self.risk_manager.state.positions,
                        available_capital = self.risk_manager.state.daily_capital,
                    )
                    if not _heat_ok:
                        logger.info(
                            f"[{format_ist_timestamp()}] HEAT GUARD BLOCK: "
                            f"{signal.symbol} — {_heat_reason}"
                        )
                        continue
                    if _heat_reason != "OK":
                        logger.debug(
                            f"[{format_ist_timestamp()}] Heat guard: {signal.symbol} — {_heat_reason}"
                        )
                except Exception as _he:
                    logger.debug(f"Portfolio heat check skipped: {_he}")

                logger.info(f"[{format_ist_timestamp()}] {signal.summary()}")
                result = self.executor.place_entry_order(signal)
                if result.success:
                    # Send Telegram alert with chart
                    try:
                        df_5m = self.fetcher.get_today_candles(signal.symbol)
                        self.alerter.send_entry_alert(signal, df_5m)
                    except Exception as e:
                        logger.warning(f"Alert failed: {e}")

                    # ── Options scalping piggyback on strong stock signals ──
                    if self.options_scalper and result.success:
                        try:
                            bal = self.fetcher.get_account_balance()
                            avail = bal.get("available", 0)
                            opt_action = self.options_scalper.evaluate_stock_signal(
                                symbol           = signal.symbol,
                                direction        = signal.direction,
                                signal_score     = signal.signal_score,
                                entry_price      = signal.entry_price,
                                available_capital= avail,
                                patterns         = [p.name for p in getattr(signal, "patterns_list", [])],
                            )
                            if opt_action:
                                self.options_scalper.execute_options_signal(opt_action)
                        except Exception as _oe:
                            logger.debug(f"Options piggyback failed for {signal.symbol}: {_oe}")

            # ── Standalone UOA scan (every 15 min) ────────────────────────
            if self.options_scalper:
                try:
                    bal   = self.fetcher.get_account_balance()
                    avail = bal.get("available", 0)
                    uoa_signals = self.options_scalper.scan_unusual_activity(
                        symbols           = list(watchlist) if 'watchlist' in dir() else [],
                        available_capital = avail,
                    )
                    if uoa_signals:
                        try:
                            self.alerter.send_options_uoa_alert(
                                [{"symbol": s["underlying"], "type": "CALL" if "CALL" in s["direction"] else "PUT",
                                  "strike": s["contract"].strike, "vol_oi": s.get("uoa_ratio", 0),
                                  "dte": s["dte"]}
                                 for s in uoa_signals]
                            )
                        except Exception:
                            pass
                        for uoa in uoa_signals[:2]:  # max 2 UOA trades per scan
                            self.options_scalper.execute_options_signal(uoa)
                except Exception as _uoa_e:
                    logger.debug(f"UOA scan error: {_uoa_e}")

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
                        # Record closed trade in Profit Engine for compounding/mode tracking
                        if self.profit_engine:
                            try:
                                self.profit_engine.record_trade_closed(
                                    pos.symbol, pnl,
                                    was_partial=pos.t1_done  # avoid double-counting T1
                                )
                            except Exception:
                                pass
                        # Record outcome in Elite Brain for adaptive weight learning
                        if self.elite_brain:
                            try:
                                _eb_votes = getattr(pos, "_elite_module_votes", {})
                                if _eb_votes:
                                    self.elite_brain.record_trade_outcome(_eb_votes, won=pnl > 0)
                            except Exception:
                                pass
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
                            f"{pos.symbol} {exit_qty}qty @ ${ltp:.2f} | "
                            f"Partial P&L: ${pnl_partial:.2f}"
                        )
                        # Notify Profit Engine — triggers compounding activation
                        if self.profit_engine:
                            try:
                                if action["action"] == "PARTIAL_EXIT_T1":
                                    self.profit_engine.record_t1_exit(pos.symbol, pnl_partial)
                                elif action["action"] == "PARTIAL_EXIT_T2":
                                    # T2 exit = additional locked profit, update mode
                                    self.profit_engine.record_trade_closed(
                                        pos.symbol, pnl_partial, was_partial=True
                                    )
                                mode_msg = self.profit_engine.state.mode
                                logger.info(
                                    f"[{format_ist_timestamp()}] Profit Engine "
                                    f"{action['action']} recorded: "
                                    f"${pnl_partial:+.2f} | Mode: {mode_msg} | "
                                    f"Total: ${self.profit_engine.state.realised_pnl:+,.2f}"
                                )
                            except Exception:
                                pass
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
            f"Today ${today_pnl:+,.2f} | "
            f"Week ${data['net_pnl']:+,.2f} ({data['weekly_pct']:+.2f}%) | "
            f"Profitable days: {data['profitable_days']}"
        )
        # Send weekly summary on Friday EOD
        if now.weekday() == 4 and self.alerter:
            try:
                self.alerter.send_text(
                    f"📊 <b>Weekly Summary</b>\n"
                    f"Week: {week}\n"
                    f"Net P&L: ${data['net_pnl']:+,.2f} ({data['weekly_pct']:+.2f}%)\n"
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
                    f"({pos.direction} {pos.quantity}@${pos.entry_price:.2f}) — "
                    f"broker shows no position (SL hit or exchange square-off)"
                )
                try:
                    self.alerter.send_text(
                        f"🔄 <b>Position Auto-Reconciled</b>\n"
                        f"Symbol: <b>{sym}</b>\n"
                        f"Direction: {pos.direction} | Qty: {pos.quantity}\n"
                        f"Entry: ${pos.entry_price:.2f}\n"
                        f"Removed: Broker closed position (SL hit or square-off)\n"
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
                    f"({'LONG' if qty > 0 else 'SHORT'} {abs(qty)}@${avg:.2f}) — "
                    f"found in Groww but not in bot tracker"
                )
                try:
                    self.alerter.send_text(
                        f"🔄 <b>Unknown Position Detected</b>\n"
                        f"Symbol: <b>{sym}</b>\n"
                        f"Direction: {'LONG' if qty > 0 else 'SHORT'} | Qty: {abs(qty)}\n"
                        f"Avg Price: ${avg:.2f}\n"
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
        """Force square-off all positions at 3:20 PM IST / 3:50 PM ET."""
        if not self.eod_done:
            logger.warning(f"[{format_ist_timestamp()}] 🔴 EOD SQUARE-OFF")
            self.executor.square_off_all("EOD automatic square-off")
            # Also close all options positions
            if self.options_scalper:
                try:
                    self.options_scalper.close_all()
                    logger.info(f"[{format_ist_timestamp()}] Options positions closed (EOD)")
                except Exception as e:
                    logger.warning(f"[{format_ist_timestamp()}] Options EOD close failed: {e}")
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
        """Run global market analysis at 8:00 AM ET. Sets day's trading bias."""
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
        Runs at 7:00 AM ET — 2 hours before market open, 1.5 before login.

        Checks everything, auto-fixes what it can, sends Telegram report.
        If unfixable, sends specific instructions so you can act by 9:30 AM.
        """
        logger.info(f"[{format_ist_timestamp()}] 🔍 Pre-market health check ({MARKET_NAME})...")
        issues   = []
        fixed    = []
        critical = []
        login_ok = False

        # ── 1. Broker auth check ─────────────────────────────────────────
        try:
            login_ok = _broker_morning_login()
            if login_ok:
                fixed.append(f"✅ {MARKET_NAME} auth: OK")
                self._token_refreshed_date  = today_str
                self._token_refreshed_today = True
            else:
                critical.append(
                    f"❌ {MARKET_NAME} auth failed\n"
                    f"  Fix: check ALPACA_API_KEY / ALPACA_SECRET_KEY in .env"
                )
        except Exception as e:
            critical.append(f"❌ Auth error: {e}")

        # ── 2. Internet connectivity ─────────────────────────────────────
        try:
            import requests as _req
            api_url = "https://api.alpaca.markets" if MARKET_NAME != "NSE" else "https://api.groww.in"
            r = _req.get(api_url, timeout=5)
            logger.info(f"[{format_ist_timestamp()}] ✅ {MARKET_NAME} API reachable (HTTP {r.status_code})")
        except Exception:
            issues.append(f"⚠️ Cannot reach {MARKET_NAME} API — check VPS internet")

        # ── 3. Memory check ───────────────────────────────────────────────
        try:
            import resource
            mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            if mem_mb > 800:
                issues.append(f"⚠️ High memory: {mem_mb:.0f} MB — consider restart")
        except Exception:
            pass

        # ── Build Telegram report ─────────────────────────────────────────
        now_str = get_current_ist_time().strftime("%d %b %Y")
        if critical:
            lines = [
                f"🚨 <b>Pre-Market Check — ACTION NEEDED</b>",
                f"📅 {now_str} | {MARKET_NAME}",
                "",
                "<b>Critical issues:</b>",
            ] + critical
            if issues:
                lines += ["", "<b>Warnings:</b>"] + issues
            lines += ["", "Bot will keep retrying — fix ASAP."]
        elif issues:
            lines = [
                f"⚠️ <b>Pre-Market Check — Minor Issues</b>",
                f"📅 {now_str} | {MARKET_NAME}",
                "",
            ]
            if fixed:
                lines += ["<b>Auto-fixed:</b>"] + fixed + [""]
            lines += ["<b>Warnings:</b>"] + issues
        else:
            lines = [
                f"✅ <b>Pre-Market Check — All Systems Go</b>",
                f"📅 {now_str} | {MARKET_NAME}",
                "",
            ] + fixed + [
                "",
                f"📈 {MARKET_NAME} market opens soon",
            ]

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
                self.fetcher = _broker_get_fetcher()
                logger.warning(f"[{format_ist_timestamp()}] ♻️ Fetcher reinitialized ({MARKET_NAME})")

            # Reinit executor if dead
            if not self.executor:
                self.executor = _broker_get_executor(
                    self.risk_manager, live_enabled=config.LIVE_TRADING_ENABLED
                )
                logger.warning(f"[{format_ist_timestamp()}] ♻️ Executor reinitialized ({MARKET_NAME})")

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

            # Mid-session auth check (Alpaca: no-op; Groww: re-login if token gone)
            mgr = _broker_get_auth()
            if hasattr(mgr, "refresh_token_if_needed"):
                mgr.refresh_token_if_needed()

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Auto-heal error: {e}")

    # --------------------------------------------------------
    # MORNING LOGIN + PRE-MARKET SCAN (8:30 AM ET)
    # --------------------------------------------------------

    def _do_morning_login_and_scan(self, today_str: str = ""):
        """Morning broker auth check — validates Alpaca API keys + pre-market scan."""
        attempt = getattr(self, "_login_attempt_count", 0) + 1
        setattr(self, "_login_attempt_count", attempt)

        logger.info(f"[{format_ist_timestamp()}] ☀️ Morning broker auth check (attempt #{attempt}) — {MARKET_NAME}")

        success = _broker_morning_login()

        if not success:
            logger.warning(
                f"[{format_ist_timestamp()}] Auth attempt #{attempt} failed — auto-retry in 5 minutes..."
            )
            if attempt in (3, 6, 12) and self.alerter:
                self.alerter.send_text(
                    f"⚠️ {MARKET_NAME} auth failing (attempt #{attempt})\n"
                    f"Check API credentials in .env\n"
                    f"Bot will NOT trade until auth succeeds. Capital is SAFE."
                )
            return

        # Auth succeeded
        self._token_refreshed_date  = today_str
        self._token_refreshed_today = True
        setattr(self, "_login_attempt_count", 0)
        logger.info(f"[{format_ist_timestamp()}] ✅ Morning auth OK — {MARKET_NAME} ready")

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
                f"🔑 Alpaca auth: ✅ API keys",
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
                    lines.append(f"{i}. {arrow} <b>{sym}</b> ${ltp:.2f} ({chg:+.2f}%)")
                lines.append("")

            lines.append("⏰ Market opens 9:30 AM ET — watching for breakout signals")

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
            if self.options_scalper:
                try:
                    self.options_scalper.close_all()
                except Exception:
                    pass

        async def cmd_status(update, context):
            if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                return
            # Inject live balance into status message
            try:
                bal = self.fetcher.get_account_balance() if self.fetcher else {}
                available = bal.get("available", 0)
                used_margin = bal.get("used_margin", 0)
                self.alerter.send_status(self.risk_manager, balance_available=available, margin_used=used_margin)
            except Exception:
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
                                    f"  {icon} {sym}: ${ltp:.2f} "
                                    f"({pos_pnl:+,.2f})"
                                )
                        except Exception:
                            pass

                # ── Margin utilisation % ─────────────────────────────────
                util_pct = (used_margin / total * 100) if total > 0 else 0

                # ── Build HTML message ────────────────────────────────────
                lines = [
                    "💰 <b>Alpaca Account Balance</b>",
                    f"🕐 {now_str}",
                    "",
                    "<b>📊 Live Funds</b>",
                    f"  Available Cash : ${available:,.2f}",
                    f"  Margin Used    : ${used_margin:,.2f}  ({util_pct:.1f}%)",
                ]
                if collateral > 0:
                    lines.append(f"  Collateral     : ${collateral:,.2f}")
                lines += [
                    f"  Opening Balance: ${opening:,.2f}",
                    f"  Total Net Value: ${total:,.2f}",
                    "",
                    "<b>📈 Today's Trading</b>",
                    f"  Realised P&L   : ${daily_pnl:+,.2f}",
                ]
                if open_count > 0:
                    lines += [
                        f"  Unrealised P&L : ${positions_pnl:+,.2f}",
                        f"  Total P&L      : ${daily_pnl + positions_pnl:+,.2f}",
                    ]
                lines += [
                    f"  Trades Today   : {trades_done}",
                    f"  Open Positions : {open_count}",
                    f"  Daily Loss     : {loss_pct:.2f}% of ${daily_loss_limit:,.2f} limit",
                ]
                if positions_lines:
                    lines += ["", "<b>📌 Open Positions</b>"]
                    lines.extend(positions_lines)

                lines += [
                    "",
                    "<b>⚙️ Bot Config</b>",
                    f"  Bot Capital    : ${compounded:,.2f}",
                    f"  Base Capital   : ${config.MAX_DAILY_CAPITAL:,.2f}",
                    f"  Daily Loss Lim : ${daily_loss_limit:,.2f}",
                    f"  Live Trading   : {'✅ ON' if config.LIVE_TRADING_ENABLED else '🔒 OFF'}",
                ]

                # Show cache notice if data is from cache (market closed / API offline)
                if bal.get("_from_cache"):
                    age = bal.get("_cache_age_min", 0)
                    lines += [
                        "",
                        f"⚠️ <i>Live balance unavailable — showing cached data from {age:.0f} min ago.</i>",
                        "<i>Alpaca balance API is only active during market hours (9:30 AM–4:00 PM ET).</i>",
                    ]
                elif available == 0 and not bal.get("_from_cache"):
                    lines += [
                        "",
                        "⚠️ <i>Alpaca returned $0 — balance API may be offline outside market hours.</i>",
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
                args = context.args  # e.g. /capital 500
                custom_capital = float(args[0]) if args else None

                # Show capital_calculator projection for any amount
                try:
                    from capital_calculator import get_telegram_summary
                    cap = custom_capital or config.MAX_DAILY_CAPITAL
                    projection = get_telegram_summary(cap)
                    self.alerter.send_text(projection)
                except Exception as _ce:
                    logger.debug(f"capital_calculator error: {_ce}")

                # Also show NSE compounded growth if available
                if not custom_capital:
                    data = json.loads(self._capital_file.read_text()) if self._capital_file.exists() else {}
                    base = config.MAX_DAILY_CAPITAL
                    compounded = data.get("compounded_capital", base)
                    growth = data.get("total_growth_pct", 0)
                    date = data.get("date", "never")
                    self.alerter.send_text(
                        f"📈 <b>Actual Capital Growth</b>\n"
                        f"Base: ${base:,.0f}\n"
                        f"Current: ${compounded:,.0f}\n"
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

                async def cmd_relogin(update, context):
                    if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                        return
                    await update.message.reply_text(
                        f"🔄 <b>Forcing {MARKET_NAME} re-auth now...</b>",
                        parse_mode="HTML"
                    )
                    try:
                        success = _broker_morning_login()
                        if success:
                            self._token_refreshed_date = get_current_ist_time().strftime("%Y-%m-%d")
                            self._token_refreshed_today = True
                            setattr(self, "_login_attempt_count", 0)
                            await update.message.reply_text(
                                f"✅ <b>{MARKET_NAME} auth successful</b>\n"
                                "Bot is authenticated and ready to trade.",
                                parse_mode="HTML"
                            )
                        else:
                            await update.message.reply_text(
                                f"❌ <b>{MARKET_NAME} auth failed</b>\n\n"
                                "Check .env has correct:\n"
                                "• ALPACA_API_KEY\n"
                                "• ALPACA_SECRET_KEY",
                                parse_mode="HTML"
                            )
                    except Exception as e:
                        await update.message.reply_text(
                            f"❌ Re-auth error: {e}",
                            parse_mode="HTML"
                        )

                app.add_handler(CommandHandler("kill",       cmd_kill))
                app.add_handler(CommandHandler("status",     cmd_status))
                app.add_handler(CommandHandler("pause",      cmd_pause))
                app.add_handler(CommandHandler("resume",     cmd_resume))
                app.add_handler(CommandHandler("watchlist",  cmd_watchlist))
                app.add_handler(CommandHandler("report",     cmd_report))
                app.add_handler(CommandHandler("balance",    cmd_balance))
                app.add_handler(CommandHandler("capital",    cmd_capital))
                app.add_handler(CommandHandler("relogin",    cmd_relogin))

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
        Called once at startup and again each morning at 9:00 AM ET.

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
                        f"base ${base:,.2f} → today ${compounded:,.2f}"
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
                f"${prev:,.2f} + P&L ${today_pnl:+,.2f} = ${new_capital:,.2f}"
            )
        except Exception as e:
            logger.warning(f"Capital save error: {e}")

    # --------------------------------------------------------
    # POSITION SYNC ON RESTART
    # --------------------------------------------------------

    def _sync_positions_from_groww(self):
        """Sync open positions from Alpaca on restart."""
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
                if sym not in self.risk_manager.state.positions:
                    from risk_manager import Position
                    pos = Position(
                        symbol=sym,
                        direction="LONG" if qty > 0 else "SHORT",
                        quantity=abs(qty),
                        entry_price=avg,
                        stop_loss=avg * 0.98,
                        target1=avg * 1.02,
                        target2=avg * 1.04,
                        entry_time=get_current_et_time(),
                    )
                    self.risk_manager.state.positions[sym] = pos
                    synced += 1
            if synced:
                logger.info(f"[{format_ist_timestamp()}] Synced {synced} open position(s) from Alpaca")
                self.alerter.send_text(
                    f"🔄 Bot restarted — synced {synced} open position(s) from Alpaca.\n"
                    "Trailing stops re-applied. Monitoring active."
                )
        except Exception as e:
            logger.warning(f"Position sync error: {e}")

    # --------------------------------------------------------
    # PRE-MARKET TOP PICKS SCAN
    # --------------------------------------------------------

    def _send_premarket_scan(self):
        """
        At 9:25 AM ET: scan full watchlist for the top 5 high-momentum
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
                lines.append(f"{i}. {arrow} <b>{sym}</b> ${ltp:.2f} ({chg:+.2f}%)")
            lines.append("\n⏰ Market opens 9:30 AM ET — watch for breakout confirmation")
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
            bal_line = f"${live_bal:,.2f}" if live_bal > 0 else f"${cap:,.2f} (cached)"
            self.alerter.send_html(
                f"💓 <b>KingTrades Heartbeat</b> — {format_ist_timestamp()}\n"
                f"Status: {status}\n"
                f"Positions: {n_pos} ({pos_symbols})\n"
                f"Day P&amp;L: ${pnl:+,.2f}\n"
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
    logger.info("  US MOMENTUM ALPACA AI BOT")
    logger.info("  ⚠️  REAL MONEY — LIVE TRADING BOT")
    logger.info(f"  Broker: NYSE/NASDAQ | Mode: ALPACA")
    logger.info(f"  ET Time: {format_ist_timestamp()}")
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
