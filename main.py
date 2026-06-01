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
        import time as _time_module
        self._start_time = _time_module.time()   # for /health uptime tracking
        self.running = False
        self.market_open_today = False
        self.eod_done = False
        self._token_refreshed_today = False
        self._token_refreshed_date = ""   # "YYYY-MM-DD" — prevents double-refresh
        self._day_initialized = False
        self._scan_interval = 30  # seconds between full watchlist scans (was 60)

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
        self.crypto_engine = None   # 24/7 crypto trading engine (BTC/ETH/SOL)
        self.oc_analyzer = None   # Option Chain analyzer
        self.fii_tracker = None   # FII/DII flow tracker
        self.gap_analyzer = None  # Pre-market gap analyzer
        self.block_deal_scanner = None  # Institutional block/bulk deal scanner
        self.sector_rotation = None     # Sector momentum rotation engine
        self.pairs_engine = None        # Pairs trading (midday arbitrage)
        self.options_signals = None     # US equity options signals (disabled)
        self.orb_strategy = None        # Opening Range Breakout (9:30-9:45 AM ET)
        self.scalping_engine = None     # Opening drive scalper (9:30-10:00 AM ET)
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
        self._mover_watchlist: List[str] = []    # dynamic top-movers added intraday
        self._last_mover_scan: float = 0.0       # timestamp of last top-movers refresh
        self._mover_scan_interval = 3600         # refresh top-movers every 60 min
        self._last_optimizer_reload: float = 0.0 # timestamp of last optimizer config reload
        self._optimizer_reload_interval = 3600   # re-apply optimizer params every 60 min
        self._orb_done_today: bool = False       # ORB scan fired once per day at 9:31-9:45 AM ET
        self._last_state_write: float = 0.0      # timestamp of last terminal dashboard state write

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

        # Initialize broker auth (Alpaca: validates API keys, no TOTP needed)
        logger.info(f"[{format_ist_timestamp()}] Broker: {MARKET_NAME}")
        self.fetcher = _broker_get_fetcher()

        # WebSocket disabled — Alpaca free tier connection limit causes infinite retry spam.
        # Bot uses REST polling for all price data (fully functional, slightly slower).
        self.price_stream = None
        logger.info(f"[{format_ist_timestamp()}] WebSocket disabled — using REST polling for all price data")

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

        # Sync overnight bias to signal generator
        try:
            _ob = getattr(self, 'overnight', None)
            if _ob:
                _overnight_result = _ob.run_analysis() if hasattr(_ob, 'run_analysis') else {}
                if hasattr(self, 'signal_gen') and self.signal_gen:
                    self.signal_gen._overnight_bias = int(_overnight_result.get('bias_score', 0))
        except Exception as _obe:
            logger.debug(f"[suppressed] overnight_bias sync: {_obe}")

        # Initialize economic calendar
        try:
            from economic_calendar import get_calendar
            self.calendar = get_calendar()
            events = self.calendar.get_today_events()
            if events:
                logger.info(f"[{format_ist_timestamp()}] Today's events: "
                            + ", ".join(getattr(e, 'name', str(e)) for e in events))
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

        # Apply self-learning threshold to HighAccuracyFilter
        try:
            if getattr(config, 'SELF_LEARNING_APPLY_THRESHOLD', True):
                from self_learning import get_learner as _get_sl
                _sl_config = _get_sl().config
                _sl_min = getattr(_sl_config, 'min_signal_score', 0)
                if _sl_min >= 65 and hasattr(self, 'signal_gen') and self.signal_gen:
                    self.signal_gen.ha_filter.min_score = _sl_min
                    logger.info(f"[{format_ist_timestamp()}] Self-learning threshold applied: min_score={_sl_min}")
        except Exception as _sle:
            logger.debug(f"[suppressed] self_learning threshold apply: {_sle}")

        # Intraday Adaptive Brain — real-time learning engine
        from adaptive_brain import get_adaptive_brain
        self.adaptive_brain = get_adaptive_brain(self.signal_gen)
        self.adaptive_brain.set_signal_gen(self.signal_gen)
        # Pre-warm brain from historical backtest results (if available)
        try:
            self.adaptive_brain.warm_up_from_history()
        except Exception as _e:
            logger.debug(f"[suppressed] AdaptiveBrain warm_up_from_history: {_e}")
        # Apply yesterday's real trade outcome insights (pattern weights, score, blacklist)
        try:
            self.adaptive_brain.warm_up_from_post_market()
            # Merge post-market symbol blacklist into self-learner so scan loop skips them
            _pm_bl = getattr(getattr(self.adaptive_brain, "_state", None), "blacklisted_symbols", set())
            if _pm_bl and self.signal_gen and hasattr(self.signal_gen, "_learner") and self.signal_gen._learner:
                for _sym in _pm_bl:
                    if _sym not in self.signal_gen._learner.config.symbol_blacklist:
                        self.signal_gen._learner.config.symbol_blacklist.append(_sym)
        except Exception as _e:
            logger.debug(f"[suppressed] AdaptiveBrain warm_up_from_post_market: {_e}")
        logger.info(f"[{format_ist_timestamp()}] AdaptiveBrain ready — {self.adaptive_brain.get_status()}")

        # System health checker
        try:
            from system_health import get_health_checker
            self.health_checker = get_health_checker()
            logger.info(f"[{format_ist_timestamp()}] SystemHealthChecker ready")
        except Exception as _e:
            logger.debug(f"[suppressed] SystemHealthChecker init: {_e}")
            self.health_checker = None

        # Commission tracker
        try:
            from commission_tracker import get_commission_tracker
            self.commission_tracker = get_commission_tracker()
        except Exception as _e:
            logger.debug(f"[suppressed] CommissionTracker init: {_e}")
            self.commission_tracker = None

        # Apply EOD-trained parameters (from yesterday's walk-forward optimization)
        self._apply_eod_trained_params()

        # Apply autonomous optimizer parameters (overrides defaults, targets 13%/month)
        try:
            from autonomous_optimizer import load_optimizer_config, DEFAULTS
            opt_cfg = load_optimizer_config()
            if self.signal_gen:
                new_min = opt_cfg.get("min_score", self.signal_gen.min_score)
                self.signal_gen.min_score = new_min
                if hasattr(self.signal_gen, "ha_filter"):
                    self.signal_gen.ha_filter.min_score = new_min
            if self.risk_manager:
                opt_max_pos = int(opt_cfg.get("max_positions", config.MAX_POSITIONS))
                self.risk_manager.max_positions = opt_max_pos
                opt_risk = opt_cfg.get("risk_per_trade_pct", config.MAX_RISK_PER_TRADE_PCT)
                self.risk_manager.max_risk_pct = float(opt_risk)  # already in percent (e.g. 1.5)
            logger.info(f"[{format_ist_timestamp()}] Autonomous optimizer params applied")
        except Exception as _oe:
            logger.debug(f"[suppressed] Optimizer config: {_oe}")

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

        # Start web dashboard on port 8080
        try:
            from web_dashboard import WebDashboard
            self.web_dashboard = WebDashboard(state_provider=self._get_web_state, port=8080)
            self.web_dashboard.start()
            logger.info(f"[{format_ist_timestamp()}] Web dashboard: http://0.0.0.0:8080")
        except Exception as e:
            self.web_dashboard = None
            logger.warning(f"[{format_ist_timestamp()}] Web dashboard unavailable: {e}")

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

        # ── Crypto Engine — DISABLED (repeated overnight losses, equity focus only) ──
        self.crypto_engine = None
        logger.info(f"[{format_ist_timestamp()}] Crypto Engine disabled — equity-only mode")

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

        # Link risk manager to alerter so exit/entry messages show live win-rate stats
        try:
            if self.alerter and self.risk_manager:
                self.alerter.set_risk_manager(self.risk_manager)
        except Exception:
            pass

        return True

    # --------------------------------------------------------
    # MARKET DAY INITIALIZATION
    # --------------------------------------------------------

    def _api_health_check(self) -> bool:
        """
        Verify Alpaca API is responding before starting the trading session.
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
                    "Check Alpaca connectivity and API keys."
                )
            return False

    def _build_trading_plan(self, available: float) -> dict:
        """
        Compute today's trading plan from live capital and broadcast it via Telegram.

        Capital tiers scale max positions, risk per trade, and daily loss limit so
        the bot never over-sizes relative to account equity.

        Returns a plan dict consumed by initialize_market_day.
        """
        # ── Tier classification ──────────────────────────────────────────────
        if available < 500:
            tier        = "⛔ INSUFFICIENT"
            max_pos     = 0
            risk_pct    = 0.0
            loss_pct    = 0.5
            note        = "Minimum $500 needed — trading disabled today"
        elif available < 2_500:
            tier        = "🟡 SMALL ($500-$2.5K)"
            max_pos     = 8     # 8 concurrent positions — scalping needs volume of trades
            risk_pct    = 1.5   # 1.5% risk per trade — larger reward per winner
            loss_pct    = 2.5   # 2.5% daily loss limit — room for 1 full stop + recovery trades
            note        = "Scalping mode — 1.5:1 R:R, 55% exit at T1, targeting 4%/day"
        elif available < 10_000:
            tier        = "🟢 MEDIUM ($2.5K-$10K)"
            max_pos     = 8      # same shots as SMALL — scalping needs volume of trades
            risk_pct    = 1.2    # scales with capital
            loss_pct    = 2.5
            note        = "Scaling mode — same scalping approach, 4%+/day target"
        elif available < 50_000:
            tier        = "🔵 LARGE ($10K-$50K)"
            max_pos     = 10     # more capital = more simultaneous positions
            risk_pct    = 1.0    # 1% risk, larger $$ per trade
            loss_pct    = 2.5
            note        = "Momentum mode — full signal suite, 4%+/day target"
        else:
            tier        = "💎 INSTITUTIONAL"
            max_pos     = 10
            risk_pct    = 0.5    # controlled — large account still caps risk
            loss_pct    = 2.0
            note        = ""

        # ── Dollar values ────────────────────────────────────────────────────
        risk_per_trade      = available * (risk_pct / 100)
        daily_loss_limit    = available * (loss_pct / 100)
        per_pos_budget      = (available / max_pos) if max_pos > 0 else 0

        # Daily target: fixed override → percentage of balance → fallback floor
        env_target = config.DAILY_PROFIT_TARGET  # 0 = auto-compute
        if env_target > 0:
            daily_target = env_target   # explicit fixed dollar target
        else:
            # Percentage-based: 0.6%/day × balance = 13%/month compounded
            pct_target   = available * config.DAILY_PROFIT_TARGET_PCT / 100
            daily_target = max(pct_target, risk_per_trade * 1.5)  # never below 1.5× one risk unit

        # ── Apply to risk manager ────────────────────────────────────────────
        self.risk_manager.max_risk_pct          = min(risk_pct, 2.0)
        self.risk_manager.daily_loss_limit_pct  = loss_pct
        self.risk_manager.max_positions         = max_pos

        plan = {
            "available":         available,
            "tier":              tier,
            "max_positions":     max_pos,
            "risk_pct":          risk_pct,
            "risk_per_trade":    risk_per_trade,
            "daily_loss_pct":    loss_pct,
            "daily_loss_limit":  daily_loss_limit,
            "per_pos_budget":    per_pos_budget,
            "daily_target":      daily_target,
        }

        logger.info(
            f"[{format_ist_timestamp()}] TRADING PLAN | "
            f"Capital: ${available:,.2f} | Tier: {tier} | "
            f"Positions: {max_pos} | Risk/trade: {risk_pct}% (${risk_per_trade:.2f}) | "
            f"Daily loss limit: {loss_pct}% (${daily_loss_limit:.2f}) | "
            f"Target: ${daily_target:,.2f}"
        )

        # ── Telegram broadcast ───────────────────────────────────────────────
        if self.alerter and available > 0:
            now_str  = get_current_ist_time().strftime("%Y-%m-%d")
            mode_str = "⚡ LIVE" if config.LIVE_TRADING_ENABLED else "🔒 PAPER"

            # Compounding projections — 3 scenarios based on daily target
            # Minimum: 15%/month (5 clean days × 3%), Realistic: 20%, Best: 25%
            _m_min  = config.MONTHLY_TARGET_PCT / 100   # 15% floor
            _m_real = 0.20                               # realistic scalping
            _m_best = 0.25                               # best case (hot streaks)
            min_m1  = available * (1 + _m_min);   min_m12 = available * ((1 + _m_min) ** 12)
            real_m1 = available * (1 + _m_real);  real_m6 = available * ((1 + _m_real) ** 6)
            real_m12= available * ((1 + _m_real) ** 12)
            best_m12= available * ((1 + _m_best) ** 12)

            lines = [
                f"📊 <b>TRADING PLAN — {now_str}</b>",
                "",
                f"💰 <b>Capital:</b>         ${available:,.2f}",
                f"📊 <b>Tier:</b>            {tier}",
                f"🔧 <b>Mode:</b>            {mode_str}",
                "",
                f"📈 <b>Max positions:</b>   {max_pos}",
                f"💼 <b>Budget/position:</b> ${per_pos_budget:,.2f}",
                "",
                f"⚠️  <b>Risk per trade:</b>  ${risk_per_trade:.2f}  ({risk_pct}%)",
                f"🛑 <b>Daily stop-out:</b>  ${daily_loss_limit:.2f}  ({loss_pct}%)",
                f"🎯 <b>Daily target:</b>    ${daily_target:,.2f}  ({config.DAILY_PROFIT_TARGET_PCT:.1f}%)",
                "",
                f"📈 <b>COMPOUNDING PROJECTIONS (daily auto-reinvest):</b>",
                f"   Minimum  15%/mo: 1m <b>${min_m1:,.0f}</b> → 12m <b>${min_m12:,.0f}</b>",
                f"   Realistic 20%/mo: 1m <b>${real_m1:,.0f}</b> → 6m <b>${real_m6:,.0f}</b> → 12m <b>${real_m12:,.0f}</b>",
                f"   Best case 25%/mo: 12m <b>${best_m12:,.0f}</b>",
                f"   ⚡ Balance auto-reads from Alpaca daily — compounds every session",
            ]
            if note:
                lines += ["", f"ℹ️ {note}"]
            self.alerter.send_text("\n".join(lines))

        return plan

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

        # ── Step 1: Fetch live capital ───────────────────────────────────────
        balance_info = self.fetcher.get_account_balance()
        available = balance_info.get("available", 0)
        if available == 0:
            available = self._load_compounded_capital()

        # ── Step 2: Build capital-based trading plan (sets risk params + Telegram) ──
        plan = self._build_trading_plan(available)
        daily_target = plan["daily_target"]

        # ── Step 3: Sync open positions from broker (restart recovery) ──────
        self._sync_positions_from_groww()

        # ── Step 4: Get SPY opening price as market reference ────────────────
        spy_q = self.fetcher.get_nifty_quote()
        spy_open = spy_q.get("ltp", 0) if spy_q else 0

        # ── Step 5: Initialize risk manager for the day ──────────────────────
        self.risk_manager.initialize_day(available, spy_open)

        # ── Step 6: Initialize Profit Engine with plan's daily target ────────
        try:
            if self.profit_engine:
                self.profit_engine.initialize(available, daily_target=daily_target)
                logger.info(
                    f"[{format_ist_timestamp()}] Profit Engine initialized | "
                    f"Balance: ${available:,.2f} | Target: ${daily_target:,.2f}"
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

        # ── Sector rotation: score sectors and prioritize hot-sector stocks ──
        try:
            if self.sector_rotation:
                try:
                    sector_summary = self.sector_rotation.format_telegram_summary()
                except AttributeError:
                    sector_summary = ""
                try:
                    # Prefer new get_hot_stocks API (US SPDR version)
                    watchlist = self.sector_rotation.get_hot_stocks(watchlist, max_symbols=40)
                    logger.info(
                        f"[{format_ist_timestamp()}] Sector-prioritized watchlist: "
                        f"{len(watchlist)} stocks (hot sectors first)"
                    )
                except AttributeError:
                    # Fallback to old NSE method signature — top_n=40 to match main path
                    watchlist = self.sector_rotation.filter_watchlist_by_sector(watchlist, top_n=40)
                if sector_summary:
                    logger.info(f"[{format_ist_timestamp()}] {sector_summary}")
                    if self.alerter:
                        self.alerter.send_text(sector_summary)
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Sector rotation failed: {e}")

        # ── New US global market context morning brief ────────────────
        try:
            from global_market_context import get_global_market_context
            _gmc = get_global_market_context(self.fetcher)
            _gmc.refresh()
            _morning_brief = _gmc.format_morning_brief()
            logger.info(f"[{format_ist_timestamp()}] {_gmc.format_one_line()}")
            if self.alerter:
                self.alerter.send_text(_morning_brief)
        except Exception as _gmc_e:
            logger.debug(f"Global market morning brief failed: {_gmc_e}")

        # ── Futures Bias — pre-market ES/NQ direction (top-1% edge) ──────────
        try:
            if getattr(config, "FUTURES_BIAS_ENABLED", True):
                from futures_bias import refresh_bias
                _fbias = refresh_bias()
                logger.info(
                    f"[{format_ist_timestamp()}] Futures bias: {_fbias.bias} | "
                    f"SPY={_fbias.spy_chg:+.2f}% QQQ={_fbias.qqq_chg:+.2f}% "
                    f"composite={_fbias.composite_chg:+.2f}% | size_mult={_fbias.size_mult:.2f}x"
                )
                if self.alerter:
                    self.alerter.send_text(_fbias.format_telegram())
                # Apply futures size multiplier to risk manager opening session size
                if self.risk_manager and hasattr(self.risk_manager, "set_session_size_mult"):
                    try:
                        self.risk_manager.set_session_size_mult(_fbias.size_mult)
                    except Exception:
                        pass
        except Exception as _fbe:
            logger.debug(f"Futures bias morning failed: {_fbe}")

        # ── Economic calendar morning check ──────────────────────────
        try:
            from economic_calendar import get_economic_calendar
            _cal = get_economic_calendar()
            _cal_brief = _cal.format_telegram_brief()
            logger.info(f"[{format_ist_timestamp()}] Calendar: {_cal_brief[:100]}")
            if self.alerter and ("BLOCKED" in _cal_brief or "event" in _cal_brief.lower()):
                self.alerter.send_text(_cal_brief)
        except Exception as _cal_e:
            logger.debug(f"Economic calendar morning check failed: {_cal_e}")

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

                # Apply morning intelligence sizing factor
                try:
                    if getattr(config, 'MORNING_INTEL_SIZING', True):
                        from utils import get_current_ist_time as _get_et
                        _now_h = _get_et().hour + _get_et().minute / 60
                        _thesis = getattr(self.morning_intel, '_last_thesis', None) or getattr(self.morning_intel, 'thesis', None)
                        if _thesis and _now_h < 10.5:   # only apply before 10:30 AM ET
                            _day_size = float(getattr(_thesis, 'size_multiplier', 1.0) or 1.0)
                            _day_bias = int(getattr(_thesis, 'bias_score', 0) or 0)
                            self._day_size_factor = max(0.5, min(1.5, _day_size))
                            self._day_bias_score  = _day_bias
                            logger.info(f"[{format_ist_timestamp()}] Morning intel: size_factor={self._day_size_factor:.2f} bias={_day_bias}")
                        else:
                            self._day_size_factor = 1.0
                            self._day_bias_score  = 0
                except Exception as _mie:
                    logger.debug(f"[suppressed] morning_intel sizing: {_mie}")
            else:
                self.alerter.send_morning_brief(
                    watchlist, available, spy_open,
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

        # ── AdaptiveBrain: reset for new day ──────────────────────────
        try:
            if hasattr(self, "adaptive_brain") and self.adaptive_brain:
                self.adaptive_brain.reset_day()
                self.adaptive_brain.set_day_capital(available)
                logger.info(f"[{format_ist_timestamp()}] AdaptiveBrain: new day reset, capital=${available:,.0f}")
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] AdaptiveBrain reset failed: {e}")

        # Log day-of-week mode
        now_ist  = get_current_ist_time()
        dow      = now_ist.weekday()
        dow_name = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"][dow]
        dow_mult = config.DOW_SIZE_MULTIPLIERS.get(dow, 1.0)
        dow_min  = config.DOW_MIN_SCORE.get(dow, config.MIN_SIGNAL_SCORE)
        dow_max  = config.DOW_MAX_TRADES.get(dow, config.MAX_TRADES_PER_DAY)
        logger.info(
            f"[{format_ist_timestamp()}] Day initialized | "
            f"Balance: ${available:,.2f} | SPY: ${spy_open:,.2f} | "
            f"Watchlist: {len(watchlist)} stocks\n"
            f"  DOW mode: {dow_name} | Size: {dow_mult:.0%} | "
            f"Min score: {dow_min:.0f} | Max trades: {dow_max}"
        )
        self._orb_done_today = False
        self._day_initialized = True
        self.market_open_today = True

    # --------------------------------------------------------
    # SIGNAL EXECUTION HELPER
    # --------------------------------------------------------

    def _execute_signal(self, signal) -> None:
        """
        Execute a single pre-validated signal directly (used by ORB and other
        sub-strategies that bypass the main scan loop).  Applies the short-selling
        guard and the standard risk gate before forwarding to the executor.
        """
        # Short selling guard
        if signal.direction == "SHORT" and not getattr(config, "SHORT_SELLING_ENABLED", True):
            logger.debug(f"Short selling disabled — skipping {signal.symbol} SHORT")
            return

        if not self.risk_manager or not self.executor:
            logger.debug("_execute_signal: risk_manager or executor not ready")
            return

        _risk_check = self.risk_manager.can_take_trade(signal.symbol, signal.direction)
        if not _risk_check["allowed"]:
            logger.info(
                f"[{format_ist_timestamp()}] RISK GATE (_execute_signal): "
                f"{signal.symbol} — {_risk_check['reason']}"
            )
            return

        logger.info(f"[{format_ist_timestamp()}] {signal.summary()}")

        # RL agent pre-trade signal validation (learn from every trade)
        try:
            from rl_agent import LakshKingRL
            _rl_ind = getattr(signal, "indicators", None)
            _rl_mkt = {
                "rsi": getattr(_rl_ind, "rsi", 50.0) if _rl_ind else getattr(signal, "rsi", 50.0),
                "macd_hist": getattr(_rl_ind, "macd_hist", 0.0) if _rl_ind else 0.0,
                "vwap_deviation_pct": (
                    (signal.entry_price - getattr(_rl_ind, "vwap", signal.entry_price))
                    / max(getattr(_rl_ind, "vwap", signal.entry_price), 0.01) * 100.0
                ) if _rl_ind and getattr(_rl_ind, "vwap", 0) > 0 else 0.0,
                "volume_ratio": getattr(_rl_ind, "volume_ratio", 1.0) if _rl_ind else 1.0,
                "mtf_score": signal.signal_score,
                "pattern_score": signal.signal_score,
                "signal_score": signal.signal_score,
                "nifty_trend": "BULLISH" if signal.direction == "LONG" else "BEARISH",
                "adx": getattr(_rl_ind, "adx", 20.0) if _rl_ind else 20.0,
            }
            _rl_decision = LakshKingRL.signal(signal.symbol, signal.direction, _rl_mkt, 250.0)
            if _rl_decision == "SKIP" and signal.signal_score < 88:
                logger.info(f"[{format_ist_timestamp()}] RL SKIP: {signal.symbol} score={signal.signal_score:.0f} — RL brain vetoed")
                return
        except Exception as _rle:
            logger.debug(f"[suppressed] RL signal: {_rle}")

        result = self.executor.place_entry_order(signal)
        if not result.success:
            logger.error(
                f"[{format_ist_timestamp()}] ORDER REJECTED (_execute_signal): "
                f"{signal.symbol} {signal.direction} score={signal.signal_score:.0f} | "
                f"Reason: {result.message}"
            )
            return

        # Register position with risk manager — critical for trailing stops, T1/T2 exits,
        # circuit breakers, and daily loss accounting. Without this, ORB fills are invisible.
        try:
            from risk_manager import Position
            fill_price = result.fill_price if result.fill_price > 0 else signal.entry_price
            fill_qty   = result.quantity   if result.quantity   > 0 else max(getattr(signal, "quantity", 0), 1)
            position = Position(
                symbol            = signal.symbol,
                direction         = signal.direction,
                quantity          = fill_qty,
                entry_price       = fill_price,
                stop_loss         = signal.stop_loss,
                target_1          = signal.target_1,
                target_2          = signal.target_2,
                atr               = getattr(signal, "atr", fill_price * 0.01),
                entry_time        = format_ist_timestamp(),
                quality_grade     = getattr(signal, "quality_grade", "B"),
                size_multiplier   = getattr(signal, "size_multiplier", 1.0),
                time_stop_minutes = getattr(signal, "time_stop_minutes", 30),
            )
            # Store entry hour (ET) for ML outcome recording
            try:
                from utils import get_current_ist_time as _gist
                _et_now_entry = _gist()
                position._entry_hour_et = (_et_now_entry.hour - 4) % 24
                position.signal_score   = getattr(signal, "signal_score", 0.0)
                position.indicators     = getattr(signal, "indicators", None)
            except Exception:
                pass
            self.risk_manager.add_position(position)
        except Exception as _pe:
            logger.warning(f"add_position failed for {signal.symbol}: {_pe}")

        # Broker-side stop order so SL is enforced even if bot crashes.
        # Guard: only place if fill is confirmed (quantity AND price > 0).
        # Placing a stop against a phantom position risks an unhedged broker-side order.
        if result.quantity > 0 and result.fill_price > 0:
            try:
                sl_id = self.executor.place_stop_order(
                    signal.symbol, int(result.quantity), signal.stop_loss, signal.direction
                )
                if sl_id:
                    pos_ref = self.risk_manager.state.positions.get(signal.symbol)
                    if pos_ref:
                        pos_ref.sl_order_id = sl_id
            except Exception as _se:
                logger.warning(f"place_stop_order failed for {signal.symbol}: {_se}")
        else:
            logger.warning(
                f"place_stop_order SKIPPED for {signal.symbol}: "
                f"fill not confirmed (qty={result.quantity}, price={result.fill_price})"
            )

    # --------------------------------------------------------
    # MAIN TRADING LOOP
    # --------------------------------------------------------

    def run(self):
        """Main bot run loop. Blocks until stopped. systemd Restart=always handles process-level restarts."""
        self.running = True
        try:
            from bot_identity import print_banner, TRADING_PHILOSOPHY
            print_banner()
        except Exception:
            pass
        logger.info(f"[{format_ist_timestamp()}] KING v15.0 — lakshaytrades | 26 gates | 8 frameworks | ML-scored")

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
                        # ── Opening Range Breakout (first 15 minutes) ───────────────────
                        if (getattr(config, "ORB_ENABLED", True)
                                and not self._orb_done_today
                                and not self.eod_done):
                            _et = get_current_et_time()
                            _mins_open = (_et.hour - 9) * 60 + (_et.minute - 30)
                            if 1 <= _mins_open <= 15:
                                # Market open 1-15 minutes — scan for ORB setups
                                try:
                                    _orb_signals = self.signal_gen.scan_watchlist(
                                        BROKER_WATCHLIST[:30], max_signals=5
                                    )
                                    _orb_high_conf = [
                                        s for s in _orb_signals
                                        if s.signal_score >= 72 and s.quality_grade in ("A+", "A")
                                    ]
                                    if _orb_high_conf:
                                        logger.info(
                                            f"[{format_ist_timestamp()}] 🔔 ORB: {len(_orb_high_conf)} "
                                            f"high-confidence setups at open"
                                        )
                                        for _orb_sig in _orb_high_conf[:3]:
                                            _orb_sig.size_multiplier = round(
                                                _orb_sig.size_multiplier * getattr(config, "ORB_RISK_MULTIPLIER", 1.2), 2
                                            )
                                            self._execute_signal(_orb_sig)
                                    # Only mark done on successful scan — allow retry if scan threw
                                    self._orb_done_today = True
                                except Exception as _orb_e:
                                    logger.warning(f"ORB scan error (will retry next cycle): {_orb_e}")
                            elif _mins_open > 15:
                                # ORB window has passed — mark done so we stop checking every cycle
                                self._orb_done_today = True

                        # Normal trading cycle
                        self._trading_cycle()

                    # Write terminal dashboard state every 30s (non-blocking)
                    self._write_terminal_state()

                elif self.market_open_today and not self.eod_done:
                    # Market just closed
                    self._do_eod_shutdown()

                else:
                    # Waiting for market (handles holidays + weekends automatically)
                    from utils import _is_nyse_holiday, get_next_market_open_et, get_current_et_date
                    _today_et = get_current_et_date()  # ET date, not IST date
                    _now_et   = get_current_et_time()
                    if _now_et.weekday() >= 5 or _is_nyse_holiday(_today_et):
                        _next_open = get_next_market_open_et()
                        _mins_hol = (_next_open - now_ist).total_seconds() / 60
                        logger.info(
                            f"[{format_ist_timestamp()}] NYSE holiday/weekend — "
                            f"next open {_next_open.strftime('%a %b %d %H:%M ET')} "
                            f"({_mins_hol/60:.1f}h away). Sleeping 1h."
                        )
                        time.sleep(3600)
                        continue
                    mins = minutes_until_market_open()
                    if mins > 0:
                        sleep_secs = min(300, max(10, mins * 60))  # up to 5 min when far from open
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
        except Exception as _fatal:
            import traceback
            logger.critical(
                f"[{format_ist_timestamp()}] FATAL main loop error: {_fatal}\n"
                f"{traceback.format_exc()}"
            )
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
        3. Check SPY/market circuit breaker
        4. Calendar & VIX blackout check
        5. Scan watchlist for new signals
        6. Execute valid signals
        """
        try:
            # 0. Self-heal — reinit anything that crashed
            self._auto_heal()

            # 0b. Reload optimizer config hourly so autonomous adjustments take effect
            import time as _time_mod
            _now_ts2 = _time_mod.monotonic()
            if _now_ts2 - self._last_optimizer_reload >= self._optimizer_reload_interval:
                self._last_optimizer_reload = _now_ts2
                try:
                    from autonomous_optimizer import load_optimizer_config
                    _opt = load_optimizer_config()
                    if self.signal_gen:
                        _new_min = _opt.get("min_score", self.signal_gen.min_score)
                        self.signal_gen.min_score = _new_min
                        if hasattr(self.signal_gen, "ha_filter"):
                            self.signal_gen.ha_filter.min_score = _new_min
                    if self.risk_manager:
                        self.risk_manager.max_positions = int(_opt.get("max_positions", self.risk_manager.max_positions))
                        self.risk_manager.max_risk_pct = float(_opt.get("risk_per_trade_pct", self.risk_manager.max_risk_pct))
                    logger.debug(f"[{format_ist_timestamp()}] Optimizer params reloaded")
                except Exception as _oe:
                    logger.debug(f"Optimizer reload skipped: {_oe}")

            # 1. Update open positions (ALWAYS — even if paused)
            self._update_positions()

            # 1b. Reconcile positions every 10 min (detect server-side SL hits)
            self._reconcile_positions()

            # 1c. 1% target hit → tighten all open stops to lock in the gain
            self._check_profit_lock()

            # 1d. Update weekly P&L mode (4-day profit optimizer)
            self._update_weekly_mode()
            if self._weekly_mode == "LOCKED":
                logger.debug(f"[{format_ist_timestamp()}] Weekly target hit — locked to A+ only")
                return

            # 2. Check SPY circuit breaker
            spy_q = self.fetcher.get_nifty_quote()
            if spy_q:
                self.risk_manager.check_nifty_circuit(spy_q.get("ltp", 0))

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

            # Sync adaptive brain threshold once per cycle
            try:
                if getattr(config, 'ADAPTIVE_BRAIN_THRESHOLD_SYNC', True) and hasattr(self, 'adaptive_brain') and self.adaptive_brain:
                    _ab_score = self.adaptive_brain.get_min_score() if hasattr(self.adaptive_brain, 'get_min_score') else None
                    if _ab_score and isinstance(_ab_score, (int, float)) and 65 <= _ab_score <= 90:
                        if hasattr(self, 'signal_gen') and self.signal_gen and hasattr(self.signal_gen, 'ha_filter'):
                            self.signal_gen.ha_filter.min_score = _ab_score
            except Exception:
                pass

            # 4. Scan watchlist (15-min cached)
            watchlist = self._get_watchlist_cached()
            _max_pos = self.risk_manager.max_positions
            max_new = _max_pos - len(self.risk_manager.state.positions)
            if max_new <= 0:
                logger.info(f"[{format_ist_timestamp()}] Max positions ({_max_pos}) reached — no new entries")
                return

            if spy_q and self.signal_gen:
                self.signal_gen.update_nifty_change(spy_q.get("change_pct", 0.0))

            # 4b. Apply day-of-week + time-of-day minimum score BEFORE scanning
            dow = now_ist.weekday()
            dow_min = config.DOW_MIN_SCORE.get(dow, config.MIN_SIGNAL_SCORE)
            dow_max_trades = config.DOW_MAX_TRADES.get(dow, config.MAX_TRADES_PER_DAY)

            # Time-of-day threshold (ET): open/power-hour = 65, midday chop = 73
            if getattr(config, "TOD_THRESHOLD_ENABLED", True):
                try:
                    _et_now = get_current_et_time()
                    _et_abs = _et_now.hour * 60 + _et_now.minute
                    _tod_min = None
                    for (start, end), _s in getattr(config, "TOD_MIN_SCORES", {}).items():
                        if start <= _et_abs < end:
                            _tod_min = _s
                            break
                    if _tod_min is not None:
                        # TOD threshold: use whichever is LOWER of DOW and TOD
                        # (during open/power hour we WANT more trades, not fewer)
                        dow_min = min(dow_min, _tod_min)
                except Exception:
                    pass

            if self.signal_gen:
                brain_score = (
                    self.adaptive_brain._state.current_min_score
                    if hasattr(self, "adaptive_brain") and self.adaptive_brain
                    else dow_min
                )
                self.signal_gen.min_score = max(dow_min, brain_score)
                # Keep HighAccuracyFilter threshold in sync — CRITICAL: without this the
                # internal ha_filter uses its construction-time value and ignores all
                # runtime changes to signal_gen.min_score.
                if hasattr(self.signal_gen, "ha_filter"):
                    self.signal_gen.ha_filter.min_score = self.signal_gen.min_score

                # ── Dynamic score relaxation: never sit idle all day ──────
                # If no trades by key times, gently lower the bar.
                # Floor is 67 — never below this, that's noise territory.
                _n_trades = self.risk_manager.state.daily_trades
                _pe_mode  = self.profit_engine.state.mode if self.profit_engine else "STOP"
                if _n_trades == 0 and _pe_mode not in ("STOP", "DEFENSIVE"):
                    try:
                        _et = get_current_et_time()
                    except Exception:
                        _et = now_ist
                    _et_min = _et.hour * 60 + _et.minute
                    if _et_min >= 810:      # 1:30 PM ET — afternoon, still zero trades
                        _floor = max(self.signal_gen.min_score - 5, 67)
                        if self.signal_gen.min_score != _floor:
                            self.signal_gen.min_score = _floor
                            self.signal_gen.ha_filter.min_score = _floor
                            logger.info(
                                f"[{format_ist_timestamp()}] No trades by 1:30 PM — "
                                f"score relaxed to {_floor:.0f} (best available, floor=67)"
                            )
                    elif _et_min >= 630:    # 10:30 AM ET — morning over, still zero trades
                        _floor = max(self.signal_gen.min_score - 3, 69)
                        if self.signal_gen.min_score != _floor:
                            self.signal_gen.min_score = _floor
                            self.signal_gen.ha_filter.min_score = _floor
                            logger.debug(
                                f"[{format_ist_timestamp()}] No trades by 10:30 AM — "
                                f"score relaxed to {_floor:.0f}"
                            )

            if self.risk_manager.state.daily_trades >= dow_max_trades:
                logger.info(
                    f"[{format_ist_timestamp()}] DOW max trades "
                    f"({dow_max_trades}) reached for {['Mon','Tue','Wed','Thu','Fri'][dow]}"
                )
                return

            # 4c. Power hour gate — only enter new positions during high-probability windows
            if config.REQUIRE_POWER_HOUR:
                from datetime import time as _time
                et_now = now_ist  # utils aliases ET time as IST for US market
                h, m   = et_now.hour, et_now.minute
                total_min = h * 60 + m
                in_power = (
                    (570 <= total_min < 690) or   # 9:30–11:30 ET opening + morning
                    (810 <= total_min < 960)       # 13:30–16:00 ET afternoon + closing
                )
                if not in_power:
                    logger.debug(
                        f"[{format_ist_timestamp()}] MIDDAY — skipping new entries "
                        f"(power hour gate, {et_now.strftime('%H:%M')} ET)"
                    )
                    return

            # SystemHealthChecker: pre-scan safety gate
            if hasattr(self, "health_checker") and self.health_checker:
                try:
                    bot_state = {
                        "day_pnl":     getattr(self.risk_manager.state, "daily_pnl", 0.0),
                        "day_capital": getattr(self.risk_manager.state, "daily_capital", 50000.0),
                        "min_score":   getattr(self.signal_gen, "min_score", 90.0),
                    }
                    sys_report = self.health_checker.check(bot_state)
                    if not sys_report.ok:
                        logger.warning(
                            f"[{format_ist_timestamp()}] SystemHealth BLOCKED: {sys_report.blocked_reason}"
                        )
                        return
                    for w in sys_report.warnings:
                        logger.debug(f"[{format_ist_timestamp()}] SystemHealth WARNING: {w}")
                except Exception as _e:
                    logger.debug(f"[suppressed] SystemHealth check: {_e}")

            # AdaptiveBrain: market health check before scanning
            if hasattr(self, "adaptive_brain") and self.adaptive_brain:
                health = self.adaptive_brain.check_market_health(
                    spy_adx    = float(spy_q.get("adx", 0)) if spy_q else 0.0,
                    spy_change = float(spy_q.get("change_pct", 0)) if spy_q else 0.0,
                )
                if not health["healthy"]:
                    logger.info(f"[{format_ist_timestamp()}] AdaptiveBrain pause: {health['reason']}")
                    return

            # Refresh dynamic top-movers hourly — fresh opportunity set each hour
            _now_ts = time.time()
            if _now_ts - self._last_mover_scan >= self._mover_scan_interval:
                self._last_mover_scan = _now_ts
                try:
                    from data_fetch_alpaca import get_top_movers
                    movers = get_top_movers(n=15)
                    new_syms = [m["symbol"] for m in movers if m["symbol"] not in watchlist]
                    if new_syms:
                        self._mover_watchlist = new_syms[:10]
                        logger.info(
                            f"[{format_ist_timestamp()}] Top movers added to scan: "
                            f"{', '.join(self._mover_watchlist)}"
                        )
                except Exception as _me:
                    logger.debug(f"top_movers refresh failed: {_me}")

            # Merge movers into the front of the scan queue (highest priority)
            combined_watchlist = self._mover_watchlist + [s for s in watchlist if s not in self._mover_watchlist]

            # Pre-market priority symbols (gap + volume) go to the very front
            # Refreshed once per morning; stale after 11 AM ET
            try:
                _et_now2 = get_current_et_time()
                _et_abs2  = _et_now2.hour * 60 + _et_now2.minute
                if _et_abs2 < 690:   # before 11:30 AM ET — pre-market gaps still relevant
                    if not hasattr(self, "_premarket_priority") or not self._premarket_priority:
                        from premarket_scanner import get_premarket_scanner
                        _pm_sc = get_premarket_scanner()
                        self._premarket_priority = _pm_sc.get_priority_symbols(combined_watchlist, top_n=8)
                        if self._premarket_priority:
                            logger.info(
                                f"[{format_ist_timestamp()}] Pre-market priority: "
                                + ", ".join(self._premarket_priority)
                            )
                    if getattr(self, "_premarket_priority", None):
                        _pmp = self._premarket_priority
                        combined_watchlist = _pmp + [s for s in combined_watchlist if s not in _pmp]
                else:
                    self._premarket_priority = []   # reset daily after 11:30 AM
            except Exception as _pme:
                logger.debug(f"[suppressed] premarket_priority: {_pme}")

            if not self.signal_gen:
                return

            # Expose current open positions to signal_gen for Gate 14 correlation check
            self.signal_gen._open_position_symbols = list(self.risk_manager.state.positions.keys())

            signals = self.signal_gen.scan_watchlist(
                symbols=combined_watchlist,
                max_signals=min(max_new, 4)  # up to 4 signals per cycle (was 3)
            )

            # 4a2. Mean-reversion engine — runs in RANGING/CHOPPY regimes
            # When momentum fails, reversion fills the gap
            if not signals:
                try:
                    from mean_reversion import get_mean_reversion_engine
                    mr_engine = get_mean_reversion_engine()
                    # Use signal_gen's already-initialized MarketRegimeDetector
                    current_regime = "UNKNOWN"
                    if self.signal_gen and self.signal_gen._regime:
                        _regime_ctx = (self.signal_gen._last_inst_ctx or {}) if hasattr(self.signal_gen, "_last_inst_ctx") else {}
                        current_regime = _regime_ctx.get("regime_name", "UNKNOWN")
                    if current_regime in ("RANGING", "LOW_VOLATILITY", "MIDDAY_CHOP", "HIGH_VOLATILITY"):
                        mr_signals = mr_engine.scan(watchlist[:30], self.fetcher, current_regime)
                        if mr_signals:
                            # Convert MeanReversionSignal → TradeSignal format
                            from signal_generator import TradeSignal
                            for mrs in mr_signals[:2]:
                                ts = TradeSignal(
                                    symbol=mrs.symbol, direction=mrs.direction,
                                    entry_price=mrs.entry_price, stop_loss=mrs.stop_loss,
                                    target_1=mrs.target_1, target_2=mrs.target_2,
                                    signal_score=mrs.score, quality_grade="B",
                                    size_multiplier=0.7,  # Conservative for reversion
                                    rationale=f"[REVERSION] {getattr(mrs, 'reason', '')}",
                                    atr=getattr(mrs, "atr", mrs.entry_price * 0.02),
                                    risk_reward=getattr(mrs, "risk_reward", 2.0),
                                )
                                signals.append(ts)
                            logger.info(f"[{format_ist_timestamp()}] Mean-reversion: {len(mr_signals)} setups in {current_regime} regime")
                except Exception as e:
                    logger.debug(f"Mean-reversion scan error: {e}")

            eff_min_score = getattr(self.signal_gen, "min_score", dow_min)
            # Apply AdaptiveBrain size_multiplier to every signal (loss streak → reduce size)
            _brain_size_mult = 1.0
            if hasattr(self, "adaptive_brain") and self.adaptive_brain:
                _brain_size_mult = self.adaptive_brain.get_size_multiplier()
            # Apply DOW size multiplier to every signal before filtering
            _dow_mult = config.DOW_SIZE_MULTIPLIERS.get(now_ist.weekday(), 1.0)
            _dd_mult = 1.0
            if hasattr(self, "risk_manager") and self.risk_manager and hasattr(self.risk_manager, "get_drawdown_size_mult"):
                _dd_mult = self.risk_manager.get_drawdown_size_mult()
                if _dd_mult < 1.0:
                    logger.info(f"[{format_ist_timestamp()}] DD recovery sizing: {_dd_mult:.2f}x (session drawdown active)")
            _combined_mult = round(_dow_mult * _brain_size_mult * _dd_mult, 3)
            if _combined_mult != 1.0:
                for _s in signals:
                    _s.size_multiplier = round(_s.size_multiplier * _combined_mult, 3)
            before_filter = len(signals)
            signals = [s for s in signals if s.signal_score >= eff_min_score]
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
                        f"{len(watchlist)} symbols (min_score={eff_min_score:.0f}, day={day_name})"
                    )
                    # Build gate rejection summary so user knows WHY — not just "no signals"
                    try:
                        _stats = self.signal_gen.ha_filter.get_stats()
                        _top_rejects = _stats.get("top_rejection_reasons", [])
                        _reject_str = ""
                        if _top_rejects:
                            _reject_str = "\nTop blocks: " + " | ".join(_top_rejects[:3])
                        self.alerter.send_html(
                            f"📊 <b>Market Scan — No Signals</b>\n"
                            f"Scanned {len(watchlist)} stocks at "
                            f"{now_ist.strftime('%H:%M')} ET\n"
                            f"Min score required: {eff_min_score:.0f} | Day: {day_name}"
                            f"{_reject_str}\n"
                            f"<i>Bot is running — waiting for quality setups</i>"
                        )
                    except Exception as _e:
                        logger.debug(f"[suppressed] {_e}")

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
                                risk_reward=_rr, atr=round(_entry * 0.02, 2),
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
                        _bd_signals = (self.signal_gen.scan_watchlist(
                            symbols=new_buys, max_signals=len(new_buys)
                        ) if self.signal_gen else [])
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
                            # Apply ProfitMaximizer enhancements — was only wired into MTF signals
                            try:
                                from profit_maximizer import get_profit_maximizer
                                _pm = get_profit_maximizer()
                                _df5 = self.fetcher.get_today_candles(setup.symbol)
                                if _df5 is not None and not _df5.empty:
                                    _pm_res = _pm.enhance(setup.symbol, setup.breakout_direction, _df5)
                                    if _pm_res and _pm_res.total_bonus > 0:
                                        orb_sig.signal_score = min(100.0, orb_sig.signal_score + _pm_res.total_bonus)
                            except Exception:
                                pass
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
                    spy_chg = 0.0
                    if spy_q:
                        spy_chg = spy_q.get("change_pct", 0.0)
                    scalp_signals = self.scalping_engine.scan(
                        watchlist, self.fetcher, nifty_change_pct=spy_chg
                    )
                    # Force-close Alpaca positions for scalps that expired (>15 min held)
                    _pending_expirations = list(self.scalping_engine.expired_symbols)
                    self.scalping_engine.expired_symbols.clear()   # consume once
                    for _exp_sym in _pending_expirations:
                        try:
                            self.executor.close_position(_exp_sym)
                            logger.info(
                                f"[{format_ist_timestamp()}] SCALP TIME-EXIT: {_exp_sym} "
                                "force-closed (max hold exceeded)"
                            )
                        except Exception as _ce:
                            logger.warning(
                                f"[{format_ist_timestamp()}] SCALP TIME-EXIT failed {_exp_sym}: {_ce}"
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

            # 4h. Momentum Burst Detector (opening 9:30-10:30 ET + afternoon 13:00-14:30 ET)
            if self.burst_detector and self.burst_detector.is_burst_time():
                try:
                    burst_setups = self.burst_detector.scan(
                        watchlist[:20], self.fetcher,
                        active_symbols=set(self.risk_manager.state.positions.keys())
                    )
                    for bs in burst_setups:
                        burst_ts = self.burst_detector.to_trade_signal(bs)
                        if burst_ts:
                            # Apply ProfitMaximizer enhancements
                            try:
                                from profit_maximizer import get_profit_maximizer
                                _pm = get_profit_maximizer()
                                _df5 = self.fetcher.get_today_candles(bs.symbol)
                                if _df5 is not None and not _df5.empty:
                                    _pm_r = _pm.enhance(bs.symbol, bs.direction, _df5)
                                    if _pm_r and _pm_r.total_bonus > 0:
                                        burst_ts.signal_score = min(100.0, burst_ts.signal_score + _pm_r.total_bonus)
                            except Exception:
                                pass
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
                        # Translate capital_usd → size multiplier relative to default
                        if self.profit_engine._available_balance > 0:
                            default_pct = config.MAX_CAPITAL_PER_TRADE_PCT / 100
                            engine_pct  = deployment.capital_usd / max(
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

                # 5b-pre. A+ setup: allow up to 2× normal risk (capped at 3%)
                if (getattr(signal, "signal_score", 0) >= 88
                        and getattr(signal, "quality_grade", "B") == "A+"):
                    try:
                        if self.risk_manager:
                            self.risk_manager.max_risk_pct = min(config.MAX_RISK_PER_TRADE_PCT * 2, 3.0)
                    except Exception:
                        pass
                else:
                    try:
                        if self.risk_manager:
                            self.risk_manager.max_risk_pct = config.MAX_RISK_PER_TRADE_PCT
                    except Exception:
                        pass

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
                except Exception as _e:
                    logger.debug(f"[suppressed] {_e}")

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
                        try:
                            from decision_log import log_rejected_heat
                            log_rejected_heat(signal.symbol, signal.direction,
                                              getattr(signal, "signal_score", 0), _heat_reason)
                        except Exception:
                            pass
                        continue
                    if _heat_reason != "OK":
                        logger.debug(
                            f"[{format_ist_timestamp()}] Heat guard: {signal.symbol} — {_heat_reason}"
                        )
                except Exception as _he:
                    logger.debug(f"Portfolio heat check skipped: {_he}")

                # Risk gate: enforce daily-loss, max-positions, pause, and duplicate checks
                # for BOTH paper and live (executor only runs can_take_trade in live mode)
                _risk_check = self.risk_manager.can_take_trade(signal.symbol, signal.direction)
                if not _risk_check["allowed"]:
                    logger.info(
                        f"[{format_ist_timestamp()}] RISK GATE: {signal.symbol} — {_risk_check['reason']}"
                    )
                    continue

                # Short selling guard
                if signal.direction == "SHORT" and not getattr(config, "SHORT_SELLING_ENABLED", True):
                    logger.debug(f"Short selling disabled — skipping {signal.symbol} SHORT")
                    continue

                logger.info(f"[{format_ist_timestamp()}] {signal.summary()}")
                result = self.executor.place_entry_order(signal)
                if not result.success:
                    logger.error(
                        f"[{format_ist_timestamp()}] ORDER REJECTED: {signal.symbol} "
                        f"{signal.direction} score={signal.signal_score:.0f} | "
                        f"Reason: {result.message}"
                    )
                    try:
                        from decision_log import log_rejected_order
                        log_rejected_order(signal.symbol, signal.direction,
                                           getattr(signal, "signal_score", 0), result.message)
                    except Exception:
                        pass
                if result.success:
                    try:
                        from decision_log import log_trade_taken
                        log_trade_taken(signal)
                    except Exception:
                        pass
                    # Register position with risk manager (trailing stops, T1/T2, daily-loss)
                    try:
                        from risk_manager import Position
                        fill_price = result.fill_price if result.fill_price > 0 else signal.entry_price
                        fill_qty   = result.quantity   if result.quantity   > 0 else max(getattr(signal, "quantity", 0), 1)
                        position = Position(
                            symbol            = signal.symbol,
                            direction         = signal.direction,
                            quantity          = fill_qty,
                            entry_price       = fill_price,
                            stop_loss         = signal.stop_loss,
                            target_1          = signal.target_1,
                            target_2          = signal.target_2,
                            atr               = getattr(signal, "atr", fill_price * 0.01),
                            entry_time        = format_ist_timestamp(),
                            quality_grade     = getattr(signal, "quality_grade", "B"),
                            size_multiplier   = getattr(signal, "size_multiplier", 1.0),
                            time_stop_minutes = getattr(signal, "time_stop_minutes", 30),
                        )
                        self.risk_manager.add_position(position)
                    except Exception as _pe:
                        logger.warning(f"add_position failed for {signal.symbol}: {_pe}")

                    # Place broker-side stop order (SL enforced even if bot crashes)
                    try:
                        sl_order_id = self.executor.place_stop_order(
                            signal.symbol, fill_qty, signal.stop_loss, signal.direction
                        )
                        if sl_order_id:
                            pos_ref = self.risk_manager.state.positions.get(signal.symbol)
                            if pos_ref:
                                pos_ref.sl_order_id = sl_order_id
                    except Exception as _se:
                        logger.warning(f"place_stop_order failed for {signal.symbol}: {_se}")

                    # Send Telegram alert with chart + trade fill notification
                    try:
                        df_5m = self.fetcher.get_today_candles(signal.symbol)
                        self.alerter.send_entry_alert(signal, df_5m)
                    except Exception as e:
                        logger.warning(f"Alert failed: {e}")
                    try:
                        self.alerter.send_trade_fill(
                            symbol       = signal.symbol,
                            direction    = signal.direction,
                            qty          = fill_qty,
                            price        = fill_price,
                            stop_loss    = signal.stop_loss,
                            target_1     = signal.target_1,
                            target_2     = signal.target_2,
                            signal_score = getattr(signal, "signal_score", 0.0),
                            quality_grade= getattr(signal, "quality_grade", ""),
                        )
                    except Exception as e:
                        logger.warning(f"send_trade_fill failed: {e}")

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

            # ── Standalone UOA scan (every 15 min — gated) ───────────────
            if self.options_scalper and now_ist.minute % 15 < 1:
                try:
                    bal   = self.fetcher.get_account_balance()
                    avail = bal.get("available", 0)
                    uoa_signals = self.options_scalper.scan_unusual_activity(
                        symbols           = list(watchlist),
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
                        except Exception as _e:
                            logger.debug(f"[suppressed] {_e}")
                        for uoa in uoa_signals[:2]:  # max 2 UOA trades per scan
                            self.options_scalper.execute_options_signal(uoa)
                except Exception as _uoa_e:
                    logger.debug(f"UOA scan error: {_uoa_e}")

            # Print dashboard periodically
            if get_current_ist_time().minute % 15 == 0:
                self.dashboard.print_live_dashboard(self.risk_manager)
                self.dashboard.print_positions(self.risk_manager)

        except Exception as e:
            import traceback
            logger.error(
                f"[{format_ist_timestamp()}] Trading cycle error: {e}\n"
                f"{traceback.format_exc()}"
            )

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
                except Exception as _e:
                    logger.debug(f"[suppressed] {_e}")

                # ── Time-stop: exit if no meaningful move after time_stop_minutes ──
                # Top-1% rule: dead money is wasted capital — free it for better setups.
                try:
                    entry_time = getattr(pos, "entry_time", None)
                    time_stop_mins = getattr(pos, "time_stop_minutes", 30)
                    if entry_time and time_stop_mins > 0 and not getattr(pos, "t1_done", False):
                        from datetime import datetime as _dt2
                        if isinstance(entry_time, str):
                            try:
                                entry_dt = _dt2.fromisoformat(
                                    entry_time.replace(" ET", "").strip()
                                ).replace(tzinfo=ET)
                            except Exception:
                                entry_dt = None
                        else:
                            entry_dt = entry_time
                        if entry_dt:
                            mins_held = (_dt2.now(ET) - entry_dt).total_seconds() / 60
                            if mins_held >= time_stop_mins:
                                pnl_pct = ((ltp - pos.entry_price) / pos.entry_price * 100
                                           if pos.direction == "LONG"
                                           else (pos.entry_price - ltp) / pos.entry_price * 100)
                                # Only time-stop if trade is flat or losing (< 0.2% profit)
                                if pnl_pct < 0.2:
                                    logger.info(
                                        f"[{format_ist_timestamp()}] TIME-STOP: {pos.symbol} "
                                        f"held {mins_held:.0f}min with only {pnl_pct:+.2f}% — exiting"
                                    )
                                    result = self.executor.place_exit_order(
                                        symbol=pos.symbol, quantity=pos.quantity,
                                        direction=pos.direction, reason="TIME_STOP",
                                        use_market_order=True,
                                    )
                                    if result.success:
                                        actual_exit = result.fill_price if result.fill_price > 0 else ltp
                                        pnl_val = pos.pnl
                                        self.risk_manager.close_position(pos.symbol, actual_exit, "TIME_STOP")
                                        self._save_capital_intraday()
                                        self.alerter.send_exit_alert(
                                            pos.symbol, pos.direction, pos.entry_price,
                                            actual_exit, pos.quantity, pnl_val, "TIME_STOP"
                                        )
                                    continue
                except Exception as _ts_err:
                    logger.debug(f"[suppressed] time_stop {pos.symbol}: {_ts_err}")

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
                        # Cancel broker-side stop order (position is closed, orphan would re-enter)
                        if pos.sl_order_id:
                            try:
                                self.executor.cancel_order(pos.sl_order_id)
                            except Exception as _cse:
                                logger.warning(f"cancel_order(stop) failed on health exit {pos.symbol}: {_cse}")
                        actual_exit = result.fill_price if result.fill_price > 0 else ltp
                        pos.current_price = actual_exit  # use fill price for accurate P&L
                        pnl = pos.pnl
                        # Remove from risk state, update daily P&L / win-loss counters
                        self.risk_manager.close_position(pos.symbol, actual_exit, health["reason"])
                        self._save_capital_intraday()
                        self.alerter.send_exit_alert(
                            pos.symbol, pos.direction, pos.entry_price,
                            actual_exit, pos.quantity, pnl, health["reason"]
                        )
                        try:
                            from decision_log import log_trade_outcome
                            log_trade_outcome(
                                pos.symbol, pos.direction, pos.entry_price, actual_exit,
                                pos.quantity, pnl, health["reason"],
                                getattr(pos, "entry_time", ""),
                                getattr(pos, "signal_score", 0.0),
                                getattr(pos, "quality_grade", "B"),
                            )
                        except Exception:
                            pass
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
                        # Cancel broker-side stop order (position closed; orphan would open new position)
                        if pos.sl_order_id:
                            try:
                                self.executor.cancel_order(pos.sl_order_id)
                            except Exception as _cse:
                                logger.warning(f"cancel_order(stop) failed on exit {pos.symbol}: {_cse}")
                        actual_exit = result.fill_price if result.fill_price > 0 else ltp
                        pos.current_price = actual_exit  # use fill price for accurate P&L
                        pnl = pos.pnl
                        # Remove from risk state: updates daily_pnl, daily_trades, consecutive counters
                        self.risk_manager.close_position(pos.symbol, actual_exit, action["reason"])
                        self._save_capital_intraday()
                        # Record closed trade in Profit Engine for compounding/mode tracking
                        if self.profit_engine:
                            try:
                                self.profit_engine.record_trade_closed(
                                    pos.symbol, pnl,
                                    was_partial=False  # final close: record full runner P&L
                                )
                            except Exception as _e:
                                logger.warning(f"profit_engine.record_trade_closed failed ({pos.symbol}): {_e}")
                        # Record outcome in Elite Brain for adaptive weight learning
                        if self.elite_brain:
                            try:
                                _eb_votes = getattr(pos, "_elite_module_votes", {})
                                if _eb_votes:
                                    self.elite_brain.record_trade_outcome(_eb_votes, won=pnl > 0)
                            except Exception as _e:
                                logger.debug(f"[suppressed] {_e}")
                        # CommissionTracker: record realistic cost-adjusted P&L
                        if hasattr(self, "commission_tracker") and self.commission_tracker:
                            try:
                                self.commission_tracker.record_trade(
                                    symbol      = pos.symbol,
                                    quantity    = pos.quantity,
                                    entry_price = pos.entry_price,
                                    exit_price  = actual_exit,
                                    direction   = pos.direction,
                                )
                            except Exception as _e:
                                logger.debug(f"[suppressed] CommissionTracker: {_e}")
                        # SymbolStats: update per-symbol rolling win rate (70-80% WR gate)
                        try:
                            from symbol_stats import SymbolStats
                            if not hasattr(self, "_symbol_stats"):
                                self._symbol_stats = SymbolStats()
                            self._symbol_stats.record(pos.symbol, win=pnl > 0)
                        except Exception as _ss_e:
                            logger.debug(f"[suppressed] symbol_stats.record: {_ss_e}")
                        # PatternAnalytics: record per-pattern win rate for adaptive scoring
                        try:
                            from pattern_analytics import PatternAnalytics
                            if not hasattr(self, "_pattern_analytics"):
                                self._pattern_analytics = PatternAnalytics()
                            _entry_hour = getattr(pos, "entry_time", None)
                            _entry_hour = _entry_hour.hour if hasattr(_entry_hour, "hour") else 0
                            _time_b = "OPEN" if _entry_hour < 11 else "MID"
                            for _pname in getattr(pos, "pattern_names", []):
                                self._pattern_analytics.record(
                                    _pname, win=pnl > 0, pnl=pnl, time_bucket=_time_b
                                )
                        except Exception as _pa_e:
                            logger.debug(f"[suppressed] pattern_analytics.record: {_pa_e}")

                        # AdaptiveKelly: record outcome for dynamic Kelly sizing
                        try:
                            from adaptive_kelly import get_adaptive_kelly
                            _risk_amt = getattr(pos, "risk_amount", abs(pos.entry_price - pos.stop_loss) * pos.quantity)
                            get_adaptive_kelly().record(pos.symbol, win=pnl > 0, pnl=pnl, risk_amount=_risk_amt)
                        except Exception as _ake:
                            logger.debug(f"[suppressed] adaptive_kelly.record: {_ake}")
                        # SessionMomentum: track intraday performance for adaptive scoring
                        try:
                            from session_momentum import get_session_momentum
                            get_session_momentum().record(pos.symbol, win=pnl > 0, pnl=pnl)
                        except Exception as _sme:
                            logger.debug(f"[suppressed] session_momentum.record: {_sme}")
                        # Sortino sizing: record trade P&L % for per-symbol Sortino calculation
                        try:
                            from institutional_strategies import record_trade_result as _record_tr
                            _pnl_pct = pnl / max(pos.entry_price * pos.quantity, 1.0)
                            _record_tr(pos.symbol, _pnl_pct)
                        except Exception as _tr_e:
                            logger.debug(f"[suppressed] record_trade_result: {_tr_e}")
                        # RL brain: feed trade outcome so agent learns from this trade
                        try:
                            from rl_agent import LakshKingRL
                            LakshKingRL.close(
                                symbol=pos.symbol, pnl=pnl,
                                exit_reason=action.get("reason", "EXIT"),
                                market_data={"signal_score": getattr(pos, "signal_score", 0.0)},
                            )
                        except Exception as _rle:
                            logger.debug(f"[suppressed] RL close: {_rle}")
                        # ML ranker: record outcome for online learning
                        try:
                            from ml_signal_ranker import record_outcome as _ml_rec
                            _ml_ind = getattr(pos, "indicators", None)
                            _ml_sc  = getattr(pos, "signal_score", 70.0)
                            _ml_r2  = getattr(pos, "_r2_quality", 0.5)
                            _ml_rec(
                                won=pnl > 0,
                                rsi=getattr(_ml_ind, "rsi", 55.0) if _ml_ind else 55.0,
                                macd_hist_norm=0.0,
                                volume_ratio=getattr(_ml_ind, "volume_ratio", 1.0) if _ml_ind else 1.0,
                                atr_pct=(getattr(_ml_ind, "atr", 0.5) / max(pos.entry_price, 1.0)) * 100.0 if _ml_ind else 1.0,
                                signal_score=_ml_sc,
                                adx=getattr(_ml_ind, "adx", 25.0) if _ml_ind else 25.0,
                                ema_slope_pct=0.0,
                                vwap_dist_pct=0.0,
                                bb_pct=0.5,
                                hour_et=getattr(pos, "_entry_hour_et", 10),
                                is_long=1 if pos.direction == "LONG" else 0,
                                r2_quality=_ml_r2,
                            )
                        except Exception as _ml_e:
                            logger.debug(f"[suppressed] ml_record_outcome: {_ml_e}")

                        # AdaptiveThreshold: record outcome for rolling WR-based score floor
                        try:
                            from adaptive_threshold import record_outcome as _rec_adaptive_outcome
                            _rec_adaptive_outcome(pnl > 0)
                        except Exception as _at_e:
                            logger.debug(f"[suppressed] adaptive_threshold.record: {_at_e}")

                        # AdaptiveBrain: record trade outcome for intraday adaptation
                        if hasattr(self, "adaptive_brain") and self.adaptive_brain:
                            try:
                                from adaptive_brain import TradeOutcome
                                outcome = TradeOutcome(
                                    symbol      = pos.symbol,
                                    direction   = pos.direction,
                                    pattern     = getattr(pos, "pattern_name", "UNKNOWN"),
                                    entry_score = getattr(pos, "signal_score", 0.0),
                                    pnl         = pnl,
                                    win         = pnl > 0,
                                    regime      = getattr(pos, "regime", "UNKNOWN"),
                                    session     = getattr(pos, "session", "UNKNOWN"),
                                )
                                adapt_msg = self.adaptive_brain.record_trade(outcome)
                                logger.info(f"[{format_ist_timestamp()}] AdaptiveBrain: {adapt_msg}")
                            except Exception as e:
                                logger.debug(f"AdaptiveBrain record error: {e}")
                        self.alerter.send_exit_alert(
                            pos.symbol, pos.direction, pos.entry_price,
                            actual_exit, pos.quantity, pnl, action["reason"]
                        )
                        try:
                            from decision_log import log_trade_outcome
                            log_trade_outcome(
                                pos.symbol, pos.direction, pos.entry_price, actual_exit,
                                pos.quantity, pnl, action["reason"],
                                getattr(pos, "entry_time", ""),
                                getattr(pos, "signal_score", 0.0),
                                getattr(pos, "quality_grade", "B"),
                            )
                        except Exception:
                            pass

                elif action["action"] in ("PARTIAL_EXIT_T1", "PARTIAL_EXIT_T2"):
                    exit_qty = action.get("exit_qty", 0)
                    if exit_qty <= 0:
                        exit_qty = max(1, pos.quantity // 2)
                    if exit_qty <= 0 or pos.quantity <= 0:
                        logger.warning(
                            f"[{format_ist_timestamp()}] {pos.symbol}: PARTIAL_EXIT skipped — "
                            f"exit_qty={exit_qty} pos.quantity={pos.quantity} (position already closed?)"
                        )
                        continue
                    result = self.executor.place_exit_order(
                        pos.symbol, exit_qty, pos.direction,
                        reason=action["reason"]
                    )
                    if result.success:
                        # Update local position quantity
                        pos.quantity = max(0, pos.quantity - exit_qty)
                        # Cancel old stop order (wrong qty after partial) + place new one
                        if pos.sl_order_id:
                            try:
                                self.executor.cancel_order(pos.sl_order_id)
                            except Exception as _cse:
                                logger.warning(f"cancel_order(stop) failed on partial exit {pos.symbol}: {_cse}")
                        if pos.quantity > 0:
                            new_sl_oid = self.executor.place_stop_order(
                                pos.symbol, pos.quantity, action["new_sl"], pos.direction
                            )
                            pos.sl_order_id = new_sl_oid
                        self.executor.modify_stop_loss(pos.symbol, action["new_sl"])
                        actual_fill = result.fill_price if result.fill_price > 0 else ltp
                        pnl_partial = (actual_fill - pos.entry_price) * exit_qty if pos.direction == "LONG" else (pos.entry_price - actual_fill) * exit_qty
                        # Accumulate partial P&L for correct win/loss determination at final close
                        pos.realized_pnl += pnl_partial
                        # Include partial P&L in daily tracking so loss limits are enforced
                        self.risk_manager.state.daily_pnl += pnl_partial
                        logger.info(
                            f"[{format_ist_timestamp()}] {action['action']}: "
                            f"{pos.symbol} {exit_qty}qty @ ${actual_fill:.2f} | "
                            f"Partial P&L: ${pnl_partial:.2f} | "
                            f"Daily P&L: ${self.risk_manager.state.daily_pnl:+.2f}"
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
                            except Exception as _e:
                                logger.warning(f"profit_engine partial-exit record failed ({pos.symbol}): {_e}")
                        try:
                            self.alerter.send_exit_alert(
                                pos.symbol, pos.direction, pos.entry_price,
                                actual_fill, exit_qty, pnl_partial, action["reason"]
                            )
                        except Exception as _e:
                            logger.warning(f"send_exit_alert failed ({pos.symbol}): {_e}")

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
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")
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
        _wcap = config.MAX_DAILY_CAPITAL or self.risk_manager.state.daily_capital or 1.0
        data["weekly_pct"]      = round(data["net_pnl"] / _wcap * 100, 2)
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
            except Exception as _e:
                logger.debug(f"[suppressed] {_e}")

    def _check_profit_lock(self) -> None:
        """
        When daily profit engine enters LOCK mode (1% target hit):
        - Tighten ALL open position stops to entry + 0.3×ATR (partial profit locked)
        - Only runs once per LOCK activation per day
        """
        try:
            if not self.profit_engine:
                return
            from daily_profit_engine import TradingMode
            if self.profit_engine.state.mode != TradingMode.LOCK:
                self._profit_lock_tightened = False  # reset flag when not locked
                return
            # Only tighten once per LOCK activation
            if getattr(self, "_profit_lock_tightened", False):
                return
            positions = self.risk_manager.state.positions
            if not positions:
                self._profit_lock_tightened = True
                return
            tightened = []
            for sym, pos in list(positions.items()):
                try:
                    new_sl = pos.entry_price + 0.3 * pos.atr if pos.direction == "LONG" else pos.entry_price - 0.3 * pos.atr
                    if pos.direction == "LONG" and new_sl > pos.active_sl:
                        pos.stop_loss = new_sl
                        pos.trailing_active = False  # use stop_loss directly
                        tightened.append(f"{sym} SL→${new_sl:.2f}")
                    elif pos.direction == "SHORT" and new_sl < pos.active_sl:
                        pos.stop_loss = new_sl
                        tightened.append(f"{sym} SL→${new_sl:.2f}")
                except Exception:
                    pass
            self._profit_lock_tightened = True
            if tightened:
                logger.info(f"[{format_ist_timestamp()}] 🔒 PROFIT LOCK: tightened stops — {', '.join(tightened)}")
                try:
                    # Big celebration — 1% daily target achieved
                    rm    = self.risk_manager
                    state = getattr(rm, "state", None)
                    _pnl     = state.daily_pnl      if state else 0.0
                    _cap     = state.daily_capital   if state else 0.0
                    _trades  = state.daily_trades    if state else 0
                    _wins    = state.winning_trades  if state else 0
                    _losses  = state.losing_trades   if state else 0
                    if hasattr(self.alerter, "send_target_achieved"):
                        self.alerter.send_target_achieved(_pnl, _cap, _trades, _wins, _losses)
                    else:
                        self.alerter.send_text(
                            f"🎯 <b>1% DAILY TARGET HIT!</b>\n"
                            f"🔒 Stops tightened — profits protected\n"
                            + "\n".join(f"• {t}" for t in tightened)
                        )
                except Exception:
                    pass
        except Exception as _e:
            logger.debug(f"[suppressed] _check_profit_lock: {_e}")

    def _write_terminal_state(self) -> None:
        """Write bot state to /tmp/kingtrades_state.json for terminal_display.py dashboard."""
        import time as _t
        if _t.monotonic() - self._last_state_write < 30:
            return
        self._last_state_write = _t.monotonic()
        try:
            import json
            from pathlib import Path
            from zoneinfo import ZoneInfo as _ZI

            _ET = _ZI("America/New_York")
            state  = getattr(self.risk_manager, "state", None)
            capital = (state.daily_capital if state else 0) or getattr(self.risk_manager, "_available_balance", 0) or config.MAX_DAILY_CAPITAL
            # Pre-market: risk manager not yet initialized — fetch live broker balance
            if capital <= 0 and self.fetcher:
                try:
                    _bal = self.fetcher.get_account_balance()
                    capital = _bal.get("available", 0) or _bal.get("equity", 0) or capital
                except Exception:
                    pass
            n_trades = state.daily_trades   if state else 0
            wins     = state.winning_trades if state else 0
            losses   = state.losing_trades  if state else 0
            wr       = wins / n_trades * 100 if n_trades > 0 else 0.0
            daily_pnl= state.daily_pnl      if state else 0.0
            consec_l = getattr(state, "consecutive_losses", 0) if state else 0
            paused   = state.trading_paused           if state else False
            cb       = state.circuit_breaker_active   if state else False

            # Position list
            raw_pos  = state.positions if state else {}
            pos_list = []
            unrealized = 0.0
            for sym, pos in list(raw_pos.items())[:10]:
                p_pnl    = getattr(pos, "pnl", 0.0)
                cur      = getattr(pos, "current_price", pos.entry_price)
                entry    = pos.entry_price
                pnl_pct  = ((cur - entry) / entry * 100) if entry > 0 and pos.direction == "LONG" else (
                           ((entry - cur) / entry * 100) if entry > 0 else 0.0)
                unrealized += p_pnl
                pos_list.append({
                    "symbol":    sym,
                    "direction": pos.direction,
                    "entry":     round(entry, 2),
                    "current":   round(cur, 2),
                    "pnl":       round(p_pnl, 2),
                    "pnl_pct":   round(pnl_pct, 2),
                    "stop":      round(getattr(pos, "active_sl", getattr(pos, "stop_loss", 0.0)), 2),
                    "target1":   round(getattr(pos, "target_1", 0.0), 2),
                })

            # Avg win/loss/EV
            wins_pnl   = [t.get("pnl", 0) for t in getattr(self, "_today_trades", []) if t.get("pnl", 0) > 0]
            losses_pnl = [t.get("pnl", 0) for t in getattr(self, "_today_trades", []) if t.get("pnl", 0) < 0]
            avg_win    = sum(wins_pnl)   / len(wins_pnl)   if wins_pnl  else 0.0
            avg_loss   = sum(losses_pnl) / len(losses_pnl) if losses_pnl else 0.0
            ev         = (wr / 100 * avg_win) + ((1 - wr / 100) * avg_loss) if n_trades > 0 else 0.0
            best_trade = max(getattr(self, "_today_trades", [{"pnl": 0}]), key=lambda t: t.get("pnl", 0), default={"pnl": 0, "symbol": "—"})

            # Session
            try:
                from risk_manager import _get_session_label
                sess_label = _get_session_label()
            except Exception:
                sess_label = "—"
            try:
                _sess_mult, _sess_name = self.risk_manager._get_session_multiplier()
            except Exception:
                _sess_mult, _sess_name = 1.0, "—"

            # Heat
            heat = sum(
                getattr(pos, "current_price", pos.entry_price) * getattr(pos, "quantity", 1)
                for pos in raw_pos.values()
            )

            data = {
                "version":       "3.0",
                "updated_at":    datetime.now(_ET).strftime("%H:%M:%S ET"),
                "capital":       round(capital, 2),
                "realized_pnl":  round(daily_pnl, 2),
                "unrealized_pnl":round(unrealized, 2),
                "target_pct":    getattr(config, "DAILY_PROFIT_TARGET_PCT", 1.0),
                "n_trades":      n_trades,
                "wins":          wins,
                "losses":        losses,
                "win_rate":      round(wr, 1),
                "avg_win":       round(avg_win, 2),
                "avg_loss":      round(avg_loss, 2),
                "ev_trade":      round(ev, 2),
                "best_symbol":   best_trade.get("symbol", "—"),
                "best_pnl":      round(best_trade.get("pnl", 0.0), 2),
                "consecutive_losses": consec_l,
                "paused":        paused,
                "circuit_breaker": cb,
                "n_positions":   len(raw_pos),
                "max_positions": getattr(config, "MAX_OPEN_POSITIONS", 5),
                "heat":          round(heat, 2),
                "risk_used":     round(daily_pnl * -1 if daily_pnl < 0 else 0.0, 2),
                "risk_max":      round(capital * getattr(config, "DAILY_LOSS_LIMIT_PCT", 2.0) / 100, 2),
                "session":       _sess_name or sess_label,
                "session_mult":  round(_sess_mult, 2),
                "spy_price":     0.0,
                "spy_pct":       0.0,
                "qqq_price":     0.0,
                "qqq_pct":       0.0,
                "vix":           0.0,
                "regime":        "UNKNOWN",
                "next_event":    "—",
                "eod_time":      "15:35 ET",
                "positions":     pos_list,
                "recent_logs":   getattr(self, "_recent_log_lines", [])[-6:],
            }

            # Market context (best-effort)
            try:
                _spy_q = self.fetcher.get_quote("SPY") or {}
                data["spy_price"] = round(float(_spy_q.get("ltp", 0) or 0), 2)
                data["spy_pct"]   = round(float(_spy_q.get("change_pct", 0) or 0), 2)
            except Exception:
                pass
            try:
                _qqq_q = self.fetcher.get_quote("QQQ") or {}
                data["qqq_price"] = round(float(_qqq_q.get("ltp", 0) or 0), 2)
                data["qqq_pct"]   = round(float(_qqq_q.get("change_pct", 0) or 0), 2)
            except Exception:
                pass
            try:
                from market_regime import get_vix
                data["vix"] = round(float(get_vix() or 0), 1)
            except Exception:
                pass
            try:
                from market_regime import get_regime_label
                data["regime"] = get_regime_label()
            except Exception:
                pass

            Path("/tmp/kingtrades_state.json").write_text(json.dumps(data, indent=2))
        except Exception as _se:
            logger.debug(f"[suppressed] state write: {_se}")

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
                today_pnl   = self.risk_manager.state.daily_pnl
                _daily_cap  = self.risk_manager.state.daily_capital or self.risk_manager._available_balance or 1.0
                total_pct   = pct + (today_pnl / _daily_cap * 100)
            else:
                total_pct = pct

            # WEEKLY_PROFIT_LOCK_PCT (15%) > WEEKLY_PROFIT_TARGET_PCT (8%)
            # Check the higher threshold first so PROTECT doesn't get skipped.
            if total_pct >= config.WEEKLY_PROFIT_LOCK_PCT:
                if self._weekly_mode != "LOCKED":
                    self._weekly_mode = "LOCKED"
                    logger.info(
                        f"[{format_ist_timestamp()}] 🔒 Weekly LOCK triggered "
                        f"({total_pct:.2f}% ≥ {config.WEEKLY_PROFIT_LOCK_PCT}%) — no new entries"
                    )
                    if self.alerter:
                        self.alerter.send_text(
                            f"🎯 <b>Weekly Lock!</b>\n"
                            f"Week P&L: {total_pct:.2f}% ≥ {config.WEEKLY_PROFIT_LOCK_PCT}%\n"
                            f"Mode: LOCKED — protecting gains, no new entries.\n"
                            f"Existing positions monitored until 3:20 PM."
                        )
            elif total_pct >= config.WEEKLY_PROFIT_TARGET_PCT:
                if self._weekly_mode not in ("PROTECT", "LOCKED"):
                    self._weekly_mode = "PROTECT"
                    logger.info(
                        f"[{format_ist_timestamp()}] 🛡 Weekly target hit "
                        f"({total_pct:.2f}% ≥ {config.WEEKLY_PROFIT_TARGET_PCT}%) — PROTECT (A/A+ only)"
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
        # In paper mode the broker has no positions (fills are simulated in memory).
        # Reconciling would force-close all simulated positions as ghost entries.
        if not config.LIVE_TRADING_ENABLED:
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

            # Build set of symbols Alpaca actually holds (non-zero quantity)
            groww_syms: set = {
                str(p.get("symbol", ""))
                for p in groww_raw
                if int(p.get("qty", 0)) != 0
            }
            bot_syms: set = set(self.risk_manager.state.positions.keys())

            # ── Case 1: ghost positions (bot tracks, broker doesn't) ─────────
            for sym in list(bot_syms - groww_syms):
                pos = self.risk_manager.state.positions.get(sym)
                if not pos:
                    continue
                # Fetch last known price for P&L accounting
                try:
                    q = self.fetcher.get_quote(sym)
                    ltp = float(q.get("ltp", pos.entry_price)) if q else pos.entry_price
                except Exception as _e:
                    logger.warning(f"Reconcile quote failed ({sym}): {_e}")
                    ltp = pos.entry_price

                # close_position() removes from state.positions and updates daily_pnl,
                # daily_trades, winning_trades, consecutive_losses — full accounting
                self.risk_manager.close_position(sym, ltp, "BROKER_RECONCILE_SL_HIT")

                # Also cancel any orphaned broker-side stop order
                if pos.sl_order_id:
                    try:
                        self.executor.cancel_order(pos.sl_order_id)
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
                except Exception as _e:
                    logger.debug(f"[suppressed] {_e}")

            # ── Case 2: unknown positions (Groww has, bot doesn't) ───────────
            for sym in list(groww_syms - bot_syms):
                raw = next((p for p in groww_raw if str(p.get("symbol", "")) == sym), None)
                if not raw:
                    continue
                qty = int(raw.get("qty", 0))
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
                    target_1=avg * 1.02,
                    target_2=avg * 1.04,
                    atr=avg * 0.02,
                    entry_time=format_ist_timestamp(),
                )
                self.risk_manager.add_position(pos)
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
                except Exception as _e:
                    logger.debug(f"[suppressed] {_e}")

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

            # Close all in-memory positions so P&L, daily_pnl, and wins/losses are recorded.
            # Applies in both live and paper modes — broker call above handles the broker side.
            for sym, pos in list(self.risk_manager.state.positions.items()):
                try:
                    q = self.fetcher.get_quote(sym) if self.fetcher else {}
                    ltp = float(q.get("ltp", pos.current_price or pos.entry_price)) if q else pos.entry_price
                    pos.current_price = ltp
                    pnl = pos.pnl
                    self.risk_manager.close_position(sym, ltp, "EOD_SQUAREOFF")
                    try:
                        from decision_log import log_trade_outcome
                        log_trade_outcome(
                            sym, pos.direction, pos.entry_price, ltp,
                            pos.quantity, pnl, "EOD_SQUAREOFF",
                            getattr(pos, "entry_time", ""),
                            getattr(pos, "signal_score", 0.0),
                            getattr(pos, "quality_grade", "B"),
                        )
                    except Exception:
                        pass
                except Exception as _e:
                    logger.warning(f"EOD in-memory close failed ({sym}): {_e}")

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

        # Save daily P&L to monthly tracker for 13%/month compounding
        if self.profit_engine:
            try:
                today_str = get_current_ist_time().strftime("%Y-%m-%d")
                today_pnl = self.risk_manager.state.daily_pnl if self.risk_manager else 0
                self.profit_engine.record_eod_pnl(today_str, today_pnl)
            except Exception as _e:
                logger.debug(f"[suppressed] record_eod_pnl: {_e}")

        # Save compounded capital for tomorrow
        self._save_compounded_capital()

        # EOD walk-forward optimization: train on today's data, save params for tomorrow
        try:
            from trainer import EODSelfTrainer
            logger.info(f"[{format_ist_timestamp()}] Running EOD walk-forward optimization…")
            trainer = EODSelfTrainer()
            trainer.run(lookback_days=60)
            logger.info(f"[{format_ist_timestamp()}] EOD trainer complete — params saved for tomorrow")
        except Exception as _te:
            logger.warning(f"[{format_ist_timestamp()}] EOD trainer skipped: {_te}")

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
                result = self.overnight.run_analysis(ai_brain=self.ai_brain)
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
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")
            issues.append(f"⚠️ Cannot reach {MARKET_NAME} API — check VPS internet")

        # ── 3. Memory check ───────────────────────────────────────────────
        try:
            import resource
            mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            if mem_mb > 800:
                issues.append(f"⚠️ High memory: {mem_mb:.0f} MB — consider restart")
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")

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
                except Exception as _e:
                    logger.debug(f"[suppressed] {_e}")
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
        """
        Start Telegram command listener using direct HTTP polling (no PTB asyncio).
        Simple getUpdates loop — immune to 409 Conflict by design.
        """
        if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
            logger.warning(f"[{format_ist_timestamp()}] Telegram not configured — command listener disabled")
            return

        def listener_thread():
            import requests as _rq

            while not self.running:
                time.sleep(0.2)

            token   = config.TELEGRAM_BOT_TOKEN
            chat_id = str(config.TELEGRAM_CHAT_ID).strip()
            base    = f"https://api.telegram.org/bot{token}"
            offset  = 0

            # Clear any stale session / webhook before starting
            try:
                _rq.get(f"{base}/deleteWebhook?drop_pending_updates=true", timeout=10)
            except Exception:
                pass
            time.sleep(3)

            logger.info(f"[{format_ist_timestamp()}] Telegram direct-HTTP listener started")

            def _reply(text, parse_mode="HTML"):
                try:
                    _rq.post(f"{base}/sendMessage",
                             json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
                             timeout=10)
                except Exception:
                    pass

            while self.running:
                try:
                    r = _rq.get(f"{base}/getUpdates",
                                params={"offset": offset, "timeout": 25, "allowed_updates": ["message"]},
                                timeout=30)
                    if not r.ok:
                        time.sleep(5)
                        continue
                    updates = r.json().get("result", [])
                    for upd in updates:
                        offset = upd["update_id"] + 1
                        msg = upd.get("message", {})
                        from_id = str(msg.get("chat", {}).get("id", ""))
                        text = (msg.get("text") or "").strip()
                        if not text.startswith("/") or from_id != chat_id:
                            continue
                        cmd = text.split()[0].lower().split("@")[0]
                        logger.info(f"[{format_ist_timestamp()}] Telegram cmd: {cmd}")
                        try:
                            if cmd == "/kill":
                                _reply("🔴 <b>KILL SWITCH activated</b>")
                                self.risk_manager.emergency_stop()
                                self.alerter.send_kill_alert()
                                self.executor.square_off_all("KILL SWITCH by Telegram /kill")
                            elif cmd == "/status":
                                try:
                                    bal = self.fetcher.get_account_balance() if self.fetcher else {}
                                    self.alerter.send_status(self.risk_manager,
                                                             balance_available=bal.get("available", 0),
                                                             margin_used=bal.get("used_margin", 0))
                                except Exception:
                                    self.alerter.send_status(self.risk_manager)
                            elif cmd == "/pause":
                                self.risk_manager._pause_trading("Manual pause via /pause")
                                _reply(f"⏸ Trading paused at {format_ist_timestamp()}")
                            elif cmd == "/resume":
                                self.risk_manager.manual_resume()
                                _reply(f"▶️ Trading resumed at {format_ist_timestamp()}")
                            elif cmd == "/balance":
                                bal = self.fetcher.get_account_balance() if self.fetcher else {}
                                avail = bal.get("available", 0)
                                pnl   = self.risk_manager.state.daily_pnl if self.risk_manager else 0
                                trades = self.risk_manager.state.daily_trades if self.risk_manager else 0
                                _reply(
                                    f"💰 <b>Account Balance</b>\n"
                                    f"Available: <b>${avail:,.2f}</b>\n"
                                    f"Daily P&L: <b>${pnl:+,.2f}</b>\n"
                                    f"Trades today: <b>{trades}</b>\n"
                                    f"Live: {'✅' if config.LIVE_TRADING_ENABLED else '🔒 PAPER'}"
                                )
                            elif cmd == "/watchlist":
                                status = self.watchlist_mgr.format_watchlist_message()
                                _reply(status)
                            elif cmd == "/report":
                                self.alerter.send_eod_report(self.risk_manager)
                            elif cmd == "/start":
                                _reply(f"✅ <b>KingTrades running</b>\nChat ID: <code>{chat_id}</code>")
                            else:
                                _reply(f"Unknown command: {cmd}\nTry: /status /balance /pause /resume /kill")
                        except Exception as _ce:
                            logger.warning(f"Telegram cmd {cmd} error: {_ce}")
                            _reply(f"Error: {_ce}")
                except Exception as _e:
                    logger.debug(f"Telegram poll error: {_e}")
                    time.sleep(5)

        self._tg_thread = threading.Thread(target=listener_thread, daemon=True, name="tg-listener")
        self._tg_thread.start()
        logger.info(f"[{format_ist_timestamp()}] Telegram command listener started (direct HTTP)")

    async def _telegram_listener(self):
        """
        Async Telegram command handler with Conflict retry.
        Render deploys overlap briefly — old + new instance both poll simultaneously,
        causing 409 Conflict. Retry with backoff until old instance dies (~30s).
        """
        import telegram.error as tg_error
        from telegram.ext import Application, CommandHandler

        # ── Shared auth check — logs the real chat ID so misconfiguration is obvious
        def _auth(update) -> bool:
            real_id = str(update.effective_chat.id)
            cfg_id  = str(config.TELEGRAM_CHAT_ID).strip()
            # If CHAT_ID is not configured, log the real ID but allow anyone for /start
            if cfg_id in ("", "YOUR_TELEGRAM_CHAT_ID_HERE", "0"):
                return False
            if real_id != cfg_id:
                logger.warning(
                    f"[{format_ist_timestamp()}] Telegram: rejected command from "
                    f"chat_id={real_id} (configured={cfg_id})"
                )
                return False
            return True

        # /start — no auth check; replies with chat ID so user can configure .env
        async def cmd_start(update, context):
            real_id = str(update.effective_chat.id)
            cfg_id  = str(config.TELEGRAM_CHAT_ID).strip()
            if cfg_id in ("", "YOUR_TELEGRAM_CHAT_ID_HERE", "0") or real_id != cfg_id:
                await update.message.reply_text(
                    f"👋 <b>KingTrades bot is running!</b>\n\n"
                    f"Your Chat ID is: <code>{real_id}</code>\n\n"
                    f"Add this to your <code>.env</code> file:\n"
                    f"<pre>TELEGRAM_CHAT_ID={real_id}</pre>\n"
                    f"Then restart the bot and all commands will work.",
                    parse_mode="HTML"
                )
            else:
                await update.message.reply_text(
                    f"✅ <b>KingTrades bot ready</b>\n"
                    f"Chat ID verified: <code>{real_id}</code>\n"
                    f"Use /status, /balance, /kill, /pause, /resume",
                    parse_mode="HTML"
                )

        # ── Command handlers (defined once, reused across retries) ──────
        async def cmd_kill(update, context):
            if not _auth(update):
                return
            logger.critical(f"[{format_ist_timestamp()}] /kill received!")
            self.risk_manager.emergency_stop()
            self.alerter.send_kill_alert()
            self.executor.square_off_all("KILL SWITCH by Telegram /kill")
            if self.options_scalper:
                try:
                    self.options_scalper.close_all()
                except Exception as _e:
                    logger.warning(f"options_scalper.close_all() failed during /kill: {_e}")
            if self.crypto_engine:
                try:
                    self.crypto_engine.executor.close_all(reason="KILL_SWITCH")
                    self.crypto_engine.stop()
                    logger.info(f"[{format_ist_timestamp()}] Crypto engine killed by /kill command")
                except Exception as _ce:
                    logger.warning(f"crypto_engine.close_all() failed during /kill: {_ce}")

        async def cmd_status(update, context):
            if not _auth(update):
                return
            # Inject live balance into status message
            try:
                bal = self.fetcher.get_account_balance() if self.fetcher else {}
                available = bal.get("available", 0)
                used_margin = bal.get("used_margin", 0)
                self.alerter.send_status(self.risk_manager, balance_available=available, margin_used=used_margin)
            except Exception as _e:
                logger.warning(f"cmd_status balance fetch failed: {_e}")
                self.alerter.send_status(self.risk_manager)
            # Also send crypto engine status
            if self.crypto_engine:
                try:
                    cs = self.crypto_engine.get_status()
                    pos_count  = cs["positions"]
                    daily_pnl  = cs["daily_pnl"]
                    trades     = cs["daily_trades"]
                    paused_str = "⏸ PAUSED" if cs["paused"] else "▶ ACTIVE"
                    self.alerter.send_text(
                        f"🪙 <b>Crypto Engine Status</b>\n"
                        f"State: <b>{paused_str}</b>\n"
                        f"Open positions: <b>{pos_count}</b>\n"
                        f"Daily P&L: <b>${daily_pnl:+,.2f}</b>\n"
                        f"Trades today: <b>{trades}</b>\n"
                        f"Total scans: <b>{cs['scan_count']}</b>"
                    )
                except Exception as _ce:
                    logger.debug(f"crypto status failed: {_ce}")

        async def cmd_pause(update, context):
            if not _auth(update):
                return
            self.risk_manager._pause_trading("Manual pause via /pause")
            self.alerter.send_text(f"⏸ Trading paused at {format_ist_timestamp()}")

        async def cmd_resume(update, context):
            if not _auth(update):
                return
            self.risk_manager.manual_resume()
            self.alerter.send_text(f"▶️ Trading resumed at {format_ist_timestamp()}")

        async def cmd_watchlist(update, context):
            if not _auth(update):
                return
            status = self.watchlist_mgr.format_watchlist_message()
            self.alerter.send_text(status)

        async def cmd_report(update, context):
            if not _auth(update):
                return
            self.alerter.send_eod_report(self.risk_manager)

        async def cmd_balance(update, context):
            if not _auth(update):
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
                        except Exception as _e:
                            logger.debug(f"[suppressed] {_e}")

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
            if not _auth(update):
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

                # Also show US market compounded growth if available
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

        # Wait until the trading engine is fully initialized before handling commands.
        # _telegram_listener starts in a background thread before self.running = True,
        # so incoming /kill or /status commands would hit uninitialized risk_manager.
        while not self.running:
            await asyncio.sleep(0.1)

        def _force_close_tg_session():
            """Call getUpdates with timeout=0 to immediately terminate any active long-poll session."""
            import requests as _req
            _tg_base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
            try:
                _req.get(f"{_tg_base}/deleteWebhook?drop_pending_updates=true", timeout=10)
            except Exception:
                pass
            # getUpdates with timeout=0 bumps out any other getUpdates that's in flight
            try:
                _req.get(f"{_tg_base}/getUpdates?offset=-1&timeout=0&limit=1", timeout=12)
            except Exception:
                pass

        # Clear any stale session before first attempt
        _force_close_tg_session()
        logger.info(f"[{format_ist_timestamp()}] Telegram: cleared stale webhook/polling session")

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

                async def cmd_health(update, context):
                    if not _auth(update):
                        return
                    try:
                        import psutil
                        import time as _t
                        from system_health import get_health_checker
                        hc = get_health_checker()
                        bot_state_health = {
                            "running": self.running,
                            "daily_pnl": self.risk_manager.state.daily_pnl if self.risk_manager else 0,
                            "open_positions": len(self.risk_manager.state.positions) if self.risk_manager else 0,
                        }
                        report = hc.check(bot_state_health)
                        uptime_hrs = (_t.time() - getattr(self, '_start_time', _t.time())) / 3600
                        mem = psutil.virtual_memory()
                        msg = (
                            f"🏥 <b>KING Health Report</b>\n"
                            f"{'─'*30}\n"
                            f"Uptime: {uptime_hrs:.1f}h\n"
                            f"Memory: {mem.percent:.0f}% used\n"
                            f"Status: {'✅ HEALTHY' if getattr(report, 'healthy', True) else '⚠️ DEGRADED'}\n"
                            f"Issues: {len(getattr(report, 'issues', []))}\n"
                        )
                        for issue in getattr(report, 'issues', [])[:5]:
                            msg += f"  • {issue}\n"
                        self.alerter.send_html(msg)
                    except Exception as _he:
                        self.alerter.send_text(f"Health check error: {_he}")

                async def cmd_relogin(update, context):
                    if not _auth(update):
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

                app.add_handler(CommandHandler("start",      cmd_start))
                app.add_handler(CommandHandler("kill",       cmd_kill))
                app.add_handler(CommandHandler("status",     cmd_status))
                app.add_handler(CommandHandler("pause",      cmd_pause))
                app.add_handler(CommandHandler("resume",     cmd_resume))
                app.add_handler(CommandHandler("watchlist",  cmd_watchlist))
                app.add_handler(CommandHandler("report",     cmd_report))
                app.add_handler(CommandHandler("balance",    cmd_balance))
                app.add_handler(CommandHandler("capital",    cmd_capital))
                app.add_handler(CommandHandler("relogin",    cmd_relogin))
                app.add_handler(CommandHandler("health",     cmd_health))

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
                    f"[{format_ist_timestamp()}] Telegram Conflict — forcing session reset. "
                    f"Retry {attempt + 1}/{max_retries} in {retry_delay}s..."
                )
                if app:
                    try:
                        await app.shutdown()
                    except Exception as _e:
                        logger.debug(f"[suppressed] {_e}")
                # Force-terminate the competing getUpdates session before sleeping
                _force_close_tg_session()
                await asyncio.sleep(retry_delay)
                retry_delay = min(int(retry_delay * 1.5), 60)

            except Exception as e:
                logger.error(f"[{format_ist_timestamp()}] Telegram listener error: {e}")
                if app:
                    try:
                        await app.shutdown()
                    except Exception as _e:
                        logger.debug(f"[suppressed] {_e}")
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
            # Clamp to [config floor, config floor+20] so stored stale params
            # (e.g. min_score=90) cannot override a manually lowered config value.
            if "min_score" in params and self.signal_gen:
                trained = float(params["min_score"])
                clamped = max(config.MIN_SIGNAL_SCORE,
                              min(trained, config.MIN_SIGNAL_SCORE + 20.0))
                self.signal_gen.min_score = clamped

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
            except Exception as _e:
                logger.debug(f"[suppressed] {_e}")
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
                compounded = float(data.get("compounded_capital", base) or base)
                # If base=0 (MAX_DAILY_CAPITAL not set), allow any positive compounded value
                if base > 0:
                    cap = base * 5
                    compounded = max(base, min(compounded, cap))
                if compounded > 0:
                    if compounded != base:
                        logger.info(
                            f"[{format_ist_timestamp()}] Auto-compound: "
                            f"base ${base:,.2f} → today ${compounded:,.2f}"
                        )
                    return compounded
        except Exception as e:
            logger.warning(f"Capital load error: {e}")
        # No capital.json yet or MAX_DAILY_CAPITAL=0 — fetch live broker balance
        if base <= 0 and self.fetcher:
            try:
                _bal = self.fetcher.get_account_balance()
                live = _bal.get("equity", 0) or _bal.get("available", 0) or 0.0
                if live > 0:
                    logger.info(f"[{format_ist_timestamp()}] Capital: using live broker balance ${live:,.2f}")
                    return live
            except Exception:
                pass
        return base

    @staticmethod
    def _atomic_write(path: Path, data: dict) -> None:
        """Write JSON atomically — write to .tmp then rename. Safe on crash/power loss."""
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(path)   # atomic on Linux (same filesystem)

    def _safe_read_json(self, path: Path) -> dict:
        """Read JSON with corruption recovery — returns {} on any error."""
        try:
            if path.exists():
                return json.loads(path.read_text())
        except Exception as e:
            logger.warning(f"JSON read failed ({path.name}): {e} — using empty dict")
        return {}

    def _save_capital_intraday(self):
        """Write capital.json mid-day so supervisor/optimizer see live P&L data."""
        try:
            if not self.risk_manager:
                return
            rm = self.risk_manager
            existing = self._safe_read_json(self._capital_file)
            self._capital_file.parent.mkdir(exist_ok=True)
            self._atomic_write(self._capital_file, {
                **existing,
                "daily_pnl":          round(rm.state.daily_pnl, 2),
                "total_equity":       round(rm.state.daily_capital, 2),
                "daily_capital":      round(rm.state.daily_capital, 2),
                "wins":               rm.state.winning_trades,
                "losses":             rm.state.losing_trades,
                "daily_trades":       rm.state.daily_trades,
                "consecutive_losses": rm.state.consecutive_losses,
            })
        except Exception as _e:
            logger.debug(f"Intraday capital save skipped: {_e}")

    def _save_compounded_capital(self):
        """Save today's P&L to capital.json for tomorrow's compound."""
        try:
            if not self.risk_manager:
                return
            today_pnl   = self.risk_manager.state.daily_pnl
            daily_cap   = self.risk_manager.state.daily_capital  # actual broker equity from risk mgr
            wins        = self.risk_manager.state.winning_trades
            losses      = self.risk_manager.state.losing_trades
            daily_trades= self.risk_manager.state.daily_trades
            cons_losses = self.risk_manager.state.consecutive_losses

            # Prefer live broker balance over risk manager's tracked capital
            # This ensures we always compound from the true account equity
            live_balance = 0.0
            try:
                if self.fetcher:
                    _bal = self.fetcher.get_account_balance()
                    live_balance = _bal.get("equity", 0) or _bal.get("available", 0) or 0.0
            except Exception:
                pass
            # Use live balance if available, otherwise fall back to risk manager's daily_capital
            actual_equity = live_balance if live_balance > 0 else daily_cap

            base        = config.MAX_DAILY_CAPITAL or actual_equity  # 0 → use live equity as base
            existing    = self._safe_read_json(self._capital_file)
            # Tomorrow's capital = today's closing equity (live balance, already includes P&L)
            new_capital = actual_equity if actual_equity > 0 else float(existing.get("compounded_capital", base))

            # Monthly P&L accumulator
            month_key = get_current_ist_time().strftime("%Y-%m")
            prev_month = existing.get("month_key", month_key)
            month_pnl  = existing.get("month_pnl", 0.0)
            if prev_month != month_key:
                month_pnl = 0.0   # reset on new month
            month_pnl += today_pnl

            # Track first-day equity as origin for growth % calculation
            _origin = float(existing.get("base_capital") or existing.get("origin_capital") or base or new_capital)
            self._atomic_write(self._capital_file, {
                "date":               get_current_ist_time().strftime("%Y-%m-%d"),
                "base_capital":       round(_origin, 2),    # first-day equity (static reference)
                "origin_capital":     round(_origin, 2),    # alias — never changes after first write
                "daily_capital":      round(actual_equity, 2),  # today's live broker equity
                "total_equity":       round(actual_equity, 2),  # alias used by optimizer
                "today_pnl":          round(today_pnl, 2),
                "compounded_capital": round(new_capital, 2),
                "total_growth_pct":   round((new_capital - _origin) / max(_origin, 1) * 100, 2),
                "wins":               wins,
                "losses":             losses,
                "daily_trades":       daily_trades,
                "consecutive_losses": cons_losses,
                "month_key":          month_key,
                "month_pnl":          round(month_pnl, 2),
                # 13% monthly progress
                "monthly_target_pct": 13.0,
                "monthly_target_usd": round(daily_cap * 0.13, 2),
                "monthly_pace_pct":   round(month_pnl / max(daily_cap, 1) * 100, 2),
            })
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
                qty = int(p.get("qty", 0))
                avg = float(p.get("avg_price", 0))
                if not sym or qty == 0:
                    continue
                if sym not in self.risk_manager.state.positions:
                    from risk_manager import Position, _load_persisted_sl
                    _default_sl = avg * 0.98
                    _persisted  = _load_persisted_sl(sym)
                    # Use persisted SL if more protective (higher for LONG, lower for SHORT)
                    _direction  = "LONG" if qty > 0 else "SHORT"
                    if _persisted is not None:
                        if _direction == "LONG" and _persisted > _default_sl:
                            _default_sl = _persisted
                            logger.info(f"Restored persisted SL for {sym}: ${_persisted:.2f} (trailed from default)")
                        elif _direction == "SHORT" and _persisted < _default_sl:
                            _default_sl = _persisted
                            logger.info(f"Restored persisted SL for {sym}: ${_persisted:.2f} (trailed from default)")
                    pos = Position(
                        symbol=sym,
                        direction=_direction,
                        quantity=abs(qty),
                        entry_price=avg,
                        stop_loss=_default_sl,
                        target_1=avg * 1.02,
                        target_2=avg * 1.04,
                        atr=avg * 0.02,
                        entry_time=format_ist_timestamp(),
                    )
                    self.risk_manager.add_position(pos)
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
                except Exception as _e:
                    logger.debug(f"[suppressed] {_e}")
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

    def _get_web_state(self) -> dict:
        """State provider for web dashboard — called on every page refresh."""
        try:
            rm = self.risk_manager
            positions = []
            if rm:
                for pos in rm.state.positions.values():
                    positions.append({
                        "symbol":    pos.symbol,
                        "direction": pos.direction,
                        "entry":     round(pos.entry_price, 2),
                        "ltp":       round(pos.current_price, 2),
                        "pnl":       round(pos.pnl, 2),
                        "pnl_pct":   round(pos.pnl_pct, 2),
                        "sl":        round(pos.stop_loss, 2),
                        "t1":        round(pos.target_1, 2),
                        "grade":     getattr(pos, "quality_grade", "B"),
                        "age_min":   round(self.risk_manager._position_age_minutes(pos), 1),
                    })
            stats = {"trades": 0, "wins": 0, "win_rate": 0.0, "total_pnl": 0.0, "day_pct": 0.0}
            brain = {"min_score": 90.0, "size_mult": 1.0, "streak_wins": 0, "streak_losses": 0, "paused": False, "reason": ""}
            if hasattr(self, "adaptive_brain") and self.adaptive_brain:
                s = self.adaptive_brain._state
                wr = round(s.wins_today / max(s.trades_today, 1) * 100, 1)
                stats = {"trades": s.trades_today, "wins": s.wins_today, "win_rate": wr,
                         "total_pnl": round(s.day_pnl, 2),
                         "day_pct":   round(s.day_pnl / max(s.day_capital, 1) * 100, 2)}
                brain = {"min_score": s.current_min_score, "size_mult": s.size_multiplier,
                         "streak_wins": s.consecutive_wins, "streak_losses": s.consecutive_losses,
                         "paused": s.paused, "reason": s.pause_reason}
            paused = (rm and rm.state.trading_paused) or (hasattr(self, "adaptive_brain") and self.adaptive_brain and self.adaptive_brain.is_paused())
            return {
                "bot_status":     "PAUSED" if paused else ("ACTIVE" if self.running else "STOPPED"),
                "live_trading":   config.LIVE_TRADING_ENABLED,
                "positions":      positions,
                "stats":          stats,
                "brain":          brain,
                "recent_signals": getattr(self, "_recent_signals_log", [])[-10:],
            }
        except Exception as e:
            return {"bot_status": "ERROR", "live_trading": False, "positions": [],
                    "stats": {}, "brain": {}, "recent_signals": [], "error": str(e)}

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
            except Exception as _e:
                logger.debug(f"[suppressed] {_e}")
            status = "🟢 TRADING" if not state.trading_paused else "⏸ PAUSED"
            if state.circuit_breaker_active:
                status = "🔴 CIRCUIT BREAK"
            pos_symbols = ", ".join(state.positions.keys()) if state.positions else "none"
            bal_line = f"${live_bal:,.2f}" if live_bal > 0 else f"${cap:,.2f} (cached)"
            # Score histogram from filter — most critical diagnostic for tuning
            _filter_lines = ""
            try:
                if self.signal_gen and hasattr(self.signal_gen, "ha_filter"):
                    _fs = self.signal_gen.ha_filter.get_stats()
                    _hist = _fs.get("score_histogram", {})
                    _thresh = int(_fs.get("min_score", 72))
                    if _hist:
                        _hist_str = "  ".join(
                            f"{k}:{v}{'✅' if int(k.split('-')[0]) >= _thresh else ''}"
                            for k, v in sorted(_hist.items(), key=lambda x: int(x[0].split("-")[0]))
                        )
                        _ml_line = ""
                        try:
                            from ml_signal_ranker import get_model_stats as _gms
                            _mls = _gms()
                            _ml_line = (
                                f"\n🤖 ML: P(win)={_mls.get('win_rate_live','N/A')} | "
                                f"live_samples={_mls.get('live_samples',0)} | "
                                f"retrain_at={_mls.get('retrain_at',50)}"
                            )
                        except Exception:
                            pass
                        _filter_lines = (
                            f"\n📊 Scores: {_hist_str}"
                            f"\n🎯 Passed: {_fs['passed']} | Rejected: {_fs['rejected']}"
                            f" | Near-miss: {_fs.get('near_miss_count', 0)}"
                            f"{_ml_line}"
                        )
            except Exception:
                pass
            # Rich WR/P&L heartbeat via new alerter method
            self.alerter.send_heartbeat(open_positions=dict(state.positions))

            # Detailed diagnostics (score histogram, ML stats) as separate message
            if _filter_lines:
                self.alerter.send_html(
                    f"📡 <b>KING Diagnostics</b> — {format_ist_timestamp()}\n"
                    f"Status: {status}\n"
                    f"Balance: {bal_line}"
                    f"{_filter_lines}"
                )
        except Exception as e:
            logger.debug(f"Heartbeat error: {e}")

    # --------------------------------------------------------
    # CACHED WATCHLIST
    # --------------------------------------------------------

    def _get_watchlist_cached(self) -> List[str]:
        """Get watchlist with 15-min cache. Guarantees >= 30 symbols (DEFAULT_WATCHLIST floor)."""
        from config import DEFAULT_WATCHLIST
        now = get_current_ist_time()
        if (not self._watchlist_cache or
                self._watchlist_cache_time is None or
                (now - self._watchlist_cache_time).total_seconds() > self._watchlist_cache_ttl):
            wl = self.watchlist_mgr.get_watchlist(
                data_fetcher=self.fetcher, learner=self.learner
            )
            # Safety floor: never scan fewer than 30 symbols
            if len(wl) < 30:
                existing = set(wl)
                for sym in DEFAULT_WATCHLIST:
                    if sym not in existing:
                        wl.append(sym)
                    if len(wl) >= 50:
                        break
                logger.info(
                    f"[{format_ist_timestamp()}] Watchlist padded to {len(wl)} symbols "
                    f"(was below 30 — DEFAULT_WATCHLIST merged in)"
                )
            self._watchlist_cache = wl
            self._watchlist_cache_time = now
        return self._watchlist_cache

    # --------------------------------------------------------
    # CLEANUP
    # --------------------------------------------------------

    def _cleanup(self):
        """Graceful shutdown."""
        self.running = False
        # Stop crypto engine gracefully
        if self.crypto_engine:
            try:
                self.crypto_engine.executor.close_all(reason="BOT_SHUTDOWN")
                self.crypto_engine.stop()
                logger.info(f"[{format_ist_timestamp()}] Crypto engine stopped at cleanup")
            except Exception as e:
                logger.warning(f"Crypto engine cleanup error: {e}")
        logger.info(f"[{format_ist_timestamp()}] Bot cleanup complete.")


# ============================================================
# ENTRY POINT
# ============================================================

class _SuppressYFNoise(logging.Filter):
    """Drop yfinance 404/fundamentals noise for ETFs — not actionable."""
    _PATTERNS = ("No fundamentals data found", "HTTP Error 404", "No data found for")
    def filter(self, record):
        msg = record.getMessage()
        return not any(p in msg for p in self._PATTERNS)


def main():
    # ── Single-instance guard — prevents duplicate processes owning the Telegram bot ──
    import fcntl
    _pid_path = Path(config.LOG_DIR) / "kingtrades.pid"
    _pid_path.parent.mkdir(parents=True, exist_ok=True)
    _pid_fh = open(_pid_path, "w")
    try:
        fcntl.flock(_pid_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        existing = _pid_path.read_text().strip() if _pid_path.exists() else "unknown"
        print(
            f"[KingTrades] Another instance is already running (PID {existing}). "
            "Stop it first with: pkill -f main.py",
            flush=True,
        )
        sys.exit(1)
    _pid_path.write_text(str(os.getpid()))

    # Setup IST logging
    setup_logging(
        log_dir=config.LOG_DIR,
        level=config.LOG_LEVEL,
        module_name="kingtrades"
    )
    logging.getLogger("yfinance").addFilter(_SuppressYFNoise())

    # Auto-update: pull latest code so bot is always current
    try:
        from adaptive_brain import auto_update_code
        update_msg = auto_update_code()
        logger.info(f"[{format_ist_timestamp()}] {update_msg}")
    except Exception as _e:
        logger.debug(f"[suppressed] {_e}")

    _SEP = "━" * 60
    logger.info(_SEP)
    logger.info("  ██╗  ██╗██╗███╗   ██╗ ██████╗ ████████╗██████╗  █████╗ ██████╗ ███████╗███████╗")
    logger.info("  ██║ ██╔╝██║████╗  ██║██╔════╝ ╚══██╔══╝██╔══██╗██╔══██╗██╔══██╗██╔════╝██╔════╝")
    logger.info("  █████╔╝ ██║██╔██╗ ██║██║  ███╗   ██║   ██████╔╝███████║██║  ██║█████╗  ███████╗")
    logger.info("  ██╔═██╗ ██║██║╚██╗██║██║   ██║   ██║   ██╔══██╗██╔══██║██║  ██║██╔══╝  ╚════██║")
    logger.info("  ██║  ██╗██║██║ ╚████║╚██████╔╝   ██║   ██║  ██║██║  ██║██████╔╝███████╗███████║")
    logger.info("  ╚═╝  ╚═╝╚═╝╚═╝  ╚═══╝ ╚═════╝    ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚══════╝╚══════╝")
    logger.info(_SEP)
    logger.info("  [BLOOMBERG TERMINAL] KingTrades v3.0 — Institutional Momentum Engine")
    logger.info(f"  [BROKER]   NYSE/NASDAQ via Alpaca  |  [MARKET] US Equities")
    logger.info(f"  [CLOCK]    {format_ist_timestamp()}")
    logger.info(f"  [MODE]     {'⚡ LIVE TRADING ENABLED — REAL MONEY' if config.LIVE_TRADING_ENABLED else '🔒 PAPER TRADING — safe mode'}")
    logger.info(f"  [CAPITAL]  ${config.MAX_DAILY_CAPITAL:,.0f}  |  [TARGET] {getattr(config, 'DAILY_PROFIT_TARGET_PCT', 1.0):.1f}%/day")
    logger.info(_SEP)

    bot = TradingBot()
    try:
        if not bot.initialize():
            logger.critical("Bot initialization failed. Exiting.")
            sys.exit(1)
    except Exception as _init_exc:
        logger.critical(f"Bot initialization raised exception: {_init_exc}", exc_info=True)
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
